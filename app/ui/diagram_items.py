"""The graphics items on the canvas: states and transitions."""

from __future__ import annotations

import math
from typing import List, Tuple, Optional

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (QBrush, QColor, QFontMetricsF, QPainter,
                         QPainterPath, QPainterPathStroker, QPen, QPolygonF)
from PyQt6.QtWidgets import (QGraphicsItem, QGraphicsObject,
                             QStyleOptionGraphicsItem)

from ..core.model import State, StateKind, Transition, TransitionKind
from ..core.text_layout import split_lines
from .theme import C, mono_font, ui_font

GRID = 10.0
MIN_W, MIN_H = 90.0, 54.0
#: The maximum width of a transition label (px).
LABEL_MAX_W = 520.0
GRIP = 14.0
PSEUDO_SIZE = 24.0
CHOICE_SIZE = 38.0
FINAL_SIZE = 30.0


# --------------------------------------------------------------------------- #
#   Geometry helpers
# --------------------------------------------------------------------------- #

def snap(value: float, enabled: bool = True) -> float:
    return round(value / GRID) * GRID if enabled else value


def _unit(v: QPointF) -> QPointF:
    length = math.hypot(v.x(), v.y())
    if length < 1e-6:
        return QPointF(1.0, 0.0)
    return QPointF(v.x() / length, v.y() / length)


def _point_segment_distance(p: QPointF, a: QPointF, b: QPointF) -> float:
    """The distance from the point `p` to the LINE SEGMENT [a, b].

    We need the distance to the SEGMENT, not to the infinite line: otherwise a
    far-away click on the extension of the arrow would count as nearest to it.
    """
    vx, vy = b.x() - a.x(), b.y() - a.y()
    uzunluk2 = vx * vx + vy * vy
    if uzunluk2 < 1e-9:
        return math.hypot(p.x() - a.x(), p.y() - a.y())
    t = ((p.x() - a.x()) * vx + (p.y() - a.y()) * vy) / uzunluk2
    t = max(0.0, min(1.0, t))
    return math.hypot(p.x() - (a.x() + t * vx), p.y() - (a.y() + t * vy))


def guarded_paint(fn):
    """A wrapper that swallows painting exceptions.

    On a Python exception escaping paint(), Qt terminates the process SILENTLY
    (without even printing a traceback). So every paint body is guarded here:
    on an error it is logged and the item is marked with a red frame.
    """
    def wrapper(self, painter, option, widget=None):
        painter.save()
        try:
            fn(self, painter, option, widget)
        except Exception:                       # pragma: no cover - defensive
            import traceback
            traceback.print_exc()
            try:
                painter.setPen(QPen(QColor(C.STATE_ERROR), 2.0, Qt.PenStyle.DotLine))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(self.boundingRect().adjusted(1.0, 1.0, -1.0, -1.0))
            except Exception:
                pass
        finally:
            painter.restore()
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


def _polygon_hit(poly: QPolygonF, center: QPointF, toward: QPointF) -> QPointF:
    """Where the ray from `center` toward `toward` crosses the polygon edge."""
    from PyQt6.QtCore import QLineF
    ray = QLineF(center, center + _unit(toward - center) * 4000.0)
    best: Optional[QPointF] = None
    best_d = 1e18
    for i in range(poly.count()):
        edge = QLineF(poly.at(i), poly.at((i + 1) % poly.count()))
        kind, pt = ray.intersects(edge)
        if kind == QLineF.IntersectionType.BoundedIntersection:
            d = (pt - center).manhattanLength()
            if d < best_d:
                best_d, best = d, pt
    return best if best is not None else center


# --------------------------------------------------------------------------- #
#   State item
# --------------------------------------------------------------------------- #

class GhostItem(QGraphicsItem):
    """The trace of a DELETED item at its old place, in DIFF mode.

    A deleted element DOES NOT EXIST in the new model, so it cannot be drawn as
    a normal StateItem. So that the user can see "what was removed" on the
    diagram, it is drawn as a dashed red frame at the position and size it had
    in the old version.

    IT DOES NOT BELONG TO THE MODEL: it cannot be selected, moved or saved.
    """

    def __init__(self, veri: dict) -> None:
        super().__init__()
        self._ad = str(veri.get("name") or "?")
        self._w = float(veri.get("w") or 120.0)
        self._h = float(veri.get("h") or 70.0)
        self.setPos(float(veri.get("x") or 0.0), float(veri.get("y") or 0.0))
        self.setZValue(-5.0)          # BELOW the real items
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setToolTip("Removed: %s" % self._ad)
        self._font = ui_font(9)

    def boundingRect(self) -> QRectF:
        return QRectF(-4.0, -4.0, self._w + 8.0, self._h + 20.0)

    @guarded_paint
    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        renk = QColor(C.GIT_DEL)
        zemin = QColor(renk)
        zemin.setAlpha(26)
        painter.setBrush(QBrush(zemin))
        painter.setPen(QPen(renk, 1.6, Qt.PenStyle.DashLine))
        painter.drawRoundedRect(QRectF(0.0, 0.0, self._w, self._h), 9.0, 9.0)
        painter.setFont(self._font)
        painter.setPen(QPen(renk))
        painter.drawText(QRectF(0.0, self._h + 1.0, self._w, 16.0),
                         int(Qt.AlignmentFlag.AlignHCenter),
                         "− %s" % self._ad)


class StateItem(QGraphicsObject):
    """The item that draws a state / pseudostate and makes it draggable."""

    def __init__(self, state: State, canvas) -> None:
        super().__init__()
        self.state = state
        self.canvas = canvas
        self.has_error = False
        self.is_active = False     # is it in the active state chain during simulation
        #: DIFF mode mark: "" | "added" | "changed" (see canvas).
        self.diff_mark = ""
        self._resizing = False
        self._resize_origin = QPointF()
        self._resize_size = (0.0, 0.0)

        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
            | QGraphicsItem.GraphicsItemFlag.ItemSendsScenePositionChanges
        )
        self.setAcceptHoverEvents(True)
        self.setPos(state.x, state.y)
        self._hover = False

        self.f_title = ui_font(10)
        self.f_title.setBold(True)
        # The user said "the text inside the states is unreadable":
        # 8 point stayed faint at a typical zoom level.
        self.f_body = mono_font(9)

    # ------------------------------------------------------------- geometry #

    @property
    def kind(self) -> StateKind:
        return self.state.kind

    def min_size(self) -> Tuple[float, float]:
        """The smallest box the content REQUIRES (in local coordinates).

        When the user lengthened the entry/exit/do text or the name, the box did
        not grow by itself; and the canvas clipped what did not fit with
        `elidedText`. A half-visible behaviour tells whoever reads the diagram
        the wrong thing about what the state does.

        Pseudostates and the final state are fixed shapes; they are left alone.
        dokunulmaz.
        """
        if self.kind.is_pseudo or self.kind is StateKind.FINAL:
            # Fixed shapes: circle, diamond, bar... MIN_W/MIN_H are NOT APPLIED
            # here; applied, a 24x24 initial would swell to 90x54 and cover the
            # neighbouring state.
            return (self.state.w, self.state.h)
        en = QFontMetricsF(self.f_title).horizontalAdvance(self.state.name)
        en += 20.0
        satirlar = self.behavior_lines()
        if satirlar:
            fmb = QFontMetricsF(self.f_body)
            for ln in satirlar:
                en = max(en, fmb.horizontalAdvance(ln) + 20.0)
            boy = 34.0 + len(satirlar) * (fmb.height() + 1.0) + 10.0
        else:
            boy = MIN_H

        # A COMPOSITE STATE MUST ALSO COVER ITS SUBSTATES.
        #
        # Because the substates now grow with their own text, the fixed size of the
        # parent may not be enough: on a machine with a different font (the
        # application does not package JetBrains Mono) a substate overflowed the
        # parent.
        if self.kind is StateKind.COMPOSITE:
            sag = alt = 0.0
            try:
                cocuklar = list(self.childItems())
            except RuntimeError:
                # The item may have been deleted in a rebuild while the caller still
                # holds the old reference. Rather than crashing, make do with the size
                # in the model. (rect() used to read only Python fields, so this case
                # worked silently.)
                cocuklar = []
            for cocuk in cocuklar:
                if not isinstance(cocuk, StateItem):
                    continue
                r = cocuk.rect()
                sag = max(sag, cocuk.pos().x() + r.width())
                alt = max(alt, cocuk.pos().y() + r.height())
            if sag > 0.0:
                en = max(en, sag + 12.0)
                boy = max(boy, alt + 12.0)
        return (max(MIN_W, en), max(MIN_H, boy))

    def rect(self) -> QRectF:
        """The drawing rectangle: the size in the model, but NOT SMALLER.

        The model is not modified -- the file, the undo stack and the "unsaved"
        flag are untouched. The size the user gave is kept; it simply cannot go
        below what the text requires.
        """
        en, boy = self.min_size()
        return QRectF(0.0, 0.0, max(self.state.w, en),
                      max(self.state.h, boy))

    #: The pseudostates that write their name UNDER the shape (INITIAL and FINAL do not).
    NAMED_PSEUDO_KINDS = (StateKind.CHOICE, StateKind.JUNCTION,
                          StateKind.SHALLOW_HISTORY, StateKind.DEEP_HISTORY,
                          StateKind.TERMINATE, StateKind.FORK, StateKind.JOIN,
                          StateKind.ENTRY_POINT, StateKind.EXIT_POINT)

    def boundingRect(self) -> QRectF:
        kutu = self.rect().adjusted(-8.0, -8.0, 8.0, 8.0)
        if self.kind in self.NAMED_PSEUDO_KINDS:
            # _paint_pseudo_name writes the name up to 50 px left and right of the
            # shape and below it. Unless that strip is in boundingRect, Qt neither
            # repaints it (the name leaves a trail as the item moves) nor counts it
            # into itemsBoundingRect (zoom_fit and export clip the name).
            kutu = kutu.united(QRectF(self.rect().left() - 50.0,
                                      self.rect().bottom() + 2.0,
                                      self.rect().width() + 100.0, 16.0))
        return kutu

    CIRCULAR_KINDS = (StateKind.INITIAL, StateKind.FINAL, StateKind.JUNCTION,
                      StateKind.SHALLOW_HISTORY, StateKind.DEEP_HISTORY,
                      StateKind.TERMINATE)
    BAR_KINDS = (StateKind.FORK, StateKind.JOIN)
    POINT_KINDS = (StateKind.ENTRY_POINT, StateKind.EXIT_POINT)
    BRANCH_KINDS = (StateKind.CHOICE,)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        if self.kind in self.CIRCULAR_KINDS or self.kind in self.POINT_KINDS:
            path.addEllipse(self.rect())
        elif self.kind in self.BAR_KINDS:
            path.addRect(self.rect())
        elif self.kind in self.BRANCH_KINDS:
            path.addPolygon(self._diamond())
            # addPolygon DOES NOT CLOSE the polygon (unlike drawPolygon): the last
            # corner is not joined to the first and ONE EDGE of the diamond is missing.
            path.closeSubpath()
        else:
            path.addRoundedRect(self.rect(), 9.0, 9.0)
        return path

    def _halo_path(self) -> QPainterPath:
        """The simulation halo: an outline running 4 px outside the shape."""
        path = QPainterPath()
        r = self.rect().adjusted(-4.0, -4.0, 4.0, 4.0)
        if self.kind in self.CIRCULAR_KINDS or self.kind in self.POINT_KINDS:
            path.addEllipse(r)
        elif self.kind in self.BAR_KINDS:
            path.addRect(r)
        elif self.kind in self.BRANCH_KINDS:
            path.addPolygon(QPolygonF([
                QPointF(r.center().x(), r.top()),
                QPointF(r.right(), r.center().y()),
                QPointF(r.center().x(), r.bottom()),
                QPointF(r.left(), r.center().y()),
            ]))
            # UNLESS IT IS CLOSED one edge of the selection halo is not drawn -- which
            # is exactly what happened when a choice diamond was selected.
            path.closeSubpath()
        else:
            path.addRoundedRect(r, 12.0, 12.0)
        return path

    def _diamond(self) -> QPolygonF:
        r = self.rect()
        return QPolygonF([
            QPointF(r.center().x(), r.top()),
            QPointF(r.right(), r.center().y()),
            QPointF(r.center().x(), r.bottom()),
            QPointF(r.left(), r.center().y()),
        ])

    def scene_polygon(self) -> QPolygonF:
        """The outline in scene coordinates (for transition anchor points)."""
        r = self.rect()
        if self.kind in self.BRANCH_KINDS:
            local = self._diamond()
        else:
            local = QPolygonF(r)
        return self.mapToScene(local)

    def scene_center(self) -> QPointF:
        return self.mapToScene(self.rect().center())

    def anchor_toward(self, target: QPointF) -> QPointF:
        """The point where the transition line will touch this state."""
        center = self.scene_center()
        if self.kind in self.CIRCULAR_KINDS:
            radius = self.state.w / 2.0
            return center + _unit(target - center) * radius
        return _polygon_hit(self.scene_polygon(), center, target)

    def behavior_lines(self) -> List[str]:
        """The lines of the entry / exit / do behaviours to be drawn.

        A line break arrives two ways (see core/text_layout): a real Enter in a
        multi-line field, and the LINE_BREAK_MARKER valid in every field.
        Continuation lines are aligned under the heading.
        """
        out: List[str] = []
        for caption, text in (("entry / ", self.state.entry),
                              ("exit  / ", self.state.exit),
                              ("do    / ", self.state.do)):
            parts = split_lines(text)
            if not parts:
                continue
            out.append(caption + parts[0])
            pad = " " * len(caption)
            out.extend(pad + ln for ln in parts[1:])
        return out

    def behavior_strip_height(self) -> float:
        """The vertical strip the behaviour lines occupy in a composite state.

        ALL BEHAVIOURS ARE WRITTEN IN A COMPOSITE STATE TOO. The previous version
        drew only the FIRST line: the exit and do behaviours of the `Running`
        state existed in the model and IN THE GENERATED CODE but never appeared
        on the diagram. Someone looking at the diagram could not see that the
        state called `led_write(false)` on exit -- unacceptable when the
        generated code is used in critical places.

        The region of the substates starts BELOW this strip (content_rect).
        """
        if self.kind is not StateKind.COMPOSITE:
            return 0.0
        lines = self.behavior_lines()
        if not lines:
            return 0.0
        return QFontMetricsF(self.f_body).height() * len(lines) + 6.0

    def content_rect(self) -> QRectF:
        """The inner area the substates can be placed in (local coordinates)."""
        return self.rect().adjusted(10.0, 34.0 + self.behavior_strip_height(),
                                    -10.0, -10.0)

    def region_count(self) -> int:
        """The number of regions this state owns (1 when not composite)."""
        if self.kind is not StateKind.COMPOSITE:
            return 1
        return max(1, int(getattr(self.state, "regions", 1) or 1))

    def region_rect(self, index: int) -> QRectF:
        """The inner area of one region (in local coordinates).

        The regions are split into HORIZONTAL bands; that is the UML notation we
        are used to (horizontal bands separated by a dashed line).
        """
        alan = self.content_rect()
        sayi = self.region_count()
        if sayi <= 1:
            return alan
        index = max(0, min(index, sayi - 1))
        yukseklik = alan.height() / float(sayi)
        return QRectF(alan.left(), alan.top() + index * yukseklik,
                      alan.width(), yukseklik)

    def region_at(self, y: float) -> int:
        """The region the local `y` coordinate falls into."""
        alan = self.content_rect()
        sayi = self.region_count()
        if sayi <= 1 or alan.height() <= 0.0:
            return 0
        oran = (y - alan.top()) / alan.height()
        return max(0, min(int(oran * sayi), sayi - 1))

    def is_resizable(self) -> bool:
        return self.kind in (StateKind.SIMPLE, StateKind.COMPOSITE)

    def _grip_rect(self) -> QRectF:
        r = self.rect()
        return QRectF(r.right() - GRIP, r.bottom() - GRIP, GRIP, GRIP)

    # ---------------------------------------------------------------- events #

    def hoverEnterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self._hover = False
        self.unsetCursor()
        self.update()
        super().hoverLeaveEvent(event)

    def hoverMoveEvent(self, event) -> None:
        if self.is_resizable() and self._grip_rect().contains(event.pos()):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        else:
            self.unsetCursor()
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event) -> None:
        if (event.button() == Qt.MouseButton.LeftButton
                and self.is_resizable()
                and self._grip_rect().contains(event.pos())):
            self._resizing = True
            self._resize_origin = event.scenePos()
            self._resize_size = (self.state.w, self.state.h)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._resizing:
            delta = event.scenePos() - self._resize_origin
            snap_on = self.canvas.snap_enabled
            # The lower bound comes FROM THE CONTENT: if the user shrank the box
            # below the text, the text would be clipped.
            en_az_w, en_az_h = self.min_size()
            w = max(en_az_w, snap(self._resize_size[0] + delta.x(), snap_on))
            h = max(en_az_h, snap(self._resize_size[1] + delta.y(), snap_on))
            w, h = self._clamp_to_children(w, h)
            self.prepareGeometryChange()
            self.state.w, self.state.h = w, h
            self.canvas.refresh_transitions()
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._resizing:
            self._resizing = False
            self.canvas.commit_geometry("Resize")
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        self.canvas.edit_element(self.state.id)
        event.accept()

    def _clamp_to_children(self, w: float, h: float):
        """A composite state cannot be smaller than the substates inside it."""
        for child in self.childItems():
            if isinstance(child, StateItem):
                w = max(w, child.pos().x() + child.state.w + 10.0)
                h = max(h, child.pos().y() + child.state.h + 10.0)
        return w, h

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange \
                and self.scene() is not None:
            pt: QPointF = value
            snap_on = self.canvas.snap_enabled
            pt = QPointF(snap(pt.x(), snap_on), snap(pt.y(), snap_on))
            # ALIGNMENT COMES AFTER the grid: the grid rounds to 10 px while the
            # alignment seats the item on a neighbour's real edge. In the reverse
            # order the grid would immediately break the alignment just found.
            pt = self.canvas.align_drag(
                self, pt, (self.state.w, self.state.h),
                self.canvas.sibling_boxes(self))
            parent = self.parentItem()
            if isinstance(parent, StateItem):
                area = parent.content_rect()
                hizali = QPointF(pt)
                pt.setX(min(max(pt.x(), area.left()), area.right() - self.state.w))
                pt.setY(min(max(pt.y(), area.top()), area.bottom() - self.state.h))
                # If the parent clamp BROKE the alignment, the guide is removed:
                # otherwise the line would show an alignment the item does not actually
                # sit on, and lie to the user.
                if pt != hizali:
                    self.canvas.align_clear()
            return pt
        if change == QGraphicsItem.GraphicsItemChange.ItemScenePositionHasChanged:
            self.canvas.refresh_transitions()
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.setZValue(1.0 if value else 0.0)
        return super().itemChange(change, value)

    # -------------------------------------------------------------- painting #

    @guarded_paint
    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        selected = self.isSelected()

        if self.is_active:
            painter.setPen(QPen(QColor(C.SIM_ACTIVE), 3.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(self._halo_path())

        # THE SELECTION HALO.
        #
        # Selection used to be shown by the border COLOUR alone plus a 1.3 -> 2.0 px
        # width difference; the user said "you cannot tell which item is selected"
        # and they are right -- two blues side by side are indistinguishable in the
        # dark theme. A halo running outside the shape, INDEPENDENT of that shape,
        # reads equally clearly on every state kind (rectangle, circle, diamond).
        if selected and not self.is_active:
            painter.setPen(QPen(QColor(C.STATE_SELECTED), 2.6))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(self._halo_path())

        if self.has_error:
            border = QColor(C.STATE_ERROR)
        elif self.diff_mark == "added":
            border = QColor(C.GIT_ADD)
        elif self.diff_mark == "changed":
            border = QColor(C.WARN)
        elif selected:
            border = QColor(C.STATE_SELECTED)
        elif self._hover:
            border = QColor(C.STATE_HOVER)
        else:
            border = QColor(C.STATE_BORDER)

        if self.kind is StateKind.INITIAL:
            self._paint_initial(painter, border, selected)
        elif self.kind is StateKind.FINAL:
            self._paint_final(painter, border, selected)
        elif self.kind is StateKind.CHOICE:
            self._paint_choice(painter, border, selected)
        elif self.kind is StateKind.JUNCTION:
            self._paint_junction(painter, border, selected)
        elif self.kind in (StateKind.SHALLOW_HISTORY, StateKind.DEEP_HISTORY):
            self._paint_history(painter, border, selected)
        elif self.kind is StateKind.TERMINATE:
            self._paint_terminate(painter, border, selected)
        elif self.kind in self.BAR_KINDS:
            self._paint_bar(painter, border, selected)
        elif self.kind in self.POINT_KINDS:
            self._paint_connection_point(painter, border, selected)
        else:
            self._paint_state(painter, border, selected)

    # -- pseudostates

    def _paint_pseudo_name(self, p: QPainter) -> None:
        """Writes the pseudostate name under the shape."""
        p.setPen(QPen(QColor(C.STATE_TEXT)))
        p.setFont(ui_font(8))
        label = QRectF(self.rect().left() - 50, self.rect().bottom() + 2,
                       self.rect().width() + 100, 14)
        p.drawText(label, Qt.AlignmentFlag.AlignHCenter, self.state.name)

    def _paint_initial(self, p: QPainter, border: QColor, selected: bool) -> None:
        p.setPen(QPen(border, 2.0 if selected else 1.0))
        p.setBrush(QBrush(QColor(C.INITIAL_FILL)))
        p.drawEllipse(self.rect().adjusted(1, 1, -1, -1))

    def _paint_final(self, p: QPainter, border: QColor, selected: bool) -> None:
        r = self.rect().adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(C.FINAL_RING) if not selected else border,
                      2.0 if selected else 1.8))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(r)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(C.FINAL_RING)))
        p.drawEllipse(r.adjusted(5, 5, -5, -5))

    def _paint_choice(self, p: QPainter, border: QColor, selected: bool) -> None:
        """Choice: a PLAIN diamond (UML 2.5.1, 14.2.4.6).

        We used to write a question mark inside it; the specification has no such
        ornament and the diamond itself already says dynamic branching. A marker
        foreign to the standard notation stopped the output of the tool looking
        familiar to someone who reads UML.
        """
        p.setPen(QPen(border, 2.0 if selected else 1.4))
        p.setBrush(QBrush(QColor(C.CHOICE_FILL)))
        p.drawPolygon(self._diamond())
        self._paint_pseudo_name(p)

    def _paint_junction(self, p: QPainter, border: QColor, selected: bool) -> None:
        """Junction: a small FILLED circle (UML 2.5.1, 14.2.4.6).

        It used to use the SAME fill colour as a choice, and the two different
        pseudostates were told apart by shape alone (diamond / circle). The
        specification says "small black circle" for a junction -- so it uses the
        same ink as the initial pseudostate and separates at a glance from the
        amber diamond of a choice.
        """
        p.setPen(QPen(border, 2.0 if selected else 1.2))
        p.setBrush(QBrush(QColor(C.INITIAL_FILL)))
        p.drawEllipse(self.rect().adjusted(1, 1, -1, -1))
        self._paint_pseudo_name(p)

    def _paint_history(self, p: QPainter, border: QColor, selected: bool) -> None:
        r = self.rect().adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(C.HISTORY_FILL) if not selected else border,
                      2.2 if selected else 1.8))
        p.setBrush(QBrush(QColor(C.STATE_FILL)))
        p.drawEllipse(r)
        p.setPen(QPen(QColor(C.HISTORY_FILL)))
        f = ui_font(9)
        f.setBold(True)
        p.setFont(f)
        text = "H*" if self.kind is StateKind.DEEP_HISTORY else "H"
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
        self._paint_pseudo_name(p)

    def _paint_terminate(self, p: QPainter, border: QColor, selected: bool) -> None:
        r = self.rect().adjusted(4, 4, -4, -4)
        color = QColor(C.TERMINATE_FILL) if not selected else border
        p.setPen(QPen(color, 2.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(r.topLeft(), r.bottomRight())
        p.drawLine(r.topRight(), r.bottomLeft())
        self._paint_pseudo_name(p)

    def _paint_bar(self, p: QPainter, border: QColor, selected: bool) -> None:
        """FORK / JOIN: the THICK BAR notation of UML.

        UML 2.5.1, 14.2.3.7: both spread across regions or merge them; the
        notation is a thick line. A fork and a join have the same shape -- the
        difference is told by the direction of the arrows, exactly as in the
        specification.
        """
        r = self.rect()
        renk = QColor(C.TEXT_BRIGHT) if not selected else border
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(renk))
        p.drawRect(r)
        self._paint_pseudo_name(p)

    def _paint_connection_point(self, p: QPainter, border: QColor,
                                selected: bool) -> None:
        """ENTRY / EXIT POINT: a small circle sitting on the border.

        UML 2.5.1, 14.2.3.7 (printed p.313): both CLOSE the inside of a composite
        state off from the outside. An entry point is a HOLLOW circle, an exit
        point a circle marked with a CROSS; so the two separate at a glance.
        ayrilir.
        """
        r = self.rect()
        renk = border if selected else QColor(C.STATE_BORDER)
        p.setPen(QPen(renk, 2.0))
        p.setBrush(QBrush(QColor(C.CANVAS_BG)))
        p.drawEllipse(r)
        if self.kind is StateKind.EXIT_POINT:
            ic = r.adjusted(4.0, 4.0, -4.0, -4.0)
            p.setPen(QPen(renk, 1.8, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            p.drawLine(ic.topLeft(), ic.bottomRight())
            p.drawLine(ic.topRight(), ic.bottomLeft())
        self._paint_pseudo_name(p)

    # -- real states

    def _paint_state(self, p: QPainter, border: QColor, selected: bool) -> None:
        r = self.rect()
        composite = self.kind is StateKind.COMPOSITE
        radius = 9.0

        body = QPainterPath()
        body.addRoundedRect(r, radius, radius)

        # shadow
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 55))
        shadow = QPainterPath()
        shadow.addRoundedRect(r.translated(0, 2), radius, radius)
        p.drawPath(shadow)

        p.setBrush(QBrush(QColor(C.STATE_FILL_ALT if composite else C.STATE_FILL)))
        p.setPen(QPen(border, 2.0 if selected else 1.3))
        p.drawPath(body)

        # title strip
        header_h = 26.0
        p.save()
        p.setClipPath(body)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(C.STATE_HEADER_ALT if composite
                                 else C.STATE_HEADER)))
        p.drawRect(QRectF(r.left(), r.top(), r.width(), header_h))
        p.restore()

        p.setPen(QPen(QColor(C.BORDER), 1.0))
        p.drawLine(QPointF(r.left() + 1, r.top() + header_h),
                   QPointF(r.right() - 1, r.top() + header_h))

        # SUBMACHINE STATE: the UML "two nested circles" mark and the reference.
        #
        # WITHOUT THE REFERENCE WRITTEN OUT the user only learns what the box
        # points at by opening the properties panel -- while the generated code
        # comes from exactly that file.
        if self.kind is StateKind.SUBMACHINE:
            isaret = QRectF(r.right() - 34.0, r.bottom() - 20.0, 26.0, 12.0)
            p.setPen(QPen(QColor(C.TEXT_DIM), 1.4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(isaret.left(), isaret.top(), 11.0, 11.0))
            p.drawEllipse(QRectF(isaret.left() + 11.0, isaret.top(),
                                 11.0, 11.0))
            ref = (getattr(self.state, "submachine_ref", "") or "").strip()
            if ref:
                p.setFont(ui_font(8))
                p.setPen(QPen(QColor(C.TEXT_DIM)))
                p.drawText(QRectF(r.left() + 8.0, r.bottom() - 20.0,
                                  r.width() - 46.0, 14.0),
                           int(Qt.AlignmentFlag.AlignVCenter),
                           ref.rsplit("/", 1)[-1])

        # ORTHOGONAL STATE: the regions are separated by a dashed line.
        #
        # UML 2.5.1, 14.2.3.2: a composite state may own several REGIONS and the
        # regions are active AT THE SAME TIME. Without the separator drawn, the
        # user cannot see which substate is in which region, nor understand why
        # the generated code behaves as it does.
        if composite and self.region_count() > 1:
            ayirici = QPen(QColor(C.BORDER_LIGHT), 1.0, Qt.PenStyle.DashLine)
            p.setPen(ayirici)
            for k in range(1, self.region_count()):
                bant = self.region_rect(k)
                p.drawLine(QPointF(r.left() + 6.0, bant.top()),
                           QPointF(r.right() - 6.0, bant.top()))

        # title
        p.setFont(self.f_title)
        p.setPen(QPen(QColor(C.STATE_TITLE)))
        fm = QFontMetricsF(self.f_title)
        title = fm.elidedText(self.state.name, Qt.TextElideMode.ElideRight,
                              r.width() - 16)
        p.drawText(QRectF(r.left() + 8, r.top(), r.width() - 16, header_h),
                   int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                   title)

        if composite:
            p.setPen(QPen(QColor(C.TEXT_DIM)))
            p.setFont(ui_font(7))
            p.drawText(QRectF(r.left() + 8, r.top(), r.width() - 12, header_h),
                       int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
                       "◧")

        # behaviours
        #
        # MULTI-LINE. The previous version collapsed every run of whitespace to one
        # space and squeezed each behaviour onto ONE line; an exit effect with two
        # statements was clipped in the box as "... check_temp_sensor(in..." and the
        # user could not see all of what they had written.
        #
        # A line break arrives two ways (see core/text_layout):
        #     * a real Enter in multi-line fields,
        #     * the LINE_BREAK_MARKER in every field.
        # Continuation lines are aligned under the heading.
        # A COMPOSITE STATE USES THE SAME PATH.
        #
        # Only `lines[0]` used to be drawn for a composite state; entry showed and
        # exit and do were dropped SILENTLY. The exit behaviour of the `Running`
        # state (`led_write(false);`) and its do behaviour existed in the model and
        # in the generated code but left no trace on the diagram. The diagram has to
        # be a verifiable representation of the generated code.
        lines = self.behavior_lines()
        if lines:
            p.setFont(self.f_body)
            fmb = QFontMetricsF(self.f_body)
            # FULL contrast in a composite state too: written with TEXT_DIM the
            # entry/exit/do lines stayed faint and unreadable.
            p.setPen(QPen(QColor(C.STATE_TEXT)))
            y = r.top() + header_h + 6.0
            # In a composite state the behaviour strip ENDS at the region of the
            # substates; in a simple state it can run to the end of the box.
            alt_sinir = (r.top() + 34.0 + self.behavior_strip_height()
                         if composite else r.bottom() - 4.0)
            kalan = 0
            for i, line in enumerate(lines):
                if y + fmb.height() > alt_sinir:
                    kalan = len(lines) - i
                    break
                text = fmb.elidedText(line, Qt.TextElideMode.ElideRight,
                                      r.width() - 16)
                p.drawText(QRectF(r.left() + 8, y, r.width() - 16, fmb.height()),
                           int(Qt.AlignmentFlag.AlignLeft
                               | Qt.AlignmentFlag.AlignVCenter),
                           text)
                y += fmb.height() + 1.0
            if kalan:
                # There is a line that DOES NOT FIT the box. Swallowed silently, the user
                # would not notice the behaviour is incomplete; leave a mark telling them
                # the box needs to be made bigger.
                p.setPen(QPen(QColor(C.TEXT_DIM)))
                p.drawText(QRectF(r.left() + 8, r.bottom() - fmb.height() - 3,
                                  r.width() - 16, fmb.height()),
                           int(Qt.AlignmentFlag.AlignRight
                               | Qt.AlignmentFlag.AlignVCenter),
                           "+%d" % kalan)

        # resize handle
        if selected and self.is_resizable():
            g = self._grip_rect()
            p.setPen(QPen(QColor(C.STATE_SELECTED), 1.4))
            for k in (4, 8, 12):
                p.drawLine(QPointF(g.right() - k, g.bottom() - 2),
                           QPointF(g.right() - 2, g.bottom() - k))


# --------------------------------------------------------------------------- #
#   Transition item
# --------------------------------------------------------------------------- #

class TransitionItem(QGraphicsItem):
    """Draws the arrow between two states, its label and its selection area."""

    ARROW = 11.0

    def __init__(self, transition: Transition, src: StateItem, dst: StateItem,
                 canvas, bow: float = 0.0, label_t: float = 0.5) -> None:
        super().__init__()
        self.transition = transition
        self.src = src
        self.dst = dst
        self.canvas = canvas
        self.bow = bow                # curvature, to separate parallel transitions
        self.label_t = label_t        # the position of the label along the path (0..1)
        self.has_error = False
        self._hover = False

        self._path = QPainterPath()
        self._arrow = QPolygonF()
        self._label_rect = QRectF()
        self._label = ""
        self._label_text = ""
        self._label_lines: List[str] = []
        self._label_drawn: List[str] = []
        self._bow_normal: Optional[QPointF] = None
        #: DIFF mode mark (see StateItem.diff_mark).
        self.diff_mark = ""

        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setAcceptHoverEvents(True)
        self.setZValue(50.0)
        self.font = ui_font(8)
        self.update_path()

    # -------------------------------------------------------------- geometry #

    #: The radius of a waypoint handle (in scene units).
    HANDLE = 4.0

    def boundingRect(self) -> QRectF:
        extra = self.ARROW + 6.0
        rect = self._path.boundingRect().adjusted(-extra, -extra, extra, extra)
        return rect.united(self._label_rect.adjusted(-4, -4, 4, 4))

    def shape(self) -> QPainterPath:
        stroker = QPainterPathStroker()
        stroker.setWidth(12.0)
        path = stroker.createStroke(self._path)
        path.addRect(self._label_rect)
        return path

    def update_path(self) -> None:
        self.prepareGeometryChange()
        t = self.transition

        if t.kind is TransitionKind.INTERNAL or self.src is self.dst:
            self._build_self_loop()
        elif t.waypoints:
            self._build_polyline()
        else:
            self._build_curve()

        self._label = t.label() or ("/" if t.kind is TransitionKind.INTERNAL else "")
        self._label_lines = t.label_lines() or ([self._label] if self._label else [])
        if not self._label and self.src.kind is StateKind.INITIAL:
            self._label = ""
        self._layout_label()
        self.update()

    def _build_self_loop(self) -> None:
        r = self.src.mapToScene(self.src.rect()).boundingRect()
        p1 = QPointF(r.center().x() - 22.0, r.top())
        p2 = QPointF(r.center().x() + 22.0, r.top())
        c1 = QPointF(p1.x() - 34.0, r.top() - 58.0)
        c2 = QPointF(p2.x() + 34.0, r.top() - 58.0)
        path = QPainterPath(p1)
        path.cubicTo(c1, c2, p2)
        self._path = path
        self._arrow = self._arrow_head(p2, p2 - c2)

    def _build_curve(self) -> None:
        c1 = self.src.scene_center()
        c2 = self.dst.scene_center()
        mid = (c1 + c2) / 2.0
        self._bow_normal = None
        if abs(self.bow) > 0.01:
            direction = _unit(c2 - c1)
            normal = QPointF(-direction.y(), direction.x())
            ctrl = mid + normal * self.bow
            # Put the label on the outside of the curve, so the labels of parallel
            # transitions do not overlap.
            self._bow_normal = normal * (1.0 if self.bow > 0 else -1.0)
        else:
            ctrl = mid

        p1 = self.src.anchor_toward(ctrl)
        p2 = self.dst.anchor_toward(ctrl)

        path = QPainterPath(p1)
        if abs(self.bow) > 0.01:
            path.quadTo(ctrl, p2)
            tangent = p2 - ctrl
        else:
            path.lineTo(p2)
            tangent = p2 - p1
        self._path = path
        self._arrow = self._arrow_head(p2, tangent)

    # ---------------------------------------------------- multi-point routing #

    def route_points(self) -> List[QPointF]:
        """The FULL line of the arrow: [source end, *waypoints, target end].

        The bending operations run over this: the user has to be able to pull
        the arrow from ANY of its points, not from a single waypoint.

        """
        pts = [QPointF(x, y) for x, y in self.transition.waypoints]
        if pts:
            return [self.src.anchor_toward(pts[0])] + pts \
                + [self.dst.anchor_toward(pts[-1])]
        c1 = self.src.scene_center()
        c2 = self.dst.scene_center()
        mid = (c1 + c2) / 2.0
        return [self.src.anchor_toward(mid), self.dst.anchor_toward(mid)]

    def label_at(self, pos: QPointF) -> bool:
        """Is the given scene point over THE LABEL?

        The label is kept SEPARATE from the arrow itself: the user must be able
        to slide the label off the arrow to somewhere readable (see
        Transition.label_dx / label_dy).
        """
        if self._label_rect.isEmpty():
            return False
        return self._label_rect.adjusted(-3.0, -3.0, 3.0, 3.0).contains(pos)

    def grab_at(self, pos: QPointF, radius: float):
        """What part of the arrow was grabbed at the given point?

        Returns:
          ("move", i)   -> WAYPOINT number i was grabbed (it will be moved)
          ("insert", i) -> SEGMENT number i was grabbed; a NEW point goes there

        The segment index and the waypoint index are THE SAME: because the path
        is [end, w0, w1, ..., end], segment i lies between w[i-1] and w[i] and
        the new point is inserted at exactly that index.
        """
        for i, (x, y) in enumerate(self.transition.waypoints):
            d = QPointF(x, y) - pos
            if (d.x() * d.x() + d.y() * d.y()) <= radius * radius:
                return ("move", i)

        route = self.route_points()
        best_i, best_d = 0, None
        for i in range(len(route) - 1):
            d = _point_segment_distance(pos, route[i], route[i + 1])
            if best_d is None or d < best_d:
                best_i, best_d = i, d
        return ("insert", best_i)

    def _build_polyline(self) -> None:
        pts = [QPointF(x, y) for x, y in self.transition.waypoints]
        p1 = self.src.anchor_toward(pts[0])
        p2 = self.dst.anchor_toward(pts[-1])
        path = QPainterPath(p1)
        for pt in pts:
            path.lineTo(pt)
        path.lineTo(p2)
        self._path = path
        self._arrow = self._arrow_head(p2, p2 - pts[-1])

    def _arrow_head(self, tip: QPointF, direction: QPointF) -> QPolygonF:
        d = _unit(direction)
        normal = QPointF(-d.y(), d.x())
        base = tip - d * self.ARROW
        return QPolygonF([tip,
                          base + normal * (self.ARROW * 0.42),
                          base - normal * (self.ARROW * 0.42)])

    def _layout_label(self) -> None:
        if not self._label:
            self._label_rect = QRectF()
            self._label_drawn = []
            return
        fm = QFontMetricsF(self.font)
        # A MULTI-LINE LABEL. When the user writes a line-break marker into the
        # event / guard / effect fields the label splits; each line is clipped
        # SEPARATELY and the box is sized to the widest line.
        satirlar = self._label_lines or [self._label]
        # 260 px meant about 37 characters, and even an ordinary label such as
        # "after(SETTLE_MS) [pin_is_low(ctx)]" was being clipped. The bound is wide
        # enough for a real event+guard+effect triple, while still stopping a
        # runaway text from covering the diagram.
        cizilecek = [fm.elidedText(ln, Qt.TextElideMode.ElideRight,
                                   LABEL_MAX_W)
                     for ln in satirlar]
        self._label_drawn = cizilecek
        self._label_text = cizilecek[0]
        w = max(fm.horizontalAdvance(t) for t in cizilecek) + 10.0
        h = fm.height() * len(cizilecek) + 4.0
        anchor = self._path.pointAtPercent(self.label_t)
        if self._bow_normal is not None:
            anchor += self._bow_normal * (h * 0.75)
            anchor += QPointF(self.transition.label_dx, 0.0)
        else:
            anchor += QPointF(self.transition.label_dx, self.transition.label_dy)
        self._label_rect = QRectF(anchor.x() - w / 2.0, anchor.y() - h / 2.0, w, h)

    # --------------------------------------------------------------- events  #

    def hoverEnterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().hoverLeaveEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        self.canvas.edit_element(self.transition.id)
        event.accept()

    # ------------------------------------------------------------- painting  #

    @guarded_paint
    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        selected = self.isSelected()

        if self.has_error:
            color = QColor(C.STATE_ERROR)
        elif self.diff_mark == "added":
            color = QColor(C.GIT_ADD)
        elif self.diff_mark == "changed":
            color = QColor(C.WARN)
        elif selected:
            color = QColor(C.TRANSITION_SEL)
        elif self._hover:
            color = QColor(C.TEXT_BRIGHT)
        else:
            color = QColor(C.TRANSITION)

        width = 3.0 if selected else 1.5
        # A pale, wide trace is drawn UNDER the selected arrow: on a thin line a
        # colour change alone was not visible enough.
        if selected:
            iz = QColor(color)
            iz.setAlpha(70)
            painter.setPen(QPen(iz, width + 6.0, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap,
                                Qt.PenJoinStyle.RoundJoin))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(self._path)

        pen = QPen(color, width, Qt.PenStyle.SolidLine,
                   Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        if self.transition.kind is TransitionKind.LOCAL:
            pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self._path)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(color))
        painter.drawPolygon(self._arrow)

        # THE WAYPOINT HANDLES (only on the selected arrow).
        #
        # The arrow can be shaped by pulling any of its points; unless it is
        # visible WHICH points EXIST, the user cannot grab them again, adds a new
        # one on every pull, and the arrow falls apart.
        if selected and self.transition.waypoints:
            painter.setPen(QPen(QColor(C.STATE_TITLE), 1.2))
            painter.setBrush(QBrush(color))
            for x, y in self.transition.waypoints:
                painter.drawEllipse(QPointF(x, y), self.HANDLE, self.HANDLE)

        if self._label_rect.isEmpty():
            return
        # The label background is the colour of THE CANVAS, NOT white: in the light
        # theme white boxes looked like patches "with no clear edge" on the light
        # grey canvas. The background only masks the arrow beneath it.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(C.LABEL_BG)))
        painter.drawRoundedRect(self._label_rect, 4.0, 4.0)
        if selected or self._hover:
            painter.setPen(QPen(color, 1.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(self._label_rect, 4.0, 4.0)
        painter.setFont(self.font)
        painter.setPen(QPen(QColor(C.STATE_ERROR if self.has_error
                                   else C.LABEL_TEXT)))
        painter.drawText(self._label_rect,
                         int(Qt.AlignmentFlag.AlignCenter),
                         "\n".join(self._label_drawn or [self._label_text]))
