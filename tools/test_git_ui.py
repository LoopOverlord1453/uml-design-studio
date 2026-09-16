"""Calisma alani + Git panelini ARAYUZ uzerinden uctan uca sinar.

    python tools/test_git_ui.py

Gercek bir gecici calisma alani olusturulur, pencere ekransiz kurulur,
uretilen kod diske yazilir, dosyalar hazirlanip commit'lenir ve commit
agacinin/fark goruntuleyicinin dogru doldugu dogrulanir.

git kurulu degilse git'e bagli adimlar ATLANIR; calisma alani adimlari yine
kosar.
"""

from __future__ import annotations



import os

os.environ.setdefault("UMLSTUDIO_SETTINGS_SCOPE", "test")   # kullanici ayarlarina DOKUNMA
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tmpdir import remove_tree   # noqa: E402
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication                          # noqa: E402

from app.core.git_backend import Repo, git_available              # noqa: E402
from app.core.workspace import Workspace                          # noqa: E402
from app.ui.main_window import MainWindow, _workspace_from_argv   # noqa: E402
from app.ui.theme import stylesheet, ui_font                      # noqa: E402
from app.ui.workspace_dialog import WorkspaceDialog               # noqa: E402

_failures: list = []
_passed = 0


def check(cond: bool, label: str, detail: str = "") -> None:
    global _passed
    if cond:
        _passed += 1
        print("  [ TAMAM ] %s" % label)
    else:
        _failures.append(label)
        print("  [ HATA  ] %s%s" % (label, ("  -- " + detail) if detail else ""))


def section(title: str) -> None:
    print("\n== %s ==" % title)


def dosya_sayisi(agac) -> int:
    """Agactaki DOSYA dugumlerinin sayisi (klasorler sayilmaz)."""
    from app.ui.git_panel import PATH_ROLE
    n = 0

    def gez(dugum):
        nonlocal n
        for i in range(dugum.childCount()):
            cocuk = dugum.child(i)
            if cocuk.data(0, PATH_ROLE):
                n += 1
            gez(cocuk)

    gez(agac.invisibleRootItem())
    return n


def ilk_dosyayi_sec(agac) -> bool:
    """Agactaki ilk DOSYA dugumunu secer."""
    from app.ui.git_panel import PATH_ROLE
    bulunan = []

    def gez(dugum):
        for i in range(dugum.childCount()):
            cocuk = dugum.child(i)
            if cocuk.data(0, PATH_ROLE) and not bulunan:
                bulunan.append(cocuk)
                return
            gez(cocuk)

    gez(agac.invisibleRootItem())
    if bulunan:
        agac.setCurrentItem(bulunan[0])
        return True
    return False


def klasor_sayisi(agac) -> int:
    """Agactaki KLASOR dugumlerinin sayisi."""
    from app.ui.git_panel import FOLDER_ROLE
    n = 0

    def gez(dugum):
        nonlocal n
        for i in range(dugum.childCount()):
            cocuk = dugum.child(i)
            if cocuk.data(0, FOLDER_ROLE):
                n += 1
            gez(cocuk)

    gez(agac.invisibleRootItem())
    return n


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(ui_font(9))
    app.setStyleSheet(stylesheet())

    tmp = tempfile.mkdtemp(prefix="umlui_")
    try:
        run_all(tmp, app)
    finally:
        remove_tree(tmp)

    print("\n== Ozet ==")
    if _failures:
        print("  %d kontrol basarisiz:" % len(_failures))
        for f in _failures:
            print("    - %s" % f)
        return 1
    print("  Tum calisma alani / git arayuz kontrolleri gecti (%d)." % _passed)
    return 0


def run_all(tmp: str, app) -> None:
    has_git = git_available()
    if not has_git:
        print("  (git yok -- git adimlari atlanacak)")

    # ------------------------------------------------------------ komut satiri
    section("1. Komut satiri secenegi")
    check(_workspace_from_argv(["--workspace", "C:/x"]) == "C:/x",
          "--workspace <yol> okundu")
    check(_workspace_from_argv(["--workspace=C:/y"]) == "C:/y",
          "--workspace=<yol> okundu")
    check(_workspace_from_argv([]) == "", "secenek yoksa bos")
    check(_workspace_from_argv(["--workspace"]) == "",
          "eksik deger coksmedi")

    # ------------------------------------------------------------ diyalog
    section("2. Calisma alani diyalogu")
    existing = os.path.join(tmp, "var-olan")
    os.makedirs(existing, exist_ok=True)
    dlg = WorkspaceDialog([existing], None, allow_cancel=True)
    check(dlg.rb_recent.isChecked(), "son kullanilan varsa o secili gelir")
    check(dlg.recent_list.count() == 1, "son kullanilan listelendi")
    check(os.path.normcase(dlg._target_root()) == os.path.normcase(existing),
          "hedef kok son kullanilan", dlg._target_root())
    dlg.rb_new.setChecked(True)
    dlg.ed_parent.setText(tmp)
    dlg.ed_name.setText("yeni-alan")
    check(os.path.normcase(dlg._target_root())
          == os.path.normcase(os.path.join(tmp, "yeni-alan")),
          "yeni kip hedefi birlestirdi", dlg._target_root())
    check(dlg.btn_ok.isEnabled(), "hedef varken Ac etkin")
    dlg.ed_name.setText("")
    check(not dlg.btn_ok.isEnabled(), "hedef yokken Ac kapali")
    dlg.deleteLater()

    boş = WorkspaceDialog([], None)
    check(boş.rb_new.isChecked(), "son kullanilan yoksa 'yeni' secili")
    boş.deleteLater()

    # ------------------------------------------------------------ pencere
    section("3. Calisma alaninin pencereye uygulanmasi")
    root = os.path.join(tmp, "calisma")
    ws = Workspace.create(root, name="Calisma")

    win = MainWindow()
    check(win.workspace is None, "acilista calisma alani yok")
    check(win.mode_tabs.count() == 3, "uc kip sekmesi var",
          str(win.mode_tabs.count()))
    check(win.mode_tabs.tabText(2) == "Repository (Git)", "ucuncu sekme depo",
          win.mode_tabs.tabText(2))

    win.apply_workspace(ws, init_git=has_git)
    app.processEvents()
    check(win.workspace is not None, "calisma alani benimsendi")
    check("Calisma" in win.lbl_workspace.text(), "durum cubugunda ad var",
          win.lbl_workspace.text())
    check("Calisma" in win.windowTitle(), "pencere basliginda ad var",
          win.windowTitle())

    # ------------------------------------------------------------ kod yazimi
    section("4. Uretilen kodun calisma alanina yazilmasi")
    win.build()
    app.processEvents()
    gen = ws.generated_path
    produced = sorted(os.listdir(gen)) if os.path.isdir(gen) else []
    check(bool(produced), "generated/ dolduruldu", str(produced))
    check(any(n.endswith(".c") for n in produced), "C dosyasi yazildi",
          str(produced))
    check(any(n.endswith(".h") for n in produced), "baslik yazildi",
          str(produced))
    check(any("_main." in n for n in produced), "MCU ornegi yazildi",
          str(produced))

    before = {n: os.path.getmtime(os.path.join(gen, n)) for n in produced}
    win.build()
    app.processEvents()
    after = {n: os.path.getmtime(os.path.join(gen, n)) for n in produced}
    check(before == after, "degismeyen dosyalar YENIDEN yazilmadi")

    win.a_auto_write.setChecked(False)
    win.toggle_auto_write(False)
    check(win.workspace.auto_write is False, "kendiliginden yazma kapandi")
    win.a_auto_write.setChecked(True)
    win.toggle_auto_write(True)
    check(win.workspace.auto_write is True, "kendiliginden yazma acildi")

    # ------------------------------------------------------------ git paneli
    if not has_git:
        win.close()
        return

    section("5. Git paneli -- durum")
    panel = win.git_panel
    check(panel.repo is not None, "panel depoyu benimsedi")
    check(panel.repo.is_repo(), "calisma alani git deposu")
    panel.refresh()
    app.processEvents()
    check(dosya_sayisi(panel.list_unstaged) > 0, "hazirlanmamis dosyalar listelendi",
          str(dosya_sayisi(panel.list_unstaged)))
    check(dosya_sayisi(panel.list_staged) == 0, "hazirlanmis liste bos")
    check(not panel.notice.isVisibleTo(panel), "uyari seridi gizli",
          panel.notice.text())
    check("⎇" in panel.lbl_branch.text(), "dal etiketi dolduruldu",
          panel.lbl_branch.text())
    check("change" in win.lbl_git.text(), "durum cubugu git ozeti",
          win.lbl_git.text())

    # KLASOR YAPISI. Duz liste her satirda tam yolu yaziyordu ve calisma
    # alani buyudukce okunaksizdi; artik agac klasorleri gosterir.
    check(klasor_sayisi(panel.list_unstaged) > 0,
          "degisen dosyalar KLASOR AGACI olarak gosteriliyor",
          "klasor dugumu: %d" % klasor_sayisi(panel.list_unstaged))
    from app.ui.git_panel import FOLDER_ROLE, PATH_ROLE
    kok = panel.list_unstaged.invisibleRootItem()
    ust_duzey = [kok.child(i) for i in range(kok.childCount())]
    klasorler = [c.text(0) for c in ust_duzey if c.data(0, FOLDER_ROLE)]
    # Klasor satiri "ad/   (n)" bicimindedir: kapaliyken de kac dosya
    # oldugunu soyler.
    check(all(k.rstrip().endswith(")") and "/" in k for k in klasorler),
          "klasor satirlari 'ad/  (dosya sayisi)' bicimde", str(klasorler))
    check(klasorler, "en az bir klasor dugumu var", str(klasorler))
    check(all("/" not in (c.text(0).split("  ")[-1])
              for c in ust_duzey if c.data(0, PATH_ROLE)),
          "dosya satirlarinda TAM YOL yok, yalnizca ad var",
          str([c.text(0) for c in ust_duzey]))

    # Klasoru secmek ICINDEKI dosyalari secer (topluca hazirlamak icin).
    klasor = next((c for c in ust_duzey if c.data(0, FOLDER_ROLE)), None)
    if klasor is not None:
        panel.list_unstaged.setCurrentItem(klasor)
        klasor.setSelected(True)
        app.processEvents()
        yollar = panel._selected_paths(panel.list_unstaged)
        check(len(yollar) >= 1,
              "klasor secimi icindeki dosyalari kapsiyor", str(yollar[:4]))
        check(all("/" in y for y in yollar),
              "kapsanan yollar TAM yol olarak doner", str(yollar[:4]))

    section("6. Fark goruntuleyici")
    ilk_dosyayi_sec(panel.list_unstaged)
    app.processEvents()
    text = panel.diff.toPlainText()
    check("+" in text, "izlenmeyen dosyanin farki gosterildi", text[:80])
    check("DIFF" in panel.diff_header.text(), "fark basligi guncellendi",
          panel.diff_header.text())
    check("unstaged" in panel.diff_header.text(),
          "fark basligi kaynagi bildirdi", panel.diff_header.text())

    section("7. Hazirlama ve commit")
    panel._stage_all()
    app.processEvents()
    check(dosya_sayisi(panel.list_staged) > 0, "tumu hazirlandi",
          str(dosya_sayisi(panel.list_staged)))
    check(panel.btn_commit.isEnabled(), "commit dugmesi etkinlesti")

    ilk_dosyayi_sec(panel.list_staged)
    app.processEvents()
    check("DIFF" in panel.diff_header.text(), "hazirlanmis fark basligi")
    check("staged" in panel.diff_header.text(),
          "hazirlanmis kaynagi bildirildi", panel.diff_header.text())

    n_staged = dosya_sayisi(panel.list_staged)
    panel._unstage_selected()
    app.processEvents()
    check(dosya_sayisi(panel.list_staged) == n_staged - 1,
          "secili dosya hazirliktan cikti",
          "%d -> %d" % (n_staged, dosya_sayisi(panel.list_staged)))

    # commit'i arka uctan yap (diyalog acmadan)
    panel.repo.stage_all()
    panel.repo.commit("ilk surum", author_name="UML Test",
                      author_email="test@example.invalid")
    panel.refresh()
    app.processEvents()
    check(dosya_sayisi(panel.list_staged) == 0, "commit sonrasi hazirlik bos")
    check(dosya_sayisi(panel.list_unstaged) == 0, "commit sonrasi calisma agaci temiz")
    check("clean" in win.lbl_git.text(), "durum cubugu temiz bildirdi",
          win.lbl_git.text())

    section("8. Commit agaci")
    graph = panel.graph
    check(len(graph._commits) == 1, "bir commit gorundu",
          str(len(graph._commits)))
    check(graph.current_sha() != "", "commit secili geldi")
    commit = graph.current_commit()
    check(commit is not None and commit.subject == "ilk surum",
          "commit konusu dogru", commit.subject if commit else "")
    check(commit is not None and commit.is_head, "HEAD isaretlendi")
    check(bool(commit.refs), "dal etiketi tasindi", str(commit.refs))
    body = panel.diff.toPlainText()
    check("+" in body, "commit farki gosterildi", body[:80])
    check("file(s)" in panel.diff_header.text() or "model" in panel.diff_header.text(),
          "commit basligi dosya sayisi",
          panel.diff_header.text())

    # ikinci commit -> agac buyumeli
    with open(os.path.join(gen, produced[0]), "a", encoding="utf-8") as fh:
        fh.write("\n/* ikinci surum */\n")
    panel.repo.stage_all()
    panel.repo.commit("ikinci surum", author_name="UML Test",
                      author_email="test@example.invalid")
    panel.refresh()
    app.processEvents()
    check(len(graph._commits) == 2, "iki commit gorundu",
          str(len(graph._commits)))
    check(all(c.lane == 0 for c in graph._commits),
          "duz gecmis tek seritte",
          str([c.lane for c in graph._commits]))

    # klavye ile gezinme
    graph._set_current(1)
    app.processEvents()
    check(graph.current_commit().subject == "ilk surum",
          "asagi gecis eski commit'i secti",
          graph.current_commit().subject)

    section("9. Dal ve cizim")
    panel.repo.create_branch("ozellik", checkout=True)
    panel.refresh()
    app.processEvents()
    check("ozellik" in panel.lbl_branch.text(), "yeni dal etiketi gorundu",
          panel.lbl_branch.text())

    # cizim gercekten kosuyor mu (paintEvent istisnasi sureci oldururdu)
    from PyQt6.QtGui import QPixmap
    pm = QPixmap(900, 300)
    graph.resize(900, 300)
    graph.viewport().resize(900, 300)
    graph.viewport().render(pm)
    check(not pm.isNull(), "commit agaci istisnasiz cizildi")

    section("10. Depo olmayan klasor")
    plain = os.path.join(tmp, "deposuz")
    os.makedirs(plain, exist_ok=True)
    panel.set_root(plain)
    app.processEvents()
    check(panel.notice.isVisibleTo(panel), "depo yok uyarisi gosterildi",
          panel.notice.text())
    check(panel.btn_init.isEnabled(), "'Depoyu Baslat' etkin")
    check(not panel.btn_commit.isEnabled(), "commit kapali")
    panel._init_repo()
    app.processEvents()
    check(Repo(plain).is_repo(), "panelden depo baslatildi")
    check(not panel.notice.isVisibleTo(panel), "uyari kalkti", panel.notice.text())

    panel.set_root(None)
    app.processEvents()
    check(panel.notice.isVisibleTo(panel), "calisma alani yokken uyari",
          panel.notice.text())

    win.close()


if __name__ == "__main__":
    sys.exit(main())
