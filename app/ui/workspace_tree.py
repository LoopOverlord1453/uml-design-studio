"""Calisma alani agaci: klasorler, model dosyalari ve her modelin ICERIGI.

NEDEN TEK AGAC
--------------
Bir calisma alaninda birden fazla model bulunur (bir sistem + alt
sistemleri). Onceki "MODEL TREE" paneli YALNIZCA acik olan modeli
gosteriyordu; diger modellerin varligi arayuzde hicbir yerde gorunmuyor,
kullanici Dosya > Ac ile klasoru elle gezmek zorunda kaliyordu.

Burada tek bir agac var:

    model/
      pump/                     <- alt sistem (klasor)
        pump.usm                <- model dosyasi (ACILIR)
          ● Initial · Start     <- modelin ICERIGI
          ▣ Composite · Running
      classes.ucd

Her model dugumu ACILIP KAPANABILIR ve icerigi ANCAK ACILINCA okunur
(tembel yukleme). Yuzlerce modelli bir calisma alaninda hepsini acilista
ayristirmak paneli dakikalarca kilitlerdi; ayrica bozuk bir dosya
uygulamayi acilista coketmemelidir -- okuma hatasi o dugumde bir uyari
satirina donusur.
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

#: Dugumun tasidigi mutlak dosya yolu (model dosyalari icin).
PATH_ROLE = Qt.ItemDataRole.UserRole + 11
#: Dugum turu: "dir" | "model" | "element" | "note"
NODE_ROLE = Qt.ItemDataRole.UserRole + 12
#: Model dugumu icerigi yuklendi mi (tembel yukleme bayragi).
LOADED_ROLE = Qt.ItemDataRole.UserRole + 13
#: Dugumun gosterdigi YAPININ imzasi (bkz. _structure_signature).
SIGNATURE_ROLE = Qt.ItemDataRole.UserRole + 14

MODEL_SUFFIXES = (".usm", ".ucd", ".json")

#: Bos panelde gosterilen yol tarifi.
#:
#: Menu yolu ve kisayol ELLE yazilir ama testte GERCEK eylemle
#: karsilastirilir (bkz. test_regressions "bos panel ipucu"). Ilk yazimda
#: buraya "File > Open Workspace... (Ctrl+Shift+O)" konmustu; eylemin
#: gercek adi "Workspace..." ve kisayolu Ctrl+Shift+W idi -- yani ipucu
#: kullaniciyi var olmayan bir menu ogesine yolluyordu.
OPEN_WORKSPACE_MENU = "File ▸ Workspace…"
OPEN_WORKSPACE_KEY = "Ctrl+Shift+W"
OPEN_WORKSPACE_HINT = "%s (%s)" % (OPEN_WORKSPACE_MENU, OPEN_WORKSPACE_KEY)


class WorkspaceTree(QTreeWidget):
    """Calisma alanindaki butun modelleri ve iceriklerini gosterir."""

    #: Kullanici bir model dosyasini actiginda (cift tiklama).
    model_activated = pyqtSignal(str)
    #: ACIK modelde bir eleman secildiginde (tuvalle esitlenir).
    element_activated = pyqtSignal(str)
    #: Bir model dosyasi calisma alanindan SILINDIGINDE.
    model_removed = pyqtSignal(str)
    #: Bir model DOSYASI secildiginde (tiklama; acmak icin degil).
    #: Ana pencere bunu FARK kipini kurmak icin kullanir.
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

        # Panel ACILISTA BOS KALMASIN. `rebuild` yalnizca bir calisma alani
        # uygulandiginda cagriliyordu; henuz hicbiri secilmemisken panelin
        # basligi ("WORKSPACE") duruyor ama altinda tek satir bile
        # bulunmuyordu -- kullaniciya ne oldugunu ya da ne yapacagini
        # anlatan hicbir sey yok.
        self.rebuild()

        self.itemExpanded.connect(self._on_expanded)
        self.itemDoubleClicked.connect(self._on_double_click)
        self.itemSelectionChanged.connect(self._on_selection)

        # SAG TIK MENUSU + Delete tusu: model dosyasini calisma alanindan
        # kaldirmanin iki yolu. Agac tek gezinme araci oldugu icin dosya
        # silmek icin isletim sistemi gezginine cikmak gerekmemeli.
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        apply_contrast(self)

    # ------------------------------------------------------------------ API #

    def set_workspace(self, workspace, open_models: Optional[Dict] = None) -> None:
        """Agaci calisma alanindan yeniden kurar.

        :param open_models: {mutlak yol: model nesnesi} -- ACIK belgeler.
            Acik bir model diskteki surumu degil, DUZENLENMEKTE olan
            surumu gostermelidir; aksi halde agac kaydedilmemis
            degisiklikleri gormez.
        """
        self._workspace = workspace
        self._open_paths = dict(open_models or {})
        self.rebuild()

    def set_open_models(self, open_models: Dict) -> None:
        """ACIK modelleri bildirir; agac yalnizca DISKTEKI dosyalari gosterir."""
        onceki = {self._norm(k) for k in self._open_paths}
        self._open_paths = dict(open_models or {})
        if {self._norm(k) for k in self._open_paths} != onceki:
            # Acik dosya KUMESI degisti: isaretler (dolu/bos elmas, renk)
            # yeniden cizilmeli.
            self.rebuild()
            return
        self.refresh_open_nodes()

    def rebuild(self) -> None:
        genisleyenler = self._expanded_paths()
        self._suppress = True
        self.clear()

        # AGAC YALNIZCA DISKTEKI DOSYALARI GOSTERIR.
        #
        # Onceden, dosyasi olmayan acik belgeler en uste
        # "◆ untitled.usm  (not saved)" olarak ekleniyordu. Uygulama
        # acilista HER IKI kip icin de birer ornek belge kurdugundan bu
        # satirlar KALICI hale gelmisti: kullanici blinky.usm uzerinde
        # calisirken bile agacin tepesinde, calisma alaniyla hicbir
        # ilgisi olmayan iki "untitled" duruyordu. Panelin adi WORKSPACE;
        # icerigi de calisma alani olmali.
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
        # STATE_TITLE tuvaldeki renkli baslik seridi icindir ve iki temada da
        # beyazdir; acik temada beyaz agac zemininde kaybolurdu.
        kok.setForeground(0, QBrush(QColor(C.TEXT_BRIGHT)))
        kok.setData(0, NODE_ROLE, "dir")
        kok.setData(0, PATH_ROLE, self._workspace.model_path)
        kok.setToolTip(0, self._workspace.root)

        self._fill_dir(kok, self._workspace.model_path)
        kok.setExpanded(True)
        self._restore_expanded(genisleyenler)
        self._suppress = False

    def _bilgi_satiri(self, *satirlar: str) -> None:
        """Agac yerine gosterilen aciklama satirlari (tiklanamaz)."""
        for metin in satirlar:
            dugum = QTreeWidgetItem(self, [metin])
            dugum.setForeground(0, QBrush(QColor(C.TEXT_DIM)))
            dugum.setData(0, NODE_ROLE, "note")
            dugum.setFlags(Qt.ItemFlag.ItemIsEnabled)

    @staticmethod
    def _structure_signature(model):
        """Agacta GORUNEN yapinin ucuz bir imzasi.

        Konum degisiklikleri (surukleme) agaci ETKILEMEZ ama yine de
        Document.changed yayar. Alt agaci her jestte yikip yeniden kurmak
        200 sinifli bir modelde ~157 ms suruyordu ve surukleme takiliyordu.
        Imza yalnizca agaca yazilan alanlardan olusur; degismediyse cizim
        tamamen atlanir.
        """
        if isinstance(model, StateMachine):
            # entry/exit/do DA imzaya girer: agac artik davranislari da
            # gosteriyor, dolayisiyla bir davranisin degismesi alt agaci
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
        """Bir modelin icerigini dugume isler (durum makinesi ya da sinif)."""
        if isinstance(model, StateMachine):
            self._fill_states(node, model, None)
            if node.childCount() == 0:
                self._note(node, "(empty model)", C.TEXT_DIM)
        else:
            self._fill_class_model(node, model)
            if node.childCount() == 0:
                self._note(node, "(empty model)", C.TEXT_DIM)

    def refresh_open_nodes(self) -> None:
        """Acik modellerin alt agacini tazeler (kaydedilmemis degisiklikler)."""
        for item in self._all_items():
            if item.data(0, NODE_ROLE) != "model":
                continue
            yol = item.data(0, PATH_ROLE) or ""
            if self._norm(yol) not in {self._norm(k) for k in self._open_paths}:
                continue
            if not item.data(0, LOADED_ROLE):
                continue
            # Salt KONUM degisikliginde alt agaci yeniden kurma (bkz.
            # _structure_signature): buyuk modellerde surukleme takiliyordu.
            model = next((m for k, m in self._open_paths.items()
                          if self._norm(k) == self._norm(yol)), None)
            if model is not None:
                imza = self._structure_signature(model)
                if item.data(0, SIGNATURE_ROLE) == imza:
                    continue
                item.setData(0, SIGNATURE_ROLE, imza)
            self._load_model_node(item, force=True)

    def select_element(self, eid: str) -> None:
        """Acik modeldeki bir elemani secer (tuvalden gelen secim)."""
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

    # ------------------------------------------------------------- kurulum -- #

    @staticmethod
    def _norm(path: str) -> str:
        return os.path.normcase(os.path.abspath(path)) if path else ""

    def _fill_dir(self, parent: QTreeWidgetItem, path: str) -> None:
        """Klasoru agaca isler: once alt klasorler, sonra model dosyalari."""
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
            # Bos calisma alani bir CIKMAZ olmamali: kullaniciya dosyanin
            # oraya nasil girecegi soylenir. Eski metin ("(no model files
            # yet)") durumu bildiriyor ama ne yapilacagini sylemiyordu.
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

        # Acilabilir gorunsun diye gecici bir cocuk konur; gercek icerik
        # dugum ACILINCA okunur (bkz. _on_expanded).
        QTreeWidgetItem(dugum, ["…"])

    # -------------------------------------------------------- tembel yukleme #

    def _on_expanded(self, item: QTreeWidgetItem) -> None:
        if item.data(0, NODE_ROLE) == "model" and not item.data(0, LOADED_ROLE):
            self._load_model_node(item)

    def _load_model_node(self, item: QTreeWidgetItem, force: bool = False) -> None:
        """Model dosyasini okur ve icerigini alt agac olarak kurar."""
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
                # Bozuk yada okunamayan dosya AGACI COKERTMEZ: dugumde bir
                # uyari satiri gorunur, diger modeller calismaya devam eder.
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

            # DAVRANISLAR (entry / exit / do) agacta da gorunur.
            #
            # Agac, sinif diyagraminda oznitelik ve islemleri zaten
            # listeliyor; durum makinesinde yalnizca gecisleri gosterip
            # davranislari atlamak tutarsizdi. Ustelik bilesik bir durumun
            # exit/do davranisi kutuya sigmayabilir -- agac o zaman tek
            # guvenilir kaynaktir.
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
        """Acik bir sinif modelini agaca isler.

        Ayri bir "MODEL TREE" paneli KALDIRILDIGI icin bu agac artik tek
        gezinme aracidir; bu yuzden yalnizca sinif adlarini degil,
        oznitelikleri, islemleri ve iliskileri de gostermek zorundadir --
        aksi halde panelin kaldirilmasi bilgi kaybi olurdu.
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
        """KAPALI bir sinif diyagramini diskten okuyup agaca isler.

        AYNI cizim yolunu kullanir (_fill_class_model): ayri bir JSON
        ozetleyici yazmak, oznitelik/islem bicimini iki yerde tutmak
        demekti ve kullanici dosyayi acinca gorunum degisirdi.
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

    # ----------------------------------------------------------- etkilesim -- #

    # ------------------------------------------------------- dosya kaldirma #

    def _selected_model_path(self) -> Optional[str]:
        """Secili dugum bir MODEL DOSYASI ise mutlak yolu, degilse None."""
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
        """Secili model dosyasini ONAY ALARAK siler.

        Silme GERI ALINAMAZ ve disk uzerinde is yapar; bu yuzden her zaman
        sorulur. Acik bir modeli silmek kullaniciyi dosyasiz bir belgeyle
        birakir, o durum ayrica uyarilir.
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

    # ------------------------------------------------------------ yardimci -- #

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
