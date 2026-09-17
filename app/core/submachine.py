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


def qualified_name(disari: str, iceri: str) -> str:
    """The name of the expanded vertex.

    Because the name is part of the generated C identifier, it must stay a
    VALID IDENTIFIER; that is why an underscore is used instead of the "::"
    separator of the specification notation. Two references to the same
    submachine therefore produce different names, and the V010 name-collision
    rule can tell them apart.
    """
    return "%s_%s" % (disari, iceri)


def flatten(sm: StateMachine, resolve: Resolver,
            _derinlik: int = 0,
            _yigin: Optional[Set[str]] = None) -> StateMachine:
    """SUBSTITUTES the submachine references; returns a new machine.

    The input IS NOT CHANGED: the caller keeps the user's document, while
    generation works on the expanded copy.
    """
    if not has_submachine(sm):
        return sm
    if _derinlik >= MAX_SUBMACHINE_DEPTH:
        raise SubmachineError(
            "Submachine references are nested more than %d levels deep."
            % MAX_SUBMACHINE_DEPTH)

    yigin = set(_yigin or set())
    sonuc = copy.deepcopy(sm)

    for dis in submachine_states(sm):
        ref = (dis.submachine_ref or "").strip()
        if not ref:
            raise SubmachineError(
                "The submachine state '%s' does not reference a machine."
                % dis.name)
        anahtar = normalise_ref(ref)
        if anahtar in yigin:
            raise SubmachineError(
                "The submachine reference of '%s' is circular: '%s' is "
                "already being expanded." % (dis.name, ref))

        ic = resolve(ref)
        if ic is None:
            raise SubmachineError(
                "The machine referenced by '%s' could not be found: %s"
                % (dis.name, ref))
        ic = flatten(ic, resolve, _derinlik + 1, yigin | {anahtar})

        _yerine_koy(sonuc, dis.id, ic)

    return sonuc


def _yerine_koy(hedef: StateMachine, dis_id: str, ic: StateMachine) -> None:
    """Turns a submachine state into a COMPOSITE state filled with the
    content of the inner machine."""
    dis = hedef.states[dis_id]
    dis_ad = dis.name

    # The outer vertex is now an ordinary composite state. entry/exit/do are
    # KEPT: in UML a submachine state may carry them.
    dis.kind = StateKind.COMPOSITE
    dis.regions = max(1, ic.region_count(None))

    ad_esleme: Dict[str, str] = {}
    for s in ic.ordered_states():
        yeni = copy.deepcopy(s)
        yeni.id = "%s__%s" % (dis_id, s.id)
        yeni.name = qualified_name(dis_ad, s.name)
        # The ROOT vertices of the inner machine become children of the outer state.
        yeni.parent = ("%s__%s" % (dis_id, s.parent)) if s.parent else dis_id
        # The position is moved inside the outer state, so it looks sane on the canvas.
        yeni.x = float(s.x) + 14.0
        yeni.y = float(s.y) + 34.0
        ad_esleme[s.id] = yeni.id
        hedef.add_state(yeni)

    for t in ic.ordered_transitions():
        yeni = copy.deepcopy(t)
        yeni.id = "%s__%s" % (dis_id, t.id)
        yeni.source = ad_esleme.get(t.source, t.source)
        yeni.target = ad_esleme.get(t.target, t.target)
        hedef.add_transition(yeni)

    _includes_birlestir(hedef, ic)


def _includes_birlestir(hedef: StateMachine, ic: StateMachine) -> None:
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
    satirlar = (hedef.user_includes or "").splitlines()
    gorulen = {ln.strip() for ln in satirlar if ln.strip()}
    eklenen = []
    for ln in (ic.user_includes or "").splitlines():
        anahtar = ln.strip()
        if anahtar and anahtar not in gorulen:
            gorulen.add(anahtar)
            eklenen.append(ln)
    if eklenen:
        hedef.user_includes = "\n".join(satirlar + eklenen).strip("\n")


def workspace_resolver(ws, acik: Optional[Dict[str, StateMachine]] = None
                       ) -> Resolver:
    """Builds a resolver that reads the files in the workspace.

    OPEN DOCUMENTS WIN. If the user has CHANGED the referenced machine in
    another tab and has not saved it yet, using the old copy on disk would
    make the generated code disagree with the diagram on screen. That is the
    hardest kind of bug to notice.
    """
    onbellek: Dict[str, StateMachine] = {}
    acik_map = {normalise_ref(k): v for k, v in (acik or {}).items()}

    def cozumle(ref: str) -> Optional[StateMachine]:
        anahtar = normalise_ref(ref)
        if anahtar in acik_map:
            return acik_map[anahtar]
        if anahtar in onbellek:
            return onbellek[anahtar]
        if ws is None:
            return None
        try:
            yol = ws.resolve(ref)
        except Exception:                       # noqa: BLE001
            return None
        if not os.path.isfile(yol):
            return None
        try:
            with open(yol, encoding="utf-8") as fh:
                makine = StateMachine.from_json(fh.read())
        except Exception:                       # noqa: BLE001
            return None
        onbellek[anahtar] = makine
        return makine

    return cozumle
