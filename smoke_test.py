"""Network smoke test for the update sources (no GUI)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from core import sources, store  # noqa: E402

wago = sources.run_check(store.DEFAULTS["items"][0])
print("wago        ", wago["latest"], "| downloadable:", bool(wago["download"]))
print("installed   ", sources.installed_version(store.DEFAULT_ADDONS_DIR, "Auctionator"))
print("release     ", sources.run_check({"name": "x", "kind": "release", "repo": "ggml-org/llama.cpp"})["latest"])
print("commits     ", sources.run_check({"name": "x", "kind": "commits", "repo": "ggml-org/llama.cpp"})["latest"])
