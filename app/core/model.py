"""Data model: the UML state machine (hierarchical / HSM).

This module holds *data* only; it does not depend on Qt. That lets the code
generator and the tests run without a user interface.

Semantic basis: UML 2.5.1, clause 14 (StateMachines) - supported subset:
  * Simple state, Composite state (tek bolge / single region)
  * Initial pseudostate, Final state, Choice pseudostate
  * External / Local / Internal transitions
  * entry / exit / do behaviours
  * Completion (event-less) transitions
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
import re
from typing import Dict, List, Optional

from .text_layout import flatten, split_lines

SCHEMA_VERSION = 1


def new_id(prefix: str) -> str:
    return "%s_%s" % (prefix, uuid.uuid4().hex[:8])


class StateKind(str, Enum):
    """UML Vertex kinds (UML 2.5.1, 14.2.3.4 Pseudostates included)."""

    SIMPLE = "simple"                    # simple state
    COMPOSITE = "composite"              # composite state (has substates inside)
    INITIAL = "initial"                  # initial pseudostate
    FINAL = "final"                      # final state
    CHOICE = "choice"                    # choice pseudostate (dynamic branching)
    JUNCTION = "junction"                # junction pseudostate (static branching)
    SHALLOW_HISTORY = "shallow_history"  # H  - shallow history pseudostate
    DEEP_HISTORY = "deep_history"        # H* - deep history pseudostate
    TERMINATE = "terminate"              # terminate pseudostate (X)
    FORK = "fork"                        # fork: splits one arrow across regions
    JOIN = "join"                        # join: merges what arrives from regions
    ENTRY_POINT = "entry_point"          # NAMED entry of a composite state
    EXIT_POINT = "exit_point"            # NAMED exit of a composite state
    SUBMACHINE = "submachine"            # a REFERENCE to another machine

    @property
    def is_pseudo(self) -> bool:
        return self in (StateKind.INITIAL, StateKind.CHOICE, StateKind.JUNCTION,
                        StateKind.SHALLOW_HISTORY, StateKind.DEEP_HISTORY,
                        StateKind.TERMINATE, StateKind.FORK, StateKind.JOIN,
                        StateKind.ENTRY_POINT, StateKind.EXIT_POINT)

    @property
    def is_sync(self) -> bool:
        """A fork/join pseudostate that spreads across regions?

        UML 2.5.1, 14.2.3.7 (printed p.313): a fork splits "an incoming
        Transition into two or more Transitions terminating on Vertices in
        orthogonal Regions"; a join is the shared target for "two or more
        Transitions originating from Vertices in different orthogonal Regions".
        """
        return self in (StateKind.FORK, StateKind.JOIN)

    @property
    def is_connection_point(self) -> bool:
        """A named entry/exit point ON THE BORDER of a composite state?

        UML 2.5.1, 14.2.3.7 (printed p.313): an entryPoint "represents an entry
        point for a StateMachine or a composite State that provides
        encapsulation of the insides of the State or StateMachine";
        an exitPoint is its counterpart on the way out. Both hide the inside
        from the outside: an arrow from outside targets the point ON THE
        BORDER, not an inner vertex.
        """
        return self in (StateKind.ENTRY_POINT, StateKind.EXIT_POINT)

    @property
    def is_history(self) -> bool:
        return self in (StateKind.SHALLOW_HISTORY, StateKind.DEEP_HISTORY)

    @property
    def is_branch(self) -> bool:
        """A guarded branching pseudostate (choice/junction)?"""
        return self in (StateKind.CHOICE, StateKind.JUNCTION)

    @property
    def is_submachine(self) -> bool:
        return self is StateKind.SUBMACHINE

    @property
    def is_real_state(self) -> bool:
        """A state the generated code can "stay in"?

        SUBMACHINE belongs here too: in UML a submachine state is a real
        state, it can carry entry/exit/do and can be stayed in (14.2.3.4.7).
        Once expanded it becomes an ordinary composite state.
        """
        return self in (StateKind.SIMPLE, StateKind.COMPOSITE,
                        StateKind.FINAL, StateKind.SUBMACHINE)


class TransitionKind(str, Enum):
    EXTERNAL = "external"  # the source state is exited, the target entered
    INTERNAL = "internal"  # the state does not change, only the effect runs
    LOCAL = "local"        # the source composite state is not exited


#: TIME EVENT form: ``after(<expression>)``.
#:
#: In UML 2.5.1 a TimeEvent is a kind of Trigger (clause 13) and state
#: machine transitions use it as one. This tool supports only the
#: RELATIVE form (``after``): absolute time (``at``) needs a calendar
#: clock on an embedded target, and half support is not offered.
TIME_EVENT_RE = re.compile(r"^\s*after\s*\((?P<delay>.*)\)\s*$",
                           re.IGNORECASE)


def time_event_delay(name: str) -> Optional[str]:
    """Returns the delay EXPRESSION for ``after(N)``, otherwise None."""
    m = TIME_EVENT_RE.match(name or "")
    if m is None:
        return None
    return m.group("delay").strip()


def is_time_event(name: str) -> bool:
    return time_event_delay(name) is not None


# --------------------------------------------------------------------------- #
#   Elements
# --------------------------------------------------------------------------- #

@dataclass
class State:
    """A state / pseudostate vertex."""

    id: str = field(default_factory=lambda: new_id("s"))
    name: str = "State"
    kind: StateKind = StateKind.SIMPLE
    parent: Optional[str] = None          # id of the parent composite state (None = root)

    # -- REGIONS (UML 2.5.1, 14.2.3.2 Region) ------------------------------- #
    #
    # A composite state owns ONE OR MORE regions. A state with several
    # regions is ORTHOGONAL: its regions are active at the same time.
    # The root (StateMachine) owns a region too; today it has exactly one.
    #
    # Older files do not carry these fields; the defaults (1 and 0) give
    # exactly today's behaviour, so no file upgrade is needed.
    #
    #: How many regions this state owns (meaningful for COMPOSITE only).
    regions: int = 1
    #: Which region of its PARENT this vertex sits in (0-based).
    region: int = 0

    #: DEFERRED EVENT types (UML deferrableTrigger).
    #:
    #: UML 2.5.1, 14.2.3.4.4 (basili s.309): "A State may specify a set of
    #: Event types that may be deferred in that State ... these Event
    #: occurrences remain in the event pool until: a state configuration is
    #: reached where these Event types are no longer deferred or, if a
    #: deferred Event type is used explicitly in a Trigger of a Transition
    #: whose source is the deferring State (i.e., a kind of override
    #: option)."
    deferred: List[str] = field(default_factory=list)

    #: SUBMACHINE reference: a file path relative to the workspace.
    #:
    #: UML 2.5.1, 14.2.3.4.7 (basili s.311): altmakineler "like programming
    #: language macros, distinct Behavior specifications, which may be
    #: defined in a different context than the one where they are used".
    #: So the reference points to ANOTHER FILE and is substituted before
    #: code generation (see app/core/submachine.py).
    submachine_ref: str = ""

    # Behaviours (the C/C++ expressions written by the user)
    entry: str = ""
    exit: str = ""
    do: str = ""

    # Appearance
    x: float = 0.0
    y: float = 0.0
    w: float = 170.0
    h: float = 84.0

    note: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kind"] = self.kind.value
        return d

    @staticmethod
    def from_dict(d: dict) -> "State":
        d = dict(d)
        d["kind"] = StateKind(d.get("kind", "simple"))
        # The list fields are COPIED: if the list loaded from file were shared,
        # two states would show the same list and a change made in one would
        # appear in the other as well.
        d["deferred"] = [str(x) for x in (d.get("deferred") or [])]
        allowed = set(State.__dataclass_fields__)
        return State(**{k: v for k, v in d.items() if k in allowed})


@dataclass
class Transition:
    """A transition between two vertices:  event [guard] / action"""

    id: str = field(default_factory=lambda: new_id("t"))
    source: str = ""
    target: str = ""
    event: str = ""     # empty => completion (event-less) transition
    guard: str = ""     # C/C++ boolean expression
    action: str = ""    # C/C++ statement(s)
    kind: TransitionKind = TransitionKind.EXTERNAL
    priority: int = 0   # a smaller number is tried first

    # Appearance
    waypoints: List[List[float]] = field(default_factory=list)
    label_dx: float = 0.0
    label_dy: float = -16.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kind"] = self.kind.value
        return d

    @staticmethod
    def from_dict(d: dict) -> "Transition":
        d = dict(d)
        d["kind"] = TransitionKind(d.get("kind", "external"))
        d["waypoints"] = [list(p) for p in d.get("waypoints", [])]
        allowed = set(Transition.__dataclass_fields__)
        return Transition(**{k: v for k, v in d.items() if k in allowed})

    def _raw_label(self) -> str:
        """Builds the label WITH the line breaks PRESERVED."""
        txt = self.event.strip()
        if self.guard.strip():
            txt = (txt + " " if txt else "") + "[" + self.guard.strip() + "]"
        if self.action.strip():
            act = self.action.strip().rstrip(";")
            txt = (txt + " / " + act) if txt else ("/ " + act)
        return txt

    def label(self) -> str:
        """The UML label -- a SINGLE line.

        The PlantUML output, the workspace tree and the property preview
        CANNOT carry a line break; this is what they use. Line-break markers
        (see core/text_layout) collapse to a space.
        """
        return flatten(self._raw_label())

    def label_lines(self) -> List[str]:
        """The display lines of the label -- for the canvas.

        The user splits the label over several lines by typing the
        line-break marker (text_layout.LINE_BREAK_MARKER) into the event /
        guard / effect fields.
        """
        return split_lines(self._raw_label())


# --------------------------------------------------------------------------- #
#  Dokuman
#  Document

@dataclass
class StateMachine:
    """The document representing the whole diagram."""

    name: str = "Blinky"
    prefix: str = "blinky"          # prefix of the generated C symbols
    description: str = ""
    context_type: str = "void"      # user context type; "void" => void *ctx
    user_includes: str = ""         # #include lines copied verbatim into the header
    states: Dict[str, State] = field(default_factory=dict)
    transitions: Dict[str, Transition] = field(default_factory=dict)

    # -- accessors ---------------------------------------------------------- #

    def add_state(self, st: State) -> State:
        self.states[st.id] = st
        return st

    def add_transition(self, tr: Transition) -> Transition:
        self.transitions[tr.id] = tr
        return tr

    def remove_state(self, sid: str) -> None:
        """Deletes the state, its whole subtree and the related transitions."""
        for cid in [c.id for c in self.children(sid)]:
            self.remove_state(cid)
        for tid in [t.id for t in self.transitions.values()
                    if t.source == sid or t.target == sid]:
            self.transitions.pop(tid, None)
        self.states.pop(sid, None)

    def remove_transition(self, tid: str) -> None:
        self.transitions.pop(tid, None)

    def children(self, sid: Optional[str]) -> List[State]:
        return [s for s in self.states.values() if s.parent == sid]

    def sorted_children(self, sid: Optional[str]) -> List[State]:
        return sorted(self.children(sid), key=lambda s: (round(s.y, 3), round(s.x, 3), s.name))

    # -- regions ------------------------------------------------------------ #

    def region_count(self, sid: Optional[str]) -> int:
        """The region count of the given owner (None for the root).

        The root currently carries ONE region; UML allows several top-level
        regions, but this tool's generator has no counterpart for that and
        half support is not offered (see test_uml_conformance, section 11).
        """
        if sid is None:
            return 1
        st = self.states.get(sid)
        if st is None or st.kind is not StateKind.COMPOSITE:
            return 1
        return max(1, int(st.regions))

    def is_orthogonal(self, sid: Optional[str]) -> bool:
        """Does the owner carry MORE THAN ONE region?"""
        return self.region_count(sid) > 1

    def region_of(self, sid: str) -> int:
        """The index of the region the vertex sits in, within its parent."""
        st = self.states.get(sid)
        if st is None:
            return 0
        return max(0, min(int(st.region), self.region_count(st.parent) - 1))

    def children_in(self, sid: Optional[str], region: int) -> List[State]:
        """The children in a SPECIFIC region of the owner, in stable order."""
        return [s for s in self.sorted_children(sid)
                if self.region_of(s.id) == region]

    def roots(self) -> List[State]:
        return self.children(None)

    def parent_of(self, sid: str) -> Optional[State]:
        st = self.states.get(sid)
        if st is None or st.parent is None:
            return None
        return self.states.get(st.parent)

    def ancestors(self, sid: str) -> List[State]:
        """The parent states, from the nearest one up to the root."""
        out: List[State] = []
        cur = self.parent_of(sid)
        seen = set()
        while cur is not None and cur.id not in seen:
            seen.add(cur.id)
            out.append(cur)
            cur = self.parent_of(cur.id)
        return out

    def depth(self, sid: str) -> int:
        return len(self.ancestors(sid))

    def max_depth(self) -> int:
        if not self.states:
            return 1
        return max(self.depth(s) for s in self.states) + 1

    def is_descendant(self, sid: str, maybe_ancestor: str) -> bool:
        return any(a.id == maybe_ancestor for a in self.ancestors(sid))

    def lca(self, a: str, b: str) -> Optional[str]:
        """The nearest common ancestor (None = the root region)."""
        chain_a = [a] + [s.id for s in self.ancestors(a)]
        chain_b = set([b] + [s.id for s in self.ancestors(b)])
        for x in chain_a:
            if x in chain_b:
                return x
        return None

    def outgoing(self, sid: str) -> List[Transition]:
        """The outgoing transitions, in the order they will be tried.

        Unguarded / 'else' branches ALWAYS go last -- the priority the user
        gave cannot change that. UML 2.5.1 14.2.3.4.6: 'else' may only be
        taken when every other guard is false. Priority orders the GUARDED
        branches among themselves; otherwise giving the 'else' branch a low
        priority would make every guarded branch unreachable.
        tamamini ulasilamaz kilardi.
        """
        def key(t: Transition):
            guard = t.guard.strip()
            unguarded = (not guard) or (guard.lower() == "else")
            return (1 if unguarded else 0, t.priority, t.id)
        return sorted((t for t in self.transitions.values() if t.source == sid),
                      key=key)

    def incoming(self, sid: str) -> List[Transition]:
        return [t for t in self.transitions.values() if t.target == sid]

    def initial_of(self, parent: Optional[str],
                   region: int = 0) -> Optional[State]:
        """The initial pseudostate in a SPECIFIC region of the given owner.

        UML 2.5.1, 14.2.3.2 (printed p.307): every region has its own default
        entry point. In an orthogonal state there is a SEPARATE initial per
        region; for single-region states the behaviour is unchanged.
        """
        for s in self.children_in(parent, region):
            if s.kind is StateKind.INITIAL:
                return s
        return None

    def events(self) -> List[str]:
        """Every event name used in the model (alphabetical, unique)."""
        evs = set()
        for t in self.transitions.values():
            if t.event.strip():
                evs.add(t.event.strip())
        # DEFERRED event types go into the table too: even when no transition
        # is triggered by one, the machine must KNOW it and hold it in the pool.
        for st in self.states.values():
            for ad in (st.deferred or []):
                if str(ad).strip():
                    evs.add(str(ad).strip())
        return sorted(evs)

    def ordered_states(self) -> List[State]:
        """Stable (deterministic) ordering: depth-first from the root.

        States whose parent cannot be found, or that sit in a circular
        hierarchy, are unreachable by this walk; they are still appended AT
        THE END. Otherwise saving (``to_dict``) and canvas redraws would drop
        them silently, and the user would not notice losing part of the model.
        The validator reports these as errors, V020/V021.
        """
        out: List[State] = []
        seen: set = set()

        def walk(parent: Optional[str]) -> None:
            for s in self.sorted_children(parent):
                if s.id in seen:
                    continue
                seen.add(s.id)
                out.append(s)
                walk(s.id)

        walk(None)
        orphans = [s for s in self.states.values() if s.id not in seen]
        out.extend(sorted(orphans,
                          key=lambda s: (round(s.y, 3), round(s.x, 3), s.name)))
        return out

    def ordered_transitions(self) -> List[Transition]:
        """Stable ordering: by source state order, then by priority."""
        order = {s.id: i for i, s in enumerate(self.ordered_states())}
        return sorted(self.transitions.values(),
                      key=lambda t: (order.get(t.source, 1 << 30), t.priority, t.id))

    # -- serialisation ------------------------------------------------------- #

    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA_VERSION,
            "type": "state_machine",
            "name": self.name,
            "prefix": self.prefix,
            "description": self.description,
            "context_type": self.context_type,
            "user_includes": self.user_includes,
            "states": [s.to_dict() for s in self.ordered_states()],
            "transitions": [t.to_dict() for t in self.ordered_transitions()],
        }

    @staticmethod
    def from_dict(d: dict) -> "StateMachine":
        sm = StateMachine(
            name=d.get("name", "StateMachine"),
            prefix=d.get("prefix", "sm"),
            description=d.get("description", ""),
            context_type=d.get("context_type", "void") or "void",
            user_includes=d.get("user_includes", ""),
        )
        # THE SAME ID IS NEVER OVERWRITTEN SILENTLY.
        #
        # Dictionary assignment let a second element arriving with the same
        # ``id`` destroy the first -- and everything beneath it -- without a
        # single message: in a model edited by hand, or merged from two files,
        # half the states vanished and the user only noticed by looking at the
        # diagram. Rejecting a corrupt file WITH A CLEAR MESSAGE is better than
        # opening it having quietly swallowed half of it.
        # acmaktan iyidir.
        for sd in d.get("states", []):
            st = State.from_dict(sd)
            if st.id in sm.states:
                raise ValueError(
                    "The model file lists two states with the same id %r "
                    "(%r and %r). Ids must be unique."
                    % (st.id, sm.states[st.id].name, st.name))
            sm.states[st.id] = st
        for td in d.get("transitions", []):
            tr = Transition.from_dict(td)
            if tr.id in sm.transitions:
                raise ValueError(
                    "The model file lists two transitions with the same id "
                    "%r. Ids must be unique." % tr.id)
            sm.transitions[tr.id] = tr
        return sm

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)

    @staticmethod
    def from_json(text: str) -> "StateMachine":
        return StateMachine.from_dict(json.loads(text))

    def clone(self) -> "StateMachine":
        return StateMachine.from_dict(json.loads(self.to_json()))

    def assign_from(self, other: "StateMachine") -> None:
        """Replaces the content while keeping the same object (undo/load)."""
        self.name = other.name
        self.prefix = other.prefix
        self.description = other.description
        self.context_type = other.context_type
        self.user_includes = other.user_includes
        self.states = other.states
        self.transitions = other.transitions
