"""Intermediate Representation for code generation.

Turns the diagram model into *flat*, *deterministic* tables that are
independent of the target language. Thanks to this step the C and the C++
generator share the same semantic reasoning; their behaviour cannot diverge.

The important transformations:
  * Initial pseudostates are eliminated; they are folded into per-region
    (initial_child, initial_action) tables.
  * A choice pseudostate becomes a real (transient) state; its outgoing
    transitions are triggered by the "completion" event.
  * Identical guard/action text is reduced to a single id (code size).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..core.model import StateKind, StateMachine, TransitionKind
from ..core.text_layout import expand_breaks

NONE = 0xFF          # invalid state index (uint8 sentinel)
MAX_VERTICES = 254   # we leave one slot for the NONE sentinel

#: Invalid REGION index. It is a SEPARATE number space from the state
#: index; sharing one sentinel across two spaces would produce a unit
#: mix-up that is very hard to notice once either space grows.
REGION_NONE = 0xFF
MAX_REGIONS = 254

#: The maximum number of event types. The event index is a `uint8_t` in
#: the generated code; number 0 is reserved for COMPLETION and 255 is the
#: INVALID sentinel. The bound WAS NOT CHECKED: the 255th user event
#: silently took the same number as INVALID, and anything beyond did not
#: fit in a uint8_t. The generated code compiled but looked at the wrong
#: event -- the hardest kind of bug to find on an embedded device.
MAX_EVENTS = 254

MAX_JUNCTION_DEPTH = 8    # the maximum number of steps in a nested junction chain
MAX_JUNCTION_PATHS = 64   # the maximum number of paths a single transition may open into

KIND_SIMPLE = 0
KIND_COMPOSITE = 1
KIND_FINAL = 2
KIND_CHOICE = 3        # choice and junction have the same run-time meaning
KIND_TERMINATE = 4     # terminate pseudostate: entering it ends the machine
KIND_HIST_SHALLOW = 5  # H  pseudostate (transient; resolved through history)
KIND_HIST_DEEP = 6     # H* pseudostate (transient; resolved through history)

TKIND_EXTERNAL = 0
TKIND_INTERNAL = 1
TKIND_LOCAL = 2


class CodegenError(Exception):
    """An error that arose during code generation and validation did not catch."""


def as_statement(code: str) -> str:
    """Turns action text written by the user into a valid C/C++ statement.

    The tool leaves the trailing semicolon optional in action fields. The
    definition lives here, in the SHARED layer of the generators: single
    actions and actions merged along junction paths must obey the same rule.
    """
    text = code.strip()
    if not text:
        return ""
    if text.endswith(";") or text.endswith("}"):
        return text
    return text + ";"


@dataclass
class IrState:
    index: int
    model_id: str
    name: str                     # the name the user gave (a C identifier)
    kind: int                     # KIND_*
    parent: int                   # NONE = root
    depth: int
    entry: str = ""
    exit: str = ""
    do: str = ""
    initial_child: int = NONE     # the default of the composite state's FIRST region
    initial_action: int = -1      # the action id of the initial transition
    history_default: int = NONE   # the default target of a history pseudostate
    note: str = ""
    #: The event indices DEFERRED in this state (UML deferrableTrigger).
    deferred: List[int] = field(default_factory=list)

    # -- regions # ---------------------------------------------------------- #
    #: The GLOBAL index of the region this vertex sits IN.
    region: int = 0
    #: The global index of the FIRST region owned by a composite state.
    first_region: int = REGION_NONE
    #: How many regions a composite state owns (0 = not composite).
    region_count: int = 0


@dataclass
class IrRegion:
    """A region (UML 2.5.1, 14.2.3.2 Region).

    A region is the container of a sub-configuration that can be active
    concurrently. At run time every region has its OWN active leaf state and
    its OWN history record.

    HISTORY IS KEYED BY REGION, NOT by the parent state: in an orthogonal
    state two regions belong to the same parent; if a single record were
    shared, returning through deep history would make one region restore the
    other region's state.
    """

    index: int
    owner: int                    # NONE = root region; otherwise a composite state
    initial_state: int = NONE     # the default substate of the region
    initial_action: int = -1      # the action id of the initial transition
    name: str = ""                # a readable name, for diagnostics


@dataclass
class IrTransition:
    index: int
    model_id: str
    source: int
    target: int
    event: int                    # 0 = completion
    guard: int = -1
    action: int = -1
    kind: int = TKIND_EXTERNAL
    text: str = ""                # a readable label, for the comment line

    # -- fork / join (UML 2.5.1, 14.2.3.7, printed p.313) # ----------------- #
    #
    # Both are FLATTENED INTO THE TABLE like a junction; at run time there
    # is no separate node kind. That way the generated code never has to
    # learn a new state class.
    #
    #: FORK: the vertices to be entered EXPLICITLY in the regions of the
    #: orthogonal state being entered. Unnamed regions start at their default.
    fork_targets: List[int] = field(default_factory=list)
    #: JOIN: the sources that must be active AT THE SAME TIME for the
    #: transition to be enabled. Empty means an ordinary transition.
    join_sources: List[int] = field(default_factory=list)
    #: Filled in by `Ir.extra_table()`: the start index of this row's
    #: fork/join items in the shared table.
    extra_first: int = 0


#: C/C++ keywords and constructs that look like a call.
#:
#: `if (x)` is NOT a function call; without filtering, the user would be
#: told "you must write the if function".
_ANAHTAR = {
    "if", "else", "for", "while", "switch", "case", "default", "do",
    "return", "break", "continue", "goto", "sizeof", "typedef", "struct",
    "union", "enum", "static", "const", "volatile", "extern", "inline",
    "signed", "unsigned", "void", "char", "short", "int", "long", "float",
    "double", "bool", "true", "false", "NULL", "nullptr", "static_cast",
    "reinterpret_cast", "const_cast", "dynamic_cast", "new", "delete",
    "this", "and", "or", "not",
}

#: Calls of the form `name(`. A preceding `.`/`->`/`::` makes it a MEMBER
#: call (like ctx->reset()) and it belongs to the user's context.
_CAGRI = re.compile(r"(?<![\w.>:])([A-Za-z_]\w*)\s*\(")


def _argument_sayisi(metin: str, acilis: int) -> int:
    """Counts the arguments by top-level commas, starting at the `(`."""
    derinlik = 0
    sayi = 0
    gorulen = False
    i = acilis
    while i < len(metin):
        ch = metin[i]
        if ch in "([{":
            derinlik += 1
        elif ch in ")]}":
            derinlik -= 1
            if derinlik == 0:
                return (sayi + 1) if gorulen else 0
        elif ch == "," and derinlik == 1:
            sayi += 1
        elif not ch.isspace() and derinlik == 1:
            gorulen = True
        i += 1
    return sayi


def _dizgileri_bosalt(metin: str) -> str:
    """Blanks out the INSIDE of string and character literals.

    Scanned by hand rather than with a regex: a pattern that handles escape
    sequences (`"a\\"b"`) correctly is both hard to read and one more chance
    to miss an escape in this file. The length is preserved so offsets hold.
    """
    out = []
    tirnak = ""
    kacis = False
    for ch in metin:
        if tirnak:
            out.append(" " if ch != tirnak or kacis else ch)
            if kacis:
                kacis = False
            elif ch == chr(92):
                kacis = True
            elif ch == tirnak:
                tirnak = ""
            continue
        if ch in ('"', "'"):
            tirnak = ch
            out.append(ch)
            continue
        out.append(ch)
    return "".join(out)


def _cagrilar(metin: str):
    """The (name, argument_count) calls found in the text."""
    # Parentheses inside a string would break the argument count.
    temiz = _dizgileri_bosalt(metin)
    for m in _CAGRI.finditer(temiz):
        ad = m.group(1)
        if ad in _ANAHTAR:
            continue
        yield ad, _argument_sayisi(temiz, m.end() - 1)


@dataclass
class RequiredSymbol:
    """An external function the user has to supply."""

    name: str
    argc: int = 0
    in_guard: bool = False
    sites: List[str] = field(default_factory=list)

    def summary(self) -> str:
        """The one-line summary written into the documentation."""
        arg = "no arguments" if self.argc == 0 else (
            "1 argument" if self.argc == 1 else "%d arguments" % self.argc)
        rol = ("used in a guard, so it must RETURN a value"
               if self.in_guard else "called as a statement")
        return "%s(): %s, %s" % (self.name, arg, rol)


@dataclass
class Ir:
    name: str
    prefix: str
    description: str
    context_type: str
    user_includes: List[str]

    states: List[IrState] = field(default_factory=list)
    regions: List[IrRegion] = field(default_factory=list)
    transitions: List[IrTransition] = field(default_factory=list)
    events: List[str] = field(default_factory=list)     # [0] = COMPLETION
    guards: List[str] = field(default_factory=list)
    actions: List[str] = field(default_factory=list)

    # Transition range grouped by source state: state -> (first, count)
    tran_slice: Dict[int, Tuple[int, int]] = field(default_factory=dict)

    #: The global indices of the root regions (a single item for now).
    root_regions: List[int] = field(default_factory=list)
    max_depth: int = 1

    # --------------------------------------------------------------- helpers #
    # For old call sites: the default of the root region.
    @property
    def root_initial(self) -> int:
        if not self.root_regions:
            return NONE
        return self.regions[self.root_regions[0]].initial_state

    @property
    def root_initial_action(self) -> int:
        if not self.root_regions:
            return -1
        return self.regions[self.root_regions[0]].initial_action

    @property
    def region_count(self) -> int:
        return len(self.regions)

    def has_orthogonal(self) -> bool:
        """Does the model contain a state with MORE THAN ONE region?"""
        return any(st.region_count > 1 for st in self.states)

    def regions_of(self, state_index: int) -> List[int]:
        """The global indices of the regions owned by a composite state."""
        st = self.states[state_index]
        if st.region_count <= 0:
            return []
        return list(range(st.first_region, st.first_region + st.region_count))

    @property
    def state_count(self) -> int:
        return len(self.states)

    @property
    def event_count(self) -> int:
        return len(self.events)

    @property
    def tran_count(self) -> int:
        return len(self.transitions)

    def state_by_index(self, i: int) -> IrState:
        return self.states[i]

    def has_context(self) -> bool:
        return self.context_type.strip() not in ("", "void")

    def has_history(self) -> bool:
        return any(st.kind in (KIND_HIST_SHALLOW, KIND_HIST_DEEP)
                   for st in self.states)

    def required_functions(self) -> List["RequiredSymbol"]:
        """The functions the user has to write THEMSELVES.

        The entry/exit/do bodies, transition effects and guard expressions in
        the model are C/C++ texts written by the user; the calls inside them
        are NOT DEFINED by the generator. If we do not say so, the user finds
        out about the gap only at link time, as
        "undefined reference to `led_write`".

        The name, HOW MANY ARGUMENTS it is called with and where it appears
        are collected. NO type inference is done: writing a wrong prototype is
        worse than writing none (a `void f()` declaration, for instance, can
        hide the real signature and create a silent mismatch). So the
        generated files DOCUMENT these rather than declare them.
        """
        bulunan: Dict[str, RequiredSymbol] = {}

        def tara(metin: str, nerede: str, guard: bool) -> None:
            for ad, argc in _cagrilar(metin):
                kayit = bulunan.get(ad)
                if kayit is None:
                    kayit = RequiredSymbol(name=ad, argc=argc)
                    bulunan[ad] = kayit
                kayit.argc = max(kayit.argc, argc)
                kayit.in_guard = kayit.in_guard or guard
                if nerede not in kayit.sites:
                    kayit.sites.append(nerede)

        for st in self.states:
            for govde, etiket in ((st.entry, "entry"), (st.exit, "exit"),
                                  (st.do, "do")):
                if govde.strip():
                    tara(govde, "%s / %s" % (st.name, etiket), False)
        for eylem in self.actions:
            tara(eylem, "transition effect", False)
        for kosul in self.guards:
            tara(kosul, "guard", True)
        return sorted(bulunan.values(), key=lambda r: r.name)

    def time_triggers(self):
        """A list of (state, event, delay_expression) triples.

        One record for every transition out of a state with an `after(N)`
        trigger. The generated code starts the timer ON ENTRY to the state and
        cancels it ON EXIT; posting the event is up to the user.

        INTERNAL TRANSITIONS ARE HERE TOO. There are two separate questions,
        and they used to be confused with each other:

        1. Does the timer RESTART when the transition fires? It does not,
           because an internal transition runs neither exit nor entry -- and
           that is correct.
        2. Does the timer START AT ALL when the state is entered? It must:

        The second question was being answered "no" as well: internal
        transitions were dropped from the list entirely. The result was a
        diagram that worked in simulation but did NOTHING in the embedded
        code. The generated file had no hook to post that event, so the event
        was never born and the internal transition never fired. Silently dead
        code on the device, a drawing that looked alive in the interface.
        """
        from ..core.model import time_event_delay
        out = []
        gorulen = set()
        for t in self.transitions:
            if t.event == 0:
                continue
            gecikme = time_event_delay(self.events[t.event])
            if gecikme is None:
                continue
            anahtar = (t.source, t.event)
            if anahtar in gorulen:
                continue
            gorulen.add(anahtar)
            out.append((t.source, t.event, gecikme))
        return sorted(out)

    def has_time_events(self) -> bool:
        return bool(self.time_triggers())

    def has_deferred(self) -> bool:
        """Does the model have any DEFERRED event?"""
        return any(st.deferred for st in self.states)

    def has_fork_join(self) -> bool:
        """Does the model contain a fork or a join?

        If not, the corresponding tables and branches are NEVER written into
        the generated code, so the output of simple models stays as it was.
        """
        return any(t.fork_targets or t.join_sources for t in self.transitions)

    def extra_table(self) -> List[int]:
        """The flattened table of fork targets and join sources."""
        out: List[int] = []
        for t in self.transitions:
            t.extra_first = len(out)
            out.extend(t.fork_targets)
            out.extend(t.join_sources)
        return out

    def has_terminate(self) -> bool:
        return any(st.kind == KIND_TERMINATE for st in self.states)


# --------------------------------------------------------------------------- #

def _dedup_add(pool: List[str], text: str) -> int:
    """Never adds the same text twice; returns its id. Empty text -> -1."""
    t = text.strip()
    if not t:
        return -1
    try:
        return pool.index(t)
    except ValueError:
        pool.append(t)
        return len(pool) - 1


_KIND_MAP = {
    StateKind.SIMPLE: KIND_SIMPLE,
    StateKind.COMPOSITE: KIND_COMPOSITE,
    StateKind.FINAL: KIND_FINAL,
    StateKind.CHOICE: KIND_CHOICE,
    StateKind.JUNCTION: KIND_CHOICE,   # junction = static choice; same table meaning
    StateKind.TERMINATE: KIND_TERMINATE,
    StateKind.SHALLOW_HISTORY: KIND_HIST_SHALLOW,
    StateKind.DEEP_HISTORY: KIND_HIST_DEEP,
}

_TKIND_MAP = {
    TransitionKind.EXTERNAL: TKIND_EXTERNAL,
    TransitionKind.INTERNAL: TKIND_INTERNAL,
    TransitionKind.LOCAL: TKIND_LOCAL,
}


def build_ir(sm: StateMachine, resolve=None) -> Ir:
    """Produces the IR from a validated model.

    Note: `validate()` must have passed without errors. There are still
    defensive checks here; an inconsistency raises CodegenError.

    ``resolve``: the function that resolves submachine references
    (``ref -> StateMachine``). If the model has a submachine, the machine is
    flattened FIRST: UML 2.5.1, 14.2.3.4.7 defines a submachine as a
    "macro-like insertion", so the expansion IS the meaning.
    """
    from ..core.submachine import SubmachineError, flatten, has_submachine

    if has_submachine(sm):
        if resolve is None:
            raise CodegenError(
                "This model references a submachine, which can only be "
                "resolved inside a workspace.")
        try:
            sm = flatten(sm, resolve)
        except SubmachineError as exc:
            raise CodegenError(str(exc))

    ir = Ir(
        name=sm.name,
        prefix=sm.prefix,
        description=sm.description,
        context_type=(sm.context_type or "void").strip() or "void",
        user_includes=[ln.strip() for ln in (sm.user_includes or "").splitlines() if ln.strip()],
    )

    # --- 1) Vertices to index: everything EXCEPT initial -----------------------
    # Like INITIAL, FORK and JOIN do not enter the table either: all three
    # are flattened into the compound transition (see step 5 below).
    _ELENEN = (StateKind.INITIAL, StateKind.FORK, StateKind.JOIN,
               StateKind.ENTRY_POINT, StateKind.EXIT_POINT)
    vertices = [s for s in sm.ordered_states() if s.kind not in _ELENEN]
    if len(vertices) > MAX_VERTICES:
        raise CodegenError("This model has %d states; at most %d are supported."
                           % (len(vertices), MAX_VERTICES))
    index_of: Dict[str, int] = {s.id: i for i, s in enumerate(vertices)}

    for i, s in enumerate(vertices):
        parent_idx = NONE
        if s.parent is not None:
            if s.parent not in index_of:
                raise CodegenError("The parent of state '%s' could not be indexed." % s.name)
            parent_idx = index_of[s.parent]
        ir.states.append(IrState(
            index=i,
            model_id=s.id,
            name=s.name,
            kind=_KIND_MAP[s.kind],
            parent=parent_idx,
            depth=sm.depth(s.id),
            # THE LINE-BREAK MARKER IS TURNED INTO A REAL NEWLINE.
            # The marker is not valid outside a string in C; written as is, the
            # generated code would not compile. What is inside a string is left
            # alone (see core/text_layout).
            entry=expand_breaks(s.entry).strip(),
            exit=expand_breaks(s.exit).strip(),
            do=expand_breaks(s.do).strip(),
            note=s.note.strip(),
        ))

    ir.max_depth = max([st.depth for st in ir.states] or [0]) + 1

    # --- 2) Event table --------------------------------------------------------
    ir.events = ["COMPLETION"] + sm.events()
    if len(ir.events) - 1 > MAX_EVENTS:
        raise CodegenError(
            "This model declares %d event types; at most %d are supported."
            % (len(ir.events) - 1, MAX_EVENTS))
    event_index = {name: i for i, name in enumerate(ir.events)}

    # The defer lists are converted into event indices.
    for i, s_ in enumerate(vertices):
        indeksler = []
        for ad in (s_.deferred or []):
            ad = str(ad).strip()
            if not ad:
                continue
            if ad not in event_index:
                raise CodegenError(
                    "State '%s' defers an unknown event: %s" % (s_.name, ad))
            if event_index[ad] not in indeksler:
                indeksler.append(event_index[ad])
        ir.states[i].deferred = sorted(indeksler)

    # THE DEFER MASK IS 32 BITS WIDE.
    #
    # The mask is a SINGLE uint32 field per state in the generated code. A
    # wider event table would need a byte matrix, which on an embedded
    # target means STATE_COUNT x EVENT_COUNT bytes. The limit is stated
    # EXPLICITLY -- rejected rather than silently producing a wrong mask.
    if any(st.deferred for st in ir.states) and len(ir.events) > 32:
        raise CodegenError(
            "Deferred events are supported for up to 32 event types; this "
            "model has %d." % (len(ir.events) - 1))

    # --- 2b) REGION TABLE ------------------------------------------------------
    #
    # Regions are numbered GLOBALLY. The order is DETERMINISTIC and also
    # fixes the processing order at run time: the root regions first, then
    # the regions of each composite state, in state index order. UML does
    # NOT define the order in which orthogonal regions are processed
    # (14.2.3.8.3); the tool fixes that order and writes it in the generated
    # header, so two generations give the same behaviour.
    for _ in range(sm.region_count(None)):
        ir.root_regions.append(len(ir.regions))
        ir.regions.append(IrRegion(index=len(ir.regions), owner=NONE,
                                   name="(root)"))
    for st in ir.states:
        if st.kind == KIND_COMPOSITE:
            st.first_region = len(ir.regions)
            st.region_count = sm.region_count(st.model_id)
            for r in range(st.region_count):
                ir.regions.append(IrRegion(index=len(ir.regions),
                                           owner=st.index,
                                           name="%s.%d" % (st.name, r)))
    if len(ir.regions) > MAX_REGIONS:
        raise CodegenError("This model has %d regions; at most %d are supported."
                           % (len(ir.regions), MAX_REGIONS))

    # Every vertex carries the GLOBAL index of the region it sits in.
    for i, s in enumerate(vertices):
        st = ir.states[i]
        if s.parent is None:
            st.region = ir.root_regions[min(sm.region_of(s.id),
                                            len(ir.root_regions) - 1)]
            continue
        ust = ir.states[index_of[s.parent]]
        if ust.region_count <= 0:
            raise CodegenError(
                "State '%s' is inside '%s', which owns no region; only a "
                "composite state can contain states." % (st.name, ust.name))
        st.region = ust.first_region + min(sm.region_of(s.id),
                                           ust.region_count - 1)

    # --- 3) Fold the initial transitions into tables ---------------------------
    def compile_initial(region: Optional[str],
                        region_index: int = 0) -> Tuple[int, int]:
        init = sm.initial_of(region, region_index)
        if init is None:
            return NONE, -1
        outs = sm.outgoing(init.id)
        if not outs:
            return NONE, -1
        tr = outs[0]
        if tr.target not in index_of:
            raise CodegenError("The target of the initial transition could not be resolved.")
        tgt = sm.states.get(tr.target)
        if tgt is not None and (tgt.kind.is_history or tgt.kind is StateKind.TERMINATE):
            # The validator prevents this with V054; a defensive check.
            raise CodegenError(
                "An initial transition cannot target a history or terminate pseudostate.")
        return index_of[tr.target], _dedup_add(ir.actions,
                                              expand_breaks(tr.action))

    # EVERY REGION has its own default entry (14.2.3.2, printed p.307).
    for reg in ir.regions:
        if reg.owner == NONE:
            sahip_id = None
            yerel = reg.index - ir.root_regions[0]
        else:
            sahip = ir.states[reg.owner]
            sahip_id = sahip.model_id
            yerel = reg.index - sahip.first_region
        cocuk, eylem = compile_initial(sahip_id, yerel)
        if cocuk == NONE:
            if sahip_id is None:
                raise CodegenError("The root region has no initial pseudostate.")
            raise CodegenError(
                "Region %d of composite state '%s' has no initial pseudostate."
                % (yerel + 1, ir.states[reg.owner].name))
        reg.initial_state = cocuk
        reg.initial_action = eylem

    # The old fields point at the FIRST region; for single-region models
    # that is exactly the previous behaviour.
    for st in ir.states:
        if st.kind == KIND_COMPOSITE and st.region_count > 0:
            ilk = ir.regions[st.first_region]
            st.initial_child = ilk.initial_state
            st.initial_action = ilk.initial_action

    # --- 3b) Default targets of the history pseudostates -----------------------
    for st in ir.states:
        if st.kind in (KIND_HIST_SHALLOW, KIND_HIST_DEEP):
            if st.parent == NONE:
                raise CodegenError(
                    "History pseudostate '%s' must live inside a composite state." % st.name)
            outs = sm.outgoing(st.model_id)
            if outs:
                if outs[0].target not in index_of:
                    raise CodegenError(
                        "The default transition target of history '%s' could not be resolved." % st.name)
                st.history_default = index_of[outs[0].target]

    # --- 4) Flattening the junction chains -------------------------------------
    # UML 2.5.1, 14.2.3.4.4: a junction is a *static* branch -- its guards are
    # evaluated BEFORE the compound transition runs. A choice is dynamic (the
    # guards are examined AFTER the incoming transition's effect). Rather than
    # carrying that difference at run time, junction paths are flattened here:
    # each path becomes one transition whose guards are ANDed together. That
    # way the engine's "check the guard, then run" flow gives the right
    # semantics, and Python / C / C++ agree by construction.
    junction_ids = {s.id for s in sm.states.values()
                    if s.kind is StateKind.JUNCTION}

    def _branch_guard(text: str) -> str:
        guard = expand_breaks(text).strip()
        return "" if guard.lower() == "else" else guard

    def _walk(target: str, guards: List[str], actions: List[str],
              depth: int, seen: frozenset):
        """Resolves a junction target to real targets (depth first).

        A path that cannot be resolved must not be dropped SILENTLY: dropping
        it would take the transition the user drew out of the IR, and the event
        would be ignored without warning or the machine would go to the wrong
        state. So a cycle or a depth overrun raises CodegenError (the validator
        """
        if target not in junction_ids:
            yield target, guards, actions
            return
        if target in seen:
            raise CodegenError(
                "The junction chain through '%s' loops; it never reaches "
                "a state." % sm.states[target].name)
        if depth >= MAX_JUNCTION_DEPTH:
            raise CodegenError(
                "The junction chain through '%s' is %d steps deep; at most "
                "%d are supported." % (sm.states[target].name, depth + 1,
                                       MAX_JUNCTION_DEPTH))
        for branch in sm.outgoing(target):
            yield from _walk(branch.target,
                             guards + [_branch_guard(branch.guard)],
                             actions + [expand_breaks(branch.action).strip()],
                             depth + 1, seen | {target})

    def _combine_guard(parts: List[str]) -> str:
        kept = [p for p in parts if p]
        if not kept:
            return ""
        if len(kept) == 1:
            return kept[0]
        return " && ".join("(%s)" % p for p in kept)

    def _combine_action(parts: List[str]) -> str:
        """Merges the effects along the path into a single body.

        Every part is terminated SEPARATELY. The tool allows an effect without
        a semicolon (the generators append one); pasting the parts raw under
        each other would merge 'cnt++' and 'hits++;' and the generated code
        would not compile.
        """
        return "\n".join(as_statement(p) for p in parts if p.strip())

    # --- 5) Transition table (grouped by source state order) -------------------
    initial_ids = {s.id for s in sm.states.values() if s.kind is StateKind.INITIAL}
    history_ids = {s.id for s in sm.states.values() if s.kind.is_history}
    fork_ids = {s.id for s in sm.states.values() if s.kind is StateKind.FORK}
    join_ids = {s.id for s in sm.states.values() if s.kind is StateKind.JOIN}

    def _ortak_sahip(idler: List[str]) -> Optional[str]:
        """The COMMON orthogonal owner of the given vertices (None if any)."""
        if not idler:
            return None
        ortak = idler[0]
        for baska in idler[1:]:
            ortak = sm.lca(ortak, baska)
            if ortak is None:
                return None
        return ortak

    entry_ids = {s.id for s in sm.states.values()
                 if s.kind is StateKind.ENTRY_POINT}
    exit_ids = {s.id for s in sm.states.values()
                if s.kind is StateKind.EXIT_POINT}

    def _entry_cozumle(entry_id: str):
        """Resolves an entry point into (owner, inner targets, segments).

        UML 2.5.1, 14.2.3.7 (printed p.313) NOTE: "If multiple Regions are
        involved, the entry point acts as a fork Pseudostate." So an entry
        point is compiled EXACTLY like a fork; using the same machinery for
        the single-region case beats keeping two separate code paths.
        """
        nokta = sm.states[entry_id]
        sahip = nokta.parent
        if sahip is None or sahip not in index_of:
            raise CodegenError(
                "The entry point '%s' is not owned by a composite state."
                % nokta.name)
        segmentler = sm.outgoing(entry_id)
        if not segmentler:
            raise CodegenError(
                "The entry point '%s' has no transition into the state."
                % nokta.name)
        hedefler = [t.target for t in segmentler]
        if any(h not in index_of for h in hedefler):
            raise CodegenError(
                "A transition leaving entry point '%s' could not be resolved."
                % nokta.name)
        sirali = sorted(hedefler, key=lambda h: sm.region_of(h))
        return sahip, [index_of[h] for h in sirali], segmentler

    def _exit_cozumle(exit_id: str):
        """Returns the outgoing transition of an exit point."""
        nokta = sm.states[exit_id]
        cikislar = sm.outgoing(exit_id)
        if not cikislar:
            raise CodegenError(
                "The exit point '%s' has no transition out of the state."
                % nokta.name)
        cikis = cikislar[0]
        if cikis.target not in index_of:
            raise CodegenError(
                "The target of the transition leaving exit point '%s' could "
                "not be resolved." % nokta.name)
        return cikis

    def _fork_cozumle(fork_id: str):
        """Resolves the fork segments into (owner, target list)."""
        segmentler = sm.outgoing(fork_id)
        hedefler = [t.target for t in segmentler]
        sahip = _ortak_sahip(hedefler)
        if sahip is None or sahip not in index_of:
            raise CodegenError(
                "The fork '%s' does not target vertices inside one "
                "orthogonal state." % sm.states[fork_id].name)
        gecersiz = [h for h in hedefler if h not in index_of]
        if gecersiz:
            raise CodegenError(
                "A fork segment of '%s' could not be resolved."
                % sm.states[fork_id].name)

        def _bolge_sirasi(hedef: str) -> int:
            """The region the target falls into, under `owner`."""
            cur = hedef
            adim = 0
            while cur is not None and adim <= len(sm.states) + 1:
                st_ = sm.states.get(cur)
                if st_ is None:
                    return 0
                if st_.parent == sahip:
                    return sm.region_of(cur)
                cur = st_.parent
                adim += 1
            return 0

        # Targets are entered in REGION order. Left to the arrow order in the
        # model file, the same diagram could produce two different entry orders
        # and code generation would not be DETERMINISTIC.
        sirali = sorted(hedefler, key=_bolge_sirasi)
        return sahip, [index_of[h] for h in sirali], segmentler

    def _join_cozumle(join_id: str):
        """Resolves join segments into (owner, source list, exit transition)."""
        segmentler = [t for t in sm.transitions.values() if t.target == join_id]
        kaynaklar = [t.source for t in segmentler]
        sahip = _ortak_sahip(kaynaklar)
        if sahip is None or sahip not in index_of:
            raise CodegenError(
                "The join '%s' does not collect vertices from inside one "
                "orthogonal state." % sm.states[join_id].name)
        cikislar = sm.outgoing(join_id)
        if not cikislar:
            raise CodegenError(
                "The join '%s' has no outgoing transition."
                % sm.states[join_id].name)
        gecersiz = [k for k in kaynaklar if k not in index_of]
        if gecersiz:
            raise CodegenError(
                "A join segment of '%s' could not be resolved."
                % sm.states[join_id].name)
        sirali = sorted(kaynaklar, key=lambda k: index_of[k])
        return sahip, [index_of[k] for k in sirali], cikislar[0]
    idx = 0
    for st in ir.states:
        first = idx
        for tr in sm.outgoing(st.model_id):
            if tr.source in initial_ids:
                continue                      # initial transitions are already folded in
            if tr.source in history_ids:
                continue                      # history defaults do not enter the table
            if tr.target in join_ids:
                continue                      # join segment: produced at the exit

            # EXIT POINT: the inner arrow targets the point on the border.
            # UML 2.5.1, 14.2.3.7 (printed p.313): "Transitions terminating
            # on an exit point ... implies exiting of this composite State".
            # When compiling, the arrow is bound to the OUTER target of the point;
            # the LCA calculation already provides the exit from the composite state.
            if tr.target in exit_ids:
                cikis = _exit_cozumle(tr.target)
                ev_name = tr.event.strip()
                if ev_name and ev_name not in event_index:
                    raise CodegenError("Unknown event: %s" % ev_name)
                ir.transitions.append(IrTransition(
                    index=idx,
                    model_id=tr.id,
                    source=st.index,
                    target=index_of[cikis.target],
                    event=event_index[ev_name] if ev_name else 0,
                    guard=_dedup_add(ir.guards, _branch_guard(tr.guard)),
                    action=_dedup_add(ir.actions, _combine_action(
                        [expand_breaks(tr.action).strip(),
                         expand_breaks(cikis.action).strip()])),
                    kind=TKIND_EXTERNAL,
                    text="%s --> exit %s --> %s"
                         % (st.name, sm.states[tr.target].name,
                            sm.states[cikis.target].name),
                ))
                idx += 1
                continue

            # ENTRY POINT: compiled like a fork (the spec's own NOTE).
            if tr.target in entry_ids:
                sahip, hedefler, segmentler = _entry_cozumle(tr.target)
                ev_name = tr.event.strip()
                if ev_name and ev_name not in event_index:
                    raise CodegenError("Unknown event: %s" % ev_name)
                eylemler = [expand_breaks(tr.action).strip()]
                eylemler += [expand_breaks(x.action).strip() for x in segmentler]
                ir.transitions.append(IrTransition(
                    index=idx,
                    model_id=tr.id,
                    source=st.index,
                    target=index_of[sahip],
                    event=event_index[ev_name] if ev_name else 0,
                    guard=_dedup_add(ir.guards, _branch_guard(tr.guard)),
                    action=_dedup_add(ir.actions, _combine_action(eylemler)),
                    kind=_TKIND_MAP[tr.kind],
                    fork_targets=hedefler,
                    text="%s --> entry %s" % (st.name,
                                              sm.states[tr.target].name),
                ))
                idx += 1
                continue

            # FORK: a single row is produced. The target is the COMMON orthogonal
            # owner of the segments; the segment targets travel as `fork_targets`
            # and are entered EXPLICITLY in their regions at run time. Unnamed
            # regions start at their defaults.
            if tr.target in fork_ids:
                sahip, hedefler, segmentler = _fork_cozumle(tr.target)
                ev_name = tr.event.strip()
                if ev_name and ev_name not in event_index:
                    raise CodegenError("Unknown event: %s" % ev_name)
                eylemler = [expand_breaks(tr.action).strip()]
                eylemler += [expand_breaks(x.action).strip() for x in segmentler]
                ir.transitions.append(IrTransition(
                    index=idx,
                    model_id=tr.id,
                    source=st.index,
                    target=index_of[sahip],
                    event=event_index[ev_name] if ev_name else 0,
                    guard=_dedup_add(ir.guards, _branch_guard(tr.guard)),
                    action=_dedup_add(ir.actions, _combine_action(eylemler)),
                    kind=_TKIND_MAP[tr.kind],
                    fork_targets=hedefler,
                    text="%s --> fork %s" % (st.name,
                                             sm.states[tr.target].name),
                ))
                idx += 1
                continue

            if tr.target not in index_of:
                raise CodegenError("The target of a transition leaving '%s' could not be resolved." % st.name)
            ev_name = tr.event.strip()
            if ev_name and ev_name not in event_index:
                raise CodegenError("Unknown event: %s" % ev_name)

            paths = list(_walk(tr.target, [_branch_guard(tr.guard)],
                               [expand_breaks(tr.action).strip()], 0, frozenset()))
            if len(paths) > MAX_JUNCTION_PATHS:
                raise CodegenError(
                    "The junction chain of a transition leaving '%s' opens "
                    "into %d paths; at most %d are supported."
                    % (st.name, len(paths), MAX_JUNCTION_PATHS))

            for final_target, guard_parts, action_parts in paths:
                if final_target not in index_of:
                    raise CodegenError(
                        "The target of a transition leaving '%s' could not "
                        "be resolved." % st.name)
                ir.transitions.append(IrTransition(
                    index=idx,
                    model_id=tr.id,
                    source=st.index,
                    target=index_of[final_target],
                    event=event_index[ev_name] if ev_name else 0,
                    guard=_dedup_add(ir.guards, _combine_guard(guard_parts)),
                    action=_dedup_add(ir.actions, _combine_action(action_parts)),
                    kind=_TKIND_MAP[tr.kind],
                    text="%s --> %s : %s" % (st.name,
                                             sm.states[final_target].name,
                                             tr.label() or "(completion)"),
                ))
                idx += 1
        # JOIN: the exit transition is added to the rows of the orthogonal OWNER.
        # That way a search walking up from any of the regions finds it; the
        # enabling condition is that all sources are active AT THE SAME TIME
        # (14.2.3.7: "all incoming Transitions have to complete before execution
        # can continue through an outgoing Transition").
        for join_id in sorted(join_ids):
            sahip, kaynaklar, cikis = _join_cozumle(join_id)
            if index_of.get(sahip) != st.index:
                continue
            if cikis.target not in index_of:
                raise CodegenError(
                    "The target of the transition leaving join '%s' could "
                    "not be resolved." % sm.states[join_id].name)
            segment_eylemleri = [
                expand_breaks(t.action).strip()
                for t in sorted((x for x in sm.transitions.values()
                                 if x.target == join_id),
                                key=lambda x: x.id)]
            segment_eylemleri.append(expand_breaks(cikis.action).strip())
            ir.transitions.append(IrTransition(
                index=idx,
                model_id=cikis.id,
                source=st.index,
                target=index_of[cikis.target],
                event=0,                      # a join carries no trigger
                guard=-1,                     # a join carries no guard
                action=_dedup_add(ir.actions,
                                  _combine_action(segment_eylemleri)),
                kind=TKIND_EXTERNAL,
                join_sources=kaynaklar,
                text="join %s --> %s" % (sm.states[join_id].name,
                                         sm.states[cikis.target].name),
            ))
            idx += 1

        ir.tran_slice[st.index] = (first, idx - first)

    return ir
