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
import time
import zipfile
from pathlib import Path

import requests

UA = {"User-Agent": "EchoWatch/0.1 (+personal update checker)"}
TIMEOUT = 20


class CheckError(Exception):
    pass


_gh_token = None


def _github_headers():
    global _gh_token
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
    if r.status_code == 403 and "rate limit" in r.text.lower():
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
    note = next((ln.lstrip("- ").strip() for ln in note[::-1] if ln.strip().startswith("-")), "")
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


CHECKERS = {"wago": check_wago, "release": check_release, "commits": check_commits}


def run_check(item):
    fn = CHECKERS[item["kind"]]
    args = {k: v for k, v in item.items() if k not in ("name", "kind")}
    if item["kind"] in ("release", "commits"):
        args = {"repo": item["repo"], "branch": item.get("branch", "")}
    return fn(**args)


# --- addon installed version / installing --------------------------------

def installed_version(addons_dir, folder):
    toc = Path(addons_dir) / folder / f"{folder}.toc"
    if not toc.exists():
        return None
    for line in toc.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"##\s*Version:\s*(.+)", line)
        if m:
            return m.group(1).strip()
    return "?"


def install_zip(url, addons_dir, backups_dir):
    """Download an addon zip and install its top-level folders into addons_dir.
    Existing folders are moved to backups_dir first. Returns installed folder names."""
    r = _get(url, headers=UA)
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    tops = sorted({n.split("/")[0] for n in names if "/" in n})
    if not tops or any(t in ("", "..") or ":" in t for t in tops):
        raise CheckError("unexpected zip layout")
    addons = Path(addons_dir).resolve()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for t in tops:
        dest = addons / t
        if dest.exists():
            bk = Path(backups_dir) / stamp
            bk.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dest), str(bk / t))
    for n in names:
        target = (addons / n).resolve()
        if addons not in target.parents and target != addons:
            raise CheckError("zip tried to escape the AddOns folder")
        if n.endswith("/"):
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(n))
    return tops
