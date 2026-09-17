"""A code viewer with line numbers, styled like JetBrains Darcula."""

from __future__ import annotations

from PyQt6.QtCore import QRect, QSize, Qt
from PyQt6.QtGui import (QColor, QPainter, QPalette, QTextCursor, QTextFormat,
                         QTextOption)
from PyQt6.QtWidgets import QPlainTextEdit, QTextEdit, QWidget

from .highlighter import CppHighlighter
from .theme import C, mono_font


class _LineNumberArea(QWidget):
    def __init__(self, editor: "CodeEditor") -> None:
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self.editor.line_number_width(), 0)

    def paintEvent(self, event) -> None:
        self.editor.paint_line_numbers(event)


class CodeEditor(QPlainTextEdit):
    """A read-only code panel (generated code must not be edited by hand)."""

    def __init__(self, parent=None, read_only: bool = True) -> None:
        super().__init__(parent)
        #: Ctrl+F match highlights (see set_search_highlights).
        self._search_selections = []
        self.setReadOnly(read_only)
        self.setFont(mono_font(10))
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabStopDistance(4 * self.fontMetrics().horizontalAdvance(" "))
        self.setWordWrapMode(QTextOption.WrapMode.NoWrap)
        self.setFrameShape(QPlainTextEdit.Shape.NoFrame)
        self.setCursorWidth(2)

        self._apply_palette()

        self.gutter = _LineNumberArea(self)
        self.highlighter = CppHighlighter(self.document())

        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter)
        self.cursorPositionChanged.connect(self._highlight_current_line)

        self._update_gutter_width(0)
        self._highlight_current_line()

    # -------------------------------------------------------------------- gutter

    def line_number_width(self) -> int:
        digits = max(3, len(str(max(1, self.blockCount()))))
        return 14 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_gutter_width(self, _count: int) -> None:
        self.setViewportMargins(self.line_number_width(), 0, 0, 0)

    def _update_gutter(self, rect: QRect, dy: int) -> None:
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width(0)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.gutter.setGeometry(QRect(cr.left(), cr.top(),
                                      self.line_number_width(), cr.height()))

    def paint_line_numbers(self, event) -> None:
        painter = QPainter(self.gutter)
        painter.fillRect(event.rect(), QColor(C.CODE_GUTTER_BG))
        painter.setPen(QColor(C.BORDER))
        painter.drawLine(event.rect().topRight(), event.rect().bottomRight())

        block = self.firstVisibleBlock()
        number = block.blockNumber()
        top = round(self.blockBoundingGeometry(block)
                    .translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        current = self.textCursor().blockNumber()

        painter.setFont(self.font())
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.setPen(QColor(C.CODE_GUTTER_CUR if number == current
                                      else C.CODE_GUTTER_TEXT))
                painter.drawText(0, top, self.gutter.width() - 8,
                                 self.fontMetrics().height(),
                                 int(Qt.AlignmentFlag.AlignRight |
                                     Qt.AlignmentFlag.AlignVCenter),
                                 str(number + 1))
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            number += 1

    # ---------------------------------------------------------- current line

    def _apply_palette(self) -> None:
        """Sets the code area up in the colours of the ACTIVE THEME.

        The panel used to use the classic Visual Studio scheme and stayed white
        regardless of the theme; in the dark theme half the screen glared white.
        Now it changes together with the theme.

        Because the style sheet gives QPlainTextEdit a global background, it
        is overridden with setStyleSheet here too, or the palette stays hidden.
        """
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Base, QColor(C.CODE_BG))
        pal.setColor(QPalette.ColorRole.Text, QColor(C.CODE_TEXT))
        pal.setColor(QPalette.ColorRole.Highlight, QColor(C.SELECTION))
        pal.setColor(QPalette.ColorRole.HighlightedText,
                     QColor(C.SELECTION_TEXT))
        self.setPalette(pal)
        self.setStyleSheet(
            "QPlainTextEdit { background: %s; color: %s; border: none;"
            " selection-background-color: %s; selection-color: %s; }"
            % (C.CODE_BG, C.CODE_TEXT, C.SELECTION, C.SELECTION_TEXT))

    def retheme(self) -> None:
        """Rebuilds the palette and the syntax colours on a theme change.

        The highlighter compiles its rules AT SET-UP time; changing only the
        palette would leave the code text in the colours of the old theme.
        """
        self._apply_palette()
        self.highlighter = CppHighlighter(self.document())
        self.highlighter.rehighlight()
        self._apply_selections()

    def set_search_highlights(self, selections) -> None:
        """Stores and applies the match highlights of the search bar (Ctrl+F).

        THEY MUST BE KEPT SEPARATE: `setExtraSelections` replaces the WHOLE
        list, and the current-line highlight is rewritten on every cursor move.
        Search highlights placed directly with `setExtraSelections` would be
        wiped on the first cursor move -- that is, by the search's own "go to
        next" step; the counter said "5 matches" while one highlight remained.
        """
        self._search_selections = list(selections)
        self._apply_selections()

    def _highlight_current_line(self) -> None:
        self._apply_selections()

    def _apply_selections(self) -> None:
        """Merges the current-line and search highlights into ONE list."""
        sel = QTextEdit.ExtraSelection()
        sel.format.setBackground(QColor(C.CODE_CURRENT_LINE))
        sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        cursor = self.textCursor()
        cursor.clearSelection()
        sel.cursor = cursor

        # The search highlights come LAST so they paint over the current line.
        self.setExtraSelections([sel] + list(self._search_selections))

    # ------------------------------------------------------------------ API

    def set_code(self, text: str) -> None:
        """Replaces the content while keeping the scroll position."""
        vbar = self.verticalScrollBar()
        hbar = self.horizontalScrollBar()
        v, h = vbar.value(), hbar.value()
        line = self.textCursor().blockNumber()

        self.setPlainText(text)

        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        cursor.movePosition(QTextCursor.MoveOperation.Down,
                            QTextCursor.MoveMode.MoveAnchor,
                            min(line, max(0, self.blockCount() - 1)))
        self.setTextCursor(cursor)
        vbar.setValue(min(v, vbar.maximum()))
        hbar.setValue(min(h, hbar.maximum()))

    def goto_line(self, line: int) -> None:
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        cursor.movePosition(QTextCursor.MoveOperation.Down,
                            QTextCursor.MoveMode.MoveAnchor, max(0, line - 1))
        self.setTextCursor(cursor)
        self.centerCursor()
