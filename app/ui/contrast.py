"""A paint delegate that keeps the selected row readable in every theme.

Tree and list items are painted in colours that carry MEANING: warning
yellow, error red, transition grey, pseudostate pale... Those colours are
pinned on the item with ``setForeground`` and Qt keeps using them WHILE
SELECTED as well. The result: dark grey text on a dark blue selection band --
an invisible row, and no way to tell which item is selected.

``ContrastDelegate`` replaces the foreground colour with the CONTRAST colour
of the theme while the row is selected (or hovered) and makes the text BOLD.
So the selected item stands out at a glance in both themes.
"""

from __future__ import annotations

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QStyle, QStyledItemDelegate

from .theme import C


class ContrastDelegate(QStyledItemDelegate):
    """Paints a selected / hovered row bold and in a colour that contrasts."""

    def initStyleOption(self, option, index) -> None:
        super().initStyleOption(option, index)
        state = option.state
        selected = bool(state & QStyle.StateFlag.State_Selected)
        hovered = bool(state & QStyle.StateFlag.State_MouseOver)
        if not (selected or hovered):
            return

        colour = QColor(C.SELECTED_TEXT if selected else C.TEXT_BRIGHT)
        for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive,
                      QPalette.ColorGroup.Normal):
            option.palette.setColor(group, QPalette.ColorRole.HighlightedText,
                                    colour)
            option.palette.setColor(group, QPalette.ColorRole.Text, colour)
        if selected:
            font = option.font
            font.setBold(True)
            option.font = font


def apply_contrast(*views) -> None:
    """Attaches the delegate to the given views (no shared instance).

    Every view keeps ITS OWN delegate: Qt does not take ownership of a
    delegate, and a shared instance could leave dangling pointers in the other
    views once one of them is destroyed.
    """
    for view in views:
        if view is None:
            continue
        delegate = ContrastDelegate(view)
        view.setItemDelegate(delegate)
        # Keep the reference on the widget: left in a local variable only, it is
        # collected on the Python side and the rows fall back to default painting.
        view._contrast_delegate = delegate
