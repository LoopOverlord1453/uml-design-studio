"""Sag panel: uretilen kaynak dosyalar, dil secimi ve disa aktarma."""

from __future__ import annotations

from typing import Dict, List

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel,
                             QSizePolicy,
                             QPushButton, QTabWidget, QVBoxLayout, QWidget)

from .code_editor import CodeEditor
from .find_bar import FindBar
from .theme import C, mono_font, ui_font

LANGUAGES = [
    ("C  (C11 + GNU)", "c"),
    ("C++ 11", "cpp"),
    ("PlantUML", "puml"),
]


class CodePanel(QWidget):
    """Uretilen kodu gosteren, salt-okunur sekmeli panel."""

    language_changed = pyqtSignal(str)
    export_requested = pyqtSignal()
    copy_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._editors: Dict[str, CodeEditor] = {}
        self._shown: Dict[str, str] = {}      # son gosterilen icerik (normalize)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # -------------------------------------------------------------- baslik
        bar = QFrame()
        bar.setObjectName("codeBar")
        self._bar = bar
        row = QHBoxLayout(bar)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(8)

        title = QLabel("GENERATED CODE")
        f = ui_font(8)
        f.setBold(True)
        f.setLetterSpacing(f.SpacingType.AbsoluteSpacing, 1.0)
        title.setFont(f)
        self._title = title
        title.setSizePolicy(QSizePolicy.Policy.Ignored,
                            QSizePolicy.Policy.Preferred)
        row.addWidget(title)

        row.addSpacing(6)
        self.language = QComboBox()
        for text, key in LANGUAGES:
            self.language.addItem(text, key)
        # SABIT GENISLIK DEGIL: sabit 140 px, baslik seridinin asgari
        # genisligini panelin tamamina dayatiyordu (555 px). Panel o
        # sinirin altina inemedigi icin "dar oldugunda gizle" mantigi hic
        # devreye girmiyor, bunun yerine metinler kirpiliyordu.
        self.language.setMinimumWidth(92)
        self.language.setMaximumWidth(150)
        self.language.setSizePolicy(QSizePolicy.Policy.Expanding,
                                    QSizePolicy.Policy.Fixed)
        self.language.currentIndexChanged.connect(
            lambda _i: self.language_changed.emit(self.language.currentData()))
        row.addWidget(self.language)

        row.addStretch(1)

        self.status = QLabel("")
        self.status.setFont(ui_font(8))
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored,
                                  QSizePolicy.Policy.Preferred)
        row.addWidget(self.status)

        copy_btn = QPushButton("Copy")
        copy_btn.setToolTip("Copies the file in the active tab to the clipboard")
        copy_btn.clicked.connect(self.copy_requested.emit)
        row.addWidget(copy_btn)

        export_btn = QPushButton("Export…")
        export_btn.setToolTip("Writes every generated file into a folder")
        export_btn.clicked.connect(self.export_requested.emit)
        row.addWidget(export_btn)

        # BASLIK SERIDI HICBIR ZAMAN KIRPILMAZ.
        #
        # Serit sabit genislikli ogelerden olusur; panel daraldiginda Qt
        # hepsini sikistiriyor ve metinler kirpiliyordu ("GENERA' C (C99)
        # files |opy| por"). Kirpilmis bir dugme kullanilamaz.
        #
        # "Dar olunca gizle" denendi ve ISE YARAMADI: panel zaten seridin
        # asgari genisliginin altina inemedigi icin esik hic tetiklenmiyordu
        # -- gizleme karari, gizlemenin MUMKUN KILDIGI genislige bagliydi.
        # Bunun yerine seridin gercekte ihtiyac duydugu genislik panelin
        # ASGARISI yapilir. Kullanici daha fazla yer isterse paneli F9 ile
        # tamamen gizleyebilir; yarim gorunen bir panel kimseye yaramaz.
        self._optional = []
        #: Baslik seridinde KISALAMAYAN ogeler.
        self._header_buttons = [copy_btn, export_btn]

        bar.setMinimumWidth(0)
        layout.addWidget(bar)

        # -------------------------------------------------------------- uyari
        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setFont(ui_font(9))
        self.banner.setContentsMargins(12, 8, 12, 8)
        self.banner.hide()
        layout.addWidget(self.banner)

        # ------------------------------------------------------------ sekmeler
        self._apply_static_styles()
        self._fit_header()

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabPosition(QTabWidget.TabPosition.North)
        layout.addWidget(self.tabs, 1)

        # ------------------------------------------------------- arama (Ctrl+F)
        # SALT ARAMA: uretilen dosyalar modelden turetildigi icin panelde
        # duzenlemeye izin verilmez (bkz. find_bar.py). Cubuk yalnizca
        # imleci tasir ve eslemeleri vurgular.
        self.find = FindBar(self)
        layout.addWidget(self.find)
        self.tabs.currentChanged.connect(
            lambda _i: self.find.attach(self.current_editor()))

        for dizi, islev in ((QKeySequence.StandardKey.Find, self.show_find),
                            (QKeySequence("F3"), self.find.find_next),
                            (QKeySequence("Shift+F3"), self.find.find_prev)):
            kisayol = QShortcut(dizi, self)
            kisayol.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            kisayol.activated.connect(islev)

    # ------------------------------------------------------------------- API #

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_header()

    def _fit_header(self) -> None:
        """Panelin asgari genisligini baslik seridine gore ayarlar.

        Yalnizca ZORUNLU ogeler sayilir: dil secimi ve iki dugme. Baslik
        ("GENERATED CODE") ve durum ("3 files · 929 lines") BILGIdir ve
        kisalabilir; onlari da asgariye katmak paneli 777 px'e zorluyor,
        tuvale yer birakmiyordu.

        Sabit bir sayi yazilamaz: yazi tipi buyudugunde (arayuz olcekleme,
        farkli DPI) serit de buyur ve panel yine kirpardi.
        """
        kenar = 10 + 10 + 8 * 4          # kenar bosluklari + ogeler arasi
        gerekli = kenar + self.language.minimumWidth()
        for dugme in self._header_buttons:
            gerekli += dugme.sizeHint().width()
        if gerekli != self.minimumWidth():
            self.setMinimumWidth(gerekli)

    def retheme(self) -> None:
        """Tema degisiminde satir-ici stilleri ve editorleri yeniden kurar."""
        self._apply_static_styles()
        for editor in self._editors.values():
            fn = getattr(editor, "retheme", None)
            if callable(fn):
                fn()
        self.find.retheme()

    def _apply_static_styles(self) -> None:
        """Kurulumda okunan renkleri yeniden uygular (bkz. retheme)."""
        self._bar.setStyleSheet(
            "#codeBar { background: %s; border-bottom: 1px solid %s; }"
            % (C.PANEL_DARK, C.BORDER))
        self._title.setStyleSheet("color: %s;" % C.TEXT_DIM)

    def current_editor(self):
        """Etkin sekmedeki editor; sekme yoksa None."""
        w = self.tabs.currentWidget()
        return w if isinstance(w, CodeEditor) else None

    def show_find(self) -> None:
        """Ctrl+F: arama cubugunu acar ve etkin editore baglar."""
        self.find.show_bar(self.current_editor())

    def current_language(self) -> str:
        return self.language.currentData()

    def set_language(self, key: str) -> None:
        for i in range(self.language.count()):
            if self.language.itemData(i) == key:
                self.language.setCurrentIndex(i)
                return

    def current_file(self):
        idx = self.tabs.currentIndex()
        if idx < 0:
            return None, ""
        name = self.tabs.tabText(idx)
        editor = self.tabs.widget(idx)
        return name, editor.toPlainText() if isinstance(editor, CodeEditor) else ""

    def set_files(self, files: Dict[str, str]) -> None:
        """Sekmeleri gunceller; etkin sekme mumkunse korunur."""
        current = self.tabs.tabText(self.tabs.currentIndex()) \
            if self.tabs.currentIndex() >= 0 else ""
        names: List[str] = list(files.keys())

        # Fazla sekmeleri kaldir
        for i in reversed(range(self.tabs.count())):
            if self.tabs.tabText(i) not in names:
                widget = self.tabs.widget(i)
                self.tabs.removeTab(i)
                self._editors.pop(widget_name(widget), None)
                self._shown.pop(widget_name(widget), None)
                widget.deleteLater()

        for pos, name in enumerate(names):
            editor = self._editors.get(name)
            if editor is None:
                editor = CodeEditor()
                editor.setObjectName(name)
                self._editors[name] = editor
                self.tabs.insertTab(pos, editor, name)
            # Uretim damgasi her seferinde degistigi icin icerigi damga haric
            # karsilastiriyoruz; degismemisse yeniden renklendirmeye gerek yok.
            key = _without_stamp(files[name])
            if self._shown.get(name) != key:
                self._shown[name] = key
                editor.set_code(files[name])

        if current in names:
            self.tabs.setCurrentIndex(names.index(current))
        elif self.tabs.count():
            self.tabs.setCurrentIndex(0)

    def show_ok(self, note: str) -> None:
        self.banner.hide()
        self.status.setText(note)
        self.status.setStyleSheet("color: %s;" % C.GREEN)

    def show_blocked(self, message: str) -> None:
        # Zemin renkleri TEMADAN gelir. Eskiden koyu tema degerleri sabitti
        # ve acik temada koyu kahverengi bant uzerinde koyu kirmizi yaziyla
        # okunmuyordu.
        self.banner.setText("⛔  %s" % message)
        self.banner.setStyleSheet(
            "background: %s; color: %s; border-bottom: 1px solid %s;"
            % (C.BANNER_ERROR_BG, C.RED, C.BORDER))
        self.banner.show()
        self.status.setText("code not generated")
        self.status.setStyleSheet("color: %s;" % C.RED)

    def show_warning(self, message: str) -> None:
        self.banner.setText("⚠  %s" % message)
        self.banner.setStyleSheet(
            "background: %s; color: %s; border-bottom: 1px solid %s;"
            % (C.BANNER_WARN_BG, C.WARN, C.BORDER))
        self.banner.show()

    def set_stale(self, stale: bool) -> None:
        """Gosterilen kodun MODELDEN ESKI oldugunu bildirir.

        Kod artik her degisiklikte kendiliginden uretilmedigi icin panelin
        ekrandaki kaynagin guncel OLMADIGINI acikca soylemesi gerekir;
        aksi halde kullanici bayat kodu dogru sanip disa aktarabilirdi.
        """
        if not stale:
            if self.banner.text().startswith("↻"):
                self.banner.hide()
            return
        self.banner.setText(
            "↻  The model has changed since this code was generated — "
            "press Build (F5).")
        self.banner.setStyleSheet(
            "background: %s; color: %s; border-bottom: 1px solid %s;"
            % (C.BANNER_WARN_BG, C.WARN, C.BORDER))
        self.banner.show()
        self.status.setText("out of date")
        self.status.setStyleSheet("color: %s;" % C.WARN)

    def set_placeholder(self, text: str) -> None:
        self.tabs.clear()
        self._editors.clear()
        self._shown.clear()
        editor = CodeEditor()
        editor.setFont(mono_font(10))
        editor.set_code(text)
        self.tabs.addTab(editor, "Info")


_STAMP_PREFIXES = (" * Tarih", "// Tarih")


def _without_stamp(text: str) -> str:
    """Uretim tarihi satirini atarak icerigi karsilastirilabilir hale getirir."""
    return "\n".join(ln for ln in text.splitlines()
                     if not ln.startswith(_STAMP_PREFIXES))


def widget_name(widget) -> str:
    return widget.objectName() if widget is not None else ""
