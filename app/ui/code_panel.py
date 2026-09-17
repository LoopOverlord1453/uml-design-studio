"""The right panel: generated source files, language choice and export."""

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
    """A read-only tabbed panel that shows the generated code."""

    language_changed = pyqtSignal(str)
    export_requested = pyqtSignal()
    copy_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._editors: Dict[str, CodeEditor] = {}
        self._shown: Dict[str, str] = {}      # the content last shown (normalised)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # -------------------------------------------------------------- header
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
        # NOT A FIXED WIDTH: a fixed 140 px forced the minimum width of the
        # header strip onto the whole panel (555 px). Because the panel could
        # not go below that bound, the "hide when narrow" logic never kicked in
        # and the texts were clipped instead.
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

        # THE HEADER STRIP IS NEVER CLIPPED.
        #
        # The strip is made of fixed-width items; as the panel narrowed Qt
        # squeezed them all and the texts were clipped ("GENERA' C (C99)
        # files |opy| por"). A clipped button cannot be used.
        #
        # "Hide when narrow" was tried and DID NOT WORK: because the panel could
        # not already go below the minimum width of the strip, the threshold was
        # never reached -- the decision to hide depended on the width that hiding
        # would have MADE POSSIBLE. Instead, the width the strip really needs
        # becomes the MINIMUM of the panel. If the user wants more room they can
        # hide the panel entirely with F9; a half-visible panel helps nobody.
        self._optional = []
        #: The items in the header strip that CANNOT SHRINK.
        self._header_buttons = [copy_btn, export_btn]

        bar.setMinimumWidth(0)
        layout.addWidget(bar)

        # ------------------------------------------------------------ warning
        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setFont(ui_font(9))
        self.banner.setContentsMargins(12, 8, 12, 8)
        self.banner.hide()
        layout.addWidget(self.banner)

        # ---------------------------------------------------------------- tabs
        self._apply_static_styles()
        self._fit_header()

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabPosition(QTabWidget.TabPosition.North)
        layout.addWidget(self.tabs, 1)

        # ------------------------------------------------------ search (Ctrl+F)
        # SEARCH ONLY: because the generated files are derived from the model, no
        # editing is allowed in the panel (see find_bar.py). The bar only moves
        # the cursor and highlights the matches.
        self.find = FindBar(self)
        layout.addWidget(self.find)
        self.tabs.currentChanged.connect(
            lambda _i: self.find.attach(self.current_editor()))

        for array, islev in ((QKeySequence.StandardKey.Find, self.show_find),
                            (QKeySequence("F3"), self.find.find_next),
                            (QKeySequence("Shift+F3"), self.find.find_prev)):
            kisayol = QShortcut(array, self)
            kisayol.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            kisayol.activated.connect(islev)

    # ------------------------------------------------------------------- API #

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_header()

    def _fit_header(self) -> None:
        """Sets the minimum width of the panel from the header strip.

        Only the MANDATORY items count: the language choice and the two
        buttons. The title ("GENERATED CODE") and the status ("3 files · 929
        lines") are INFORMATION and may shrink; counting them into the minimum
        forced the panel to 777 px and left no room for the canvas.

        A fixed number cannot be written: when the font grows (interface
        scaling, a different DPI) the strip grows and the panel clips again.
        """
        kenar = 10 + 10 + 8 * 4          # margins + spacing between items
        needed = kenar + self.language.minimumWidth()
        for button in self._header_buttons:
            needed += button.sizeHint().width()
        if needed != self.minimumWidth():
            self.setMinimumWidth(needed)

    def retheme(self) -> None:
        """Rebuilds the inline styles and the editors on a theme change."""
        self._apply_static_styles()
        for editor in self._editors.values():
            fn = getattr(editor, "retheme", None)
            if callable(fn):
                fn()
        self.find.retheme()

    def _apply_static_styles(self) -> None:
        """Reapplies the colours read at set-up (see retheme)."""
        self._bar.setStyleSheet(
            "#codeBar { background: %s; border-bottom: 1px solid %s; }"
            % (C.PANEL_DARK, C.BORDER))
        self._title.setStyleSheet("color: %s;" % C.TEXT_DIM)

    def current_editor(self):
        """The editor in the active tab; None when there is no tab."""
        w = self.tabs.currentWidget()
        return w if isinstance(w, CodeEditor) else None

    def show_find(self) -> None:
        """Ctrl+F: opens the search bar and binds it to the active editor."""
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
        """Refreshes the tabs; the active tab is kept when possible."""
        current = self.tabs.tabText(self.tabs.currentIndex()) \
            if self.tabs.currentIndex() >= 0 else ""
        names: List[str] = list(files.keys())

        # Remove the extra tabs
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
            # Because the generation stamp changes every time, the content is
            # compared without it; unchanged content needs no re-highlighting.
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
        # The background colours come FROM THE THEME. The dark theme values used
        # to be hard-coded, and in the light theme dark red text on a dark brown
        # band was unreadable.
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
        """Reports that the code shown is OLDER THAN THE MODEL.

        Because the code is no longer generated on every change, the panel has
        to say plainly that the source on screen is NOT up to date; otherwise
        the user could export stale code believing it correct.
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
    """Makes the content comparable by dropping the generation-date line."""
    return "\n".join(ln for ln in text.splitlines()
                     if not ln.startswith(_STAMP_PREFIXES))


def widget_name(widget) -> str:
    return widget.objectName() if widget is not None else ""
