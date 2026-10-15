# Echo Watch

A small PyQt6 desktop app: press **Check all** once a day and see what changed.

- **WoW addons** - compares the version in your AddOns folder (read from the `.toc`) with the latest
  [Wago](https://addons.wago.io) release for the *forever* flavor. **Update now** downloads the zip,
  moves the old folder to a backup, and installs the new one.
- **GitHub releases** - flags a new tag since you last marked it seen.
- **GitHub commits** - shows the newest commit and how many are new since you last marked it seen.

Every item has a display name you can rename. First check of a release/commit item sets a silent
baseline; use **Mark seen** to clear a "New" flag.

## Run

```
pip install -r requirements.txt
python echo_watch.py        # or double-click run.bat
python echo_watch.py --check  # check immediately on launch
```

Config lives in `%LOCALAPPDATA%\EchoWatch\config.json`; addon backups in `...\EchoWatch\backups`.
Set the AddOns folder with the **AddOns folder...** button (default is the WoW classic beta folder).

GitHub lookups use `GH_TOKEN`/`GITHUB_TOKEN` or `gh auth token` if available, otherwise the 60/hour
anonymous limit applies. `python smoke_test.py` checks the network sources.

Windows only for now. Wago's page layout is scraped, so it may need a fix if Wago changes it.
Addons are third-party software under their own licenses; Echo Watch only downloads what you
choose to watch.
