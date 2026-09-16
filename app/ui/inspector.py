"""Secili elemanin ozelliklerini duzenleyen panel."""

from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import (QComboBox, QDoubleSpinBox, QFormLayout, QFrame,
                             QGroupBox, QLabel, QLineEdit, QPlainTextEdit,
                             QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from ..core.model import StateKind, TransitionKind
from ..core.text_layout import LINE_BREAK_MARKER
from .document import Document
from .highlighter import CppHighlighter
from .theme import C, mono_font, ui_font

KIND_LABELS = [
    ("State", StateKind.SIMPLE),
    ("Composite State", StateKind.COMPOSITE),
    ("Initial", StateKind.INITIAL),
    ("Final State", StateKind.FINAL),
    ("Choice", StateKind.CHOICE),
    ("Junction", StateKind.JUNCTION),
    ("Shallow History (H)", StateKind.SHALLOW_HISTORY),
    ("Deep History (H*)", StateKind.DEEP_HISTORY),
    ("Terminate", StateKind.TERMINATE),
    ("Fork", StateKind.FORK),
    ("Join", StateKind.JOIN),
    ("Entry Point", StateKind.ENTRY_POINT),
    ("Exit Point", StateKind.EXIT_POINT),
    ("Submachine", StateKind.SUBMACHINE),
]

TKIND_LABELS = [
    ("external", TransitionKind.EXTERNAL),
    ("internal", TransitionKind.INTERNAL),
    ("local", TransitionKind.LOCAL),
]

#: Sozde-durum turlerinin CIZIM OLCUSU. Tuvaldeki palet varsayilanlariyla
#: (canvas._NEW_STATE_DEFAULTS) ayni olmalidir: ayni sekil, nasil
#: yaratildigina gore farkli buyuklukte cizilmemelidir.
#:
#: FORK, JOIN ve baglanti noktalari burada YOKTU. Eksiklik gorunurden
#: fazlasini bozuyordu: 170x84 bir basit durum fork'a cevrilince asagidaki
#: "cok kucukse buyut" dali da calismiyor (olcu zaten buyuk) ve ekranda
#: cubuk yerine KOCA BIR KUTU kaliyordu. Bu sozde-durumlar yeniden
#: boyutlandirilamadigi icin (bkz. StateItem.is_resizable) kullanicinin
#: geri donusu de yoktu -- sekli duzeltmenin tek yolu ogeyi silip yeniden
#: cizmekti.
PSEUDO_SIZES = {
    StateKind.INITIAL: (24.0, 24.0),
    StateKind.FINAL: (30.0, 30.0),
    StateKind.CHOICE: (38.0, 38.0),
    StateKind.JUNCTION: (22.0, 22.0),
    StateKind.SHALLOW_HISTORY: (32.0, 32.0),
    StateKind.DEEP_HISTORY: (32.0, 32.0),
    StateKind.TERMINATE: (30.0, 30.0),
    StateKind.FORK: (10.0, 90.0),
    StateKind.JOIN: (10.0, 90.0),
    StateKind.ENTRY_POINT: (18.0, 18.0),
    StateKind.EXIT_POINT: (18.0, 18.0),
}

#: GERCEK durum turlerinin, kucuk bir sozde-durumdan donuldugunde
#: verilecek olcusu.
REAL_SIZES = {
    StateKind.COMPOSITE: (320.0, 200.0),
    StateKind.SUBMACHINE: (220.0, 96.0),
    StateKind.SIMPLE: (170.0, 84.0),
}


class _TekerleksizKarisim:
    """Odakli DEGILKEN fare tekerlegini yok sayar.

    GERCEK BIR VERI KAYBI KUSURUYDU. Qt'de bir QComboBox / QSpinBox,
    imlec uzerindeyse tekerlek olayini yakalar ve DEGERINI DEGISTIRIR --
    tiklanmamis, odaklanmamis olsa bile. Kullanici PROPERTIES panelini
    kaydirirken imlec "Event" kutusunun uzerinden gecince kutu sessizce
    ilk olaya ("BUTTON") atliyor, odak kaybinda da modele yaziliyordu.

    Sonuc: baslangic (initial) gecisine olay eklenmis oluyor ve
    "V034 - An initial transition cannot have an event." hatasi, kullanici
    hicbir sey yazmadan Problems panelinde beliriyordu. Tam olarak
    olculdu: tek bir tekerlek centigi '' -> 'BUTTON'.

    Odaklanmamis widget tekerlegi YOK SAYAR ve olayi ustteki kaydirma
    alanina birakir; boylece panel beklenen sekilde kayar.
    """

    def wheelEvent(self, event) -> None:
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class NoWheelComboBox(_TekerleksizKarisim, QComboBox):
    pass


class NoWheelSpinBox(_TekerleksizKarisim, QSpinBox):
    pass


class NoWheelDoubleSpinBox(_TekerleksizKarisim, QDoubleSpinBox):
    pass


class MiniCodeEdit(QPlainTextEdit):
    """Kucuk, sozdizimi renklendirmeli tek/iki satirlik kod alani."""

    committed = pyqtSignal(str)

    def __init__(self, rows: int = 2, placeholder: str = "",
                 highlight: bool = True) -> None:
        super().__init__()
        self.setFont(mono_font(9))
        self.setPlaceholderText(placeholder)

        # Satir sonu isaretini KESFEDILEBILIR yap. Isaret butun metin
        # alanlarinda gecerli; cagiran daha ozel bir ipucu koyarsa
        # (ornegin "baslangic gecisinde guard olamaz") o kazanir.
        self.setToolTip("Type %s to break the line on the diagram."
                        % LINE_BREAK_MARKER)

        # DUZ METIN SARILIR, KOD SARILMAZ.
        #
        # Aciklama alani duz yazidir; sarmak dogal olani. Guard / eylem /
        # include alanlari KOD tasir ve satir sonu anlamlidir, orada
        # sarmak satiri yaniltici gosterir -- yatay kaydirma dogrusu.
        self._wraps = not highlight
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth
                             if self._wraps
                             else QPlainTextEdit.LineWrapMode.NoWrap)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff if self._wraps
            else Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._rows = max(1, rows)
        self._apply_height()

        # Zamanlayici BU WIDGET'IN COCUGU. Panel, secim degisince formu
        # yikar; sahipsiz bir `QTimer.singleShot` silinmis C++ nesnesi
        # uzerinde atesler ve sureci segfault ile dusururdu. Cocuk timer
        # widget'la birlikte yok olur.
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.setInterval(0)
        self._fit_timer.timeout.connect(self.fit_to_content)

        self.document().documentLayout().documentSizeChanged.connect(
            self._on_doc_size)
        self.setTabStopDistance(4 * self.fontMetrics().horizontalAdvance(" "))
        self.highlighter = CppHighlighter(self.document()) if highlight else None

        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Base, QColor(C.EDITOR_BG))
        self.setPalette(pal)
        self._value = ""

    #: Kutu en fazla bu kadar satira buyur; sonrasinda kaydirilir.
    MAX_ROWS = 8

    def _chrome(self) -> int:
        """Metin disindaki dikey giderler (cerceve, kenar, kaydirma cubugu).

        Eski hesap ``lineSpacing * rows + 8`` idi ve uc gideri saymiyordu:
        cerceve kalinligi, belge kenar boslugu ve -- en onemlisi --
        **yatay kaydirma cubugu**. `NoWrap` ile uzun bir satir cubugu
        getirir ve cubuk SABIT yuksekligin ICINDEN ~14 px goturur; son
        satir tam da bu yuzden ortadan kesiliyordu.
        """
        # +6: belge duzeninin yuvarlama payi. 4 px'te icerik bir-iki
        # piksel tasiyor ve gereksiz bir kaydirma cubugu beliriyordu.
        gider = 2 * self.frameWidth() + 2 * int(self.document().documentMargin()) + 6
        if not self._wraps:
            gider += self.horizontalScrollBar().sizeHint().height()
        return gider

    def _apply_height(self, satir: Optional[int] = None) -> None:
        """Kutuyu `satir` (verilmezse `_rows`) satiri TAM gosterecek boyutlar."""
        n = self._rows if satir is None else satir
        n = max(self._rows, min(self.MAX_ROWS, n))
        self.setFixedHeight(self.fontMetrics().lineSpacing() * n + self._chrome())

    def _schedule_fit(self) -> None:
        """Olcumu, Qt yerlesimi bitirdikten SONRAYA birakir.

        `documentSizeChanged` widget daha yerlesmeden, genisligi
        bilinmezken gelir ve her seferinde "1 satir" bildirir; o anda
        olcmek aciklama kutusunu iki satirda cakili birakiyordu.
        Sifir gecikmeli zamanlayici, olay dongusu bir tur dondukten --
        yani viewport genisligi ve sarma hesabi kesinlestikten -- sonra
        calisir. Tek-atimlik timer'i yeniden baslatmak, ardarda gelen
        sinyalleri kendiliginden TEK olcume indirir.
        """
        self._fit_timer.start()

    def _on_doc_size(self, _boyut) -> None:
        """`documentSizeChanged` -> olcumu ertele (gelen boyut bayat)."""
        self._schedule_fit()

    def fit_to_content(self) -> None:
        """Kutuyu ICERIGE gore buyutur (en fazla ::MAX_ROWS satir).

        Sabit iki satir, aciklama gibi alanlarda metnin bir kismini
        gorunmez birakiyordu: kullanici yazdigi cumleyi okumak icin
        kaydirmak zorundaydi.

        DIKKAT: `QPlainTextDocumentLayout.documentSize()` bir istisnadir.
        Genisligi piksel, YUKSEKLIGI ise SATIR SAYISI olarak verir (duz
        metin duzeni sadelestirilmis bir duzendir). Pikselmis gibi satir
        yuksekligine bolmek 6 satirlik bir aciklamayi 0.4 -> 1 satira
        indiriyor, kutu hic buyumuyordu.
        """
        duzen = self.document().documentLayout()
        satir = duzen.documentSize().height() if duzen is not None else 0.0

        gereken = int(satir + 0.999)
        hedef = max(self._rows, min(self.MAX_ROWS, gereken))

        yeni = self.fontMetrics().lineSpacing() * hedef + self._chrome()
        if yeni != self.height():
            # setFixedHeight yeni bir resizeEvent dogurur; yalnizca
            # DEGISIKLIK varken cagirmak dongunun kapanmasini saglar.
            self.setFixedHeight(yeni)

    def resizeEvent(self, event) -> None:
        # Genislik degisince SARILAN metnin satir sayisi da degisir;
        # panel daraldiginda kutu buyumezse alt satirlar gizlenirdi.
        super().resizeEvent(event)
        if self._wraps:
            self._schedule_fit()

    def set_value(self, text: str) -> None:
        self._value = text
        if self.toPlainText() != text:
            self.setPlainText(text)
        # Yukseklik documentSizeChanged sinyaliyle ayarlanir (bkz.
        # fit_to_content); burada cagirmak yerlesme oncesi olacagi icin
        # yanlis satir sayisi verir.

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self._maybe_commit()

    def _maybe_commit(self) -> None:
        text = self.toPlainText()
        if text != self._value:
            self._value = text
            self.committed.emit(text)


class Inspector(QScrollArea):
    """Secime gore degisen ozellik formu."""

    message = pyqtSignal(str)

    def __init__(self, doc: Document, parent=None) -> None:
        super().__init__(parent)
        self.doc = doc
        self._loading = False
        self._ids: List[str] = []

        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._host = QWidget()
        self._layout = QVBoxLayout(self._host)
        # Kullanici paneli "cok buyuk" buldu; bosluklar kisildi.
        self._layout.setContentsMargins(8, 6, 8, 6)
        self._layout.setSpacing(6)
        self.setWidget(self._host)
        self.show_selection([])

    # ------------------------------------------------------------------ API #

    def show_selection(self, ids: List[str]) -> None:
        self._ids = list(ids)
        self._clear()
        sm = self.doc.machine

        states = [i for i in ids if i in sm.states]
        trans = [i for i in ids if i in sm.transitions]

        if len(ids) > 1:
            self._build_multi(len(states), len(trans))
        elif states:
            self._build_state(states[0])
        elif trans:
            self._build_transition(trans[0])
        else:
            self._build_machine()
        self._layout.addStretch(1)

    def retheme(self) -> None:
        """Formu yeniden kurar: satir-ici renkler kurulumda okunuyor."""
        self.show_selection(self._ids)

    def selected_ids(self) -> List[str]:
        """Formun su an gosterdigi eleman id'leri."""
        return list(self._ids)

    def refresh(self) -> None:
        """Model disaridan degistiginde formu tazeler.

        Kullanici bir metin alanina YAZIYORSA dokunulmaz; aksi halde
        yazilan sey tusa basildikca sifirlanirdi.

        AMA yalnizca metin alanlari icin. Eskiden panel ICINDEKI HERHANGI
        bir bilesen odakliysa tazeleme tumden atlaniyordu ve en cok da
        "Kind" kutusu bunu tetikliyordu: tur degistiren kullanicinin odagi
        zaten o kutudadir. Form eski turun satirlariyla ayakta kaliyordu
        -- bir basit durum junction'a cevrildikten sonra "Defers" ve
        davranis kutulari ekranda duruyor, birine dokunmak UML'de
        sozde-durumda BULUNAMAYACAK bir alani modele yaziyordu
        (14.5.9.6: bunlar State ozellikleridir).

        DONDURME KUTUSU DA KORUNUR, acilir kutu korunmaz. Ayrim, formun
        yeniden kurulmasinin o bilesende NE BOZDUGUNA dayanir:

        * "Kind" acilir kutusu formu ZATEN degistirmelidir -- turle
          birlikte hangi satirlarin var olacagi degisir. Korunsaydi
          sozde-duruma cevrilen bir durumda "Defers" ve davranis kutulari
          ekranda kalirdi.
        * Dondurme kutusu ise formu degistirmez, ama yeniden kurulmak onu
          YOK EDER: kullanici yukari okuna her bastiginda bilesen silinip
          yeniden yaratiliyor, odak kayboluyor ve klavye izleme kapali
          oldugu icin yarim yazilmis deger de atiliyordu.

        NOT: `QApplication.focusWidget()` bir QSpinBox odaklandiginda
        KUTUNUN KENDISINI dondurur, icindeki QLineEdit'i degil; bu yuzden
        QAbstractSpinBox acikca listelenir.
        """
        from PyQt6.QtWidgets import (QAbstractSpinBox, QApplication,
                                     QLineEdit, QPlainTextEdit, QTextEdit)
        focus = QApplication.focusWidget()
        korunan = (QLineEdit, QPlainTextEdit, QTextEdit, QAbstractSpinBox)
        if isinstance(focus, korunan) and self._host.isAncestorOf(focus):
            return
        self.show_selection(self._ids)

    # -------------------------------------------------------------- yardimci #

    def _clear(self) -> None:
        """Formu bosaltir.

        deleteLater() TEK BASINA YETMEZ: silmeyi bir sonraki olay dongusune
        erteler, oysa bilesen o ana kadar hala panelin cocugudur ve
        yerlesimden cikarildigi icin son (ya da hic yerlesmemisse varsayilan
        640x480) geometrisiyle panelin UZERINE cizilmeye devam eder.
        Kullanici secimi hizlica degistirdiginde -- ya da bir alanin
        editingFinished sinyali modeli degistirip refresh() cagirdiginda --
        olay dongusu araya girmez ve ust uste binmis birden fazla "hayalet"
        form gorunur. Once ayirip sonra silmek gerekir; silmeyi yine de
        ertelemek sart, cunku bu kod silinen bilesenin kendi sinyalinin
        icinden cagrilabilir.
        """
        while self._layout.count():
            item = self._layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

    def _group(self, title: str) -> QFormLayout:
        box = QGroupBox(title)
        form = QFormLayout(box)
        form.setContentsMargins(8, 4, 8, 6)
        form.setSpacing(4)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight
                               | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        # ETIKET HER ZAMAN ALANIN USTUNDE.
        #
        # Iki sutunlu duzende "Extra includes" gibi uzun bir etiket yaninda
        # alana ~94 px kaliyordu ve icerik ("blinky_ctx_t",
        # '#include "blinky_ctx.h"') kirpiliyordu -- kullanici yazdigi seyi
        # goremiyordu. WrapLongRows denendi: Qt satiri "sigiyor" saydigi
        # icin hic tetiklenmedi. Ozellikler paneli dogasi geregi DAR
        # oldugundan tek sutun dogru olan: alan panelin TAM genisligini
        # alir ve hicbir genislikte kirpilmaz.
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft
                               | Qt.AlignmentFlag.AlignVCenter)
        self._layout.addWidget(box)
        return form

    def _hint(self, text: str) -> None:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setFont(ui_font(8))
        label.setStyleSheet("color: %s;" % C.TEXT_DIM)
        self._layout.addWidget(label)

    def _edit(self, mutator, label: str) -> None:
        if self._loading:
            return
        if self.doc.edit(label, mutator):
            self.message.emit(label)

    # --------------------------------------------------------------- makine  #

    def _build_machine(self) -> None:
        sm = self.doc.machine
        self._loading = True
        form = self._group("State machine")

        name = QLineEdit(sm.name)
        name.editingFinished.connect(
            lambda: self._edit(lambda m: setattr(m, "name", name.text().strip() or m.name),
                               "Machine name"))
        form.addRow("Name", name)

        prefix = QLineEdit(sm.prefix)
        prefix.setToolTip("Prefix of the generated C symbols, e.g. 'blinky' -> blinky_dispatch()")
        prefix.editingFinished.connect(
            lambda: self._edit(lambda m: setattr(m, "prefix", prefix.text().strip() or m.prefix),
                               "Symbol prefix"))
        form.addRow("Symbol prefix", prefix)

        ctx = QLineEdit(sm.context_type)
        ctx.setToolTip("The type that appears as 'ctx' inside action and "
                       "guard bodies.\n"
                       "Write 'void' to generate no context at all.")
        ctx.editingFinished.connect(
            lambda: self._edit(lambda m: setattr(m, "context_type",
                                                 ctx.text().strip() or "void"),
                               "Context type"))
        form.addRow("Context type", ctx)

        inc = MiniCodeEdit(2, '#include "app_types.h"')
        inc.set_value(sm.user_includes)
        inc.committed.connect(
            lambda t: self._edit(lambda m: setattr(m, "user_includes", t), "Extra includes"))
        form.addRow("Extra includes", inc)

        desc = MiniCodeEdit(2, "What does this machine do?", highlight=False)
        desc.setFont(ui_font(9))
        desc.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        desc.set_value(sm.description)
        desc.committed.connect(
            lambda t: self._edit(lambda m: setattr(m, "description", t), "Description"))
        form.addRow("Description", desc)

        self._loading = False

    # ---------------------------------------------------------------- durum  #

    def _build_state(self, sid: str) -> None:
        sm = self.doc.machine
        st = sm.states[sid]
        self._loading = True

        form = self._group("State")

        name = QLineEdit(st.name)
        name.setObjectName("state_name")
        name.editingFinished.connect(
            lambda: self._edit(self._rename(sid, name), "State name"))
        form.addRow("Name", name)
        self._name_field = name

        kind = NoWheelComboBox()
        for text, value in KIND_LABELS:
            kind.addItem(text, value)
        kind.setCurrentIndex([k for _, k in KIND_LABELS].index(st.kind))
        kind.activated.connect(lambda _i: self._change_kind(sid, kind.currentData()))
        form.addRow("Kind", kind)

        parent = sm.parent_of(sid)
        if parent is not None and sm.is_orthogonal(parent.id):
            lbl = QLabel("%s  ·  region %d of %d"
                         % (parent.name, sm.region_of(sid) + 1,
                            sm.region_count(parent.id)))
            lbl.setToolTip("Drag the state into another band of the "
                           "orthogonal state to move it to that region.")
        else:
            lbl = QLabel(parent.name if parent else "(root region)")
        lbl.setStyleSheet("color: %s;" % C.TEXT_DIM)
        form.addRow("Parent state", lbl)

        # ERTELENEN OLAYLAR -- yalnizca gercek durumlarda.
        #
        # UML 2.5.1, 14.5.9.6: deferrableTrigger bir DURUM ozelligidir.
        # Virgulle ayrilmis liste; bos birakmak "hicbiri" demektir.
        if st.kind.is_real_state:
            ertelenen = QLineEdit(", ".join(st.deferred or []))
            ertelenen.setObjectName("state_deferred")
            ertelenen.setAccessibleName("Deferred events")
            ertelenen.setPlaceholderText("e.g. PRINT, RESIZE")
            ertelenen.setToolTip(
                "Event types held in the pool while this state is active "
                "instead of being dispatched. They are delivered once the "
                "machine reaches a configuration that no longer defers them "
                "(UML 2.5.1, 14.2.3.4.4).")
            ertelenen.editingFinished.connect(
                lambda _sid=sid, _f=ertelenen: self._edit(
                    self._set_deferred(_sid, _f.text()), "Deferred events"))
            form.addRow("Defers", ertelenen)

        # ALTMAKINE REFERANSI -- yalnizca altmakine durumlarinda.
        #
        # Calisma alanindaki .usm dosyalari LISTELENIR. Serbest metin
        # kutusu olsaydi kullanici yolu yanlis yazabilir ve hatayi ancak
        # kod uretiminde gorurdu; ustelik yol calisma alanina GOREDIR.
        if st.kind is StateKind.SUBMACHINE:
            secim = NoWheelComboBox()
            secim.setAccessibleName("Referenced machine")
            secim.setToolTip(
                "The state machine this state stands for. Its contents are "
                "inserted here when code is generated (UML 2.5.1, "
                "14.2.3.4.7).")
            mevcut = (getattr(st, "submachine_ref", "") or "").strip()
            secim.addItem("(none)", "")
            for yol in self._workspace_machines():
                secim.addItem(yol, yol)
            if mevcut and secim.findData(mevcut) < 0:
                # Referans edilen dosya artik yok; SESSIZCE silmek yerine
                # goster ki kullanici neyin kayip oldugunu bilsin.
                secim.addItem("%s  (missing)" % mevcut, mevcut)
            secim.setCurrentIndex(max(0, secim.findData(mevcut)))
            secim.activated.connect(
                lambda _i, _sid=sid, _c=secim: self._edit(
                    self._set_submachine(_sid, _c.currentData()),
                    "Submachine reference"))
            form.addRow("Machine", secim)

        # BOLGE SAYISI -- yalnizca bilesik durumlarda.
        #
        # UML 2.5.1, 14.2.3.2 (basili s.307): bir bilesik durum bir ya da
        # daha cok bolge sahibidir; birden cok bolgesi olan durum
        # ORTOGONALDIR ve bolgeleri es zamanli etkindir.
        if st.kind is StateKind.COMPOSITE:
            bolge = NoWheelSpinBox()
            bolge.setRange(1, 8)
            bolge.setValue(max(1, int(getattr(st, "regions", 1) or 1)))
            bolge.setAccessibleName("Region count")
            bolge.setToolTip(
                "How many orthogonal regions this state owns. Two or more "
                "regions run at the same time; each needs its own initial "
                "pseudostate.")
            # Klavye izleme KAPALI: kutuya "12" yazmak once 1 degerini
            # yayinlar ve aradaki her deger ayri bir duzenleme olurdu.
            bolge.setKeyboardTracking(False)
            bolge.valueChanged.connect(
                lambda v, _sid=sid: self._edit(self._set_regions(_sid, v),
                                               "Region count"))
            form.addRow("Regions", bolge)

        # DAVRANISLAR: yalnizca ICINDE KALINABILEN durumlarda.
        #
        # `is_real_state` FINAL'i de iceriyor cunku KOD URETIMI icin final
        # de bir durumdur. Ama UML'de (14.2.3.4) bir FinalState entry /
        # exit / doActivity TASIYAMAZ -- form yine de uc kutu aciyordu.
        # Kullanici: "toollarin ozelliklerine gore properties tamami
        # gozukmuyor"; ogenin turune ait OLMAYAN alanlar da gosteriliyordu.
        # FINAL'de alanlar, ICLERINDE ZATEN BIR SEY VARSA gosterilir.
        # Aksi halde eski bir dosyadan gelen davranis V069 hatasi uretir
        # ama kullanicinin onu SILECEGI hicbir yer kalmazdi.
        final_dolu = (st.kind == StateKind.FINAL
                      and (st.entry.strip() or st.exit.strip()
                           or st.do.strip()))
        if st.kind.is_real_state and (st.kind != StateKind.FINAL or final_dolu):
            beh = self._group("Behaviors")
            if final_dolu:
                uyari = QLabel("A final state cannot carry behavior "
                               "(UML 2.5.1 §14.2.3.4) — clear these fields.")
                uyari.setWordWrap(True)
                uyari.setStyleSheet("color: %s;" % C.WARN)
                beh.addRow(uyari)
            for attr, caption, ph in (("entry", "entry /", "when the state becomes active"),
                                      ("exit", "exit /", "when the state is exited"),
                                      ("do", "do /", "on every <prefix>_do() call")):
                # Tek satirla baslar, ICERIGE gore buyur (fit_to_content).
                # Sabit iki satir, uc kutuda 39 px fazladan yer kapliyor ve
                # form panele sigmiyordu.
                ed = MiniCodeEdit(1, ph)
                ed.set_value(getattr(st, attr))
                ed.committed.connect(
                    lambda t, a=attr: self._edit(
                        lambda m: setattr(m.states[sid], a, t), "%s behavior" % a))
                beh.addRow(caption, ed)
            self._hint("Enter, or %s anywhere in the text, breaks the line "
                       "on the diagram. Inside a C string it stays literal, "
                       "so printf(\"…%s\") is left alone."
                       % (LINE_BREAK_MARKER, LINE_BREAK_MARKER))

        # GEOMETRI ALANLARI (X / Y / Width / Height) KALDIRILDI.
        #
        # Kullanici: "properties'de sacma ozellikleri yazma, x y eksenindeki
        # bilgileri ben ne yapayim, ya da genislik yukseklik gibi". Hakli:
        # kutuyu tuvalde surukleyerek ve kenarindan cekerek konumlandirmak
        # hem daha hizli hem de gordugun seyi verir. Dort sayi kutusu
        # panelin en degerli yerini -- davranislarin hemen altini --
        # kapliyordu. Konum/boyut MODELDE duruyor, yalnizca formdan cikti.
        note_grubu = self._group("Note")
        note = QLineEdit(st.note)
        note.editingFinished.connect(
            lambda: self._edit(lambda m: setattr(m.states[sid], "note", note.text()),
                               "Note"))
        note_grubu.addRow("Note", note)

        self._loading = False

    def _set_deferred(self, sid: str, metin: str):
        """Virgulle ayrilmis listeyi ERTELENEN olaylara cevirir."""
        adlar = []
        for parca in (metin or "").replace(";", ",").split(","):
            ad = parca.strip()
            if ad and ad not in adlar:
                adlar.append(ad)

        def mutate(machine):
            st = machine.states.get(sid)
            if st is not None:
                st.deferred = list(adlar)
        return mutate

    def _workspace_machines(self):
        """Calisma alanindaki durum makinesi dosyalari (goreli yollar).

        `Workspace.model_path` bir OZELLIKTIR, islev degil. Burada
        `ws.model_path()` diye cagriliyordu: her seferinde
        `TypeError: 'str' object is not callable` firlatiyor, hemen
        altindaki genis `except Exception` onu yutuyor ve islev BOS LISTE
        donduruyordu.

        Sonucu kucuk degildi: "Machine" acilir kutusu HER calisma
        alaninda, HER ZAMAN bos kaliyordu. Kullanici bir altmakine durumu
        cizebiliyor ama onu bir makineye BAGLAYAMIYORDU -- yani
        altmakine ozelliginin tamami arayuzden erisilemezdi. Kusuru
        gizleyen sey de o genis `except`ti: bir programlama hatasini
        "hicbir makine bulunamadi" gibi makul bir sonuca cevirdi.

        Bu yuzden burada artik YALNIZCA dosya sistemi hatalari yutulur;
        baska bir istisna yukari cikar ve gorunur olur.
        """
        import os
        pencere = self.window()
        ws = getattr(pencere, "workspace", None)
        if ws is None:
            return []
        kok = ws.model_path
        if not os.path.isdir(kok):
            return []
        try:
            adlar = sorted(os.listdir(kok))
        except OSError:
            return []
        out = []
        for ad in adlar:
            if not ad.lower().endswith(".usm"):
                continue
            try:
                out.append(ws.relative(os.path.join(kok, ad)))
            except ValueError:
                continue                        # kok disinda kalan yol
        return out

    def _set_submachine(self, sid: str, ref: str):
        def mutate(machine):
            st = machine.states.get(sid)
            if st is not None:
                st.submachine_ref = (ref or "").strip()
        return mutate

    def _set_regions(self, sid: str, sayi: int):
        """Bilesik durumun bolge sayisini degistiren islem.

        KUCULTMEK ARTIK KAYIPLI DEGILDIR. Bolge numarasi, ogenin
        cizildigi SERITTEN her yeniden cizimde yeniden okunur
        (DiagramCanvas._sync_regions_from_drawing) ve ogelerin
        koordinati bu islemde degismez. Uc bolgeden bire inip yeniden
        uce cikmak, eski dagilimi aynen geri getirir: bilgi zaten
        `region` alaninda degil, KONUMDA duruyordu.

        Buradaki kelepceleme, tuval bir sonraki kez cizilene kadar
        modelin gecerli kalmasi icindir; eski ya da elle duzenlenmis bir
        dosya olmayan bir bolgeyi gosteriyorsa da onu toparlar
        (dogrulayici ayrica V103 ile bildirir).
        """
        def mutate(machine):
            st = machine.states.get(sid)
            if st is None:
                return
            st.regions = max(1, int(sayi))
            son = st.regions - 1
            for c in machine.children(sid):
                if int(getattr(c, "region", 0) or 0) > son:
                    c.region = son
        return mutate

    def _rename(self, sid: str, field: QLineEdit):
        def mutate(m):
            text = field.text().strip()
            if text:
                m.states[sid].name = text
        return mutate

    def _change_kind(self, sid: str, new_kind: StateKind) -> None:
        sm = self.doc.machine
        st = sm.states[sid]
        if st.kind is new_kind:
            return
        if st.kind is StateKind.COMPOSITE and sm.children(sid):
            self.message.emit("Move the substates out first: the composite state is not empty.")
            self.show_selection(self._ids)
            return

        def mutate(m):
            target = m.states[sid]
            target.kind = new_kind
            if new_kind in PSEUDO_SIZES:
                target.w, target.h = PSEUDO_SIZES[new_kind]
                # Sozde-durumun DAVRANISI OLMAZ. UML 2.5.1, 14.5.9.6:
                # entry/exit/doActivity ve ertelenen tetikleyiciler State
                # ozellikleridir, Pseudostate degil. Ertelenen olaylar
                # eskiden HIC temizlenmiyordu: bir basit durumu junction'a
                # cevirmek, uretilen `deferred[]` tablosunda hicbir
                # diyagram ogesinin anlatmadigi bir kayit birakiyordu.
                target.entry = ""
                target.exit = ""
                target.do = ""
                target.deferred = []
            elif target.w < 90.0 or target.h < 54.0:
                target.w, target.h = REAL_SIZES.get(new_kind,
                                                    REAL_SIZES[StateKind.SIMPLE])
            if new_kind is not StateKind.SUBMACHINE:
                # Alt makine adi yalnizca SUBMACHINE turunde anlamlidir;
                # kalirsa dogrulama ve duzlestirme olmayan bir belgeyi
                # aramaya devam eder.
                target.submachine_ref = ""

        self._edit(mutate, "State kind")

    def focus_name_field(self) -> None:
        field = getattr(self, "_name_field", None)
        if field is not None and field.parent() is not None:
            field.setFocus()
            field.selectAll()

    # ---------------------------------------------------------------- gecis  #

    def _build_transition(self, tid: str) -> None:
        sm = self.doc.machine
        tr = sm.transitions[tid]
        self._loading = True

        form = self._group("Transition")

        src = sm.states.get(tr.source)
        dst = sm.states.get(tr.target)
        route = QLabel("%s  →  %s" % (src.name if src else "?", dst.name if dst else "?"))
        # STATE_TITLE tuvaldeki renkli baslik seridi icindir (iki temada da
        # beyaz); acik temada panel zemininde okunmazdi.
        route.setStyleSheet("color: %s; font-weight: 700;" % C.TEXT_BRIGHT)
        form.addRow("Route", route)

        # BASLANGIC GECISI olay da koruma da TASIYAMAZ (UML 2.5.1
        # §14.5.6.7; dogrulayicida V034 / V035). Alanlari yazilabilir
        # birakmak kullaniciyi ancak hata uretmeye davet eder -- nitekim
        # tekerlek kazasiyla tam da bu oldu. Alanlar gorunur kalir
        # (kural ogreticidir) ama KAPALIDIR ve nedenini soyler.
        baslangic = src is not None and src.kind == StateKind.INITIAL

        event = NoWheelComboBox()
        event.setEditable(True)
        event.addItem("")
        for ev in sm.events():
            if ev:
                event.addItem(ev)
        event.setCurrentText(tr.event)
        event.setToolTip("Leave empty for a completion (event-less) transition.")
        event.lineEdit().editingFinished.connect(
            lambda: self._edit(
                lambda m: setattr(m.transitions[tid], "event",
                                  event.currentText().strip()), "Event"))
        form.addRow("Event", event)

        guard = MiniCodeEdit(1, "ctx->count > 3   or   else")
        guard.set_value(tr.guard)
        guard.committed.connect(
            lambda t: self._edit(lambda m: setattr(m.transitions[tid], "guard", t),
                                 "Guard"))
        form.addRow("[Guard]", guard)

        if baslangic:
            engel = ("An initial transition cannot have an event or a guard "
                     "(UML 2.5.1 §14.5.6.7).")
            for w in (event, guard):
                w.setEnabled(False)
                w.setToolTip(engel)

        action = MiniCodeEdit(2, "ctx->count++;")
        action.set_value(tr.action)
        action.committed.connect(
            lambda t: self._edit(lambda m: setattr(m.transitions[tid], "action", t),
                                 "Action"))
        form.addRow("/ Effect", action)
        self._hint("Type %s in the event, guard or effect to break the "
                   "transition label over several lines."
                   % LINE_BREAK_MARKER)

        adv = self._group("Details")

        kind = NoWheelComboBox()
        for text, value in TKIND_LABELS:
            kind.addItem(text, value)
        kind.setCurrentIndex([k for _, k in TKIND_LABELS].index(tr.kind))
        kind.activated.connect(
            lambda _i: self._edit(
                lambda m: setattr(m.transitions[tid], "kind", kind.currentData()),
                "Transition kind"))
        adv.addRow("Kind", kind)

        prio = NoWheelSpinBox()
        prio.setRange(0, 99)
        prio.setValue(tr.priority)
        prio.setToolTip("Lower numbers are tried first; this orders the branches of a choice.")
        prio.editingFinished.connect(
            lambda: self._edit(
                lambda m: setattr(m.transitions[tid], "priority", prio.value()),
                "Priority"))
        adv.addRow("Priority", prio)

        preview = QLabel(tr.label() or "(completion)")
        preview.setFont(mono_font(9))
        preview.setStyleSheet("color: %s;" % C.GREEN)
        preview.setWordWrap(True)
        adv.addRow("UML label", preview)

        self._loading = False

    # ------------------------------------------------------------- coklu secim

    def _build_multi(self, n_states: int, n_trans: int) -> None:
        form = self._group("Multiple selection")
        form.addRow("State", QLabel(str(n_states)))
        form.addRow("Transition", QLabel(str(n_trans)))
