"""The SHARED behaviour of the canvases: navigation, edge scrolling, ALIGNMENT.

Both canvases (state machine and class diagram) hit the same QGraphicsView
problems, so the rule lives in ONE PLACE. Written separately, one would get
fixed and the other forgotten -- which is exactly how the diff marks ended up
on the state canvas only and missing on the class canvas.

Two concrete complaints this solves:

* "it keeps jumping somewhere else while I drag" -- the scene rectangle was
  recomputed only when the mouse was released; when the range of the scroll
  bars changed all at once, everything in the view jumped. The scene now
  grows DURING the drag and the view position is compensated on every change.

* "scroll left/right/up/down automatically when it would go off the window" --
  the viewport was never scrolled and an item reaching the edge disappeared.
  The view now scrolls like draw.io as the cursor approaches an edge.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QMouseEvent, QPen
from PyQt6.QtWidgets import QApplication

from .theme import C


class CanvasNavigation:
    """A navigation mixin added to canvases derived from QGraphicsView.

    The using class must set up the ``self._scene`` field and call
    :meth:`_init_navigation` inside its ``__init__``.
    """

    # EDGE SCROLLING WHILE DRAGGING (draw.io behaviour) #
    #: Scrolling starts when the cursor comes this close to the viewport edge (px).
    AUTOSCROLL_MARGIN = 34.0
    #: Pixels scrolled per tick while pressed right against the edge.
    AUTOSCROLL_STEP = 26.0
    #: The scroll tick interval (ms) -- about 60 Hz.
    AUTOSCROLL_MS = 16
    #: The empty scene margin kept around the cursor during a drag.
    SCENE_PAD = 600.0

    def _init_navigation(self) -> None:
        """Sets up the scroll timer and the drag state."""
        self._autoscroll = QTimer(self)
        self._autoscroll.setInterval(self.AUTOSCROLL_MS)
        self._autoscroll.timeout.connect(self._autoscroll_step)
        #: Pixels to scroll on each tick (x, y); zero stops the scrolling.
        self._autoscroll_vec = QPointF()
        #: The last mouse position in the drag (in viewport pixels).
        self._drag_pos: Optional[QPoint] = None
        self._init_alignment()

    def _set_scene_rect(self, rect: QRectF) -> None:
        """Changes the scene rectangle WITHOUT SCROLLING THE VIEW.

        THE BUG WE HIT: the scene rectangle was recomputed with
        `itemsBoundingRect()`. When the user dragged a box far to the right and
        let go, the scene widened all at once (we measured 1917 -> 3082 px),
        and because the range of the scroll bars changed, EVERYTHING in the
        view jumped. The user reported it as "it keeps jumping somewhere else".
        diye bildirdi.

        The fix: after the new rectangle is set, the SCENE position of the top
        left corner of the viewport is measured; if it moved, the scroll bars
        are moved back by the difference. So the scene grows but the view does
        not budge.
        """
        # THE AREA CURRENTLY VISIBLE must always stay inside the scene.
        #
        # Otherwise, when the scene shrinks, the range of the scroll bar narrows,
        # Qt CLIPS the value and the view jumps somewhere else -- from -399 to 0
        # vertically, as far as we measured. On a rebuild the scene shrinks to fit
        # the content, so this happened the moment a drag ended.
        # yasaniyordu.
        gorunen = self.mapToScene(self.viewport().rect()).boundingRect()
        rect = rect.united(gorunen)
        if rect == self._scene.sceneRect():
            return
        before = self.mapToScene(QPoint(0, 0))
        self._scene.setSceneRect(rect)
        sonrasi = self.mapToScene(QPoint(0, 0))
        kayma = sonrasi - before
        if kayma.isNull():
            return
        olcek = self.transform().m11() or 1.0
        dikey = self.transform().m22() or 1.0
        yatay_cubuk = self.horizontalScrollBar()
        dikey_cubuk = self.verticalScrollBar()
        yatay_cubuk.setValue(yatay_cubuk.value() + int(round(kayma.x() * olcek)))
        dikey_cubuk.setValue(dikey_cubuk.value() + int(round(kayma.y() * dikey)))

    def _grow_scene(self, rect: QRectF) -> None:
        """GROWS the scene to cover the given rectangle (it never shrinks it)."""
        simdiki = self._scene.sceneRect()
        birlesik = simdiki.united(rect)
        if birlesik != simdiki:
            self._set_scene_rect(birlesik)

    def _room_for_drag(self, view_pos: QPoint) -> None:
        """Makes room around the cursor and around the selected items.

        If the scene grows DURING the drag there is nothing left to grow on
        release, and so there is no jump. It also widens the range of the
        scroll bars, so edge scrolling actually advances -- otherwise there is
        nowhere to scroll to once the cursor reaches the edge.
        """
        nokta = self.mapToScene(view_pos)
        pay = self.SCENE_PAD
        field = QRectF(nokta.x() - pay, nokta.y() - pay, pay * 2.0, pay * 2.0)
        for oge in self._scene.selectedItems():
            field = field.united(oge.sceneBoundingRect())
        self._grow_scene(field)

    def _autoscroll_vector(self, pos: QPoint) -> QPointF:
        """A scroll vector: the closer the cursor to an edge, the faster."""
        field = self.viewport().rect()
        kenar = self.AUTOSCROLL_MARGIN
        adim = self.AUTOSCROLL_STEP

        def eksen(deger: float, alt: float, ust: float) -> float:
            if deger < alt + kenar:
                return -adim * min(1.0, (alt + kenar - deger) / kenar)
            if deger > ust - kenar:
                return adim * min(1.0, (deger - (ust - kenar)) / kenar)
            return 0.0

        return QPointF(eksen(float(pos.x()), float(field.left()),
                             float(field.right())),
                       eksen(float(pos.y()), float(field.top()),
                             float(field.bottom())))

    def _update_autoscroll(self, pos: QPoint) -> None:
        """Starts/stops edge scrolling during a drag."""
        self._drag_pos = QPoint(pos)
        self._autoscroll_vec = self._autoscroll_vector(pos)
        calisiyor = self._autoscroll.isActive()
        needed = not self._autoscroll_vec.isNull()
        if needed and not calisiyor:
            self._autoscroll.start()
        elif not needed and calisiyor:
            self._autoscroll.stop()

    def _stop_autoscroll(self) -> None:
        self._autoscroll.stop()
        self._autoscroll_vec = QPointF()
        self._drag_pos = None

    def _autoscroll_step(self) -> None:
        """One scroll tick: scrolls the view and ADVANCES THE DRAG.

        Scrolling alone is not enough: because the mouse does not move, Qt's
        item drag machinery is not triggered and the box would stay where it
        was -- the cursor would drift away from it. So the SAME mouse position
        is reprocessed after the scroll; the box follows the cursor.
        """
        if self._drag_pos is None or self._autoscroll_vec.isNull():
            self._stop_autoscroll()
            return
        if not (QApplication.mouseButtons() & Qt.MouseButton.LeftButton):
            self._stop_autoscroll()
            return

        self._room_for_drag(self._drag_pos)
        yatay = self.horizontalScrollBar()
        dikey = self.verticalScrollBar()
        yatay.setValue(yatay.value() + int(round(self._autoscroll_vec.x())))
        dikey.setValue(dikey.value() + int(round(self._autoscroll_vec.y())))

        nokta = QPointF(self._drag_pos)
        event = QMouseEvent(QMouseEvent.Type.MouseMove, nokta, nokta,
                           Qt.MouseButton.NoButton,
                           Qt.MouseButton.LeftButton,
                           Qt.KeyboardModifier.NoModifier)
        self.mouseMoveEvent(event)

    # == ALIGNMENT GUIDES =============================================== #
    #
    # When the user drags a state, it snaps once it is ON THE SAME ROW or in
    # THE SAME COLUMN as its neighbours, and a dashed line shows the alignment
    # (draw.io / Visio behaviour). Aligning by eye is off by a pixel, and that
    # shows up immediately once the diagram is printed or put into a document.
    # 
    #
    # The measures compared are OF THE SAME KIND: left-left, centre-centre,
    # right-right (column) and top-top, centre-centre, bottom-bottom (row).
    # Cross matching (my left against their right, say) is DELIBERATELY not
    # done: it multiplies the snap points and the user cannot predict them.

    #: The snap radius. It is in SCREEN pixels, so it feels the same at any
    #: zoom. Fixed in scene units, everything would stick together when zoomed
    #: out and nothing would snap when zoomed in.
    ALIGN_PX = 7.0
    #: How far the guide line runs past the aligned boxes (scene px).
    ALIGN_PAD = 26.0

    def _init_alignment(self) -> None:
        """Sets up the alignment state."""
        self.align_enabled = True
        #: The guides to draw right now; in scene coordinates (start, end).
        self._align_guides: List[Tuple[QPointF, QPointF]] = []

    # guides

    def align_clear(self) -> None:
        """Clears the guides (when the drag ends)."""
        if getattr(self, "_align_guides", None):
            self._align_guides = []
            self.viewport().update()

    def align_paint(self, painter) -> None:
        """Draws the guides; called from ``drawForeground``."""
        cizgiler = getattr(self, "_align_guides", None)
        if not cizgiler:
            return
        kalem = QPen(QColor(C.ORANGE), 0.0)
        # A COSMETIC pen: the width does not grow with the zoom. Given 1 px in
        # scene units, at 400% zoom it would be a 4 px band and would hide the
        # very alignment it marks.
        kalem.setCosmetic(True)
        kalem.setStyle(Qt.PenStyle.DashLine)
        painter.save()
        painter.setPen(kalem)
        for bas, last in cizgiler:
            painter.drawLine(bas, last)
        painter.restore()

    # snapping

    def _align_uygun(self, item) -> bool:
        """Alignment runs ONLY during a manual drag.

        `itemChange` fires on programmatic `setPos()` calls too: while loading
        a file, while undoing/redoing, while nudging with the arrow keys.
        Snapping there would change the model silently. The item held by the
        mouse is the scene's `mouseGrabberItem()`.

        It does not run on a multiple selection either: Qt sends a SEPARATE
        `ItemPositionChange` per item; snapping only the held one scatters it.
        """
        if not getattr(self, "align_enabled", True):
            return False
        sahne = self.scene()
        if sahne is None or sahne.mouseGrabberItem() is not item:
            return False
        return len(sahne.selectedItems()) <= 1

    def align_drag(self, item, pt: QPointF, size, komsular) -> QPointF:
        """Aligns the dragged item to its neighbours.

        ``pt``          -- the proposed top left corner (in PARENT coordinates)
        ``size``        -- (width, height)
        ``neighbours``  -- neighbours in the same space: [(x, y, w, h), ...]

        Returns the aligned position and prepares the guides to be drawn.
        """
        previous = getattr(self, "_align_guides", [])
        if not self._align_uygun(item) or not komsular:
            if previous:
                self._align_guides = []
                self.viewport().update()
            return pt

        olcek = abs(self.transform().m11()) or 1.0
        esik = self.ALIGN_PX / olcek
        gen, yuk = float(size[0]), float(size[1])

        dx, x_hiza = self._align_eksen(pt.x(), gen, komsular, 0, esik)
        dy, y_hiza = self._align_eksen(pt.y(), yuk, komsular, 1, esik)

        new = QPointF(pt.x() + dx, pt.y() + dy)
        box = (new.x(), new.y(), gen, yuk)
        cizgiler = []
        if x_hiza is not None:
            cizgiler.append(self._align_cizgi(item, x_hiza, box, komsular, 0))
        if y_hiza is not None:
            cizgiler.append(self._align_cizgi(item, y_hiza, box, komsular, 1))

        if cizgiler != previous:
            self._align_guides = cizgiler
            self.viewport().update()
        return new

    @staticmethod
    def _align_olcutler(bas: float, uzunluk: float):
        """The measures on one axis: the CENTRE comes first.

        The order matters so that a tie picks the centre alignment: the centre
        and the edge of two boxes can be the same distance away, and in that
        case the centre line is what looks most natural.
        """
        return (bas + uzunluk / 2.0, bas, bas + uzunluk)

    def _align_eksen(self, bas: float, uzunluk: float, komsular, eksen: int,
                     esik: float):
        """Finds the nearest alignment on a single axis.

        Returns ``(offset, target_value)``; ``(0.0, None)`` when there is none.
        """
        benim = self._align_olcutler(bas, uzunluk)
        en_iyi = None
        for komsu in komsular:
            k_bas = komsu[eksen]
            k_uzunluk = komsu[eksen + 2]
            onun = self._align_olcutler(k_bas, k_uzunluk)
            for sira in range(3):
                fark = onun[sira] - benim[sira]
                if abs(fark) > esik:
                    continue
                aday = (abs(fark), sira, fark, onun[sira])
                if en_iyi is None or aday[:2] < en_iyi[:2]:
                    en_iyi = aday
        if en_iyi is None:
            return (0.0, None)
        return (en_iyi[2], en_iyi[3])

    def _align_cizgi(self, item, deger: float, box, komsular, eksen: int):
        """Produces the guide line in SCENE coordinates.

        The line is stretched to cover every box that SHARES the alignment, so
        the user sees at a glance what they snapped to.
        """
        # The boxes that share the alignment (the dragged one included).
        related = [box]
        for komsu in komsular:
            olcutler = self._align_olcutler(komsu[eksen], komsu[eksen + 2])
            if any(abs(o - deger) < 0.5 for o in olcutler):
                related.append(komsu)

        other = 1 - eksen
        bas = min(k[other] for k in related) - self.ALIGN_PAD
        last = max(k[other] + k[other + 2] for k in related) + self.ALIGN_PAD
        if eksen == 0:
            p1, p2 = QPointF(deger, bas), QPointF(deger, last)
        else:
            p1, p2 = QPointF(bas, deger), QPointF(last, deger)

        # When the item is INSIDE a composite state its coordinates are relative
        # to that parent; the guide is drawn on the scene, so it must be converted.
        ebeveyn = item.parentItem()
        if ebeveyn is not None:
            p1 = ebeveyn.mapToScene(p1)
            p2 = ebeveyn.mapToScene(p2)
        return (p1, p2)
