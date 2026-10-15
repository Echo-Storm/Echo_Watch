"""Render docs/screenshot.png from sample data (no network, no personal config)."""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import sources, store  # noqa: E402

tmp = Path(tempfile.mkdtemp())
store.DATA_DIR, store.CONFIG = tmp, tmp / "config.json"

from PyQt6.QtCore import QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import echo_watch as e  # noqa: E402

app = QApplication([])
app.setStyleSheet(e.STYLE)
w = e.Main()
w.resize(1160, 470)
w.cfg["items"] = [
    {"name": "Auctionator", "kind": "wago", "slug": "auctionator", "folder": "Auctionator",
     "latest": "341", "note": "Update toc for 12.1.5", "download": "x", "url": "u"},
    {"name": "llama.cpp", "kind": "release", "repo": "ggml-org/llama.cpp", "latest": "v0.6.0",
     "seen": "v0.6.0", "note": "v0.6.0", "url": "u"},
    {"name": "RXPGuides", "kind": "ghaddon", "repo": "RestedXP/RXPGuides", "folder": "RXPGuides",
     "latest": "v4.11.24", "note": "Fix guide step 12", "download": "x", "url": "u"},
    {"name": "Upload page", "kind": "page", "url": "https://example.com", "pattern": "x",
     "latest": "15/10/2026 09:02:11 PM", "seen": "14/10/2026 08:47:49 PM"},
    {"name": "Some repo", "kind": "commits", "repo": "owner/repo", "latest": "d5d4816",
     "seen": "a1b2c3d", "ahead": 3, "note": "chore: update midnight guide", "url": "u"},
    {"name": "Old addon", "kind": "wago", "slug": "gone", "folder": "Gone",
     "error": "not found (404) - private or wrong name?"},
]
sources.installed_version = lambda d, f: {"Auctionator": "341", "RXPGuides": "v4.11.23"}.get(f)
w.cfg["last_check"] = 1792072000
w.sort_items()
w.reload_table()
w._buttons()
w.show()


def snap():
    out = ROOT / "docs" / "screenshot.png"
    out.parent.mkdir(exist_ok=True)
    w.grab().save(str(out))
    print("saved", out)
    app.quit()


QTimer.singleShot(700, snap)
app.exec()
