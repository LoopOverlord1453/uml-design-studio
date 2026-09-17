"""The workspace tree: folders, model files and the CONTENT of every model.

WHY A SINGLE TREE
-----------------
A workspace holds more than one model (a system plus its subsystems). The
previous "MODEL TREE" panel showed ONLY the model that was open; the other
models were nowhere to be seen in the interface, and the user had to walk the
folder by hand through File > Open.

Here there is a single tree:

    model/
      pump/                     <- subsystem (folder)
        pump.usm                <- model file (EXPANDABLE)
          ● Initial · Start     <- the CONTENT of the model
          ▣ Composite · Running
      classes.ucd

Every model node CAN BE EXPANDED and its content is read ONLY WHEN OPENED
(lazy loading). In a workspace with hundreds of models, parsing them all at
start-up would lock the panel for minutes; and a corrupt file must not crash
the application at start-up -- a read error turns into one warning row under
that node.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (QAbstractItemView, QMenu, QMessageBox,
                             QTreeWidget, QTreeWidgetItem)

from ..core.class_model import ClassModel
from ..core.model import StateMachine
from .document import KIND_CLASS, KIND_STATE, detect_kind
from .panels import (CLASS_GLYPH, CLASS_LABEL, ID_ROLE, KIND_GLYPH,
                     KIND_LABEL, RELATION_GLYPH, RELATION_LABEL)
from .contrast import apply_contrast
from .theme import C, mono_font, ui_font

#: The absolute file path the node carries (for model files).
PATH_ROLE = Qt.ItemDataRole.UserRole + 11
#: The node kind: "dir" | "model" | "element" | "note"
NODE_ROLE = Qt.ItemDataRole.UserRole + 12
#: Has the content of the model node been loaded (the lazy-loading flag).
LOADED_ROLE = Qt.ItemDataRole.UserRole + 13
#: The signature of the STRUCTURE the node shows (see _structure_signature).
SIGNATURE_ROLE = Qt.ItemDataRole.UserRole + 14

MODEL_SUFFIXES = (".usm", ".ucd", ".json")

#: The directions shown in an empty panel.
#:
#: The menu path and the shortcut are written BY HAND but compared against
#: the REAL action in a test (see test_regressions, "empty panel hint"). The
#: first version put "File > Open Workspace... (Ctrl+Shift+O)" here; the real
#: name of the action was "Workspace..." and its shortcut Ctrl+Shift+W -- so
#: the hint sent the user to a menu item that did not exist.
OPEN_WORKSPACE_MENU = "File ▸ Workspace…"
OPEN_WORKSPACE_KEY = "Ctrl+Shift+W"
OPEN_WORKSPACE_HINT = "%s (%s)" % (OPEN_WORKSPACE_MENU, OPEN_WORKSPACE_KEY)


class WorkspaceTree(QTreeWidget):
    """Shows every model in the workspace together with its content."""

    #: When the user opens a model file (double click).
    model_activated = pyqtSignal(str)
    #: When an element is selected in the OPEN model (synced with the canvas).
    element_activated = pyqtSignal(str)
    #: When a model file is DELETED from the workspace.
    model_removed = pyqtSignal(str)
    #: When a model FILE is selected (a click, not to open it).
    #: The main window uses this to set up DIFF mode.
    model_selected = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setIndentation(14)
        self.setFont(ui_font(9))
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setAlternatingRowColors(False)

        self._workspace = None
        self._open_paths: Dict[str, StateMachine] = {}
        self._suppress = False

        # THE PANEL MUST NOT BE EMPTY AT START-UP. `rebuild` was called only when a
        # workspace had been applied; before any was chosen, the panel title
        # ("WORKSPACE") was there but not a single row below it -- nothing telling
        # the user what had happened or what to do.
        # 
        self.rebuild()

        self.itemExpanded.connect(self._on_expanded)
        self.itemDoubleClicked.connect(self._on_double_click)
        self.itemSelectionChanged.connect(self._on_selection)

        # CONTEXT MENU + the Delete key: two ways to remove a model file from the
        # workspace. Since the tree is the only navigation aid, deleting a file
        # should not require going out to the file manager.
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        apply_contrast(self)

    # ------------------------------------------------------------------ API #

    def set_workspace(self, workspace, open_models: Optional[Dict] = None) -> None:
        """Rebuilds the tree from the workspace.

        :param open_models: {absolute path: model object} -- the OPEN documents.
            An open model must show the version BEING EDITED, not the one on
            disk; otherwise the tree does not see unsaved changes.
            degisiklikleri gormez.
        """
        self._workspace = workspace
        self._open_paths = dict(open_models or {})
        self.rebuild()

    def set_open_models(self, open_models: Dict) -> None:
        """Reports the OPEN models; the tree shows only the files ON DISK."""
        onceki = {self._norm(k) for k in self._open_paths}
        self._open_paths = dict(open_models or {})
        if {self._norm(k) for k in self._open_paths} != onceki:
            # The SET of open files changed: the marks (filled/hollow diamond,
            # colour) have to be redrawn.
            self.rebuild()
            return
        self.refresh_open_nodes()

    def rebuild(self) -> None:
        genisleyenler = self._expanded_paths()
        self._suppress = True
        self.clear()

        # THE TREE SHOWS ONLY THE FILES ON DISK.
        #
        # Open documents without a file used to be added at the top as
        # "◆ untitled.usm  (not saved)". Because the application set up a sample
        # document for BOTH modes at start-up, those rows became PERMANENT: even
        # while the user worked on blinky.usm, two "untitled" entries with no
        # connection to the workspace sat at the top of the tree. The panel is
        # called WORKSPACE; its content should be the workspace.
        # 
        if self._workspace is None:
            self._bilgi_satiri(
                "No workspace is open.",
                "Use %s to choose one." % OPEN_WORKSPACE_HINT)
            self._suppress = False
            return

        kok = QTreeWidgetItem(self, ["▣  %s" % self._workspace.name])
        f = ui_font(9)
        f.setBold(True)
        kok.setFont(0, f)
        # STATE_TITLE is for the coloured title strip on the canvas and is white in
        # both themes; it would vanish on the white tree background in the light theme.
        kok.setForeground(0, QBrush(QColor(C.TEXT_BRIGHT)))
        kok.setData(0, NODE_ROLE, "dir")
        kok.setData(0, PATH_ROLE, self._workspace.model_path)
        kok.setToolTip(0, self._workspace.root)

        self._fill_dir(kok, self._workspace.model_path)
        kok.setExpanded(True)
        self._restore_expanded(genisleyenler)
        self._suppress = False

    def _bilgi_satiri(self, *satirlar: str) -> None:
        """The explanatory rows shown instead of a tree (not clickable)."""
        for metin in satirlar:
            dugum = QTreeWidgetItem(self, [metin])
            dugum.setForeground(0, QBrush(QColor(C.TEXT_DIM)))
            dugum.setData(0, NODE_ROLE, "note")
            dugum.setFlags(Qt.ItemFlag.ItemIsEnabled)

    @staticmethod
    def _structure_signature(model):
        """A cheap signature of the structure VISIBLE in the tree.

        Position changes (dragging) DO NOT AFFECT the tree, yet they still emit
        Document.changed. Tearing down and rebuilding the subtree on every
        gesture took about 157 ms on a model with 200 classes and made dragging
        stutter. The signature is built only from the fields written into the
        tree; when it has not changed, the drawing is skipped entirely.
        """
        if isinstance(model, StateMachine):
            # entry/exit/do GO INTO the signature too: the tree now shows the
            # behaviours as well, so a changed behaviour has to refresh the subtree.
            # tazelemek zorunda.
            return tuple(sorted(
                (s.id, s.name, s.kind.value, s.parent or "",
                 s.entry, s.exit, s.do)
                for s in model.states.values())) + tuple(sorted(
                    (t.id, t.source, t.target, t.label() or "")
                    for t in model.transitions.values()))
        parts = []
        for c in getattr(model, "ordered_classes", lambda: [])():
            parts.append((c.id, c.name, str(c.stereotype),
                          tuple(a.label() for a in c.attributes),
                          tuple(o.label() for o in c.operations)))
        for r in getattr(model, "ordered_relations", lambda: [])():
            parts.append((r.id, r.source, r.target, str(r.kind)))
        return tuple(parts)

    def _fill_model_node(self, node: QTreeWidgetItem, model) -> None:
        """Writes the content of a model into the node (state machine or class)."""
        if isinstance(model, StateMachine):
            self._fill_states(node, model, None)
            if node.childCount() == 0:
                self._note(node, "(empty model)", C.TEXT_DIM)
        else:
            self._fill_class_model(node, model)
            if node.childCount() == 0:
                self._note(node, "(empty model)", C.TEXT_DIM)

    def refresh_open_nodes(self) -> None:
        """Refreshes the subtree of the open models (unsaved changes)."""
        for item in self._all_items():
            if item.data(0, NODE_ROLE) != "model":
                continue
            yol = item.data(0, PATH_ROLE) or ""
            if self._norm(yol) not in {self._norm(k) for k in self._open_paths}:
                continue
            if not item.data(0, LOADED_ROLE):
                continue
            # Do not rebuild the subtree on a POSITION-only change (see
            # _structure_signature): dragging stuttered on large models.
            model = next((m for k, m in self._open_paths.items()
                          if self._norm(k) == self._norm(yol)), None)
            if model is not None:
                imza = self._structure_signature(model)
                if item.data(0, SIGNATURE_ROLE) == imza:
                    continue
                item.setData(0, SIGNATURE_ROLE, imza)
            self._load_model_node(item, force=True)

    def select_element(self, eid: str) -> None:
        """Selects an element in the open model (a selection from the canvas)."""
        self._suppress = True
        self.clearSelection()
        for item in self._all_items():
            if item.data(0, ID_ROLE) == eid:
                item.setSelected(True)
                self.scrollToItem(item)
                break
        self._suppress = False

    def retheme(self) -> None:
        self.rebuild()

    # -------------------------------------------------------------- set-up -- #

    @staticmethod
    def _norm(path: str) -> str:
        return os.path.normcase(os.path.abspath(path)) if path else ""

    def _fill_dir(self, parent: QTreeWidgetItem, path: str) -> None:
        """Writes a folder into the tree: subfolders first, then model files."""
        try:
            girdiler = sorted(os.listdir(path), key=str.lower)
        except OSError:
            uyari = QTreeWidgetItem(parent, ["(folder cannot be read)"])
            uyari.setForeground(0, QBrush(QColor(C.WARN)))
            uyari.setData(0, NODE_ROLE, "note")
            return

        klasorler = [a for a in girdiler if os.path.isdir(os.path.join(path, a))]
        dosyalar = [a for a in girdiler
                    if a.lower().endswith(MODEL_SUFFIXES)
                    and os.path.isfile(os.path.join(path, a))]

        for ad in klasorler:
            tam = os.path.join(path, ad)
            dugum = QTreeWidgetItem(parent, ["📁  %s" % ad])
            dugum.setData(0, NODE_ROLE, "dir")
            dugum.setData(0, PATH_ROLE, tam)
            dugum.setForeground(0, QBrush(QColor(C.TEXT)))
            self._fill_dir(dugum, tam)

        for ad in dosyalar:
            self._add_model_node(parent, os.path.join(path, ad), ad)

        if not klasorler and not dosyalar and parent.parent() is None:
            # An empty workspace must not be a DEAD END: the user is told how a file
            # gets there. The old text ("(no model files yet)") reported the state
            # but did not say what to do about it.
            for metin in ("No models here yet.",
                          "Press Ctrl+S to save the open diagram "
                          "into this workspace."):
                bos = QTreeWidgetItem(parent, [metin])
                bos.setForeground(0, QBrush(QColor(C.TEXT_DIM)))
                bos.setData(0, NODE_ROLE, "note")
                bos.setFlags(Qt.ItemFlag.ItemIsEnabled)

    def _add_model_node(self, parent: QTreeWidgetItem, tam: str, ad: str) -> None:
        acik = self._norm(tam) in {self._norm(k) for k in self._open_paths}
        etiket = "%s  %s" % ("◆" if acik else "◇", ad)
        dugum = QTreeWidgetItem(parent, [etiket])
        dugum.setData(0, NODE_ROLE, "model")
        dugum.setData(0, PATH_ROLE, tam)
        dugum.setData(0, LOADED_ROLE, False)
        dugum.setToolTip(0, tam + ("\n(open)" if acik else ""))
        if acik:
            f = ui_font(9)
            f.setBold(True)
            dugum.setFont(0, f)
            dugum.setForeground(0, QBrush(QColor(C.ACCENT)))
        else:
            dugum.setForeground(0, QBrush(QColor(C.TEXT)))

        # A placeholder child is added so the node looks expandable; the real
        # content is read when the node IS EXPANDED (see _on_expanded).
        QTreeWidgetItem(dugum, ["…"])

    # ---------------------------------------------------------- lazy loading #

    def _on_expanded(self, item: QTreeWidgetItem) -> None:
        if item.data(0, NODE_ROLE) == "model" and not item.data(0, LOADED_ROLE):
            self._load_model_node(item)

    def _load_model_node(self, item: QTreeWidgetItem, force: bool = False) -> None:
        """Reads the model file and builds its content as a subtree."""
        yol = item.data(0, PATH_ROLE) or ""
        item.takeChildren()
        item.setData(0, LOADED_ROLE, True)

        makine = None
        for acik_yol, model in self._open_paths.items():
            if self._norm(acik_yol) == self._norm(yol):
                makine = model
                break

        if makine is None:
            try:
                with open(yol, "r", encoding="utf-8") as fh:
                    metin = fh.read()
                tur = detect_kind(metin)
                if tur == KIND_STATE:
                    makine = StateMachine.from_json(metin)
                elif tur == KIND_CLASS:
                    self._fill_class_summary(item, metin)
                    return
                else:
                    self._note(item, "unrecognised model file", C.WARN)
                    return
            except Exception as exc:
                # A corrupt or unreadable file DOES NOT CRASH THE TREE: a warning row
                # appears under the node and the other models keep working.
                self._note(item, "cannot be read: %s" % exc, C.RED)
                return

        self._fill_model_node(item, makine)

    def _fill_states(self, parent: QTreeWidgetItem, sm: StateMachine,
                     parent_id: Optional[str]) -> None:
        for st in sm.sorted_children(parent_id):
            dugum = QTreeWidgetItem(
                parent, ["%s  %s · %s" % (KIND_GLYPH[st.kind],
                                          KIND_LABEL[st.kind], st.name)])
            dugum.setData(0, ID_ROLE, st.id)
            dugum.setData(0, NODE_ROLE, "element")
            dugum.setToolTip(0, "%s — %s" % (KIND_LABEL[st.kind], st.name))
            if st.kind.is_pseudo:
                dugum.setForeground(0, QBrush(QColor(C.TEXT_DIM)))

            # THE BEHAVIOURS (entry / exit / do) APPEAR IN THE TREE TOO.
            #
            # The tree already lists attributes and operations for a class diagram;
            # showing only the transitions for a state machine and skipping the
            # behaviours was inconsistent. And the exit/do behaviour of a composite
            # state may not fit in its box -- the tree is then the only reliable
            # source.
            for etiket, metin in (("entry /", st.entry),
                                  ("exit  /", st.exit),
                                  ("do    /", st.do)):
                if not metin.strip():
                    continue
                self._member(dugum, st.id,
                             "%s %s" % (etiket, " ".join(metin.split())),
                             C.STATE_TEXT)

            self._fill_states(dugum, sm, st.id)
            for tr in sm.outgoing(st.id):
                hedef = sm.states.get(tr.target)
                etiket = tr.label() or "(completion)"
                yaprak = QTreeWidgetItem(
                    dugum, ["→ %s : %s" % (hedef.name if hedef else "?", etiket)])
                yaprak.setData(0, ID_ROLE, tr.id)
                yaprak.setData(0, NODE_ROLE, "element")
                yaprak.setForeground(0, QBrush(QColor(C.TRANSITION)))
                yaprak.setFont(0, mono_font(8))

    def _fill_class_model(self, parent: QTreeWidgetItem, cm) -> None:
        """Writes an open class model into the tree.

        Because a separate "MODEL TREE" panel WAS REMOVED, this tree is now the
        only navigation aid; so it has to show not just the class names but the
        attributes, the operations and the relationships as well -- otherwise
        removing that panel would have lost information.
        """
        for cls in getattr(cm, "ordered_classes", lambda: [])():
            glyph = CLASS_GLYPH.get(cls.stereotype, "▭")
            label = CLASS_LABEL.get(cls.stereotype, "Class")
            dugum = QTreeWidgetItem(parent, ["%s  %s · %s"
                                             % (glyph, label, cls.name)])
            dugum.setData(0, ID_ROLE, cls.id)
            dugum.setData(0, NODE_ROLE, "element")
            dugum.setToolTip(0, "%s — %s" % (label, cls.name))
            for attr in cls.attributes:
                self._member(dugum, cls.id, attr.label(), C.TEXT_DIM)
            for op in cls.operations:
                self._member(dugum, cls.id, op.label(), C.CLASS_TEXT)

        for rel in getattr(cm, "ordered_relations", lambda: [])():
            src = cm.classes.get(rel.source)
            dst = cm.classes.get(rel.target)
            glyph = RELATION_GLYPH.get(rel.kind, "──")
            label = RELATION_LABEL.get(rel.kind, "Association")
            yaprak = QTreeWidgetItem(
                parent, ["%s  %s : %s → %s"
                         % (glyph, label,
                            src.name if src else "?",
                            dst.name if dst else "?")])
            yaprak.setData(0, ID_ROLE, rel.id)
            yaprak.setData(0, NODE_ROLE, "element")
            yaprak.setForeground(0, QBrush(QColor(C.TRANSITION)))
            yaprak.setFont(0, mono_font(8))
            yaprak.setToolTip(0, label)

    @staticmethod
    def _member(parent: QTreeWidgetItem, cid: str, text: str,
                color: str) -> None:
        leaf = QTreeWidgetItem(parent, ["  %s" % text])
        leaf.setData(0, ID_ROLE, cid)
        leaf.setData(0, NODE_ROLE, "element")
        leaf.setFont(0, mono_font(8))
        leaf.setForeground(0, QBrush(QColor(color)))

    def _fill_class_summary(self, parent: QTreeWidgetItem, metin: str) -> None:
        """Reads a CLOSED class diagram from disk and writes it into the tree.

        It uses the SAME drawing path (_fill_class_model): writing a separate
        JSON summariser would mean keeping the attribute/operation format in
        two places, and the view would change when the user opened the file.
        """
        try:
            cm = ClassModel.from_json(metin)
        except Exception:
            self._note(parent, "cannot be parsed", C.RED)
            return
        self._fill_class_model(parent, cm)
        if not cm.classes:
            self._note(parent, "(no classes)", C.TEXT_DIM)

    @staticmethod
    def _note(parent: QTreeWidgetItem, text: str, color: str) -> None:
        note = QTreeWidgetItem(parent, [text])
        note.setForeground(0, QBrush(QColor(color)))
        note.setData(0, NODE_ROLE, "note")

    # --------------------------------------------------------- interaction -- #

    # --------------------------------------------------------- file removal #

    def _selected_model_path(self) -> Optional[str]:
        """The absolute path when the selected node is a MODEL FILE, else None."""
        items = self.selectedItems()
        if not items:
            return None
        item = items[0]
        if item.data(0, NODE_ROLE) != "model":
            return None
        return item.data(0, PATH_ROLE) or None

    def _show_context_menu(self, pos) -> None:
        item = self.itemAt(pos)
        if item is None:
            return
        item.setSelected(True)
        yol = self._selected_model_path()
        if yol is None:
            return

        menu = QMenu(self)
        ac = menu.addAction("Open")
        menu.addSeparator()
        kaldir = menu.addAction("Remove from workspace…")
        secim = menu.exec(self.viewport().mapToGlobal(pos))
        if secim is ac:
            self.model_activated.emit(yol)
        elif secim is kaldir:
            self.remove_selected()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self._selected_model_path() is not None:
                self.remove_selected()
                event.accept()
                return
        super().keyPressEvent(event)

    def remove_selected(self) -> None:
        """Deletes the selected model file AFTER ASKING FOR CONFIRMATION.

        Deletion CANNOT BE UNDONE and touches the disk, so it always asks.
        Deleting an open model leaves the user with a document that has no
        file, and that case is warned about separately.
        """
        yol = self._selected_model_path()
        if yol is None:
            return

        acik = self._norm(yol) in {self._norm(k) for k in self._open_paths}
        metin = "Delete this model file from the workspace?\n\n%s" % yol
        if acik:
            metin += ("\n\nIt is open in the editor and will be closed; "
                      "the canvas will be emptied.")

        cevap = QMessageBox.warning(
            self, "Remove model", metin,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if cevap != QMessageBox.StandardButton.Yes:
            return

        try:
            os.remove(yol)
        except OSError as exc:
            QMessageBox.warning(self, "Could not remove",
                                "%s\n\n%s" % (yol, exc))
            return

        self.model_removed.emit(yol)
        self.rebuild()

    def _on_double_click(self, item: QTreeWidgetItem, _col: int) -> None:
        if item.data(0, NODE_ROLE) == "model":
            yol = item.data(0, PATH_ROLE)
            if yol:
                self.model_activated.emit(yol)

    def _on_selection(self) -> None:
        if self._suppress:
            return
        items = self.selectedItems()
        if not items:
            return
        secili = items[0]
        if secili.data(0, NODE_ROLE) == "model":
            yol = secili.data(0, PATH_ROLE)
            if yol:
                self.model_selected.emit(yol)
            return
        eid = secili.data(0, ID_ROLE)
        if eid:
            self.element_activated.emit(eid)

    # ------------------------------------------------------------- helpers -- #

    def _all_items(self) -> List[QTreeWidgetItem]:
        out: List[QTreeWidgetItem] = []

        def walk(item: QTreeWidgetItem) -> None:
            out.append(item)
            for i in range(item.childCount()):
                walk(item.child(i))

        for i in range(self.topLevelItemCount()):
            walk(self.topLevelItem(i))
        return out

    def _expanded_paths(self):
        return {item.data(0, PATH_ROLE) for item in self._all_items()
                if item.isExpanded() and item.data(0, PATH_ROLE)}

    def _restore_expanded(self, paths) -> None:
        if not paths:
            return
        for item in self._all_items():
            if item.data(0, PATH_ROLE) in paths:
                item.setExpanded(True)
