"""The model validator.

Runs BEFORE any code is generated. The goal: the generated C/C++ must always
compile and be semantically consistent. A single ERROR stops code generation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Set

from .model import (StateKind, StateMachine, TransitionKind,
                    time_event_delay)
from .naming import pascal, screaming_snake

IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Names that clash with the symbols the generator emits. The list is not
# guesswork but a measurement of whether each candidate ACTUALLY compiles;
# tools/test_reserved_names.py keeps a regression case for every entry.
#
#     The <PREFIX>_STATE_COUNT / <PREFIX>_STATE_NONE macros clash with the state
#     enum constants of the same prefix; on the event side there are also
#     EVENT_COUNT / EVENT_INVALID / EVENT_COMPLETION.
RESERVED_STATE_NAMES = {"NONE", "COUNT"}
RESERVED_EVENT_NAMES = {"COUNT", "INVALID", "COMPLETION"}

#: Members the generator already defines inside the C++ 'enum class Event'
RESERVED_EVENT_PASCAL = {"Completion", "Invalid"}

# C and C++ keywords - they cannot be used as a state/event name.
C_KEYWORDS: Set[str] = {
    "auto", "break", "case", "char", "const", "continue", "default", "do",
    "double", "else", "enum", "extern", "float", "for", "goto", "if", "inline",
    "int", "long", "register", "restrict", "return", "short", "signed",
    "sizeof", "static", "struct", "switch", "typedef", "union", "unsigned",
    "void", "volatile", "while", "bool", "true", "false", "class", "namespace",
    "template", "typename", "public", "private", "protected", "virtual", "new",
    "delete", "this", "operator", "try", "catch", "throw", "using", "nullptr",
    "constexpr", "noexcept", "explicit", "friend", "mutable", "static_assert",
}

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


@dataclass
class Issue:
    severity: str           # "error" | "warning" | "info"
    code: str               # a stable code such as V001
    message: str
    element_id: Optional[str] = None   # the element to select when clicked

    @property
    def is_error(self) -> bool:
        return self.severity == "error"

    def __str__(self) -> str:
        return "[%s] %s: %s" % (self.severity.upper(), self.code, self.message)


def _valid_ident(name: str) -> bool:
    return bool(IDENT_RE.match(name)) and name not in C_KEYWORDS


def _balanced(expr: str) -> bool:
    """Roughly checks bracket balance and that quotes are closed."""
    stack: List[str] = []
    pairs = {")": "(", "]": "[", "}": "{"}
    i = 0
    n = len(expr)
    while i < n:
        ch = expr[i]
        if ch in ("'", '"'):
            quote = ch
            i += 1
            while i < n:
                if expr[i] == "\\":
                    i += 2
                    continue
                if expr[i] == quote:
                    break
                i += 1
            if i >= n:
                return False           # an unclosed quote
        elif ch in "([{":
            stack.append(ch)
        elif ch in ")]}":
            if not stack or stack.pop() != pairs[ch]:
                return False
        i += 1
    return not stack


def _baglamli(tip: str) -> bool:
    """Does the machine carry a USER CONTEXT (`void` does not)."""
    return (tip or "").strip() not in ("", "void")


def _submachine_flatten(sm, resolve):
    """TRIES to flatten; raises on an error (cycle, depth, missing).

    The input is not changed; `flatten` returns an expanded COPY.
    """
    from .submachine import flatten
    return flatten(sm, resolve)


def _sorumlu_altmakine(sm, genis_id: str):
    """The id of the submachine state that PRODUCED an expanded state.

    Substitution gives inner states ids of the form `<outerId>__<innerId>`,
    and nesting keeps prefixing. The first part is always the submachine
    state in the USER's model.

    :return: the id, or None when the state does not come from an expansion
    """
    if "__" not in genis_id:
        return None
    outer = genis_id.split("__", 1)[0]
    if outer in sm.states:
        return outer
    return None


def _error_owner(sm, text: str):
    """The submachine state NAMED in the text of a flattening error.

    `SubmachineError` messages name the culprit in quotes. Because the canvas
    binds the error to an element and paints it red, finding the right element
    means not sending the user to an innocent state.

    :return: the id, or None when the name alone is not enough
    """
    adaylar = [x.id for x in sm.ordered_states()
               if x.kind is StateKind.SUBMACHINE and x.name
               and ("'%s'" % x.name) in text]
    if len(adaylar) == 1:
        return adaylar[0]
    return None


def _expanded_name_problems(sm, duz):
    """The name collisions that appear only AFTER flattening.

    When a submachine is substituted, the inner states are renamed to
    `Outer_Inner`. That name can fall on the same C constant as a state that
    ALREADY EXISTS in the main machine. Both of the user's diagrams look
    perfect on their own; the collision exists only in the expanded model.

    This model USED NOT TO BE VALIDATED AT ALL: the validator only checked
    that flattening SUCCEEDED, never its result. The collision surfaced in the
    customer's compiler as "redeclaration of enumerator" -- without a single
    warning from the tool.

    THE RESPONSIBLE ELEMENT is returned too. The canvas binds every error to
    an element and paints it red; whichever submachine state the problem came
    from is the one to mark. They all used to be bound to the FIRST submachine
    state in the file, sending the user to an element that was perfectly fine.
    yonlendiriliyordu.

    :return: (code, message, culprit_id) triples; culprit_id may be None
    """
    sorunlar = []
    gorulen = {}
    for st in duz.ordered_states():
        if st.kind is StateKind.INITIAL:
            continue
        sorumlu = _sorumlu_altmakine(sm, st.id)
        if not _valid_ident(st.name):
            sorunlar.append((
                "V165",
                "Expanding the submachines produces the state name '%s', "
                "which is not a valid C identifier." % st.name,
                sorumlu))
            continue
        anahtar = screaming_snake(st.name)
        previous = gorulen.get(anahtar)
        if previous is not None:
            # Of the two sides of the collision, mark the one that DOES NOT COME
            # FROM AN EXPANSION: that is what the user will fix (either the name of
            # the inner state or the name of the submachine state qualifying it).
            hedef = sorumlu if sorumlu is not None else previous[1]
            if previous[0] != st.name:
                sorunlar.append((
                    "V164",
                    "Expanding the submachines produces two states, '%s' and "
                    "'%s', that generate the same constant '%s'. Rename one "
                    "of them or the submachine state that qualifies it."
                    % (previous[0], st.name, anahtar),
                    hedef))
            else:
                sorunlar.append((
                    "V164",
                    "Expanding the submachines produces two states both named "
                    "'%s', which generate the same constant '%s'. Rename the "
                    "submachine state that qualifies one of them."
                    % (st.name, anahtar),
                    hedef))
        else:
            gorulen[anahtar] = (st.name, sorumlu)
    return sorunlar


def _region_split(sm, dugum, idler, ne: str):
    """Are the given vertices in DIFFERENT regions of the SAME orthogonal state?

    UML 2.5.1, 14.5.6.7 (printed p.350-351): transitions leaving a fork
    "must target states in different regions of an orthogonal state", while
    transitions entering a join "must originate in different Regions of an
    orthogonal State".
    """
    idler = [x for x in idler if x in sm.states]
    if len(idler) < 2:
        return None
    common = idler[0]
    for other in idler[1:]:
        common = sm.lca(common, other)
        if common is None:
            break
    if common is None or not sm.is_orthogonal(common):
        return "The segments of '%s' must %s." % (dugum.name, ne)
    regions = []
    for x in idler:
        b = _region_index(sm, x, common)
        if b is None:
            return "The segments of '%s' must %s." % (dugum.name, ne)
        regions.append(b)
    if len(set(regions)) != len(regions):
        return ("Two segments of '%s' use the SAME region of '%s'; they must "
                "%s." % (dugum.name, sm.states[common].name, ne))
    return None


def _region_index(sm, sid: str, sahip: str):
    """Which region under `owner` `sid` falls into (None if none)."""
    cur = sid
    n = 0
    while cur is not None and n <= len(sm.states) + 1:
        st = sm.states.get(cur)
        if st is None:
            return None
        if st.parent == sahip:
            return sm.region_of(cur)
        cur = st.parent
        n += 1
    return None


def validate(sm: StateMachine, resolve=None) -> List[Issue]:
    """Validates the model and returns the problems in order of severity.

    ``resolve``: the function that resolves submachine references. Without it
    the EXISTENCE of a reference is not checked -- only the structural rules
    are applied. That lets the tool run outside a workspace too (tests, a
    quick preview), while the application, which passes a real resolver, still
    CATCHES a missing or cyclic reference.
    """
    issues: List[Issue] = []
    #: Expansion problems are reported ONLY ONCE.
    _genisletme_bildirildi: List[bool] = []
    #: The flattening RESULT: [(expanded_machine, error)] -- at most one item.
    #:
    #: Flattening used to run once PER submachine state, and every call
    #: deep-copied the WHOLE machine. The cost grew quadratically: with 8
    #: submachine states validation took 0.067 s, with 32 it passed a second
    #: -- and validation runs after EVERY edit. Since the machine does not
    #: change during validation, the result can be shared.
    _plain_result: List = []

    def err(code, msg, eid=None):
        issues.append(Issue("error", code, msg, eid))

    def warn(code, msg, eid=None):
        issues.append(Issue("warning", code, msg, eid))

    def info(code, msg, eid=None):
        issues.append(Issue("info", code, msg, eid))

    def _genisletilmis():
        """Computes the expanded model ONCE.

        :return: (machine, error) -- a None machine means either there is no
                 submachine, or no resolver was given, or the expansion
                 failed (and then `error` is set).
        """
        if not _plain_result:
            altmakine_var = any(x.kind is StateKind.SUBMACHINE
                                for x in sm.states.values())
            if resolve is None or not altmakine_var:
                _plain_result.append((None, None))
            else:
                try:
                    _plain_result.append((_submachine_flatten(sm, resolve), None))
                except Exception as exc:            # noqa: BLE001
                    _plain_result.append((None, exc))
        return _plain_result[0]

    # --------------------------------------------------------------- machine #
    if not _valid_ident(sm.prefix):
        err("V001", "Symbol prefix '%s' is not a valid C identifier." % sm.prefix)
    if not sm.states:
        err("V002", "Diagram is empty: at least one initial pseudostate and one state are required.")
        return issues

    # ----------------------------------------------------------------- naming #
    # Every vertex except initial enters the generated enum; its name must be a C id.
    real_states = [s for s in sm.ordered_states()
                   if s.kind is not StateKind.INITIAL]
    seen_names = {}
    for s in real_states:
        if not _valid_ident(s.name):
            err("V010", "'%s' is not a valid C identifier (state name)." % s.name, s.id)
        # The key is the constant the generator WILL WRITE (naming.screaming_snake).
        # Looking at a plain .upper() gives the wrong answer: 'LedOn' and 'Led_On'
        # produce the same '..._LED_ON' constant but look different under .upper().
        key = screaming_snake(s.name)
        if key in seen_names:
            err("V011", "States '%s' and '%s' generate the same "
                        "'<PREFIX>_STATE_%s' constant."
                % (seen_names[key][1], s.name, key), s.id)
        seen_names[key] = (s.id, s.name)

    # Clashes with the symbols the generator emits itself
    for s in real_states:
        sym = screaming_snake(s.name)
        if sym in RESERVED_STATE_NAMES:
            err("V013", "State name '%s' conflicts with the generated "
                        "'<PREFIX>_STATE_%s' constant; choose another name."
                % (s.name, sym), s.id)

    # THE EVENT RULES RUN ON THE EXPANDED MODEL.
    #
    # When a submachine is substituted, the events of the inner machine enter
    # the generated enum too -- and THEIR NAMES ARE NOT QUALIFIED (states
    # become `Outer_Inner`, events stay as they are). Looking at the
    # unexpanded model, the outer `DO_IT` and the inner `DoIt` NEVER MEET:
    # validation passes clean, the same constant is defined twice in the
    # generated header, and the customer's compiler says "redeclaration of
    # enumerator". The same gap also disabled V012/V014/V017 and the time
    # event rules (V190/V191) for events coming from a submachine.
    # devre disi birakiyordu.
    _wide_model, _ = _genisletilmis()
    if _wide_model is not None:
        event_source = _wide_model
    else:
        event_source = sm

    def tran_of(event_name: str):
        """The id of the transition that carries the event.

        Events coming from an expansion have no transition id in the
        USER's model; the canvas cannot mark that id. In such a case the
        problem is bound to the submachine state that brought the event in.
        """
        own = next((t.id for t in sm.transitions.values()
                      if t.event.strip() == event_name), None)
        if own is not None:
            return own
        return next((x.id for x in sm.ordered_states()
                     if x.kind is StateKind.SUBMACHINE), None)

    for ev in event_source.events():
        if screaming_snake(ev) in RESERVED_EVENT_NAMES:
            err("V014", "Event name '%s' is reserved by the generator; choose "
                        "another name." % ev, tran_of(ev))

    seen_events = {}
    seen_pascal = {}
    for ev in event_source.events():
        # A TIME EVENT has a separate form: `after(<expression>)`.
        #
        # THE EXEMPTION APPLIES ONLY TO THE IDENTIFIER RULE. A time event used to
        # be skipped entirely (`continue`) and never reached the collision checks
        # below. But the generated constant goes through the same path:
        # `after(50)` and `AFTER50` produce the same `<PREFIX>_EVENT_...`
        # constant, and so do `after(1.5)` and `after(15)`. The result was a
        # DUPLICATE ENUM CONSTANT in the generated header and a file that would
        # not compile -- with the tool saying nothing.
        gecikme = time_event_delay(ev)
        if gecikme is not None:
            if not gecikme:
                err("V190", "The time event '%s' has no delay; write "
                            "after(100) or after(MY_TIMEOUT_MS)." % ev,
                    tran_of(ev))
            elif not _balanced(gecikme):
                err("V191", "The delay of the time event '%s' has unbalanced "
                            "brackets or quotes." % ev, tran_of(ev))
        elif not _valid_ident(ev):
            err("V012", "Event name '%s' is not a valid C identifier." % ev,
                tran_of(ev))
        esym = screaming_snake(ev)
        if esym in seen_events:
            err("V015", "Events '%s' and '%s' generate the same "
                        "'<PREFIX>_EVENT_%s' constant."
                % (seen_events[esym], ev, esym),
                tran_of(ev))
        seen_events[esym] = ev

        # C++ event constants are converted to PascalCase; 'MY_EVENT' and 'MyEvent'
        # fall on the same member and the generated .hpp does not compile.
        pev = pascal(ev)
        if pev in RESERVED_EVENT_PASCAL:
            err("V017", "Event name '%s' conflicts with the generator's own "
                        "'Event::%s' constant in the C++ output; choose another name."
                % (ev, pev), tran_of(ev))
        if pev in seen_pascal and seen_pascal[pev] != ev:
            err("V016", "Events '%s' and '%s' generate the same 'Event::%s' "
                        "constant in the C++ output."
                % (seen_pascal[pev], ev, pev), tran_of(ev))
        seen_pascal[pev] = ev

    # ------------------------------------------------------------ structural #
    for s in sm.states.values():
        if s.parent is not None and s.parent not in sm.states:
            err("V020", "The parent state of '%s' was not found." % s.name, s.id)
        if s.parent is not None and sm.is_descendant(s.parent, s.id):
            err("V021", "State '%s' has a circular hierarchy." % s.name, s.id)
        parent = sm.parent_of(s.id)
        if parent is not None and parent.kind is not StateKind.COMPOSITE:
            err("V022", "'%s' is inside non-composite state '%s'." % (s.name, parent.name), s.id)

    for s in sm.states.values():
        if s.kind is StateKind.COMPOSITE and not sm.children(s.id):
            warn("V023", "Composite state '%s' is empty; convert it to a simple state." % s.name, s.id)

    # ---------------------------------------------------------- transitions  #
    for t in sm.transitions.values():
        src = sm.states.get(t.source)
        tgt = sm.states.get(t.target)
        if src is None:
            err("V030", "The source of a transition is undefined (not connected).", t.id)
            continue
        if tgt is None:
            err("V031", "The target of the transition leaving '%s' is undefined (not connected)." % src.name, t.id)
            continue

        if src.kind is StateKind.FINAL:
            err("V032", "A final state ('%s') cannot have an outgoing transition." % src.name, t.id)
        if src.kind is StateKind.TERMINATE:
            err("V045", "A terminate pseudostate ('%s') cannot have an outgoing transition."
                % src.name, t.id)
        if tgt.kind is StateKind.INITIAL:
            err("V033", "An initial pseudostate ('%s') cannot be the target of a transition." % tgt.name, t.id)

        if src.kind is StateKind.INITIAL:
            if t.event.strip():
                err("V034", "An initial transition cannot have an event.", t.id)
            if t.guard.strip():
                err("V035", "An initial transition cannot have a guard.", t.id)
            if tgt.parent != src.parent:
                err("V036", "An initial transition cannot leave its own region "
                            "('%s' -> '%s')." % (src.name, tgt.name), t.id)

        if src.kind.is_branch and t.event.strip():
            err("V037", "Transitions leaving a %s pseudostate cannot have an event "
                        "(guard only)."
                % ("Choice" if src.kind is StateKind.CHOICE else "Junction"), t.id)

        if src.kind.is_history:
            if t.event.strip():
                err("V046", "A history default transition cannot have an event.", t.id)
            if t.guard.strip():
                err("V047", "A history default transition cannot have a guard.", t.id)
            if tgt.parent != src.parent:
                err("V048", "The target of a history default transition must be in the same region "
                            "('%s' -> '%s')." % (src.name, tgt.name), t.id)

        if t.kind is TransitionKind.INTERNAL and t.source != t.target:
            err("V038", "An internal transition must have the same source and target "
                        "('%s' -> '%s')." % (src.name, tgt.name), t.id)
        if t.kind is TransitionKind.INTERNAL and not t.event.strip():
            # THE OLD TEXT WAS WRONG: it said "never fires", whereas such a
            # transition is triggered by the completion event of the state and runs
            # EXACTLY ONCE (UML 2.5.1, 14.2.3.8.3 -- the completion event is born
            # WHEN THE STATE IS ENTERED). The previous engine ran it 16 times; that
            # is fixed, but the construct itself is valid and is reported only
            # because it is easily confused with an entry behaviour.
            info("V039", "This internal transition is triggered by the state's "
                         "completion event, so it runs exactly once, right after "
                         "the entry behavior.", t.id)
        if t.kind is TransitionKind.LOCAL and not sm.is_descendant(t.target, t.source):
            err("V040", "The target of a local transition ('%s') must be inside the source ('%s')."
                % (tgt.name, src.name), t.id)

        # THERE IS NO PLAIN TRANSITION BETWEEN ORTHOGONAL REGIONS.
        #
        # UML 2.5.1, 14.2.3.7 (printed p.313): branching into regions is the job
        # of a fork, merging them the job of a join. If a plain arrow joins two
        # regions, the source region closes while the target region is still
        # active; the configuration stays inconsistent.
        # FORK / JOIN and the connection points are EXEMPT FROM THIS RULE:
        # crossing between regions is exactly THEIR job. Without the exemption,
        # the tool's "use a fork or a join" message appeared precisely when the
        # user drew a fork, and three features became unusable at once. V120-V127
        # and V140-V149 already check the distinction.
        _MUAF = (StateKind.FORK, StateKind.JOIN,
                 StateKind.ENTRY_POINT, StateKind.EXIT_POINT)
        common = sm.lca(t.source, t.target)
        if (common is not None and sm.is_orthogonal(common)
                and src.kind not in _MUAF and tgt.kind not in _MUAF
                and t.source != common and t.target != common):
            s_region = _region_index(sm, t.source, common)
            t_region = _region_index(sm, t.target, common)
            if s_region is not None and t_region is not None and s_region != t_region:
                err("V102", "This transition crosses from region %d to region %d "
                            "of orthogonal state '%s'; use a fork or a join."
                    % (s_region + 1, t_region + 1, sm.states[common].name), t.id)

        for label, expr in (("guard", t.guard), ("action", t.action)):
            if expr.strip() and not _balanced(expr):
                err("V041", "The transition's %s expression has unbalanced brackets/quotes." % label, t.id)

    # --------------------------------------------------------- initial/region #
    #
    # THE RULES ARE PER REGION, not per state. UML 2.5.1, 14.2.3.2 (printed
    # p.307): a composite state owns one or more REGIONS and EVERY region has
    # its own default entry. Having two initials in an orthogonal state is
    # correct; putting them in one bucket and counting them would reject a
    # valid model.
    sahipler: List[Optional[str]] = [None]
    sahipler += [s.id for s in sm.states.values()
                 if s.kind is StateKind.COMPOSITE]
    for sahip in sahipler:
        if sahip is not None and not sm.children(sahip):
            continue
        bolge_sayisi = sm.region_count(sahip)

        # THERE MUST BE NO CHILD OUTSIDE THE DECLARED REGION COUNT.
        #
        # `build_ir` uses a child's `region` field as it is: put a child carrying
        # `region=5` into a two-region state and the generated table opens a
        # region THAT APPEARS NOWHERE ON THE DIAGRAM, `active[]` grows, and that
        # child enters the code even though it can never become active. No rule
        # was catching this.
        #
        # The interface can no longer PRODUCE such a model (the region is read
        # from the band the element is drawn in, and shrinking refuses to delete
        # a populated region), but a file edited by hand or coming from another
        # version may carry it. Every silent divergence between the drawing and
        # the generated code has to be reported.
        if sahip is not None:
            for child in sm.children(sahip):
                region_no = int(getattr(child, "region", 0) or 0)
                if region_no < 0 or region_no >= bolge_sayisi:
                    err("V103",
                        "'%s' says it is in region %d of '%s', but that state "
                        "has only %d region(s). Drag it into one of the bands "
                        "shown in the diagram."
                        % (child.name, region_no + 1,
                           sm.states[sahip].name, bolge_sayisi),
                        child.id)
        for region in range(bolge_sayisi):
            content = sm.children_in(sahip, region)
            if sahip is None:
                rname = "root region"
            elif bolge_sayisi > 1:
                rname = "region %d of '%s'" % (region + 1, sm.states[sahip].name)
            else:
                rname = "'%s'" % sm.states[sahip].name
            if sahip is not None and bolge_sayisi > 1 and not content:
                err("V100", "%s is empty; every region of an orthogonal state "
                            "must contain at least one state." % rname, sahip)
                continue
            inits = [x for x in content if x.kind is StateKind.INITIAL]
            if not inits:
                err("V050", "%s has no initial pseudostate." % rname, sahip)
            elif len(inits) > 1:
                err("V051", "%s contains %d initial pseudostates; only one is "
                            "allowed." % (rname, len(inits)), inits[1].id)
            else:
                outs = sm.outgoing(inits[0].id)
                if not outs:
                    err("V052", "The initial pseudostate in %s has no outgoing "
                                "transition." % rname, inits[0].id)
                elif len(outs) > 1:
                    err("V053", "The initial pseudostate in %s has more than one "
                                "outgoing transition." % rname, inits[0].id)
                else:
                    tgt = sm.states.get(outs[0].target)
                    if tgt is not None and (tgt.kind.is_history
                                            or tgt.kind is StateKind.TERMINATE):
                        err("V054", "The initial transition in %s cannot target a "
                                    "history/terminate pseudostate ('%s'); target "
                                    "a state directly." % (rname, tgt.name),
                            outs[0].id)
                    elif tgt is not None and sm.region_of(tgt.id) != region:
                        err("V101", "The initial transition in %s targets '%s', "
                                    "which is in another region; a region's "
                                    "default entry must stay inside it."
                            % (rname, tgt.name), outs[0].id)

    # ----------------------------------------------- pseudostate constraints #
    for s in sm.states.values():
        if s.kind is StateKind.INITIAL and sm.incoming(s.id):
            err("V060", "An initial pseudostate cannot have an incoming transition.", s.id)
        if s.kind.is_branch:
            kname = "choice" if s.kind is StateKind.CHOICE else "junction"
            outs = sm.outgoing(s.id)
            if not outs:
                err("V061", "The '%s' %s node has no outgoing transitions; the machine "
                            "cannot proceed once it reaches this node." % (s.name, kname), s.id)
            elif len(outs) < 2:
                warn("V073", "The '%s' %s node should have at least two outgoing transitions."
                     % (s.name, kname), s.id)
            has_else = any(not t.guard.strip() or t.guard.strip().lower() == "else"
                           for t in outs)
            if outs and not has_else:
                # A CHOICE AND A JUNCTION ARE NOT THE SAME THING.
                #
                # UML 2.5.1, 14.2.3.7 (printed p.313) on a choice: "If none of
                # the guards evaluates to true, then the model is considered
                # ill formed." -- that is an ERROR.
                #
                # The same clause DOES NOT SAY this for a junction; on the contrary:
                # "the entire compound transition is disabled even though its
                # Triggers are enabled." So when no path is found the compound
                # transition is disabled and the model is not ill formed. Treating a
                # junction as an error blocked code generation for a valid model.
                if s.kind is StateKind.CHOICE:
                    err("V062", "The '%s' choice node has no default (else / "
                                "unguarded) branch; if no guard holds the model "
                                "is ill-formed." % s.name, s.id)
                else:
                    info("V062", "The '%s' junction node has no default (else / "
                                 "unguarded) branch; if no path holds, the whole "
                                 "compound transition is simply disabled."
                                 % s.name, s.id)
            if not sm.incoming(s.id):
                warn("V063", "The '%s' %s node has no incoming transitions." % (s.name, kname), s.id)
        # fork / join rules
        #
        # Every quotation is taken verbatim from OMG UML 2.5.1; see
        # app/core/uml_spec.py.
        if s.kind is StateKind.FORK:
            gelen = sm.incoming(s.id)
            giden = sm.outgoing(s.id)
            if len(gelen) != 1:
                err("V120", "Fork '%s' must have exactly one incoming "
                            "transition (it has %d)." % (s.name, len(gelen)),
                    s.id)
            if len(giden) < 2:
                err("V121", "Fork '%s' must have at least two outgoing "
                            "transitions (it has %d); with one target it is "
                            "an ordinary transition." % (s.name, len(giden)),
                    s.id)
            for t in giden:
                if t.guard.strip() or t.event.strip():
                    err("V122", "A transition leaving fork '%s' cannot carry a "
                                "guard or a trigger." % s.name, t.id)
            # The code sits HERE, at the call site, as PLAIN TEXT: the test that
            # checks the reference table for completeness looks for the literal
            # "V123" in the source and cannot see a code passed in a variable.
            sorun = _region_split(sm, s, [t.target for t in giden],
                                  "target states in different regions of an "
                                  "orthogonal state")
            if sorun:
                err("V123", sorun, s.id)

        if s.kind is StateKind.JOIN:
            gelen = sm.incoming(s.id)
            giden = sm.outgoing(s.id)
            if len(gelen) < 2:
                err("V124", "Join '%s' must have at least two incoming "
                            "transitions (it has %d)." % (s.name, len(gelen)),
                    s.id)
            if len(giden) != 1:
                err("V125", "Join '%s' must have exactly one outgoing "
                            "transition (it has %d)." % (s.name, len(giden)),
                    s.id)
            for t in gelen:
                if t.guard.strip() or t.event.strip():
                    err("V126", "A transition entering join '%s' cannot carry "
                                "a guard or a trigger." % s.name, t.id)
            sorun = _region_split(sm, s, [t.source for t in gelen],
                                  "originate in different regions of an "
                                  "orthogonal state")
            if sorun:
                err("V127", sorun, s.id)

        # deferred events
        if s.deferred:
            if not s.kind.is_real_state:
                err("V180", "'%s' is a pseudostate; only a state can defer "
                            "events." % s.name, s.id)
            # The events used IN TRANSITIONS. Because `sm.events()` includes the
            # deferred ones, using it would count an event no transition consumes as
            # "known" and the warning would NEVER fire.
            bilinen = {t.event.strip() for t in sm.transitions.values()
                       if t.event.strip()}
            gorulen = set()
            for ad in s.deferred:
                ad = str(ad).strip()
                if not ad:
                    continue
                if not IDENT_RE.match(ad):
                    err("V181", "'%s' is not a valid event name to defer in "
                                "'%s'." % (ad, s.name), s.id)
                elif ad in gorulen:
                    warn("V182", "'%s' is listed twice in the deferred events "
                                 "of '%s'." % (ad, s.name), s.id)
                gorulen.add(ad)
                # When a state's OWN outgoing transition carries the same event, UML
                # gives the transition priority ("a kind of override option"). That is
                # VALID but easily misread, so it IS REPORTED.
                # BILDIRILIR.
                for t in sm.outgoing(s.id):
                    if t.event.strip() == ad:
                        info("V183", "'%s' both defers '%s' and has a "
                                     "transition triggered by it; the "
                                     "transition wins." % (s.name, ad), s.id)
                        break
            empty = gorulen - bilinen
            if empty:
                warn("V184", "'%s' defers %s, which no transition uses."
                     % (s.name, ", ".join(sorted(empty))), s.id)

        # submachine
        if s.kind is StateKind.SUBMACHINE:
            ref = (s.submachine_ref or "").strip()
            if not ref:
                err("V160", "The submachine state '%s' does not reference a "
                            "machine; pick one in the properties panel."
                    % s.name, s.id)
            if sm.children(s.id):
                err("V161", "The submachine state '%s' contains states of its "
                            "own; its contents come from the referenced "
                            "machine. Move them out or make it a composite "
                            "state." % s.name, s.id)
            # CONNECTION POINT BINDING (ConnectionPointReference) is NOT SUPPORTED
            # for now and is refused explicitly; see core/submachine.py.
            if ref and resolve is not None:
                try:
                    hedef = resolve(ref)
                except Exception:               # noqa: BLE001
                    hedef = None
                if hedef is None:
                    err("V162", "The machine referenced by '%s' could not be "
                                "found: %s" % (s.name, ref), s.id)
                elif (_baglamli(hedef.context_type)
                      and hedef.context_type.strip()
                      != sm.context_type.strip()):
                    # THE CONTEXT TYPE WAS BEING DROPPED SILENTLY.
                    #
                    # Substitution carries the entry/exit/do and guard texts of the inner
                    # machine VERBATIM; the `ctx` in those texts compiles against the context
                    # type of the expanded machine. If the two types differ, the code of the
                    # inner machine is compiled against THE WRONG STRUCT -- while validation
                    # passes perfectly clean.
                    #
                    # If the inner machine's context is 'void' there is no problem: that
                    # code never touches `ctx`.
                    err("V166",
                        "The submachine state '%s' references '%s', whose "
                        "context type '%s' differs from this machine's "
                        "'%s'. Expanding it would compile the referenced "
                        "machine's behaviour against the wrong context. "
                        "Make the two match, or give the referenced machine "
                        "the context type 'void'."
                        % (s.name, hedef.name, hedef.context_type.strip(),
                           sm.context_type.strip()), s.id)
                elif not _genisletme_bildirildi:
                    # The expansion and its checks are for the WHOLE MACHINE, not for a
                    # single submachine state: it happens once and the result is shared.
                    # Otherwise the same collision would be reported again for every
                    # submachine state.
                    _genisletme_bildirildi.append(True)
                    genis, error = _genisletilmis()
                    if error is not None:
                        # The message ALREADY names the culprit; bind the error to that
                        # element. If it cannot be found, bind it to NO element -- painting
                        # an arbitrary submachine state red sends the user to the wrong
                        # place.
                        err("V163", "A submachine reference cannot be "
                                    "expanded: %s" % error,
                            _error_owner(sm, str(error)))
                    elif genis is not None:
                        # The codes are given as plain text, not through a VARIABLE: the
                        # reference test looks for the `err("Vxxx"` pattern in the source and
                        # a code passed in a variable looks like a "dead entry".
                        for kod, mesaj, hedef in _expanded_name_problems(
                                sm, genis):
                            if kod == "V164":
                                err("V164", mesaj, hedef)
                            else:
                                err("V165", mesaj, hedef)

        # connection points
        if s.kind.is_connection_point:
            ad = ("entry point" if s.kind is StateKind.ENTRY_POINT
                  else "exit point")
            sahip = sm.states.get(s.parent) if s.parent else None
            if sahip is None or sahip.kind is not StateKind.COMPOSITE:
                err("V140", "The %s '%s' must belong to a composite state; "
                            "drag it onto the state whose boundary it sits "
                            "on." % (ad, s.name), s.id)
            giden = sm.outgoing(s.id)
            gelen = sm.incoming(s.id)

            if s.kind is StateKind.ENTRY_POINT:
                if not giden:
                    err("V141", "The entry point '%s' has no transition into "
                                "the state; it would lead nowhere." % s.name,
                        s.id)
                if not gelen:
                    warn("V142", "Nothing enters the entry point '%s'."
                         % s.name, s.id)
                if sahip is not None:
                    for t in giden:
                        if not sm.is_descendant(t.target, sahip.id):
                            err("V143", "A transition leaving entry point "
                                        "'%s' must end inside '%s'."
                                % (s.name, sahip.name), t.id)
                    # 14.2.3.7 (printed p.313): "In each Region ... there is
                    # at most a single Transition from the entry point to a
                    # Vertex within that Region."
                    kullanilan = {}
                    for t in giden:
                        b = _region_index(sm, t.target, sahip.id)
                        if b is None:
                            continue
                        if b in kullanilan:
                            err("V144", "The entry point '%s' has two "
                                        "transitions into the same region of "
                                        "'%s'; at most one is allowed."
                                % (s.name, sahip.name), t.id)
                        kullanilan[b] = t.id
                    for t in gelen:
                        if sahip is not None and sm.is_descendant(t.source,
                                                                 sahip.id):
                            err("V145", "The entry point '%s' is entered from "
                                        "inside '%s'; an entry point is the "
                                        "way IN from outside."
                                % (s.name, sahip.name), t.id)
            else:
                if len(giden) != 1:
                    err("V146", "The exit point '%s' must have exactly one "
                                "outgoing transition (it has %d)."
                        % (s.name, len(giden)), s.id)
                if not gelen:
                    warn("V147", "Nothing inside the state reaches the exit "
                                 "point '%s'." % s.name, s.id)
                if sahip is not None:
                    for t in gelen:
                        if not sm.is_descendant(t.source, sahip.id):
                            err("V148", "A transition entering exit point "
                                        "'%s' must start inside '%s'."
                                % (s.name, sahip.name), t.id)
                    for t in giden:
                        if sm.is_descendant(t.target, sahip.id):
                            err("V149", "The transition leaving exit point "
                                        "'%s' ends inside '%s'; an exit point "
                                        "is the way OUT."
                                % (s.name, sahip.name), t.id)

        if s.kind.is_history:
            if s.parent is None:
                err("V064", "History pseudostate '%s' cannot be in the root region; move "
                            "it inside a composite state." % s.name, s.id)
            outs = sm.outgoing(s.id)
            if len(outs) > 1:
                err("V065", "History pseudostate '%s' can have at most one default "
                            "transition." % s.name, s.id)
            if not sm.incoming(s.id):
                warn("V066", "History pseudostate '%s' has no incoming transitions." % s.name, s.id)
        if s.kind.is_pseudo and (s.entry.strip() or s.exit.strip() or s.do.strip()):
            err("V067", "'%s' is a pseudostate; it cannot carry entry/exit/do behavior."
                % s.name, s.id)
        # ONLY EXIT IS FORBIDDEN.
        #
        # UML 2.5.1, 14.5.2.5 FinalState Constraints (printed p.346) lists exactly
        # THREE constraints: no_exit_behavior, no_outgoing_transitions and
        # no_regions. There is NO constraint at all about entry or doActivity.
        # The previous version rejected all three and blocked code generation for
        # a valid model.
        if s.kind is StateKind.FINAL and s.exit.strip():
            err("V069", "'%s' is a final state; it cannot carry exit behavior. "
                        "Move it into the action of the incoming transition."
                % s.name, s.id)
        if s.kind.is_branch:
            for t in sm.outgoing(s.id):
                if t.target == s.id:
                    err("V070", "Pseudostate '%s' cannot transition to itself; the "
                                "machine would hang on this node." % s.name, t.id)

    # ----------------------------------------------------- junction chains -- #
    # A junction is a STATIC branch: the chain is flattened during code
    # generation and every path becomes one transition. If the chain does not
    # reach a state (a cycle) or is too deep, no path can be produced; unless
    # the user is told HERE, the transition vanishes and the event is ignored.
    MAX_JUNCTION_CHAIN = 8
    junction_ids = {j.id for j in sm.states.values()
                    if j.kind is StateKind.JUNCTION}

    def junction_chain_fault(start: str):
        """The first problem in the chain as (code, message); None if sound."""
        stack = [(start, 0, frozenset())]
        while stack:
            node, depth, seen = stack.pop()
            if node not in junction_ids:
                continue
            if node in seen:
                return ("V074", "The junction chain at '%s' has a loop; the chain "
                                "never reaches a state." % sm.states[node].name)
            if depth >= MAX_JUNCTION_CHAIN:
                return ("V075", "The junction chain at '%s' is %d steps deep; at "
                                "most %d steps are supported."
                        % (sm.states[node].name, depth + 1, MAX_JUNCTION_CHAIN))
            for branch in sm.outgoing(node):
                stack.append((branch.target, depth + 1, seen | {node}))
        return None

    for tran in sm.transitions.values():
        if tran.target in junction_ids and tran.source not in junction_ids:
            fault = junction_chain_fault(tran.target)
            if fault is not None:
                err(fault[0], fault[1], tran.id)

    # At most one history pseudostate of each kind PER REGION.
    #
    # UML 2.5.1, 14.2.3.7 (printed p.312-313): "A deepHistory Pseudostate can
    # only be defined for composite States and, at most one such Pseudostate
    # can be contained in a Region of a composite State." The limit is PER
    # REGION, not per STATE.
    #
    # The key ignored the region: putting one deep history per region into a
    # three-region orthogonal state -- something UML explicitly allows and the
    # tool's own palette encourages -- was rejected from the second one on,
    # and the model could generate NO code at all.
    hist_seen: dict = {}
    for s in sm.states.values():
        if s.kind.is_history:
            key = (s.parent, int(getattr(s, "region", 0) or 0), s.kind)
            if key in hist_seen:
                err("V068", "More than one %s in the same region ('%s')."
                    % ("deep history (H*)" if s.kind is StateKind.DEEP_HISTORY
                       else "shallow history (H)", s.name), s.id)
            hist_seen[key] = s.id

    # --------------------------------------------------------------- reachability #
    start = sm.initial_of(None)
    if start is not None:
        reachable: Set[str] = set()
        stack = [start.id]
        while stack:
            cur = stack.pop()
            if cur in reachable:
                continue
            reachable.add(cur)
            # PARENT STATES HAVE BEEN REACHED TOO.
            #
            # Entering a substate means entering every state that CONTAINS it. The
            # walk went down (children) and sideways (transitions) but not up: a
            # model entering a substate directly from a fork showed the containing
            # composite state as "unreachable" -- and once that composite counted as
            # unreachable, its children were never scanned and came out unreachable
            # as well. A textbook fork drawing warned about all three.
            # 
            for a in sm.ancestors(cur):
                if a.id not in reachable:
                    stack.append(a.id)
            # descend into the substates
            for c in sm.children(cur):
                if c.id not in reachable:
                    stack.append(c.id)
            # outgoing transitions
            for t in sm.outgoing(cur):
                if t.target and t.target not in reachable:
                    stack.append(t.target)
            # the transitions of the parent states count too
            for a in sm.ancestors(cur):
                for t in sm.outgoing(a.id):
                    if t.target and t.target not in reachable:
                        stack.append(t.target)
        for s in sm.states.values():
            if s.id not in reachable and s.kind is not StateKind.INITIAL:
                warn("V072", "State '%s' is unreachable by any path." % s.name, s.id)

    # --------------------------------------------------------------- dead end #
    for s in sm.states.values():
        if s.kind is StateKind.SIMPLE:
            has_out = bool(sm.outgoing(s.id))
            has_ancestor_out = any(sm.outgoing(a.id) for a in sm.ancestors(s.id))
            if not has_out and not has_ancestor_out:
                warn("V071", "State '%s' has no outgoing transition (deadlock state)." % s.name, s.id)

    # --------------------------------------------------- ambiguous transitions #
    for s in sm.states.values():
        # THE BRANCHES OF A FORK ARE NOT ALTERNATIVES; ALL OF THEM ARE TAKEN.
        #
        # UML 2.5.1, 14.2.3.7: a fork splits an incoming transition into "two or
        # more Transitions terminating on Vertices in orthogonal Regions", and
        # those transitions CANNOT carry a trigger or a guard. So the same
        # (event, guard, priority) triple IS THE RULE ITSELF for a fork, not a
        # violation -- but the check mistook it for ordinary branching and warned
        # on every valid fork. An ENTRY POINT leading into several regions also
        # "acts as a fork" (same clause), so it is exempt too.
        # madde), o da muaftir.
        if s.kind in (StateKind.FORK, StateKind.ENTRY_POINT):
            continue
        buckets = {}
        for t in sm.outgoing(s.id):
            key = (t.event.strip(), t.guard.strip(), t.priority)
            buckets.setdefault(key, []).append(t)
        for (ev, gd, _pri), group in buckets.items():
            if len(group) > 1:
                warn("V080", "State '%s' has %d transitions with the same "
                             "event/guard/priority; the choice is ambiguous."
                     % (s.name, len(group)), group[1].id)

    if sm.max_depth() > 8:
        warn("V090", "Hierarchy depth is %d; stack usage increases in the generated code."
             % sm.max_depth())

    issues.sort(key=lambda i: (SEVERITY_ORDER.get(i.severity, 9), i.code))
    return issues


def has_errors(issues: List[Issue]) -> bool:
    return any(i.is_error for i in issues)
