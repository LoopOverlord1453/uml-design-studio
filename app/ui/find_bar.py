"""Uretilen kod panelinde SALT ARAMA cubugu (Ctrl+F).

DEGISTIRME YOKTUR ve olmayacaktir. Uretilen dosyalar modelden turetilir;
panelde elle yapilan bir degisiklik bir sonraki uretimde sessizce kaybolur.
Bu yuzden arayuz "bul ve degistir" degil, YALNIZCA "bul" sunar -- kullanicinin
kaybedecegi bir duzenleme hic olusmaz. Editorler zaten ``setReadOnly(True)``
ile korunur; bu cubuk o sozlesmeyi bozmaz, sadece imleci tasir.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QTextCursor, QTextDocument
from PyQt6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLabel,
                             QLineEdit, QPlainTextEdit, QTextEdit,
                             QToolButton)

from .theme import C, ui_font


class FindBar(QFrame):
    """Ince arama cubugu: alan, esleme sayaci, ileri/geri, secenekler."""

    closed = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("findBar")

        self._editor: Optional[QPlainTextEdit] = None

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 5, 8, 5)
        row.setSpacing(7)

        etiket = QLabel("Find")
        etiket.setFont(ui_font(8))
        self._etiket = etiket
        row.addWidget(etiket)

        self.field = QLineEdit()
        self.field.setPlaceholderText("Search in generated code…")
        # Yer tutucu metin ETIKET DEGILDIR (WCAG 3.3.2); odak
        # gelince kaybolur. Erisilebilir ad ayrica verilir.
        self.field.setAccessibleName("Search in generated code")
        self.field.setFont(ui_font(9))
        self.field.setClearButtonEnabled(True)
        self.field.setMinimumWidth(220)
        self.field.textChanged.connect(self._on_text)
        self.field.returnPressed.connect(self.find_next)
        row.addWidget(self.field, 1)

        self.count = QLabel("")
        self.count.setFont(ui_font(8))
        self.count.setMinimumWidth(84)
        row.addWidget(self.count)

        self.case = QCheckBox("Aa")
        self.case.setToolTip("Match case")
        self.case.setFont(ui_font(8))
        self.case.stateChanged.connect(lambda _s: self._on_text(self.field.text()))
        row.addWidget(self.case)

        self.whole = QCheckBox("W")
        self.whole.setToolTip("Whole words only")
        self.whole.setFont(ui_font(8))
        self.whole.stateChanged.connect(lambda _s: self._on_text(self.field.text()))
        row.addWidget(self.whole)

        prev_btn = QToolButton()
        prev_btn.setText("▲")
        prev_btn.setToolTip("Previous match (Shift+F3)")
        prev_btn.clicked.connect(self.find_prev)
        row.addWidget(prev_btn)

        next_btn = QToolButton()
        next_btn.setText("▼")
        next_btn.setToolTip("Next match (F3)")
        next_btn.clicked.connect(self.find_next)
        row.addWidget(next_btn)

        close_btn = QToolButton()
        close_btn.setText("✕")
        close_btn.setToolTip("Close (Esc)")
        close_btn.clicked.connect(self.hide_bar)
        row.addWidget(close_btn)

        self.retheme()
        self.hide()

    def retheme(self) -> None:
        """Tema degisiminde satir-ici renkleri yeniden uygular."""
        self.setStyleSheet(
            "#findBar { background: %s; border-top: 1px solid %s; }"
            % (C.PANEL_DARK, C.BORDER))
        self._etiket.setStyleSheet("color: %s;" % C.TEXT_DIM)
        self.count.setStyleSheet("color: %s;" % C.TEXT_DIM)
        self._refresh()

    # ------------------------------------------------------------------ API #

    def attach(self, editor: Optional[QPlainTextEdit]) -> None:
        """Aramanin uzerinde calisacagi editoru degistirir (sekme degisimi)."""
        self._editor = editor
        if self.isVisible():
            self._refresh()

    def show_bar(self, editor: Optional[QPlainTextEdit] = None) -> None:
        if editor is not None:
            self._editor = editor
        # Imlecte secili metin varsa arama alanina tasinir -- editorlerde
        # beklenen davranis budur.
        if self._editor is not None:
            secili = self._editor.textCursor().selectedText()
            if secili and " " not in secili:
                self.field.setText(secili)
        self.show()
        self.field.setFocus()
        self.field.selectAll()
        self._refresh()

    def hide_bar(self) -> None:
        self._clear_highlights()
        self.hide()
        if self._editor is not None:
            self._editor.setFocus()
        self.closed.emit()

    # -------------------------------------------------------------- arama -- #

    def _flags(self, geri: bool = False) -> QTextDocument.FindFlag:
        flags = QTextDocument.FindFlag(0)
        if self.case.isChecked():
            flags |= QTextDocument.FindFlag.FindCaseSensitively
        if self.whole.isChecked():
            flags |= QTextDocument.FindFlag.FindWholeWords
        if geri:
            flags |= QTextDocument.FindFlag.FindBackward
        return flags

    def _on_text(self, _text: str) -> None:
        self._refresh()
        self._seek(geri=False, baslangictan=True)

    def find_next(self) -> None:
        self._seek(geri=False)

    def find_prev(self) -> None:
        self._seek(geri=True)

    def _seek(self, geri: bool, baslangictan: bool = False) -> None:
        """Sonraki/onceki eslemeye gider; sona gelince BASA SARAR."""
        if self._editor is None:
            return
        desen = self.field.text()
        if not desen:
            return

        if baslangictan:
            imlec = self._editor.textCursor()
            imlec.setPosition(imlec.selectionStart())
            self._editor.setTextCursor(imlec)

        if self._editor.find(desen, self._flags(geri)):
            return

        # Sarma: belgenin basina (geri aramada sonuna) don ve bir kez daha dene.
        imlec = self._editor.textCursor()
        imlec.movePosition(QTextCursor.MoveOperation.End if geri
                           else QTextCursor.MoveOperation.Start)
        self._editor.setTextCursor(imlec)
        self._editor.find(desen, self._flags(geri))

    # ------------------------------------------------------------ vurgular -- #

    @staticmethod
    def _push(editor, selections) -> None:
        """Vurgulari editorun ARAMA katmanina yazar.

        Dogrudan setExtraSelections cagirmak, etkin satir vurgusuyla ayni
        listeyi paylastigi icin ilk imlec hareketinde silinirdi
        (bkz. CodeEditor.set_search_highlights).
        """
        if hasattr(editor, "set_search_highlights"):
            editor.set_search_highlights(selections)
        else:
            editor.setExtraSelections(selections)

    def _clear_highlights(self) -> None:
        if self._editor is not None:
            self._push(self._editor, [])

    def _refresh(self) -> None:
        """Butun eslemeleri vurgular ve sayaci gunceller."""
        if self._editor is None:
            self.count.setText("")
            return

        desen = self.field.text()
        if not desen:
            self.count.setText("")
            self._clear_highlights()
            self.field.setStyleSheet("")
            return

        secimler = []
        belge = self._editor.document()
        imlec = QTextCursor(belge)
        renk = QColor(C.ACCENT)
        renk.setAlpha(70)

        while True:
            imlec = belge.find(desen, imlec, self._flags())
            if imlec.isNull():
                break
            # ExtraSelection QTextEdit'in ic sinifidir; QPlainTextEdit'te
            # YOKTUR. Yanlis ad PyQt6'da yakalanabilir bir istisna degil,
            # dogrudan surec olumu uretiyordu.
            sel = QTextEdit.ExtraSelection()
            sel.cursor = imlec
            sel.format.setBackground(renk)
            secimler.append(sel)

        self._push(self._editor, secimler)
        n = len(secimler)
        self.count.setText("%d match%s" % (n, "" if n == 1 else "es"))
        # Bulunamayan desen alani kirmiziya boyar -- yazarken anlik geri bildirim.
        self.field.setStyleSheet(
            "" if n else "color: %s;" % C.RED)

    # ------------------------------------------------------------ klavye --- #

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide_bar()
            return
        super().keyPressEvent(event)
