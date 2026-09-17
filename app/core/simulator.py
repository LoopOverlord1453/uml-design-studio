"""The reference HSM interpreter (Python).

It uses the *same* intermediate representation (IR) and the *same* algorithm
as the generated C/C++ code, but is written in an independent language. That

  * lets us run and watch the diagram in the interface without generating code,
  * lets `tools/test_semantics.py` compare the two implementations and catch
    semantic drift in the generators.

The algorithm matches the C generator line by line; when you change one,
change the other.

THE ACTIVE CONFIGURATION IS A VECTOR
------------------------------------
A single "active state" is not enough: the regions of an orthogonal state are
active AT THE SAME TIME (UML 2.5.1, 14.2.3.2, printed p.307). So the
configuration is an `active[]` array holding one leaf PER REGION. The history
record is keyed by region as well; keyed by parent state, the two regions of
an orthogonal state would share one record and returning through deep history
would make one restore the other's state.

THERE IS NO RECURSION. Entry and exit walk with explicit stacks so the same
construction is possible in the generated C: on an embedded target the event
handling depth cannot depend on THE MODEL, or stack use cannot be bounded.
"""

from __future__ import annotations

from typing import Callable, List, Optional

from ..codegen.ir import (Ir, KIND_COMPOSITE, KIND_FINAL, KIND_HIST_DEEP,
                          KIND_HIST_SHALLOW, KIND_SIMPLE, KIND_TERMINATE, NONE,
                          REGION_NONE, TKIND_EXTERNAL, TKIND_INTERNAL,
                          build_ir)
from .model import StateMachine

MAX_RTC_STEPS = 16
COMPLETION = 0

#: The maximum number of steps in an entry/exit walk. It is bounded by the
#: model hierarchy and the region count; the value here only cuts an
#: endless loop on a corrupt IR.
MAX_WALK_STEPS = 4096

#: The size of the pool holding deferred events. UML treats the pool as
#: unbounded; on an embedded target there is no unbounded queue, so the
#: limit is set EXPLICITLY and an overflow DOES NOT STAY SILENT.
MAX_DEFERRED = 16


class Simulator:
    """Runs a state machine in memory."""

    def __init__(self, sm: StateMachine,
                 guard_eval: Optional[Callable[[str], bool]] = None,
                 on_event: Optional[Callable[[str, str], None]] = None,
                 resolve=None) -> None:
        # `resolve`: resolves submachine references; never used when the model
        # has no submachine (see codegen/ir.build_ir).
        self.ir: Ir = build_ir(sm, resolve)
        #: The active leaf per region; NONE = the region is not active.
        self.active: List[int] = [NONE] * self.ir.region_count
        #: The REPRESENTATIVE leaf, for display and backward compatibility.
        self.state: int = NONE
        self.started = False
        self.terminated = False
        #: The history record, keyed BY REGION (NOT by the parent state).
        self.history: List[int] = [NONE] * self.ir.region_count
        #: A completion event is awaited PER REGION.
        #:
        #: UML 2.5.1, 14.2.3.8.3 (printed p.314): "If no such Behaviors are
        #: defined, the completion event is generated upon entry into the
        #: State." The event belongs to THE STATE; in an orthogonal machine every
        #: region has its own leaf, and therefore its own pending event.
        #:
        #: THE BUG WE HIT: there was a single machine-wide flag. An INTERNAL
        #: TRANSITION in a higher-numbered region cleared the flag raised by the
        #: entry of a lower-numbered one; the completion transition drawn in that
        #: region was NEVER taken and no warning was given. The result depended
        #: on the order in which the user had drawn the regions.
        #: cizdigine bagliydi.
        self.completion_pending = [False] * self.ir.region_count
        #: True when the completion loop hit the limit (the model is unstable).
        self.rtc_overflow = False
        #: The DEFERRED event pool (event indices, in arrival order).
        self.deferred_pool: List[int] = []
        #: True when the pool has overflowed.
        self.defer_overflow = False
        self.trace: List[str] = []
        self._guard_eval = guard_eval or (lambda expr: True)
        self._on_event = on_event
        self._event_index = {name: i for i, name in enumerate(self.ir.events)}

    # --------------------------------------------------------------- helpers #

    def _emit(self, kind: str, detail: str) -> None:
        self.trace.append("%s:%s" % (kind, detail))
        if self._on_event is not None:
            self._on_event(kind, detail)

    def _note(self, kind: str, detail: str) -> None:
        """Sends narration to the INTERFACE only; it does not write to `trace`.

        `trace` has to match the trace of the generated C/C++ code exactly
        (tools/test_semantics.py compares them; the regression suite also
        asserts things like "the trace must be empty on a terminated machine"
        and "the first record must be X:S"). Guard evaluations and the chosen
        transitions are NOT in the trace of the generated code, so they flow
        only to the panel, not here.
        """
        if self._on_event is not None:
            self._on_event(kind, detail)

    def transition_label(self, tran) -> str:
        """Turns a transition into one readable line: Src --EV [g] / effect--> Dst."""
        kaynak = self._name(tran.source)
        hedef = self._name(tran.target)
        olay = "" if tran.event == COMPLETION else self.ir.events[tran.event]
        koruma = "" if tran.guard < 0 else self.ir.guards[tran.guard]
        eylem = "" if tran.action < 0 else " ".join(
            self.ir.actions[tran.action].split())

        middle = olay if olay else "completion"
        if koruma:
            middle += " [%s]" % koruma
        if eylem:
            middle += " / %s" % eylem
        if tran.kind == TKIND_INTERNAL:
            return "%s --%s-- (internal, stays in %s)" % (kaynak, middle, kaynak)
        return "%s --%s--> %s" % (kaynak, middle, hedef)

    def _name(self, index: int) -> str:
        return self.ir.states[index].name if index != NONE else "<none>"

    def _parent(self, index: int) -> int:
        return self.ir.states[index].parent if index != NONE else NONE

    def _region_of(self, index: int) -> int:
        return self.ir.states[index].region if index != NONE else REGION_NONE

    def _depth(self, index: int) -> int:
        d = 0
        cur = index
        while cur != NONE and d <= self.ir.max_depth:
            cur = self._parent(cur)
            d += 1
        return d

    def _lca(self, a: int, b: int) -> int:
        da, db = self._depth(a), self._depth(b)
        while da > db:
            a = self._parent(a)
            da -= 1
        while db > da:
            b = self._parent(b)
            db -= 1
        while a != b:
            if a == NONE or b == NONE:
                return NONE
            a = self._parent(a)
            b = self._parent(b)
        return a

    def _is_ancestor(self, maybe: int, node: int) -> bool:
        """Is `maybe` a PROPER ancestor of `node` (not the node itself)?"""
        cur = self._parent(node)
        n = 0
        while cur != NONE and n <= self.ir.max_depth:
            if cur == maybe:
                return True
            cur = self._parent(cur)
            n += 1
        return False

    # -------------------------------------------------------------- behaviour #

    def _exec_entry(self, index: int) -> None:
        st = self.ir.states[index]
        self._emit("E", st.name)
        if st.entry:
            self._emit("code", st.entry)

    def _exec_exit(self, index: int) -> None:
        st = self.ir.states[index]
        self._emit("X", st.name)
        if st.exit:
            self._emit("code", st.exit)
        # Shallow history record (UML 14.2.3.4.5): only real states are stored; if
        # the region completed through a final state the history is CLEARED (the
        # default transition is used); pseudostate exits do not change the record.
        if st.region != REGION_NONE and st.parent != NONE:
            if st.kind in (KIND_SIMPLE, KIND_COMPOSITE):
                self.history[st.region] = index
            elif st.kind == KIND_FINAL:
                self.history[st.region] = NONE

    def _exec_action(self, action_id: int) -> None:
        if action_id < 0:
            return
        self._emit("A", self.ir.actions[action_id])

    def _guard(self, guard_id: int) -> bool:
        if guard_id < 0:
            return True
        expr = self.ir.guards[guard_id]
        result = bool(self._guard_eval(expr))
        self._note("G", "%s\t%s" % (expr, "true" if result else "false"))
        return result

    # -------------------------------------------------------- entry / exit #

    def _enter_one(self, index: int) -> None:
        """Activates a single state (it DOES NOT TOUCH its sub-regions)."""
        self._exec_entry(index)
        region = self._region_of(index)
        if region != REGION_NONE:
            self.active[region] = index
            # THE STATE WAS ENTERED: the completion event of this region is born.
            self.completion_pending[region] = True

    def _exit_one(self, index: int) -> None:
        """Closes a single state (its sub-regions must already be emptied)."""
        self._exec_exit(index)
        region = self._region_of(index)
        if region != REGION_NONE and self.active[region] == index:
            self.active[region] = NONE
            self.completion_pending[region] = False

    def _activate_below(self, index: int) -> None:
        """Activates the INSIDE of an entered state with the defaults.

        Regions are processed in ASCENDING order. UML does not define that
        order (14.2.3.8.3); the tool fixes it and writes it in the header.
        """
        # Stack items: [state, the index of the next region to process]
        stack: List[List[int]] = [[index, 0]]
        step = 0
        while stack and step < MAX_WALK_STEPS:
            step += 1
            cur, k = stack[-1]
            st = self.ir.states[cur]
            if k >= st.region_count:
                stack.pop()
                continue
            stack[-1][1] = k + 1
            reg = self.ir.regions[st.first_region + k]
            target = reg.initial_state
            if target == NONE:
                continue
            self._exec_action(reg.initial_action)
            self._enter_one(target)
            stack.append([target, 0])

    def _deepest_active_below(self, index: int) -> int:
        """The DEEPEST active state under `index` (NONE if there is none).

        Regions are scanned in DESCENDING order: exit is the reverse of entry.
        """
        found = NONE
        node = index
        step = 0
        while step < MAX_WALK_STEPS:
            step += 1
            st = self.ir.states[node]
            next_ = NONE
            for k in range(st.region_count - 1, -1, -1):
                aday = self.active[st.first_region + k]
                if aday != NONE:
                    next_ = aday
                    break
            if next_ == NONE:
                return found
            found = next_
            node = next_
        return found

    def _exit_below(self, index: int) -> None:
        """Closes everything under `index`; `index` itself stays."""
        step = 0
        while step < MAX_WALK_STEPS:
            step += 1
            target = self._deepest_active_below(index)
            if target == NONE:
                return
            self._exit_one(target)

    def _enter_path(self, target: int, top: int) -> None:
        """Enters the states between `top` and `target`, outside in.

        When a state on the path is ORTHOGONAL, the regions the path DOES NOT
        GO THROUGH are activated with their defaults: entering an orthogonal
        state starts all of its regions (14.2.3.2, printed p.307). The regions
        of `target` itself are not opened here; `_descend` does that.
        """
        zincir: List[int] = []
        s = target
        step = 0
        while s != top and s != NONE and step <= MAX_WALK_STEPS:
            zincir.append(s)
            s = self._parent(s)
            step += 1
        zincir.reverse()
        for i, cur in enumerate(zincir):
            self._enter_one(cur)
            st = self.ir.states[cur]
            if st.region_count <= 0:
                continue
            if i + 1 >= len(zincir):
                # The LAST item is `target`; `_descend` opens its regions. Opening them
                # here too would enter the substates of the target TWICE (the same entry
                # shows up twice in the trace).
                continue
            gecilen = self._region_of(zincir[i + 1])
            for r in self.ir.regions_of(cur):
                if r == gecilen:
                    continue
                reg = self.ir.regions[r]
                if reg.initial_state == NONE:
                    continue
                self._exec_action(reg.initial_action)
                self._enter_one(reg.initial_state)
                self._activate_below(reg.initial_state)

    def _leaf_of(self, index: int) -> int:
        """The REPRESENTATIVE leaf for display: always descend the FIRST region."""
        cur = index
        step = 0
        while cur != NONE and step <= MAX_WALK_STEPS:
            step += 1
            st = self.ir.states[cur]
            if st.region_count <= 0:
                return cur
            next_ = self.active[st.first_region]
            if next_ == NONE or next_ == cur:
                return cur
            cur = next_
        return cur

    def _descend(self, s: int) -> int:
        self._activate_below(s)
        return self._leaf_of(s)

    def _resolve_history(self, h: int) -> int:
        """Turns a history pseudostate into a real target and runs the entry chain."""
        st = self.ir.states[h]
        bolge = st.region
        sahip = st.parent
        stored = self.history[bolge] if bolge != REGION_NONE else NONE
        if stored == NONE:
            stored = st.history_default
        if stored == NONE and bolge != REGION_NONE:
            stored = self.ir.regions[bolge].initial_state
        if stored == NONE:
            return sahip                       # safety: the owner of the region
        self._enter_path(stored, sahip)
        if st.kind == KIND_HIST_DEEP:
            self._restore_deep(stored)
            return self._leaf_of(stored)
        return self._descend(stored)

    def _restore_deep(self, index: int) -> None:
        """Deep history: restores EVERY REGION UNDER `index` from the record.

        REFERENCE: 14.2.3.6 is the FinalState clause, NOT history. The right
        place is 14.2.3.4.5 (printed p.310), the "Deep history entry" item:
        the rule is the same as for shallow history, except that "the rule is
        applied recursively to all levels in the active state configuration
        below this". So the record is followed AT EVERY DEPTH and in every region.

        THE BUG WE HIT: only the record of the FIRST region was followed, and
        `_activate_below` was called for the others -- and that opens the
        DEFAULT rather than the record. The result: the remembered subtree of
        the second and later regions was lost, and the initial effect of that
        region -- a real side effect in embedded code -- ran again.

        The walk is BREADTH FIRST: entry goes outside in, regions in ascending
        order, and two runs give the same order.
        """
        queue: List[int] = [index]
        step = 0
        while queue and step < MAX_WALK_STEPS:
            step += 1
            cur = queue.pop(0)
            for r in self.ir.regions_of(cur):
                record = self.history[r]
                reg = self.ir.regions[r]
                if record == NONE:
                    if reg.initial_state == NONE:
                        continue
                    self._exec_action(reg.initial_action)
                    self._enter_one(reg.initial_state)
                    self._activate_below(reg.initial_state)
                    continue
                self._enter_one(record)
                queue.append(record)

    def _land(self, target: int) -> int:
        """Determines the real leaf state once the transition target is reached."""
        kind = self.ir.states[target].kind
        if kind in (KIND_HIST_SHALLOW, KIND_HIST_DEEP):
            return self._resolve_history(target)
        if kind == KIND_TERMINATE:
            self.terminated = True             # UML terminate: the machine ends
            return target
        return self._descend(target)

    # ---------------------------------------------------------- transitions #

    def _take(self, tran) -> None:
        self._note("T", self.transition_label(tran))
        if tran.kind == TKIND_INTERNAL:
            # AN INTERNAL TRANSITION DOES NOT CHANGE THE STATE, so it DOES NOT GIVE
            # BIRTH TO A NEW COMPLETION EVENT.
            #
            # THE BUG WE HIT: the completion loop ran unconditionally after every
            # dispatch. AFTER the completion transition of a state had been skipped
            # because of its guard, an unrelated internal transition arrived and
            # re-fired the OLD completion event, moving the machine into another
            # state.
            #
            # It clears the flag OF ITS OWN REGION ONLY. Clearing a machine-wide
            # flag would also destroy the pending completion event of another region.
            # olayini da yok ederdi.
            region = self._region_of(tran.source)
            if region != REGION_NONE:
                self.completion_pending[region] = False
            self._exec_action(tran.action)
            return

        # UML 2.5.1, 14.2.3.7: when a terminate pseudostate is entered, the machine
        # EXITS no state; the exit behaviours are not run.
        if self.ir.states[tran.target].kind == KIND_TERMINATE:
            self._exec_action(tran.action)
            self.terminated = True
            self.completion_pending = [False] * self.ir.region_count
            self.state = tran.target
            return

        top = self._lca(tran.source, tran.target)
        if tran.kind == TKIND_EXTERNAL and (top == tran.source or top == tran.target):
            top = NONE if top == NONE else self._parent(top)

        # THE EXIT STARTS FROM THE AFFECTED REGION UNDER `top`.
        #
        # Starting from the source's own region is wrong: for a LOCAL transition
        # (Work --JUMP--> W2, say) the source is `top` itself and the loop never
        # turns; yet Work's current substate (W1) MUST BE CLOSED. Which region is
        # affected is told by the TARGET's path: the region of the vertex right
        # below `top`.
        child_node = tran.target
        step = 0
        while (self._parent(child_node) != top and self._parent(child_node) != NONE
               and step <= MAX_WALK_STEPS):
            child_node = self._parent(child_node)
            step += 1
        region = self._region_of(child_node)
        s = self.active[region] if region != REGION_NONE else NONE
        step = 0
        while s != top and s != NONE and step <= MAX_WALK_STEPS:
            step += 1
            self._exit_below(s)
            self._exit_one(s)
            s = self._parent(s)

        self._exec_action(tran.action)
        self._enter_path(tran.target, top)
        if tran.fork_targets:
            self._enter_forked(tran.target, tran.fork_targets)
            self.state = self._leaf_of(tran.target)
        else:
            self.state = self._land(tran.target)
        # The flags are set inside `_enter_one`, PER REGION ENTERED.
        if self.terminated:
            self.completion_pending = [False] * self.ir.region_count

    def _completed(self, index: int) -> bool:
        """Has the COMPLETION event of a composite state been born?

        UML 2.5.1, 14.2.3.8.3 (printed p.315): "if the State is a composite
        State, all its orthogonal Regions have reached a FinalState". In an
        orthogonal state the condition is an AND: one region reaching a final
        state is not enough.
        """
        st = self.ir.states[index]
        if st.region_count <= 0:
            return True
        for r in self.ir.regions_of(index):
            etkin = self.active[r]
            if etkin == NONE or self.ir.states[etkin].kind != KIND_FINAL:
                return False
        return True

    def _join_ready(self, tran) -> bool:
        """Are all the sources of the join active right now?"""
        for source in tran.join_sources:
            region = self._region_of(source)
            if region == REGION_NONE or self.active[region] != source:
                return False
        return True

    def _enter_forked(self, sahip: int, hedefler: List[int]) -> None:
        """FORK: enters the named regions EXPLICITLY, the rest by default.

        UML 2.5.1, 14.2.3.7 (printed p.313): a fork splits "an incoming
        Transition into two or more Transitions terminating on Vertices in
        orthogonal Regions of a composite State". Unnamed regions still start;
        entering an orthogonal state starts ALL of its regions.
        """
        kapsanan = set()
        for target in hedefler:
            child_node = target
            step = 0
            while (self._parent(child_node) != sahip and self._parent(child_node) != NONE
                   and step <= MAX_WALK_STEPS):
                child_node = self._parent(child_node)
                step += 1
            kapsanan.add(self._region_of(child_node))
            self._enter_path(target, sahip)
            self._activate_below(target)
        for r in self.ir.regions_of(sahip):
            if r in kapsanan:
                continue
            reg = self.ir.regions[r]
            if reg.initial_state == NONE:
                continue
            self._exec_action(reg.initial_action)
            self._enter_one(reg.initial_state)
            self._activate_below(reg.initial_state)

    def _select(self, region: int, event_index: int):
        """Walks up from the leaf of a region and finds the FIRST enabled transition."""
        s = self.active[region]
        step = 0
        while s != NONE and step <= MAX_WALK_STEPS:
            step += 1
            first, count = self.ir.tran_slice.get(s, (0, 0))
            for i in range(count):
                tran = self.ir.transitions[first + i]
                if tran.event != event_index:
                    continue
                if (event_index == COMPLETION
                        and self.ir.states[s].kind == KIND_COMPOSITE
                        and not self._completed(s)
                        and not tran.join_sources):
                    continue
                # JOIN: all incoming segments must be active AT THE SAME TIME.
                # UML 2.5.1, 14.2.3.7 (printed p.313): "all incoming
                # Transitions have to complete before execution can
                # continue through an outgoing Transition."
                if tran.join_sources and not self._join_ready(tran):
                    continue
                if not self._guard(tran.guard):
                    continue
                return s, tran
            s = self._parent(s)
        return NONE, None

    def _try_event(self, event_index: int) -> bool:
        """Offers the event to EVERY ACTIVE REGION and drops conflicting transitions.

        UML 2.5.1, 14.2.3.9.4: a transition leaving a deeper state CONFLICTS
        with one leaving a state that contains it, and priority goes to the
        DEEPER one. In orthogonal regions two regions may pick the same outer
        transition, or one may pick an outer and the other a deeper one;
        processing both would close the state twice.

        The non-conflicting ones are processed in ASCENDING region order. UML
        does not define that order; the tool fixes it and writes it in the header.
        """
        secimler = []                      # (region, source, transition)
        for r in range(self.ir.region_count):
            if self.active[r] == NONE:
                continue
            source, tran = self._select(r, event_index)
            if tran is None:
                continue
            secimler.append((r, source, tran))

        if not secimler:
            return False

        # If two regions picked the same transition it is processed ONCE.
        benzersiz = []
        gorulen = set()
        for r, source, tran in secimler:
            if tran.index in gorulen:
                continue
            gorulen.add(tran.index)
            benzersiz.append((r, source, tran))

        # When the source of one choice is a PROPER ANCESTOR of another choice's
        # source, the outer one is dropped: priority goes to the deeper one.
        kalan = []
        for r, source, tran in benzersiz:
            if any(self._is_ancestor(source, other_one)
                   for _r2, other_one, _t2 in benzersiz if other_one != source):
                continue
            kalan.append((r, source, tran))

        islendi = False
        for r, _source, tran in kalan:
            # TERMINATE STOPS EVERYTHING.
            #
            # UML 2.5.1, 14.2.3.7: once a terminate pseudostate is entered the
            # machine STOPS executing; no state is exited. Processing the chosen
            # transitions of the other regions after one region has killed the
            # machine would mean running exit/effect/entry on a dead machine and
            # reporting a state entered AFTER it had ended.
            # bildirmek demekti.
            if self.terminated:
                break
            # A transition processed earlier may already have closed this region.
            if self.active[r] == NONE and tran.kind != TKIND_INTERNAL:
                continue
            self._take(tran)
            islendi = True
        return islendi

    def _run_to_completion(self) -> None:
        """Processes pending completion events until the configuration is stable.

        The loop is not UNCONDITIONAL; it depends on the `completion_pending`
        flag: a completion event is born WHEN A STATE IS ENTERED (UML 2.5.1,
        14.2.3.8.3) and is consumed once processed -- whether or not a
        transition is taken. Looping unconditionally re-fired a consumed event
        on every later dispatch.
        """
        # THE LIMIT IS PER REGION.
        #
        # Each step of the loop processes the pending completion event of A
        # SINGLE REGION. A fixed machine-wide limit was shared between the
        # regions as their number grew and ran out after 8: the completion
        # transitions drawn on the diagram were NEVER taken. The generated C/C++
        # does not even report it. Scaling with the region count gives every
        # region its own budget while keeping the guard against a cyclic model.
        # da yerinde kalir.
        sinir = MAX_RTC_STEPS * max(1, self.ir.region_count)
        steps = 0
        while not self.terminated:
            region = REGION_NONE
            for r in range(self.ir.region_count):
                if self.completion_pending[r]:
                    region = r
                    break
            if region == REGION_NONE:
                return
            if steps >= sinir:
                # REPORT RATHER THAN BREAKING SILENTLY. The previous version left the
                # loop and carried on; the model stayed unstable and the user was told
                # nothing.
                self.rtc_overflow = True
                self._emit("error",
                           "run-to-completion limit (%d steps) reached: the "
                           "model has a cycle of completion transitions"
                           % sinir)
                return
            self.completion_pending[region] = False
            if self.active[region] != NONE:
                _source, tran = self._select(region, COMPLETION)
                if tran is not None:
                    self._take(tran)
            steps += 1

    # ------------------------------------------------------------------- API #

    def start(self) -> None:
        if self.started:
            return
        self.started = True
        self.terminated = False
        self.state = NONE
        self.active = [NONE] * self.ir.region_count
        self.history = [NONE] * self.ir.region_count
        self.completion_pending = [False] * self.ir.region_count
        self.rtc_overflow = False
        self.deferred_pool = []
        self.defer_overflow = False
        for r in self.ir.root_regions:
            reg = self.ir.regions[r]
            self._exec_action(reg.initial_action)
            self._enter_path(reg.initial_state, NONE)
            leaf = self._land(reg.initial_state)
            if self.state == NONE:
                self.state = leaf
        if self.terminated:
            self.completion_pending = [False] * self.ir.region_count
        self._run_to_completion()
        self._drain_deferred()

    def _is_deferred(self, event_index: int) -> bool:
        """Does even ONE state in the active configuration defer this event?

        UML 2.5.1, 14.2.3.4.4 (printed p.309): "An Event may be deferred by
        a composite State or submachine States, in which case it remains
        deferred as long as the composite State remains in the active
        configuration." So not only the leaf but THE WHOLE CHAIN is examined.

        """
        for index in self.active_indices():
            if event_index in self.ir.states[index].deferred:
                return True
        return False

    def _defer(self, event_index: int) -> None:
        if len(self.deferred_pool) >= MAX_DEFERRED:
            # DO NOT DROP IT SILENTLY. A lost event would make it impossible to
            # explain why the model does not behave as expected.
            self.defer_overflow = True
            self._emit("error",
                       "the deferred-event pool is full (%d); the "
                       "occurrence of '%s' was dropped"
                       % (MAX_DEFERRED, self.ir.events[event_index]))
            return
        self.deferred_pool.append(event_index)
        self._emit("defer", self.ir.events[event_index])

    def _drain_deferred(self) -> None:
        """Takes the events that are no longer deferred out of the pool."""
        step = 0
        while step < MAX_DEFERRED * 2:
            if self.terminated:
                return                         # a terminated machine processes no event
            step += 1
            siradaki = None
            for index in self.deferred_pool:
                if not self._is_deferred(index):
                    siradaki = index
                    break
            if siradaki is None:
                return
            self.deferred_pool.remove(siradaki)
            self._emit("recall", self.ir.events[siradaki])
            if self._try_event(siradaki):
                self._run_to_completion()

    def dispatch(self, event: str) -> bool:
        if not self.started:
            self.start()
        if self.terminated:
            return False
        index = self._event_index.get(event)
        if index is None or index == COMPLETION:
            return False
        # THE TRIGGER IS TRIED FIRST. The spec says so explicitly: when a deferred
        # event type is the trigger of a transition WHOSE SOURCE is the deferring
        # state, the transition WINS ("a kind of override option"). Looking at the
        # deferral first would mean that transition never fired.
        handled = self._try_event(index)
        if handled:
            self._run_to_completion()
            self._drain_deferred()
            return True
        if self._is_deferred(index):
            self._defer(index)
            return True                        # the event is CONSUMED: it stays in the pool
        return False

    def do_activity(self) -> None:
        if self.terminated:
            return              # a terminated machine runs no behaviour
        for index in self.active_indices():
            st = self.ir.states[index]
            if st.do:
                self._emit("D", st.name)

    # ----------------------------------------------------------------- state #

    def active_indices(self) -> List[int]:
        """ALL active states; inside out, in region order.

        On a non-orthogonal model this is exactly the old chain running from
        the leaf up to the root.
        """
        out: List[int] = []
        for r in range(self.ir.region_count - 1, -1, -1):
            index = self.active[r]
            if index != NONE and index not in out:
                out.append(index)
        return out

    @property
    def state_name(self) -> str:
        return self._name(self.state)

    def active_chain(self) -> List[str]:
        """The model ids of the active states (for highlighting on the canvas)."""
        return [self.ir.states[i].model_id for i in self.active_indices()]

    def is_terminated(self) -> bool:
        if self.terminated:
            return True
        for r in self.ir.root_regions:
            index = self.active[r]
            if index == NONE:
                return False
            st = self.ir.states[index]
            if st.kind != KIND_FINAL:
                return False
        return bool(self.ir.root_regions)
