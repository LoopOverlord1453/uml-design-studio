"""The simulation panel (a horizontal strip above the canvas).

Runs the diagram without generating code. The reference interpreter
(app/core/simulator.py) uses the same intermediate representation and the same
run-to-completion algorithm as the generated C/C++, so the behaviour seen here
is identical to that of the generated code (tools/test_semantics.py verifies it).

The panel shows three things at once:

  * VARIABLES -- the names the guards read (`ctx->counter`, `limit`...) appear
    in a table and can be given values. The guard expression is COMPUTED from
    those values; no switch has to be flipped by hand.
  * GUARDS -- "auto / true / false" for every expression, plus the value
    currently resolved. Expressions that cannot be evaluated (for instance
    `app_over_limit(ctx)`) say so plainly and fall back to the manual value.
  * TRACE -- the transition chosen at each step, the guards evaluated, the
    exit/effect/entry behaviours that ran, and the ACTIVE CONFIGURATION at
    the end of the step.

The terms come from the UML 2.5.1 glossary: event dispatch, guard, entry/exit
behavior, effect, completion transition, run-to-completion (RTC), active
state configuration.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QComboBox,
                             QFrame, QHBoxLayout, QHeaderView, QLabel,
                             QLineEdit, QPlainTextEdit, QPushButton,
                             QSizePolicy, QSplitter, QTableWidget,
                             QTableWidgetItem, QTabWidget, QVBoxLayout,
                             QWidget)

from ..codegen.ir import CodegenError, build_ir
from ..core import guard_expr
from ..core.simulator import Simulator
from ..core.validator import has_errors, validate
from .document import Document
from .theme import C, mono_font, ui_font

#: The options of the combo box on a guard row.
_MODES = ("auto", "true", "false")


def _drop_children(layout) -> None:
    """DETACHES every widget in a layout FIRST, then leaves them to deletion.

    deleteLater() alone is not enough: it defers the deletion to the next event
    loop, while until then the widget is still a child of the parent and, having
    been removed from the layout, keeps painting OVER the panel with its last --
    or, if it was never laid out, the default 640x480 -- geometry. Because
    rebuild() is called on every model change (loading a model produces several
    signals in one go), these buttons pile up and ghost buttons cover the whole
    panel. The deletion still has to be deferred: this code can be called from
    inside the deleted widget's own signal.
    """
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.hide()
            w.setParent(None)
            w.deleteLater()


def _clear_table(table: QTableWidget) -> None:
    """Empties the table and RELEASES THE CELL WIDGETS too.

    setRowCount(0) alone is not enough: the rows go, but the cell widgets
    (QLineEdit / QComboBox) stay hanging in the viewport of the table and the
    event loop does not collect them. Because rebuild() is called on every model
    change these accumulate, and their signal connections survive as well.
    """
    for satir in range(table.rowCount()):
        for sutun in range(table.columnCount()):
            w = table.cellWidget(satir, sutun)
            if w is None:
                continue
            table.removeCellWidget(satir, sutun)
            try:
                w.setParent(None)
                w.deleteLater()
            except RuntimeError:
                # Qt has already deleted it; nothing to do.
                pass
    table.clearContents()
    table.setRowCount(0)


class SimulatorPanel(QWidget):
    """Event dispatch buttons, variable/guard tables, the active state
    configuration and the run-to-completion trace."""

    active_changed = pyqtSignal(list)      # the active state chain (model ids)
    message = pyqtSignal(str)
    #: The simulation has started -- the canvas fits the diagram to the window.
    started = pyqtSignal()

    def _resolver(self):
        """The submachine resolver; the main window supplies it.

        The simulation panel has no direct access to the workspace; the main
        window hands it over through `submachine_resolver()`. When none is
        found it returns None and models without submachines work as before.
        """
        pencere = self.window()
        fn = getattr(pencere, "submachine_resolver", None)
        if callable(fn):
            try:
                return fn()
            except Exception:                   # noqa: BLE001
                return None
        return None

    def __init__(self, doc: Document, parent=None) -> None:
        super().__init__(parent)
        self.doc = doc
        self.sim: Optional[Simulator] = None
        self._event_buttons: List[QPushButton] = []

        #: name -> value (used when evaluating guards)
        self._variables: Dict[str, object] = {}
        #: name -> the raw text the user typed (preserved across rebuilds)
        self._var_text: Dict[str, str] = {}
        #: guard expression -> "auto" | "true" | "false"
        self._guard_mode: Dict[str, str] = {}
        #: where the last evaluation came from ("variables" / "manual")
        self._guard_source: Dict[str, str] = {}
        #: the guard expressions currently listed
        self._guards: List[str] = []
        #: the step counter in the trace
        self._step = 0

        #: For retheme(): the items given an inline style at set-up.
        #: Walking the widget tree is not enough, because some widgets of the
        #: same type (the separator lines, say) carry a theme colour while
        #: others do not.
        self._separators: List[QFrame] = []
        self._dim_labels: List[QLabel] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 6, 10, 6)
        outer.setSpacing(6)

        # title
        head = QHBoxLayout()
        head.setSpacing(10)
        title = QLabel("SIMULATION")
        f = ui_font(8)
        f.setBold(True)
        f.setLetterSpacing(f.SpacingType.AbsoluteSpacing, 1.0)
        title.setFont(f)
        self._title = title
        head.addWidget(title)

        sub = QLabel("run-to-completion engine · identical to the generated code")
        sub.setFont(ui_font(8))
        self._sub = sub
        head.addWidget(sub)
        head.addStretch(1)

        head.addWidget(self._dim_label("Step:"))
        self.lbl_step = QLabel("0")
        self.lbl_step.setFont(mono_font(10))
        self._step_label = self.lbl_step
        head.addWidget(self.lbl_step)

        sep0 = self._separator()
        head.addWidget(sep0)

        head.addWidget(self._dim_label("Active state configuration:"))
        self.lbl_state = QLabel("—")
        self.lbl_state.setFont(mono_font(10))
        head.addWidget(self.lbl_state)
        outer.addLayout(head)

        # controls
        row = QHBoxLayout()
        row.setSpacing(6)
        self.btn_start = QPushButton("▶  Start")
        self.btn_start.setToolTip("Initial transition + entry behavior + RTC")
        self.btn_start.clicked.connect(self.start)
        self.btn_reset = QPushButton("⟲  Reset")
        self.btn_reset.setToolTip("Forget the run; the model is untouched")
        self.btn_reset.clicked.connect(self.reset)
        self.btn_do = QPushButton("do()")
        self.btn_do.setToolTip("doActivity behaviors of the active configuration")
        self.btn_do.clicked.connect(self.step_do)
        row.addWidget(self.btn_start)
        row.addWidget(self.btn_reset)
        row.addWidget(self.btn_do)

        row.addWidget(self._separator())

        row.addWidget(self._dim_label("Event dispatch:"))
        self.events_host = QWidget()
        self.events_layout = QHBoxLayout(self.events_host)
        self.events_layout.setContentsMargins(0, 0, 0, 0)
        self.events_layout.setSpacing(5)
        row.addWidget(self.events_host, 1)
        outer.addLayout(row)

        # variables / guards + trace
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._build_variables_tab(), "Variables")
        self.tabs.setTabToolTip(
            0, "Names read by the guards. Set a value and every condition "
               "that uses it is evaluated from there.")
        self.tabs.addTab(self._build_guards_tab(), "Guards")
        self.tabs.setTabToolTip(
            1, "auto = computed from the Variables tab; true/false = "
               "forced by hand.")
        split.addWidget(self.tabs)

        split.addWidget(self._build_trace_column())
        split.setSizes([380, 620])
        outer.addWidget(split, 1)

        self.setMinimumHeight(200)
        self.setMaximumHeight(380)
        self._apply_static_styles()
        self.rebuild()

    # -------------------------------------------------------------- set-up #

    def _build_variables_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(3)

        self.var_table = QTableWidget(0, 2)
        self.var_table.setHorizontalHeaderLabels(["Name", "Value"])
        self.var_table.verticalHeader().setVisible(False)
        self.var_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.var_table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection)
        self.var_table.setFont(mono_font(8))
        # A tight row: the panel is a horizontal strip so every pixel counts;
        # at the default height the third row disappeared.
        self.var_table.verticalHeader().setDefaultSectionSize(22)
        head = self.var_table.horizontalHeader()
        head.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        head.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.var_table.setColumnWidth(1, 110)
        v.addWidget(self.var_table, 1)

        self.lbl_var_empty = self._dim_label(
            "(no guard reads a plain variable)")
        v.addWidget(self.lbl_var_empty)
        return page

    def _build_guards_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(3)

        self.guard_table = QTableWidget(0, 3)
        self.guard_table.setHorizontalHeaderLabels(
            ["Guard condition", "Mode", "Value"])
        self.guard_table.verticalHeader().setVisible(False)
        self.guard_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.guard_table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection)
        self.guard_table.setFont(mono_font(8))
        self.guard_table.verticalHeader().setDefaultSectionSize(22)
        head = self.guard_table.horizontalHeader()
        head.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        head.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        head.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.guard_table.setColumnWidth(1, 74)
        self.guard_table.setColumnWidth(2, 108)
        v.addWidget(self.guard_table, 1)

        self.lbl_guard_note = self._dim_label("(no guard)")
        self.lbl_guard_note.setWordWrap(True)
        v.addWidget(self.lbl_guard_note)
        return page

    def _build_trace_column(self) -> QWidget:
        col = QWidget()
        tv = QVBoxLayout(col)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.setSpacing(2)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.addWidget(self._dim_label(
            "Trace — transition ▸ guard ▸ exit ▸ effect ▸ entry ▸ "
            "configuration:"))
        bar.addStretch(1)
        self.btn_copy = QPushButton("Copy")
        self.btn_copy.setToolTip("Copy the whole trace to the clipboard")
        self.btn_copy.clicked.connect(self._copy_trace)
        self.btn_clear = QPushButton("Clear")
        self.btn_clear.setToolTip("Empty the trace; the run continues")
        self.btn_clear.clicked.connect(self._clear_trace)
        for b in (self.btn_copy, self.btn_clear):
            b.setFont(ui_font(8))
            b.setSizePolicy(QSizePolicy.Policy.Fixed,
                            QSizePolicy.Policy.Fixed)
            bar.addWidget(b)
        tv.addLayout(bar)

        self.trace = QPlainTextEdit()
        self.trace.setReadOnly(True)
        self.trace.setFont(mono_font(9))
        self.trace.setFrameShape(QFrame.Shape.NoFrame)
        self.trace.setPlaceholderText(
            "Press Start: the initial transition runs and every "
            "run-to-completion step is written here, line by line.")
        tv.addWidget(self.trace, 1)
        return col

    def _dim_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setFont(ui_font(8))
        lbl.setStyleSheet("color: %s;" % C.TEXT_DIM)
        self._dim_labels.append(lbl)
        return lbl

    def _separator(self) -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("color: %s;" % C.BORDER_LIGHT)
        self._separators.append(sep)
        return sep

    # ----------------------------------------------------------------- theme #

    def retheme(self) -> None:
        """Reapplies the inline colours on a theme change.

        The BACKGROUND of the panel and the label colours are embedded as
        strings at set-up time; they are not updated by themselves when ``C``
        changes. Walking the child widgets is not enough: the panel's OWN
        stylesheet (``SimulatorPanel { background: ... }``) is on no child at
        all, and skipping it leaves a dark band in the light theme.
        """
        self._apply_static_styles()
        # The event buttons and the table rows are drawn with the set-up colours;
        # rebuild() recreates them.
        self.rebuild()

    def _apply_static_styles(self) -> None:
        """(Re)applies the colours read at set-up."""
        self.setStyleSheet("SimulatorPanel { background: %s; }" % C.PANEL_DARK)
        self._title.setStyleSheet("color: %s;" % C.CYAN)
        self._sub.setStyleSheet("color: %s;" % C.TEXT_DIM)
        self.btn_start.setStyleSheet(
            "QPushButton { color: %s; font-weight: 600; }" % C.GREEN)
        self.lbl_step.setStyleSheet("color: %s;" % C.TEXT_BRIGHT)
        # The active state is shown in the SAME style as the selected row in the
        # model tree: bold and in a colour that stands out (see app/ui/contrast.py).
        self.lbl_state.setStyleSheet(
            "color: %s; font-weight: 700;" % C.SIM_ACTIVE)
        for sep in self._separators:
            sep.setStyleSheet("color: %s;" % C.BORDER_LIGHT)
        for lbl in self._dim_labels:
            lbl.setStyleSheet("color: %s;" % C.TEXT_DIM)

    # ------------------------------------------------------------------- API #

    def rebuild(self) -> None:
        """Refreshes the event buttons and the tables when the model changes."""
        self.reset(quiet=True)

        _drop_children(self.events_layout)
        self._event_buttons.clear()

        sm = self.doc.machine
        blocked = has_errors(validate(sm, resolve=self._resolver()))

        events = sm.events()
        if not events:
            label = QLabel("(the model declares no event; completion only)")
            label.setFont(ui_font(8))
            label.setStyleSheet("color: %s;" % C.TEXT_DIM)
            self.events_layout.addWidget(label)
        else:
            for ev in events:
                btn = QPushButton(ev)
                btn.setFont(mono_font(9))
                btn.setToolTip("dispatch(%s)" % ev)
                btn.clicked.connect(lambda _c, e=ev: self.dispatch(e))
                self.events_layout.addWidget(btn)
                self._event_buttons.append(btn)
        self.events_layout.addStretch(1)

        # THE GUARD LIST COVERS THE INSIDE OF A SUBMACHINE TOO.
        #
        # `build_ir` was being called without a resolver, while the simulation
        # itself was called as `Simulator(..., resolve=...)`. The two were
        # looking at DIFFERENT models: the guards inside a submachine never
        # appeared in the list, and their values were taken as True by the
        # default below.
        guards: List[str] = []
        guard_error = ""
        if not blocked:
            try:
                guards = build_ir(sm, resolve=self._resolver()).guards
            except CodegenError as exc:
                # DO NOT SWALLOW THE ERROR. The list used to be emptied silently: the
                # panel said "(no guard)", the user believed their model had no guard,
                # and because `_eval_guard` returned True for every expression the
                # simulation took EVERY guarded transition. What was wrong looked right
                # on screen.
                guard_error = str(exc)

        self._guards = list(guards)
        self._rebuild_variables()
        self._rebuild_guards(guard_error)

        self.setEnabled(not blocked)
        if blocked:
            self.lbl_state.setText("model is not valid")
            self.lbl_state.setStyleSheet("color: %s; font-weight: 700;" % C.RED)
        else:
            self.lbl_state.setStyleSheet("color: %s; font-weight: 700;"
                                         % C.SIM_ACTIVE)

    # variable table

    def _guard_variables(self) -> List[str]:
        """The names every guard reads, in first-seen order."""
        out: List[str] = []
        for expr in self._guards:
            for ad in guard_expr.identifiers(expr):
                if ad not in out:
                    out.append(ad)
        return out

    def _rebuild_variables(self) -> None:
        adlar = self._guard_variables()
        _clear_table(self.var_table)
        self.var_table.setRowCount(len(adlar))
        for satir, ad in enumerate(adlar):
            oge = QTableWidgetItem(ad)
            oge.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.var_table.setItem(satir, 0, oge)

            kutu = QLineEdit(self._var_text.get(ad, ""))
            kutu.setFont(mono_font(8))
            kutu.setPlaceholderText("0")
            kutu.setToolTip("A number (12, 0x0C, 1.5) or true / false")
            kutu.textChanged.connect(
                lambda text, a=ad: self._set_variable(a, text))
            self.var_table.setCellWidget(satir, 1, kutu)
            # On a rebuild, turn the previous text back into a value.
            self._set_variable(ad, kutu.text(), refresh=False)
        self.var_table.setVisible(bool(adlar))
        self.lbl_var_empty.setVisible(not adlar)
        if not adlar:
            self.lbl_var_empty.setText(
                "(no guard reads a plain variable — every condition is a "
                "call, so set it by hand on the Guards tab)"
                if self._guards else "(the model has no guard)")

    def _set_variable(self, name: str, text: str, refresh: bool = True) -> None:
        self._var_text[name] = text
        ok, deger = guard_expr.parse_value(text)
        if ok:
            self._variables[name] = deger
        else:
            self._variables.pop(name, None)
        self._paint_variable_row(name, ok, bool(text.strip()))
        if refresh:
            self._refresh_guard_values()

    def _paint_variable_row(self, name: str, ok: bool, filled: bool) -> None:
        for satir in range(self.var_table.rowCount()):
            oge = self.var_table.item(satir, 0)
            if oge is None or oge.text() != name:
                continue
            kutu = self.var_table.cellWidget(satir, 1)
            if kutu is None:
                return
            if filled and not ok:
                kutu.setStyleSheet("color: %s;" % C.RED)
                kutu.setToolTip("Not a number and not true/false — the "
                                "guards that read it stay unresolved.")
            else:
                kutu.setStyleSheet("")
                kutu.setToolTip("A number (12, 0x0C, 1.5) or true / false")
            return

    # guard table

    def _rebuild_guards(self, guard_error: str = "") -> None:
        _clear_table(self.guard_table)
        self.guard_table.setRowCount(len(self._guards))
        for satir, expr in enumerate(self._guards):
            oge = QTableWidgetItem(expr)
            oge.setFlags(Qt.ItemFlag.ItemIsEnabled)
            oge.setToolTip(expr)
            self.guard_table.setItem(satir, 0, oge)

            secim = QComboBox()
            secim.addItems(list(_MODES))
            secim.setFont(ui_font(8))
            secim.setCurrentText(self._guard_mode.get(expr, "auto"))
            secim.currentTextChanged.connect(
                lambda mode, e=expr: self._set_guard_mode(e, mode))
            self.guard_table.setCellWidget(satir, 1, secim)

            deger = QTableWidgetItem("")
            deger.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.guard_table.setItem(satir, 2, deger)

        self.guard_table.setVisible(bool(self._guards))
        if guard_error:
            self.lbl_guard_note.setText("guards unavailable: %s" % guard_error)
            self.lbl_guard_note.setStyleSheet("color: %s;" % C.RED)
            self.lbl_guard_note.setVisible(True)
        elif not self._guards:
            self.lbl_guard_note.setText("(the model has no guard)")
            self.lbl_guard_note.setStyleSheet("color: %s;" % C.TEXT_DIM)
            self.lbl_guard_note.setVisible(True)
        else:
            self.lbl_guard_note.setVisible(False)
        self._refresh_guard_values()

    def _set_guard_mode(self, expr: str, mode: str) -> None:
        self._guard_mode[expr] = mode
        self._refresh_guard_values()

    def _resolve_guard(self, expr: str):
        """Returns (value, source) for a guard.

        A source of "variables" means the value was COMPUTED from the
        variables; "manual" means it is the value the user gave (or a default).
        """
        if expr.strip().lower() == "else":
            return True, "else"
        mode = self._guard_mode.get(expr, "auto")
        if mode == "true":
            return True, "manual"
        if mode == "false":
            return False, "manual"
        sonuc = guard_expr.evaluate(expr, self._variables)
        if sonuc is None:
            # It cannot be evaluated: without a manual value TRUE is assumed (the
            # old behaviour), but the source is written out plainly in the table.
            return True, "unresolved"
        return sonuc, "variables"

    def _refresh_guard_values(self) -> None:
        for satir, expr in enumerate(self._guards):
            oge = self.guard_table.item(satir, 2)
            if oge is None:
                continue
            deger, kaynak = self._resolve_guard(expr)
            etiket = "true" if deger else "false"
            if kaynak == "unresolved":
                etiket += "  ?"
                oge.setToolTip(
                    "This condition cannot be computed from variables "
                    "(it is a call or uses a pointer). Force it with the "
                    "Mode column.")
                renk = C.WARN
            elif kaynak == "manual":
                oge.setToolTip("Forced by hand in the Mode column.")
                renk = C.TEXT_BRIGHT
            else:
                oge.setToolTip("Computed from the Variables tab.")
                renk = C.GREEN if deger else C.ORANGE
            oge.setText(etiket)
            oge.setForeground(_brush(renk))

    # actions

    def start(self) -> None:
        sm = self.doc.machine
        if has_errors(validate(sm, resolve=self._resolver())):
            self.message.emit("Fix the validation errors first.")
            return
        try:
            self.sim = Simulator(sm, resolve=self._resolver(),
                                 guard_eval=self._eval_guard,
                                 on_event=self._log)
        except CodegenError as exc:
            self.message.emit("Could not start the simulation: %s" % exc)
            return
        self.trace.clear()
        self._step = 0
        self._write_legend()
        self._section("START — initial transition")
        self.sim.start()
        self._sync()
        self.started.emit()

    def reset(self, quiet: bool = False) -> None:
        self.sim = None
        self._step = 0
        self.lbl_step.setText("0")
        self.lbl_state.setText("—")
        self.active_changed.emit([])
        if not quiet:
            self.trace.clear()
            self.message.emit("Simulation reset.")

    def dispatch(self, event: str) -> None:
        if self.sim is None:
            self.start()
            if self.sim is None:
                return
        self._section("dispatch(%s)" % event)
        onceki = len(getattr(self.sim, "deferred_pool", []))
        handled = self.sim.dispatch(event)
        sonraki = len(getattr(self.sim, "deferred_pool", []))
        if handled and sonraki > onceki:
            # The event was CONSUMED but triggered no transition: it is waiting in
            # the deferral pool (UML 2.5.1, 14.2.3.4.4).
            self._append("    deferred — kept until no active state defers it",
                         C.WARN)
        elif not handled:
            self._append("    no enabled transition and not deferred — "
                         "the event is discarded", C.TEXT_DIM)
        self._sync()

    def step_do(self) -> None:
        if self.sim is None:
            return
        self._section("doActivity() — behaviors of the active configuration")
        self.sim.do_activity()
        self._sync()

    def _copy_trace(self) -> None:
        QApplication.clipboard().setText(self.trace.toPlainText())
        self.message.emit("Trace copied to the clipboard.")

    def _clear_trace(self) -> None:
        self.trace.clear()

    # helpers

    def _eval_guard(self, expr: str) -> bool:
        deger, kaynak = self._resolve_guard(expr)
        self._guard_source[expr] = kaynak
        return deger

    def _write_legend(self) -> None:
        """Writes a reading guide at the top of the trace."""
        self._append(
            "legend:  ▸ transition   ? guard   ← exit   » effect   "
            "→ entry   ● do   = configuration", C.TEXT_DIM)

    def _section(self, title: str) -> None:
        cizgi = "─" * max(4, 58 - len(title))
        self._append("", C.TEXT_DIM)
        self._append("── %s %s" % (title, cizgi), C.CYAN)

    def _log(self, kind: str, detail: str) -> None:
        """Writes every record coming from the simulation into the trace.

        The "T" and "G" records are NOT in the `Simulator.trace` list; they are
        NARRATION sent only to the panel (see Simulator._note). That keeps the
        reference trace identical to the trace of the generated C/C++.
        """
        if kind == "T":
            self._step += 1
            self.lbl_step.setText(str(self._step))
            self._append("  ▸ %s" % detail, C.ACCENT)
            return
        if kind == "G":
            expr, _, sonuc = detail.partition("\t")
            kaynak = self._guard_source.get(expr, "manual")
            aciklama = {"variables": "computed from the variables",
                        "manual": "forced by hand",
                        "else": "the default branch",
                        "unresolved": "not computable — assumed true"}.get(
                            kaynak, kaynak)
            renk = C.GREEN if sonuc == "true" else C.ORANGE
            if kaynak == "unresolved":
                renk = C.WARN
            self._append("    ? %s  ⇒  %s   (%s)"
                         % (" ".join(expr.split()), sonuc, aciklama), renk)
            return
        if kind == "error":
            self._append("    !! %s" % detail, C.RED)
            return
        metin = {"E": "    → entry   %s", "X": "    ← exit    %s",
                 "D": "    ● do      %s", "A": "    » effect  %s",
                 "code": "              %s"}.get(kind, "    %s")
        renk = {"E": C.GREEN, "X": C.ORANGE, "A": C.BLUE,
                "D": C.PURPLE}.get(kind, C.TEXT_DIM)
        self._append(metin % " ".join(detail.split()), renk)

    def _append(self, text: str, color: str) -> None:
        self.trace.appendHtml(
            '<span style="color:%s; white-space:pre">%s</span>'
            % (color, _escape(text)))
        self.trace.verticalScrollBar().setValue(
            self.trace.verticalScrollBar().maximum())

    def _sync(self) -> None:
        if self.sim is None:
            return
        chain = self.sim.active_chain()
        sm = self.doc.machine
        names = [sm.states[i].name for i in reversed(chain) if i in sm.states]
        metin = " ▸ ".join(names) if names else self.sim.state_name
        self.lbl_state.setText(metin)
        self._append("    = %s" % metin, C.SIM_ACTIVE)
        if self.sim.is_terminated():
            self._append("    ■ machine terminated (final / terminate) — "
                         "further events are ignored", C.TEXT_DIM)
            for btn in self._event_buttons:
                btn.setEnabled(False)
        else:
            for btn in self._event_buttons:
                btn.setEnabled(True)
        self.active_changed.emit(chain)


def _brush(color: str):
    from PyQt6.QtGui import QBrush, QColor
    return QBrush(QColor(color))


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace(" ", "&nbsp;"))
