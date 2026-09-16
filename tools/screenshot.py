"""Saves a picture of the user interface as a PNG (for docs / review).

    python tools/screenshot.py output.png
"""
import os, sys
os.environ.setdefault("UMLSTUDIO_SETTINGS_SCOPE", "test")   # DO NOT touch user settings
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# Note: the "offscreen" platform draws no text; we use the real one.
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication
from app.ui.main_window import MainWindow
from app.ui.theme import stylesheet, ui_font

# --light: capture with the LIGHT theme (dark is the default).
#
# The theme must be applied AFTER the window is built: MainWindow._restore_state()
# reloads the theme stored in QSettings and overrides a choice applied
# earlier. That is the CORRECT behaviour in the app; this tool follows it.
LIGHT_SHOT = "--light" in sys.argv
if LIGHT_SHOT:
    sys.argv.remove("--light")

out = sys.argv[1] if len(sys.argv) > 1 else "screenshot.png"

app = QApplication(sys.argv)
app.setStyle("Fusion")
app.setFont(ui_font(9))
app.setStyleSheet(stylesheet())
win = MainWindow()
if LIGHT_SHOT:
    win.set_theme("light")
win.resize(1680, 960)
win.show()

def grab():
    app.processEvents()
    win.canvas.zoom_fit()
    app.processEvents()
    win.grab().save(out)
    win.settings.clear()
    print("kaydedildi:", out)
    app.quit()

QTimer.singleShot(900, grab)
sys.exit(app.exec())
