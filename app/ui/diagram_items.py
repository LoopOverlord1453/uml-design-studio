"""Tuval uzerindeki grafik elemanlar: durumlar ve gecisler."""

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
#: Gecis etiketinin en fazla genisligi (px).
LABEL_MAX_W = 520.0
GRIP = 14.0
PSEUDO_SIZE = 24.0
CHOICE_SIZE = 38.0
FINAL_SIZE = 30.0


# --------------------------------------------------------------------------- #
#  Geometri yardimcilari
# --------------------------------------------------------------------------- #

def snap(value: float, enabled: bool = True) -> float:
    return round(value / GRID) * GRID if enabled else value


def _unit(v: QPointF) -> QPointF:
    length = math.hypot(v.x(), v.y())
    if length < 1e-6:
        return QPointF(1.0, 0.0)
    return QPointF(v.x() / length, v.y() / length)


def _point_segment_distance(p: QPointF, a: QPointF, b: QPointF) -> float:
    """`p` noktasinin [a, b] DOGRU PARCASINA uzakligi.

    Sonsuz dogruya degil PARCAYA olan uzaklik gerekir: aksi halde okun
    uzantisi uzerindeki uzak bir tik, o parcaya en yakin sayilirdi.
    """
    vx, vy = b.x() - a.x(), b.y() - a.y()
    uzunluk2 = vx * vx + vy * vy
    if uzunluk2 < 1e-9:
        return math.hypot(p.x() - a.x(), p.y() - a.y())
    t = ((p.x() - a.x()) * vx + (p.y() - a.y()) * vy) / uzunluk2
    t = max(0.0, min(1.0, t))
    return math.hypot(p.x() - (a.x() + t * vx), p.y() - (a.y() + t * vy))


def guarded_paint(fn):
    """Cizim istisnalarini yutan sarmalayici.

    Qt, paint() icinden sizan bir Python istisnasinda sureci SESSIZCE sonlandirir
    (izi bile basmadan). Bu yuzden her paint govdesi burada korunur: hata olursa
    konsola yazilir ve eleman kirmizi bir cerceve ile isaretlenir.
    """
    def wrapper(self, painter, option, widget=None):
        painter.save()
        try:
            fn(self, painter, option, widget)
        except Exception:                       # pragma: no cover - savunma amacli
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
    """`center`ten `toward`a giden isinin cokgen kenariyla kesisimi."""
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
#  Durum elemani
# --------------------------------------------------------------------------- #

class GhostItem(QGraphicsItem):
    """FARK kipinde SILINEN bir elemanin eski yerindeki izi.

    Silinen eleman yeni modelde YOKTUR, dolayisiyla normal bir StateItem
    olarak cizilemez. Kullanicinin "ne cikarilmis" sorusunu diyagram
    uzerinde gorebilmesi icin eski surumdeki konum ve boyutuyla kesik
    kirmizi bir cerceve olarak cizilir.

    Modele AIT DEGILDIR: secilemez, tasinamaz, kaydedilmez.
    """

    def __init__(self, veri: dict) -> None:
        super().__init__()
        self._ad = str(veri.get("name") or "?")
        self._w = float(veri.get("w") or 120.0)
        self._h = float(veri.get("h") or 70.0)
        self.setPos(float(veri.get("x") or 0.0), float(veri.get("y") or 0.0))
        self.setZValue(-5.0)          # gercek elemanlarin ALTINDA
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
    """Bir durumu / sozde-durumu cizen ve suruklenebilir kilan eleman."""

    def __init__(self, state: State, canvas) -> None:
        super().__init__()
        self.state = state
        self.canvas = canvas
        self.has_error = False
        self.is_active = False     # simulasyonda etkin durum zincirinde mi
        #: FARK kipi isareti: "" | "added" | "changed" (bkz. canvas).
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
        # Kullanici "durumlarin icindeki yazilar okunmuyor" dedi:
        # 8 punto, tipik yakinlastirma duzeyinde silik kaliyordu.
        self.f_body = mono_font(9)

    # ------------------------------------------------------------- geometri #

    @property
    def kind(self) -> StateKind:
        return self.state.kind

    def min_size(self) -> Tuple[float, float]:
        """Icerigin GEREKTIRDIGI en kucuk kutu (yerel koordinat).

        Kullanici entry/exit/do metnini ya da adi uzattiginda kutu
        kendiliginden buyumuyordu; tuval de sigmayani `elidedText` ile
        kirpiyordu. Yarim gorunen bir davranis, diyagrami okuyan kisiye
        durumun ne yaptigini yanlis anlatir.

        Sozde-durumlar ve final durum sabit sekillerdir; onlara
        dokunulmaz.
        """
        if self.kind.is_pseudo or self.kind is StateKind.FINAL:
            # Sabit sekiller: daire, elmas, cubuk... MIN_W/MIN_H buraya
            # UYGULANMAZ; uygulanirsa 24x24'luk bir initial 90x54'e sisip
            # komsu durumun uzerine biner.
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

        # BILESIK DURUM ALT DURUMLARINI DA KAPSAMALI.
        #
        # Alt durumlar artik kendi metinlerine gore buyudugu icin ust
        # durumun sabit boyutu yetmeyebilir: yazi tipi degisen bir
        # makinede (uygulama JetBrains Mono'yu paketlemiyor) alt durum
        # ust durumun disina tasiyordu.
        if self.kind is StateKind.COMPOSITE:
            sag = alt = 0.0
            try:
                cocuklar = list(self.childItems())
            except RuntimeError:
                # Oge yeniden kurulumda silinmis olabilir; cagiran taraf
                # hala eski basvuruyu tutuyor olabilir. Cokmek yerine
                # modeldeki boyutla yetin. (rect() eskiden yalnizca Python
                # alanlarini okudugu icin bu durum sessizce calisiyordu.)
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
        """Cizim dikdortgeni: modeldeki boyut, ama icerikten KUCUK DEGIL.

        Model degistirilmez -- dosya, geri alma ve "kaydedilmedi" bayragi
        etkilenmez. Kullanicinin verdigi boyut korunur; yalnizca metnin
        gerektirdiginin altina dusulemez.
        """
        en, boy = self.min_size()
        return QRectF(0.0, 0.0, max(self.state.w, en),
                      max(self.state.h, boy))

    #: Adini seklin ALTINA yazan sozde-durumlar (INITIAL ve FINAL yazmaz).
    NAMED_PSEUDO_KINDS = (StateKind.CHOICE, StateKind.JUNCTION,
                          StateKind.SHALLOW_HISTORY, StateKind.DEEP_HISTORY,
                          StateKind.TERMINATE, StateKind.FORK, StateKind.JOIN,
                          StateKind.ENTRY_POINT, StateKind.EXIT_POINT)

    def boundingRect(self) -> QRectF:
        kutu = self.rect().adjusted(-8.0, -8.0, 8.0, 8.0)
        if self.kind in self.NAMED_PSEUDO_KINDS:
            # _paint_pseudo_name adi seklin 50 px solundan/sagindan tasarak
            # ve altina yazar. Bu serit boundingRect'e girmezse Qt orayi ne
            # yeniden boyar (oge tasinirken ad iz birakir) ne de
            # itemsBoundingRect'e katar (zoom_fit ve disa aktarma adi kirpar).
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
            # addPolygon cokgeni KAPATMAZ (drawPolygon'un aksine): son
            # kose ilk koseye baglanmaz ve elmasin BIR KENARI eksik kalir.
            path.closeSubpath()
        else:
            path.addRoundedRect(self.rect(), 9.0, 9.0)
        return path

    def _halo_path(self) -> QPainterPath:
        """Simulasyon halesi: seklin 4 px disindan gecen dis hat."""
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
            # KAPATILMAZSA secim halesinin bir kenari cizilmez -- choice
            # elmasi secildiginde tam da bu goruluyordu.
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
        """Sahne koordinatlarinda dis hat (gecis baglama noktalari icin)."""
        r = self.rect()
        if self.kind in self.BRANCH_KINDS:
            local = self._diamond()
        else:
            local = QPolygonF(r)
        return self.mapToScene(local)

    def scene_center(self) -> QPointF:
        return self.mapToScene(self.rect().center())

    def anchor_toward(self, target: QPointF) -> QPointF:
        """Gecis cizgisinin bu duruma degecegi nokta."""
        center = self.scene_center()
        if self.kind in self.CIRCULAR_KINDS:
            radius = self.state.w / 2.0
            return center + _unit(target - center) * radius
        return _polygon_hit(self.scene_polygon(), center, target)

    def behavior_lines(self) -> List[str]:
        """entry / exit / do davranislarinin cizilecek satirlari.

        Satir sonu iki yoldan gelir (bkz. core/text_layout): cok satirli
        alanlardaki gercek Enter ve her alanda gecerli LINE_BREAK_MARKER.
        Devam satirlari basligin altina hizalanir.
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
        """Bilesik durumda davranis satirlarinin kapladigi dikey serit.

        BILESIK DURUMDA DA BUTUN DAVRANISLAR YAZILIR. Onceki surum yalnizca
        ILK satiri ciziyordu: `Running` durumunun exit ve do davranislari
        modelde ve URETILEN KODDA vardi ama diyagramda hic gorunmuyordu.
        Diyagrama bakan biri durumun cikista `led_write(false)` cagirdigini
        goremiyordu -- uretilen kod kritik yerlerde kullanildigi icin bu
        kabul edilemez bir eksiklik.

        Alt durumlarin bolgesi bu seridin ALTINDAN baslar (content_rect).
        """
        if self.kind is not StateKind.COMPOSITE:
            return 0.0
        lines = self.behavior_lines()
        if not lines:
            return 0.0
        return QFontMetricsF(self.f_body).height() * len(lines) + 6.0

    def content_rect(self) -> QRectF:
        """Alt durumlarin yerlesebilecegi ic alan (yerel koordinat)."""
        return self.rect().adjusted(10.0, 34.0 + self.behavior_strip_height(),
                                    -10.0, -10.0)

    def region_count(self) -> int:
        """Bu durumun sahip oldugu bolge sayisi (bilesik degilse 1)."""
        if self.kind is not StateKind.COMPOSITE:
            return 1
        return max(1, int(getattr(self.state, "regions", 1) or 1))

    def region_rect(self, index: int) -> QRectF:
        """Bir bolgenin ic alani (yerel koordinat).

        Bolgeler YATAY seritlere bolunur; UML'in alistigimiz gosterimi
        budur (kesikli cizgiyle ayrilmis yatay bantlar).
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
        """Yerel `y` koordinatinin dustugu bolge."""
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

    # --------------------------------------------------------------- olaylar #

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
            # Alt sinir ICERIKTEN gelir: kullanici kutuyu metnin altina
            # kuculturse yazi kirpilirdi.
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
        """Bilesik durum, icindeki alt durumlardan kucuk olamaz."""
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
            # HIZALAMA izgaradan SONRA gelir: izgara 10 px'e yuvarlar,
            # hiza ise komsunun gercek kenarina oturtur. Tersi sirada
            # izgara, bulunan hizayi hemen bozardi.
            pt = self.canvas.align_drag(
                self, pt, (self.state.w, self.state.h),
                self.canvas.sibling_boxes(self))
            parent = self.parentItem()
            if isinstance(parent, StateItem):
                area = parent.content_rect()
                hizali = QPointF(pt)
                pt.setX(min(max(pt.x(), area.left()), area.right() - self.state.w))
                pt.setY(min(max(pt.y(), area.top()), area.bottom() - self.state.h))
                # Ebeveyn kirpmasi hizayi BOZDUYSA kilavuz kaldirilir:
                # aksi halde cizgi, ogenin aslinda oturmadigi bir hizayi
                # gosterir ve kullaniciya yalan soyler.
                if pt != hizali:
                    self.canvas.align_clear()
            return pt
        if change == QGraphicsItem.GraphicsItemChange.ItemScenePositionHasChanged:
            self.canvas.refresh_transitions()
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.setZValue(1.0 if value else 0.0)
        return super().itemChange(change, value)

    # ----------------------------------------------------------------- cizim #

    @guarded_paint
    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        selected = self.isSelected()

        if self.is_active:
            painter.setPen(QPen(QColor(C.SIM_ACTIVE), 3.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(self._halo_path())

        # SECIM HALESI.
        #
        # Secim yalnizca kenarlik RENGI ve 1.3 -> 2.0 px kalinlik farkiyla
        # gosteriliyordu; kullanici "secilen oge hic belli olmuyor" dedi ve
        # haklidir -- koyu temada iki mavi tonu yan yana ayirt edilmiyor.
        # Sekilden BAGIMSIZ, seklin disindan gecen bir hale her durum
        # turunde (dikdortgen, daire, elmas) ayni netlikte gorunur.
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

    # -- sozde-durumlar

    def _paint_pseudo_name(self, p: QPainter) -> None:
        """Sozde-durum adini seklin altina yazar."""
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
        """Choice: DUZ bir elmas (UML 2.5.1, 14.2.4.6).

        Icine bir soru isareti yaziyorduk; spesifikasyonda boyle bir sus
        yok ve elmasin kendisi zaten dinamik dallanmayi anlatiyor.
        Standart gosterime yabanci bir isaret, aracin ciktisini UML
        okuyan birine tanidik gelmekten cikariyordu.
        """
        p.setPen(QPen(border, 2.0 if selected else 1.4))
        p.setBrush(QBrush(QColor(C.CHOICE_FILL)))
        p.drawPolygon(self._diamond())
        self._paint_pseudo_name(p)

    def _paint_junction(self, p: QPainter, border: QColor, selected: bool) -> None:
        """Junction: kucuk DOLU daire (UML 2.5.1, 14.2.4.6).

        Choice ile AYNI dolgu rengini kullaniyordu; iki ayri sozde-durum
        yalnizca bicimle (elmas / daire) ayirt ediliyordu. Spesifikasyon
        junction icin "small black circle" der -- bu yuzden initial ile
        ayni murekkep rengi kullanilir ve choice'in kehribar elmasindan
        bakisla ayrilir.
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
        """FORK / JOIN: UML'in KALIN CUBUK gosterimi.

        UML 2.5.1, 14.2.3.7: ikisi de bolgeler arasinda dagitim ya da
        birlestirme yapar; gosterimi kalin bir cizgidir. Fork ile join
        ayni sekle sahiptir -- farki oklarin yonu soyler, tipki belgede
        oldugu gibi.
        """
        r = self.rect()
        renk = QColor(C.TEXT_BRIGHT) if not selected else border
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(renk))
        p.drawRect(r)
        self._paint_pseudo_name(p)

    def _paint_connection_point(self, p: QPainter, border: QColor,
                                selected: bool) -> None:
        """ENTRY / EXIT POINT: sinirda duran kucuk daire.

        UML 2.5.1, 14.2.3.7 (basili s.313): ikisi de bilesik durumun
        icini disariya KAPATIR. Giris noktasi ICI BOS bir daire, cikis
        noktasi CARPI isaretli bir dairedir; boylece ikisi tek bakista
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

    # -- gercek durumlar

    def _paint_state(self, p: QPainter, border: QColor, selected: bool) -> None:
        r = self.rect()
        composite = self.kind is StateKind.COMPOSITE
        radius = 9.0

        body = QPainterPath()
        body.addRoundedRect(r, radius, radius)

        # golge
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 55))
        shadow = QPainterPath()
        shadow.addRoundedRect(r.translated(0, 2), radius, radius)
        p.drawPath(shadow)

        p.setBrush(QBrush(QColor(C.STATE_FILL_ALT if composite else C.STATE_FILL)))
        p.setPen(QPen(border, 2.0 if selected else 1.3))
        p.drawPath(body)

        # baslik seridi
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

        # ALTMAKINE DURUMU: UML'in "ic ice iki daire" isareti ve referans.
        #
        # Referans YAZILMAZSA kullanici, kutunun neyi gosterdigini ancak
        # ozellikler panelini acarak ogrenir; oysa uretilen kod tam da o
        # dosyadan gelir.
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

        # ORTOGONAL DURUM: bolgeler kesikli cizgiyle ayrilir.
        #
        # UML 2.5.1, 14.2.3.2: bir bilesik durum birden cok BOLGE sahibi
        # olabilir ve bolgeler ES ZAMANLI etkindir. Ayirici cizilmezse
        # kullanici hangi alt durumun hangi bolgede oldugunu goremez ve
        # uretilen kodun neden oyle davrandigini anlayamaz.
        if composite and self.region_count() > 1:
            ayirici = QPen(QColor(C.BORDER_LIGHT), 1.0, Qt.PenStyle.DashLine)
            p.setPen(ayirici)
            for k in range(1, self.region_count()):
                bant = self.region_rect(k)
                p.drawLine(QPointF(r.left() + 6.0, bant.top()),
                           QPointF(r.right() - 6.0, bant.top()))

        # baslik
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

        # davranislar
        #
        # COK SATIRLI. Onceki surum butun bosluklari tek bosluga indirip
        # her davranisi TEK satira siktiriyordu; iki ifadeli bir exit
        # eylemi kutuda "... check_temp_sensor(in..." diye kirpiliyor ve
        # kullanici yazdiginin tamamini goremiyordu.
        #
        # Satir sonu iki yoldan gelir (bkz. core/text_layout):
        #   * cok satirli alanlarda gercek Enter,
        #   * her alanda LINE_BREAK_MARKER isareti.
        # Devam satirlari basligin altina hizalanir.
        # BILESIK DURUM DA AYNI YOLU KULLANIR.
        #
        # Eskiden bilesik durum icin yalnizca `lines[0]` ciziliyordu; entry
        # gorunuyor, exit ve do SESSIZCE dusuyordu. `Running` durumunun
        # exit davranisi (`led_write(false);`) ve do davranisi modelde ve
        # uretilen kodda vardi ama diyagramda hicbir izi yoktu. Diyagram,
        # uretilen kodun dogrulanabilir gosterimi olmak zorunda.
        lines = self.behavior_lines()
        if lines:
            p.setFont(self.f_body)
            fmb = QFontMetricsF(self.f_body)
            # Bilesik durumda da TAM kontrast: TEXT_DIM ile yazilinca
            # entry/exit/do satirlari silik kaliyor ve okunmuyordu.
            p.setPen(QPen(QColor(C.STATE_TEXT)))
            y = r.top() + header_h + 6.0
            # Bilesik durumda davranis seridi alt durumlarin bolgesinde
            # BITER; basit durumda kutunun sonuna kadar gidebilir.
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
                # Kutuya SIGMAYAN satir var. Sessizce yutulursa kullanici
                # davranisin eksik oldugunu fark etmez; kutuyu buyutmesi
                # gerektigini soyleyen bir isaret birak.
                p.setPen(QPen(QColor(C.TEXT_DIM)))
                p.drawText(QRectF(r.left() + 8, r.bottom() - fmb.height() - 3,
                                  r.width() - 16, fmb.height()),
                           int(Qt.AlignmentFlag.AlignRight
                               | Qt.AlignmentFlag.AlignVCenter),
                           "+%d" % kalan)

        # boyutlandirma tutamagi
        if selected and self.is_resizable():
            g = self._grip_rect()
            p.setPen(QPen(QColor(C.STATE_SELECTED), 1.4))
            for k in (4, 8, 12):
                p.drawLine(QPointF(g.right() - k, g.bottom() - 2),
                           QPointF(g.right() - 2, g.bottom() - k))


# --------------------------------------------------------------------------- #
#  Gecis elemani
# --------------------------------------------------------------------------- #

class TransitionItem(QGraphicsItem):
    """Iki durum arasindaki oku, etiketi ve secim alanini cizer."""

    ARROW = 11.0

    def __init__(self, transition: Transition, src: StateItem, dst: StateItem,
                 canvas, bow: float = 0.0, label_t: float = 0.5) -> None:
        super().__init__()
        self.transition = transition
        self.src = src
        self.dst = dst
        self.canvas = canvas
        self.bow = bow                # paralel gecisleri ayirmak icin egrilik
        self.label_t = label_t        # etiketin yol uzerindeki konumu (0..1)
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
        #: FARK kipi isareti (bkz. StateItem.diff_mark).
        self.diff_mark = ""

        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setAcceptHoverEvents(True)
        self.setZValue(50.0)
        self.font = ui_font(8)
        self.update_path()

    # -------------------------------------------------------------- geometri #

    #: Kirilma noktasi tutamaginin yaricapi (sahne birimi).
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
            # Etiketi egrinin dis tarafina koy; boylece paralel gecislerin
            # etiketleri ust uste binmez.
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

    # ------------------------------------------------ cok noktali yonlendirme #

    def route_points(self) -> List[QPointF]:
        """Okun TAM cizgisi: [kaynak ucu, *kirilma noktalari, hedef ucu].

        Bukme islemleri bunun uzerinden yurur: kullanici okun HERHANGI bir
        noktasindan cekebilmelidir, yalnizca tek bir kirilma noktasindan
        degil.
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
        """Verilen sahne noktasi ETIKETIN uzerinde mi?

        Etiket, okun kendisinden AYRI tutulur: kullanici etiketi okun
        uzerinden kaydirip okunur bir yere tasiyabilmelidir (bkz.
        Transition.label_dx / label_dy).
        """
        if self._label_rect.isEmpty():
            return False
        return self._label_rect.adjusted(-3.0, -3.0, 3.0, 3.0).contains(pos)

    def grab_at(self, pos: QPointF, radius: float):
        """Verilen noktada okun neresi tutuldu?

        Doner:
          ("move", i)   -> i numarali KIRILMA NOKTASI tutuldu (tasinacak)
          ("insert", i) -> i numarali PARCA tutuldu; oraya YENI nokta girer

        Parca indisi ile kirilma noktasi indisi AYNIDIR: yol
        [uc, w0, w1, ..., uc] oldugu icin i. parca w[i-1] ile w[i] arasinda
        kalir ve yeni nokta tam o indise eklenir.
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
        # COK SATIRLI ETIKET. Kullanici olay / guard / eylem alanlarina
        # satir sonu isareti yazarsa etiket bolunur; her satir AYRI
        # kirpilir, kutu en genis satira gore kurulur.
        satirlar = self._label_lines or [self._label]
        # 260 px yaklasik 37 karakter demekti ve
        # "after(SETTLE_MS) [pin_is_low(ctx)]" gibi siradan bir etiket bile
        # kirpiliyordu. Sinir, gercek bir olay+guard+eylem ucusunu tasiyacak
        # kadar genis; yine de kacak bir metnin diyagrami kaplamasini onler.
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

    # -------------------------------------------------------------- olaylar  #

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

    # ---------------------------------------------------------------- cizim  #

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
        # Secili okun ALTINA solgun ve genis bir iz cizilir: ince bir
        # cizgide renk degisimi tek basina yeterince belli olmuyordu.
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

        # KIRILMA NOKTASI TUTAMAKLARI (yalnizca secili okta).
        #
        # Ok her noktasindan cekilerek sekillendirilebilir; hangi
        # noktalarin VAR OLDUGU gorunmezse kullanici onlari yeniden
        # yakalayamaz, her cekiste yenisini ekler ve ok bozulur.
        if selected and self.transition.waypoints:
            painter.setPen(QPen(QColor(C.STATE_TITLE), 1.2))
            painter.setBrush(QBrush(color))
            for x, y in self.transition.waypoints:
                painter.drawEllipse(QPointF(x, y), self.HANDLE, self.HANDLE)

        if self._label_rect.isEmpty():
            return
        # Etiket zemini TUVALIN rengidir, beyaz DEGIL: acik temada beyaz
        # kutucuklar acik gri tuvalde "etrafi belirgin olmayan" lekeler
        # gibi duruyordu. Zemin yalnizca altindaki oku maskeler.
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
