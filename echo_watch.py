"""Echo Watch - one-click daily update check for WoW addons and GitHub projects."""
import sys
import time
import webbrowser

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QDialog,
                             QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
                             QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
                             QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
                             QWidget)

from core import sources, store

KINDS = {"wago": "WoW addon (Wago)", "release": "GitHub releases", "commits": "GitHub commits"}
KIND_ICON = {"wago": "🧩", "release": "📦", "commits": "🔀"}
STATUS_ICON = {"Up to date": "✔", "Update available": "⬆", "Not installed": "⬇",
               "New": "●", "Check failed": "✖", "Not checked": "○"}

STYLE = """
* { font-family: 'Segoe UI'; font-size: 13px; color: #d8dee9; }
QMainWindow, QDialog { background: #14171c; }
QLabel#title { font-size: 22px; font-weight: 600; color: #ffffff; }
QLabel#sub { color: #7f8896; }
QTableWidget { background: #1a1e25; border: 1px solid #262c36; border-radius: 8px;
               gridline-color: transparent; selection-background-color: #243447; }
QHeaderView::section { background: #14171c; color: #7f8896; border: none; padding: 6px; }
QPushButton { background: #232a35; border: 1px solid #2f3846; border-radius: 6px; padding: 7px 14px; }
QPushButton:hover { background: #2b3441; }
QPushButton:disabled { color: #566070; background: #1b2028; }
QPushButton#primary { background: #2fb5a8; color: #0b1114; border: none; font-weight: 600; padding: 9px 22px; }
QPushButton#primary:hover { background: #3fcabc; }
QPushButton#primary:disabled { background: #25544f; color: #0b1114; }
QLineEdit, QComboBox { background: #1a1e25; border: 1px solid #2f3846; border-radius: 6px; padding: 6px; }
"""

GOOD, WARN, BAD, MUTED = QColor("#5fd39a"), QColor("#f0b44c"), QColor("#ef6b6b"), QColor("#7f8896")
COLS = ["Name", "Type", "Current", "New", "Status", "What changed"]


class Signals(QObject):
    done = pyqtSignal(int, object, str)  # row, result dict | None, error


class CheckJob(QRunnable):
    def __init__(self, row, item):
        super().__init__()
        self.row, self.item, self.signals = row, dict(item), Signals()

    def run(self):
        try:
            res = sources.run_check(self.item)
            if self.item["kind"] == "commits" and self.item.get("seen_full"):
                res["ahead"] = sources.commits_since(
                    self.item["repo"], self.item["seen_full"], res["full_sha"])
            self.signals.done.emit(self.row, res, "")
        except sources.CheckError as e:
            self.signals.done.emit(self.row, None, str(e))
        except Exception as e:  # keep the pool alive on surprises
            self.signals.done.emit(self.row, None, f"{e.__class__.__name__}: {e}")


class InstallJob(QRunnable):
    def __init__(self, row, url, addons_dir):
        super().__init__()
        self.row, self.url, self.addons_dir = row, url, addons_dir
        self.signals = Signals()

    def run(self):
        try:
            tops = sources.install_zip(self.url, self.addons_dir, store.BACKUPS)
            self.signals.done.emit(self.row, {"installed": tops}, "")
        except Exception as e:
            self.signals.done.emit(self.row, None, str(e))


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
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.hint)
        lay.addWidget(bb)
        self.kind.currentIndexChanged.connect(self._sync)
        self._sync()

    def _sync(self):
        if self.kind.currentData() == "wago":
            self.target_label.setText("Wago slug")
            self.target.setPlaceholderText("auctionator")
            self.extra_label.setText("AddOns folder")
            self.extra.setPlaceholderText("Auctionator")
            self.hint.setText("Slug is the end of addons.wago.io/addons/<slug>. The installed version "
                              "is read from the folder's .toc. Release flavor: forever.")
        else:
            self.target_label.setText("Repo")
            self.target.setPlaceholderText("owner/name")
            self.extra_label.setText("Branch (commits)")
            self.extra.setPlaceholderText("default branch")
            self.hint.setText("Baselined on first check, then flagged when something new appears.")

    def load(self, it):
        self.setWindowTitle("Edit item")
        self.kind.setCurrentIndex(self.kind.findData(it["kind"]))
        self.kind.setEnabled(False)
        self.name.setText(it["name"])
        self.target.setText(it.get("slug") or it.get("repo", ""))
        self.extra.setText(it.get("folder") or it.get("branch", ""))

    def item(self):
        k = self.kind.currentData()
        t, x, n = self.target.text().strip(), self.extra.text().strip(), self.name.text().strip()
        if not t:
            return None
        if k == "wago":
            return {"name": n or t, "kind": k, "slug": t, "flavor": "forever", "folder": x or n or t}
        it = {"name": n or t, "kind": k, "repo": t}
        if k == "commits" and x:
            it["branch"] = x
        return it


class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Echo Watch")
        self.resize(1000, 540)
        self.cfg = store.load()
        self.pool = QThreadPool.globalInstance()
        self.pending = 0

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
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(len(COLS) - 1, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._buttons)
        self.table.doubleClicked.connect(lambda: self.edit_item())

        self.btn_open = QPushButton("Open page")
        self.btn_update = QPushButton("Update now")
        self.btn_seen = QPushButton("Mark seen")
        self.btn_add = QPushButton("Add...")
        self.btn_edit = QPushButton("Rename / edit...")
        self.btn_edit.clicked.connect(self.edit_item)
        self.btn_remove = QPushButton("Remove")
        self.btn_dir = QPushButton("AddOns folder...")
        self.btn_open.clicked.connect(self.open_page)
        self.btn_update.clicked.connect(self.update_now)
        self.btn_seen.clicked.connect(self.mark_seen)
        self.btn_add.clicked.connect(self.add_item)
        self.btn_remove.clicked.connect(self.remove_item)
        self.btn_dir.clicked.connect(self.pick_dir)
        bar = QHBoxLayout()
        for b in (self.btn_open, self.btn_update, self.btn_seen):
            bar.addWidget(b)
        bar.addStretch()
        for b in (self.btn_add, self.btn_edit, self.btn_remove, self.btn_dir):
            bar.addWidget(b)

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

    def have(self, it):
        if it["kind"] == "wago":
            return sources.installed_version(self.cfg["addons_dir"], it["folder"])
        return it.get("seen")

    def status(self, it):
        """(text, color, can_update)"""
        if it.get("error"):
            return "Check failed", BAD, False
        latest = it.get("latest")
        if not latest:
            return "Not checked", MUTED, False
        if it["kind"] == "wago":
            have = self.have(it)
            can = bool(it.get("download"))
            if have is None:
                return "Not installed", WARN, can
            if have == latest:
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
        self.sub.setText("Last checked " + time.strftime("%a %b %d, %H:%M", time.localtime(last))
                         if last else "Never checked - press Check all")

    def render_row(self, r):
        it = self.items()[r]
        text, color, _ = self.status(it)
        detail = it.get("error") or it.get("note") or ""
        icon = STATUS_ICON.get(text, "●" if "commit" in text else "")
        newer = text not in ("Up to date", "Not checked", "Check failed")
        vals = [it["name"], f"{KIND_ICON[it['kind']]}  {KINDS[it['kind']]}", self.have(it) or "-",
                (it.get("latest") or "-") if newer else "-", f"{icon}  {text}", detail]
        for c, v in enumerate(vals):
            cell = QTableWidgetItem(v)
            if c == 4:
                cell.setForeground(color)
                f = QFont()
                f.setBold(True)
                cell.setFont(f)
            elif c == 5:
                cell.setForeground(MUTED)
            self.table.setItem(r, c, cell)

    def cur(self):
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else None

    def _buttons(self):
        r = self.cur()
        it = self.items()[r] if r is not None else None
        self.btn_open.setEnabled(bool(it and it.get("url")))
        self.btn_seen.setEnabled(bool(it and it["kind"] != "wago" and it.get("latest")))
        self.btn_update.setEnabled(bool(it and self.status(it)[2]))
        self.btn_remove.setEnabled(it is not None)
        self.btn_edit.setEnabled(it is not None)

    # -- actions -----------------------------------------------------------
    def check_all(self):
        if self.pending or not self.items():
            return
        self.btn_check.setEnabled(False)
        self.btn_check.setText("Checking...")
        for r, it in enumerate(self.items()):
            self.pending += 1
            job = CheckJob(r, it)
            job.signals.done.connect(self.on_checked)
            self.pool.start(job)

    def on_checked(self, row, res, err):
        it = self.items()[row]
        if res is None:
            it["error"] = err
        else:
            it.pop("error", None)
            it["latest"], it["url"] = res["latest"], res["url"]
            it["note"], it["download"] = res.get("note", ""), res.get("download")
            it["latest_full"] = res.get("full_sha")
            it["ahead"] = res.get("ahead")
            if it["kind"] != "wago" and it.get("seen") is None:  # baseline silently
                it["seen"], it["seen_full"] = res["latest"], res.get("full_sha")
        self.render_row(row)
        self.pending -= 1
        if self.pending == 0:
            self.cfg["last_check"] = time.time()
            self.save()
            self.btn_check.setEnabled(True)
            self.btn_check.setText("Check all")
            self.reload_table()
            self._buttons()
            n = sum(1 for i in self.items() if self.status(i)[1] == WARN)
            self.statusBar().showMessage(f"{n} item(s) need attention" if n else "Everything is current", 6000)

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

    def update_now(self):
        r = self.cur()
        if r is None:
            return
        it = self.items()[r]
        ask = QMessageBox.question(
            self, "Update", f"Install {it['name']} {it['latest']} into\n{self.cfg['addons_dir']}?\n\n"
            "The current folder is moved to a backup first.")
        if ask != QMessageBox.StandardButton.Yes:
            return
        job = InstallJob(r, it["download"], self.cfg["addons_dir"])
        job.signals.done.connect(self.on_installed)
        self.btn_update.setEnabled(False)
        self.statusBar().showMessage(f"Installing {it['name']}...")
        self.pool.start(job)

    def on_installed(self, row, res, err):
        if res is None:
            QMessageBox.warning(self, "Update failed", err)
            self.statusBar().clearMessage()
        else:
            self.statusBar().showMessage(
                "Installed: " + ", ".join(res["installed"]) + f"  (backups in {store.BACKUPS})", 10000)
        self.render_row(row)
        self._buttons()

    def add_item(self):
        d = AddDialog(self)
        if d.exec():
            it = d.item()
            if it:
                self.items().append(it)
                self.save()
                self.reload_table()

    def edit_item(self):
        r = self.cur()
        if r is None:
            return
        old = self.items()[r]
        d = AddDialog(self)
        d.load(old)
        if d.exec():
            new = d.item()
            if not new:
                return
            same_target = all(new.get(k) == old.get(k) for k in ("slug", "repo", "branch", "folder"))
            if same_target:  # rename only: keep all tracked state
                old["name"] = new["name"]
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

    def pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "WoW AddOns folder", self.cfg["addons_dir"])
        if d:
            self.cfg["addons_dir"] = d
            self.save()
            self.reload_table()


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    w = Main()
    w.show()
    if "--check" in sys.argv:
        w.check_all()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
