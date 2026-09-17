"""Submachine states: inserting another machine like a MACRO.

UML 2.5.1, 14.2.3.4.7 (printed p.311):

    "A submachine State implies a macro-like insertion of the specification
     of the corresponding submachine StateMachine."

The same clause says submachines are "like programming language macros,
distinct Behavior specifications, which may be defined in a different context
than the one where they are used". So the reference points to ANOTHER FILE
and is substituted BEFORE code generation.

WHY FLATTENING RATHER THAN GENERATING A SEPARATE MODULE
-------------------------------------------------------
Generating the submachine as a separate C module looks more economical at
first sight, but it has three concrete problems:

* Dispatch would become RECURSIVE: the `take` function of the outer machine
  calls the `dispatch` function of the inner one. Stack consumption becomes a
  function of THE MODEL and cannot be bounded statically on an embedded
  target -- which is the very guarantee this tool's whole design rests on.
* The priority rule is GLOBAL (14.2.3.9.4): a transition leaving a deep state
  competes with one leaving its container, and the deeper one wins. Two
  separate modules could only imitate that with a hand-built two-stage protocol.
* TWO REFERENCES to the same submachine are SEPARATE instances (14.2.3.4.7
  NOTE: "Each submachine State represents a distinct instantiation of a
  submachine"). Shared module state ties the two together; that is a bug that
  simply behaves wrongly in silence.

The cost of flattening is REAL: N references duplicate a K-vertex machine N
times. That cost is not hidden -- the vertex and transition counts after
expansion are written into the header of the generated file.

WHAT IS NOT SUPPORTED
---------------------
Named connection points ON a submachine state (ConnectionPointReference,
14.2.3.5) are NOT SUPPORTED for now and are REFUSED explicitly. Binding the
entry/exit points of the referenced machine to an outside arrow needs an
interface that shows those points on the border of the submachine state on the
canvas; half support would mean the arrow the user drew silently going
somewhere else. The specification also says this notation is NOT REQUIRED for
default entry and exit by completion (14.2.4.4.2, printed p.323).
(14.2.4.4.2, basili s.323).
"""

from __future__ import annotations

import copy
import os
from typing import Callable, Dict, List, Optional, Set

from .model import State, StateKind, StateMachine

#: Upper bound on the nesting depth of submachines.
#:
#: UML sets NO such limit; this is a TOOL RULE. Macro expansion does not
#: terminate on a cyclic reference graph, and every level multiplies the
#: vertex count. The limit exists to give the user a comprehensible error.
MAX_SUBMACHINE_DEPTH = 8


class SubmachineError(Exception):
    """A submachine reference could not be resolved."""


#: A resolver of the form `resolve(ref) -> StateMachine | None`.
Resolver = Callable[[str], Optional[StateMachine]]


def submachine_states(sm: StateMachine) -> List[State]:
    """The submachine states in a machine (in stable order)."""
    return [s for s in sm.ordered_states()
            if s.kind is StateKind.SUBMACHINE]


def has_submachine(sm: StateMachine) -> bool:
    return any(s.kind is StateKind.SUBMACHINE for s in sm.states.values())


def normalise_ref(ref: str) -> str:
    """Reduces a reference to a single comparable form."""
    return os.path.normcase(os.path.normpath((ref or "").strip()))


def qualified_name(outward: str, inward: str) -> str:
    """The name of the expanded vertex.

    Because the name is part of the generated C identifier, it must stay a
    VALID IDENTIFIER; that is why an underscore is used instead of the "::"
    separator of the specification notation. Two references to the same
    submachine therefore produce different names, and the V010 name-collision
    rule can tell them apart.
    """
    return "%s_%s" % (outward, inward)


def flatten(sm: StateMachine, resolve: Resolver,
            _depth: int = 0,
            _stack: Optional[Set[str]] = None) -> StateMachine:
    """SUBSTITUTES the submachine references; returns a new machine.

    The input IS NOT CHANGED: the caller keeps the user's document, while
    generation works on the expanded copy.
    """
    if not has_submachine(sm):
        return sm
    if _depth >= MAX_SUBMACHINE_DEPTH:
        raise SubmachineError(
            "Submachine references are nested more than %d levels deep."
            % MAX_SUBMACHINE_DEPTH)

    stack = set(_stack or set())
    result = copy.deepcopy(sm)

    for outer in submachine_states(sm):
        ref = (outer.submachine_ref or "").strip()
        if not ref:
            raise SubmachineError(
                "The submachine state '%s' does not reference a machine."
                % outer.name)
        key = normalise_ref(ref)
        if key in stack:
            raise SubmachineError(
                "The submachine reference of '%s' is circular: '%s' is "
                "already being expanded." % (outer.name, ref))

        inner = resolve(ref)
        if inner is None:
            raise SubmachineError(
                "The machine referenced by '%s' could not be found: %s"
                % (outer.name, ref))
        inner = flatten(inner, resolve, _depth + 1, stack | {key})

        _substitute(result, outer.id, inner)

    return result


def _substitute(dest: StateMachine, outer_id: str, inner: StateMachine) -> None:
    """Turns a submachine state into a COMPOSITE state filled with the
    content of the inner machine."""
    outer = dest.states[outer_id]
    outer_name = outer.name

    # The outer vertex is now an ordinary composite state. entry/exit/do are
    # KEPT: in UML a submachine state may carry them.
    outer.kind = StateKind.COMPOSITE
    outer.regions = max(1, inner.region_count(None))

    name_map: Dict[str, str] = {}
    for s in inner.ordered_states():
        new = copy.deepcopy(s)
        new.id = "%s__%s" % (outer_id, s.id)
        new.name = qualified_name(outer_name, s.name)
        # The ROOT vertices of the inner machine become children of the outer state.
        new.parent = ("%s__%s" % (outer_id, s.parent)) if s.parent else outer_id
        # The position is moved inside the outer state, so it looks sane on the canvas.
        new.x = float(s.x) + 14.0
        new.y = float(s.y) + 34.0
        name_map[s.id] = new.id
        dest.add_state(new)

    for t in inner.ordered_transitions():
        new = copy.deepcopy(t)
        new.id = "%s__%s" % (outer_id, t.id)
        new.source = name_map.get(t.source, t.source)
        new.target = name_map.get(t.target, t.target)
        dest.add_transition(new)

    _merge_includes(dest, inner)


def _merge_includes(target: StateMachine, inner: StateMachine) -> None:
    """APPENDS the include lines of the inner machine to the outer one.

    The entry / exit / do texts and guard expressions of the inner machine are
    copied VERBATIM. The functions those texts call are declared in the inner
    machine's OWN include lines; without carrying them over, the generated
    file calls functions that were never declared.

    This loss surfaced IN THE CUSTOMER'S COMPILER without the tool saying
    anything. Both of the user's diagrams generate perfectly on their own;
    only putting one inside the other breaks it.

    The same line is never written twice and the ORDER is preserved: the lines of
    the outer machine first, then the new ones from the inner machine.
    """
    rows = (target.user_includes or "").splitlines()
    seen = {ln.strip() for ln in rows if ln.strip()}
    added = []
    for ln in (inner.user_includes or "").splitlines():
        key = ln.strip()
        if key and key not in seen:
            seen.add(key)
            added.append(ln)
    if added:
        target.user_includes = "\n".join(rows + added).strip("\n")


def workspace_resolver(ws, opened: Optional[Dict[str, StateMachine]] = None
                       ) -> Resolver:
    """Builds a resolver that reads the files in the workspace.

    OPEN DOCUMENTS WIN. If the user has CHANGED the referenced machine in
    another tab and has not saved it yet, using the old copy on disk would
    make the generated code disagree with the diagram on screen. That is the
    hardest kind of bug to notice.
    """
    cache: Dict[str, StateMachine] = {}
    opened_map = {normalise_ref(k): v for k, v in (opened or {}).items()}

    def resolve_ref(ref: str) -> Optional[StateMachine]:
        key = normalise_ref(ref)
        if key in opened_map:
            return opened_map[key]
        if key in cache:
            return cache[key]
        if ws is None:
            return None
        try:
            fpath = ws.resolve(ref)
        except Exception:                       # noqa: BLE001
            return None
        if not os.path.isfile(fpath):
            return None
        try:
            with open(fpath, encoding="utf-8") as fh:
                machine = StateMachine.from_json(fh.read())
        except Exception:                       # noqa: BLE001
            return None
        cache[key] = machine
        return machine

    return resolve_ref
