"""Render assets/logo.svg to assets/icon.ico (256px) for the exe."""
import sys
from pathlib import Path

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QGuiApplication, QImage, QPainter
from PyQt6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parent.parent
app = QGuiApplication(sys.argv)
size = 256
img = QImage(size, size, QImage.Format.Format_ARGB32)
img.fill(Qt.GlobalColor.transparent)
painter = QPainter(img)
painter.setRenderHint(QPainter.RenderHint.Antialiasing)
QSvgRenderer(str(ROOT / "assets" / "logo.svg")).render(painter, QRectF(0, 0, size, size))
painter.end()
out = ROOT / "assets" / "icon.ico"
if not img.save(str(out), "ICO"):
    sys.exit("could not write icon.ico (Qt ICO writer unavailable)")
print("wrote", out)
