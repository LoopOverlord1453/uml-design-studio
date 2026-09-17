"""Class diagram canvas elements: class boxes and relationship arrows.

UML 2.5.1 notation:
  * Class: a rectangle with three compartments (name / attributes / operations)
  * <<interface>>: the stereotype plus an italic name; abstract class: italic
  * Generalization: hollow triangle arrow   * Realization: dashed + hollow
  * Composition: filled diamond (whole end)  * Aggregation: hollow diamond
  * Association: open arrow                  * Dependency: dashed + open arrow
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (QBrush, QColor, QFontMetricsF, QPainter, QPainterPath,
                         QPainterPathStroker, QPen, QPolygonF)
from PyQt6.QtWidgets import QGraphicsItem, QGraphicsObject, QStyleOptionGraphicsItem

from ..core.class_model import Relation, RelationKind, Stereotype, UmlClass
from .diagram_items import guarded_paint, snap, _polygon_hit, _unit
from .theme import C, mono_font, ui_font

MIN_W, MIN_H = 140.0, 90.0
GRIP = 14.0
HEADER_H = 30.0


class ClassItem(QGraphicsObject):
    """The element that draws a UML class and makes it draggable."""

    def __init__(self, cls: UmlClass, canvas) -> None:
        super().__init__()
        self.cls = cls
        self.canvas = canvas
        self.has_error = False
        #: DIFF mode mark: "" | "added" | "changed" (see ClassCanvas).
        self.diff_mark = ""
        self._hover = False
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
        self.setPos(cls.x, cls.y)

        self.f_title = ui_font(10)
        self.f_title.setBold(True)
        self.f_small = ui_font(7)
        self.f_member = mono_font(8)

    # ------------------------------------------------------------- geometry #

    def min_size(self):
        """The smallest box the members REQUIRE.

        The box did not grow when an attribute or operation was added to the
        class: the rows were either clipped or left outside the box.
        """
        fm = QFontMetricsF(self.f_member)
        en = QFontMetricsF(self.f_title).horizontalAdvance(self.cls.name) + 24.0
        satir = 0
        for uye in list(self.cls.attributes) + list(self.cls.operations):
            etiket = uye.label()
            en = max(en, fm.horizontalAdvance(etiket) + 24.0)
            satir += 1
        boy = 34.0 + satir * (fm.height() + 2.0) + 20.0
        return (max(MIN_W, en), max(MIN_H, boy))

    def rect(self) -> QRectF:
        """The drawing rectangle: the size in the model, but NOT SMALLER."""
        en, boy = self.min_size()
        return QRectF(0.0, 0.0, max(self.cls.w, en), max(self.cls.h, boy))

    def boundingRect(self) -> QRectF:
        return self.rect().adjusted(-8.0, -8.0, 8.0, 8.0)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addRect(self.rect())
        return path

    def scene_center(self) -> QPointF:
        return self.mapToScene(self.rect().center())

    def anchor_toward(self, target: QPointF) -> QPointF:
        poly = self.mapToScene(QPolygonF(self.rect()))
        return _polygon_hit(poly, self.scene_center(), target)

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
        if self._grip_rect().contains(event.pos()):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        else:
            self.unsetCursor()
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event) -> None:
        if (event.button() == Qt.MouseButton.LeftButton
                and self._grip_rect().contains(event.pos())):
            self._resizing = True
            self._resize_origin = event.scenePos()
            self._resize_size = (self.cls.w, self.cls.h)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._resizing:
            delta = event.scenePos() - self._resize_origin
            snap_on = self.canvas.snap_enabled
            self.prepareGeometryChange()
            en_az_w, en_az_h = self.min_size()
            self.cls.w = max(en_az_w,
                             snap(self._resize_size[0] + delta.x(), snap_on))
            self.cls.h = max(en_az_h,
                             snap(self._resize_size[1] + delta.y(), snap_on))
            self.canvas.refresh_relations()
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
        self.canvas.edit_element(self.cls.id)
        event.accept()

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange \
                and self.scene() is not None:
            pt: QPointF = value
            snap_on = self.canvas.snap_enabled
            pt = QPointF(snap(pt.x(), snap_on), snap(pt.y(), snap_on))
            return self.canvas.align_drag(
                self, pt, (self.cls.w, self.cls.h),
                self.canvas.sibling_boxes(self))
        if change == QGraphicsItem.GraphicsItemChange.ItemScenePositionHasChanged:
            self.canvas.refresh_relations()
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.setZValue(1.0 if value else 0.0)
        return super().itemChange(change, value)

    # -------------------------------------------------------------- painting #

    @guarded_paint
    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem,
              widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        selected = self.isSelected()
        c = self.cls
        r = self.rect()

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

        header_color = {Stereotype.INTERFACE: C.IFACE_HEADER,
                        Stereotype.ABSTRACT: C.ABSTRACT_HEADER}.get(
                            c.stereotype, C.CLASS_HEADER)

        # shadow + body
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 60))
        painter.drawRect(r.translated(0, 2))
        painter.setBrush(QBrush(QColor(C.CLASS_FILL)))
        painter.setPen(QPen(border, 2.0 if selected else 1.3))
        painter.drawRect(r)

        # title
        painter.save()
        painter.setClipRect(r)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(header_color)))
        painter.drawRect(QRectF(r.left(), r.top(), r.width(), HEADER_H))
        painter.restore()

        title_font = QFontMetricsF(self.f_title)
        y = r.top()
        if c.stereotype is Stereotype.INTERFACE:
            painter.setFont(self.f_small)
            painter.setPen(QPen(QColor(C.TEXT_BRIGHT)))
            painter.drawText(QRectF(r.left(), y + 2, r.width(), 11),
                             Qt.AlignmentFlag.AlignHCenter, "«interface»")
            y += 9
        f = ui_font(10)
        f.setBold(True)
        f.setItalic(c.is_abstract)
        painter.setFont(f)
        painter.setPen(QPen(QColor(C.STATE_TITLE)))
        name = title_font.elidedText(c.name, Qt.TextElideMode.ElideRight,
                                     r.width() - 12)
        painter.drawText(QRectF(r.left() + 6, y, r.width() - 12,
                                HEADER_H - (y - r.top())),
                         int(Qt.AlignmentFlag.AlignCenter), name)

        # compartment line + attributes
        painter.setPen(QPen(border, 1.0))
        painter.drawLine(QPointF(r.left(), r.top() + HEADER_H),
                         QPointF(r.right(), r.top() + HEADER_H))

        fm = QFontMetricsF(self.f_member)
        painter.setFont(self.f_member)
        line_h = fm.height() + 1.0
        y = r.top() + HEADER_H + 4.0

        attr_bottom = y
        for a in c.attributes:
            if y + line_h > r.bottom() - 4:
                break
            painter.setPen(QPen(QColor(C.CLASS_TEXT)))
            text = fm.elidedText(a.label(), Qt.TextElideMode.ElideRight,
                                 r.width() - 12)
            painter.drawText(QRectF(r.left() + 6, y, r.width() - 12, line_h),
                             int(Qt.AlignmentFlag.AlignLeft
                                 | Qt.AlignmentFlag.AlignVCenter), text)
            y += line_h
            attr_bottom = y

        # operation compartment
        sep_y = max(attr_bottom + 3.0, r.top() + HEADER_H + 4.0 + 3.0)
        if sep_y < r.bottom() - 6:
            painter.setPen(QPen(border, 1.0))
            painter.drawLine(QPointF(r.left(), sep_y), QPointF(r.right(), sep_y))
            y = sep_y + 4.0
            for o in c.operations:
                if y + line_h > r.bottom() - 2:
                    break
                fo = mono_font(8)
                fo.setItalic(o.abstract or c.is_interface)
                painter.setFont(fo)
                painter.setPen(QPen(QColor(C.CLASS_TEXT)))
                text = QFontMetricsF(fo).elidedText(
                    o.label(), Qt.TextElideMode.ElideRight, r.width() - 12)
                painter.drawText(QRectF(r.left() + 6, y, r.width() - 12, line_h),
                                 int(Qt.AlignmentFlag.AlignLeft
                                     | Qt.AlignmentFlag.AlignVCenter), text)
                y += line_h

        # resize handle
        if selected:
            g = self._grip_rect()
            painter.setPen(QPen(QColor(C.STATE_SELECTED), 1.4))
            for k in (4, 8, 12):
                painter.drawLine(QPointF(g.right() - k, g.bottom() - 2),
                                 QPointF(g.right() - 2, g.bottom() - k))


class RelationItem(QGraphicsItem):
    """Draws the relationship between two classes with its UML end."""

    ARROW = 12.0
    DIAMOND = 15.0
    TRIANGLE = 14.0

    def __init__(self, relation: Relation, src: ClassItem, dst: ClassItem,
                 canvas, bow: float = 0.0) -> None:
        super().__init__()
        self.relation = relation
        self.src = src
        self.dst = dst
        self.canvas = canvas
        self.bow = bow
        self.has_error = False
        #: DIFF mode mark (see ClassItem.diff_mark).
        self.diff_mark = ""
        self._hover = False

        self._path = QPainterPath()
        self._src_marker = QPainterPath()
        self._dst_marker = QPainterPath()
        self._src_fill: Optional[bool] = None    # None = not drawn
        self._dst_fill: Optional[bool] = None
        self._label_rect = QRectF()
        self._src_mult_pt = QPointF()
        self._dst_mult_pt = QPointF()

        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setAcceptHoverEvents(True)
        self.setZValue(50.0)
        self.font = ui_font(8)
        self.update_path()

    # -------------------------------------------------------------- geometry #

    def boundingRect(self) -> QRectF:
        extra = self.TRIANGLE + 8.0
        rect = self._path.boundingRect().adjusted(-extra, -extra, extra, extra)
        rect = rect.united(self._label_rect.adjusted(-4, -4, 4, 4))
        rect = rect.united(QRectF(self._src_mult_pt, self._src_mult_pt).adjusted(-40, -16, 40, 16))
        rect = rect.united(QRectF(self._dst_mult_pt, self._dst_mult_pt).adjusted(-40, -16, 40, 16))
        return rect

    def shape(self) -> QPainterPath:
        stroker = QPainterPathStroker()
        stroker.setWidth(12.0)
        path = stroker.createStroke(self._path)
        path.addRect(self._label_rect)
        return path

    def update_path(self) -> None:
        self.prepareGeometryChange()
        c1 = self.src.scene_center()
        c2 = self.dst.scene_center()
        mid = (c1 + c2) / 2.0
        if abs(self.bow) > 0.01:
            direction = _unit(c2 - c1)
            normal = QPointF(-direction.y(), direction.x())
            ctrl = mid + normal * self.bow
        else:
            ctrl = mid

        p1 = self.src.anchor_toward(ctrl)
        p2 = self.dst.anchor_toward(ctrl)

        kind = self.relation.kind
        # Pull the line back by the length of the end markers
        d1 = _unit(ctrl - p1)
        d2 = _unit(ctrl - p2)
        start = p1
        end = p2
        self._src_marker = QPainterPath()
        self._dst_marker = QPainterPath()
        self._src_fill = None
        self._dst_fill = None

        if kind in (RelationKind.AGGREGATION, RelationKind.COMPOSITION):
            # the diamond sits at the WHOLE (source) end
            tip = p1
            back = p1 + d1 * self.DIAMOND
            n = QPointF(-d1.y(), d1.x())
            half = self.DIAMOND * 0.38
            poly = QPolygonF([tip, tip + d1 * (self.DIAMOND / 2) + n * half,
                              back, tip + d1 * (self.DIAMOND / 2) - n * half])
            self._src_marker.addPolygon(poly)
            self._src_marker.closeSubpath()
            self._src_fill = kind is RelationKind.COMPOSITION
            start = back
        if kind in (RelationKind.GENERALIZATION, RelationKind.REALIZATION):
            tip = p2
            back = p2 + d2 * self.TRIANGLE
            n = QPointF(-d2.y(), d2.x())
            half = self.TRIANGLE * 0.55
            poly = QPolygonF([tip, back + n * half, back - n * half])
            self._dst_marker.addPolygon(poly)
            self._dst_marker.closeSubpath()
            self._dst_fill = False
            end = back
        if kind in (RelationKind.ASSOCIATION, RelationKind.DEPENDENCY):
            tip = p2
            back = p2 + d2 * self.ARROW
            n = QPointF(-d2.y(), d2.x())
            half = self.ARROW * 0.42
            self._dst_marker.moveTo(back + n * half)
            self._dst_marker.lineTo(tip)
            self._dst_marker.lineTo(back - n * half)
            # Association and Dependency use an OPEN arrow head: two lines,
            # not a closed triangle -- hence no fill.
            self._dst_fill = None
            end = tip

        path = QPainterPath(start)
        if abs(self.bow) > 0.01:
            path.quadTo(ctrl, end)
        else:
            path.lineTo(end)
        self._path = path

        self._src_mult_pt = p1 + d1 * 22.0
        self._dst_mult_pt = p2 + d2 * 22.0
        self._layout_label()
        self.update()

    def _layout_label(self) -> None:
        text = self.relation.label.strip()
        if not text:
            self._label_rect = QRectF()
            return
        fm = QFontMetricsF(self.font)
        w = fm.horizontalAdvance(text) + 10.0
        h = fm.height() + 4.0
        anchor = self._path.pointAtPercent(0.5)
        self._label_rect = QRectF(anchor.x() - w / 2.0,
                                  anchor.y() - h - 4.0, w, h)

    # ---------------------------------------------------------------- events #

    def hoverEnterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().hoverLeaveEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        self.canvas.edit_element(self.relation.id)
        event.accept()

    # -------------------------------------------------------------- painting #

    @guarded_paint
    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem,
              widget=None) -> None:
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

        width = 2.2 if selected else 1.5
        pen = QPen(color, width, Qt.PenStyle.SolidLine,
                   Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        if self.relation.kind in (RelationKind.REALIZATION,
                                  RelationKind.DEPENDENCY):
            pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self._path)

        solid = QPen(color, width, Qt.PenStyle.SolidLine,
                     Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(solid)
        if self._src_fill is not None:
            painter.setBrush(QBrush(color) if self._src_fill
                             else QBrush(QColor(C.CANVAS_BG)))
            painter.drawPath(self._src_marker)
        if not self._dst_marker.isEmpty():
            if self._dst_fill is None:
                painter.setBrush(Qt.BrushStyle.NoBrush)
            else:
                painter.setBrush(QBrush(color) if self._dst_fill
                                 else QBrush(QColor(C.CANVAS_BG)))
            painter.drawPath(self._dst_marker)

        # multiplicity labels
        painter.setFont(self.font)
        painter.setPen(QPen(QColor(C.STATE_TEXT)))
        if self.relation.source_mult:
            painter.drawText(QRectF(self._src_mult_pt.x() - 40,
                                    self._src_mult_pt.y() - 16, 80, 14),
                             Qt.AlignmentFlag.AlignCenter,
                             self.relation.source_mult)
        if self.relation.target_mult:
            painter.drawText(QRectF(self._dst_mult_pt.x() - 40,
                                    self._dst_mult_pt.y() - 16, 80, 14),
                             Qt.AlignmentFlag.AlignCenter,
                             self.relation.target_mult)

        if not self._label_rect.isEmpty():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(C.LABEL_BG)))
            painter.drawRoundedRect(self._label_rect, 4.0, 4.0)
            painter.setPen(QPen(QColor(C.STATE_TEXT)))
            painter.drawText(self._label_rect, Qt.AlignmentFlag.AlignCenter,
                             self.relation.label.strip())
