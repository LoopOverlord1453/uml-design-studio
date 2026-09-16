"""Cift tiklama ile acilan ozellik diyaloglari (durum makinesi)."""

from __future__ import annotations

from PyQt6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                             QLabel, QLineEdit, QPlainTextEdit, QSizePolicy,
                             QSpinBox, QVBoxLayout, QWidget)


def _fixed_row(widget: QWidget) -> QWidget:
    """Satiri DIKEYDE sabitler.

    QFormLayout fazla yuksekligi esneyebilen satirlara dagitir. Ust
    bilgi etiketi ve tek satirlik alanlar sabitlenmezse butun bos alani
    onlar yutar; pencerenin yarisi bos dururken kod alani 40 pikselde
    kalir.
    """
    widget.setSizePolicy(widget.sizePolicy().horizontalPolicy(),
                         QSizePolicy.Policy.Fixed)
    return widget

from ..core.model import State, StateKind, Transition, TransitionKind
from .highlighter import CppHighlighter
from .theme import C, mono_font

KIND_TITLES = {
    StateKind.SIMPLE: "State",
    StateKind.COMPOSITE: "Composite State",
    StateKind.INITIAL: "Initial Pseudostate",
    StateKind.FINAL: "Final State",
    StateKind.CHOICE: "Choice Pseudostate",
    StateKind.JUNCTION: "Junction Pseudostate",
    StateKind.SHALLOW_HISTORY: "Shallow History (H)",
    StateKind.DEEP_HISTORY: "Deep History (H*)",
    StateKind.TERMINATE: "Terminate Pseudostate",
    StateKind.FORK: "Fork Pseudostate",
    StateKind.JOIN: "Join Pseudostate",
    StateKind.ENTRY_POINT: "Entry Point",
    StateKind.EXIT_POINT: "Exit Point",
    StateKind.SUBMACHINE: "Submachine State",
}

TKIND_LABELS = [
    ("external", TransitionKind.EXTERNAL),
    ("internal", TransitionKind.INTERNAL),
    ("local", TransitionKind.LOCAL),
]


class _CodeField(QPlainTextEdit):
    """Cok satirli kod alani: EN AZ `rows` satir, pencere buyudukce buyur.

    Eskiden `setFixedHeight` ile cakiliydi. Pencere buyutuldugunde fazla
    alan bu alana degil, formun en ustundeki bilgi etiketine gidiyordu:
    kullanici birkac satirlik bir entry davranisini 40 piksellik bir
    kutuya sigdirmaya calisiyordu.
    """

    def __init__(self, text: str = "", rows: int = 3) -> None:
        super().__init__()
        self.setFont(mono_font(9))
        self.setPlainText(text)
        self.setTabChangesFocus(True)
        line = self.fontMetrics().lineSpacing()
        self.setMinimumHeight(int(line * rows + 14))
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self.highlighter = CppHighlighter(self.document())


class _NoteField(QPlainTextEdit):
    """Kisa, cok satirli serbest metin (kod vurgulamasi YOK)."""

    def __init__(self, text: str = "", rows: int = 2) -> None:
        super().__init__()
        self.setPlainText(text)
        self.setTabChangesFocus(True)
        line = self.fontMetrics().lineSpacing()
        self.setMinimumHeight(int(line * rows + 12))
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)

class StateDialog(QDialog):
    """Durum / sozde-durum ozellikleri."""

    def __init__(self, state: State, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self.setWindowTitle(KIND_TITLES.get(state.kind, "State"))
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(8)
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        layout.addLayout(form, 1)

        kind_lbl = QLabel(KIND_TITLES.get(state.kind, ""))
        kind_lbl.setStyleSheet("color: %s; font-weight: 600;" % C.ACCENT)
        form.addRow("UML", _fixed_row(kind_lbl))

        self.name_edit = QLineEdit(state.name)
        form.addRow("Name", _fixed_row(self.name_edit))

        self.entry_edit = self.exit_edit = self.do_edit = None
        if state.kind.is_real_state:
            self.entry_edit = _CodeField(state.entry)
            self.exit_edit = _CodeField(state.exit)
            self.do_edit = _CodeField(state.do)
            form.addRow("entry /", self.entry_edit)
            form.addRow("exit /", self.exit_edit)
            form.addRow("do /", self.do_edit)

        # Not da cok satirli: bir durumun aciklamasi tek satira sigmiyor
        # ve tuval zaten satir sonlarini ciziyor.
        self.note_edit = _NoteField(state.note)
        form.addRow("Note", self.note_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def apply_to(self, st: State) -> None:
        name = self.name_edit.text().strip()
        if name:
            st.name = name
        if self.entry_edit is not None:
            st.entry = self.entry_edit.toPlainText().strip()
            st.exit = self.exit_edit.toPlainText().strip()
            st.do = self.do_edit.toPlainText().strip()
        st.note = self.note_edit.toPlainText().strip()


class TransitionDialog(QDialog):
    """Gecis ozellikleri:  trigger [guard] / effect"""

    def __init__(self, tr: Transition, src_name: str, dst_name: str,
                 events, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Transition")
        self.setMinimumWidth(440)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(8)
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        layout.addLayout(form, 1)

        route = QLabel("%s  →  %s" % (src_name, dst_name))
        route.setStyleSheet("color: %s; font-weight: 600;" % C.ACCENT)
        form.addRow("UML", _fixed_row(route))

        self.event_edit = QComboBox()
        self.event_edit.setEditable(True)
        self.event_edit.addItem("")
        for ev in events:
            if ev:
                self.event_edit.addItem(ev)
        self.event_edit.setCurrentText(tr.event)
        self.event_edit.lineEdit().setPlaceholderText("(empty = completion)")
        form.addRow("Trigger", _fixed_row(self.event_edit))

        self.guard_edit = _CodeField(tr.guard, rows=2)
        form.addRow("[Guard]", self.guard_edit)

        self.action_edit = _CodeField(tr.action, rows=3)
        form.addRow("/ Effect", self.action_edit)

        self.kind_edit = QComboBox()
        for text, value in TKIND_LABELS:
            self.kind_edit.addItem(text, value)
        self.kind_edit.setCurrentIndex(
            [k for _, k in TKIND_LABELS].index(tr.kind))
        form.addRow("Kind", _fixed_row(self.kind_edit))

        self.prio_edit = QSpinBox()
        self.prio_edit.setRange(0, 99)
        self.prio_edit.setValue(tr.priority)
        form.addRow("Priority", _fixed_row(self.prio_edit))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.event_edit.setFocus()

    def apply_to(self, tr: Transition) -> None:
        tr.event = self.event_edit.currentText().strip()
        tr.guard = self.guard_edit.toPlainText().strip()
        tr.action = self.action_edit.toPlainText().strip()
        tr.kind = self.kind_edit.currentData()
        tr.priority = self.prio_edit.value()
