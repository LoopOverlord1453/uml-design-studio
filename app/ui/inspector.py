"""The panel that edits the properties of the selected element."""

from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import (QComboBox, QDoubleSpinBox, QFormLayout, QFrame,
                             QGroupBox, QLabel, QLineEdit, QPlainTextEdit,
                             QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from ..core.model import StateKind, TransitionKind
from ..core.text_layout import LINE_BREAK_MARKER
from .document import Document
from .highlighter import CppHighlighter
from .theme import C, mono_font, ui_font

KIND_LABELS = [
    ("State", StateKind.SIMPLE),
    ("Composite State", StateKind.COMPOSITE),
    ("Initial", StateKind.INITIAL),
    ("Final State", StateKind.FINAL),
    ("Choice", StateKind.CHOICE),
    ("Junction", StateKind.JUNCTION),
    ("Shallow History (H)", StateKind.SHALLOW_HISTORY),
    ("Deep History (H*)", StateKind.DEEP_HISTORY),
    ("Terminate", StateKind.TERMINATE),
    ("Fork", StateKind.FORK),
    ("Join", StateKind.JOIN),
    ("Entry Point", StateKind.ENTRY_POINT),
    ("Exit Point", StateKind.EXIT_POINT),
    ("Submachine", StateKind.SUBMACHINE),
]

TKIND_LABELS = [
    ("external", TransitionKind.EXTERNAL),
    ("internal", TransitionKind.INTERNAL),
    ("local", TransitionKind.LOCAL),
]

#: The DRAWING SIZE of the pseudostate kinds. It must match the palette
#: defaults on the canvas (canvas._NEW_STATE_DEFAULTS): the same shape must
#: not be drawn at different sizes depending on how it was created.
#:
#: FORK, JOIN and the connection points WERE MISSING here. The gap broke more
#: than looks: turning a 170x84 simple state into a fork also skipped the
#: "enlarge if too small" branch below (the size was already large) and left A
#: HUGE BOX on screen instead of a bar. Because these pseudostates cannot be
#: resized (see StateItem.is_resizable) the user had no way back either -- the
#: only way to fix the shape was to delete the element and draw it again.
#: cizmekti.
PSEUDO_SIZES = {
    StateKind.INITIAL: (24.0, 24.0),
    StateKind.FINAL: (30.0, 30.0),
    StateKind.CHOICE: (38.0, 38.0),
    StateKind.JUNCTION: (22.0, 22.0),
    StateKind.SHALLOW_HISTORY: (32.0, 32.0),
    StateKind.DEEP_HISTORY: (32.0, 32.0),
    StateKind.TERMINATE: (30.0, 30.0),
    StateKind.FORK: (10.0, 90.0),
    StateKind.JOIN: (10.0, 90.0),
    StateKind.ENTRY_POINT: (18.0, 18.0),
    StateKind.EXIT_POINT: (18.0, 18.0),
}

#: The size given to the REAL state kinds when coming back from a small
#: pseudostate.
REAL_SIZES = {
    StateKind.COMPOSITE: (320.0, 200.0),
    StateKind.SUBMACHINE: (220.0, 96.0),
    StateKind.SIMPLE: (170.0, 84.0),
}


class _NoWheelMixin:
    """Ignores the mouse wheel while NOT focused.

    THIS WAS A REAL DATA-LOSS DEFECT. In Qt a QComboBox / QSpinBox catches the
    wheel event whenever the cursor is over it and CHANGES ITS VALUE -- even
    when it has not been clicked or focused. While scrolling the PROPERTIES
    panel, the cursor passing over the "Event" box made it jump silently to
    the first event ("BUTTON"), and on focus loss that was written to the model.

    The result: an event ended up on the initial transition and the error
    "V034 - An initial transition cannot have an event." appeared in the
    Problems panel without the user typing anything. We measured it exactly:
    a single wheel notch went '' -> 'BUTTON'.

    An unfocused widget IGNORES the wheel and leaves the event to the scroll
    area above it, so the panel scrolls as expected.
    """

    def wheelEvent(self, event) -> None:
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class NoWheelComboBox(_NoWheelMixin, QComboBox):
    pass


class NoWheelSpinBox(_NoWheelMixin, QSpinBox):
    pass


class NoWheelDoubleSpinBox(_NoWheelMixin, QDoubleSpinBox):
    pass


class MiniCodeEdit(QPlainTextEdit):
    """A small one- or two-line code field with syntax highlighting."""

    committed = pyqtSignal(str)

    def __init__(self, rows: int = 2, placeholder: str = "",
                 highlight: bool = True) -> None:
        super().__init__()
        self.setFont(mono_font(9))
        self.setPlaceholderText(placeholder)

        # Make the line-break marker DISCOVERABLE. The marker is valid in every
        # text field; when the caller sets a more specific hint (such as "an
        # initial transition cannot have a guard"), that one wins.
        self.setToolTip("Type %s to break the line on the diagram."
                        % LINE_BREAK_MARKER)

        # PLAIN TEXT WRAPS, CODE DOES NOT.
        #
        # The description field is prose; wrapping is the natural thing. The guard /
        # effect / include fields carry CODE where the line break is meaningful, and
        # wrapping there misrepresents the line -- horizontal scrolling is right.
        self._wraps = not highlight
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth
                             if self._wraps
                             else QPlainTextEdit.LineWrapMode.NoWrap)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff if self._wraps
            else Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._rows = max(1, rows)
        self._apply_height()

        # The timer is A CHILD OF THIS WIDGET. The panel tears the form down when
        # the selection changes; an ownerless `QTimer.singleShot` would fire on a
        # deleted C++ object and drop the process with a segfault. A child timer
        # dies together with the widget.
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.setInterval(0)
        self._fit_timer.timeout.connect(self.fit_to_content)

        self.document().documentLayout().documentSizeChanged.connect(
            self._on_doc_size)
        self.setTabStopDistance(4 * self.fontMetrics().horizontalAdvance(" "))
        self.highlighter = CppHighlighter(self.document()) if highlight else None

        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Base, QColor(C.EDITOR_BG))
        self.setPalette(pal)
        self._value = ""

    #: The box grows to at most this many rows; after that it scrolls.
    MAX_ROWS = 8

    def _chrome(self) -> int:
        """The vertical costs beyond the text (frame, margin, scroll bar).

        The old calculation was ``lineSpacing * rows + 8`` and it counted none
        of three costs: the frame width, the document margin and -- most
        importantly -- the **horizontal scroll bar**. With `NoWrap` a long line
        brings up the bar, and the bar takes about 14 px FROM INSIDE the fixed
        height; that is exactly why the last line was cut in half.
        """
        # +6: the rounding margin of the document layout. At 4 px the content
        # overflowed by a pixel or two and an unnecessary scroll bar appeared.
        overhead = 2 * self.frameWidth() + 2 * int(self.document().documentMargin()) + 6
        if not self._wraps:
            overhead += self.horizontalScrollBar().sizeHint().height()
        return overhead

    def _apply_height(self, row: Optional[int] = None) -> None:
        """The size that shows `rows` (or `_rows`) lines IN FULL."""
        n = self._rows if row is None else row
        n = max(self._rows, min(self.MAX_ROWS, n))
        self.setFixedHeight(self.fontMetrics().lineSpacing() * n + self._chrome())

    def _schedule_fit(self) -> None:
        """Defers the measurement until AFTER Qt has finished laying out.

        `documentSizeChanged` arrives before the widget is laid out, while its
        width is still unknown, and reports "1 line" every time; measuring then
        left the description box nailed at two lines. A zero-delay timer runs
        after the event loop has turned once -- that is, after the viewport
        width and the wrapping calculation are settled. Restarting the
        single-shot timer also collapses a burst of signals into ONE
        measurement by itself.
        """
        self._fit_timer.start()

    def _on_doc_size(self, _size) -> None:
        """`documentSizeChanged` -> defer the measurement (the size is stale)."""
        self._schedule_fit()

    def fit_to_content(self) -> None:
        """Grows the box to fit ITS CONTENT (at most ::MAX_ROWS lines).

        A fixed two lines left part of the text invisible in fields such as the
        description: the user had to scroll to read the sentence they had just
        written.

        CAREFUL: `QPlainTextDocumentLayout.documentSize()` is an exception. It
        gives the width in pixels but the HEIGHT AS A LINE COUNT (the plain
        text layout is a simplified one). Dividing it by the line height as if
        it were pixels turned a six-line description into 0.4 -> 1 line and the
        box never grew.
        """
        layout = self.document().documentLayout()
        row = layout.documentSize().height() if layout is not None else 0.0

        needed = int(row + 0.999)
        target = max(self._rows, min(self.MAX_ROWS, needed))

        new = self.fontMetrics().lineSpacing() * target + self._chrome()
        if new != self.height():
            # setFixedHeight gives birth to a new resizeEvent; calling it only when
            # there IS a change is what closes the loop.
            self.setFixedHeight(new)

    def resizeEvent(self, event) -> None:
        # When the width changes so does the line count of WRAPPED text; if the box
        # did not grow as the panel narrowed, the lower lines would be hidden.
        super().resizeEvent(event)
        if self._wraps:
            self._schedule_fit()

    def set_value(self, text: str) -> None:
        self._value = text
        if self.toPlainText() != text:
            self.setPlainText(text)
        # The height is set by the documentSizeChanged signal (see
        # fit_to_content); calling it here would happen before layout and give the
        # wrong line count.

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self._maybe_commit()

    def _maybe_commit(self) -> None:
        text = self.toPlainText()
        if text != self._value:
            self._value = text
            self.committed.emit(text)


class Inspector(QScrollArea):
    """A property form that changes with the selection."""

    message = pyqtSignal(str)

    def __init__(self, doc: Document, parent=None) -> None:
        super().__init__(parent)
        self.doc = doc
        self._loading = False
        self._ids: List[str] = []

        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._host = QWidget()
        self._layout = QVBoxLayout(self._host)
        # The user found the panel "too big"; the spacings were tightened.
        self._layout.setContentsMargins(8, 6, 8, 6)
        self._layout.setSpacing(6)
        self.setWidget(self._host)
        self.show_selection([])

    # ------------------------------------------------------------------ API #

    def show_selection(self, ids: List[str]) -> None:
        self._ids = list(ids)
        self._clear()
        sm = self.doc.machine

        states = [i for i in ids if i in sm.states]
        trans = [i for i in ids if i in sm.transitions]

        if len(ids) > 1:
            self._build_multi(len(states), len(trans))
        elif states:
            self._build_state(states[0])
        elif trans:
            self._build_transition(trans[0])
        else:
            self._build_machine()
        self._layout.addStretch(1)

    def retheme(self) -> None:
        """Rebuilds the form: the inline colours are read at set-up."""
        self.show_selection(self._ids)

    def selected_ids(self) -> List[str]:
        """The element ids the form is showing right now."""
        return list(self._ids)

    def refresh(self) -> None:
        """Refreshes the form when the model is changed from outside.

        If the user IS TYPING in a text field it is left alone; otherwise what
        they type would be reset on every keystroke.

        BUT only for text fields. The refresh used to be skipped entirely
        whenever ANY widget INSIDE the panel had focus, and the "Kind" box
        triggered that most of all: a user changing the kind already has focus
        in that box. The form stayed up with the rows of the old kind -- after
        a simple state was turned into a junction, the "Defers" and behaviour
        boxes were still on screen, and touching one of them wrote a field into
        the model that CANNOT EXIST on a pseudostate in UML (14.5.9.6: those
        are State properties).

        THE SPIN BOX IS PRESERVED TOO, the combo box is not. The distinction
        rests on WHAT rebuilding the form BREAKS in that widget:

        * The "Kind" combo box MUST change the form -- which rows exist changes
          with the kind. Preserved, a state turned into a pseudostate would
          keep its "Defers" and behaviour boxes on screen.
          ekranda kalirdi.
        * A spin box does not change the form, but rebuilding DESTROYS it:
          every press of the up arrow deleted and recreated the widget, focus
          was lost, and because keyboard tracking is off a half-typed value was
          discarded as well.

        NOTE: `QApplication.focusWidget()` returns THE BOX ITSELF when a
        QSpinBox has focus, not the QLineEdit inside it; that is why
        QAbstractSpinBox is listed explicitly.
        """
        from PyQt6.QtWidgets import (QAbstractSpinBox, QApplication,
                                     QLineEdit, QPlainTextEdit, QTextEdit)
        focus = QApplication.focusWidget()
        preserved = (QLineEdit, QPlainTextEdit, QTextEdit, QAbstractSpinBox)
        if isinstance(focus, preserved) and self._host.isAncestorOf(focus):
            return
        self.show_selection(self._ids)

    # --------------------------------------------------------------- helpers #

    def _clear(self) -> None:
        """Empties the form.

        deleteLater() ALONE IS NOT ENOUGH: it defers the deletion to the next
        event loop, while until then the widget is still a child of the panel
        and, removed from the layout, keeps painting OVER the panel with its
        last (or, if never laid out, the default 640x480) geometry. When the
        user changes the selection quickly -- or when the editingFinished signal
        of a field changes the model and calls refresh() -- the event loop does
        not get a turn and several overlapping "ghost" forms appear. They must
        be detached first and deleted after; the deletion still has to be
        deferred, because this code can be called from inside the deleted
        widget's own signal.
        """
        while self._layout.count():
            item = self._layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

    def _group(self, title: str) -> QFormLayout:
        box = QGroupBox(title)
        form = QFormLayout(box)
        form.setContentsMargins(8, 4, 8, 6)
        form.setSpacing(4)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight
                               | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        # THE LABEL IS ALWAYS ABOVE THE FIELD.
        #
        # In a two-column layout, a long label such as "Extra includes" left about
        # 94 px for the field and the content ("blinky_ctx_t",
        # '#include "blinky_ctx.h"') was clipped -- the user could not see what they
        # had typed. WrapLongRows was tried: Qt considered the row to "fit" so it
        # never triggered. Because a properties panel is NARROW by nature, a single
        # column is the right answer: the field takes the FULL width of the panel and
        # is clipped at no width at all.
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft
                               | Qt.AlignmentFlag.AlignVCenter)
        self._layout.addWidget(box)
        return form

    def _hint(self, text: str) -> None:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setFont(ui_font(8))
        label.setStyleSheet("color: %s;" % C.TEXT_DIM)
        self._layout.addWidget(label)

    def _edit(self, mutator, label: str) -> None:
        if self._loading:
            return
        if self.doc.edit(label, mutator):
            self.message.emit(label)

    # -------------------------------------------------------------- machine  #

    def _build_machine(self) -> None:
        sm = self.doc.machine
        self._loading = True
        form = self._group("State machine")

        name = QLineEdit(sm.name)
        name.editingFinished.connect(
            lambda: self._edit(lambda m: setattr(m, "name", name.text().strip() or m.name),
                               "Machine name"))
        form.addRow("Name", name)

        prefix = QLineEdit(sm.prefix)
        prefix.setToolTip("Prefix of the generated C symbols, e.g. 'blinky' -> blinky_dispatch()")
        prefix.editingFinished.connect(
            lambda: self._edit(lambda m: setattr(m, "prefix", prefix.text().strip() or m.prefix),
                               "Symbol prefix"))
        form.addRow("Symbol prefix", prefix)

        ctx = QLineEdit(sm.context_type)
        ctx.setToolTip("The type that appears as 'ctx' inside action and "
                       "guard bodies.\n"
                       "Write 'void' to generate no context at all.")
        ctx.editingFinished.connect(
            lambda: self._edit(lambda m: setattr(m, "context_type",
                                                 ctx.text().strip() or "void"),
                               "Context type"))
        form.addRow("Context type", ctx)

        inc = MiniCodeEdit(2, '#include "app_types.h"')
        inc.set_value(sm.user_includes)
        inc.committed.connect(
            lambda t: self._edit(lambda m: setattr(m, "user_includes", t), "Extra includes"))
        form.addRow("Extra includes", inc)

        desc = MiniCodeEdit(2, "What does this machine do?", highlight=False)
        desc.setFont(ui_font(9))
        desc.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        desc.set_value(sm.description)
        desc.committed.connect(
            lambda t: self._edit(lambda m: setattr(m, "description", t), "Description"))
        form.addRow("Description", desc)

        self._loading = False

    # ---------------------------------------------------------------- state  #

    def _build_state(self, sid: str) -> None:
        sm = self.doc.machine
        st = sm.states[sid]
        self._loading = True

        form = self._group("State")

        name = QLineEdit(st.name)
        name.setObjectName("state_name")
        name.editingFinished.connect(
            lambda: self._edit(self._rename(sid, name), "State name"))
        form.addRow("Name", name)
        self._name_field = name

        kind = NoWheelComboBox()
        for text, value in KIND_LABELS:
            kind.addItem(text, value)
        kind.setCurrentIndex([k for _, k in KIND_LABELS].index(st.kind))
        kind.activated.connect(lambda _i: self._change_kind(sid, kind.currentData()))
        form.addRow("Kind", kind)

        parent = sm.parent_of(sid)
        if parent is not None and sm.is_orthogonal(parent.id):
            lbl = QLabel("%s  ·  region %d of %d"
                         % (parent.name, sm.region_of(sid) + 1,
                            sm.region_count(parent.id)))
            lbl.setToolTip("Drag the state into another band of the "
                           "orthogonal state to move it to that region.")
        else:
            lbl = QLabel(parent.name if parent else "(root region)")
        lbl.setStyleSheet("color: %s;" % C.TEXT_DIM)
        form.addRow("Parent state", lbl)

        # DEFERRED EVENTS -- only on real states.
        #
        # UML 2.5.1, 14.5.9.6: deferrableTrigger is a STATE property. A
        # comma-separated list; leaving it empty means "none".
        if st.kind.is_real_state:
            deferred_list = QLineEdit(", ".join(st.deferred or []))
            deferred_list.setObjectName("state_deferred")
            deferred_list.setAccessibleName("Deferred events")
            deferred_list.setPlaceholderText("e.g. PRINT, RESIZE")
            deferred_list.setToolTip(
                "Event types held in the pool while this state is active "
                "instead of being dispatched. They are delivered once the "
                "machine reaches a configuration that no longer defers them "
                "(UML 2.5.1, 14.2.3.4.4).")
            deferred_list.editingFinished.connect(
                lambda _sid=sid, _f=deferred_list: self._edit(
                    self._set_deferred(_sid, _f.text()), "Deferred events"))
            form.addRow("Defers", deferred_list)

        # SUBMACHINE REFERENCE -- only on submachine states.
        #
        # The .usm files in the workspace are LISTED. With a free text box the user
        # could mistype the path and would only see the error during code
        # generation; and the path is RELATIVE to the workspace.
        if st.kind is StateKind.SUBMACHINE:
            selection = NoWheelComboBox()
            selection.setAccessibleName("Referenced machine")
            selection.setToolTip(
                "The state machine this state stands for. Its contents are "
                "inserted here when code is generated (UML 2.5.1, "
                "14.2.3.4.7).")
            existing = (getattr(st, "submachine_ref", "") or "").strip()
            selection.addItem("(none)", "")
            for path in self._workspace_machines():
                selection.addItem(path, path)
            if existing and selection.findData(existing) < 0:
                # The referenced file is gone; show it rather than removing it SILENTLY,
                # so the user knows what is missing.
                selection.addItem("%s  (missing)" % existing, existing)
            selection.setCurrentIndex(max(0, selection.findData(existing)))
            selection.activated.connect(
                lambda _i, _sid=sid, _c=selection: self._edit(
                    self._set_submachine(_sid, _c.currentData()),
                    "Submachine reference"))
            form.addRow("Machine", selection)

        # REGION COUNT -- only on composite states.
        #
        # UML 2.5.1, 14.2.3.2 (printed p.307): a composite state owns one or more
        # regions; a state with several regions is ORTHOGONAL and its regions are
        # active at the same time.
        if st.kind is StateKind.COMPOSITE:
            region = NoWheelSpinBox()
            region.setRange(1, 8)
            region.setValue(max(1, int(getattr(st, "regions", 1) or 1)))
            region.setAccessibleName("Region count")
            region.setToolTip(
                "How many orthogonal regions this state owns. Two or more "
                "regions run at the same time; each needs its own initial "
                "pseudostate.")
            # Keyboard tracking is OFF: typing "12" into the box would first emit the
            # value 1, and every intermediate value would be a separate edit.
            region.setKeyboardTracking(False)
            region.valueChanged.connect(
                lambda v, _sid=sid: self._edit(self._set_regions(_sid, v),
                                               "Region count"))
            form.addRow("Regions", region)

        # BEHAVIOURS: only on states that can be STAYED IN.
        #
        # `is_real_state` includes FINAL because for CODE GENERATION a final state is
        # a state too. But in UML (14.2.3.4) a FinalState CANNOT CARRY entry / exit /
        # doActivity -- and the form still opened three boxes. The user: "the
        # properties do not show up according to the tool"; fields that did NOT belong
        # to the kind of the element were being shown as well. On a FINAL state the
        # fields are shown only IF THEY ALREADY CONTAIN SOMETHING. Otherwise a
        # behaviour coming from an old file produces a V069 error with nowhere for the
        # user to DELETE it.
        final_filled = (st.kind == StateKind.FINAL
                      and (st.entry.strip() or st.exit.strip()
                           or st.do.strip()))
        if st.kind.is_real_state and (st.kind != StateKind.FINAL or final_filled):
            beh = self._group("Behaviors")
            if final_filled:
                warning = QLabel("A final state cannot carry behavior "
                               "(UML 2.5.1 §14.2.3.4) — clear these fields.")
                warning.setWordWrap(True)
                warning.setStyleSheet("color: %s;" % C.WARN)
                beh.addRow(warning)
            for attr, caption, ph in (("entry", "entry /", "when the state becomes active"),
                                      ("exit", "exit /", "when the state is exited"),
                                      ("do", "do /", "on every <prefix>_do() call")):
                # Starts at one line and grows WITH THE CONTENT (fit_to_content). A fixed
                # two lines took 39 px extra across three boxes and the form did not fit
                # in the panel.
                ed = MiniCodeEdit(1, ph)
                ed.set_value(getattr(st, attr))
                ed.committed.connect(
                    lambda t, a=attr: self._edit(
                        lambda m: setattr(m.states[sid], a, t), "%s behavior" % a))
                beh.addRow(caption, ed)
            self._hint("Enter, or %s anywhere in the text, breaks the line "
                       "on the diagram. Inside a C string it stays literal, "
                       "so printf(\"…%s\") is left alone."
                       % (LINE_BREAK_MARKER, LINE_BREAK_MARKER))

        # THE GEOMETRY FIELDS (X / Y / Width / Height) WERE REMOVED.
        #
        # The user: "do not write silly properties in the panel, what am I to do
        # with the x and y axis information, or the width and height". Fair: placing
        # the box by dragging it on the canvas and pulling its edge is both faster
        # and gives you what you see. Four spin boxes occupied the most valuable
        # part of the panel -- right under the behaviours. The position/size stay IN
        # THE MODEL; they only left the form.
        note_grubu = self._group("Note")
        note = QLineEdit(st.note)
        note.editingFinished.connect(
            lambda: self._edit(lambda m: setattr(m.states[sid], "note", note.text()),
                               "Note"))
        note_grubu.addRow("Note", note)

        self._loading = False

    def _set_deferred(self, sid: str, text: str):
        """Turns a comma-separated list into DEFERRED events."""
        names = []
        for part in (text or "").replace(";", ",").split(","):
            name = part.strip()
            if name and name not in names:
                names.append(name)

        def mutate(machine):
            st = machine.states.get(sid)
            if st is not None:
                st.deferred = list(names)
        return mutate

    def _workspace_machines(self):
        """The state machine files in the workspace (relative paths).

        `Workspace.model_path` is a PROPERTY, not a function. It was being
        called here as `ws.model_path()`: it threw
        `TypeError: 'str' object is not callable` every time, the broad
        `except Exception` right below swallowed it, and the function returned
        AN EMPTY LIST.

        The consequence was not small: the "Machine" combo box was ALWAYS empty,
        in EVERY workspace. The user could draw a submachine state but could
        NOT BIND it to a machine -- so the whole submachine feature was
        unreachable from the interface. What hid the defect was that broad
        `except`: it turned a programming error into a plausible-looking
        "no machine was found".

        So only file system errors are swallowed here now; any other exception
        travels up and becomes visible.
        """
        import os
        win = self.window()
        ws = getattr(win, "workspace", None)
        if ws is None:
            return []
        root = ws.model_path
        if not os.path.isdir(root):
            return []
        try:
            names = sorted(os.listdir(root))
        except OSError:
            return []
        out = []
        for name in names:
            if not name.lower().endswith(".usm"):
                continue
            try:
                out.append(ws.relative(os.path.join(root, name)))
            except ValueError:
                continue                        # a path that falls outside the root
        return out

    def _set_submachine(self, sid: str, ref: str):
        def mutate(machine):
            st = machine.states.get(sid)
            if st is not None:
                st.submachine_ref = (ref or "").strip()
        return mutate

    def _set_regions(self, sid: str, count: int):
        """The operation that changes the region count of a composite state.

        SHRINKING IS NO LONGER LOSSY. The region number is reread from the BAND
        the element is drawn in on every redraw
        (DiagramCanvas._sync_regions_from_drawing) and the coordinates of the
        elements do not change in this operation. Going from three regions down
        to one and back to three restores the old distribution exactly: the
        information was never in the `region` field but IN THE POSITION.

        The clamping here is so the model stays valid until the canvas is drawn
        next; and if an old or hand-edited file points at a region that does not
        exist, it tidies that up too (the validator also reports it with V103).

        """
        def mutate(machine):
            st = machine.states.get(sid)
            if st is None:
                return
            st.regions = max(1, int(count))
            last = st.regions - 1
            for c in machine.children(sid):
                if int(getattr(c, "region", 0) or 0) > last:
                    c.region = last
        return mutate

    def _rename(self, sid: str, field: QLineEdit):
        def mutate(m):
            text = field.text().strip()
            if text:
                m.states[sid].name = text
        return mutate

    def _change_kind(self, sid: str, new_kind: StateKind) -> None:
        sm = self.doc.machine
        st = sm.states[sid]
        if st.kind is new_kind:
            return
        if st.kind is StateKind.COMPOSITE and sm.children(sid):
            self.message.emit("Move the substates out first: the composite state is not empty.")
            self.show_selection(self._ids)
            return

        def mutate(m):
            target = m.states[sid]
            target.kind = new_kind
            if new_kind in PSEUDO_SIZES:
                target.w, target.h = PSEUDO_SIZES[new_kind]
                # A PSEUDOSTATE HAS NO BEHAVIOUR. UML 2.5.1, 14.5.9.6: entry/exit/
                # doActivity and the deferred triggers are State properties, not
                # Pseudostate ones. The deferred events used never to be cleared: turning
                # a simple state into a junction left a record in the generated
                # `deferred[]` table that no diagram element accounted for.
                # 
                target.entry = ""
                target.exit = ""
                target.do = ""
                target.deferred = []
            elif target.w < 90.0 or target.h < 54.0:
                target.w, target.h = REAL_SIZES.get(new_kind,
                                                    REAL_SIZES[StateKind.SIMPLE])
            if new_kind is not StateKind.SUBMACHINE:
                # The submachine name is meaningful only on the SUBMACHINE kind; left in
                # place, validation and flattening keep looking for a document that does
                # not exist.
                target.submachine_ref = ""

        self._edit(mutate, "State kind")

    def focus_name_field(self) -> None:
        field = getattr(self, "_name_field", None)
        if field is not None and field.parent() is not None:
            field.setFocus()
            field.selectAll()

    # ----------------------------------------------------------- transition  #

    def _build_transition(self, tid: str) -> None:
        sm = self.doc.machine
        tr = sm.transitions[tid]
        self._loading = True

        form = self._group("Transition")

        src = sm.states.get(tr.source)
        dst = sm.states.get(tr.target)
        route = QLabel("%s  →  %s" % (src.name if src else "?", dst.name if dst else "?"))
        # STATE_TITLE is for the coloured title strip on the canvas (white in both
        # themes); it was unreadable on the panel background in the light theme.
        route.setStyleSheet("color: %s; font-weight: 700;" % C.TEXT_BRIGHT)
        form.addRow("Route", route)

        # AN INITIAL TRANSITION CAN CARRY NEITHER an event NOR a guard (UML 2.5.1
        # §14.5.6.7; V034 / V035 in the validator). Leaving the fields writable only
        # invites the user to produce an error -- which is exactly what the wheel
        # accident did. The fields stay visible (the rule is instructive) but are
        # DISABLED and say why.
        start = src is not None and src.kind == StateKind.INITIAL

        event = NoWheelComboBox()
        event.setEditable(True)
        event.addItem("")
        for ev in sm.events():
            if ev:
                event.addItem(ev)
        event.setCurrentText(tr.event)
        event.setToolTip("Leave empty for a completion (event-less) transition.")
        event.lineEdit().editingFinished.connect(
            lambda: self._edit(
                lambda m: setattr(m.transitions[tid], "event",
                                  event.currentText().strip()), "Event"))
        form.addRow("Event", event)

        guard = MiniCodeEdit(1, "ctx->count > 3   or   else")
        guard.set_value(tr.guard)
        guard.committed.connect(
            lambda t: self._edit(lambda m: setattr(m.transitions[tid], "guard", t),
                                 "Guard"))
        form.addRow("[Guard]", guard)

        if start:
            blocked = ("An initial transition cannot have an event or a guard "
                     "(UML 2.5.1 §14.5.6.7).")
            for w in (event, guard):
                w.setEnabled(False)
                w.setToolTip(blocked)

        action = MiniCodeEdit(2, "ctx->count++;")
        action.set_value(tr.action)
        action.committed.connect(
            lambda t: self._edit(lambda m: setattr(m.transitions[tid], "action", t),
                                 "Action"))
        form.addRow("/ Effect", action)
        self._hint("Type %s in the event, guard or effect to break the "
                   "transition label over several lines."
                   % LINE_BREAK_MARKER)

        adv = self._group("Details")

        kind = NoWheelComboBox()
        for text, value in TKIND_LABELS:
            kind.addItem(text, value)
        kind.setCurrentIndex([k for _, k in TKIND_LABELS].index(tr.kind))
        kind.activated.connect(
            lambda _i: self._edit(
                lambda m: setattr(m.transitions[tid], "kind", kind.currentData()),
                "Transition kind"))
        adv.addRow("Kind", kind)

        prio = NoWheelSpinBox()
        prio.setRange(0, 99)
        prio.setValue(tr.priority)
        prio.setToolTip("Lower numbers are tried first; this orders the branches of a choice.")
        prio.editingFinished.connect(
            lambda: self._edit(
                lambda m: setattr(m.transitions[tid], "priority", prio.value()),
                "Priority"))
        adv.addRow("Priority", prio)

        preview = QLabel(tr.label() or "(completion)")
        preview.setFont(mono_font(9))
        preview.setStyleSheet("color: %s;" % C.GREEN)
        preview.setWordWrap(True)
        adv.addRow("UML label", preview)

        self._loading = False

    # ------------------------------------------------------ multiple selection

    def _build_multi(self, n_states: int, n_trans: int) -> None:
        form = self._group("Multiple selection")
        form.addRow("State", QLabel(str(n_states)))
        form.addRow("Transition", QLabel(str(n_trans)))
