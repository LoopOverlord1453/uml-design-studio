"""Sorun listesi paneli ve agac gosterimi icin ortak sembol tablolari."""

from __future__ import annotations

from typing import List

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (QAbstractItemView, QHeaderView, QTreeWidget,
                             QTreeWidgetItem)

from ..core.class_model import RelationKind, Stereotype
from ..core.model import StateKind
from ..core import uml_spec
from ..core.validator import Issue
from .contrast import apply_contrast
from .theme import C, mono_font, ui_font

ID_ROLE = Qt.ItemDataRole.UserRole + 1

KIND_GLYPH = {
    StateKind.SIMPLE: "▢",
    StateKind.COMPOSITE: "▣",
    StateKind.INITIAL: "●",
    StateKind.FINAL: "◉",
    StateKind.CHOICE: "◇",
    StateKind.JUNCTION: "◆",
    StateKind.SHALLOW_HISTORY: "Ⓗ",
    StateKind.DEEP_HISTORY: "Ⓗ*",
    StateKind.TERMINATE: "✕",
    # UML'de fork/join KALIN BIR CUBUK, baglanti noktalari ise bilesik
    # durumun sinirindaki kucuk dairelerdir; agacta da oyle anilirlar.
    StateKind.FORK: "▬",
    StateKind.JOIN: "▬",
    StateKind.ENTRY_POINT: "○",
    StateKind.EXIT_POINT: "⊗",
    StateKind.SUBMACHINE: "▤",
}

#: Simgenin YANINA yazilan arac adi (UML terimleri).
#:
#: Sembol tek basina yeterli degildi: 12 pikselde "◇" ile "◆" (choice /
#: junction) ve "▢" ile "▣" (simple / composite) birbirinden ayirt
#: edilemiyordu. Arac adi yazili oldugu icin agac artik tuvaldeki arac
#: cubugu ile ayni dili konusur.
KIND_LABEL = {
    StateKind.SIMPLE: "State",
    StateKind.COMPOSITE: "Composite",
    StateKind.INITIAL: "Initial",
    StateKind.FINAL: "Final",
    StateKind.CHOICE: "Choice",
    StateKind.JUNCTION: "Junction",
    StateKind.SHALLOW_HISTORY: "History",
    StateKind.DEEP_HISTORY: "Deep History",
    StateKind.TERMINATE: "Terminate",
    StateKind.FORK: "Fork",
    StateKind.JOIN: "Join",
    StateKind.ENTRY_POINT: "Entry Point",
    StateKind.EXIT_POINT: "Exit Point",
    StateKind.SUBMACHINE: "Submachine",
}

# HER StateKind ICIN BIR KAYIT SART.
#
# Agac dugumleri `KIND_GLYPH[st.kind]` ile kuruluyor; eksik bir tur orada
# KeyError'a donuyordu. Fork, join, giris/cikis noktasi ve altmakine
# eklendiginde bu iki tablo guncellenmemisti: o ogelerden birini iceren
# bir modeli calisma alaninda GORUNTULEMEK bile mumkun degildi. Sessiz
# bir varsayilana dusmek yerine burada patlamak dogrudur -- ama testin
# yakalamasi icin (bkz. test_regressions 41. bolum) once bu kontrol var.
assert set(KIND_GLYPH) == set(StateKind), \
    "KIND_GLYPH eksik: %s" % sorted(k.value for k in StateKind
                                    if k not in KIND_GLYPH)
assert set(KIND_LABEL) == set(StateKind), \
    "KIND_LABEL eksik: %s" % sorted(k.value for k in StateKind
                                    if k not in KIND_LABEL)

SEVERITY_GLYPH = {"error": "✖", "warning": "▲", "info": "ℹ"}
SEVERITY_COLOR = {"error": C.RED, "warning": C.WARN, "info": C.INFO}
SEVERITY_TEXT = {"error": "Error", "warning": "Warning", "info": "Info"}


# OutlinePanel / ClassOutlinePanel KALDIRILDI.
#
# Ayri bir "MODEL TREE" paneli, calisma alani agacinin zaten
# gosterdigi bilgiyi ikinci kez gosteriyordu. Asagidaki sembol ve
# etiket tablolari KALIR: hem durum makinesi hem sinif diyagrami
# dugumleri workspace_tree.py tarafindan bu tablolarla cizilir.


#: Sinif diyagrami agacinda kullanilan UML sembolleri ve arac adlari.
CLASS_GLYPH = {
    Stereotype.CLASS: "▭",
    Stereotype.ABSTRACT: "▱",
    Stereotype.INTERFACE: "◍",
}

CLASS_LABEL = {
    Stereotype.CLASS: "Class",
    Stereotype.ABSTRACT: "Abstract",
    Stereotype.INTERFACE: "Interface",
}

RELATION_GLYPH = {
    RelationKind.ASSOCIATION: "──",
    RelationKind.AGGREGATION: "◇─",
    RelationKind.COMPOSITION: "◆─",
    RelationKind.GENERALIZATION: "─▷",
    RelationKind.REALIZATION: "┈▷",
    RelationKind.DEPENDENCY: "┈>",
}

RELATION_LABEL = {
    RelationKind.ASSOCIATION: "Association",
    RelationKind.AGGREGATION: "Aggregation",
    RelationKind.COMPOSITION: "Composition",
    RelationKind.GENERALIZATION: "Generalization",
    RelationKind.REALIZATION: "Realization",
    RelationKind.DEPENDENCY: "Dependency",
}


class ProblemsPanel(QTreeWidget):
    """Dogrulama sonuclari; cift tiklaninca ilgili elemani secer."""

    element_activated = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setColumnCount(4)
        # SPEC sutunu: ihlal edilen kuralin OMG UML 2.5.1'deki bolumu ve
        # BASILI sayfa numarasi. Ipucunda kuralin tek cumlelik ozeti durur.
        self.setHeaderLabels(["Severity", "Code", "Description", "UML 2.5.1"])
        self.setRootIsDecorated(False)
        self.setFont(ui_font(9))
        self.setAlternatingRowColors(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.setColumnWidth(0, 78)
        self.setColumnWidth(1, 54)
        self.setColumnWidth(3, 250)
        self.itemDoubleClicked.connect(self._activate)
        self.itemSelectionChanged.connect(self._on_select)
        apply_contrast(self)

    def show_issues(self, issues: List[Issue]) -> None:
        self.clear()
        if not issues:
            item = QTreeWidgetItem(self, ["✔", "", "No problems - the model is ready for code generation.", ""])
            item.setForeground(0, QBrush(QColor(C.GREEN)))
            item.setForeground(2, QBrush(QColor(C.GREEN)))
            return
        for issue in issues:
            item = QTreeWidgetItem(self, [
                "%s %s" % (SEVERITY_GLYPH.get(issue.severity, "•"),
                           SEVERITY_TEXT.get(issue.severity, issue.severity)),
                issue.code,
                issue.message,
                "",
            ])
            self._attach_spec(item, issue.code)
            color = QColor(SEVERITY_COLOR.get(issue.severity, C.TEXT))
            item.setForeground(0, QBrush(color))
            item.setForeground(1, QBrush(QColor(C.TEXT_DIM)))
            item.setFont(1, mono_font(8))
            # ILETININ KENDISI DE RENKLENIR.
            #
            # Onceden yalnizca "Severity" sutunu renkliydi; asil okunan
            # metin (aciklama) duz renkteydi ve listede goze carpmiyordu.
            # Hata KIRMIZI, uyari KEHRIBAR: iki dert birbirinden ayrilir
            # ve ikisi de duz metinden ayrilir.
            if issue.severity in ("error", "warning"):
                item.setForeground(2, QBrush(color))
            if issue.severity == "error":
                f = self.font()
                f.setBold(True)
                item.setFont(2, f)
            item.setData(0, ID_ROLE, issue.element_id or "")

    def _attach_spec(self, item: QTreeWidgetItem, code: str) -> None:
        """Bulguya spesifikasyon atfini islar.

        Sutunda KISA atif (bolum + sayfa) durur; kuralin tam cumlesi
        ipucundadir. Arac kurallarinin (ad cakismasi, gecersiz tanimlayici)
        UML karsiligi YOKTUR -- uydurma bir sayfa vermek yerine "tool rule"
        yazilir, cunku kullanici o sayfayi belgede arayacaktir.
        """
        ref = uml_spec.lookup(code)
        if ref is None:
            return

        if ref.is_tool_rule:
            item.setText(3, "tool rule")
            item.setForeground(3, QBrush(QColor(C.TEXT_DIM)))
        else:
            sayfa = ref.page
            etiket = "§%s" % ref.section
            if sayfa is not None:
                etiket += "  ·  p. %d" % sayfa
            item.setText(3, etiket)
            item.setForeground(3, QBrush(QColor(C.ACCENT)))
        item.setFont(3, mono_font(8))

        ipucu = "%s\n\n%s" % (ref.rule, ref.citation())
        if not ref.is_tool_rule:
            ipucu += "\n%s  (%s)" % (uml_spec.SPEC_TITLE,
                                     uml_spec.SPEC_DOCUMENT)
        for col in range(4):
            item.setToolTip(col, ipucu)

    def _activate(self, item: QTreeWidgetItem, _col: int) -> None:
        eid = item.data(0, ID_ROLE)
        if eid:
            self.element_activated.emit(eid)

    def _on_select(self) -> None:
        items = self.selectedItems()
        if items:
            eid = items[0].data(0, ID_ROLE)
            if eid:
                self.element_activated.emit(eid)
