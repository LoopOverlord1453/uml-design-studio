"""Depo (Git) panelinin goruntusunu PNG olarak kaydeder.

    python tools/screenshot_git.py cikti.png

Gecici bir calisma alani kurar, kodu uretir, birkac commit atar ve bir dal
acar; boylece agacta gercek bir gecmis gorunur.
"""
import os
import shutil
import subprocess
import sys
import tempfile



os.environ.setdefault("UMLSTUDIO_SETTINGS_SCOPE", "test")   # kullanici ayarlarina DOKUNMA

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tmpdir import remove_tree   # noqa: E402
# Not: "offscreen" platformu yazi cizmez; gercek platform kullaniyoruz.
from PyQt6.QtCore import QTimer                                    # noqa: E402
from PyQt6.QtWidgets import QApplication                           # noqa: E402

from app.core.git_backend import Repo, git_available               # noqa: E402
from app.core.workspace import Workspace                           # noqa: E402
from app.ui.main_window import MainWindow                          # noqa: E402
from app.ui.theme import stylesheet, ui_font                       # noqa: E402

AUTHOR = ("UML Design Studio", "studio@example.invalid")
out = sys.argv[1] if len(sys.argv) > 1 else "git_panel.png"

if not git_available():
    sys.stderr.write("git bulunamadi.\n")
    sys.exit(2)

tmp = tempfile.mkdtemp(prefix="umlshot_")
ws = Workspace.create(os.path.join(tmp, "version-control"), name="version-control")
repo = Repo(ws.root)
repo.init()

app = QApplication(sys.argv)
app.setStyle("Fusion")
app.setFont(ui_font(9))
app.setStyleSheet(stylesheet())

win = MainWindow()
win.resize(1680, 960)
win.apply_workspace(ws)
win.build()
app.processEvents()


def commit(message: str) -> None:
    repo.stage_all()
    repo.commit(message, author_name=AUTHOR[0], author_email=AUTHOR[1])


commit("initial: generated state machine")

# ikinci commit
gen = ws.generated_path
first = sorted(n for n in os.listdir(gen) if n.endswith(".c"))[0]
with open(os.path.join(gen, first), "a", encoding="utf-8") as fh:
    fh.write("\n/* review note */\n")
commit("review guard conditions")

# bir dal + birlesme -> agacta ikinci serit gorunsun.
# Dal AYRI bir dosyaya dokunur; boylece birlesme cakismadan tamamlanir.
repo.create_branch("feature/calibration", checkout=True)
with open(os.path.join(gen, "calibration.h"), "w", encoding="utf-8") as fh:
    fh.write("#ifndef CALIBRATION_H\n#define CALIBRATION_H\n#endif\n")
commit("add calibration hook")
base = repo.branches()[0] if repo.branches() else "main"
for name in repo.branches():
    if name != "feature/calibration":
        base = name
        break
repo.checkout(base)
with open(os.path.join(gen, first), "a", encoding="utf-8") as fh:
    fh.write("\n/* fix on main */\n")
commit("small fix on main")
subprocess.run(["git",
                "-c", "user.name=%s" % AUTHOR[0],
                "-c", "user.email=%s" % AUTHOR[1],
                "merge", "--no-ff", "-m", "merge feature/calibration",
                "feature/calibration"], cwd=ws.root,
               stdout=subprocess.PIPE, stderr=subprocess.PIPE)

# calisma agacinda birkac degisiklik biraksin ki listeler dolu gorunsun
with open(os.path.join(gen, first), "a", encoding="utf-8") as fh:
    fh.write("\n/* unstaged change */\n")
with open(os.path.join(ws.root, "NOTES.md"), "w", encoding="utf-8") as fh:
    fh.write("# Notes\n\nNew file.\n")
repo.stage(["NOTES.md"])

win.show()
win.show_git_tab()


def grab() -> None:
    """Paneli ISTENEN TEMALARDA yakalar.

    Ikinci arguman "both" ise hem koyu hem acik tema kaydedilir:
    <cikti>-dark.png ve <cikti>-light.png.
    """
    from app.ui.theme import stylesheet as _ss
    hedefler = ["dark"]
    if len(sys.argv) > 2 and sys.argv[2] == "both":
        hedefler = ["dark", "light"]
    for tema in hedefler:
        win.set_theme(tema)
        app.setStyleSheet(_ss())
        for _ in range(30):
            app.processEvents()
        win.git_panel.refresh()
        for _ in range(30):
            app.processEvents()
        if win.git_panel.graph._commits:
            win.git_panel.graph._set_current(0)
        for _ in range(20):
            app.processEvents()
        yol = out if len(hedefler) == 1 else out.replace(".png", "-%s.png" % tema)
        win.grab().save(yol)
        print("kaydedildi:", yol)
    win.settings.clear()
    app.quit()


QTimer.singleShot(1200, grab)
code = app.exec()
remove_tree(tmp)
sys.exit(code)
