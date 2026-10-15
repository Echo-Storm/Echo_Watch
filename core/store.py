"""Config + watchlist persistence.

Data lives in ./data next to the app (portable, easy to back up). A config left by an
older version in %LOCALAPPDATA%/EchoWatch is copied over once on first run.
"""
import json
import os
import shutil
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = APP_DIR / "data"
LEGACY_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "EchoWatch"

DATA_DIR = DEFAULT_DATA_DIR
CONFIG = DATA_DIR / "config.json"
BACKUPS = DATA_DIR / "backups"

DEFAULT_ADDONS_DIR = r"C:\Program Files (x86)\World of Warcraft\_classic_beta_\Interface\AddOns"

DEFAULTS = {
    "addons_dir": DEFAULT_ADDONS_DIR,
    "check_on_launch": False,
    "items": [
        {
            "name": "Auctionator",
            "kind": "wago",
            "slug": "auctionator",
            "flavor": "forever",
            "folder": "Auctionator",
        }
    ],
}


def _key(it):
    """Identity of a watchlist item, used to skip duplicates on import."""
    if it.get("kind") in ("wago", "ghaddon"):
        return ("addon", it.get("folder", "").lower())
    if it.get("kind") == "page":
        return ("page", it.get("url"), it.get("pattern", ""))
    return (it.get("kind"), it.get("repo", "").lower(), it.get("branch", ""))


def _import_extra_items(data):
    """Merge data/import.json ({"items": [...]}) once, then rename it to import.done.json."""
    src = DATA_DIR / "import.json"
    if not src.exists():
        return False
    try:
        extra = json.loads(src.read_text(encoding="utf-8")).get("items", [])
    except (OSError, ValueError):
        return False
    have = {_key(i) for i in data["items"]}
    added = [i for i in extra if _key(i) not in have]
    data["items"].extend(added)
    src.replace(DATA_DIR / "import.done.json")
    return bool(added)


def load():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG.exists() and DATA_DIR == DEFAULT_DATA_DIR and (LEGACY_DIR / "config.json").exists():
        try:
            shutil.copy2(LEGACY_DIR / "config.json", CONFIG)
        except OSError:
            pass
    data = None
    if CONFIG.exists():
        try:
            data = json.loads(CONFIG.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            try:  # keep the unreadable file instead of silently overwriting the user's list
                CONFIG.replace(CONFIG.with_suffix(".bad"))
            except OSError:
                pass
    if data is None:
        data = json.loads(json.dumps(DEFAULTS))
    for k, v in DEFAULTS.items():
        data.setdefault(k, v)
    if _import_extra_items(data):
        save(data)
    return data


def save(data):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(CONFIG)
