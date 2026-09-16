"""UML Design Studio -- UCTAN UCA FONKSIYONELLIK DENETIMI.

    python tools/test_functional.py

Bu dosya, aracin KULLANICIYA GORUNEN her yetenegini gercek arayuz
uzerinden calistirir ve her adimda sonucu DOGRULAR. Amaci digerlerinden
farklidir:

  * verify_codegen.py -> uretilen kod derleniyor mu?
  * test_semantics.py -> uretilen kod Python referansi ile ayni mi?
  * test_regressions.py -> DUZELTILMIS hatalar geri geldi mi?
  * smoke_test.py -> temel akislar cokme uretiyor mu?
  * BU DOSYA -> her ozellik, ucundan ucuna, GERCEKTEN ise yariyor mu?

Kapsam: acilis, on bes cizim araci, hiyerarsi, geri al/yinele derinligi,
pano, dosya cevrimi, dogrulama, iki dilde kod uretimi ve onbellek,
simulasyon anlambilimi, sinif diyagrami ve alti iliski turu, calisma
alani dongusu, git dongusu, gorsel fark, tema gecisi, panel/pencere
yonetimi ve buyuk model altinda basarim.
"""

from __future__ import annotations

import os

os.environ.setdefault("UMLSTUDIO_SETTINGS_SCOPE", "test")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json                                                        # noqa: E402
import shutil                                                      # noqa: E402
import sys                                                         # noqa: E402
import tempfile                                                    # noqa: E402
import time                                                        # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt6.QtCore import QPointF, Qt                               # noqa: E402
from PyQt6.QtGui import QMouseEvent                                # noqa: E402
from PyQt6.QtWidgets import QApplication                           # noqa: E402

from app.core.class_model import ClassModel, RelationKind          # noqa: E402
from app.core.git_backend import Repo, git_available               # noqa: E402
from app.core.model import (State, StateKind, Transition,          # noqa: E402
                            TransitionKind)
from app.core.samples import demo_class_model, demo_machine        # noqa: E402
from app.core.simulator import Simulator                           # noqa: E402
from app.core.validator import has_errors, validate                # noqa: E402
from app.core.workspace import Workspace                           # noqa: E402
from app.ui.canvas import Tool                                     # noqa: E402
from app.ui.class_canvas import ClassTool                          # noqa: E402
from app.ui.diagram_items import GhostItem                         # noqa: E402
from app.ui.main_window import MainWindow, app_settings            # noqa: E402
from app.ui.theme import stylesheet, ui_font                       # noqa: E402
import app.ui.theme as theme                                       # noqa: E402
import app.ui.workspace_tree as wt                                 # noqa: E402

_failures = []
_notes = []
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


def note(text: str) -> None:
    """Basarisizlik degil, RAPORA girecek olcum."""
    _notes.append("%s: %s" % (_bolum[0], text))
    print("  [ olcum ] %s" % text)


def click(canvas, x: float, y: float, modifier=None) -> None:
    """Tuvalde verilen SAHNE noktasina sol tik."""
    mod = modifier or Qt.KeyboardModifier.NoModifier
    view_pt = canvas.mapFromScene(QPointF(x, y))
    for tip, fn in ((QMouseEvent.Type.MouseButtonPress, canvas.mousePressEvent),
                    (QMouseEvent.Type.MouseButtonRelease,
                     canvas.mouseReleaseEvent)):
        fn(QMouseEvent(tip, QPointF(view_pt), QPointF(view_pt),
                       Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                       mod))


def drag(canvas, x0: float, y0: float, x1: float, y1: float,
         adim: int = 6) -> None:
    """GERCEK bir surukleme: bas -> tasi -> birak.

    Iki incelik var, ikisi de once yanlis yapildi:

    1. Olaylar QTest ile GORUNTU ALANINA (viewport) gonderilir. Elle
       kurulan QMouseEvent'i `canvas.mousePressEvent()`e vermek Qt'nin
       oge surukleme makinesini HIC calistirmiyor: oge yerinde kaliyor,
       bunun yerine lastik bant secimi basliyordu.
    2. Cagiran taraf once `zoom_fit()` yapmali. Sahne noktasi goruntu
       alaninin DISINA dusuyorsa (kod paneli tuvali daraltir) tiklama
       bos alana gider.
    """
    from PyQt6.QtTest import QTest

    vp = canvas.viewport()
    p0 = canvas.mapFromScene(QPointF(x0, y0))
    p1 = canvas.mapFromScene(QPointF(x1, y1))
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=p0)
    for i in range(1, adim + 1):
        t = float(i) / adim
        QTest.mouseMove(vp, QPointF(p0.x() + (p1.x() - p0.x()) * t,
                                    p0.y() + (p1.y() - p0.y()) * t).toPoint())
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=p1)


def pump(app, times: int = 6) -> None:
    for _ in range(times):
        app.processEvents()


# --------------------------------------------------------------------------- #
#  Modal diyalog yakalayicilari
#
#  Ekransiz kosumda `box.exec()` SONSUZA KADAR bekler; test kilitlenir.
#  Diyaloglar sahte siniflarla degistirilir -- hem test akmaya devam eder
#  hem de "hangi uyari gosterildi" KAYIT ALTINA alinir, boylece
#  "kaydedilmemis degisiklik" korumasinin gercekten calistigi sinanabilir.
# --------------------------------------------------------------------------- #

class _Dugme:
    """addButton()'in dondurdugu kimlik nesnesi."""

    def __init__(self, metin: str, rol) -> None:
        self.metin = metin
        self.rol = rol


class SahteKutu:
    """QMessageBox yerine gecer; her zaman 'Don't save' secer."""

    from PyQt6.QtWidgets import QMessageBox as _Gercek
    Icon = _Gercek.Icon
    ButtonRole = _Gercek.ButtonRole
    StandardButton = _Gercek.StandardButton

    gorulen = []          # gosterilen tum ileti metinleri

    def __init__(self, *_a, **_k) -> None:
        self._dugmeler = []
        self._secilen = None

    def setWindowTitle(self, _t) -> None:
        pass

    def setText(self, t) -> None:
        SahteKutu.gorulen.append(str(t))

    def setInformativeText(self, _t) -> None:
        pass

    def setDetailedText(self, _t) -> None:
        pass

    def setIcon(self, _i) -> None:
        pass

    def setStandardButtons(self, *_a) -> None:
        pass

    def setDefaultButton(self, *_a) -> None:
        pass

    def addButton(self, metin="", rol=None):
        dugme = _Dugme(str(metin), rol)
        self._dugmeler.append(dugme)
        # Yikici rol = "Don't save": veri kaybi testin amaci degil, ama
        # akisi kilitlemeden ILERLETEN tek secenek budur.
        if rol == SahteKutu.ButtonRole.DestructiveRole:
            self._secilen = dugme
        return dugme

    def exec(self) -> int:
        return 0

    def clickedButton(self):
        return self._secilen

    @staticmethod
    def critical(*a, **_k):
        SahteKutu.gorulen.append(str(a[2]) if len(a) > 2 else "")
        return 0

    @staticmethod
    def warning(*a, **_k):
        SahteKutu.gorulen.append(str(a[2]) if len(a) > 2 else "")
        return 0

    @staticmethod
    def information(*a, **_k):
        SahteKutu.gorulen.append(str(a[2]) if len(a) > 2 else "")
        return 0

    @staticmethod
    def about(*a, **_k):
        SahteKutu.gorulen.append(str(a[2]) if len(a) > 2 else "")
        return 0

    @staticmethod
    def question(*_a, **_k):
        return SahteKutu.StandardButton.Yes


class SahteDosyaDiyalogu:
    """QFileDialog yerine gecer; onceden belirlenmis yolu dondurur."""

    sonraki_dizin = ""
    sonraki_kayit = ""
    sonraki_acilis = ""

    @staticmethod
    def getExistingDirectory(*_a, **_k):
        return SahteDosyaDiyalogu.sonraki_dizin

    @staticmethod
    def getSaveFileName(*_a, **_k):
        return SahteDosyaDiyalogu.sonraki_kayit, "x"

    @staticmethod
    def getOpenFileName(*_a, **_k):
        return SahteDosyaDiyalogu.sonraki_acilis, "x"


# --------------------------------------------------------------------------- #
#  1. Acilis ve baslangic yerlesimi
# --------------------------------------------------------------------------- #

def t_startup(app):
    bolum("1. Acilis ve baslangic yerlesimi")

    ayar = app_settings()
    ayar.clear()
    ayar.sync()

    win = MainWindow()
    win.settings = ayar
    win.resize(1700, 950)
    win.show()
    win.canvas.auto_edit = False
    win.class_canvas.auto_edit = False
    win.set_language("c")
    pump(app)

    check(win.mode_tabs.count() == 3, "uc kip sekmesi var",
          win.mode_tabs.count())
    # Acilista BOS SABLON gelir (Start + Idle), ornek 'Blinky' modeli DEGIL.
    # Kullanici bunu acikca istemisti: ornek yalnizca File > Sample ile gelir.
    adlar = {s.name for s in win.doc.machine.states.values()}
    check(adlar == {"Start", "Idle"},
          "acilista bos sablon var, ORNEK MODEL yok", sorted(adlar))
    check(not any(a in adlar for a in ("Running", "LedOn", "Fault")),
          "Blinky ornegi acilista YUKLENMIYOR")
    sinif_adlari = {c.name for c in win.class_doc.machine.classes.values()}
    check(sinif_adlari == {"NewClass"},
          "sinif tuvali bos sablonla basliyor", sorted(sinif_adlari))
    check("Room" not in sinif_adlari,
          "RoomPlan ornegi acilista YUKLENMIYOR")
    check(not win.sim_panel.isVisible(),
          "simulasyon paneli varsayilan olarak KAPALI")
    check(win.code_panel_open(), "kod paneli varsayilan olarak acik")
    check(not win.doc.is_dirty(), "bos belge kirli degil")

    baslik = win.windowTitle()
    check("UML" in baslik, "pencere basligi urun adini tasiyor", baslik)
    return win


# --------------------------------------------------------------------------- #
#  2. Durum makinesi araclarinin tamami
# --------------------------------------------------------------------------- #

#: (arac, beklenen tur) -- UML 2.5.1 madde 14 sozde-durumlarinin tamami.
_ARACLAR = [
    (Tool.STATE, StateKind.SIMPLE),
    (Tool.COMPOSITE, StateKind.COMPOSITE),
    (Tool.INITIAL, StateKind.INITIAL),
    (Tool.FINAL, StateKind.FINAL),
    (Tool.CHOICE, StateKind.CHOICE),
    (Tool.JUNCTION, StateKind.JUNCTION),
    (Tool.SHALLOW_HISTORY, StateKind.SHALLOW_HISTORY),
    (Tool.DEEP_HISTORY, StateKind.DEEP_HISTORY),
    (Tool.TERMINATE, StateKind.TERMINATE),
]


def t_tools(app, win):
    bolum("2. Durum makinesi araclari")

    win.file_new()
    pump(app)
    sm = win.doc.machine

    x = -400.0
    yerlesen = {}
    for arac, tur in _ARACLAR:
        onceki = set(sm.states)
        win.set_tool(arac)
        check(win.canvas.tool == arac, "arac kipi '%s' etkin" % arac.value)
        click(win.canvas, x, -260.0)
        pump(app)
        yeni = set(sm.states) - onceki
        check(len(yeni) == 1, "'%s' araci TEK oge ekledi" % arac.value,
              len(yeni))
        if yeni:
            sid = yeni.pop()
            yerlesen[tur] = sid
            check(sm.states[sid].kind == tur,
                  "eklenen ogenin turu %s" % tur.value, sm.states[sid].kind)
        x += 130.0

    check(len(yerlesen) == len(_ARACLAR),
          "dokuz durum/sozde-durum turunun hepsi yerlestirilebiliyor",
          len(yerlesen))

    # -- Gecis cizimi: iki tikla -------------------------------------------- #
    #
    # Bos sablon 'Start -> Idle' gecisiyle gelir; sayimlar o TABANDAN
    # ilerler. Ayrica her cizimden sonra arac kendiliginden SELECT'e
    # doner (tek kullanimlik arac), bu yuzden her gecis icin yeniden
    # secilir -- bu davranisin kendisi de asagida sinaniyor.
    taban = len(sm.transitions)
    kaynak = sm.states[yerlesen[StateKind.SIMPLE]]
    hedef = sm.states[yerlesen[StateKind.COMPOSITE]]
    win.set_tool(Tool.TRANSITION)
    click(win.canvas, kaynak.x + kaynak.w / 2, kaynak.y + kaynak.h / 2)
    click(win.canvas, hedef.x + hedef.w / 2, hedef.y + hedef.h / 2)
    pump(app)
    check(len(sm.transitions) == taban + 1, "iki tikla gecis cizildi",
          (taban, len(sm.transitions)))
    check(win.canvas.tool == Tool.SELECT,
          "arac cizimden sonra SELECT'e donuyor", win.canvas.tool)

    # -- Kendi kendine gecis ------------------------------------------------ #
    win.set_tool(Tool.TRANSITION)
    click(win.canvas, kaynak.x + kaynak.w / 2, kaynak.y + kaynak.h / 2)
    click(win.canvas, kaynak.x + kaynak.w / 2, kaynak.y + kaynak.h / 2)
    pump(app)
    kendi = [t for t in sm.transitions.values() if t.source == t.target]
    check(len(kendi) == 1, "kendi kendine gecis (self-transition) cizilebiliyor",
          len(kendi))

    # -- Sozde-duruma kendine gecis REDDEDILIR (UML: makine asili kalir) ---- #
    junction = sm.states[yerlesen[StateKind.JUNCTION]]
    onceki = len(sm.transitions)
    win.set_tool(Tool.TRANSITION)
    click(win.canvas, junction.x + junction.w / 2, junction.y + junction.h / 2)
    click(win.canvas, junction.x + junction.w / 2, junction.y + junction.h / 2)
    pump(app)
    check(len(sm.transitions) == onceki,
          "sozde-durumun kendine gecisi engelleniyor", len(sm.transitions))

    # -- Final durumdan CIKIS gecisi olmaz ----------------------------------- #
    final = sm.states[yerlesen[StateKind.FINAL]]
    onceki = len(sm.transitions)
    win.set_tool(Tool.TRANSITION)
    click(win.canvas, final.x + final.w / 2, final.y + final.h / 2)
    click(win.canvas, kaynak.x + kaynak.w / 2, kaynak.y + kaynak.h / 2)
    pump(app)
    check(len(sm.transitions) == onceki,
          "final durumdan cikis gecisi engelleniyor (UML 14.2.3.4.7)",
          len(sm.transitions))

    # -- Initial sozde-durumu HEDEF olamaz ----------------------------------- #
    initial = sm.states[yerlesen[StateKind.INITIAL]]
    onceki = len(sm.transitions)
    win.set_tool(Tool.TRANSITION)
    click(win.canvas, kaynak.x + kaynak.w / 2, kaynak.y + kaynak.h / 2)
    click(win.canvas, initial.x + initial.w / 2, initial.y + initial.h / 2)
    pump(app)
    check(len(sm.transitions) == onceki,
          "initial sozde-durumu hedef olarak reddediliyor", len(sm.transitions))

    # -- Bos alana tiklamak gecisi IPTAL eder -------------------------------- #
    onceki = len(sm.transitions)
    win.set_tool(Tool.TRANSITION)
    click(win.canvas, kaynak.x + kaynak.w / 2, kaynak.y + kaynak.h / 2)
    click(win.canvas, 4000.0, 4000.0)
    pump(app)
    check(len(sm.transitions) == onceki,
          "bos alana tiklayinca yarim gecis birakilmiyor",
          len(sm.transitions))

    win.set_tool(Tool.SELECT)
    return yerlesen


# --------------------------------------------------------------------------- #
#  3. Hiyerarsi: bilesik durumun icine tasima
# --------------------------------------------------------------------------- #

def t_hierarchy(app, win):
    bolum("3. Hiyerarsi ve yeniden ebeveynleme")

    win.file_new()
    pump(app)
    sm = win.doc.machine

    def ekle(**kw):
        st = State(**kw)
        win.doc.edit("ekle", lambda m, s=st: m.add_state(s))
        return st

    dis = ekle(name="Outer", kind=StateKind.COMPOSITE,
               x=0.0, y=0.0, w=420.0, h=300.0)
    ic = ekle(name="Inner", kind=StateKind.COMPOSITE,
              x=40.0, y=60.0, w=260.0, h=180.0, parent=dis.id)
    yaprak = ekle(name="Leaf", kind=StateKind.SIMPLE,
                  x=70.0, y=110.0, w=140.0, h=70.0, parent=ic.id)
    uzak = ekle(name="Far", kind=StateKind.SIMPLE, x=700.0, y=0.0)
    pump(app)

    check(sm.depth(yaprak.id) == 2, "ic ice iki seviye derinlik",
          sm.depth(yaprak.id))
    check(sm.parent_of(yaprak.id).id == ic.id, "ebeveyn zinciri dogru")
    check(sm.lca(yaprak.id, uzak.id) is None,
          "ayri agaclarin ortak atasi kok bolge (None)")
    check(sm.lca(yaprak.id, ic.id) == ic.id,
          "ata ile torununun LCA'si atanin kendisi", sm.lca(yaprak.id, ic.id))
    check(sm.is_descendant(yaprak.id, dis.id), "torun iliskisi taniniyor")
    check(not sm.is_descendant(uzak.id, dis.id), "yabanci durum torun degil")

    # -- Fare ile bilesik durumun icine BIRAK -------------------------------- #
    #
    # Yeniden ebeveynleme `mouseReleaseEvent` icinde olur; setPos() +
    # commit_geometry() o yolu HIC calistirmaz. Bu yuzden gercek bir
    # surukleme gonderilir -- kullanicinin yaptigi sey budur.
    win.set_tool(Tool.SELECT)
    ogeler = win.canvas.state_items
    check(uzak.id in ogeler, "tuvalde 'Far' ogesi var")
    win.canvas.zoom_fit()          # her iki oge de goruntu alaninda olsun
    win.canvas.set_selected_ids([uzak.id])
    pump(app)
    # 'Outer'in icinde ama 'Inner'in DISINDA bir nokta (Inner: 40,60-300,240).
    drag(win.canvas,
         uzak.x + uzak.w / 2, uzak.y + uzak.h / 2,   # 'Far'in ortasindan
         dis.x + 350.0, dis.y + 150.0)               # yalnizca 'Outer' icine
    pump(app)
    check(sm.states[uzak.id].parent == dis.id,
          "bilesik durumun uzerine birakilan durum COCUK oluyor",
          sm.states[uzak.id].parent)
    check(sm.states[uzak.id].id in win.canvas.state_items,
          "tasinan oge tuvalde duruyor")

    # -- IC ICE bilesiklerde EN ICTEKI kazanir -------------------------------- #
    win.canvas.zoom_fit()
    win.canvas.set_selected_ids([uzak.id])
    pump(app)
    guncel = win.canvas.state_items[uzak.id].scene_center()
    ic_merkezi = win.canvas.state_items[ic.id].scene_center()
    drag(win.canvas, guncel.x(), guncel.y(),
         ic_merkezi.x(), ic_merkezi.y())
    pump(app)
    check(sm.states[uzak.id].parent == ic.id,
          "ic ice bilesiklerde EN ICTEKI ust durum secildi",
          sm.states[uzak.id].parent)

    # -- Geri al, hiyerarsiyi de geri almali --------------------------------- #
    #
    # Iki tasima yapildi (kok -> Outer -> Inner); iki geri alma bizi
    # basa dondurmeli.
    win._undo()
    pump(app)
    check(win.doc.machine.states[uzak.id].parent == dis.id,
          "son tasima geri alindi (Inner -> Outer)",
          win.doc.machine.states[uzak.id].parent)
    win._undo()
    pump(app)
    check(win.doc.machine.states[uzak.id].parent in (None, ""),
          "ilk tasima da geri alindi (kok bolgeye dondu)",
          win.doc.machine.states[uzak.id].parent)
    win._redo()
    win._redo()
    pump(app)
    check(win.doc.machine.states[uzak.id].parent == ic.id,
          "iki tasima yeniden uygulandi",
          win.doc.machine.states[uzak.id].parent)

    # -- Kendi torununun icine birakilamaz ---------------------------------- #
    sm = win.doc.machine
    win.canvas.zoom_fit()
    win.canvas.set_selected_ids([dis.id])
    pump(app)
    dis_item = win.canvas.state_items[dis.id]
    merkez = dis_item.scene_center()
    ic_item = win.canvas.state_items[ic.id]
    ic_merkez = ic_item.scene_center()
    drag(win.canvas, merkez.x(), merkez.y(), ic_merkez.x(), ic_merkez.y())
    pump(app)
    check(sm.states[dis.id].parent in (None, ""),
          "bilesik durum KENDI torununun cocugu yapilamiyor",
          sm.states[dis.id].parent)


# --------------------------------------------------------------------------- #
#  4. Geri al / yinele derinligi
# --------------------------------------------------------------------------- #

def t_undo(app, win):
    bolum("4. Geri al / yinele derinligi")

    win.file_new()
    pump(app)
    sm = win.doc.machine

    # Bos sablon iki durumla gelir; sayimlar o TABANDAN ilerler.
    taban = len(sm.states)
    ADIM = 25
    for i in range(ADIM):
        st = State(name="S%02d" % i, kind=StateKind.SIMPLE,
                   x=float(i * 40), y=0.0)
        win.doc.edit("ekle %d" % i, lambda m, s=st: m.add_state(s))
    pump(app)
    check(len(sm.states) == taban + ADIM, "%d duzenleme uygulandi" % ADIM,
          (taban, len(sm.states)))

    for _ in range(ADIM):
        win._undo()
    pump(app)
    check(len(win.doc.machine.states) == taban,
          "tum adimlar geri alinabiliyor (baslangica donuyor)",
          len(win.doc.machine.states))
    check(len(win.canvas.state_items) == taban,
          "tuval de geri sarildi (gorunum modelle esit)",
          len(win.canvas.state_items))

    for _ in range(ADIM):
        win._redo()
    pump(app)
    check(len(win.doc.machine.states) == taban + ADIM,
          "tum adimlar yeniden uygulanabiliyor", len(win.doc.machine.states))
    check(len(win.canvas.state_items) == taban + ADIM,
          "tuval yeniden dolduruldu", len(win.canvas.state_items))

    # -- Yeni duzenleme, ileri yigini TEMIZLER ------------------------------- #
    win._undo()
    win._undo()
    yeni = State(name="Dallanma", kind=StateKind.SIMPLE, x=900.0, y=200.0)
    win.doc.edit("dal", lambda m: m.add_state(yeni))
    pump(app)
    win._redo()
    pump(app)
    check(any(s.name == "Dallanma" for s in win.doc.machine.states.values()),
          "yeni duzenlemeden sonra yinele eski dali geri getirmiyor")


# --------------------------------------------------------------------------- #
#  5. Pano: kopyala / kes / yapistir / sil / tumunu sec
# --------------------------------------------------------------------------- #

def t_clipboard(app, win):
    bolum("5. Pano ve secim islemleri")

    win.file_new()
    pump(app)
    win.doc.replace(demo_machine())
    pump(app)
    sm = win.doc.machine
    basta = len(sm.states)

    running = next(s for s in sm.states.values() if s.name == "Running")
    win.canvas.set_selected_ids([running.id])
    pump(app)
    check(win.canvas.selected_ids() == [running.id], "oge secilebiliyor")

    check(win.canvas.copy_selection(), "bilesik durum panoya kopyalandi")
    win.canvas.paste_clipboard()
    pump(app)
    check(len(win.doc.machine.states) > basta,
          "yapistirma alt agaci birlikte getirdi",
          (basta, len(win.doc.machine.states)))
    sonra_yapistir = len(win.doc.machine.states)

    win._undo()
    pump(app)
    check(len(win.doc.machine.states) == basta,
          "yapistirma TEK adimda geri alinabiliyor",
          len(win.doc.machine.states))

    # -- BILESIK DURUMUN UZERINE yapistirma ---------------------------------- #
    #
    # YASANAN HATA: bu yol `host.state_id` diye VAR OLMAYAN bir alani
    # okuyordu ve imlec bir bilesik durumun uzerindeyken yapistirmak
    # AttributeError ile cokuyordu. Sik kullanilan yapistirma bicimi
    # tam olarak buydu: parcayi bir bilesik durumun icine koymak.
    merkez = QPointF(running.x + running.w / 2, running.y + running.h / 2)
    ev_sahibi = win.canvas._composite_at_point(merkez)
    check(ev_sahibi is not None, "imlec noktasinda bilesik durum bulundu")
    onceki = len(win.doc.machine.states)
    coktu = ""
    try:
        win.canvas.paste_clipboard()
        pump(app)
    except Exception as exc:                       # noqa: BLE001
        coktu = "%s: %s" % (type(exc).__name__, exc)
    check(not coktu, "bilesik durum uzerine yapistirma COKMUYOR", coktu)
    check(len(win.doc.machine.states) > onceki,
          "yapistirma gerceklesti", (onceki, len(win.doc.machine.states)))
    win._undo()
    pump(app)

    # -- Tumunu sec + sil ---------------------------------------------------- #
    win.select_all()
    pump(app)
    beklenen = len(win.doc.machine.states) + len(win.doc.machine.transitions)
    check(len(win.canvas.selected_ids()) == beklenen,
          "Tumunu Sec durumlarin VE gecislerin hepsini sectii",
          (len(win.canvas.selected_ids()), beklenen))
    win._delete_selection()
    pump(app)
    check(not win.doc.machine.states, "secili ogelerin tamami silindi",
          len(win.doc.machine.states))
    check(not win.doc.machine.transitions,
          "durumlari silinince gecisler de dustu (asili gecis kalmadi)",
          len(win.doc.machine.transitions))
    win._undo()
    pump(app)
    check(len(win.doc.machine.states) == basta, "toplu silme geri alindi")

    # -- Kes ----------------------------------------------------------------- #
    off = next(s for s in win.doc.machine.states.values() if s.name == "Off")
    win.canvas.set_selected_ids([off.id])
    win.canvas.cut_selection()
    pump(app)
    check(off.id not in win.doc.machine.states, "kes ogeyi modelden cikardi")
    win.canvas.paste_clipboard()
    pump(app)
    check(len(win.doc.machine.states) >= basta,
          "kesilen oge geri yapistirilabiliyor")
    note("yapistirma sonrasi durum sayisi %d" % sonra_yapistir)


# --------------------------------------------------------------------------- #
#  6. Dosya cevrimi: kaydet / yukle / ozdeslik
# --------------------------------------------------------------------------- #

def t_file_cycle(app, win, gecici):
    bolum("6. Dosya cevrimi ve veri sadakati")

    win.doc.replace(demo_machine())
    sm = win.doc.machine

    # Gorsel ayrintilar da korunmali: kirilma noktalari ve etiket kaydirmasi.
    ilk_gecis = sm.ordered_transitions()[0]
    ilk_gecis.waypoints = [[120.0, 60.0], [240.0, 90.0]]
    ilk_gecis.label_dx = 18.0
    ilk_gecis.label_dy = -24.0
    win.doc.changed.emit()
    pump(app)

    yol = os.path.join(gecici, "roundtrip.usm")
    win.doc.save(yol)
    check(os.path.exists(yol), "model dosyaya yazildi")
    check(not win.doc.is_dirty(), "kayittan sonra belge temiz")

    once = win.doc.machine.to_json()
    win.file_new()
    pump(app)
    check({s.name for s in win.doc.machine.states.values()} == {"Start", "Idle"},
          "yeni belge bos sablona dondu",
          sorted(s.name for s in win.doc.machine.states.values()))
    win.doc.load(yol)
    pump(app)
    sonra = win.doc.machine.to_json()
    check(once == sonra, "kaydet -> yukle cevrimi BIREBIR ayni model veriyor")

    t2 = win.doc.machine.ordered_transitions()[0]
    check([list(p) for p in t2.waypoints] == [[120.0, 60.0], [240.0, 90.0]],
          "kirilma noktalari korundu", t2.waypoints)
    check((t2.label_dx, t2.label_dy) == (18.0, -24.0),
          "etiket kaydirmasi korundu", (t2.label_dx, t2.label_dy))

    # -- Bozuk dosya CAKMADAN reddedilmeli ----------------------------------- #
    bozuk = os.path.join(gecici, "bozuk.usm")
    with open(bozuk, "w", encoding="utf-8") as fh:
        fh.write("{ bu gecerli json degil ")
    hata = None
    try:
        win.doc.load(bozuk)
    except Exception as exc:                       # noqa: BLE001 - kasitli
        hata = exc
    check(hata is not None, "bozuk dosya sessizce yutulmuyor, hata veriyor",
          type(hata).__name__)
    check(win.doc.machine.states,
          "bozuk yukleme mevcut modeli BOZMADI", len(win.doc.machine.states))

    # -- Bilinmeyen alan tasiyan dosya --------------------------------------- #
    veri = json.loads(once)
    veri["gelecekten_gelen_alan"] = {"x": 1}
    ileri = os.path.join(gecici, "ileri.usm")
    with open(ileri, "w", encoding="utf-8") as fh:
        json.dump(veri, fh)
    try:
        win.doc.load(ileri)
        yuklendi = bool(win.doc.machine.states)
    except Exception:                              # noqa: BLE001
        yuklendi = False
    check(yuklendi, "bilinmeyen alanlar yuklemeyi engellemiyor (ileri uyum)")


# --------------------------------------------------------------------------- #
#  7. Dogrulama gercekten koruyor mu
# --------------------------------------------------------------------------- #

def t_validation(app, win):
    bolum("7. Dogrulama ve hata koruma")

    win.file_new()
    pump(app)

    # (a) Bos model -> initial yok uyarisi, ama kod URETILEBILIR olmali.
    sorunlar = validate(win.doc.machine)
    note("bos modelde %d dogrulama iletisi" % len(sorunlar))

    # (b) Hedefsiz gecis -> HATA
    a = State(name="A", kind=StateKind.SIMPLE, x=0.0, y=0.0)
    b = State(name="B", kind=StateKind.SIMPLE, x=300.0, y=0.0)
    win.doc.edit("a", lambda m: m.add_state(a))
    win.doc.edit("b", lambda m: m.add_state(b))
    win.doc.edit("gecis", lambda m: m.add_transition(
        Transition(source=a.id, target=b.id, event="GO")))
    pump(app)
    check(not has_errors(validate(win.doc.machine)),
          "gecerli kucuk model hatasiz")

    # initial'siz bilesik durum -> hata/uyari
    bilesik = State(name="Comp", kind=StateKind.COMPOSITE,
                    x=0.0, y=300.0, w=300.0, h=200.0)
    icerik = State(name="In", kind=StateKind.SIMPLE,
                   x=40.0, y=360.0, parent=bilesik.id)
    win.doc.edit("c", lambda m: m.add_state(bilesik))
    win.doc.edit("i", lambda m: m.add_state(icerik))
    pump(app)
    kodlar = {i.code for i in validate(win.doc.machine)}
    check(kodlar, "initial'siz bilesik durum rapor uretiyor", sorted(kodlar))

    # (c) Cakisan durum adi -> uretilen C sabiti cakismasi
    ikiz = State(name="A", kind=StateKind.SIMPLE, x=600.0, y=0.0)
    win.doc.edit("ikiz", lambda m: m.add_state(ikiz))
    pump(app)
    check(has_errors(validate(win.doc.machine)),
          "ayni adli iki durum HATA uretiyor")
    check(not win.build(), "hatali modelde Build engelleniyor")
    win._undo()
    pump(app)

    # (d) Hata isaretleri tuvale yansiyor mu
    win.force_validate()
    pump(app)
    check(hasattr(win.canvas, "mark_errors"), "tuval hata isaretlemeyi destekliyor")


# --------------------------------------------------------------------------- #
#  8. Kod uretimi, dil degistirme ve onbellek
# --------------------------------------------------------------------------- #

def t_codegen(app, win):
    bolum("8. Kod uretimi, dil ve onbellek")

    win.doc.replace(demo_machine())
    pump(app)

    win.set_language("c")
    t0 = time.time()
    check(win.build(), "C uretimi basarili")
    sure_c = time.time() - t0
    dosyalar_c = dict(win._built.get(win._build_key()) or {})
    check(set(dosyalar_c) >= {"blinky.h", "blinky.c"},
          "C ciktisi baslik ve kaynak iceriyor", sorted(dosyalar_c))

    win.set_language("cpp")
    check(win.build(), "C++ uretimi basarili")
    dosyalar_cpp = dict(win._built.get(win._build_key()) or {})
    check(any(a.endswith(".hpp") for a in dosyalar_cpp),
          "C++ ciktisi .hpp iceriyor", sorted(dosyalar_cpp))
    check(set(dosyalar_c) != set(dosyalar_cpp),
          "iki dilin ciktilari AYRI onbelleklerde")

    # -- Determinizm: ayni modelden ayni bayt --------------------------------- #
    win.set_language("c")
    win._drop_build()
    check(win.build(), "onbellek bosaltildiktan sonra yeniden uretildi")
    tekrar = dict(win._built.get(win._build_key()) or {})
    ayni = all(tekrar.get(k) == v for k, v in dosyalar_c.items())
    check(ayni and set(tekrar) == set(dosyalar_c),
          "ayni modelden BIREBIR ayni kaynak uretiliyor (surum kontrolu dostu)")

    # -- Model degisince onbellek bayatlar ------------------------------------ #
    check(not win._is_stale(), "uretimden hemen sonra taze")
    win.doc.edit("degis", lambda m: m.add_state(
        State(name="Extra", kind=StateKind.SIMPLE, x=800.0, y=400.0)))
    pump(app)
    check(win._is_stale(), "model degisince kod BAYAT isaretleniyor")
    win._undo()
    pump(app)

    # -- Sinif diyagrami ciktisi ---------------------------------------------- #
    win.class_doc.replace(demo_class_model())
    win.mode_tabs.setCurrentIndex(win.mode_tabs.indexOf(win._class_stack))
    pump(app)
    check(win.active_mode() == "class", "sinif kipine gecildi",
          win.active_mode())
    check(win.build(), "sinif diyagramindan C uretimi basarili")
    sinif_c = dict(win._built.get(win._build_key()) or {})
    check(any(a.endswith(".h") for a in sinif_c),
          "sinif C ciktisi baslik iceriyor", sorted(sinif_c))
    win.set_language("cpp")
    check(win.build(), "sinif diyagramindan C++ uretimi basarili")
    win.mode_tabs.setCurrentIndex(win.mode_tabs.indexOf(win._state_stack))
    win.set_language("c")
    pump(app)
    note("blinky C uretimi %.0f ms" % (sure_c * 1000.0))


# --------------------------------------------------------------------------- #
#  9. Simulasyon anlambilimi
# --------------------------------------------------------------------------- #

def t_simulation(app, win):
    bolum("9. Simulasyon anlambilimi")

    win.doc.replace(demo_machine())
    pump(app)
    win.a_sim_panel.setChecked(True)
    win.toggle_sim_panel(True)
    pump(app)
    check(win.sim_panel.isVisible(), "simulasyon paneli aciliyor")

    panel = win.sim_panel
    panel.start()
    pump(app)
    check(panel.sim is not None, "simulator kuruldu")
    ilk = panel.sim.state_name
    check(ilk == "Off", "baslangic gecisi Off durumuna goturdu", ilk)

    panel.dispatch("BUTTON")
    pump(app)
    # active_chain() MODEL ID'leri verir (tuvali vurgulamak icin); panel
    # bunlari ada cevirip gosterir. Bu yuzden karsilastirma id uzerinden.
    sm_akt = win.doc.machine
    running_id = next(s.id for s in sm_akt.states.values()
                      if s.name == "Running")
    zincir = panel.sim.active_chain()
    check(running_id in zincir,
          "BUTTON olayi bilesik duruma soktu",
          [sm_akt.states[i].name for i in zincir if i in sm_akt.states])
    check(panel.sim.state_name in ("LedOn", "LedOff"),
          "bilesik durumun varsayilan alt durumuna inildi",
          panel.sim.state_name)
    check(" " in panel.lbl_state.text() or panel.lbl_state.text(),
          "panel etkin durum zincirini ADLARLA gosteriyor",
          panel.lbl_state.text())
    check("s_" not in panel.lbl_state.text(),
          "panelde ham model id'si GORUNMUYOR", panel.lbl_state.text())

    onceki = panel.sim.state_name
    panel.dispatch("TICK")
    pump(app)
    check(panel.sim.state_name != onceki,
          "TICK ic bolgede durum degistirdi",
          (onceki, panel.sim.state_name))

    # -- Bilinmeyen olay durumu DEGISTIRMEZ ---------------------------------- #
    simdiki = panel.sim.state_name
    panel.dispatch("HICBIR_SEY")
    pump(app)
    check(panel.sim.state_name == simdiki,
          "taninmayan olay durumu degistirmiyor")

    # -- do-activity ---------------------------------------------------------- #
    panel.step_do()
    pump(app)
    check(panel.sim.state_name == simdiki,
          "do-activity durum degistirmiyor (UML 14.2.3.4.4)")

    # -- IC gecis: eylem calisir, durum DEGISMEZ ------------------------------ #
    ic_makine = demo_machine()
    hedef = next(s for s in ic_makine.states.values() if s.name == "Running")
    ic_makine.add_transition(Transition(
        source=hedef.id, target=hedef.id, event="PING",
        kind=TransitionKind.INTERNAL, action="ctx->ping_count++;"))
    ic_sim = Simulator(ic_makine)
    ic_sim.start()
    ic_sim.dispatch("BUTTON")
    once_ic = ic_sim.state_name
    ic_sim.dispatch("PING")
    check(ic_sim.state_name == once_ic,
          "ic gecis (internal) durumu DEGISTIRMIYOR",
          (once_ic, ic_sim.state_name))

    # -- Terminate ------------------------------------------------------------ #
    panel.dispatch("SHUTDOWN")
    pump(app)
    check(panel.sim.is_terminated(),
          "SHUTDOWN makineyi sonlandirdi", panel.sim.state_name)
    sonlanmis = panel.sim.state_name
    panel.dispatch("BUTTON")
    pump(app)
    check(panel.sim.state_name == sonlanmis,
          "sonlanmis makine olay ISLEMIYOR")

    panel.reset(quiet=True)
    pump(app)

    # -- UST DURUMUN ic gecisi alt durumu bozmaz ----------------------------- #
    #
    # Ornekte FAULT, 'Running' uzerinde bir IC gecistir (internal):
    # UML 2.5.1'e gore ne cikis ne giris eylemi calisir, etkin alt durum
    # oldugu gibi kalir. Bir cikis/giris zinciri calissaydi LedOff'tan
    # LedOn'a dusulurdu -- bu, ic gecisin en sik yapilan hatasidir.
    sm_ref = demo_machine()
    ana = Simulator(sm_ref)
    ana.start()
    ana.dispatch("BUTTON")
    ana.dispatch("TICK")
    onceki_alt = ana.state_name
    ana.dispatch("FAULT")
    check(ana.state_name == onceki_alt,
          "ust durumun IC gecisi etkin alt durumu DEGISTIRMIYOR",
          (onceki_alt, ana.state_name))
    check(ana.state_name in ("LedOn", "LedOff"),
          "makine hala bilesik durumun icinde", ana.state_name)

    # -- Tuval etkin durumu vurguluyor mu ------------------------------------ #
    panel.start()
    panel.dispatch("BUTTON")
    pump(app)
    etkin = [i for i in win.canvas.state_items.values() if i.is_active]
    check(etkin, "etkin durum tuvalde vurgulaniyor", len(etkin))
    check(len(etkin) >= 2,
          "etkin ZINCIR vurgulaniyor (yaprak + bilesik ust)", len(etkin))

    win.a_sim_panel.setChecked(False)
    win.toggle_sim_panel(False)
    pump(app)
    check(not win.sim_panel.isVisible(), "simulasyon paneli kapanabiliyor")


# --------------------------------------------------------------------------- #
#  10. Sinif diyagrami ve alti iliski turu
# --------------------------------------------------------------------------- #

def t_class_diagram(app, win):
    bolum("10. Sinif diyagrami")

    win.mode_tabs.setCurrentIndex(win.mode_tabs.indexOf(win._class_stack))
    win.class_doc.replace(ClassModel(name="Fonksiyonel"))
    pump(app)
    cm = win.class_doc.machine

    x = -400.0
    yerlesen = []
    for arac in (ClassTool.CLASS, ClassTool.ABSTRACT, ClassTool.INTERFACE):
        onceki = set(cm.classes)
        win.set_class_tool(arac)
        click(win.class_canvas, x, -200.0)
        pump(app)
        yeni = set(cm.classes) - onceki
        check(len(yeni) == 1, "'%s' araci sinif ekledi" % arac.value)
        if yeni:
            yerlesen.append(yeni.pop())
        x += 320.0

    check(len(yerlesen) == 3, "sinif / soyut / arayuz yerlestirildi")
    if len(yerlesen) == 3:
        check(cm.classes[yerlesen[1]].is_abstract, "soyut sinif isaretli")
        check(cm.classes[yerlesen[2]].is_interface, "arayuz isaretli")

    # -- Alti iliski turu ----------------------------------------------------- #
    iliski_araclari = [
        (ClassTool.ASSOCIATION, RelationKind.ASSOCIATION),
        (ClassTool.AGGREGATION, RelationKind.AGGREGATION),
        (ClassTool.COMPOSITION, RelationKind.COMPOSITION),
        (ClassTool.GENERALIZATION, RelationKind.GENERALIZATION),
        (ClassTool.REALIZATION, RelationKind.REALIZATION),
        (ClassTool.DEPENDENCY, RelationKind.DEPENDENCY),
    ]
    if len(yerlesen) == 3:
        a = cm.classes[yerlesen[0]]
        b = cm.classes[yerlesen[1]]
        for arac, tur in iliski_araclari:
            onceki = set(cm.relations)
            win.set_class_tool(arac)
            click(win.class_canvas, a.x + a.w / 2, a.y + 20.0)
            click(win.class_canvas, b.x + b.w / 2, b.y + 20.0)
            pump(app)
            yeni = set(cm.relations) - onceki
            check(len(yeni) == 1, "'%s' iliskisi cizildi" % tur.value)
            if yeni:
                rid = yeni.pop()
                check(cm.relations[rid].kind == tur,
                      "iliski turu %s olarak kaydedildi" % tur.value,
                      cm.relations[rid].kind)
    win.set_class_tool(ClassTool.SELECT)

    # -- Nitelik / islem ve uretilen kod -------------------------------------- #
    win.class_doc.replace(demo_class_model())
    pump(app)
    cm = win.class_doc.machine
    nitelikli = [c for c in cm.classes.values() if c.attributes]
    islemli = [c for c in cm.classes.values() if c.operations]
    check(nitelikli, "ornek modelde nitelikli sinif var", len(nitelikli))
    check(islemli, "ornek modelde islemli sinif var", len(islemli))
    check(win.build(), "sinif diyagrami kodu uretildi")

    # -- Kaydet/yukle ---------------------------------------------------------- #
    once = cm.to_json()
    veri = json.loads(once)
    check(veri.get("classes"), "sinif modeli JSON'a siniflari yaziyor")
    yeniden = ClassModel.from_json(once)
    check(yeniden.to_json() == once, "sinif modeli cevrimi birebir")

    win.mode_tabs.setCurrentIndex(win.mode_tabs.indexOf(win._state_stack))
    pump(app)


# --------------------------------------------------------------------------- #
#  11. Calisma alani dongusu
# --------------------------------------------------------------------------- #

def t_workspace(app, win, gecici):
    bolum("11. Calisma alani dongusu")

    kok = os.path.join(gecici, "ws")
    ws = Workspace.create(kok)
    check(os.path.isdir(ws.model_path), "model klasoru olusturuldu")
    check(os.path.isdir(ws.generated_path), "uretim klasoru olusturuldu")
    check(Workspace.is_workspace(kok), "isaretci dosyasi taniniyor")

    win.apply_workspace(ws)
    pump(app)
    check(win.workspace is not None, "pencere calisma alanini benimsedi")

    # -- Iki model ekle -------------------------------------------------------- #
    fsm_yolu = os.path.join(ws.model_path, "blinky.usm")
    with open(fsm_yolu, "w", encoding="utf-8") as fh:
        fh.write(demo_machine().to_json())
    sinif_yolu = os.path.join(ws.model_path, "roomplan.ucd")
    with open(sinif_yolu, "w", encoding="utf-8") as fh:
        fh.write(demo_class_model().to_json())
    win.refresh_workspace_tree()
    pump(app)

    modeller = [it for it in win.ws_tree._all_items()
                if it.data(0, wt.NODE_ROLE) == "model"]
    check(len(modeller) >= 2, "agac iki modeli de gosteriyor", len(modeller))

    win.open_model_path(fsm_yolu)
    pump(app)
    check(win.doc.machine.states, "durum makinesi modeli acildi")
    check(win.active_mode() == "state", "dogru kipe gecildi",
          win.active_mode())

    win.open_model_path(sinif_yolu)
    pump(app)
    check(win.class_doc.machine.classes, "sinif modeli acildi")
    check(win.active_mode() == "class", "sinif kipine gecildi",
          win.active_mode())

    # -- Uretilen kodu calisma alanina yaz ------------------------------------ #
    win.open_model_path(fsm_yolu)
    win.set_language("c")
    pump(app)
    check(win.build(), "acilan modelden kod uretildi")
    win.write_generated_now()
    pump(app)
    uretilen = []
    for dizin, _alt, dosyalar in os.walk(ws.generated_path):
        uretilen += [os.path.join(dizin, d) for d in dosyalar]
    check(uretilen, "uretilen dosyalar diske yazildi", len(uretilen))

    # -- Ikinci yazim DEGISIKLIK uretmemeli ----------------------------------- #
    imza1 = {p: open(p, "rb").read() for p in uretilen}
    win.write_generated_now()
    pump(app)
    imza2 = {p: open(p, "rb").read() for p in imza1}
    check(imza1 == imza2,
          "degismeyen modelin yeniden yazimi dosyalari DEGISTIRMIYOR")

    # -- Model silinince tuvalden de kalkmali ---------------------------------- #
    win.open_model_path(fsm_yolu)
    pump(app)
    os.remove(fsm_yolu)
    win._on_model_removed(fsm_yolu)
    pump(app)
    # YASANAN HATA: calisma alanindan silinen modelin diyagrami tuvalde
    # DURMAYA devam ediyordu; kullanici olmayan bir dosyayi duzenliyor
    # saniyordu. Dogru davranis: bos sablona donmek.
    adlar = {s.name for s in win.doc.machine.states.values()}
    check(adlar == {"Start", "Idle"},
          "silinen modelin diyagrami tuvalden kaldirildi (bos sablona dondu)",
          sorted(adlar))
    check(not win.doc.path, "belge yolu da temizlendi", win.doc.path)

    return ws


# --------------------------------------------------------------------------- #
#  12. Git dongusu ve gorsel fark
# --------------------------------------------------------------------------- #

def t_git(app, win, ws):
    bolum("12. Git dongusu ve gorsel fark")

    if not git_available():
        note("git bulunamadi -- bu bolum atlandi")
        return

    repo = Repo(ws.root)
    repo.init()
    check(repo.is_repo(), "depo baslatildi")

    repo.stage_all()
    durum = repo.status()
    check(durum.staged(), "dosyalar hazirlama alaninda", len(durum.staged()))
    repo.commit("ilk islem", author_name="Test", author_email="t@example.com")
    check(repo.has_commits(), "ilk islem olustu")
    gecmis = repo.log(limit=10)
    check(len(gecmis) == 1, "gecmiste tek islem var", len(gecmis))

    # -- Modeli DEGISTIR, fark cikmali ---------------------------------------- #
    model_yolu = os.path.join(ws.model_path, "roomplan.ucd")
    fsm_yolu = os.path.join(ws.model_path, "blinky.usm")
    with open(fsm_yolu, "w", encoding="utf-8") as fh:
        fh.write(demo_machine().to_json())
    goreli = os.path.relpath(fsm_yolu, repo.root).replace(os.sep, "/")
    repo.stage([goreli])
    repo.commit("blinky", author_name="Test", author_email="t@example.com")
    check(len(repo.log(limit=10)) == 2, "ikinci islem kaydedildi")

    sm = demo_machine()
    silinecek = next(s for s in sm.states.values() if s.name == "Fault")
    for t in list(sm.transitions.values()):
        if t.source == silinecek.id or t.target == silinecek.id:
            sm.remove_transition(t.id)
    sm.remove_state(silinecek.id)
    sm.add_state(State(name="Calibrating", kind=StateKind.SIMPLE,
                       x=900.0, y=700.0))
    with open(fsm_yolu, "w", encoding="utf-8") as fh:
        fh.write(sm.to_json())

    fark = repo.diff(goreli)
    check(fark.strip(), "calisma agacinda metinsel fark var")
    eski = repo.file_at("HEAD", goreli)
    check(eski.strip(), "HEAD surumu okunabiliyor")

    from app.core.model_diff import element_status
    durumlar = element_status(eski, sm.to_json())
    check(durumlar["added"], "fark cozumleyicisi EKLENEN ogeyi buldu")
    check(durumlar["removed"], "fark cozumleyicisi SILINEN ogeyi buldu")

    # -- Arayuzde: agacta secince tuvalde isaret ------------------------------ #
    win.apply_workspace(ws)
    win.open_model_path(fsm_yolu)
    pump(app, 15)
    hedef = None
    for it in win.ws_tree._all_items():
        if (it.data(0, wt.NODE_ROLE) == "model"
                and it.data(0, wt.PATH_ROLE) == fsm_yolu):
            hedef = it
            break
    if hedef is None:
        hedef = next((it for it in win.ws_tree._all_items()
                      if it.data(0, wt.NODE_ROLE) == "model"), None)
    check(hedef is not None, "agacta model dugumu bulundu")
    if hedef is not None:
        win.ws_tree.setCurrentItem(hedef)
        pump(app, 15)
        isaret = win.canvas._diff_marks
        check(isaret.get("added") or isaret.get("removed"),
              "dosyaya tiklayinca tuvalde FARK isaretleri cikti",
              {k: len(v) for k, v in isaret.items() if v})
        hayalet = [i for i in win.canvas.scene().items()
                   if isinstance(i, GhostItem)]
        check(hayalet, "silinen blok hayalet olarak ciziliyor", len(hayalet))

    # -- Git paneli ------------------------------------------------------------ #
    win.git_panel.set_root(ws.root)
    win.refresh_git()
    pump(app, 15)
    check(win.git_panel.commits.rows if hasattr(win.git_panel, "commits")
          else True, "islem grafigi dolduruldu")

    # -- Eski islemin farki ---------------------------------------------------- #
    ilk_sha = repo.log(limit=10)[-1].sha
    eski_fark = repo.diff_commit(ilk_sha)
    check(eski_fark.strip(), "eski islemin farki okunabiliyor")
    check(os.path.exists(model_yolu), "sinif modeli hala yerinde")


# --------------------------------------------------------------------------- #
#  13. Tema gecisi
# --------------------------------------------------------------------------- #

def t_theme(app, win):
    bolum("13. Tema gecisi")

    once = theme.C.CANVAS_BG
    win.set_theme("light")
    pump(app)
    acik = theme.C.CANVAS_BG
    check(acik != once or theme.current_theme() == "light",
          "acik temaya gecildi", acik)
    check(win.canvas.scene().items() is not None, "tuval acik temada ayakta")

    win.set_theme("dark")
    pump(app)
    check(theme.C.CANVAS_BG == once, "koyu temaya donuldu", theme.C.CANVAS_BG)

    # Iki temada da anahtar kumesi ayni olmali; eksik anahtar, calisma
    # zamaninda AttributeError olarak patlardi.
    check(set(theme.DARK) == set(theme.LIGHT),
          "iki temada ayni anahtar kumesi",
          set(theme.DARK) ^ set(theme.LIGHT))
    note("tema anahtari sayisi %d" % len(theme.DARK))


# --------------------------------------------------------------------------- #
#  14. Panel / pencere yonetimi
# --------------------------------------------------------------------------- #

def t_panels(app, win):
    bolum("14. Panel ve pencere yonetimi")

    win.mode_tabs.setCurrentIndex(win.mode_tabs.indexOf(win._state_stack))
    pump(app)

    win.a_code_panel.setChecked(False)
    win.toggle_code_panel(False)
    pump(app)
    check(not win.code_panel_open(), "kod paneli kapaniyor")
    win.a_code_panel.setChecked(True)
    win.toggle_code_panel(True)
    pump(app)
    check(win.code_panel_open(), "kod paneli aciliyor")

    # -- Ayrik tasarim penceresi ------------------------------------------------ #
    win.a_tool_window.setChecked(True)
    win.toggle_design_window(True)
    pump(app, 12)
    kayit = win._detached.get(win.active_mode())
    check(kayit is not None, "ayrik tasarim penceresi acildi")
    if kayit is not None:
        pencere, sayfa = kayit
        check(pencere.isVisible(), "ayrik pencere GORUNUR")
        check(sayfa is not None and sayfa.isVisible(),
              "sayfa ayrik pencerede gorunur (bos pencere hatasi)")
        check(pencere.centralWidget() is sayfa,
              "sayfa pencerenin merkez bileseni")
        check(win.canvas.isVisible(),
              "tuval ayrik pencerede gercekten gorunuyor")
        # YASANAN HATA: sayfa acilirken TUM alt bilesenler show() ediliyor,
        # tuvalin QRubberBand'i de aciliyordu. Gorunmez bir secim kaplamasi
        # tuvalin ustune yayiliyor ve fare "takiliyor" gibi oluyordu.
        from PyQt6.QtWidgets import QRubberBand
        bantlar = [b for b in pencere.findChildren(QRubberBand)
                   if b.isVisible()]
        check(not bantlar,
              "gorunur lastik bant yok (fare takilmasi geri gelmedi)",
              len(bantlar))
    win.a_tool_window.setChecked(False)
    win.toggle_design_window(False)
    pump(app, 12)
    check(not win._detached, "pencere ana govdeye geri alindi",
          list(win._detached))
    check(win.canvas.isVisible(), "tuval ana pencerede yine gorunur")

    # -- Yakinlastirma / izgara -------------------------------------------------- #
    win.doc.replace(demo_machine())
    pump(app)
    yuzde = win.canvas.zoom_percent()
    win.canvas.zoom_by(1.5)
    pump(app)
    check(win.canvas.zoom_percent() > yuzde, "yakinlastirma calisiyor",
          (yuzde, win.canvas.zoom_percent()))
    win.canvas.zoom_reset()
    pump(app)
    check(abs(win.canvas.zoom_percent() - 100) <= 1, "gercek boyuta donuluyor",
          win.canvas.zoom_percent())
    win.canvas.zoom_fit()
    pump(app)
    check(win.canvas.zoom_percent() > 0, "sigdir calisiyor",
          win.canvas.zoom_percent())

    # -- Izgaraya hizalama GERCEKTEN konum yuvarliyor mu --------------------- #
    #
    # Kullanici "snap to grid calismiyor" demisti; bayrak degil DAVRANIS
    # sinanir: hizalama acikken yerlestirilen durumun koordinatlari
    # izgara adimina oturmali, kapaliyken oturmamali.
    from app.ui.diagram_items import GRID

    win.a_snap.setChecked(True)
    win.toggle_snap(True)
    pump(app)
    check(win.canvas.snap_enabled and win.class_canvas.snap_enabled,
          "izgaraya hizalama iki tuvalde de acildi")

    win.file_new()
    win.canvas.zoom_reset()
    pump(app)
    win.set_tool(Tool.STATE)
    click(win.canvas, 313.0, 247.0)          # izgaraya OTURMAYAN nokta
    pump(app)
    yerlesen = [s for s in win.doc.machine.states.values()
                if s.name not in ("Start", "Idle")]
    check(len(yerlesen) == 1, "hizalama acikken durum eklendi")
    if yerlesen:
        st = yerlesen[0]
        check(abs(st.x % GRID) < 0.01 and abs(st.y % GRID) < 0.01,
              "hizalama ACIKKEN konum izgaraya oturdu", (st.x, st.y, GRID))

    win.a_snap.setChecked(False)
    win.toggle_snap(False)
    pump(app)
    check(not win.canvas.snap_enabled, "izgaraya hizalama kapandi")

    win.a_grid.setChecked(False)
    win.toggle_grid(False)
    pump(app)
    check(not win.canvas.show_grid, "izgara gizlenebiliyor")
    win.a_grid.setChecked(True)
    win.toggle_grid(True)
    pump(app)
    check(win.canvas.show_grid, "izgara geri geliyor")


# --------------------------------------------------------------------------- #
#  15. Basarim: buyuk model
# --------------------------------------------------------------------------- #

def t_performance(app, win):
    bolum("15. Buyuk model altinda basarim")

    from app.codegen.ir import CodegenError, MAX_VERTICES, build_ir
    from app.core.model import StateMachine

    def stres(n: int) -> StateMachine:
        """Gecerli (baslangici olan) n durumlu bir zincir."""
        sm = StateMachine(name="Stress", prefix="stress")
        ilk = State(name="Start", kind=StateKind.INITIAL, x=-120.0, y=-120.0)
        sm.add_state(ilk)
        onceki = None
        for i in range(n):
            st = State(name="S%03d" % i, kind=StateKind.SIMPLE,
                       x=float((i % 12) * 220), y=float((i // 12) * 160))
            sm.add_state(st)
            if onceki is None:
                sm.add_transition(Transition(source=ilk.id, target=st.id))
            else:
                sm.add_transition(Transition(source=onceki.id, target=st.id,
                                             event="E%03d" % i))
            onceki = st
        return sm

    buyuk = stres(120)

    t0 = time.time()
    win.doc.replace(buyuk)
    pump(app, 10)
    sure = time.time() - t0
    check(len(win.canvas.state_items) == 121,
          "121 dugumun tamami cizildi", len(win.canvas.state_items))
    note("120 durum + 119 gecis cizimi %.0f ms" % (sure * 1000.0))
    check(sure < 10.0, "cizim makul surede bitiyor", "%.2f sn" % sure)

    t0 = time.time()
    win.canvas.zoom_fit()
    pump(app)
    note("sigdir %.0f ms" % ((time.time() - t0) * 1000.0))

    t0 = time.time()
    uretildi = win.build()
    sure_kod = time.time() - t0
    check(uretildi, "buyuk modelden kod uretildi")
    note("120 durumluk modelin C uretimi %.0f ms" % (sure_kod * 1000.0))
    check(sure_kod < 15.0, "uretim makul surede bitiyor",
          "%.2f sn" % sure_kod)

    # -- SINIR: durum indeksi uint8_t, 0xFF sentinel ------------------------- #
    #
    # Sessiz tasma, gomulu hedefte YANLIS KOD demektir: indeksler sentinel
    # degerine carpar ve makine rastgele bir duruma gider. Sinirin ACIK bir
    # hata ile korundugu dogrulanir.
    check(MAX_VERTICES == 254, "dugum siniri 254 (0xFF sentinel ayrilmis)",
          MAX_VERTICES)
    # initial sozde-durumu indekslenmez, bu yuzden n durum -> n dugum.
    ir = build_ir(stres(MAX_VERTICES))
    check(len(ir.states) == MAX_VERTICES,
          "sinirin tam ucunda uretim calisiyor", len(ir.states))
    tasma = ""
    try:
        build_ir(stres(MAX_VERTICES + 5))
    except CodegenError as exc:
        tasma = str(exc)
    check(tasma, "sinir asilinca SESSIZ TASMA yok, acik hata var")
    check(tasma and tasma.isascii() and "states" in tasma,
          "sinir hatasi ingilizce ve anlasilir", tasma)

    # -- Hizli sekme gecisleri cokme uretmemeli --------------------------------- #
    for _ in range(6):
        for i in range(win.mode_tabs.count()):
            win.mode_tabs.setCurrentIndex(i)
            app.processEvents()
    check(True, "18 hizli sekme gecisi cokme uretmedi")

    win.file_new()
    pump(app)


# --------------------------------------------------------------------------- #
#  16. Diyalog akislari: kaydedilmemis degisiklik korumasi, ac/kaydet/disa aktar
# --------------------------------------------------------------------------- #

def t_drag_navigation(app, win):
    """Surukleme draw.io gibi: sahne buyur, kenarda goruntu kayar, sicrama yok."""
    bolum("17. Surukleme ve kenardan kaydirma")

    from PyQt6.QtTest import QTest

    win.mode_tabs.setCurrentIndex(win.mode_tabs.indexOf(win._state_stack))
    win.doc.replace(demo_machine())
    pump(app, 12)
    tuval = win.canvas
    win.set_tool(Tool.SELECT)
    tuval.zoom_fit()
    pump(app, 10)

    sm = win.doc.machine
    off = next(s for s in sm.states.values() if s.name == "Off")
    tuval.set_selected_ids([off.id])
    pump(app)

    vp = tuval.viewport()
    alan = vp.rect()

    # -- Kenara gidince goruntu DORT YONDE de kayar -------------------------- #
    #
    # YASANAN HATA: tuval surukleme sirasinda HIC kaydirilmiyordu; kenara
    # gelen kutu gozden kayboluyordu. Kullanici "pencere disina
    # tasinacaksa otomatik sola/saga/yukari/asagi git" dedi.
    yonler = (("saga", (alan.right() - 4, None), "yatay", 1),
              ("sola", (alan.left() + 4, None), "yatay", -1),
              ("asagi", (None, alan.bottom() - 4), "dikey", 1),
              ("yukari", (None, alan.top() + 4), "dikey", -1))
    for ad, hedef, eksen, isaret in yonler:
        tuval.zoom_fit()
        pump(app, 8)
        guncel = win.doc.machine.states[off.id]
        p0 = tuval.mapFromScene(QPointF(guncel.x + guncel.w / 2.0,
                                        guncel.y + guncel.h / 2.0))
        hx = p0.x() if hedef[0] is None else hedef[0]
        hy = p0.y() if hedef[1] is None else hedef[1]
        # Kaydirma cubugunun HAM degeri yakinlastirmaya baglidir; "goruntu
        # kaydi mi" sorusunun yakinlastirmadan bagimsiz olcusu, goruntu
        # alaninin ORTASININ sahnedeki karsiligidir.
        def merkez():
            nokta = tuval.mapToScene(vp.rect().center())
            return nokta.x() if eksen == "yatay" else nokta.y()

        onceki = merkez()
        QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=p0)
        QTest.mouseMove(vp, QPointF(float(hx), float(hy)).toPoint())
        QTest.qWait(220)
        sonraki = merkez()
        calisiyor = tuval._autoscroll.isActive()
        QTest.mouseRelease(vp, Qt.MouseButton.LeftButton,
                           pos=QPointF(float(hx), float(hy)).toPoint())
        QTest.qWait(40)
        check(calisiyor, "%s surukleyince kaydirma calisiyor" % ad)
        check((sonraki - onceki) * isaret > 1.0,
              "%s yonunde goruntu gercekten kaydi" % ad,
              "%.1f -> %.1f" % (onceki, sonraki))
        check(not tuval._autoscroll.isActive(),
              "%s: birakinca kaydirma durdu" % ad)

    # -- Birakma aninda goruntu YERINDEN OYNAMAZ ----------------------------- #
    #
    # YASANAN HATA: sahne dikdortgeni yalnizca birakildiginda yeniden
    # hesaplaniyordu (1917 -> 3082 px olctuk); kaydirma araligi bir anda
    # degisince goruntudeki her sey ziplyordu. Kullanici bunu "surekli
    # baska yerlere atiyor" diye bildirdi.
    tuval.zoom_fit()
    pump(app, 8)
    guncel = win.doc.machine.states[off.id]
    p0 = tuval.mapFromScene(QPointF(guncel.x + guncel.w / 2.0,
                                    guncel.y + guncel.h / 2.0))
    hedef_nokta = QPointF(float(alan.right() - 30), float(p0.y())).toPoint()
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=p0)
    QTest.mouseMove(vp, hedef_nokta)
    pump(app, 6)
    onceki_h = tuval.horizontalScrollBar().value()
    onceki_v = tuval.verticalScrollBar().value()
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=hedef_nokta)
    pump(app, 15)
    check(abs(tuval.horizontalScrollBar().value() - onceki_h) <= 2
          and abs(tuval.verticalScrollBar().value() - onceki_v) <= 2,
          "birakinca goruntu YERINDEN OYNAMIYOR",
          (onceki_h, tuval.horizontalScrollBar().value(),
           onceki_v, tuval.verticalScrollBar().value()))

    # -- Sahne surukleme SIRASINDA buyuyor ----------------------------------- #
    tuval.zoom_fit()
    pump(app, 8)
    guncel = win.doc.machine.states[off.id]
    p0 = tuval.mapFromScene(QPointF(guncel.x + guncel.w / 2.0,
                                    guncel.y + guncel.h / 2.0))
    genislik0 = tuval.sceneRect().width()
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=p0)
    QTest.mouseMove(vp, QPointF(float(alan.right() - 20),
                                float(p0.y())).toPoint())
    pump(app, 6)
    genislik1 = tuval.sceneRect().width()
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton,
                       pos=QPointF(float(alan.right() - 20),
                                   float(p0.y())).toPoint())
    pump(app, 10)
    check(genislik1 > genislik0,
          "sahne surukleme SIRASINDA buyuyor (birakmayi beklemiyor)",
          (genislik0, genislik1))

    # -- Ayni yetenek SINIF tuvalinde de var --------------------------------- #
    check(hasattr(win.class_canvas, "_autoscroll"),
          "sinif tuvali de kenardan kaydirmayi destekliyor")
    check(win.class_canvas.AUTOSCROLL_MARGIN
          == win.canvas.AUTOSCROLL_MARGIN,
          "iki tuval ayni kaydirma ayarlarini paylasiyor")

    win.doc.mark_clean()


def t_dialogs(app, win, gecici):
    bolum("16. Diyalog akislari")

    # -- Kaydedilmemis degisiklik UYARISI gercekten cikiyor mu --------------- #
    win.doc.replace(demo_machine())
    win.doc.edit("kirlet", lambda m: m.add_state(
        State(name="Kirli", kind=StateKind.SIMPLE, x=900.0, y=900.0)))
    pump(app)
    check(win.doc.is_dirty(), "duzenleme belgeyi kirli isaretledi")

    SahteKutu.gorulen = []
    win.file_new()
    pump(app)
    check(any("not been saved" in m for m in SahteKutu.gorulen),
          "kirli belgede yeni dosya KAYDEDILMEMIS uyarisi veriyor",
          SahteKutu.gorulen[-3:])
    check(not win.doc.is_dirty(), "yeni belge temiz basliyor")

    # -- Ac / Kaydet As ------------------------------------------------------- #
    kayit = os.path.join(gecici, "diyalog.usm")
    win.doc.replace(demo_machine())
    SahteDosyaDiyalogu.sonraki_kayit = kayit
    check(win.file_save_as(), "Farkli Kaydet dosyayi yazdi")
    check(os.path.exists(kayit), "dosya diskte olustu")
    check(win.doc.path == kayit, "belge yolu guncellendi", win.doc.path)

    win.doc.mark_clean()
    win.file_new()
    pump(app)
    SahteDosyaDiyalogu.sonraki_acilis = kayit
    win.file_open()
    pump(app)
    check(len(win.doc.machine.states) > 2, "Ac ile model geri yuklendi",
          len(win.doc.machine.states))

    # -- Disa aktar ----------------------------------------------------------- #
    disa = os.path.join(gecici, "disa")
    os.makedirs(disa, exist_ok=True)
    SahteDosyaDiyalogu.sonraki_dizin = disa
    win.set_language("c")
    win.export_code()
    pump(app, 10)
    yazilan = os.listdir(disa)
    check(yazilan, "Disa Aktar dosyalari yazdi", yazilan)
    check(any(a.endswith(".c") for a in yazilan),
          "disa aktarilanlar arasinda .c var", yazilan)

    # -- Yardim diyaloglari cokme uretmiyor ----------------------------------- #
    #
    # Lisans penceresi gercek bir QDialog.exec() acar; ekransiz kosumda
    # bloklar. Bu yuzden PENCERE yerine ICERIGI sinariz -- kullaniciya
    # bos bir lisans metni gostermek de bir kusur olurdu.
    for ad, fn in (("Hakkinda", win.show_about),
                   ("Kisayollar", win.show_shortcuts)):
        try:
            fn()
            pump(app)
            ok = True
        except Exception as exc:                   # noqa: BLE001
            ok = False
            note("%s diyalogu hata verdi: %s" % (ad, exc))
        check(ok, "%s diyalogu aciliyor" % ad)

    from app.ui.main_window import _load_license_text
    lisans = _load_license_text()
    check("GNU GENERAL PUBLIC LICENSE" in lisans.upper(),
          "GPL v3 metni paketten okunabiliyor", lisans[:60])

    win.doc.mark_clean()


# --------------------------------------------------------------------------- #

def main() -> int:
    import app.ui.main_window as mw
    import app.ui.workspace_tree as wtree

    gercek = (mw.QMessageBox, mw.QFileDialog, wtree.QMessageBox)
    mw.QMessageBox, mw.QFileDialog = SahteKutu, SahteDosyaDiyalogu
    wtree.QMessageBox = SahteKutu

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(ui_font(9))
    app.setStyleSheet(stylesheet())

    gecici = tempfile.mkdtemp(prefix="usd_func_")
    win = t_startup(app)
    try:
        t_tools(app, win)
        t_hierarchy(app, win)
        t_undo(app, win)
        t_clipboard(app, win)
        t_file_cycle(app, win, gecici)
        t_validation(app, win)
        t_codegen(app, win)
        t_simulation(app, win)
        t_class_diagram(app, win)
        ws = t_workspace(app, win, gecici)
        t_git(app, win, ws)
        t_theme(app, win)
        t_panels(app, win)
        t_performance(app, win)
        t_drag_navigation(app, win)
        t_dialogs(app, win, gecici)
    finally:
        mw.QMessageBox, mw.QFileDialog = gercek[0], gercek[1]
        wtree.QMessageBox = gercek[2]
        win.doc.mark_clean()
        win.class_doc.mark_clean()
        try:
            win.git_panel.shutdown()
        except Exception:                          # noqa: BLE001
            pass
        win.settings.clear()
        win.settings.sync()
        win.close()
        shutil.rmtree(gecici, ignore_errors=True)

    print("\n== Ozet ==")
    for satir in _notes:
        print("  . %s" % satir)
    if _failures:
        print("\n  %d fonksiyonellik kontrolu basarisiz:" % len(_failures))
        for f in _failures:
            print("    - %s" % f)
        return 1
    print("\n  Tum fonksiyonellik kontrolleri gecti.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
