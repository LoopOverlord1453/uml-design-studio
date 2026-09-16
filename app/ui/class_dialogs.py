"""Sinif diyagrami ozellik diyaloglari (cift tiklama ile acilir)."""

from __future__ import annotations

from typing import List

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                             QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                             QPushButton, QTabWidget, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from ..core.class_model import (Attribute, Operation, Parameter, Relation,
                                RelationKind, Stereotype, UmlClass)
from .theme import C, ui_font

STEREOTYPES = [
    ("Class", Stereotype.CLASS),
    ("Abstract Class", Stereotype.ABSTRACT),
    ("«interface»", Stereotype.INTERFACE),
]

RELATION_KINDS = [
    ("Association", RelationKind.ASSOCIATION),
    ("Aggregation", RelationKind.AGGREGATION),
    ("Composition", RelationKind.COMPOSITION),
    ("Generalization", RelationKind.GENERALIZATION),
    ("Realization", RelationKind.REALIZATION),
    ("Dependency", RelationKind.DEPENDENCY),
]

VIS_VALUES = ["+", "-", "#", "~"]


def _vis_combo(current: str) -> QComboBox:
    box = QComboBox()
    box.addItems(VIS_VALUES)
    if current in VIS_VALUES:
        box.setCurrentText(current)
    return box


def _check_item(checked: bool) -> QTableWidgetItem:
    item = QTableWidgetItem()
    item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
    item.setCheckState(Qt.CheckState.Checked if checked
                       else Qt.CheckState.Unchecked)
    return item


def params_to_text(params: List[Parameter]) -> str:
    return ", ".join("%s : %s" % (p.name, p.type) for p in params)


def text_to_params(text: str) -> List[Parameter]:
    out: List[Parameter] = []
    for i, chunk in enumerate(t for t in text.split(",") if t.strip()):
        if ":" in chunk:
            name, _, type_ = chunk.partition(":")
            out.append(Parameter(name=name.strip() or "arg%d" % i,
                                 type=type_.strip() or "int32_t"))
        else:
            out.append(Parameter(name="arg%d" % i, type=chunk.strip()))
    return out


class ClassDialog(QDialog):
    """Sinif ozellikleri: ad, kalip, nitelikler, islemler."""

    def __init__(self, cls: UmlClass, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Class")
        self.setMinimumSize(640, 480)

        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.name_edit = QLineEdit(cls.name)
        form.addRow("Name", self.name_edit)

        self.stereo_edit = QComboBox()
        for text, value in STEREOTYPES:
            self.stereo_edit.addItem(text, value)
        self.stereo_edit.setCurrentIndex(
            [v for _, v in STEREOTYPES].index(cls.stereotype))
        form.addRow("Stereotype", self.stereo_edit)
        layout.addLayout(form)

        tabs = QTabWidget()
        layout.addWidget(tabs, 1)

        # ------------------------------------------------------- nitelikler
        attr_page = QWidget()
        av = QVBoxLayout(attr_page)
        self.attr_table = QTableWidget(0, 5)
        self.attr_table.setHorizontalHeaderLabels(
            ["Visibility", "Name", "Type", "Default", "Multiplicity"])
        self.attr_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.attr_table.verticalHeader().setVisible(False)
        av.addWidget(self.attr_table, 1)
        av.addLayout(self._row_buttons(self.attr_table, self._add_attr_row))
        tabs.addTab(attr_page, "Attributes")

        # --------------------------------------------------------- islemler
        op_page = QWidget()
        ov = QVBoxLayout(op_page)
        self.op_table = QTableWidget(0, 6)
        self.op_table.setHorizontalHeaderLabels(
            ["Visibility", "Name", "Parameters (name : type)", "Returns",
             "Abstract", "Const"])
        header = self.op_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.op_table.verticalHeader().setVisible(False)
        ov.addWidget(self.op_table, 1)
        ov.addLayout(self._row_buttons(self.op_table, self._add_op_row))
        tabs.addTab(op_page, "Operations")

        for a in cls.attributes:
            self._add_attr_row(a)
        for o in cls.operations:
            self._add_op_row(o)

        # Not cok satirli: sinif aciklamalari tek satira sigmiyor ve
        # tuval zaten satir sonlarini ciziyor. (Durum penceresiyle ayni.)
        from .dialogs import _NoteField
        self.note_edit = _NoteField(cls.note, rows=2)
        bottom = QFormLayout()
        bottom.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        bottom.addRow("Note", self.note_edit)
        layout.addLayout(bottom)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def _row_buttons(self, table: QTableWidget, adder) -> QHBoxLayout:
        row = QHBoxLayout()
        add = QPushButton("+ Add")
        add.clicked.connect(lambda: adder(None))
        rem = QPushButton("− Remove")

        def remove():
            r = table.currentRow()
            if r >= 0:
                table.removeRow(r)
        rem.clicked.connect(remove)
        row.addWidget(add)
        row.addWidget(rem)
        row.addStretch(1)
        return row

    def _add_attr_row(self, a: Attribute = None) -> None:
        a = a or Attribute(name="attr%d" % (self.attr_table.rowCount() + 1))
        r = self.attr_table.rowCount()
        self.attr_table.insertRow(r)
        self.attr_table.setCellWidget(r, 0, _vis_combo(a.visibility))
        self.attr_table.setItem(r, 1, QTableWidgetItem(a.name))
        self.attr_table.setItem(r, 2, QTableWidgetItem(a.type))
        self.attr_table.setItem(r, 3, QTableWidgetItem(a.default))
        self.attr_table.setItem(r, 4, QTableWidgetItem(a.multiplicity))

    def _add_op_row(self, o: Operation = None) -> None:
        o = o or Operation(name="operation%d" % (self.op_table.rowCount() + 1))
        r = self.op_table.rowCount()
        self.op_table.insertRow(r)
        self.op_table.setCellWidget(r, 0, _vis_combo(o.visibility))
        self.op_table.setItem(r, 1, QTableWidgetItem(o.name))
        self.op_table.setItem(r, 2, QTableWidgetItem(params_to_text(o.params)))
        self.op_table.setItem(r, 3, QTableWidgetItem(o.return_type))
        self.op_table.setItem(r, 4, _check_item(o.abstract))
        self.op_table.setItem(r, 5, _check_item(o.const))
        # govde bilgisini koru (tabloda gosterilmez)
        self.op_table.item(r, 1).setData(Qt.ItemDataRole.UserRole,
                                         (o.body, o.static))

    @staticmethod
    def _cell(table: QTableWidget, r: int, col: int) -> str:
        item = table.item(r, col)
        return item.text().strip() if item is not None else ""

    def apply_to(self, cls: UmlClass) -> None:
        name = self.name_edit.text().strip()
        if name:
            cls.name = name
        cls.stereotype = self.stereo_edit.currentData()
        cls.note = self.note_edit.toPlainText().strip()

        attrs: List[Attribute] = []
        for r in range(self.attr_table.rowCount()):
            aname = self._cell(self.attr_table, r, 1)
            if not aname:
                continue
            vis = self.attr_table.cellWidget(r, 0)
            attrs.append(Attribute(
                name=aname,
                type=self._cell(self.attr_table, r, 2) or "int32_t",
                visibility=vis.currentText() if vis else "-",
                default=self._cell(self.attr_table, r, 3),
                multiplicity=self._cell(self.attr_table, r, 4)))
        cls.attributes = attrs

        ops: List[Operation] = []
        for r in range(self.op_table.rowCount()):
            oname = self._cell(self.op_table, r, 1)
            if not oname:
                continue
            vis = self.op_table.cellWidget(r, 0)
            extra = self.op_table.item(r, 1).data(Qt.ItemDataRole.UserRole) \
                or ("", False)
            abstract = self.op_table.item(r, 4).checkState() \
                is Qt.CheckState.Checked
            const = self.op_table.item(r, 5).checkState() \
                is Qt.CheckState.Checked
            ops.append(Operation(
                name=oname,
                return_type=self._cell(self.op_table, r, 3) or "void",
                visibility=vis.currentText() if vis else "+",
                params=text_to_params(self._cell(self.op_table, r, 2)),
                abstract=abstract,
                const=const,
                body=extra[0],
                static=extra[1]))
        cls.operations = ops


class RelationDialog(QDialog):
    """Iliski ozellikleri: tur, cokluklar, rol/etiket."""

    def __init__(self, rel: Relation, src_name: str, dst_name: str,
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Relationship")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(8)
        layout.addLayout(form)

        route = QLabel("%s  →  %s" % (src_name, dst_name))
        route.setStyleSheet("color: %s; font-weight: 600;" % C.ACCENT)
        form.addRow("UML", route)

        self.kind_edit = QComboBox()
        for text, value in RELATION_KINDS:
            self.kind_edit.addItem(text, value)
        self.kind_edit.setCurrentIndex(
            [v for _, v in RELATION_KINDS].index(rel.kind))
        form.addRow("Kind", self.kind_edit)

        self.label_edit = QLineEdit(rel.label)
        form.addRow("Label", self.label_edit)

        self.src_mult = QLineEdit(rel.source_mult)
        self.src_mult.setPlaceholderText("1")
        form.addRow("Source multiplicity", self.src_mult)

        self.dst_mult = QLineEdit(rel.target_mult)
        self.dst_mult.setPlaceholderText("0..*")
        form.addRow("Target multiplicity", self.dst_mult)

        self.role_edit = QLineEdit(rel.target_role)
        self.role_edit.setPlaceholderText("generated member name")
        form.addRow("Target role", self.role_edit)

        hint = QLabel("Aggregation / Composition: the source is the WHOLE, the target the PART.")
        hint.setFont(ui_font(8))
        hint.setStyleSheet("color: %s;" % C.TEXT_DIM)
        form.addRow("", hint)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def apply_to(self, rel: Relation) -> None:
        rel.kind = self.kind_edit.currentData()
        rel.label = self.label_edit.text().strip()
        rel.source_mult = self.src_mult.text().strip()
        rel.target_mult = self.dst_mult.text().strip()
        rel.target_role = self.role_edit.text().strip()
