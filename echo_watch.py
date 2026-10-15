"""Echo Watch - one-click daily update check for WoW addons and GitHub projects."""
import re
import sys
import time
import webbrowser
from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, Qt, QThreadPool, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
                             QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
                             QHeaderView, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
                             QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
                             QWidget)

from core import sources, store

KINDS = {"wago": "WoW addon (Wago)", "ghaddon": "WoW addon (GitHub)", "release": "GitHub releases",
         "commits": "GitHub commits"}
ADDON_KINDS = ("wago", "ghaddon")  # installed locally, can auto-update
KIND_ICON = {"wago": "\U0001F9E9", "ghaddon": "\U0001F9E9", "release": "\U0001F4E6", "commits": "\U0001F500"}
STATUS_ICON = {"Up to date": "✔", "Update available": "⬆", "Not installed": "⬇",
               "Version unknown": "⬆", "New": "●", "Check failed": "✖",
               "Not checked": "○"}

STYLE = """
* { font-family: 'Segoe UI'; font-size: 13px; color: #d8dee9; }
QMainWindow, QDialog { background: #14171c; }
QLabel#title { font-size: 22px; font-weight: 600; color: #ffffff; }
QLabel#sub { color: #6f7885; }
QTableWidget { background: transparent; border: none; outline: 0; }
QTableWidget::item { padding: 0 18px; border: none; border-bottom: 1px solid #1d222a; }
QTableWidget::item:selected { background: #1c2632; }
QHeaderView::section { background: transparent; color: #5d6674; border: none;
                       border-bottom: 1px solid #262c36; padding: 10px 18px; font-size: 11px; }
QPushButton { background: transparent; border: 1px solid #2a3240; border-radius: 6px; padding: 7px 16px; }
QPushButton:hover { background: #222a35; }
QPushButton:disabled { color: #4a5360; border-color: #212731; }
QPushButton#primary { background: #2fb5a8; color: #0b1114; border: none; font-weight: 600; padding: 9px 24px; }
QPushButton#primary:hover { background: #3fcabc; }
QPushButton#primary:disabled { background: #25544f; color: #0b1114; }
QPushButton#accent { border-color: #2fb5a8; color: #5fd3c6; }
QPushButton#accent:hover { background: #17302f; }
QPushButton#accent:disabled { border-color: #212731; color: #4a5360; }
QLineEdit, QComboBox { background: #1a1e25; border: 1px solid #2f3846; border-radius: 6px; padding: 6px; }
QMenu { background: #1a1e25; border: 1px solid #2f3846; padding: 4px; }
QMenu::item { padding: 6px 22px; border-radius: 4px; }
QMenu::item:selected { background: #243447; }
QMenu::item:disabled { color: #4a5360; }
QStatusBar { color: #6f7885; }
"""

GOOD, WARN, BAD, MUTED = QColor("#5fd39a"), QColor("#f0b44c"), QColor("#ef6b6b"), QColor("#7f8896")
COLS = ["NAME", "VERSION", "STATUS", "WHAT CHANGED"]


def apply_result(it, res):
    """Copy a successful check result onto a watchlist item."""
    it.pop("error", None)
    it["latest"], it["url"] = res["latest"], res["url"]
    it["note"], it["download"] = res.get("note", ""), res.get("download")
    it["latest_full"] = res.get("full_sha")
    it["ahead"] = res.get("ahead")
    if it["kind"] not in ADDON_KINDS and it.get("seen") is None:  # silent baseline on first sight
        it["seen"], it["seen_full"] = res["latest"], res.get("full_sha")


class Signals(QObject):
    done = pyqtSignal(object, object, str)  # watchlist item (identity), result | None, error
    scanned = pyqtSignal(list, int)         # new items, folders that matched nothing


class CheckJob(QRunnable):
    def __init__(self, ref):
        super().__init__()
        self.ref, self.signals = ref, Signals()

    def run(self):
        item = dict(self.ref)
        try:
            res = sources.run_check(item)
            if item["kind"] == "commits" and item.get("seen_full") and res.get("full_sha") != item["seen_full"]:
                res["ahead"] = sources.commits_since(item["repo"], item["seen_full"], res["full_sha"])
            self.signals.done.emit(self.ref, res, "")
        except sources.CheckError as e:
            self.signals.done.emit(self.ref, None, str(e))
        except Exception as e:  # keep the pool alive on surprises
            self.signals.done.emit(self.ref, None, f"{e.__class__.__name__}: {e}")


class InstallJob(QRunnable):
    """Installs addons one after another (they may share folders)."""

    def __init__(self, refs, addons_dir):
        super().__init__()
        self.refs, self.addons_dir, self.signals = refs, addons_dir, Signals()

    def run(self):
        for ref in self.refs:
            try:
                fresh = sources.run_check(ref)  # re-fetch: Wago download links expire
                if not fresh.get("download"):
                    raise sources.CheckError("no download link")
                tops = sources.install_zip(fresh["download"], self.addons_dir, store.BACKUPS)
                self.signals.done.emit(ref, dict(fresh, installed=tops), "")
            except Exception as e:
                self.signals.done.emit(ref, None, str(e) or e.__class__.__name__)


class ScanJob(QRunnable):
    """Find installed addons that Wago knows about and build watchlist items."""

    def __init__(self, addons_dir, known_folders, known_slugs):
        super().__init__()
        self.addons_dir, self.known_folders, self.known_slugs = addons_dir, set(known_folders), set(known_slugs)
        self.signals = Signals()

    def run(self):
        found, misses, seen_slugs = [], 0, set(self.known_slugs)
        for a in sources.scan_addons(self.addons_dir):
            if a["folder"] in self.known_folders:
                continue
            slug = a["wago_id"] or a["folder"].lower()
            if slug in seen_slugs:  # sub-module of a package we already track
                continue
            try:
                res = sources.check_wago(slug)
            except Exception:
                misses += 1
                continue
            seen_slugs.add(slug)
            it = {"name": a["folder"], "kind": "wago", "slug": slug, "flavor": "forever", "folder": a["folder"]}
            apply_result(it, res)
            found.append(it)
        self.signals.scanned.emit(found, misses)


class AddDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add to watchlist")
        self.setMinimumWidth(440)
        self.kind = QComboBox()
        for k, v in KINDS.items():
            self.kind.addItem(v, k)
        self.name, self.target, self.extra = QLineEdit(), QLineEdit(), QLineEdit()
        self.hint = QLabel()
        self.hint.setObjectName("sub")
        self.hint.setWordWrap(True)
        self.target_label, self.extra_label = QLabel(), QLabel()
        form = QFormLayout()
        form.addRow("Type", self.kind)
        form.addRow("Display name", self.name)
        form.addRow(self.target_label, self.target)
        form.addRow(self.extra_label, self.extra)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.hint)
        lay.addWidget(bb)
        self.kind.currentIndexChanged.connect(self._sync)
        self._sync()

    def _sync(self):
        k = self.kind.currentData()
        if k == "ghaddon":
            self.target_label.setText("Repo")
            self.target.setPlaceholderText("RestedXP/RXPGuides")
            self.extra_label.setText("AddOns folder")
            self.extra.setPlaceholderText("RXPGuides")
            self.hint.setText("Uses the repo's latest GitHub release (its .zip asset) and the installed "
                              "version from the folder's .toc.")
        elif k == "wago":
            self.target_label.setText("Wago slug or ID")
            self.target.setPlaceholderText("auctionator")
            self.extra_label.setText("AddOns folder")
            self.extra.setPlaceholderText("Auctionator")
            self.hint.setText("The slug is the end of addons.wago.io/addons/<slug>. The installed version "
                              "is read from the folder's .toc. Release flavor: forever.")
        else:
            self.target_label.setText("Repo")
            self.target.setPlaceholderText("owner/name")
            self.extra_label.setText("Branch (commits)")
            self.extra.setPlaceholderText("default branch")
            self.hint.setText("Baselined on first check, then flagged when something new appears.")

    def load(self, it):
        self.setWindowTitle("Rename / edit")
        self.kind.setCurrentIndex(self.kind.findData(it["kind"]))
        self.kind.setEnabled(False)
        self.name.setText(it["name"])
        self.target.setText(it.get("slug") or it.get("repo", ""))
        self.extra.setText(it.get("folder") or it.get("branch", ""))

    def _ok(self):
        k, t, x = self.kind.currentData(), self.target.text().strip(), self.extra.text().strip()
        if k in ADDON_KINDS:
            folder = x or self.name.text().strip() or t.split("/")[-1]
            if not t or not sources.safe_folder(folder):
                return QMessageBox.warning(self, "Check input", "Enter the source and a plain folder name.")
            if k == "ghaddon" and not re.fullmatch(r"[\w.-]+/[\w.-]+", t):
                return QMessageBox.warning(self, "Check input", "Repo must look like owner/name.")
        elif not re.fullmatch(r"[\w.-]+/[\w.-]+", t):
            return QMessageBox.warning(self, "Check input", "Repo must look like owner/name.")
        self.accept()

    def item(self):
        k = self.kind.currentData()
        t, x, n = self.target.text().strip(), self.extra.text().strip(), self.name.text().strip()
        if k == "wago":
            folder = x or n or t
            return {"name": n or folder, "kind": k, "slug": t, "flavor": "forever", "folder": folder}
        if k == "ghaddon":
            folder = x or n or t.split("/")[-1]
            return {"name": n or folder, "kind": k, "repo": t, "folder": folder}
        it = {"name": n or t, "kind": k, "repo": t}
        if k == "commits" and x:
            it["branch"] = x
        return it


class SettingsDialog(QDialog):
    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(560)
        self.path = QLineEdit(cfg["addons_dir"])
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.path, 1)
        row.addWidget(browse)
        self.launch = QCheckBox("Check for updates when the app opens")
        self.launch.setChecked(bool(cfg.get("check_on_launch")))
        self.status = QLabel()
        self.status.setObjectName("sub")
        self.path.textChanged.connect(self._validate)
        form = QFormLayout()
        form.addRow("WoW AddOns folder", row)
        form.addRow("", self.status)
        form.addRow("", self.launch)
        hint = QLabel("Point this at the Interface/AddOns folder of the client you play (retail, classic, "
                      "or a beta such as _classic_beta_). It is saved; Scan and Update use it every time.")
        hint.setObjectName("sub")
        hint.setWordWrap(True)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(hint)
        lay.addWidget(bb)
        self._validate()

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "WoW AddOns folder", self.path.text())
        if d:
            self.path.setText(d.replace("/", "\\"))

    def _validate(self):
        p = Path(self.path.text().strip())
        if p.is_dir():
            n = sum(1 for d in p.iterdir() if d.is_dir() and (d / f"{d.name}.toc").exists())
            self.status.setText(f"✔  Found - {n} addon folder(s)")
        else:
            self.status.setText("✖  Folder not found")

    def values(self):
        return self.path.text().strip(), self.launch.isChecked()


class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Echo Watch")
        self.resize(1040, 560)
        self.cfg = store.load()
        self.pool = QThreadPool.globalInstance()
        self.pending = 0
        self.busy = False

        title = QLabel("Echo Watch")
        title.setObjectName("title")
        self.sub = QLabel("")
        self.sub.setObjectName("sub")
        self.btn_check = QPushButton("Check all")
        self.btn_check.setObjectName("primary")
        self.btn_check.clicked.connect(self.check_all)
        head = QHBoxLayout()
        tcol = QVBoxLayout()
        tcol.addWidget(title)
        tcol.addWidget(self.sub)
        head.addLayout(tcol)
        head.addStretch()
        head.addWidget(self.btn_check)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # no dotted focus box on cells
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.row_menu)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        hh = self.table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hh.setHighlightSections(False)
        for c, w in ((0, 260), (1, 200), (2, 180)):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(c, w)
        hh.setSectionResizeMode(len(COLS) - 1, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._buttons)
        self.table.doubleClicked.connect(lambda: self.edit_item())

        self.btn_update = QPushButton("Update now")
        self.btn_update_all = QPushButton("Update all")
        for b_ in (self.btn_update, self.btn_update_all):
            b_.setObjectName("accent")
        self.btn_add = QPushButton("Add...")
        self.btn_scan = QPushButton("Scan AddOns")
        self.btn_dir = QPushButton("Settings...")
        for b_, fn in ((self.btn_update, self.update_now), (self.btn_update_all, self.update_all),
                       (self.btn_add, self.add_item), (self.btn_scan, self.scan_addons),
                       (self.btn_dir, self.open_settings)):
            b_.clicked.connect(fn)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(self.btn_update)
        bar.addWidget(self.btn_update_all)
        bar.addStretch()
        for b_ in (self.btn_add, self.btn_scan, self.btn_dir):
            bar.addWidget(b_)

        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(12)
        lay.addLayout(head)
        lay.addWidget(self.table, 1)
        lay.addLayout(bar)
        self.setCentralWidget(root)

        self.reload_table()
        self._buttons()

    # -- data helpers ------------------------------------------------------
    def items(self):
        return self.cfg["items"]

    def save(self):
        store.save(self.cfg)

    def row_of(self, ref):
        return next((i for i, x in enumerate(self.items()) if x is ref), None)

    def have(self, it):
        if it["kind"] in ADDON_KINDS:
            return sources.installed_version(self.cfg["addons_dir"], it["folder"])
        return it.get("seen")

    def status(self, it):
        """(text, color, can_update)"""
        if it.get("error"):
            return "Check failed", BAD, False
        latest = it.get("latest")
        if not latest:
            return "Not checked", MUTED, False
        if it["kind"] in ADDON_KINDS:
            have, can = self.have(it), bool(it.get("download"))
            if have is None:
                return "Not installed", WARN, can
            if sources.version_unknown(have):
                return "Version unknown", WARN, can
            if sources.norm_version(have) == sources.norm_version(latest):
                return "Up to date", GOOD, False
            return "Update available", WARN, can
        if it.get("seen") == latest:
            return "Up to date", GOOD, False
        if it["kind"] == "commits" and it.get("ahead"):
            return f"{it['ahead']} new commits", WARN, False
        return "New", WARN, False

    def reload_table(self):
        self.table.setRowCount(len(self.items()))
        for r in range(len(self.items())):
            self.render_row(r)
        last = self.cfg.get("last_check")
        self.sub.setText(("Last checked " + time.strftime("%a %b %d, %H:%M", time.localtime(last))
                          if last else "Never checked") + "   \u00B7   right-click a row for more")

    def render_row(self, r):
        it = self.items()[r]
        text, color, _ = self.status(it)
        calm = text in ("Up to date", "Not checked")
        have, latest = self.have(it), it.get("latest")
        if calm or not latest:
            version = have or "\u2014"
        else:
            version = f"{have or 'none'}  \u2192  {latest}"
        detail = it.get("error") or it.get("note") or ""
        vals = [f"{KIND_ICON[it['kind']]}   {it['name']}", version,
                f"{STATUS_ICON.get(text, chr(0x25CF))}  {text}", detail]
        for c, v in enumerate(vals):
            cell = QTableWidgetItem(v)
            cell.setToolTip(f"{KINDS[it['kind']]}\n{detail}" if c == 0 else v)
            if c == 1 and calm:
                cell.setForeground(MUTED)
            if c == 2:
                cell.setForeground(color)
                if not calm:
                    f = QFont()
                    f.setBold(True)
                    cell.setFont(f)
            elif c == 3:
                cell.setForeground(MUTED)
            self.table.setItem(r, c, cell)

    def row_menu(self, pos):
        r = self.table.rowAt(pos.y())
        if r < 0:
            return
        self.table.selectRow(r)
        it, idle = self.items()[r], not self.busy
        m = QMenu(self)
        a = m.addAction("Open page")
        a.setEnabled(bool(it.get("url")))
        a.triggered.connect(self.open_page)
        if it["kind"] not in ADDON_KINDS:
            a = m.addAction("Mark as seen")
            a.setEnabled(idle and bool(it.get("latest")))
            a.triggered.connect(self.mark_seen)
        m.addSeparator()
        a = m.addAction("Rename / edit...")
        a.setEnabled(idle)
        a.triggered.connect(self.edit_item)
        a = m.addAction("Remove")
        a.setEnabled(idle)
        a.triggered.connect(self.remove_item)
        m.exec(self.table.viewport().mapToGlobal(pos))

    def cur(self):
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else None

    def _buttons(self):
        r = self.cur()
        it = self.items()[r] if r is not None and r < len(self.items()) else None
        idle = not self.busy
        self.btn_check.setEnabled(idle)
        self.btn_update.setEnabled(idle and bool(it and self.status(it)[2]))
        self.btn_update_all.setEnabled(idle and any(self.status(i)[2] for i in self.items()))
        for b_ in (self.btn_add, self.btn_scan, self.btn_dir):
            b_.setEnabled(idle)

    def set_busy(self, busy, msg=""):
        self.busy = busy
        self.btn_check.setText("Working..." if busy else "Check all")
        if msg:
            self.statusBar().showMessage(msg)
        self._buttons()

    # -- checking ----------------------------------------------------------
    def check_all(self):
        if self.busy or not self.items():
            return
        self.set_busy(True, "Checking...")
        for it in list(self.items()):
            self.pending += 1
            job = CheckJob(it)
            job.signals.done.connect(self.on_checked)
            self.pool.start(job)

    def on_checked(self, ref, res, err):
        row = self.row_of(ref)
        if row is not None:
            if res is None:
                ref["error"] = err
            else:
                apply_result(ref, res)
            self.render_row(row)
        self.pending -= 1
        if self.pending == 0:
            self.cfg["last_check"] = time.time()
            self.save()
            self.reload_table()
            self.set_busy(False)
            n = sum(1 for i in self.items() if self.status(i)[1] in (WARN, BAD))
            self.statusBar().showMessage(f"{n} item(s) need attention" if n else "Everything is current", 8000)

    # -- row actions -------------------------------------------------------
    def open_page(self):
        r = self.cur()
        if r is not None and self.items()[r].get("url"):
            webbrowser.open(self.items()[r]["url"])

    def mark_seen(self):
        r = self.cur()
        if r is None:
            return
        it = self.items()[r]
        it["seen"], it["seen_full"], it["ahead"] = it.get("latest"), it.get("latest_full"), None
        self.save()
        self.render_row(r)
        self._buttons()

    def _confirm_install(self, refs):
        names = "\n".join(f"  {i['name']}  {i.get('latest', '')}" for i in refs)
        ask = QMessageBox.question(
            self, "Update", f"Install into\n{self.cfg['addons_dir']}\n\n{names}\n\n"
            "Current folders are moved to a backup first.")
        return ask == QMessageBox.StandardButton.Yes

    def _run_install(self, refs):
        job = InstallJob(refs, self.cfg["addons_dir"])
        job.signals.done.connect(self.on_installed)
        self.pending = len(refs)
        self.set_busy(True, "Installing...")
        self.pool.start(job)

    def update_now(self):
        r = self.cur()
        if r is not None and self._confirm_install([self.items()[r]]):
            self._run_install([self.items()[r]])

    def update_all(self):
        refs = [i for i in self.items() if i["kind"] in ADDON_KINDS and self.status(i)[2]
                and self.status(i)[0] != "Not installed"]
        if refs and self._confirm_install(refs):
            self._run_install(refs)

    def on_installed(self, ref, res, err):
        row = self.row_of(ref)
        if res is None:
            ref["error"] = f"update failed: {err}"
        else:
            apply_result(ref, res)
        if row is not None:
            self.render_row(row)
        self.pending -= 1
        if self.pending == 0:
            self.save()
            self.set_busy(False)
            failed = [i["name"] for i in self.items() if str(i.get("error", "")).startswith("update failed")]
            self.statusBar().showMessage(
                ("Some updates failed: " + ", ".join(failed)) if failed
                else f"Update complete  (backups in {store.BACKUPS})", 12000)
            self.reload_table()

    # -- list management ---------------------------------------------------
    def _duplicate(self, new, ignore=None):
        for i in self.items():
            if i is ignore:
                continue
            if new["kind"] in ADDON_KINDS and i["kind"] in ADDON_KINDS:
                if i["folder"].lower() == new["folder"].lower():
                    return True
            elif i["kind"] == new["kind"] and i["repo"].lower() == new["repo"].lower() \
                    and i.get("branch", "") == new.get("branch", ""):
                return True
        return False

    def add_item(self):
        d = AddDialog(self)
        if d.exec():
            it = d.item()
            if self._duplicate(it):
                return QMessageBox.information(self, "Already watching", "That item is already on the list.")
            self.items().append(it)
            self.save()
            self.reload_table()

    def edit_item(self):
        r = self.cur()
        if r is None or self.busy:
            return
        old = self.items()[r]
        d = AddDialog(self)
        d.load(old)
        if d.exec():
            new = d.item()
            same_target = all(new.get(k) == old.get(k) for k in ("slug", "repo", "branch", "folder"))
            if same_target:  # rename only: keep tracked state
                old["name"] = new["name"]
            elif self._duplicate(new, ignore=old):
                return QMessageBox.information(self, "Already watching", "That item is already on the list.")
            else:
                self.items()[r] = new
            self.save()
            self.render_row(r)
            self._buttons()

    def remove_item(self):
        r = self.cur()
        if r is None:
            return
        ask = QMessageBox.question(self, "Remove", f"Stop watching {self.items()[r]['name']}?")
        if ask == QMessageBox.StandardButton.Yes:
            del self.items()[r]
            self.save()
            self.reload_table()
            self._buttons()

    def open_settings(self):
        d = SettingsDialog(self.cfg, self)
        if d.exec():
            self.cfg["addons_dir"], self.cfg["check_on_launch"] = d.values()
            self.save()
            self.reload_table()
            self._buttons()

    def scan_addons(self):
        if not Path(self.cfg["addons_dir"]).is_dir():
            QMessageBox.information(self, "AddOns folder", "The saved AddOns folder doesn't exist. "
                                    "Set the right one in Settings first.")
            return self.open_settings()
        known = [i["folder"] for i in self.items() if i["kind"] in ADDON_KINDS]
        slugs = [i["slug"] for i in self.items() if i["kind"] == "wago"]
        job = ScanJob(self.cfg["addons_dir"], known, slugs)
        job.signals.scanned.connect(self.on_scanned)
        self.set_busy(True, "Scanning AddOns folder and asking Wago...")
        self.pool.start(job)

    def on_scanned(self, found, misses):
        self.items().extend(found)
        self.save()
        self.reload_table()
        self.set_busy(False)
        msg = f"Added {len(found)} addon(s) found on Wago"
        self.statusBar().showMessage(msg + (f"; {misses} folder(s) not on Wago were skipped." if misses else "."), 12000)


def _log_crash(exc_type, exc, tb):
    """pythonw has no console, so write startup/runtime crashes to a file."""
    import traceback
    store.DATA_DIR.mkdir(parents=True, exist_ok=True)
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    (store.DATA_DIR / "error.log").write_text(text, encoding="utf-8")
    try:
        QMessageBox.critical(None, "Echo Watch crashed", f"{exc}\n\nDetails: {store.DATA_DIR / 'error.log'}")
    except Exception:
        pass


def main():
    sys.excepthook = _log_crash
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    w = Main()
    w.show()
    if "--check" in sys.argv or w.cfg.get("check_on_launch"):
        w.check_all()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
