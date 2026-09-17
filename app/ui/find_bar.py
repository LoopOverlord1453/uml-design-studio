"""A SEARCH-ONLY bar for the generated-code panel (Ctrl+F).

THERE IS NO REPLACE, and there will not be. The generated files are derived
from the model; an edit made by hand in the panel is silently lost on the next
generation. So the interface offers only "find" -- the user never builds up an
edit they could lose. The editors are already protected with
``setReadOnly(True)``; this bar does not break that, it only moves the cursor.
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
    """A slim search bar: field, match counter, next/previous, options."""

    closed = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("findBar")

        self._editor: Optional[QPlainTextEdit] = None

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 5, 8, 5)
        row.setSpacing(7)

        label = QLabel("Find")
        label.setFont(ui_font(8))
        self._label = label
        row.addWidget(label)

        self.field = QLineEdit()
        self.field.setPlaceholderText("Search in generated code…")
        # Placeholder text IS NOT A LABEL (WCAG 3.3.2); it disappears when the
        # field takes focus. The accessible name is given separately.
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
        """Reapplies the inline colours on a theme change."""
        self.setStyleSheet(
            "#findBar { background: %s; border-top: 1px solid %s; }"
            % (C.PANEL_DARK, C.BORDER))
        self._label.setStyleSheet("color: %s;" % C.TEXT_DIM)
        self.count.setStyleSheet("color: %s;" % C.TEXT_DIM)
        self._refresh()

    # ------------------------------------------------------------------ API #

    def attach(self, editor: Optional[QPlainTextEdit]) -> None:
        """Changes the editor the search runs on (tab change)."""
        self._editor = editor
        if self.isVisible():
            self._refresh()

    def show_bar(self, editor: Optional[QPlainTextEdit] = None) -> None:
        if editor is not None:
            self._editor = editor
        # Text selected at the cursor is carried into the search field -- that is
        # the behaviour expected in editors.
        if self._editor is not None:
            selected = self._editor.textCursor().selectedText()
            if selected and " " not in selected:
                self.field.setText(selected)
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

    # ------------------------------------------------------------- search -- #

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
        """Goes to the next/previous match; WRAPS AROUND at the end."""
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

        # Wrap: go back to the start (the end when searching backwards) and retry.
        imlec = self._editor.textCursor()
        imlec.movePosition(QTextCursor.MoveOperation.End if geri
                           else QTextCursor.MoveOperation.Start)
        self._editor.setTextCursor(imlec)
        self._editor.find(desen, self._flags(geri))

    # ---------------------------------------------------------- highlights -- #

    @staticmethod
    def _push(editor, selections) -> None:
        """Writes the highlights into the SEARCH layer of the editor.

        Calling setExtraSelections directly would share one list with the
        current-line highlight and the highlights would be wiped on the first
        cursor move (see CodeEditor.set_search_highlights).
        """
        if hasattr(editor, "set_search_highlights"):
            editor.set_search_highlights(selections)
        else:
            editor.setExtraSelections(selections)

    def _clear_highlights(self) -> None:
        if self._editor is not None:
            self._push(self._editor, [])

    def _refresh(self) -> None:
        """Highlights every match and updates the counter."""
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
        color = QColor(C.ACCENT)
        color.setAlpha(70)

        while True:
            imlec = belge.find(desen, imlec, self._flags())
            if imlec.isNull():
                break
            # ExtraSelection is an inner class of QTextEdit; it DOES NOT EXIST on
            # QPlainTextEdit. In PyQt6 the wrong name was not a catchable exception
            # but killed the process outright.
            sel = QTextEdit.ExtraSelection()
            sel.cursor = imlec
            sel.format.setBackground(color)
            secimler.append(sel)

        self._push(self._editor, secimler)
        n = len(secimler)
        self.count.setText("%d match%s" % (n, "" if n == 1 else "es"))
        # An unmatched pattern paints the field red -- instant feedback while typing.
        self.field.setStyleSheet(
            "" if n else "color: %s;" % C.RED)

    # ---------------------------------------------------------- keyboard --- #

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide_bar()
            return
        super().keyPressEvent(event)
