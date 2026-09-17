"""Vector icons.

Every icon is drawn with QPainter so that no external resource file is
needed; the application stays portable in one folder and follows the theme.
"""

from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (QBrush, QColor, QIcon, QPainter, QPainterPath, QPen,
                         QPixmap, QPolygonF)

from .theme import C

SIZE = 40


def ink() -> str:
    """The foreground colour of the active theme with MOST CONTRAST to the base.

    Almost black in the light theme, almost white in the dark one. Icon bodies
    must call this AT USE time; an icon bound to a fixed colour drops to the
    same tone as the background on a theme change and becomes invisible.
    """
    return C.TEXT_BRIGHT


def _icon(draw: Callable[[QPainter], None],
          color: Optional[str] = None) -> QIcon:
    """Draws the icon.

    ``color`` CANNOT BE GIVEN AS A DEFAULT VALUE: default values are computed
    once WHEN THE MODULE IS IMPORTED and are bound permanently to the colour of
    the theme at that moment. The icons would keep the old colour even when
    regenerated; a toolbar drawn white on a light background looked empty.
    """
    pm = QPixmap(SIZE, SIZE)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(QColor(color or ink()), 2.4, Qt.PenStyle.SolidLine,
               Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    draw(p)
    p.end()
    return QIcon(pm)


# ------------------------------------------------------------------- tool icons

def select_icon() -> QIcon:
    def draw(p: QPainter):
        arrow = QPolygonF([QPointF(13, 8), QPointF(13, 30), QPointF(19, 24),
                           QPointF(23, 32), QPointF(27, 30), QPointF(23, 22),
                           QPointF(30, 21)])
        p.setBrush(QBrush(QColor(C.TEXT_BRIGHT)))
        p.drawPolygon(arrow)
    return _icon(draw)


def state_icon() -> QIcon:
    def draw(p: QPainter):
        p.setBrush(QBrush(QColor(C.STATE_FILL)))
        p.drawRoundedRect(QRectF(7, 11, 26, 18), 5, 5)
        p.drawLine(QPointF(7, 18), QPointF(33, 18))
    return _icon(draw)


def composite_icon() -> QIcon:
    def draw(p: QPainter):
        p.setBrush(QBrush(QColor(C.STATE_FILL)))
        p.drawRoundedRect(QRectF(5, 8, 30, 24), 5, 5)
        p.setBrush(QBrush(QColor(C.STATE_HEADER)))
        p.setPen(QPen(QColor(C.STATE_TITLE), 1.8))
        p.drawRoundedRect(QRectF(10, 16, 20, 11), 3, 3)
    return _icon(draw)


def initial_icon() -> QIcon:
    """Initial pseudostate: a FILLED circle in UML notation.

    The body and the ring are drawn in the tone with most contrast to the
    background, because UML tells these two vertices apart by FILL, not colour.
    """
    def draw(p: QPainter):
        p.setBrush(QBrush(QColor(ink())))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(12, 12, 16, 16))
    return _icon(draw)


def final_icon() -> QIcon:
    """Final state: outer ring + filled core (UML notation)."""
    def draw(p: QPainter):
        p.setPen(QPen(QColor(ink()), 2.4))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(10, 10, 20, 20))
        p.setBrush(QBrush(QColor(ink())))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(15, 15, 10, 10))
    return _icon(draw)


def choice_icon() -> QIcon:
    def draw(p: QPainter):
        p.setBrush(QBrush(QColor(C.STATE_HEADER)))
        p.drawPolygon(QPolygonF([QPointF(20, 8), QPointF(32, 20),
                                 QPointF(20, 32), QPointF(8, 20)]))
    return _icon(draw)


def transition_icon() -> QIcon:
    def draw(p: QPainter):
        path = QPainterPath(QPointF(7, 28))
        path.cubicTo(QPointF(14, 10), QPointF(24, 30), QPointF(31, 13))
        p.drawPath(path)
        p.setBrush(QBrush(QColor(C.TEXT_BRIGHT)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(QPolygonF([QPointF(33, 9), QPointF(26, 14), QPointF(32, 17)]))
    return _icon(draw)


def junction_icon() -> QIcon:
    def draw(p: QPainter):
        p.setBrush(QBrush(QColor(C.CHOICE_FILL)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(14, 14, 12, 12))
        p.setPen(QPen(QColor(C.TEXT_BRIGHT), 2.0))
        p.drawLine(QPointF(6, 20), QPointF(14, 20))
        p.drawLine(QPointF(26, 20), QPointF(34, 14))
        p.drawLine(QPointF(26, 20), QPointF(34, 26))
    return _icon(draw)


def shallow_history_icon() -> QIcon:
    def draw(p: QPainter):
        p.setPen(QPen(QColor(C.HISTORY_FILL), 2.4))
        p.drawEllipse(QRectF(8, 8, 24, 24))
        p.setPen(QPen(QColor(C.TEXT_BRIGHT), 2.6))
        p.drawLine(QPointF(16, 13), QPointF(16, 27))
        p.drawLine(QPointF(24, 13), QPointF(24, 27))
        p.drawLine(QPointF(16, 20), QPointF(24, 20))
    return _icon(draw)


def deep_history_icon() -> QIcon:
    def draw(p: QPainter):
        p.setPen(QPen(QColor(C.HISTORY_FILL), 2.4))
        p.drawEllipse(QRectF(6, 8, 24, 24))
        p.setPen(QPen(QColor(C.TEXT_BRIGHT), 2.4))
        p.drawLine(QPointF(13, 14), QPointF(13, 26))
        p.drawLine(QPointF(20, 14), QPointF(20, 26))
        p.drawLine(QPointF(13, 20), QPointF(20, 20))
        f = p.font()
        f.setPointSize(9)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(24, 4, 14, 14), Qt.AlignmentFlag.AlignCenter, "*")
    return _icon(draw)


def terminate_icon() -> QIcon:
    def draw(p: QPainter):
        p.setPen(QPen(QColor(C.TERMINATE_FILL), 2.8, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(11, 11), QPointF(29, 29))
        p.drawLine(QPointF(29, 11), QPointF(11, 29))
    return _icon(draw)


def fork_icon() -> QIcon:
    """A thick bar with two diverging arrows (UML fork notation)."""
    def draw(p: QPainter):
        p.setBrush(QBrush(QColor(C.TEXT_BRIGHT)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRect(QRectF(17, 8, 5, 24))
        p.setPen(QPen(QColor(C.TEXT_BRIGHT), 2.0))
        p.drawLine(QPointF(6, 20), QPointF(17, 20))
        p.drawLine(QPointF(22, 14), QPointF(34, 11))
        p.drawLine(QPointF(22, 26), QPointF(34, 29))
    return _icon(draw)


def join_icon() -> QIcon:
    """A thick bar with two converging arrows (UML join notation)."""
    def draw(p: QPainter):
        p.setBrush(QBrush(QColor(C.TEXT_BRIGHT)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRect(QRectF(18, 8, 5, 24))
        p.setPen(QPen(QColor(C.TEXT_BRIGHT), 2.0))
        p.drawLine(QPointF(6, 11), QPointF(18, 14))
        p.drawLine(QPointF(6, 29), QPointF(18, 26))
        p.drawLine(QPointF(23, 20), QPointF(34, 20))
    return _icon(draw)


def entry_point_icon() -> QIcon:
    """A small HOLLOW circle on the border of a composite state (entry point)."""
    def draw(p: QPainter):
        p.setPen(QPen(QColor(C.STATE_BORDER), 1.6))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(14, 9, 20, 22), 4, 4)
        p.setPen(QPen(QColor(C.TEXT_BRIGHT), 1.8))
        p.setBrush(QBrush(QColor(C.PANEL)))
        p.drawEllipse(QRectF(9, 15, 10, 10))
        p.setPen(QPen(QColor(C.TEXT_BRIGHT), 2.0))
        p.drawLine(QPointF(19, 20), QPointF(25, 20))
    return _icon(draw)


def exit_point_icon() -> QIcon:
    """A small circle with a CROSS on the border (UML exit point)."""
    def draw(p: QPainter):
        p.setPen(QPen(QColor(C.STATE_BORDER), 1.6))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(6, 9, 20, 22), 4, 4)
        p.setPen(QPen(QColor(C.TEXT_BRIGHT), 1.8))
        p.setBrush(QBrush(QColor(C.PANEL)))
        p.drawEllipse(QRectF(21, 15, 10, 10))
        p.setPen(QPen(QColor(C.TEXT_BRIGHT), 1.6))
        p.drawLine(QPointF(23, 17), QPointF(29, 23))
        p.drawLine(QPointF(29, 17), QPointF(23, 23))
    return _icon(draw)


def submachine_icon() -> QIcon:
    """A box plus the UML submachine mark (two nested circles and a line)."""
    def draw(p: QPainter):
        p.setBrush(QBrush(QColor(C.STATE_FILL)))
        p.setPen(QPen(QColor(C.STATE_BORDER), 1.6))
        p.drawRoundedRect(QRectF(6, 10, 28, 20), 4, 4)
        p.setPen(QPen(QColor(C.TEXT_BRIGHT), 1.6))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(21, 20, 6, 6))
        p.drawEllipse(QRectF(27, 20, 6, 6))
        p.drawLine(QPointF(27, 23), QPointF(27, 23))
        p.drawLine(QPointF(24, 20), QPointF(24, 16))
    return _icon(draw)


# ------------------------------------------------------------ class diagram icons

def class_icon() -> QIcon:
    def draw(p: QPainter):
        p.setBrush(QBrush(QColor(C.STATE_FILL)))
        p.drawRect(QRectF(7, 7, 26, 26))
        p.setPen(QPen(QColor(C.CLASS_HEADER), 2.0))
        p.setBrush(QBrush(QColor(C.CLASS_HEADER)))
        p.drawRect(QRectF(7, 7, 26, 8))
        p.setPen(QPen(QColor(C.TEXT_BRIGHT), 1.6))
        p.drawLine(QPointF(7, 23), QPointF(33, 23))
    return _icon(draw)


def interface_icon() -> QIcon:
    def draw(p: QPainter):
        p.setPen(QPen(QColor(C.IFACE_HEADER), 2.6))
        p.drawEllipse(QRectF(12, 14, 16, 16))
        p.drawLine(QPointF(20, 14), QPointF(20, 6))
        p.drawLine(QPointF(13, 6), QPointF(27, 6))
    return _icon(draw)


def abstract_icon() -> QIcon:
    def draw(p: QPainter):
        p.setBrush(QBrush(QColor(C.STATE_FILL)))
        p.drawRect(QRectF(7, 7, 26, 26))
        p.setPen(QPen(QColor(C.ABSTRACT_HEADER), 2.0))
        p.setBrush(QBrush(QColor(C.ABSTRACT_HEADER)))
        p.drawRect(QRectF(7, 7, 26, 8))
        f = p.font()
        f.setPointSize(10)
        f.setItalic(True)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QPen(QColor(C.TEXT_BRIGHT)))
        p.drawText(QRectF(7, 15, 26, 18), Qt.AlignmentFlag.AlignCenter, "A")
    return _icon(draw)


def association_icon() -> QIcon:
    def draw(p: QPainter):
        p.drawLine(QPointF(6, 28), QPointF(30, 12))
        p.drawLine(QPointF(30, 12), QPointF(22, 12))
        p.drawLine(QPointF(30, 12), QPointF(30, 20))
    return _icon(draw)


def aggregation_icon() -> QIcon:
    def draw(p: QPainter):
        p.drawLine(QPointF(6, 28), QPointF(20, 18))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolygon(QPolygonF([QPointF(20, 18), QPointF(27, 12),
                                 QPointF(34, 16), QPointF(27, 22)]))
    return _icon(draw)


def composition_icon() -> QIcon:
    def draw(p: QPainter):
        p.drawLine(QPointF(6, 28), QPointF(20, 18))
        p.setBrush(QBrush(QColor(C.TEXT_BRIGHT)))
        p.drawPolygon(QPolygonF([QPointF(20, 18), QPointF(27, 12),
                                 QPointF(34, 16), QPointF(27, 22)]))
    return _icon(draw)


def generalization_icon() -> QIcon:
    def draw(p: QPainter):
        p.drawLine(QPointF(8, 30), QPointF(24, 16))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolygon(QPolygonF([QPointF(24, 16), QPointF(34, 10),
                                 QPointF(30, 21)]))
    return _icon(draw)


def realization_icon() -> QIcon:
    def draw(p: QPainter):
        pen = p.pen()
        pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawLine(QPointF(8, 30), QPointF(24, 16))
        pen.setStyle(Qt.PenStyle.SolidLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolygon(QPolygonF([QPointF(24, 16), QPointF(34, 10),
                                 QPointF(30, 21)]))
    return _icon(draw)


def dependency_icon() -> QIcon:
    def draw(p: QPainter):
        pen = p.pen()
        pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawLine(QPointF(6, 28), QPointF(30, 12))
        pen.setStyle(Qt.PenStyle.SolidLine)
        p.setPen(pen)
        p.drawLine(QPointF(30, 12), QPointF(22, 12))
        p.drawLine(QPointF(30, 12), QPointF(30, 20))
    return _icon(draw)


def panel_icon() -> QIcon:
    def draw(p: QPainter):
        p.drawRoundedRect(QRectF(6, 8, 28, 24), 3, 3)
        p.setBrush(QBrush(QColor(C.ACCENT)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRect(QRectF(22, 10, 10, 20))
    return _icon(draw)


def eye_icon(open_eye: bool = True) -> QIcon:
    """Visibility icon: an open eye (visible) / a crossed eye (hidden).

    Using an eye instead of words for "show / hide" reads at a glance in the
    menu and on the toolbar: the user sees which panel is open without reading
    any text.
    """
    def draw(p: QPainter):
        # Almond body: two arcs on top of each other.
        path = QPainterPath()
        path.moveTo(6, 20)
        path.quadTo(20, 6, 34, 20)
        path.quadTo(20, 34, 6, 20)
        p.drawPath(path)
        # Pupil
        p.setBrush(QBrush(QColor(C.ACCENT)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(20, 20), 5.0, 5.0)
        if not open_eye:
            # When closed, a slanted line over it: "hidden".
            pen = QPen(QColor(C.TEXT_BRIGHT))
            pen.setWidthF(3.0)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawLine(QPointF(8, 32), QPointF(32, 8))
    return _icon(draw)


def play_icon() -> QIcon:
    def draw(p: QPainter):
        p.setBrush(QBrush(QColor(C.GREEN)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(QPolygonF([QPointF(12, 8), QPointF(32, 20), QPointF(12, 32)]))
    return _icon(draw)


# ------------------------------------------------------------------ action icons

def new_icon() -> QIcon:
    def draw(p: QPainter):
        p.drawRoundedRect(QRectF(11, 7, 18, 26), 3, 3)
        p.drawLine(QPointF(16, 16), QPointF(24, 16))
        p.drawLine(QPointF(16, 22), QPointF(24, 22))
    return _icon(draw)


def open_icon() -> QIcon:
    def draw(p: QPainter):
        p.drawPolyline(QPolygonF([QPointF(7, 30), QPointF(7, 12), QPointF(17, 12),
                                  QPointF(20, 16), QPointF(30, 16)]))
        p.drawPolyline(QPolygonF([QPointF(7, 30), QPointF(13, 20), QPointF(35, 20),
                                  QPointF(29, 30), QPointF(7, 30)]))
    return _icon(draw)


def save_icon() -> QIcon:
    def draw(p: QPainter):
        p.drawRoundedRect(QRectF(8, 8, 24, 24), 3, 3)
        p.drawRect(QRectF(14, 8, 12, 8))
        p.drawRect(QRectF(13, 22, 14, 10))
    return _icon(draw)


def export_icon() -> QIcon:
    def draw(p: QPainter):
        p.drawLine(QPointF(20, 26), QPointF(20, 8))
        p.drawPolyline(QPolygonF([QPointF(13, 15), QPointF(20, 8), QPointF(27, 15)]))
        p.drawPolyline(QPolygonF([QPointF(9, 24), QPointF(9, 32), QPointF(31, 32),
                                  QPointF(31, 24)]))
    return _icon(draw)


def undo_icon() -> QIcon:
    def draw(p: QPainter):
        path = QPainterPath(QPointF(11, 16))
        path.arcTo(QRectF(10, 10, 22, 18), 150, -260)
        p.drawPath(path)
        p.setBrush(QBrush(QColor(C.TEXT_BRIGHT)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(QPolygonF([QPointF(8, 11), QPointF(17, 14), QPointF(9, 20)]))
    return _icon(draw)


def redo_icon() -> QIcon:
    def draw(p: QPainter):
        path = QPainterPath(QPointF(29, 16))
        path.arcTo(QRectF(8, 10, 22, 18), 30, 260)
        p.drawPath(path)
        p.setBrush(QBrush(QColor(C.TEXT_BRIGHT)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(QPolygonF([QPointF(32, 11), QPointF(23, 14), QPointF(31, 20)]))
    return _icon(draw)


def zoom_in_icon() -> QIcon:
    def draw(p: QPainter):
        p.drawEllipse(QRectF(8, 8, 20, 20))
        p.drawLine(QPointF(25, 25), QPointF(33, 33))
        p.drawLine(QPointF(14, 18), QPointF(22, 18))
        p.drawLine(QPointF(18, 14), QPointF(18, 22))
    return _icon(draw)


def zoom_out_icon() -> QIcon:
    def draw(p: QPainter):
        p.drawEllipse(QRectF(8, 8, 20, 20))
        p.drawLine(QPointF(25, 25), QPointF(33, 33))
        p.drawLine(QPointF(14, 18), QPointF(22, 18))
    return _icon(draw)


def fit_icon() -> QIcon:
    def draw(p: QPainter):
        for dx, dy, sx, sy in ((0, 0, 1, 1), (1, 0, -1, 1),
                               (0, 1, 1, -1), (1, 1, -1, -1)):
            x = 9 + dx * 22
            y = 9 + dy * 22
            p.drawLine(QPointF(x, y), QPointF(x + 7 * sx, y))
            p.drawLine(QPointF(x, y), QPointF(x, y + 7 * sy))
    return _icon(draw)


def grid_icon() -> QIcon:
    def draw(p: QPainter):
        p.setPen(QPen(QColor(C.TEXT_BRIGHT), 1.6))
        for i in range(4):
            v = 9 + i * 7.5
            p.drawLine(QPointF(v, 9), QPointF(v, 31))
            p.drawLine(QPointF(9, v), QPointF(31, v))
    return _icon(draw)


def generate_icon() -> QIcon:
    def draw(p: QPainter):
        p.drawPolyline(QPolygonF([QPointF(16, 13), QPointF(9, 20), QPointF(16, 27)]))
        p.drawPolyline(QPolygonF([QPointF(24, 13), QPointF(31, 20), QPointF(24, 27)]))
        p.setPen(QPen(QColor(C.ORANGE), 2.2))
        p.drawLine(QPointF(22, 10), QPointF(18, 30))
    return _icon(draw)


def build_icon() -> QIcon:
    """A hammer: the action that builds the model and generates code.

    It is DELIBERATELY different from the double-arrow 'Regenerate' icon:
    generation no longer happens by itself but on the user's command, and the
    button has to say so.
    """
    def draw(p: QPainter):
        # handle
        p.drawLine(QPointF(11, 30), QPointF(22, 19))
        # head
        p.setPen(QPen(QColor(C.ORANGE), 3.0, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawLine(QPointF(19, 11), QPointF(30, 22))
        p.drawLine(QPointF(24, 7), QPointF(28, 11))
        p.drawLine(QPointF(30, 18), QPointF(26, 22))
    return _icon(draw)


def validate_icon() -> QIcon:
    def draw(p: QPainter):
        p.setPen(QPen(QColor(C.GREEN), 3.0, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPolyline(QPolygonF([QPointF(10, 21), QPointF(17, 28), QPointF(31, 12)]))
    return _icon(draw)


def delete_icon() -> QIcon:
    def draw(p: QPainter):
        p.setPen(QPen(QColor(C.RED), 2.4, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(12, 12), QPointF(28, 28))
        p.drawLine(QPointF(28, 12), QPointF(12, 28))
    return _icon(draw)


def app_icon() -> QIcon:
    def draw(p: QPainter):
        p.setPen(QPen(QColor(C.STATE_BORDER), 2.0))
        p.setBrush(QBrush(QColor(C.STATE_FILL)))
        p.drawRoundedRect(QRectF(4, 7, 16, 11), 3, 3)
        p.drawRoundedRect(QRectF(21, 22, 16, 11), 3, 3)
        p.setPen(QPen(QColor(C.ORANGE), 2.2))
        p.drawLine(QPointF(13, 18), QPointF(27, 22))
        p.setBrush(QBrush(QColor(C.ORANGE)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(QPolygonF([QPointF(29, 23), QPointF(22, 22), QPointF(25, 17)]))
    return _icon(draw)
