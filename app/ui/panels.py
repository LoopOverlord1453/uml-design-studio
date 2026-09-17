"""Shared symbol tables for the problem list panel and the tree view."""

from __future__ import annotations

from typing import List

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (QAbstractItemView, QHeaderView, QTreeWidget,
                             QTreeWidgetItem)

from ..core.class_model import RelationKind, Stereotype
from ..core.model import StateKind
from ..core import uml_spec
from ..core.validator import Issue
from .contrast import apply_contrast
from .theme import C, mono_font, ui_font

ID_ROLE = Qt.ItemDataRole.UserRole + 1

KIND_GLYPH = {
    StateKind.SIMPLE: "▢",
    StateKind.COMPOSITE: "▣",
    StateKind.INITIAL: "●",
    StateKind.FINAL: "◉",
    StateKind.CHOICE: "◇",
    StateKind.JUNCTION: "◆",
    StateKind.SHALLOW_HISTORY: "Ⓗ",
    StateKind.DEEP_HISTORY: "Ⓗ*",
    StateKind.TERMINATE: "✕",
    # In UML a fork/join is A THICK BAR and the connection points are small
    # circles on the border of a composite state; the tree names them so too.
    StateKind.FORK: "▬",
    StateKind.JOIN: "▬",
    StateKind.ENTRY_POINT: "○",
    StateKind.EXIT_POINT: "⊗",
    StateKind.SUBMACHINE: "▤",
}

#: The tool name written NEXT TO the symbol (UML terms).
#:
#: The symbol alone was not enough: at 12 pixels "◇" and "◆" (choice /
#: junction) and "▢" and "▣" (simple / composite) could not be told
#: apart. With the tool name written out, the tree now speaks the same
#: language as the toolbar on the canvas.
KIND_LABEL = {
    StateKind.SIMPLE: "State",
    StateKind.COMPOSITE: "Composite",
    StateKind.INITIAL: "Initial",
    StateKind.FINAL: "Final",
    StateKind.CHOICE: "Choice",
    StateKind.JUNCTION: "Junction",
    StateKind.SHALLOW_HISTORY: "History",
    StateKind.DEEP_HISTORY: "Deep History",
    StateKind.TERMINATE: "Terminate",
    StateKind.FORK: "Fork",
    StateKind.JOIN: "Join",
    StateKind.ENTRY_POINT: "Entry Point",
    StateKind.EXIT_POINT: "Exit Point",
    StateKind.SUBMACHINE: "Submachine",
}

# EVERY StateKind MUST HAVE AN ENTRY.
#
# Tree nodes are built with `KIND_GLYPH[st.kind]`, and a missing kind
# turned into a KeyError there. When fork, join, entry/exit point and
# submachine were added, these two tables were not updated: a model
# containing any of those could not even be VIEWED in the workspace.
# Blowing up here is right, rather than falling back to a silent default
# -- but this check comes first so a test can catch it (test_regressions 41).
assert set(KIND_GLYPH) == set(StateKind), \
    "KIND_GLYPH eksik: %s" % sorted(k.value for k in StateKind
                                    if k not in KIND_GLYPH)
assert set(KIND_LABEL) == set(StateKind), \
    "KIND_LABEL eksik: %s" % sorted(k.value for k in StateKind
                                    if k not in KIND_LABEL)

SEVERITY_GLYPH = {"error": "✖", "warning": "▲", "info": "ℹ"}
SEVERITY_COLOR = {"error": C.RED, "warning": C.WARN, "info": C.INFO}
SEVERITY_TEXT = {"error": "Error", "warning": "Warning", "info": "Info"}


# OutlinePanel / ClassOutlinePanel WERE REMOVED.
#
# A separate "MODEL TREE" panel showed, a second time, information the
# workspace tree already displayed. The symbol and label tables below
# STAY: workspace_tree.py draws both state machine and class diagram
# nodes with these tables.


#: The UML symbols and tool names used in the class diagram tree.
CLASS_GLYPH = {
    Stereotype.CLASS: "▭",
    Stereotype.ABSTRACT: "▱",
    Stereotype.INTERFACE: "◍",
}

CLASS_LABEL = {
    Stereotype.CLASS: "Class",
    Stereotype.ABSTRACT: "Abstract",
    Stereotype.INTERFACE: "Interface",
}

RELATION_GLYPH = {
    RelationKind.ASSOCIATION: "──",
    RelationKind.AGGREGATION: "◇─",
    RelationKind.COMPOSITION: "◆─",
    RelationKind.GENERALIZATION: "─▷",
    RelationKind.REALIZATION: "┈▷",
    RelationKind.DEPENDENCY: "┈>",
}

RELATION_LABEL = {
    RelationKind.ASSOCIATION: "Association",
    RelationKind.AGGREGATION: "Aggregation",
    RelationKind.COMPOSITION: "Composition",
    RelationKind.GENERALIZATION: "Generalization",
    RelationKind.REALIZATION: "Realization",
    RelationKind.DEPENDENCY: "Dependency",
}


class ProblemsPanel(QTreeWidget):
    """Validation results; double-clicking selects the matching element."""

    element_activated = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setColumnCount(4)
        # SPEC column: the clause of the violated rule in OMG UML 2.5.1 and the
        # PRINTED page number. The tooltip carries a one-sentence summary.
        self.setHeaderLabels(["Severity", "Code", "Description", "UML 2.5.1"])
        self.setRootIsDecorated(False)
        self.setFont(ui_font(9))
        self.setAlternatingRowColors(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.setColumnWidth(0, 78)
        self.setColumnWidth(1, 54)
        self.setColumnWidth(3, 250)
        self.itemDoubleClicked.connect(self._activate)
        self.itemSelectionChanged.connect(self._on_select)
        apply_contrast(self)

    def show_issues(self, issues: List[Issue]) -> None:
        self.clear()
        if not issues:
            item = QTreeWidgetItem(self, ["✔", "", "No problems - the model is ready for code generation.", ""])
            item.setForeground(0, QBrush(QColor(C.GREEN)))
            item.setForeground(2, QBrush(QColor(C.GREEN)))
            return
        for issue in issues:
            item = QTreeWidgetItem(self, [
                "%s %s" % (SEVERITY_GLYPH.get(issue.severity, "•"),
                           SEVERITY_TEXT.get(issue.severity, issue.severity)),
                issue.code,
                issue.message,
                "",
            ])
            self._attach_spec(item, issue.code)
            color = QColor(SEVERITY_COLOR.get(issue.severity, C.TEXT))
            item.setForeground(0, QBrush(color))
            item.setForeground(1, QBrush(QColor(C.TEXT_DIM)))
            item.setFont(1, mono_font(8))
            # THE MESSAGE ITSELF IS COLOURED TOO.
            #
            # Only the "Severity" column used to be coloured; the text that actually
            # gets read (the description) was plain and did not stand out in the list.
            # An error is RED, a warning AMBER: the two problems are separated from
            # each other, and both from plain text.
            if issue.severity in ("error", "warning"):
                item.setForeground(2, QBrush(color))
            if issue.severity == "error":
                f = self.font()
                f.setBold(True)
                item.setFont(2, f)
            item.setData(0, ID_ROLE, issue.element_id or "")

    def _attach_spec(self, item: QTreeWidgetItem, code: str) -> None:
        """Marks the finding with its specification reference.

        The column carries the SHORT reference (clause + page); the full
        sentence of the rule is in the tooltip. Tool rules (name collision,
        invalid identifier) have NO UML counterpart -- rather than inventing a
        page, "tool rule" is written, because the user would look that page up.
        """
        ref = uml_spec.lookup(code)
        if ref is None:
            return

        if ref.is_tool_rule:
            item.setText(3, "tool rule")
            item.setForeground(3, QBrush(QColor(C.TEXT_DIM)))
        else:
            page_no = ref.page
            label = "§%s" % ref.section
            if page_no is not None:
                label += "  ·  p. %d" % page_no
            item.setText(3, label)
            item.setForeground(3, QBrush(QColor(C.ACCENT)))
        item.setFont(3, mono_font(8))

        tooltip = "%s\n\n%s" % (ref.rule, ref.citation())
        if not ref.is_tool_rule:
            tooltip += "\n%s  (%s)" % (uml_spec.SPEC_TITLE,
                                     uml_spec.SPEC_DOCUMENT)
        for col in range(4):
            item.setToolTip(col, tooltip)

    def _activate(self, item: QTreeWidgetItem, _col: int) -> None:
        eid = item.data(0, ID_ROLE)
        if eid:
            self.element_activated.emit(eid)

    def _on_select(self) -> None:
        items = self.selectedItems()
        if items:
            eid = items[0].data(0, ID_ROLE)
            if eid:
                self.element_activated.emit(eid)
