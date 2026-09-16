"""Diyagram tuvali: sahne kurulumu, arac kipleri, yakinlastirma, kaydirma."""

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


#: Yapistirilan parcanin orijinalden kaymasi (px). Ust uste binerse
#: kullanici kopyanin olustugunu goremez.
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
    # Fork ve join UML'de KALIN BIR CUBUK olarak cizilir; varsayilan olcu
    # dikey bir cubuktur (bolgeler yatay seritler oldugu icin).
    Tool.FORK: (StateKind.FORK, "Fork", 10.0, 90.0),
    Tool.JOIN: (StateKind.JOIN, "Join", 10.0, 90.0),
    # Baglanti noktalari bilesik durumun SINIRINDA durur; kucuk daireler.
    Tool.ENTRY_POINT: (StateKind.ENTRY_POINT, "EntryPoint", 18.0, 18.0),
    Tool.EXIT_POINT: (StateKind.EXIT_POINT, "ExitPoint", 18.0, 18.0),
    Tool.SUBMACHINE: (StateKind.SUBMACHINE, "Submachine", 220.0, 96.0),
}


class DiagramCanvas(CanvasNavigation, QGraphicsView):
    """Model ile grafik sahne arasindaki koprü."""

    #: Sol tusla bukmenin baslamasi icin gereken en kucuk hareket (sahne px).
    #: Altinda kalan hareket SECIM tikidir, bukme degil.
    BEND_THRESHOLD = 4.0
    #: Var olan bir kirilma noktasinin "tutulmus" sayildigi EKRAN yaricapi.
    BEND_GRAB_PX = 9.0

    selection_changed = pyqtSignal(list)     # secili eleman id'leri
    tool_finished = pyqtSignal()             # arac kullanildi -> Select'e don
    status_message = pyqtSignal(str)

    def __init__(self, doc: Document, parent=None) -> None:
        super().__init__(parent)
        self.doc = doc
        self.tool = Tool.SELECT
        self.snap_enabled = True
        self.show_grid = True
        # Yerlestirme sonrasi ozellik diyalogu (testler kapatabilir; modal).
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
        #: Sol tusla basildi ama henuz esigi asmadi (bkz. BEND_THRESHOLD).
        self._bend_candidate = None
        self._rubber_line: Optional[QGraphicsPathItem] = None
        self._pre_drag: Optional[str] = None
        self._panning = False
        self._pan_origin = QPointF()
        self._suppress_selection = False
        self._init_navigation()

        #: Ok bukme durumu (sag tus dogrudan, sol tus esikten sonra).
        self._bend_item = None
        self._bend_before = None
        self._bend_origin = QPointF()
        #: Surukleme sirasinda TASINAN kirilma noktasinin indisi.
        #: None ise henuz eklenmemistir (esik asilinca eklenir).
        self._bend_index = None
        #: Tutamak yakalandiginda hesaplanan (kip, indis) ikilisi.
        self._bend_grab = None
        #: ETIKET suruklemesi: (gecis ogesi, baslangic dx, dy, fare).
        #: Etiket okun kendisinden AYRI tutulur; kullanici etiketi
        #: okunur bir yere kaydirabilmelidir.
        self._label_drag = None
        #: FARK kipi: {"added": ..., "removed": ..., "changed": ...}
        self._diff_marks = {}
        #: Silinen elemanlarin hayalet cizimleri (MODELE AIT DEGIL).
        self._ghosts = []
        #: Sag tus ile dikdortgen secim. Qt'nin kendi RubberBandDrag'i
        #: YALNIZCA sol tusla calisir, bu yuzden kendi bandimizi ciziyoruz.
        self._band = QRubberBand(QRubberBand.Shape.Rectangle, self.viewport())
        self._band_origin = QPoint()

        self._scene.selectionChanged.connect(self._on_selection_changed)

    # ================================================================== kurulum

    def rebuild(self) -> None:
        """Modelden sahneyi bastan olusturur (secim korunur)."""
        selected = self.selected_ids()
        self._suppress_selection = True
        self._cancel_transition()

        self._scene.clear()
        # Sahne temizlendi: hayalet referanslari da dusurulmeli, yoksa
        # silinmis C++ nesnelerine erisilir.
        self._ghosts = []
        self.state_items.clear()
        self.tran_items.clear()

        sm = self.doc.machine
        # Once ust durumlar, sonra alt durumlar (ebeveyn once var olmali).
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
                continue        # bagli olmayan gecis cizilmez (dogrulayici uyarir)
            bow, label_t = routes.get(tr.id, (0.0, 0.5))
            item = TransitionItem(tr, src, dst, self, bow, label_t)
            self.tran_items[tr.id] = item
            self._scene.addItem(item)

        # BOLGELERI CIZIMDEN YENIDEN OKU.
        #
        # Bolge, ogenin durdugu SERITTEN okunur; serit sinirlari ise
        # bilesik durumun IC ALANINDAN cikar ve o alan iki sekilde
        # KAYABILIR:
        #
        #   * Bilesik duruma entry/exit/do metni yazilinca ustteki
        #     davranis seridi buyur (bkz. StateItem.content_rect).
        #   * Bolge sayisi artinca ayni alan daha cok serite bolunur.
        #
        # Her ikisinde de ogelerin KOORDINATI degismez ama hangi banda
        # dustukleri degisir. Model eski numarayi tasimaya devam ediyor,
        # dogrulayici da bir sey demiyordu: uretilen `state_region[]`
        # tablosu kullanicinin ekranda gordugu resimle CELISIYOR, en cok
        # da bolge sayisini 1'den 2'ye cikaran kullanicida -- orta
        # cizginin altindaki her oge ikinci bantta cizilip birinci
        # bolgede kaliyordu.
        #
        # Cizim modelin saf bir islevi oldugu icin bu yeniden okuma
        # belirlenimcidir ve kendini tekrar ettiginde bir sey degistirmez.
        self._sync_regions_from_drawing()

        self._update_scene_rect()
        self._suppress_selection = False
        self.set_selected_ids(selected)
        if self._diff_marks:
            # Yeniden kurulan ogeler isaretlerini kaybeder.
            self.set_diff_marks(self._diff_marks)

    def _compute_routes(self):
        """Ayni ikili arasindaki gecisleri birbirinden ayirir.

        Iki eksende birden ayirmak gerekir: `bow` egriyi yanlara acar,
        `label_t` ise etiketi yol uzerinde kaydirir. Yalnizca biri
        kullanilirsa uzun etiketler yine ust uste biner.
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
        """Tema degisiminde sahne zeminini yeniden okur.

        Sahne zemini KURULUM aninda QColor'a cevrildigi icin ``C``
        degistiginde kendiliginden guncellenmez; oge firca/kalemleri
        rebuild() ile zaten yeniden kurulur.
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
        """Diyagrami FARK kipinde boyar (eklenen / degisen / silinen).

        Kullanici calisma alaninda bir modele tikladiginda "ne degismis"
        sorusunun cevabini METIN olarak degil DIYAGRAM uzerinde gormek
        istiyor. Eklenen ve degisen elemanlar yerinde boyanir; SILINEN
        elemanlar yeni modelde bulunmadigi icin eski surumdeki konumlariyla
        HAYALET olarak cizilir (bkz. diagram_items.GhostItem).

        `marks` None ise fark kipi kapanir.
        """
        self._diff_marks = marks or {}
        eklenen = self._diff_marks.get("added") or set()
        degisen = self._diff_marks.get("changed") or set()

        for kimlik, item in list(self.state_items.items()) \
                + list(self.tran_items.items()):
            if kimlik in eklenen:
                yeni = "added"
            elif kimlik in degisen:
                yeni = "changed"
            else:
                yeni = ""
            if getattr(item, "diff_mark", "") != yeni:
                item.diff_mark = yeni
                item.update()

        self._rebuild_ghosts(self._diff_marks.get("removed") or {})

    def _rebuild_ghosts(self, removed: dict) -> None:
        """Silinen elemanlari eski konumlariyla hayalet olarak cizer."""
        for item in self._ghosts:
            if item.scene() is self._scene:
                self._scene.removeItem(item)
        self._ghosts = []
        if not removed:
            return
        for kimlik, veri in removed.items():
            # Yalnizca KONUMU olan elemanlar cizilebilir; gecisler ve
            # iliskiler uc noktalarina bagli oldugu icin (ve uclar da
            # silinmis olabilecegi icin) listede METIN olarak kalir.
            if "x" not in veri or "y" not in veri:
                continue
            ghost = GhostItem(veri)
            self._scene.addItem(ghost)
            self._ghosts.append(ghost)

    def set_active_states(self, ids: List[str]) -> None:
        """Simulasyondaki etkin durum zincirini vurgular."""
        active = set(ids)
        for sid, item in self.state_items.items():
            flag = sid in active
            if item.is_active != flag:
                item.is_active = flag
                item.update()

    # ================================================================== secim

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
        """Cift tiklama: ozellik diyalogunu acar ve degisikligi uygular."""
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

    # ================================================================== araclar

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

    # ================================================================== fare

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_origin = event.position()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return

        scene_pos = self.mapToScene(event.position().toPoint())

        # ---------------------------------------------------------- sag tus --
        #
        # Sag tus IKI ise yarar ve hedefe gore ayrilir:
        #   * bir GECISIN uzerinde  -> oku BUK (waypoint surukle)
        #   * bos alanda            -> dikdortgen SECIM
        # Ayrim tikin altindaki ogeye bakilarak yapilir; boylece iki islev
        # birbirini yemez.
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
            # SOL TUSLA DA OK BUKULEBILIR.
            #
            # Bukme yalnizca SAG tustaydi; kullanici oku "tutup
            # surukleyemedigini" bildirdi -- cunku kimse bir oku sag tusla
            # surukleyerek sekillendirmeyi denemez.
            #
            # Ama sol tik AYNI ZAMANDA secimdir: basar basmaz bukmeye
            # baslamak, gecisin ozelliklerini gormek icin uzerine tiklamayi
            # imkansiz kilardi. Bu yuzden burada yalnizca ADAY kaydedilir;
            # bukme, imlec esigi asarsa (bkz. mouseMoveEvent) baslar.
            # Kimildamadan birakilirsa tik sade bir SECIM olarak kalir.
            tran = self._transition_at(scene_pos)
            if tran is not None and self.tool is Tool.SELECT:
                # ETIKET ONCE. Etiketin uzerinden baslayan surukleme oku
                # BUKMEZ, etiketi TASIR; aksi halde etiketi kavramak
                # imkansizdi (etiket okun uzerinde durur).
                if tran.label_at(scene_pos):
                    # ETIKETE TIKLAMAK GECISI DE SECER.
                    #
                    # Bu dal `event.accept()` ile donuyordu, yani
                    # `super().mousePressEvent()` hic calismiyordu ve Qt
                    # secimi yapamiyordu: kullanici etikete tikladiginda
                    # ozellikler paneli bos kaliyor, Del bir sey silmiyordu.
                    # Etiket gecisin bir parcasidir; ona tiklamak gecisi
                    # secmelidir. Shift basiliysa secime EKLENIR.
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
        """Tutamak yaricapini SAHNE birimine cevirir.

        Sabit bir sahne yaricapi, yakinlastirmaya gore buyuyup kuculurdu:
        uzaklasinca noktayi yakalamak imkansiz, yakinlasinca her tik nokta
        tutmus sayilirdi. Olcek bolunerek yaricap EKRANDA sabit tutulur.
        """
        olcek = self.transform().m11() or 1.0
        return self.BEND_GRAB_PX / abs(olcek)

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
            oge, dx0, dy0, baslangic = self._label_drag
            simdi = self.mapToScene(event.position().toPoint())
            fark = simdi - baslangic
            oge.transition.label_dx = round(dx0 + fark.x(), 2)
            oge.transition.label_dy = round(dy0 + fark.y(), 2)
            oge.update_path()
            self._update_autoscroll(event.position().toPoint())
            event.accept()
            return

        # ADAY -> GERCEK bukme: imlec esigi asinca basla.
        #
        # Esik olmadan her secim tiklamasi minik bir bukme sayilir ve ok
        # farkedilmeden kayardi.
        if self._bend_item is None and self._bend_candidate is not None:
            simdi = self.mapToScene(event.position().toPoint())
            fark = simdi - self._bend_origin
            if abs(fark.x()) >= self.BEND_THRESHOLD \
                    or abs(fark.y()) >= self.BEND_THRESHOLD:
                self._bend_item = self._bend_candidate
                self._bend_candidate = None
                self._bend_before = self.doc.machine.to_json()
                self.viewport().setCursor(Qt.CursorShape.SizeAllCursor)

        if self._bend_item is not None:
            # COK NOKTALI YONLENDIRME.
            #
            # Onceki surum, nereden cekilirse cekilsin butun kirilma
            # noktalarini TEK bir noktayla degistiriyordu; ok her zaman tek
            # bir "V" olabiliyordu ve ikinci bir kivrim eklemek mumkun
            # degildi. Artik tutulan yer onemlidir: var olan bir noktanin
            # uzerindeyse O NOKTA tasinir, degilse tutulan PARCAYA yeni bir
            # nokta eklenir ve o tasinir. Boylece ok, her noktasindan
            # cekilerek istenen kadar kivrimla sekillendirilir.
            pos = self.mapToScene(event.position().toPoint())
            if self.snap_enabled:
                pos = QPointF(snap(pos.x()), snap(pos.y()))
            noktalar = self._bend_item.transition.waypoints

            if self._bend_index is None:
                kip, indis = self._bend_grab or ("insert", 0)
                if kip == "move" and indis < len(noktalar):
                    self._bend_index = indis
                else:
                    indis = max(0, min(indis, len(noktalar)))
                    noktalar.insert(indis, [round(pos.x(), 2),
                                            round(pos.y(), 2)])
                    self._bend_index = indis

            noktalar[self._bend_index] = [round(pos.x(), 2), round(pos.y(), 2)]
            self._bend_item.update_path()
            # Ok bukerken de kenardan kaydir: uzun bir oku ekranin disina
            # dogru sekillendirmek aksi halde imkansizdi.
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

        surukluyor = bool(event.buttons() & Qt.MouseButton.LeftButton)
        if surukluyor:
            # Sahneyi ONCE buyut: super() ogeyi tasirken yer hazir olsun,
            # yoksa oge sahne sinirinda kalir ve birakma aninda ziplardi.
            self._room_for_drag(event.position().toPoint())

        super().mouseMoveEvent(event)

        if surukluyor:
            self._update_autoscroll(event.position().toPoint())
        else:
            self._stop_autoscroll()

    def mouseReleaseEvent(self, event) -> None:
        # Fare birakildi: kenardan kaydirma HER kosulda durur. Asagida
        # birden fazla erken 'return' var; durdurmayi onlara birakmak
        # zamanlayicinin acik kalmasina yol acardi.
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

        # Sol tusla baslayan bukme ADAYI, hic kimildamadan birakildiysa
        # sadece bir secim tikiydi: iz birakmadan dusur.
        self._bend_candidate = None
        if self._bend_item is None:
            self._bend_grab = None
            self._bend_index = None

        if self._bend_item is not None and event.button() in (
                Qt.MouseButton.RightButton, Qt.MouseButton.LeftButton):
            item, before = self._bend_item, self._bend_before
            tasima = (self.mapToScene(event.position().toPoint())
                      - self._bend_origin)
            self._bend_item = None
            self._bend_before = None
            self.viewport().setCursor(
                Qt.CursorShape.ArrowCursor if self.tool is Tool.SELECT
                else Qt.CursorShape.CrossCursor)

            grab = self._bend_grab
            self._bend_grab = None
            self._bend_index = None

            # SURUKLEMEDEN birakmak oku DUZLESTIRIR -- ama YALNIZCA sag
            # tusta. Sol tusta ayni davranis, gecisi secmek icin yapilan
            # her tiklamada bukumu silerdi. (Sol tusta zaten esik asilmadan
            # buraya gelinmez.)
            durgun = (event.button() == Qt.MouseButton.RightButton
                      and abs(tasima.x()) < 3.0 and abs(tasima.y()) < 3.0)
            if durgun and grab and grab[0] == "move" \
                    and grab[1] < len(item.transition.waypoints):
                # Bir NOKTANIN uzerinde sag tik: yalnizca o noktayi kaldir.
                # Tek kivrimi silmek icin okun tamamini duzlestirmek
                # gerekmemeli.
                item.transition.waypoints.pop(grab[1])
                item.update_path()
                self.status_message.emit("Bend point removed.")
            elif durgun:
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

        # SURUKLE-BIRAK ile gecis. Iki kullanim da desteklenir:
        #   * tikla-tikla : kaynaga tikla, hedefe tikla
        #   * surukle     : kaynakta basili tut, hedefte birak
        # Ayrim, birakma noktasinin KAYNAKTAN FARKLI bir durum olmasidir;
        # aksi halde tikla-tikla akisinin ILK birakmasi gecisi hemen
        # tamamlar ve kullanici hedefi hic secemez.
        if (event.button() == Qt.MouseButton.LeftButton
                and self.tool is Tool.TRANSITION
                and self._pending_source is not None):
            scene_pos = self.mapToScene(event.position().toPoint())
            hedef = self._state_at(scene_pos)
            if hedef is not None and hedef is not self._pending_source:
                self._handle_transition_click(scene_pos)
                event.accept()
                return

        if event.button() == Qt.MouseButton.RightButton and self._band.isVisible():
            rect = self._band.geometry()
            self._band.hide()
            # Cok kucuk dikdortgen = kazara tiklama; secimi bozmayalim.
            if rect.width() > 3 and rect.height() > 3:
                path_rect = self.mapToScene(rect).boundingRect()
                secili = [i for i in self._scene.items(path_rect)
                          if isinstance(i, StateItem)]
                if not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                    self._scene.clearSelection()
                for it in secili:
                    it.setSelected(True)
                self.status_message.emit("%d element(s) selected." % len(secili))
            event.accept()
            return

        super().mouseReleaseEvent(event)
        # Surukleme bitti: kilavuzlar kalkar.
        self.align_clear()

        if self._pre_drag is not None and self.tool is Tool.SELECT:
            snapshot = self._pre_drag
            self._pre_drag = None
            reparented = self._apply_reparenting()
            self._write_geometry_to_model(skip=reparented)
            if self.doc.machine.to_json() != snapshot:
                self.doc.edit_from("Move / resize", snapshot)

    def commit_geometry(self, label: str) -> None:
        """StateItem tarafindan boyutlandirma bitiminde cagrilir."""
        snapshot = self._pre_drag
        self._pre_drag = None
        self._write_geometry_to_model()
        if snapshot is not None and self.doc.machine.to_json() != snapshot:
            self.doc.edit_from(label, snapshot)

    def _write_geometry_to_model(self, skip: Optional[Set[str]] = None) -> None:
        """Sahnedeki konumlari modele yazar.

        ``skip``: bu islemde ust durumu DEGISEN elemanlar. Onlarin koordinati
        `_apply_reparenting` tarafindan yeni ebeveyne GORE hesaplanmistir;
        `item.pos()` ise hala eski (sahne) konumudur, cunku grafik agac ancak
        yeniden cizimde guncellenir. Uzerine yazilirsa eleman ebeveyninin
        disinda cizilir.
        """
        skipped = skip or set()
        kaymalar = {}
        for eid, item in self.state_items.items():
            if eid in skipped:
                continue
            dx = round(item.pos().x(), 2) - round(item.state.x, 2)
            dy = round(item.pos().y(), 2) - round(item.state.y, 2)
            if dx or dy:
                kaymalar[eid] = (dx, dy)
            item.state.x = round(item.pos().x(), 2)
            item.state.y = round(item.pos().y(), 2)
            item.state.w = round(item.state.w, 2)
            item.state.h = round(item.state.h, 2)
            self._assign_region(item)
        if kaymalar:
            self._shift_waypoints(kaymalar)

    def _shift_waypoints(self, kaymalar) -> None:
        """Rijit tasinan bir alt resmin ARA NOKTALARINI da tasir.

        Gecis ara noktalari MUTLAK SAHNE koordinatinda saklanir, alt
        durumlar ise ebeveynlerine GORE. Bir bilesik durum surukleninc
        cocuklari onunla birlikte gider ama ara noktalar yerinde kalir:
        kullanicinin elle bicimlendirdigi yol kutunun disina sarkar ve bu
        haliyle KAYDEDILIR. Dosyayi yeniden acmak da duzeltmez.

        Yalnizca IKI UCU DA AYNI KADAR kayan gecisler tasinir -- yani
        resmin o parcasi butun olarak yer degistirmistir ve yolun sekli
        anlamini korur. Tek ucu kayan bir geciste ara noktalar
        kullanicinin sahnede sectigi yerlerdir; onlara dokunmak, elle
        yapilmis bir bicimlendirmeyi bozmak olurdu.
        """
        sm = self.doc.machine

        def sahne_kaymasi(sid):
            """Ogenin SAHNEDEKI toplam kaymasi (kendisi + butun ustleri)."""
            dx = dy = 0.0
            cur = sid
            gorulen = set()
            while cur is not None and cur not in gorulen:
                gorulen.add(cur)
                adim = kaymalar.get(cur)
                if adim is not None:
                    dx += adim[0]
                    dy += adim[1]
                st = sm.states.get(cur)
                cur = st.parent if st is not None else None
            return (round(dx, 2), round(dy, 2))

        onbellek = {}
        for tr in sm.transitions.values():
            if not tr.waypoints:
                continue
            for uc in (tr.source, tr.target):
                if uc not in onbellek:
                    onbellek[uc] = sahne_kaymasi(uc)
            kayma = onbellek[tr.source]
            if kayma != onbellek[tr.target] or kayma == (0.0, 0.0):
                continue
            tr.waypoints = [[round(x + kayma[0], 2), round(y + kayma[1], 2)]
                            for x, y in tr.waypoints]

    @staticmethod
    def _region_for(ust: Optional[QGraphicsItem],
                    yerel_ust_kenar: float, yukseklik: float) -> int:
        """`ust`un hangi seridine dusuldugunu YEREL koordinattan hesaplar.

        Konum `item.pos()` uzerinden DEGIL, disaridan verilen yerel
        koordinattan okunur. Sebebi: bir oge yeni bir ebeveyne
        tasindiginda ya da daha yeni yaratildiginda grafik agac henuz
        guncel degildir -- `pos()` eski sahne konumunu, `parentItem()`
        eski ebeveyni verir. O anlarda hesaplanan bolge yanlis cikardi.
        """
        if not isinstance(ust, StateItem) or ust.region_count() <= 1:
            return 0
        return ust.region_at(yerel_ust_kenar + yukseklik / 2.0)

    def _fit_pasted_into(self, host: StateItem, ids: List[str],
                         scene_pos: QPointF) -> None:
        """Yapistirilan KOKLERI birakma noktasina tasir ve icine sigdirir.

        `paste_fragment` kokleri KENDI eski koordinatlarina PASTE_OFFSET
        ekleyerek koyar. Hedef bir bilesik durumsa o koordinat artik ust
        durumun YEREL cercevesinde okunur ve parca cogu zaman kutunun
        tamamen DISINA duser: kok bolgede y=600'de duran bir durumu, ic
        alani y=34..290 olan bir bilesige yapistirmak onu uc yuz piksel
        asagiya koyuyordu. Kullanici "yapistirdim, hicbir sey olmadi"
        diyordu -- oysa oge vardi, ekranin gormedigi bir yerdeydi. Bolge
        numarasi da o durumda anlamsizdi (kelepcelenmis bir deger).

        Parca, imlecin bulundugu noktaya ORTALANIR ve ust durumun ic
        alanina kelepcelenir; parcanin kendi ic duzeni korunur.
        """
        sm = self.doc.machine
        kokler = [sm.states[i] for i in ids
                  if i in sm.states and sm.states[i].parent == host.state.id]
        if not kokler:
            return
        sol = min(st.x for st in kokler)
        ust = min(st.y for st in kokler)
        genislik = max(st.x + st.w for st in kokler) - sol
        yukseklik = max(st.y + st.h for st in kokler) - ust

        alan = host.content_rect()
        yerel = host.mapFromScene(scene_pos)
        hedef_x = yerel.x() - genislik / 2.0
        hedef_y = yerel.y() - yukseklik / 2.0
        hedef_x = min(max(hedef_x, alan.left()),
                      max(alan.left(), alan.right() - genislik))
        hedef_y = min(max(hedef_y, alan.top()),
                      max(alan.top(), alan.bottom() - yukseklik))

        dx = hedef_x - sol
        dy = hedef_y - ust
        for st in kokler:
            st.x = round(st.x + dx, 2)
            st.y = round(st.y + dy, 2)

    def _sync_regions_from_drawing(self) -> bool:
        """Her ogenin bolgesini, CIZILDIGI seritten yeniden okur.

        :return: modelde bir sey degistiyse True
        """
        degisti = False
        for item in self.state_items.values():
            yeni = self._region_for(item.parentItem(), item.pos().y(),
                                    item.state.h)
            if int(item.state.region or 0) != yeni:
                item.state.region = yeni
                degisti = True
        return degisti

    def _assign_region(self, item: StateItem) -> None:
        """Ogeyi, ust durumun HANGI bolgesinde durdugu bilgisiyle isaretler.

        Bolge, diyagramda gorunen seritten OKUNUR: kullanici ogeyi hangi
        banda birakirsa o bolgeye girer. Ayri bir "bolge sec" alani
        olsaydi resim ile model birbirinden ayrilabilir, uretilen kod
        cizimin anlatmadigi bir seyi yapardi.
        """
        item.state.region = self._region_for(
            item.parentItem(), item.pos().y(), item.state.h)

    def _apply_reparenting(self) -> Set[str]:
        """Bir durum bilesik durumun uzerine birakildiysa hiyerarsiyi gunceller.

        :return: ust durumu degisen ve koordinati burada belirlenen eleman id'leri
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
            # BOLGEYI DE BURADA YAZ. Bu ogeler `_write_geometry_to_model`
            # tarafindan ATLANIR (koordinatlari zaten burada hesaplandi),
            # dolayisiyla tek bolge yazan yer burasidir. Atlaninca, ogeyi
            # ortogonal bir duruma surukleyen kullanici diyagramda ikinci
            # seritte duran ama uretilen `state_region[]` tablosunda
            # birinci bolgede gorunen bir durum elde ediyordu -- hicbir
            # uyari vermeden.
            item.state.region = self._region_for(target, local.y(),
                                                 item.state.h)
            moved.add(item.state.id)
            changed = True
        if changed:
            self.status_message.emit("Hierarchy updated.")
        return moved

    def _composite_at(self, scene_pos: QPointF, exclude: StateItem) -> Optional[StateItem]:
        """Noktadaki en ictekki bilesik durum (kendisi ve alt agaci haric)."""
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

    # ------------------------------------------------------- eleman olusturma

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

        # Paletten birakilan oge de dustugu seride girer. Bu satir
        # olmadan HER yeni oge birinci bolgeye yaziliyordu: ortogonal bir
        # durumu paletle kurmak imkansizdi, cunku ikinci serite birakilan
        # durum modelde birinci bolgede kaliyordu.
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
            # Sozde-duruma kendine gecis: makine o dugumde asili kalir.
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

        # OZELLIK PENCERESI hemen acilir. Yeni bir gecis olay/guard/eylem
        # olmadan anlamsizdir; kullaniciyi ayrica cift tiklamaya zorlamak
        # yerine alanlar dogrudan sorulur. Durum yerlestirmede de ayni
        # davranis var (bkz. _place_state), boylece iki arac tutarli.
        if self.auto_edit:
            self.edit_element(new_tran.id)

    def _transition_at(self, scene_pos: QPointF) -> Optional[TransitionItem]:
        """Verilen noktadaki gecis oku (yoksa None).

        `TransitionItem.shape()` oku 12 px kalinlikta bir seride genisletir,
        bu yuzden ince cizgiyi tam isabetle tutturmak gerekmez.
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

    # ================================================================== klavye

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

    # ============================================================ kopyala/yapistir

    def copy_selection(self) -> bool:
        """Secili durumlari (alt agaclariyla) SISTEM panosuna koyar.

        Sistem panosu kullanilir, uygulama ici bir degisken degil: boylece
        ayni parca ikinci bir pencereye de yapistirilabilir ve kullanicinin
        alistigi Ctrl+C davranisi korunur. Yuk JSON'dur; tur imzasi
        tasidigi icin baska bir metin yapistirilirsa sessizce yok sayilir.
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
        """Panodaki parcayi yapistirir ve YAPISTIRILANI secer.

        Hedef ust durum, imlecin ustundeki bilesik durumdur; imlec bos
        alandaysa kok bolgeye yapistirilir. Kopya orijinalin tam ustune
        binmesin diye ::PASTE_OFFSET kadar kaydirilir.

        ``scene_pos``: hedefi ACIKCA verir. Menuden ya da kisayoldan
        gelen cagri bunu bos birakir ve imlec kullanilir; testler ise
        gercek fare imlecini oynatmadan belirli bir noktaya yapistirmak
        icin verir -- aksi halde yapistirma yolu OLCULEMEZ kalirdi.
        """
        payload = QApplication.clipboard().text()
        if not payload.strip():
            return

        if scene_pos is not None:
            pos = QPointF(scene_pos)
        else:
            yerel = self.viewport().mapFromGlobal(self.cursor().pos())
            if self.viewport().rect().contains(yerel):
                pos = self.mapToScene(yerel)
            else:
                pos = self.mapToScene(self.viewport().rect().center())

        host = self._composite_at_point(pos)
        # StateItem'in kimligi `state.id`dir; `state_id` diye bir alan HIC
        # OLMADI. Imlec bir bilesik durumun uzerindeyken yapistirmak
        # AttributeError ile cokuyordu -- en sik kullanilan yapistirma
        # bicimi tam olarak buydu (parcayi bir bilesik durumun icine koymak).
        parent_id = host.state.id if host is not None else None

        before = self.doc.machine.to_json()
        try:
            yeni = paste_fragment(self.doc.machine, payload,
                                  parent=parent_id,
                                  dx=PASTE_OFFSET, dy=PASTE_OFFSET)
        except ValueError:
            # Panoda bizim parcamiz yok (baska bir uygulamadan metin).
            # Sessizce yok say: kullanici Ctrl+V'yi baska bir sey icin
            # kullanmis olabilir, uyari kutusu acmak rahatsiz edici olurdu.
            return
        if not yeni:
            return

        # Once KONUM, sonra bolge: bolge zaten konumdan turetilir.
        #
        # Geri alma anlik goruntusu ("before") bu satirlardan ONCE
        # alindigi icin duzeltmeler de geri alinabilir.
        if host is not None:
            self._fit_pasted_into(host, yeni, pos)
            if host.region_count() > 1:
                for sid in yeni:
                    st = self.doc.machine.states.get(sid)
                    if st is not None and st.parent == parent_id:
                        st.region = self._region_for(host, st.y, st.h)

        self.doc.edit_from("Paste", before)
        self.rebuild()
        self.set_selected_ids(yeni)
        self.status_message.emit("Pasted %d element(s)." % len(yeni))

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

    # ================================================================== gorunum

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

    # ------------------------------------------------------------- arka plan

    def sibling_boxes(self, item: StateItem):
        """Hizalanilacak komsularin kutulari (ogenin EBEVEYN koordinati).

        Yalnizca AYNI ebeveyne sahip durumlar alinir: bir bilesik durumun
        icindeki alt durum, disaridaki durumlara degil kardeslerine
        hizalanir -- koordinat uzaylari da zaten ayridir.

        Secili ogeler DISARIDA birakilir: onlar suruklenen ogeyle
        BIRLIKTE hareket eder, birbirlerine hizalanmalari anlamsizdir.
        """
        ebeveyn = item.parentItem()
        kutular = []
        for baska in self.state_items.values():
            if baska is item or baska.parentItem() is not ebeveyn:
                continue
            if baska.isSelected():
                continue
            kutular.append((baska.pos().x(), baska.pos().y(),
                            baska.state.w, baska.state.h))
        return kutular

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
