"""The class diagram canvas: tool modes, zooming, panning."""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional, Set

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QGraphicsPathItem, QGraphicsScene, QGraphicsView

from ..core.class_model import Relation, RelationKind, Stereotype, UmlClass
from .canvas_nav import CanvasNavigation
from .class_items import ClassItem, RelationItem
from .diagram_items import GRID, GhostItem, snap
from .document import Document
from .theme import C


class ClassTool(str, Enum):
    SELECT = "select"
    CLASS = "class"
    ABSTRACT = "abstract"
    INTERFACE = "interface"
    ASSOCIATION = "association"
    AGGREGATION = "aggregation"
    COMPOSITION = "composition"
    GENERALIZATION = "generalization"
    REALIZATION = "realization"
    DEPENDENCY = "dependency"


_NEW_CLASS_DEFAULTS = {
    ClassTool.CLASS: (Stereotype.CLASS, "NewClass"),
    ClassTool.ABSTRACT: (Stereotype.ABSTRACT, "AbstractClass"),
    ClassTool.INTERFACE: (Stereotype.INTERFACE, "NewInterface"),
}

_RELATION_TOOLS = {
    ClassTool.ASSOCIATION: RelationKind.ASSOCIATION,
    ClassTool.AGGREGATION: RelationKind.AGGREGATION,
    ClassTool.COMPOSITION: RelationKind.COMPOSITION,
    ClassTool.GENERALIZATION: RelationKind.GENERALIZATION,
    ClassTool.REALIZATION: RelationKind.REALIZATION,
    ClassTool.DEPENDENCY: RelationKind.DEPENDENCY,
}


class ClassCanvas(CanvasNavigation, QGraphicsView):
    """The bridge between the ClassModel and the graphics scene."""

    selection_changed = pyqtSignal(list)
    tool_finished = pyqtSignal()
    status_message = pyqtSignal(str)

    def __init__(self, doc: Document, parent=None) -> None:
        super().__init__(parent)
        self.doc = doc
        self.tool = ClassTool.SELECT
        self.snap_enabled = True
        self.show_grid = True
        # Property dialog after placement (tests may switch it off; modal).
        self.auto_edit = True

        self._scene = QGraphicsScene(self)
        self._scene.setBackgroundBrush(QColor(C.CANVAS_BG))
        self.setScene(self._scene)

        self.setRenderHints(QPainter.RenderHint.Antialiasing
                            | QPainter.RenderHint.TextAntialiasing
                            | QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setViewportUpdateMode(
            QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setMouseTracking(True)

        self.class_items: Dict[str, ClassItem] = {}
        self.rel_items: Dict[str, RelationItem] = {}

        #: DIFF marks coming from the workspace (see set_diff_marks).
        self._diff_marks: dict = {}
        #: Ghost frames drawn at the old places of deleted classes.
        self._ghosts: List[GhostItem] = []

        self._pending_source: Optional[ClassItem] = None
        self._rubber_line: Optional[QGraphicsPathItem] = None
        self._pre_drag: Optional[str] = None
        self._panning = False
        self._pan_origin = QPointF()
        self._suppress_selection = False
        self._init_navigation()

        self._scene.selectionChanged.connect(self._on_selection_changed)

    # =================================================================== set-up

    def retheme(self) -> None:
        """Rereads the scene background on a theme change."""
        self._scene.setBackgroundBrush(QColor(C.CANVAS_BG))

    def rebuild(self) -> None:
        selected = self.selected_ids()
        self._suppress_selection = True
        self._cancel_relation()

        self._scene.clear()
        self.class_items.clear()
        self.rel_items.clear()
        # The ghosts were deleted together with the scene; empty the list too
        # so that DEAD pointers are not touched below.
        self._ghosts = []

        cm = self.doc.machine
        for c in cm.ordered_classes():
            item = ClassItem(c, self)
            self.class_items[c.id] = item
            self._scene.addItem(item)

        routes = self._compute_routes()
        for r in cm.ordered_relations():
            src = self.class_items.get(r.source)
            dst = self.class_items.get(r.target)
            if src is None or dst is None:
                continue
            item = RelationItem(r, src, dst, self, routes.get(r.id, 0.0))
            self.rel_items[r.id] = item
            self._scene.addItem(item)

        self._update_scene_rect()
        self._suppress_selection = False
        self.set_selected_ids(selected)
        if self._diff_marks:
            # Rebuilt items lose their marks.
            self.set_diff_marks(self._diff_marks)

    def _compute_routes(self) -> Dict[str, float]:
        groups: Dict[frozenset, List[Relation]] = {}
        for r in self.doc.machine.ordered_relations():
            if r.source == r.target:
                continue
            groups.setdefault(frozenset((r.source, r.target)), []).append(r)
        routes: Dict[str, float] = {}
        for pair, items in groups.items():
            if len(items) == 1:
                routes[items[0].id] = 0.0
                continue
            step = 56.0
            start = -(len(items) - 1) / 2.0
            for i, r in enumerate(items):
                sign = 1.0 if r.source == sorted(pair)[0] else -1.0
                routes[r.id] = (start + i) * step * sign
        return routes

    def _update_scene_rect(self) -> None:
        rect = self._scene.itemsBoundingRect()
        if rect.isNull():
            rect = QRectF(0, 0, 800, 600)
        # WITHOUT SCROLLING THE VIEW: see CanvasNavigation._set_scene_rect.
        self._set_scene_rect(rect.adjusted(-400, -300, 400, 300))

    def refresh_relations(self) -> None:
        for item in self.rel_items.values():
            item.update_path()

    def mark_errors(self, error_ids: Set[str], warning_ids: Set[str]) -> None:
        for cid, item in self.class_items.items():
            flag = cid in error_ids
            if item.has_error != flag:
                item.has_error = flag
                item.update()
        for rid, item in self.rel_items.items():
            flag = rid in error_ids
            if item.has_error != flag:
                item.has_error = flag
                item.update()

    def set_diff_marks(self, marks: Optional[dict]) -> None:
        """Paints the class diagram in DIFF mode.

        The SAME contract as on the state diagram (see DiagramCanvas): an added
        class/relationship is green, a changed one yellow, and deleted ones are
        dashed red ghosts at their old positions.

        THE BUG WE HIT: this function DID NOT EXIST AT ALL on the class canvas.
        When the main window called it while showing a diff, an AttributeError
        arose inside the signal and PyQt treated the uncaught exception as fatal
        and KILLED THE PROCESS (0xC0000409). Clicking a class model closed the app.
        """
        self._diff_marks = marks or {}
        added = self._diff_marks.get("added") or set()
        changed = self._diff_marks.get("changed") or set()

        for ident, item in list(self.class_items.items()) \
                + list(self.rel_items.items()):
            if ident in added:
                new = "added"
            elif ident in changed:
                new = "changed"
            else:
                new = ""
            if getattr(item, "diff_mark", "") != new:
                item.diff_mark = new
                item.update()

        self._rebuild_ghosts(self._diff_marks.get("removed") or {})

    def _rebuild_ghosts(self, removed: dict) -> None:
        """Draws deleted classes as ghosts at their old positions."""
        for item in self._ghosts:
            if item.scene() is self._scene:
                self._scene.removeItem(item)
        self._ghosts = []
        for data in (removed or {}).values():
            # Relationships hang off their end points, and the ends may have been
            # deleted too; only items that HAVE a position can be drawn.
            if "x" not in data or "y" not in data:
                continue
            ghost = GhostItem(data)
            self._scene.addItem(ghost)
            self._ghosts.append(ghost)

    # ============================================================== selection

    def selected_ids(self) -> List[str]:
        out: List[str] = []
        for item in self._scene.selectedItems():
            if isinstance(item, ClassItem):
                out.append(item.cls.id)
            elif isinstance(item, RelationItem):
                out.append(item.relation.id)
        return out

    def set_selected_ids(self, ids: List[str]) -> None:
        self._suppress_selection = True
        self._scene.clearSelection()
        for eid in ids:
            item = self.class_items.get(eid) or self.rel_items.get(eid)
            if item is not None:
                item.setSelected(True)
        self._suppress_selection = False
        self._on_selection_changed()

    def focus_element(self, eid: str) -> None:
        item = self.class_items.get(eid) or self.rel_items.get(eid)
        if item is None:
            return
        self.set_selected_ids([eid])
        self.ensureVisible(item, 80, 80)

    def _on_selection_changed(self) -> None:
        if not self._suppress_selection:
            self.selection_changed.emit(self.selected_ids())

    # ================================================================== editing

    def edit_element(self, eid: str) -> None:
        """Double click: opens the property dialog and applies the change."""
        from .class_dialogs import ClassDialog, RelationDialog
        cm = self.doc.machine

        c = cm.classes.get(eid)
        if c is not None:
            dlg = ClassDialog(c, self)
            if dlg.exec():
                def mutate(m):
                    dlg.apply_to(m.classes[eid])
                self.doc.edit("Edit '%s'" % c.name, mutate)
            return

        r = cm.relations.get(eid)
        if r is not None:
            src = cm.classes.get(r.source)
            dst = cm.classes.get(r.target)
            dlg = RelationDialog(r, src.name if src else "?",
                                 dst.name if dst else "?", self)
            if dlg.exec():
                def mutate(m):
                    dlg.apply_to(m.relations[eid])
                self.doc.edit("Edit relationship", mutate)

    # ==================================================================== tools

    def set_tool(self, tool: ClassTool) -> None:
        self.tool = tool
        self._cancel_relation()
        if tool is ClassTool.SELECT:
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.viewport().setCursor(Qt.CursorShape.CrossCursor)

    def _cancel_relation(self) -> None:
        self._pending_source = None
        if self._rubber_line is not None:
            if self._rubber_line.scene() is not None:
                self._scene.removeItem(self._rubber_line)
            self._rubber_line = None

    # ================================================================= mouse

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_origin = event.position()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return

        scene_pos = self.mapToScene(event.position().toPoint())

        if self.tool in _RELATION_TOOLS and \
                event.button() == Qt.MouseButton.LeftButton:
            self._handle_relation_click(scene_pos)
            event.accept()
            return

        if self.tool in _NEW_CLASS_DEFAULTS and \
                event.button() == Qt.MouseButton.LeftButton:
            self._place_class(scene_pos)
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton:
            self._pre_drag = self.doc.machine.to_json()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._panning:
            delta = event.position() - self._pan_origin
            self._pan_origin = event.position()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - int(delta.x()))
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - int(delta.y()))
            event.accept()
            return

        if self._pending_source is not None and self._rubber_line is not None:
            start = self._pending_source.scene_center()
            end = self.mapToScene(event.position().toPoint())
            path = QPainterPath(start)
            path.lineTo(end)
            self._rubber_line.setPath(path)

        # The class canvas behaves EXACTLY like the state canvas: the scene grows
        # while dragging, and the view scrolls when the cursor reaches the edge.
        dragging = bool(event.buttons() & Qt.MouseButton.LeftButton)
        if dragging:
            self._room_for_drag(event.position().toPoint())

        super().mouseMoveEvent(event)

        if dragging:
            self._update_autoscroll(event.position().toPoint())
        else:
            self._stop_autoscroll()

    def mouseReleaseEvent(self, event) -> None:
        self._stop_autoscroll()
        if self._panning and event.button() == Qt.MouseButton.MiddleButton:
            self._panning = False
            self.viewport().setCursor(
                Qt.CursorShape.ArrowCursor if self.tool is ClassTool.SELECT
                else Qt.CursorShape.CrossCursor)
            event.accept()
            return

        super().mouseReleaseEvent(event)
        # Drag finished: the guides go away.
        self.align_clear()

        if self._pre_drag is not None and self.tool is ClassTool.SELECT:
            snapshot = self._pre_drag
            self._pre_drag = None
            self._write_geometry_to_model()
            if self.doc.machine.to_json() != snapshot:
                self.doc.edit_from("Move / resize", snapshot)

    def commit_geometry(self, label: str) -> None:
        snapshot = self._pre_drag
        self._pre_drag = None
        self._write_geometry_to_model()
        if snapshot is not None and self.doc.machine.to_json() != snapshot:
            self.doc.edit_from(label, snapshot)

    def _write_geometry_to_model(self) -> None:
        for item in self.class_items.values():
            item.cls.x = round(item.pos().x(), 2)
            item.cls.y = round(item.pos().y(), 2)
            item.cls.w = round(item.cls.w, 2)
            item.cls.h = round(item.cls.h, 2)

    # ------------------------------------------------------- element creation

    def _place_class(self, scene_pos: QPointF) -> None:
        stereo, base = _NEW_CLASS_DEFAULTS[self.tool]
        x = snap(scene_pos.x() - 110.0, self.snap_enabled)
        y = snap(scene_pos.y() - 70.0, self.snap_enabled)
        name = self._unique_name(base)
        new_class = UmlClass(name=name, stereotype=stereo, x=x, y=y)

        def mutate(cm):
            cm.add_class(new_class)

        self.doc.edit("Add '%s'" % name, mutate)
        self.status_message.emit(
            "'%s' added. Double-click to edit it." % name)
        self.tool_finished.emit()
        if self.auto_edit:
            self.edit_element(new_class.id)

    def _unique_name(self, base: str) -> str:
        used = {c.name for c in self.doc.machine.classes.values()}
        if base not in used:
            return base
        i = 2
        while "%s%d" % (base, i) in used:
            i += 1
        return "%s%d" % (base, i)

    def _handle_relation_click(self, scene_pos: QPointF) -> None:
        hit = self._class_at(scene_pos)
        if hit is None:
            self._cancel_relation()
            self.status_message.emit("Relationship cancelled.")
            return

        if self._pending_source is None:
            self._pending_source = hit
            line = QGraphicsPathItem()
            pen = QPen(QColor(C.ACCENT), 1.6, Qt.PenStyle.DashLine)
            line.setPen(pen)
            line.setZValue(200.0)
            path = QPainterPath(hit.scene_center())
            path.lineTo(hit.scene_center())
            line.setPath(path)
            self._scene.addItem(line)
            self._rubber_line = line
            kind = _RELATION_TOOLS[self.tool]
            hints = {
                RelationKind.GENERALIZATION: "Pick the superclass (generalization target).",
                RelationKind.REALIZATION: "Pick the interface to realize.",
                RelationKind.COMPOSITION: "Pick the PART class (source is the whole).",
                RelationKind.AGGREGATION: "Pick the PART class (source is the whole).",
            }
            self.status_message.emit(hints.get(kind, "Pick the target class."))
            return

        source = self._pending_source
        self._cancel_relation()
        kind = _RELATION_TOOLS[self.tool]
        src_id, dst_id = source.cls.id, hit.cls.id
        if src_id == dst_id and kind is not RelationKind.ASSOCIATION:
            self.status_message.emit(
                "This relationship type cannot connect a class to itself.")
            return

        defaults = {}
        if kind in (RelationKind.COMPOSITION, RelationKind.AGGREGATION):
            defaults = {"source_mult": "1", "target_mult": "0..*"}
        new_rel = Relation(source=src_id, target=dst_id, kind=kind, **defaults)
        label = "%s -> %s %s" % (source.cls.name, hit.cls.name, kind.value)

        def mutate(cm):
            cm.add_relation(new_rel)

        self.doc.edit(label, mutate)
        self.status_message.emit(label + " added.")
        self.tool_finished.emit()

    def _class_at(self, scene_pos: QPointF) -> Optional[ClassItem]:
        for item in self._scene.items(scene_pos):
            if isinstance(item, ClassItem):
                return item
        return None

    # ================================================================ keyboard

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self._cancel_relation()
            self.tool_finished.emit()
            event.accept()
            return
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selection()
            event.accept()
            return
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up,
                   Qt.Key.Key_Down):
            step = 1.0 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier \
                else GRID
            dx = (-step if key == Qt.Key.Key_Left else
                  step if key == Qt.Key.Key_Right else 0.0)
            dy = (-step if key == Qt.Key.Key_Up else
                  step if key == Qt.Key.Key_Down else 0.0)
            self.nudge_selection(dx, dy)
            event.accept()
            return
        super().keyPressEvent(event)

    def nudge_selection(self, dx: float, dy: float) -> None:
        ids = [i for i in self.selected_ids() if i in self.doc.machine.classes]
        if not ids:
            return

        def mutate(cm):
            for cid in ids:
                c = cm.classes[cid]
                c.x = round(c.x + dx, 2)
                c.y = round(c.y + dy, 2)

        self.doc.edit("Nudge", mutate)

    def delete_selection(self) -> None:
        ids = self.selected_ids()
        if not ids:
            return
        cm = self.doc.machine
        class_ids = [i for i in ids if i in cm.classes]
        rel_ids = [i for i in ids if i in cm.relations]

        def mutate(model):
            for rid in rel_ids:
                model.remove_relation(rid)
            for cid in class_ids:
                model.remove_class(cid)

        count = len(class_ids) + len(rel_ids)
        self.doc.edit("Delete %d elements" % count, mutate)
        self.status_message.emit("%d elements deleted." % count)

    # ===================================================================== view

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self.zoom_by(factor)
            event.accept()
            return
        super().wheelEvent(event)

    def zoom_by(self, factor: float) -> None:
        current = self.transform().m11()
        target = current * factor
        if 0.15 <= target <= 6.0:
            self.scale(factor, factor)

    def zoom_reset(self) -> None:
        self.resetTransform()

    def zoom_fit(self) -> None:
        rect = self._scene.itemsBoundingRect()
        if rect.isNull():
            return
        self.fitInView(rect.adjusted(-40, -40, 40, 40),
                       Qt.AspectRatioMode.KeepAspectRatio)
        if self.transform().m11() > 1.6:
            self.resetTransform()

    def zoom_percent(self) -> int:
        return int(round(self.transform().m11() * 100))

    # ------------------------------------------------------------ background

    def sibling_boxes(self, item):
        """The boxes of the neighbours to align against.

        Selected items are left out: they move together with the item being
        dragged.
        """
        boxes = []
        for other in self.class_items.values():
            if other is item or other.isSelected():
                continue
            boxes.append((other.pos().x(), other.pos().y(),
                            other.cls.w, other.cls.h))
        return boxes

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        super().drawForeground(painter, rect)
        self.align_paint(painter)

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        painter.fillRect(rect, QColor(C.CANVAS_BG))
        if not self.show_grid or self.transform().m11() < 0.45:
            return
        left = int(rect.left()) - (int(rect.left()) % int(GRID))
        top = int(rect.top()) - (int(rect.top()) % int(GRID))
        minor = QPen(QColor(C.GRID_MINOR), 0.0)
        major = QPen(QColor(C.GRID_MAJOR), 0.0)

        painter.setPen(minor)
        x = float(left)
        while x < rect.right():
            if int(x) % (int(GRID) * 10) != 0:
                painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            x += GRID
        y = float(top)
        while y < rect.bottom():
            if int(y) % (int(GRID) * 10) != 0:
                painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            y += GRID

        painter.setPen(major)
        x = float(left)
        while x < rect.right():
            if int(x) % (int(GRID) * 10) == 0:
                painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            x += GRID
        y = float(top)
        while y < rect.bottom():
            if int(y) % (int(GRID) * 10) == 0:
                painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            y += GRID
