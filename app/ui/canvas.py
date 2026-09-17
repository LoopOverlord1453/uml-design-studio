"""The diagram canvas: scene set-up, tool modes, zooming, panning."""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional, Set

from PyQt6.QtCore import (QPoint, QPointF, QRect, QRectF, QSize, Qt,
                          pyqtSignal)
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (QApplication, QGraphicsItem, QGraphicsPathItem,
                             QGraphicsScene, QGraphicsView, QRubberBand)

from ..core.clipboard import copy_fragment, fragment_summary, paste_fragment
from .canvas_nav import CanvasNavigation
from ..core.model import State, StateKind, Transition
from .diagram_items import (GRID, GhostItem, StateItem, TransitionItem,
                            snap)
from .document import Document
from .theme import C


#: How far a pasted fragment is offset from the original (px). Landing on
#: top of it, the user could not see that a copy had been made.
PASTE_OFFSET = 30.0


class Tool(str, Enum):
    SELECT = "select"
    STATE = "state"
    COMPOSITE = "composite"
    INITIAL = "initial"
    FINAL = "final"
    CHOICE = "choice"
    JUNCTION = "junction"
    SHALLOW_HISTORY = "shallow_history"
    DEEP_HISTORY = "deep_history"
    TERMINATE = "terminate"
    FORK = "fork"
    JOIN = "join"
    ENTRY_POINT = "entry_point"
    EXIT_POINT = "exit_point"
    SUBMACHINE = "submachine"
    TRANSITION = "transition"


_NEW_STATE_DEFAULTS = {
    Tool.STATE: (StateKind.SIMPLE, "State", 170.0, 84.0),
    Tool.COMPOSITE: (StateKind.COMPOSITE, "Group", 320.0, 200.0),
    Tool.INITIAL: (StateKind.INITIAL, "Start", 24.0, 24.0),
    Tool.FINAL: (StateKind.FINAL, "Final", 30.0, 30.0),
    Tool.CHOICE: (StateKind.CHOICE, "Choice", 38.0, 38.0),
    Tool.JUNCTION: (StateKind.JUNCTION, "Junction", 22.0, 22.0),
    Tool.SHALLOW_HISTORY: (StateKind.SHALLOW_HISTORY, "History", 32.0, 32.0),
    Tool.DEEP_HISTORY: (StateKind.DEEP_HISTORY, "DeepHistory", 32.0, 32.0),
    Tool.TERMINATE: (StateKind.TERMINATE, "Terminate", 30.0, 30.0),
    # A fork and a join are drawn in UML as A THICK BAR; the default size is a
    # vertical bar (because the regions are horizontal bands).
    Tool.FORK: (StateKind.FORK, "Fork", 10.0, 90.0),
    Tool.JOIN: (StateKind.JOIN, "Join", 10.0, 90.0),
    # The connection points sit ON THE BORDER of a composite state; small circles.
    Tool.ENTRY_POINT: (StateKind.ENTRY_POINT, "EntryPoint", 18.0, 18.0),
    Tool.EXIT_POINT: (StateKind.EXIT_POINT, "ExitPoint", 18.0, 18.0),
    Tool.SUBMACHINE: (StateKind.SUBMACHINE, "Submachine", 220.0, 96.0),
}


class DiagramCanvas(CanvasNavigation, QGraphicsView):
    """The bridge between the model and the graphics scene."""

    #: The smallest movement that starts a left-button bend (scene px).
    #: Anything below that is a SELECTION click, not a bend.
    BEND_THRESHOLD = 4.0
    #: The SCREEN radius within which an existing waypoint counts as "grabbed".
    BEND_GRAB_PX = 9.0

    selection_changed = pyqtSignal(list)     # the ids of the selected elements
    tool_finished = pyqtSignal()             # a tool was used -> go back to Select
    status_message = pyqtSignal(str)

    def __init__(self, doc: Document, parent=None) -> None:
        super().__init__(parent)
        self.doc = doc
        self.tool = Tool.SELECT
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
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setMouseTracking(True)

        self.state_items: Dict[str, StateItem] = {}
        self.tran_items: Dict[str, TransitionItem] = {}

        self._pending_source: Optional[StateItem] = None
        #: The left button is down but has not passed the threshold (BEND_THRESHOLD).
        self._bend_candidate = None
        self._rubber_line: Optional[QGraphicsPathItem] = None
        self._pre_drag: Optional[str] = None
        self._panning = False
        self._pan_origin = QPointF()
        self._suppress_selection = False
        self._init_navigation()

        #: The arrow-bending state (right button directly, left button after the threshold).
        self._bend_item = None
        self._bend_before = None
        self._bend_origin = QPointF()
        #: The index of the waypoint BEING MOVED during a drag.
        #: None means it has not been added yet (it is added once the threshold is passed).
        self._bend_index = None
        #: The (mode, index) pair computed when a handle is caught.
        self._bend_grab = None
        #: LABEL drag: (transition item, starting dx, dy, mouse).
        #: The label is kept SEPARATE from the arrow itself; the user must be able
        #: to slide the label somewhere readable.
        self._label_drag = None
        #: DIFF mode: {"added": ..., "removed": ..., "changed": ...}
        self._diff_marks = {}
        #: The ghost drawings of deleted elements (THEY DO NOT BELONG TO THE MODEL).
        self._ghosts = []
        #: Rectangular selection with the right button. Qt's own RubberBandDrag works
        #: with the LEFT button ONLY, so we draw our own band.
        self._band = QRubberBand(QRubberBand.Shape.Rectangle, self.viewport())
        self._band_origin = QPoint()

        self._scene.selectionChanged.connect(self._on_selection_changed)

    # set-up

    def rebuild(self) -> None:
        """Rebuilds the scene from the model (the selection is kept)."""
        selected = self.selected_ids()
        self._suppress_selection = True
        self._cancel_transition()

        self._scene.clear()
        # The scene was cleared: the ghost references have to be dropped too, or
        # deleted C++ objects would be accessed.
        self._ghosts = []
        self.state_items.clear()
        self.tran_items.clear()

        sm = self.doc.machine
        # Parent states first, then substates (the parent must exist first).
        for st in sm.ordered_states():
            item = StateItem(st, self)
            self.state_items[st.id] = item
            if st.parent and st.parent in self.state_items:
                item.setParentItem(self.state_items[st.parent])
                item.setPos(st.x, st.y)
            else:
                self._scene.addItem(item)
                item.setPos(st.x, st.y)

        routes = self._compute_routes()
        for tr in sm.ordered_transitions():
            src = self.state_items.get(tr.source)
            dst = self.state_items.get(tr.target)
            if src is None or dst is None:
                continue        # an unbound transition is not drawn (the validator warns about it)
            bow, label_t = routes.get(tr.id, (0.0, 0.5))
            item = TransitionItem(tr, src, dst, self, bow, label_t)
            self.tran_items[tr.id] = item
            self._scene.addItem(item)

        # REREAD THE REGIONS FROM THE DRAWING.
        #
        # The region is read from the BAND the item sits in, and the band boundaries
        # come out of the INNER AREA of the composite state -- and that area CAN
        # SHIFT in two ways:
        #
        #     * Writing entry/exit/do text into the composite state grows the
        #         behaviour strip at the top (see StateItem.content_rect).
        #     * Raising the region count splits the same area into more bands.
        #
        # In both cases the COORDINATES of the items do not change but the band
        # they fall into does. The model kept carrying the old number and the
        # validator said nothing: the generated `state_region[]` table CONTRADICTED
        # the picture the user saw on screen -- most of all for a user raising the
        # region count from 1 to 2, where every item below the middle line was
        # drawn in the second band while staying in the first region.
        # bolgede kaliyordu.
        #
        # Because the drawing is a pure function of the model, this reread is
        # deterministic and changes nothing when it repeats itself.
        self._sync_regions_from_drawing()

        self._update_scene_rect()
        self._suppress_selection = False
        self.set_selected_ids(selected)
        if self._diff_marks:
            # Rebuilt items lose their marks.
            self.set_diff_marks(self._diff_marks)

    def _compute_routes(self):
        """Separates the transitions between the same pair.

        They have to be separated on two axes: `bow` opens the curve sideways
        while `label_t` slides the label along the path. With only one of them,
        long labels still overlap.
        """
        groups: Dict[frozenset, List[Transition]] = {}
        for tr in self.doc.machine.ordered_transitions():
            if tr.source == tr.target:
                continue
            groups.setdefault(frozenset((tr.source, tr.target)), []).append(tr)

        routes: Dict[str, tuple] = {}
        for pair, items in groups.items():
            if len(items) == 1:
                routes[items[0].id] = (0.0, 0.5)
                continue
            step = 52.0
            start = -(len(items) - 1) / 2.0
            spread = 0.34 / max(1, len(items) - 1)
            for i, tr in enumerate(items):
                sign = 1.0 if tr.source == sorted(pair)[0] else -1.0
                routes[tr.id] = ((start + i) * step * sign,
                                 0.5 + (start + i) * spread * sign)
        return routes

    def _update_scene_rect(self) -> None:
        rect = self._scene.itemsBoundingRect()
        if rect.isNull():
            rect = QRectF(0, 0, 800, 600)
        self._set_scene_rect(rect.adjusted(-400, -300, 400, 300))

    def retheme(self) -> None:
        """Rereads the scene background on a theme change.

        Because the scene background is converted to a QColor AT SET-UP time, it
        is not updated by itself when ``C`` changes; the item brushes and pens
        are rebuilt by rebuild() anyway.
        """
        self._scene.setBackgroundBrush(QColor(C.CANVAS_BG))

    def refresh_transitions(self) -> None:
        for item in self.tran_items.values():
            item.update_path()

    def mark_errors(self, error_ids: Set[str], warning_ids: Set[str]) -> None:
        for eid, item in self.state_items.items():
            flag = eid in error_ids
            if item.has_error != flag:
                item.has_error = flag
                item.update()
        for tid, item in self.tran_items.items():
            flag = tid in error_ids
            if item.has_error != flag:
                item.has_error = flag
                item.update()

    def set_diff_marks(self, marks: Optional[dict]) -> None:
        """Paints the diagram in DIFF mode (added / changed / deleted).

        When the user clicks a model in the workspace, they want the answer to
        "what changed" ON THE DIAGRAM, not as TEXT. Added and changed elements
        are painted in place; DELETED elements are not in the new model, so they
        are drawn as GHOSTS at their positions in the old version (see
        diagram_items.GhostItem).

        With `marks` set to None, diff mode is switched off.
        """
        self._diff_marks = marks or {}
        added = self._diff_marks.get("added") or set()
        modified = self._diff_marks.get("changed") or set()

        for ident, item in list(self.state_items.items()) \
                + list(self.tran_items.items()):
            if ident in added:
                new = "added"
            elif ident in modified:
                new = "changed"
            else:
                new = ""
            if getattr(item, "diff_mark", "") != new:
                item.diff_mark = new
                item.update()

        self._rebuild_ghosts(self._diff_marks.get("removed") or {})

    def _rebuild_ghosts(self, removed: dict) -> None:
        """Draws deleted elements as ghosts at their old positions."""
        for item in self._ghosts:
            if item.scene() is self._scene:
                self._scene.removeItem(item)
        self._ghosts = []
        if not removed:
            return
        for ident, data in removed.items():
            # Only elements that HAVE a position can be drawn; transitions and
            # relationships hang off their end points (and the ends may have been
            # deleted too), so they stay in the list as TEXT.
            if "x" not in data or "y" not in data:
                continue
            ghost = GhostItem(data)
            self._scene.addItem(ghost)
            self._ghosts.append(ghost)

    def set_active_states(self, ids: List[str]) -> None:
        """Highlights the active state chain of the simulation."""
        active = set(ids)
        for sid, item in self.state_items.items():
            flag = sid in active
            if item.is_active != flag:
                item.is_active = flag
                item.update()

    # selection

    def selected_ids(self) -> List[str]:
        out: List[str] = []
        for item in self._scene.selectedItems():
            if isinstance(item, StateItem):
                out.append(item.state.id)
            elif isinstance(item, TransitionItem):
                out.append(item.transition.id)
        return out

    def set_selected_ids(self, ids: List[str]) -> None:
        self._suppress_selection = True
        self._scene.clearSelection()
        for eid in ids:
            item = self.state_items.get(eid) or self.tran_items.get(eid)
            if item is not None:
                item.setSelected(True)
        self._suppress_selection = False
        self._on_selection_changed()

    def focus_element(self, eid: str) -> None:
        item = self.state_items.get(eid) or self.tran_items.get(eid)
        if item is None:
            return
        self.set_selected_ids([eid])
        self.ensureVisible(item, 80, 80)

    def edit_element(self, eid: str) -> None:
        """Double click: opens the property dialog and applies the change."""
        from .dialogs import StateDialog, TransitionDialog
        sm = self.doc.machine

        st = sm.states.get(eid)
        if st is not None:
            dlg = StateDialog(st, self)
            if dlg.exec():
                def mutate(m):
                    dlg.apply_to(m.states[eid])
                self.doc.edit("Edit '%s'" % st.name, mutate)
            return

        tr = sm.transitions.get(eid)
        if tr is not None:
            src = sm.states.get(tr.source)
            dst = sm.states.get(tr.target)
            dlg = TransitionDialog(tr, src.name if src else "?",
                                   dst.name if dst else "?", sm.events(), self)
            if dlg.exec():
                def mutate(m):
                    dlg.apply_to(m.transitions[eid])
                self.doc.edit("Edit transition", mutate)

    def _on_selection_changed(self) -> None:
        if not self._suppress_selection:
            self.selection_changed.emit(self.selected_ids())

    # tools

    def set_tool(self, tool: Tool) -> None:
        self.tool = tool
        self._cancel_transition()
        if tool is Tool.SELECT:
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.viewport().setCursor(Qt.CursorShape.CrossCursor)

    def _cancel_transition(self) -> None:
        self._pending_source = None
        if self._rubber_line is not None:
            if self._rubber_line.scene() is not None:
                self._scene.removeItem(self._rubber_line)
            self._rubber_line = None

    # mouse

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_origin = event.position()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return

        scene_pos = self.mapToScene(event.position().toPoint())

        # ---------------------------------------------------------- right button --
        #
        # The right button does TWO things, told apart by the target:
        #     * over a TRANSITION  -> BEND the arrow (drag a waypoint)
        #     * over empty space   -> rectangular SELECTION
        # The distinction is made by looking at the item under the click, so the two
        # functions do not eat each other.
        if event.button() == Qt.MouseButton.RightButton:
            tran = self._transition_at(scene_pos)
            if tran is not None:
                self._bend_item = tran
                self._bend_before = self.doc.machine.to_json()
                self._bend_origin = scene_pos
                self._bend_grab = tran.grab_at(scene_pos, self._grab_radius())
                self._bend_index = None
                self.viewport().setCursor(Qt.CursorShape.SizeAllCursor)
            else:
                self._band_origin = event.position().toPoint()
                self._band.setGeometry(QRect(self._band_origin, QSize()))
                self._band.show()
            event.accept()
            return

        if self.tool is Tool.TRANSITION and event.button() == Qt.MouseButton.LeftButton:
            self._handle_transition_click(scene_pos)
            event.accept()
            return

        if self.tool in _NEW_STATE_DEFAULTS and event.button() == Qt.MouseButton.LeftButton:
            self._place_state(scene_pos)
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton:
            # AN ARROW CAN BE BENT WITH THE LEFT BUTTON TOO.
            #
            # Bending was on the RIGHT button only; the user reported that they could
            # not "grab and drag" the arrow -- because nobody tries to shape an arrow by
            # dragging it with the right button.
            #
            # But a left click IS ALSO a selection: starting to bend the moment it goes
            # down would make it impossible to click a transition to see its properties.
            # So only a CANDIDATE is recorded here; the bend starts if the cursor passes
            # the threshold (see mouseMoveEvent). Released without moving, the click
            # stays a plain SELECTION.
            tran = self._transition_at(scene_pos)
            if tran is not None and self.tool is Tool.SELECT:
                # THE LABEL FIRST. A drag starting over the label does NOT BEND the arrow,
                # it MOVES the label; otherwise grabbing the label was impossible (the label
                # sits on the arrow).
                if tran.label_at(scene_pos):
                    # CLICKING THE LABEL SELECTS THE TRANSITION TOO.
                    #
                    # This branch used to return with `event.accept()`, so
                    # `super().mousePressEvent()` never ran and Qt could not make the
                    # selection: clicking the label left the properties panel empty and Del
                    # deleted nothing. The label is part of the transition; clicking it must
                    # select the transition. With Shift held it is ADDED to the selection.
                    # 
                    if not (event.modifiers()
                            & Qt.KeyboardModifier.ShiftModifier):
                        self._scene.clearSelection()
                    tran.setSelected(True)
                    self._label_drag = (tran, tran.transition.label_dx,
                                        tran.transition.label_dy, scene_pos)
                    self._pre_drag = self.doc.machine.to_json()
                    self.viewport().setCursor(Qt.CursorShape.SizeAllCursor)
                    event.accept()
                    return
                self._bend_candidate = tran
                self._bend_origin = scene_pos
                self._bend_grab = tran.grab_at(scene_pos, self._grab_radius())
                self._bend_index = None
            self._pre_drag = self.doc.machine.to_json()
        super().mousePressEvent(event)

    def _grab_radius(self) -> float:
        """Converts the handle radius into SCENE units.

        A fixed scene radius would grow and shrink with the zoom: grabbing the
        point would be impossible when zoomed out and every click would count as
        grabbing one when zoomed in. Dividing by the scale keeps it fixed ON SCREEN.
        """
        scale = self.transform().m11() or 1.0
        return self.BEND_GRAB_PX / abs(scale)

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

        if self._label_drag is not None:
            elem, dx0, dy0, begin = self._label_drag
            now = self.mapToScene(event.position().toPoint())
            shift = now - begin
            elem.transition.label_dx = round(dx0 + shift.x(), 2)
            elem.transition.label_dy = round(dy0 + shift.y(), 2)
            elem.update_path()
            self._update_autoscroll(event.position().toPoint())
            event.accept()
            return

        # CANDIDATE -> REAL bend: start once the cursor passes the threshold.
        #
        # Without the threshold every selection click counted as a tiny bend and the
        # arrow drifted unnoticed.
        if self._bend_item is None and self._bend_candidate is not None:
            now = self.mapToScene(event.position().toPoint())
            shift = now - self._bend_origin
            if abs(shift.x()) >= self.BEND_THRESHOLD \
                    or abs(shift.y()) >= self.BEND_THRESHOLD:
                self._bend_item = self._bend_candidate
                self._bend_candidate = None
                self._bend_before = self.doc.machine.to_json()
                self.viewport().setCursor(Qt.CursorShape.SizeAllCursor)

        if self._bend_item is not None:
            # MULTI-POINT ROUTING.
            #
            # The previous version replaced every waypoint with a SINGLE point wherever
            # it was pulled from; an arrow could only ever be one "V" and adding a
            # second bend was impossible. Now the place grabbed matters: over an
            # existing point THAT POINT is moved, otherwise a new point is inserted into
            # the grabbed SEGMENT and that one is moved. So the arrow can be shaped with
            # as many bends as wanted, pulled from any of its points.
            # 
            pos = self.mapToScene(event.position().toPoint())
            if self.snap_enabled:
                pos = QPointF(snap(pos.x()), snap(pos.y()))
            points = self._bend_item.transition.waypoints

            if self._bend_index is None:
                mode, index = self._bend_grab or ("insert", 0)
                if mode == "move" and index < len(points):
                    self._bend_index = index
                else:
                    index = max(0, min(index, len(points)))
                    points.insert(index, [round(pos.x(), 2),
                                            round(pos.y(), 2)])
                    self._bend_index = index

            points[self._bend_index] = [round(pos.x(), 2), round(pos.y(), 2)]
            self._bend_item.update_path()
            # Scroll from the edge while bending an arrow too: shaping a long arrow
            # towards the outside of the screen was otherwise impossible.
            self._room_for_drag(event.position().toPoint())
            self._update_autoscroll(event.position().toPoint())
            event.accept()
            return

        if self._band.isVisible():
            self._band.setGeometry(
                QRect(self._band_origin, event.position().toPoint()).normalized())
            event.accept()
            return

        if self._pending_source is not None and self._rubber_line is not None:
            from PyQt6.QtGui import QPainterPath
            start = self._pending_source.scene_center()
            end = self.mapToScene(event.position().toPoint())
            path = QPainterPath(start)
            path.lineTo(end)
            self._rubber_line.setPath(path)

        dragging = bool(event.buttons() & Qt.MouseButton.LeftButton)
        if dragging:
            # Grow the scene FIRST: the room should be ready while super() moves the
            # item, or the item stops at the scene boundary and jumps on release.
            self._room_for_drag(event.position().toPoint())

        super().mouseMoveEvent(event)

        if dragging:
            self._update_autoscroll(event.position().toPoint())
        else:
            self._stop_autoscroll()

    def mouseReleaseEvent(self, event) -> None:
        # The mouse was released: edge scrolling stops in EVERY case. There are
        # several early returns below; leaving the stop to them would leave the
        # timer running.
        self._stop_autoscroll()
        if self._panning and event.button() == Qt.MouseButton.MiddleButton:
            self._panning = False
            self.viewport().setCursor(
                Qt.CursorShape.ArrowCursor if self.tool is Tool.SELECT
                else Qt.CursorShape.CrossCursor)
            event.accept()
            return

        if self._label_drag is not None:
            self._label_drag = None
            self.viewport().setCursor(
                Qt.CursorShape.ArrowCursor if self.tool is Tool.SELECT
                else Qt.CursorShape.CrossCursor)
            if self.doc.edit_from("Move label", self._pre_drag):
                self.status_message.emit("Transition label moved.")
            event.accept()
            return

        # A bend CANDIDATE started with the left button, released without moving at
        # all, was just a selection click: drop it without a trace.
        self._bend_candidate = None
        if self._bend_item is None:
            self._bend_grab = None
            self._bend_index = None

        if self._bend_item is not None and event.button() in (
                Qt.MouseButton.RightButton, Qt.MouseButton.LeftButton):
            item, before = self._bend_item, self._bend_before
            moving = (self.mapToScene(event.position().toPoint())
                      - self._bend_origin)
            self._bend_item = None
            self._bend_before = None
            self.viewport().setCursor(
                Qt.CursorShape.ArrowCursor if self.tool is Tool.SELECT
                else Qt.CursorShape.CrossCursor)

            grab = self._bend_grab
            self._bend_grab = None
            self._bend_index = None

            # Releasing WITHOUT DRAGGING STRAIGHTENS the arrow -- but ONLY on the right
            # button. On the left button the same behaviour would erase the bend on
            # every click made to select the transition. (On the left button we never
            # get here without passing the threshold anyway.)
            idle = (event.button() == Qt.MouseButton.RightButton
                      and abs(moving.x()) < 3.0 and abs(moving.y()) < 3.0)
            if idle and grab and grab[0] == "move" \
                    and grab[1] < len(item.transition.waypoints):
                # A right click ON A POINT: remove only that point. Straightening the whole
                # arrow should not be necessary to delete a single bend.
                # gerekmemeli.
                item.transition.waypoints.pop(grab[1])
                item.update_path()
                self.status_message.emit("Bend point removed.")
            elif idle:
                item.transition.waypoints = []
                item.update_path()
                self.status_message.emit("Transition straightened.")
            else:
                self.status_message.emit(
                    "Transition reshaped (%d bend point(s))."
                    % len(item.transition.waypoints))
            self.doc.edit_from("Reshape transition", before)
            event.accept()
            return

        # Transitions by DRAG AND DROP. Both usages are supported:
        #     * click-click : click the source, click the target
        #     * drag        : hold down on the source, release on the target
        # The distinction is that the release point is a DIFFERENT state from the
        # SOURCE; otherwise the FIRST release of the click-click flow would complete
        # the transition immediately and the user could never pick the target.
        if (event.button() == Qt.MouseButton.LeftButton
                and self.tool is Tool.TRANSITION
                and self._pending_source is not None):
            scene_pos = self.mapToScene(event.position().toPoint())
            target = self._state_at(scene_pos)
            if target is not None and target is not self._pending_source:
                self._handle_transition_click(scene_pos)
                event.accept()
                return

        if event.button() == Qt.MouseButton.RightButton and self._band.isVisible():
            rect = self._band.geometry()
            self._band.hide()
            # A very small rectangle = an accidental click; do not disturb the selection.
            if rect.width() > 3 and rect.height() > 3:
                path_rect = self.mapToScene(rect).boundingRect()
                selected = [i for i in self._scene.items(path_rect)
                          if isinstance(i, StateItem)]
                if not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                    self._scene.clearSelection()
                for it in selected:
                    it.setSelected(True)
                self.status_message.emit("%d element(s) selected." % len(selected))
            event.accept()
            return

        super().mouseReleaseEvent(event)
        # The drag finished: the guides go away.
        self.align_clear()

        if self._pre_drag is not None and self.tool is Tool.SELECT:
            snapshot = self._pre_drag
            self._pre_drag = None
            reparented = self._apply_reparenting()
            self._write_geometry_to_model(skip=reparented)
            if self.doc.machine.to_json() != snapshot:
                self.doc.edit_from("Move / resize", snapshot)

    def commit_geometry(self, label: str) -> None:
        """Called by StateItem when a resize finishes."""
        snapshot = self._pre_drag
        self._pre_drag = None
        self._write_geometry_to_model()
        if snapshot is not None and self.doc.machine.to_json() != snapshot:
            self.doc.edit_from(label, snapshot)

    def _write_geometry_to_model(self, skip: Optional[Set[str]] = None) -> None:
        """Writes the positions on the scene back into the model.

        ``skip``: the elements whose PARENT CHANGED in this operation. Their
        coordinates were computed RELATIVE TO the new parent by
        `_apply_reparenting`, while `item.pos()` is still the old (scene)
        position, because the graphics tree is only updated on the next redraw.
        Overwritten, the element would be drawn outside its parent.
        """
        skipped = skip or set()
        offsets = {}
        for eid, item in self.state_items.items():
            if eid in skipped:
                continue
            dx = round(item.pos().x(), 2) - round(item.state.x, 2)
            dy = round(item.pos().y(), 2) - round(item.state.y, 2)
            if dx or dy:
                offsets[eid] = (dx, dy)
            item.state.x = round(item.pos().x(), 2)
            item.state.y = round(item.pos().y(), 2)
            item.state.w = round(item.state.w, 2)
            item.state.h = round(item.state.h, 2)
            self._assign_region(item)
        if offsets:
            self._shift_waypoints(offsets)

    def _shift_waypoints(self, offsets) -> None:
        """Moves the WAYPOINTS of a subpicture that is being moved rigidly.

        Transition waypoints are stored in ABSOLUTE SCENE coordinates while
        substates are stored RELATIVE to their parents. When a composite state is
        dragged its children go with it but the waypoints stay put: the path the
        user shaped by hand hangs outside the box -- and IS SAVED that way.
        Reopening the file does not fix it either.

        Only transitions whose BOTH ENDS shift BY THE SAME AMOUNT are moved --
        that is, the whole of that part of the picture moved and the shape of the
        path keeps its meaning. On a transition with only one end shifting, the
        waypoints are where the user placed them on the scene; touching them
        would break a hand-made routing.
        """
        sm = self.doc.machine

        def scene_shift(sid):
            """The total shift of an item ON THE SCENE (itself + every parent)."""
            dx = dy = 0.0
            cur = sid
            seen = set()
            while cur is not None and cur not in seen:
                seen.add(cur)
                step = offsets.get(cur)
                if step is not None:
                    dx += step[0]
                    dy += step[1]
                st = sm.states.get(cur)
                cur = st.parent if st is not None else None
            return (round(dx, 2), round(dy, 2))

        cache = {}
        for tr in sm.transitions.values():
            if not tr.waypoints:
                continue
            for uc in (tr.source, tr.target):
                if uc not in cache:
                    cache[uc] = scene_shift(uc)
            offset = cache[tr.source]
            if offset != cache[tr.target] or offset == (0.0, 0.0):
                continue
            tr.waypoints = [[round(x + offset[0], 2), round(y + offset[1], 2)]
                            for x, y in tr.waypoints]

    @staticmethod
    def _region_for(upper: Optional[QGraphicsItem],
                    local_top_edge: float, height: float) -> int:
        """Computes WHICH BAND of `parent` was landed in, from LOCAL coordinates.

        The position is read from the local coordinate passed in, NOT from
        `item.pos()`. The reason: when an item has just been moved to a new
        parent, or has just been created, the graphics tree is not up to date --
        `pos()` gives the old scene position and `parentItem()` the old parent.
        The region computed at those moments came out wrong.
        """
        if not isinstance(upper, StateItem) or upper.region_count() <= 1:
            return 0
        return upper.region_at(local_top_edge + height / 2.0)

    def _fit_pasted_into(self, host: StateItem, ids: List[str],
                         scene_pos: QPointF) -> None:
        """Moves the pasted ROOTS to the drop point and fits them inside.

        `paste_fragment` places the roots by adding PASTE_OFFSET to THEIR OWN old
        coordinates. When the target is a composite state, that coordinate is now
        read in the LOCAL frame of the parent and the fragment usually falls
        entirely OUTSIDE the box: pasting a state sitting at y=600 in the root
        region into a composite whose inner area is y=34..290 put it three
        hundred pixels below. The user said "I pasted and nothing happened" --
        the element was there, just somewhere off screen. The region number was
        meaningless in that case too (a clamped value).

        The fragment is CENTRED on the cursor point and clamped to the inner
        area of the parent; its own internal layout is preserved.
        """
        sm = self.doc.machine
        roots = [sm.states[i] for i in ids
                  if i in sm.states and sm.states[i].parent == host.state.id]
        if not roots:
            return
        left_x = min(st.x for st in roots)
        upper = min(st.y for st in roots)
        width = max(st.x + st.w for st in roots) - left_x
        height = max(st.y + st.h for st in roots) - upper

        field = host.content_rect()
        local_pt = host.mapFromScene(scene_pos)
        target_x = local_pt.x() - width / 2.0
        target_y = local_pt.y() - height / 2.0
        target_x = min(max(target_x, field.left()),
                      max(field.left(), field.right() - width))
        target_y = min(max(target_y, field.top()),
                      max(field.top(), field.bottom() - height))

        dx = target_x - left_x
        dy = target_y - upper
        for st in roots:
            st.x = round(st.x + dx, 2)
            st.y = round(st.y + dy, 2)

    def _sync_regions_from_drawing(self) -> bool:
        """Rereads the region of every item from the BAND IT IS DRAWN IN.

        :return: True when something changed in the model
        """
        was_changed = False
        for item in self.state_items.values():
            new = self._region_for(item.parentItem(), item.pos().y(),
                                    item.state.h)
            if int(item.state.region or 0) != new:
                item.state.region = new
                was_changed = True
        return was_changed

    def _assign_region(self, item: StateItem) -> None:
        """Marks the item with WHICH region of the parent state it sits in.

        The region is READ from the band visible on the diagram: whichever band
        the user drops the item into is the region it joins. With a separate
        "pick a region" field, the picture and the model could drift apart and
        the generated code would do something the drawing does not say.
        """
        item.state.region = self._region_for(
            item.parentItem(), item.pos().y(), item.state.h)

    def _apply_reparenting(self) -> Set[str]:
        """Updates the hierarchy when a state is dropped onto a composite state.

        :return: ids of the elements whose parent changed and were placed here
        """
        changed = False
        moved: Set[str] = set()
        for item in list(self._scene.selectedItems()):
            if not isinstance(item, StateItem):
                continue
            target = self._composite_at(item.scene_center(), exclude=item)
            new_parent = target.state.id if target is not None else None
            if new_parent == item.state.parent:
                continue
            scene_tl = item.mapToScene(QPointF(0.0, 0.0))
            if target is not None:
                local = target.mapFromScene(scene_tl)
                area = target.content_rect()
                local.setX(min(max(local.x(), area.left()), max(area.left(), area.right() - item.state.w)))
                local.setY(min(max(local.y(), area.top()), max(area.top(), area.bottom() - item.state.h)))
            else:
                local = scene_tl
            item.state.parent = new_parent
            item.state.x = round(local.x(), 2)
            item.state.y = round(local.y(), 2)
            # WRITE THE REGION HERE TOO. These items are SKIPPED by
            # `_write_geometry_to_model` (their coordinates were already computed here),
            # so this is the only place that writes their region. Skipped, a user
            # dragging an item into an orthogonal state got a state drawn in the second
            # band on the diagram but showing up in the first region of the generated
            # `state_region[]` table -- without a single warning.
            # 
            item.state.region = self._region_for(target, local.y(),
                                                 item.state.h)
            moved.add(item.state.id)
            changed = True
        if changed:
            self.status_message.emit("Hierarchy updated.")
        return moved

    def _composite_at(self, scene_pos: QPointF, exclude: StateItem) -> Optional[StateItem]:
        """The innermost composite state at the point (excluding itself/subtree)."""
        for item in self._scene.items(scene_pos):
            if not isinstance(item, StateItem):
                continue
            if item is exclude or self._is_descendant(item, exclude):
                continue
            if item.kind is StateKind.COMPOSITE:
                return item
        return None

    @staticmethod
    def _is_descendant(item: QGraphicsItem, ancestor: QGraphicsItem) -> bool:
        cur = item.parentItem()
        while cur is not None:
            if cur is ancestor:
                return True
            cur = cur.parentItem()
        return False

    # element creation

    def _place_state(self, scene_pos: QPointF) -> None:
        kind, base, w, h = _NEW_STATE_DEFAULTS[self.tool]
        parent_item = self._composite_at_point(scene_pos)
        if parent_item is not None:
            local = parent_item.mapFromScene(scene_pos)
            parent_id = parent_item.state.id
        else:
            local = scene_pos
            parent_id = None

        x = snap(local.x() - w / 2.0, self.snap_enabled)
        y = snap(local.y() - h / 2.0, self.snap_enabled)
        name = self._unique_name(base)

        # An item dropped from the palette also joins the band it lands in. Without
        # this line EVERY new item was written into the first region: building an
        # orthogonal state from the palette was impossible, because a state dropped
        # into the second band stayed in the first region in the model.
        region = self._region_for(parent_item, y, h)
        new_state = State(name=name, kind=kind, parent=parent_id,
                          x=x, y=y, w=w, h=h, region=region)

        def mutate(sm):
            sm.add_state(new_state)

        self.doc.edit("Add '%s'" % name, mutate)
        self.status_message.emit(
            "'%s' added. Double-click to edit it." % name)
        self.tool_finished.emit()
        if self.auto_edit and kind in (StateKind.SIMPLE, StateKind.COMPOSITE,
                                       StateKind.CHOICE, StateKind.JUNCTION):
            self.edit_element(new_state.id)

    def _composite_at_point(self, scene_pos: QPointF) -> Optional[StateItem]:
        for item in self._scene.items(scene_pos):
            if isinstance(item, StateItem) and item.kind is StateKind.COMPOSITE:
                return item
        return None

    def _unique_name(self, base: str) -> str:
        used = {s.name for s in self.doc.machine.states.values()}
        i = 1
        while "%s%d" % (base, i) in used:
            i += 1
        return "%s%d" % (base, i)

    def _handle_transition_click(self, scene_pos: QPointF) -> None:
        hit = self._state_at(scene_pos)
        if hit is None:
            self._cancel_transition()
            self.status_message.emit("Transition cancelled.")
            return

        if self._pending_source is None:
            if hit.kind is StateKind.FINAL:
                self.status_message.emit(
                    "A final state cannot have outgoing transitions.")
                return
            if hit.kind is StateKind.TERMINATE:
                self.status_message.emit(
                    "A terminate pseudostate cannot have outgoing transitions.")
                return
            self._pending_source = hit
            from PyQt6.QtGui import QPainterPath
            line = QGraphicsPathItem()
            pen = QPen(QColor(C.ACCENT), 1.6, Qt.PenStyle.DashLine)
            line.setPen(pen)
            line.setZValue(200.0)
            path = QPainterPath(hit.scene_center())
            path.lineTo(hit.scene_center())
            line.setPath(path)
            self._scene.addItem(line)
            self._rubber_line = line
            self.status_message.emit("Pick the target state (Esc to cancel).")
            return

        source = self._pending_source
        self._cancel_transition()
        if hit.kind is StateKind.INITIAL:
            self.status_message.emit(
                "An initial pseudostate cannot be a transition target.")
            return
        if hit is source and hit.kind.is_pseudo:
            # A self-transition on a pseudostate: the machine would hang on that vertex.
            self.status_message.emit(
                "A pseudostate cannot have a transition to itself.")
            return

        src_id, dst_id = source.state.id, hit.state.id
        new_tran = Transition(source=src_id, target=dst_id)
        label = "Transition %s -> %s" % (source.state.name, hit.state.name)

        def mutate(sm):
            sm.add_transition(new_tran)

        self.doc.edit(label, mutate)
        self.status_message.emit(label + " added.")
        self.tool_finished.emit()

        # THE PROPERTY DIALOG opens at once. A new transition is meaningless without
        # an event/guard/effect; rather than forcing the user into a separate double
        # click, the fields are asked for directly. State placement behaves the same
        # way (see _place_state), so the two tools are consistent.
        if self.auto_edit:
            self.edit_element(new_tran.id)

    def _transition_at(self, scene_pos: QPointF) -> Optional[TransitionItem]:
        """The transition arrow at the given point (None if there is none).

        `TransitionItem.shape()` widens the arrow into a 12 px band, so the thin
        line does not have to be hit exactly.
        """
        for item in self._scene.items(scene_pos):
            if isinstance(item, TransitionItem):
                return item
        return None

    def _state_at(self, scene_pos: QPointF) -> Optional[StateItem]:
        for item in self._scene.items(scene_pos):
            if isinstance(item, StateItem):
                return item
        return None

    # keyboard

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self._cancel_transition()
            self.tool_finished.emit()
            event.accept()
            return
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selection()
            event.accept()
            return
        mods = event.modifiers()
        if mods & Qt.KeyboardModifier.ControlModifier:
            if key == Qt.Key.Key_C:
                self.copy_selection()
                event.accept()
                return
            if key == Qt.Key.Key_X:
                self.cut_selection()
                event.accept()
                return
            if key == Qt.Key.Key_V:
                self.paste_clipboard()
                event.accept()
                return

        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            step = 1.0 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else GRID
            dx = (-step if key == Qt.Key.Key_Left else
                  step if key == Qt.Key.Key_Right else 0.0)
            dy = (-step if key == Qt.Key.Key_Up else
                  step if key == Qt.Key.Key_Down else 0.0)
            self.nudge_selection(dx, dy)
            event.accept()
            return
        super().keyPressEvent(event)

    #  copy/paste

    def copy_selection(self) -> bool:
        """Puts the selected states (with their subtrees) on the SYSTEM clipboard.

        The system clipboard is used, not a variable inside the application: so
        the same fragment can be pasted into a second window and the Ctrl+C
        behaviour the user knows is preserved. The payload is JSON; because it
        carries a type signature, other text is ignored silently.
        """
        payload = copy_fragment(self.doc.machine, self.selected_ids())
        if payload is None:
            self.status_message.emit("Nothing to copy - select a state first.")
            return False
        QApplication.clipboard().setText(payload)
        n_state, n_tran = fragment_summary(payload)
        self.status_message.emit(
            "Copied %d state(s) and %d transition(s)." % (n_state, n_tran))
        return True

    def cut_selection(self) -> None:
        if self.copy_selection():
            self.delete_selection()

    def paste_clipboard(self, scene_pos: Optional[QPointF] = None) -> None:
        """Pastes the fragment on the clipboard and SELECTS WHAT WAS PASTED.

        The target parent is the composite state under the cursor; with the
        cursor over empty space it is pasted into the root region. The copy is
        offset by ::PASTE_OFFSET so it does not land exactly on the original.

        ``scene_pos``: gives the target EXPLICITLY. A call from the menu or a
        shortcut leaves it empty and the cursor is used; the tests pass it to
        paste at a specific point without moving the real mouse cursor --
        otherwise the paste path would stay UNMEASURABLE.
        """
        payload = QApplication.clipboard().text()
        if not payload.strip():
            return

        if scene_pos is not None:
            pos = QPointF(scene_pos)
        else:
            local_pt = self.viewport().mapFromGlobal(self.cursor().pos())
            if self.viewport().rect().contains(local_pt):
                pos = self.mapToScene(local_pt)
            else:
                pos = self.mapToScene(self.viewport().rect().center())

        host = self._composite_at_point(pos)
        # The id of a StateItem is `state.id`; a field called `state_id` NEVER
        # EXISTED. Pasting while the cursor was over a composite state crashed with
        # AttributeError -- and that was exactly the most common way to paste
        # (putting the fragment inside a composite state).
        parent_id = host.state.id if host is not None else None

        before = self.doc.machine.to_json()
        try:
            new = paste_fragment(self.doc.machine, payload,
                                  parent=parent_id,
                                  dx=PASTE_OFFSET, dy=PASTE_OFFSET)
        except ValueError:
            # The clipboard does not hold a fragment of ours (text from another
            # application). Ignore it silently: the user may have used Ctrl+V for
            # something else, and a warning box would be irritating.
            return
        if not new:
            return

        # POSITION first, then the region: the region is derived from the position.
        #
        # Because the undo snapshot ("before") is taken BEFORE these lines, the
        # corrections can be undone as well.
        if host is not None:
            self._fit_pasted_into(host, new, pos)
            if host.region_count() > 1:
                for sid in new:
                    st = self.doc.machine.states.get(sid)
                    if st is not None and st.parent == parent_id:
                        st.region = self._region_for(host, st.y, st.h)

        self.doc.edit_from("Paste", before)
        self.rebuild()
        self.set_selected_ids(new)
        self.status_message.emit("Pasted %d element(s)." % len(new))

    def nudge_selection(self, dx: float, dy: float) -> None:
        ids = [i for i in self.selected_ids() if i in self.doc.machine.states]
        if not ids:
            return

        def mutate(sm):
            for sid in ids:
                st = sm.states[sid]
                st.x = round(st.x + dx, 2)
                st.y = round(st.y + dy, 2)

        self.doc.edit("Nudge", mutate)

    def delete_selection(self) -> None:
        ids = self.selected_ids()
        if not ids:
            return
        sm = self.doc.machine
        state_ids = [i for i in ids if i in sm.states]
        tran_ids = [i for i in ids if i in sm.transitions]

        def mutate(machine):
            for tid in tran_ids:
                machine.remove_transition(tid)
            for sid in state_ids:
                machine.remove_state(sid)

        count = len(state_ids) + len(tran_ids)
        self.doc.edit("Delete %d elements" % count, mutate)
        self.status_message.emit("%d elements deleted." % count)

    # view

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

    # background

    def sibling_boxes(self, item: StateItem):
        """The boxes of the neighbours to align to (in the PARENT coordinates).

        Only states with the SAME parent are taken: a substate inside a composite
        state aligns to its siblings, not to the states outside -- their
        coordinate spaces are separate anyway.

        Selected items are left OUT: they move TOGETHER with the dragged item, so
        aligning them to each other is meaningless.
        """
        parent_of = item.parentItem()
        boxes = []
        for other in self.state_items.values():
            if other is item or other.parentItem() is not parent_of:
                continue
            if other.isSelected():
                continue
            boxes.append((other.pos().x(), other.pos().y(),
                            other.state.w, other.state.h))
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

        x = float(left)
        painter.setPen(minor)
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
