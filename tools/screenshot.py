"""Arayuzun goruntusunu PNG olarak kaydeder (belge/inceleme icin).

    python tools/screenshot.py cikti.png
"""
import os, sys
os.environ.setdefault("UMLSTUDIO_SETTINGS_SCOPE", "test")   # kullanici ayarlarina DOKUNMA
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# Not: "offscreen" platformu yazi cizmez; gercek platform kullaniyoruz.
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication
from app.ui.main_window import MainWindow
from app.ui.theme import stylesheet, ui_font

# --light: goruntuyu ACIK temayla al (varsayilan koyu).
#
# Tema, pencere KURULDUKTAN SONRA uygulanmali: MainWindow._restore_state()
# QSettings'teki kayitli temayi geri yukler ve daha once uygulanan secimi
# ezer. Bu, uygulamada DOGRU davranistir; goruntu araci ona uyar.
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
