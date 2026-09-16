"""ARAYUZ STANDARTLARI DENETIMI.

    python tools/test_standards.py

Bu dosya "calisiyor mu" sormaz -- onu test_functional.py yapar. Buradaki
soru "STANDARDA UYUYOR MU": urun musteriye teslim edilecek ve arayuzun
yerlesik masaustu kurallarina, UML 2.5.1 gosterimine ve erisilebilirlik
olcutlerine uymasi gerekiyor.

Dayanaklar:

  * OMG UML 2.5.1  -- 14.2.4 (StateMachine gosterimi), 11.4 (Classifier)
  * Windows/CUA menu duzeni  -- File, Edit, View, ..., Help sirasi;
    erisim harfleri; diyalog acan komutlarda "..."
  * WCAG 2.1 AA  -- 1.4.3 metin zitligi 4.5:1, 1.4.11 arayuz bileseni
    3:1, 2.1.1 klavye ile tam erisim, 3.3.2 etiketler
  * Qt yerlesik kurallari -- QDialogButtonBox ile platforma gore dugme
    sirasi, QMenu.addSection() ile bolum basligi

Her bolum, bulundugu kusuru ve neden kusur oldugunu yazar.
"""

from __future__ import annotations

import os

os.environ.setdefault("UMLSTUDIO_SETTINGS_SCOPE", "test")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import re                                                          # noqa: E402
import sys                                                         # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt6.QtCore import Qt                                        # noqa: E402
from PyQt6.QtGui import QAction                                    # noqa: E402
from PyQt6.QtWidgets import (QApplication, QLineEdit,             # noqa: E402
                             QPlainTextEdit, QToolButton, QWidget)

from app.ui.main_window import MainWindow                          # noqa: E402
from app.ui.theme import stylesheet, ui_font                       # noqa: E402

_failures = []
_bolum = [""]


def bolum(title: str) -> None:
    _bolum[0] = title
    print("\n== %s ==" % title)


def check(condition: bool, label: str, detail="") -> None:
    if condition:
        print("  [ TAMAM ] %s" % label)
        return
    _failures.append("%s -> %s" % (_bolum[0], label))
    print("  [ HATA  ] %s" % label)
    if detail != "":
        for satir in str(detail).splitlines()[:8]:
            print("           | %s" % satir)


def pump(app, n=10):
    for _ in range(n):
        app.processEvents()


def _duz(text: str) -> str:
    """Erisim harfi isaretini atilmis menu metni."""
    return text.replace("&&", "\x00").replace("&", "").replace("\x00", "&")


# --------------------------------------------------------------------------- #
#  1. Menu duzeni: sira, erisim harfleri, "..." kurali
# --------------------------------------------------------------------------- #

#: Masaustu kilavuzlarinin ortak sirasi. Alan menuleri ortada durur.
_ILK_IKI = ["File", "Edit"]

#: Erisim harfi BEKLENMEYEN menuler.
#:
#: Tool menusundeki araclarin her birinin zaten TEK HARFLI bir kisayolu
#: var (V, S, G, ...); ayrica erisim harfi vermek 21 oge icinde tekil
#: kalamaz ve kisayolla karisir. References ise bir KAYNAK LISTESIDIR,
#: komut degil -- uzun baslıklara harf atamak gurultu olurdu.
_HARF_MUAF = {"Tool", "References"}


def t_menu(win):
    bolum("1. Menu duzeni (Windows/CUA)")

    bar = win.menuBar()
    menuler = [(a.menu(), _duz(a.text())) for a in bar.actions()
               if a.menu() is not None]
    adlar = [ad for _m, ad in menuler]

    check(adlar[:2] == _ILK_IKI,
          "menu sirasi File, Edit ile basliyor", adlar)
    check(adlar[-1] == "Help", "Help EN SONDA", adlar)
    check(adlar.index("View") == 2, "View ucuncu sirada", adlar)
    check(adlar.index("Repository") > adlar.index("Edit"),
          "alan menuleri File-Edit bitisikligini BOLMUYOR", adlar)

    # -- Menu adlarinin erisim harfleri TEKIL ------------------------------- #
    harfler = []
    for a in bar.actions():
        if a.menu() is None:
            continue
        m = re.search(r"&(.)", a.text())
        check(m is not None, "menu '%s' erisim harfi tasiyor" % _duz(a.text()))
        if m:
            harfler.append(m.group(1).upper())
    check(len(harfler) == len(set(harfler)),
          "menu erisim harfleri TEKIL", harfler)

    # -- Menu ogeleri --------------------------------------------------------- #
    for menu, ad in menuler:
        ic_harf = []
        for act in menu.actions():
            if act.isSeparator():
                continue
            metin = act.text()
            duz = _duz(metin)
            m = re.search(r"&(.)", metin)
            if ad not in _HARF_MUAF and act.menu() is None:
                check(m is not None,
                      "%s > '%s' erisim harfi tasiyor" % (ad, duz))
            if m:
                ic_harf.append(m.group(1).upper())

            # "..." KURALI: komut tamamlanmadan once kullanicidan BILGI
            # istiyorsa uc nokta ile biter. Yalnizca bilgi GOSTEREN
            # komutlar (About, Shortcuts) bitmez.
            if duz.lower().rstrip(" .…") in ("open", "save as", "workspace",
                                             "export code"):
                check(duz.endswith("…"),
                      "%s > '%s' diyalog aciyor, ... ile bitmeli" % (ad, duz))
            for yasak in ("About", "Keyboard Shortcuts", "Reset Layout"):
                if duz.startswith(yasak):
                    check(not duz.endswith("…"),
                          "%s > '%s' diyalog istemiyor, ... ALMAMALI"
                          % (ad, duz))

        check(len(ic_harf) == len(set(ic_harf)),
              "%s menusunde erisim harfleri tekil" % ad,
              [h for h in ic_harf if ic_harf.count(h) > 1])

    # -- Bolum basliklari pasif OGE ile degil, addSection ile --------------- #
    #
    # Pasif bir QAction'i baslik gibi kullanmak, ekran okuyuculara
    # "devre disi menu ogesi" olarak duyurulur.
    pasif = []
    for menu, ad in menuler:
        for act in menu.actions():
            if (not act.isSeparator() and not act.isEnabled()
                    and act.menu() is None and not act.isCheckable()
                    and act.text() and not act.shortcut().toString()):
                pasif.append("%s > %s" % (ad, _duz(act.text())))
    check(not pasif, "menu basliklari pasif QAction ile yapilmiyor",
          "\n".join(pasif))

    # -- Kisaltilmis menu ogesi YOK ------------------------------------------ #
    kisa = []
    for menu, ad in menuler:
        for act in menu.actions():
            duz = _duz(act.text())
            if duz.endswith(".") and not duz.endswith("..."):
                kisa.append("%s > %s" % (ad, duz))
    check(not kisa, "menu ogeleri KISALTILMAMIS", "\n".join(kisa))


# --------------------------------------------------------------------------- #
#  2. Kisayollar: yerlesik baglamalar, cakisma yok
# --------------------------------------------------------------------------- #

#: Masaustu uygulamalarinda YERLESIK kabul edilen baglamalar.
_BEKLENEN = {
    "Ctrl+N": "New", "Ctrl+O": "Open", "Ctrl+S": "Save",
    "Ctrl+Shift+S": "Save As", "Ctrl+A": "Select All",
    "Ctrl+Z": "Undo", "Ctrl+Y": "Redo", "Del": "Delete",
    "Ctrl+0": "Actual Size", "F1": "Help", "F11": "Full Screen",
}


def t_shortcuts(win):
    bolum("2. Klavye kisayollari")

    kisayol = {}
    for a in win.findChildren(QAction):
        s = a.shortcut().toString()
        if s:
            kisayol.setdefault(s, set()).add(_duz(a.text()))

    for tus, ne in sorted(_BEKLENEN.items()):
        check(tus in kisayol, "yerlesik kisayol %s (%s) tanimli" % (tus, ne),
              sorted(kisayol)[:12])

    cakisan = {t: sorted(v) for t, v in kisayol.items() if len(v) > 1}
    check(not cakisan,
          "ayni kisayola bagli IKI FARKLI komut yok (Qt 'ambiguous' der)",
          "\n".join("%s -> %s" % (t, v) for t, v in cakisan.items()))

    # Ctrl+Q ile cikis -- Qt uygulamalarinin ortak baglamasi.
    check("Ctrl+Q" in kisayol, "Ctrl+Q cikis icin bagli")


# --------------------------------------------------------------------------- #
#  3. Erisilebilirlik (WCAG 2.1 AA)
# --------------------------------------------------------------------------- #

def t_accessibility(app, win):
    bolum("3. Erisilebilirlik")

    # -- Simge-yalniz dugmelerin ipucu OLMAK ZORUNDA ------------------------- #
    #
    # Yazisi olmayan bir dugme, ipucu da yoksa ne oldugunu SOYLEMEZ; ekran
    # okuyucu da okuyacak bir sey bulamaz (WCAG 4.1.2).
    eksik = []
    for tb in win.findChildren(QToolButton):
        if tb.objectName().startswith(("qt_", "Scroll")):
            continue            # Qt'nin kendi tasma / kaydirma dugmeleri
        if isinstance(tb.parentWidget(), QLineEdit):
            continue            # QLineEdit'in yerlesik temizleme dugmesi
        act = tb.defaultAction()
        metin = (act.text() if act else tb.text()).strip()
        ipucu = ((act.toolTip() if act else "") or tb.toolTip()
                 or tb.accessibleName())
        yazili = tb.toolButtonStyle() != Qt.ToolButtonStyle.ToolButtonIconOnly
        if not ipucu and not (yazili and metin):
            eksik.append("%s(objectName=%r, metin=%r)"
                         % (type(tb).__name__, tb.objectName(), metin))
    check(not eksik, "her arac dugmesinin adi ya da ipucu var",
          "\n".join(eksik[:6]))

    # -- Metin alanlarinda ERISILEBILIR AD ----------------------------------- #
    #
    # Yer tutucu metin etiket yerine gecmez (WCAG 3.3.2): odak gelince
    # kaybolur.
    adsiz = []
    for w in win.findChildren(QLineEdit) + win.findChildren(QPlainTextEdit):
        if w.isReadOnly():
            continue
        if w.accessibleName() or w.toolTip() or w.whatsThis():
            continue
        # QFormLayout etiketi buddy olarak baglandiysa ad zaten var.
        ust = w.parentWidget()
        etiketli = False
        if ust is not None:
            for lbl in ust.findChildren(QWidget):
                if lbl.metaObject().className() == "QLabel" \
                        and getattr(lbl, "buddy", None) and lbl.buddy() is w:
                    etiketli = True
                    break
        if not etiketli:
            adsiz.append("%s yer-tutucu=%r"
                         % (type(w).__name__, w.placeholderText()))
    check(not adsiz, "her yazi alaninin erisilebilir adi var",
          "\n".join(adsiz[:6]))

    # -- Menulere KLAVYE ile ulasilabiliyor ---------------------------------- #
    #
    # Gercek menu cubugu gizlenip yerine arac dugmeleri konuyor; `&`
    # silinirse Alt+F gibi kisayollar kaybolur ve menuye fare olmadan
    # ULASILAMAZ (WCAG 2.1.1).
    alt = {}
    for btn in getattr(win, "menu_buttons", []):
        ks = btn.shortcut().toString()
        check(ks.startswith("Alt+"),
              "menu '%s' Alt kisayoluyla acilabiliyor" % _duz(btn.text()), ks)
        alt.setdefault(ks, []).append(_duz(btn.text()))
    ikili = {k: v for k, v in alt.items() if len(v) > 1}
    check(not ikili, "Alt kisayollari tekil", ikili)

    # -- Diyaloglar platform DUGME SIRASINI kullaniyor ----------------------- #
    #
    # QDialogButtonBox, Tamam/Iptal sirasini isletim sistemine gore
    # ayarlar; elle yerlestirilen dugmeler Windows ve Linux'ta ters duser.
    import app.ui.class_dialogs as cd
    import app.ui.dialogs as dlg
    import app.ui.workspace_dialog as wd
    for modul in (dlg, cd, wd):
        kaynak = open(modul.__file__, encoding="utf-8").read()
        check("QDialogButtonBox" in kaynak,
              "%s diyaloglari QDialogButtonBox kullaniyor"
              % os.path.basename(modul.__file__))
        check("layout.addWidget(QPushButton" not in kaynak,
              "%s elle dugme dizmiyor" % os.path.basename(modul.__file__))

    # -- Pencere basligi: belge adi + degisiklik isareti + urun adi ---------- #
    from app.core.model import State, StateKind
    win.doc.mark_clean()
    pump(app)
    temiz = win.windowTitle()
    check(" — " in temiz or " - " in temiz,
          "baslik 'belge — urun' bicimini kullaniyor", temiz)
    check(temiz.endswith("UML Design Studio"), "baslik urun adiyla bitiyor",
          temiz)
    check("*" not in temiz, "temiz belgede yildiz YOK", temiz)
    win.doc.edit("kirlet", lambda m: m.add_state(
        State(name="Tmp", kind=StateKind.SIMPLE, x=900.0, y=900.0)))
    pump(app)
    check("*" in win.windowTitle(),
          "kaydedilmemis belge baslikta * ile isaretli", win.windowTitle())
    win.doc.mark_clean()


# --------------------------------------------------------------------------- #
#  4. UML 2.5.1 gosterimi
# --------------------------------------------------------------------------- #

def t_uml_notation(app, win):
    bolum("4. UML 2.5.1 gosterimi")

    import inspect

    from app.ui import diagram_items as di
    from app.ui import class_items as ci

    # -- Sozde-durum sekilleri ------------------------------------------------ #
    ilk = inspect.getsource(di.StateItem._paint_initial)
    check("drawEllipse" in ilk and "setBrush" in ilk,
          "initial: DOLU daire (14.2.4.6)")

    son = inspect.getsource(di.StateItem._paint_final)
    check(son.count("drawEllipse") == 2,
          "final: halka + ICINDE dolu daire (14.2.4.6)")

    secim = inspect.getsource(di.StateItem._paint_choice)
    check("drawPolygon" in secim, "choice: elmas (14.2.4.6)")
    check('"?"' not in secim,
          "choice elmasinda SPESIFIKASYON DISI isaret yok",
          "UML 2.5.1'de choice duz bir elmastir; icine '?' konmaz")

    kavsak = inspect.getsource(di.StateItem._paint_junction)
    check("drawEllipse" in kavsak, "junction: daire (14.2.4.6)")
    check("INITIAL_FILL" in kavsak,
          "junction DOLU/koyu ciziliyor ('small black circle')",
          "choice ile ayni kehribar dolgu kullanilirsa iki sozde-durum "
          "yalnizca bicimle ayirt edilir")

    tarih = inspect.getsource(di.StateItem._paint_history)
    check('"H*"' in tarih and '"H"' in tarih,
          "history: daire icinde H / H* (14.2.4.6)")

    bitir = inspect.getsource(di.StateItem._paint_terminate)
    check(bitir.count("drawLine") == 2, "terminate: capraz X (14.2.4.6)")

    # -- Gecis etiketi bicimi ------------------------------------------------- #
    from app.core.model import Transition
    t = Transition(event="TICK", guard="x > 0", action="f();")
    check(t.label() == "TICK [x > 0] / f()",
          "gecis etiketi 'trigger [guard] / effect' (14.2.4.9)", t.label())
    check(Transition(action="f();").label() == "/ f()",
          "olaysiz gecis '/ effect' ile yaziliyor")

    # -- Sinif diyagrami ------------------------------------------------------ #
    rel = inspect.getsource(ci.RelationItem)
    check("DIAMOND" in rel, "aggregation/composition: elmas (11.5.4)")
    check("_src_fill = kind is RelationKind.COMPOSITION" in rel,
          "composition DOLU, aggregation ICI BOS elmas")
    check("TRIANGLE" in rel and "_dst_fill = False" in rel,
          "generalization/realization: ICI BOS kapali ucgen")
    check("DashLine" in rel, "realization ve dependency KESIKLI cizgi")

    # DIKKAT: `paint` @guarded_paint ile sarmalanmistir; dogrudan
    # `getsource(ClassItem.paint)` sarmalayiciyi verir, gercek govdeyi
    # degil. Bu yuzden MODULUN tamami okunur.
    sinif = inspect.getsource(ci)
    check("«interface»" in sinif, "arayuz «interface» kalibiyla isaretli")
    check("setItalic" in sinif, "soyut sinif ADI ITALIK (11.4.4)")

    from app.core.class_model import Attribute, Operation, Parameter
    a = Attribute(name="x", type="double", visibility="-")
    check(a.label().startswith("- x : double"),
          "nitelik 'gorunurluk ad : tip' (11.4.4)", a.label())
    o = Operation(name="f", return_type="bool",
                  params=[Parameter(name="p", type="int")])
    check(o.label() == "+ f(p : int) : bool",
          "islem 'gorunurluk ad(par : tip) : donus'", o.label())


# --------------------------------------------------------------------------- #
#  5. Metin ve terminoloji
# --------------------------------------------------------------------------- #

def t_wording(win):
    bolum("5. Metin ve terminoloji")

    bar = win.menuBar()
    metinler = []
    for a in bar.actions():
        if a.menu() is None:
            continue
        for act in a.menu().actions():
            if not act.isSeparator() and act.text():
                metinler.append(_duz(act.text()))

    # Menuler BASLIK BICIMINDE yazilir (Windows kilavuzu).
    kucuk = [m for m in metinler
             if m and m[0].islower() and not m.startswith("«")]
    check(not kucuk, "menu ogeleri buyuk harfle basliyor", kucuk[:6])

    # Turkce sizmasi olmamali -- urun arayuzu tamamen ingilizce.
    tr = [m for m in metinler if re.search(r"[çğıöşüÇĞİÖŞÜ]", m)]
    check(not tr, "menulerde Turkce karakter yok", tr[:6])

    # Arac ipuclari kisayolu da soylemeli: kullanici ogrenebilsin.
    from app.ui.main_window import CLASS_TOOLS, STATE_TOOLS
    for tablo, ad in ((STATE_TOOLS, "durum"), (CLASS_TOOLS, "sinif")):
        for kayit in tablo:
            ipucu, tus = kayit[4], kayit[2]
            check("(%s)" % tus in ipucu,
                  "%s araci '%s' ipucunda kisayolu yaziyor" % (ad, kayit[1]),
                  ipucu)

    # Kisaltma yok.
    kisaltma = [k[1] for tablo in (STATE_TOOLS, CLASS_TOOLS) for k in tablo
                if k[1].endswith(".")]
    check(not kisaltma, "arac adlari kisaltilmamis", kisaltma)


# --------------------------------------------------------------------------- #

def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(ui_font(9))
    app.setStyleSheet(stylesheet())

    win = MainWindow()
    win.resize(1700, 950)
    win.show()
    win.canvas.auto_edit = False
    win.class_canvas.auto_edit = False
    pump(app, 15)

    try:
        t_menu(win)
        t_shortcuts(win)
        t_accessibility(app, win)
        t_uml_notation(app, win)
        t_wording(win)
    finally:
        win.doc.mark_clean()
        win.class_doc.mark_clean()
        try:
            win.git_panel.shutdown()
        except Exception:                          # noqa: BLE001
            pass
        win.settings.clear()
        win.close()

    print("\n== Ozet ==")
    if _failures:
        print("  %d standart kontrolu basarisiz:" % len(_failures))
        for f in _failures:
            print("    - %s" % f)
        return 1
    print("  Tum standart kontrolleri gecti.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
