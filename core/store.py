"""Config + watchlist persistence (JSON in %LOCALAPPDATA%/EchoWatch)."""
import json
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "EchoWatch"
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


def load():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG.exists():
        try:
            data = json.loads(CONFIG.read_text(encoding="utf-8"))
            for k, v in DEFAULTS.items():
                data.setdefault(k, v)
            return data
        except (OSError, ValueError):
            try:  # keep the unreadable file instead of silently overwriting the user's list
                CONFIG.replace(CONFIG.with_suffix(".bad"))
            except OSError:
                pass
    return json.loads(json.dumps(DEFAULTS))


def save(data):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(CONFIG)
