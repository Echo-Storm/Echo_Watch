"""Update sources. Each check returns a dict:
   latest: str      id shown to the user (version / tag / short sha)
   url: str         page to open
   note: str        extra detail (changelog line, commit message)
   download: str|None  zip URL when the app can install it itself
"""
import html
import io
import json
import os
import re
import shutil
import subprocess
import threading
import time
import zipfile
from pathlib import Path

import requests

UA = {"User-Agent": "EchoWatch/0.1 (+personal update checker)"}
TIMEOUT = 20


class CheckError(Exception):
    pass


_gh_token = None
_token_lock = threading.Lock()


def _github_headers():
    global _gh_token
    with _token_lock:  # worker threads race here on the first check
        if _gh_token is None:
            _gh_token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
            if not _gh_token:
                try:
                    out = subprocess.run(["gh", "auth", "token"], capture_output=True,
                                         text=True, timeout=5, creationflags=0x08000000)
                    _gh_token = out.stdout.strip() if out.returncode == 0 else ""
                except (OSError, subprocess.SubprocessError):
                    _gh_token = ""
    h = dict(UA, Accept="application/vnd.github+json")
    if _gh_token:
        h["Authorization"] = f"Bearer {_gh_token}"
    return h


def _get(url, **kw):
    try:
        r = requests.get(url, timeout=TIMEOUT, **kw)
    except requests.RequestException as e:
        raise CheckError(f"network: {e.__class__.__name__}") from e
    if r.status_code == 404:
        raise CheckError("not found (404) - private or wrong name?")
    if r.status_code in (403, 429) and "rate limit" in r.text.lower():
        raise CheckError("GitHub rate limit hit")
    if r.status_code >= 400:
        raise CheckError(f"HTTP {r.status_code}")
    return r


def check_wago(slug, flavor="forever", **_):
    page = f"https://addons.wago.io/addons/{slug}"
    r = _get(page, headers=UA)
    m = re.search(r'data-page="([^"]+)"', r.text)
    if not m:
        raise CheckError("Wago page layout changed")
    try:
        props = json.loads(html.unescape(m.group(1)))["props"]
        releases = props["metadata"]["recent_releases"]
    except (KeyError, ValueError) as e:
        raise CheckError("Wago data missing") from e
    rel = releases.get(flavor) or next(iter(releases.values()), None)
    if not rel:
        raise CheckError(f"no '{flavor}' release")
    note = (rel.get("changelog") or "").strip().splitlines()
    note = next((ln.strip().lstrip("-* ").strip() for ln in note if ln.strip().startswith(("-", "*"))), "")
    return {"latest": str(rel["label"]), "url": page, "note": note,
            "download": rel.get("download_link")}


def check_release(repo, **_):
    r = _get(f"https://api.github.com/repos/{repo}/releases/latest", headers=_github_headers())
    j = r.json()
    zips = [a["browser_download_url"] for a in j.get("assets", []) if a["name"].endswith(".zip")]
    return {"latest": j["tag_name"], "url": j["html_url"],
            "note": (j.get("name") or "").strip(), "download": zips[0] if zips else None}


def check_commits(repo, branch="", **_):
    q = f"?per_page=1&sha={branch}" if branch else "?per_page=1"
    r = _get(f"https://api.github.com/repos/{repo}/commits{q}", headers=_github_headers())
    j = r.json()
    if not j:
        raise CheckError("no commits")
    c = j[0]
    return {"latest": c["sha"][:7], "url": c["html_url"],
            "note": c["commit"]["message"].splitlines()[0][:100], "download": None,
            "full_sha": c["sha"]}


def commits_since(repo, base_sha, head_sha):
    """How many commits head is ahead of base (None if unknown)."""
    try:
        r = _get(f"https://api.github.com/repos/{repo}/compare/{base_sha}...{head_sha}",
                 headers=_github_headers())
        return r.json().get("ahead_by")
    except CheckError:
        return None


def _visible_text(markup):
    markup = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", markup)
    markup = re.sub(r"(?s)<!--.*?-->", " ", markup)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", markup))).strip()


def check_page(url, pattern="", **_):
    """Watch a value on any web page. `pattern` is a regex run on the visible text;
    the first group (or the whole match) is the value. Empty pattern = hash of the text."""
    r = _get(url, headers=UA)
    text = _visible_text(r.text)
    if pattern:
        try:
            m = re.search(pattern, text, re.I)
        except re.error as e:
            raise CheckError(f"bad pattern: {e}") from e
        if not m:
            raise CheckError("pattern not found on the page")
        value = (m.group(1) if m.groups() else m.group(0)).strip()
    else:
        import hashlib
        value = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
    return {"latest": value, "url": url, "note": "", "download": None}


CHECKERS = {"wago": check_wago, "release": check_release, "commits": check_commits,
            "ghaddon": check_release, "page": check_page}


def run_check(item):
    fn = CHECKERS[item["kind"]]
    args = {k: v for k, v in item.items() if k not in ("name", "kind")}
    if item["kind"] == "page":
        args = {"url": item["url"], "pattern": item.get("pattern", "")}
    elif item["kind"] in ("release", "commits", "ghaddon"):
        args = {"repo": item["repo"], "branch": item.get("branch", "")}
    return fn(**args)


# --- addon installed version / installing --------------------------------

def safe_folder(name):
    """A bare folder name: no separators, no traversal."""
    return bool(name) and name not in (".", "..") and not any(c in name for c in "\\/:")


def _read_toc(path):
    meta = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"##\s*([\w-]+)\s*:\s*(.*)", line)
        if m and m.group(1) not in meta:
            meta[m.group(1)] = m.group(2).strip()
    return meta


def installed_version(addons_dir, folder):
    """Version string from the .toc, None if the addon isn't installed."""
    if not safe_folder(folder):
        return None
    toc = Path(addons_dir) / folder / f"{folder}.toc"
    if not toc.exists():
        return None
    return _read_toc(toc).get("Version") or "?"


def norm_version(v):
    return re.sub(r"^[vV]\s*", "", (v or "").strip())


def version_unknown(v):
    return v in (None, "", "?") or "@" in v  # "@project-version@" = unpackaged dev copy


def scan_addons(addons_dir):
    """Top-level addon folders with a matching .toc -> [{folder, title, version, wago_id}]."""
    out = []
    base = Path(addons_dir)
    if not base.is_dir():
        return out
    for d in sorted(base.iterdir(), key=lambda x: x.name.lower()):
        toc = d / f"{d.name}.toc"
        if d.is_dir() and not d.name.startswith("Blizzard_") and toc.exists():
            meta = _read_toc(toc)
            out.append({"folder": d.name, "version": meta.get("Version", "?"),
                        "wago_id": meta.get("X-Wago-ID", "")})
    return out


def install_zip(url, addons_dir, backups_dir):
    """Download an addon zip and install its top-level folders into addons_dir.
    Existing folders are moved to backups_dir first; on any failure the new files
    are removed and the backups restored. Returns installed folder names."""
    r = _get(url, headers=UA)
    try:
        zf = zipfile.ZipFile(io.BytesIO(r.content))
    except zipfile.BadZipFile as e:
        raise CheckError("download was not a zip file") from e
    names = zf.namelist()
    tops = sorted({n.split("/")[0] for n in names if "/" in n})
    if not tops or not all(safe_folder(t) for t in tops):
        raise CheckError("unexpected zip layout")
    addons = Path(addons_dir).resolve()
    for n in names:  # validate everything before touching the disk
        target = (addons / n).resolve()
        if addons not in target.parents and target != addons:
            raise CheckError("zip tried to escape the AddOns folder")
    bk = Path(backups_dir) / time.strftime("%Y%m%d-%H%M%S")
    moved = []
    try:
        for t in tops:
            dest = addons / t
            if dest.exists():
                bk.mkdir(parents=True, exist_ok=True)
                shutil.move(str(dest), str(bk / t))
                moved.append(t)
        for n in names:
            target = addons / n
            if n.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(n))
    except Exception:
        for t in tops:
            shutil.rmtree(addons / t, ignore_errors=True)
        for t in moved:
            shutil.move(str(bk / t), str(addons / t))
        raise
    return tops
