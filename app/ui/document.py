"""The document controller: model + undo/redo + change notification.

The undo strategy is *snapshot* based: the model JSON is stored before and
after every user operation. At this diagram scale (hundreds of vertices) the
cost is negligible, and it removes the whole class of "half-applied command"
bugs.

The same class manages both state machine (StateMachine) and class diagram
(ClassModel) documents; it relies on the model object's `to_json` /
`assign_from` interface and on the class's `from_json` static method.
"""

from __future__ import annotations

import json
from typing import Callable, List, Optional

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QUndoCommand, QUndoStack

#: Document type names, by file content
KIND_STATE = "state"
KIND_CLASS = "class"


def detect_kind(text: str) -> Optional[str]:
    """Determines the type of a model file FROM ITS CONTENT.

    Trusting the extension is not enough: ".json" can carry either type, and a
    file loaded into the wrong mode would silently turn into an EMPTY model and
    erase the original content on the first save.

    :return: ``"state"``, ``"class"``, or ``None`` when unrecognised
    """
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    declared = data.get("type")
    if declared == "class_diagram":
        return KIND_CLASS
    if declared == "state_machine":
        return KIND_STATE
    # Older files without a type field: look at the distinguishing keys.
    if isinstance(data.get("classes"), list):
        return KIND_CLASS
    if isinstance(data.get("states"), list):
        return KIND_STATE
    return None


class _SnapshotCommand(QUndoCommand):
    def __init__(self, doc: "Document", before: str, after: str, text: str) -> None:
        super().__init__(text)
        self._doc = doc
        self._before = before
        self._after = after
        self._first = True

    def redo(self) -> None:
        if self._first:
            self._first = False        # the mutation has already been applied
            self._doc.changed.emit()
            return
        self._doc._restore(self._after)

    def undo(self) -> None:
        self._doc._restore(self._before)


class Document(QObject):
    """The open diagram (a state machine or a class model)."""

    changed = pyqtSignal()             # the model changed -> refresh the view and the code
    path_changed = pyqtSignal()
    selection_request = pyqtSignal(list)   # a list of ids

    def __init__(self, machine, parent=None, default_name: str = "untitled.usm") -> None:
        super().__init__(parent)
        self.machine = machine
        self._model_cls = type(machine)
        self._default_name = default_name
        # The document type this document carries (for load validation)
        self.kind = KIND_CLASS if hasattr(machine, "classes") else KIND_STATE
        self.undo_stack = QUndoStack(self)
        self.path: Optional[str] = None
        self._clean_snapshot = machine.to_json()
        self._in_restore = False

    # ----------------------------------------------------------------- change

    def edit(self, label: str, mutator: Callable) -> bool:
        """Changes the model and makes the operation undoable.

        If `mutator` changes nothing, no command is pushed onto the stack.
        """
        before = self.machine.to_json()
        mutator(self.machine)
        after = self.machine.to_json()
        if before == after:
            return False
        self.undo_stack.push(_SnapshotCommand(self, before, after, label))
        return True

    def edit_from(self, label: str, before: str) -> bool:
        """Used when the model has ALREADY been changed (during a drag, say).

        `before` is the snapshot taken before the change.
        """
        after = self.machine.to_json()
        if before == after:
            return False
        self.undo_stack.push(_SnapshotCommand(self, before, after, label))
        return True

    def _restore(self, snapshot: str) -> None:
        self._in_restore = True
        try:
            restored = self._model_cls.from_json(snapshot)
            # We keep the same object so outside references are not broken.
            self.machine.assign_from(restored)
        finally:
            self._in_restore = False
        self.changed.emit()

    # ------------------------------------------------------------------- file

    def load(self, path: str) -> None:
        """Reads the file and replaces the model.

        If the content is not of this document's type, nothing is changed;
        ``ValueError`` is raised and the caller reports it to the user.
        """
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        kind = detect_kind(text)
        if kind is None:
            raise ValueError(
                "Unrecognised file: not a valid state machine (.usm) or "
                "class diagram (.ucd).")
        if kind != self.kind:
            other = "class diagram" if kind == KIND_CLASS else "state machine"
            mine = "class diagram" if self.kind == KIND_CLASS else "state machine"
            raise ValueError(
                "This file is a %s; it cannot be opened as a %s." % (other, mine))
        restored = self._model_cls.from_json(text)
        self.machine.assign_from(restored)
        self.path = path
        self.undo_stack.clear()
        self.mark_clean()
        self.path_changed.emit()
        self.changed.emit()

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(self.machine.to_json())
        self.path = path
        self.mark_clean()
        self.path_changed.emit()

    def replace(self, machine, path: Optional[str] = None) -> None:
        self.machine.assign_from(machine)
        self.path = path
        self.undo_stack.clear()
        self.mark_clean()
        self.path_changed.emit()
        self.changed.emit()

    # ------------------------------------------------------------------- state

    def mark_clean(self) -> None:
        self._clean_snapshot = self.machine.to_json()
        self.undo_stack.setClean()

    def is_dirty(self) -> bool:
        return self.machine.to_json() != self._clean_snapshot

    def title(self) -> str:
        import os
        base = os.path.basename(self.path) if self.path else self._default_name
        return "%s%s" % (base, "*" if self.is_dirty() else "")

    def select(self, ids: List[str]) -> None:
        self.selection_request.emit(list(ids))
