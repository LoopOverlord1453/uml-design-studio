"""C++11 hierarchical state machine generator.

It uses the *same* IR and the *same* runtime algorithm as the C generator;
only the packaging differs:
  * the tables are `constexpr` in an anonymous namespace inside the .cpp,
  * state/event ids are an `enum class` (type safe),
  * no exceptions, no RTTI, no dynamic memory - suitable for embedded targets.
"""

from __future__ import annotations

from typing import Dict, List

from ..core.naming import pascal as _pascal
from .c_generator import (TOOL_NAME, TOOL_VERSION, align_enum,
                          allman,
                          as_expression, as_statement,
                          _single_line, c_comment, indent_block)
from .ir import (Ir, KIND_CHOICE, KIND_COMPOSITE, KIND_FINAL, KIND_HIST_DEEP,
                 KIND_HIST_SHALLOW, KIND_SIMPLE, KIND_TERMINATE, NONE, REGION_NONE,
                 TKIND_EXTERNAL, TKIND_INTERNAL, TKIND_LOCAL, build_ir)

MISRA_NOTE_CPP = [
    "// @par MISRA C++:2008 / AUTOSAR C++14 compliance",
    "// - No dynamic memory, no exceptions, no RTTI, no recursion",
    "//   (M18-4-1, M15-0-1, M5-2-2, M17-0-5).",
    "// - All integer types are the fixed-width types of <cstdint> (M3-9-2).",
    "// - Every switch has a default clause (M6-4-6); no implicit conversions",
    "//   between unrelated types (M5-0-x).",
    "// - Copying is deleted: a state machine instance owns its configuration",
    "//   and must not be duplicated silently (M12-8-1).",
    "// - Known deviations: multiple return statements (M6-6-5), used for",
    "//   defensive early exits; lookup tables are static file-scope const so",
    "//   they live in flash rather than on the stack.",
]


#: The name conversion lives in the core and the validator uses the SAME
#: function (see app/core/naming.py). Re-exported here for compatibility.
pascal = _pascal


def section(title: str, indent: str = "") -> str:
    """A section heading of the SAME width as in the C generator.

    The user asked for "the comment lines and the code layout to be the same
    in C and C++ too". The C side had 79-character bands such as
    `/* ---...--- states -- */`; C++ had none and ran on without sections.
    """
    kuyruk = " %s --" % title
    width = 79 - len(indent)
    dolgu = "-" * max(3, width - len("// ") - len(kuyruk))
    return "%s// %s%s" % (indent, dolgu, kuyruk)


def doc(lines: List[str], indent: str = "") -> List[str]:
    """A Doxygen block: `/** ... */`.

    The C side wrote @brief/@param/@return for every function; the C++ side
    made do with a one-line `///`. The blocks are produced here so the
    documentation has the same depth on both sides.
    """
    if len(lines) == 1:
        return ["%s/** %s */" % (indent, lines[0])]
    out = ["%s/**" % indent]
    out += ["%s * %s" % (indent, ln) if ln else "%s *" % indent
            for ln in lines]
    out.append("%s */" % indent)
    return out


def banner(ir: Ir, filename: str, kind: str) -> List[str]:
    # THE GENERATION TIME IS NOT WRITTEN.
    #
    # A second-resolution stamp in the header made the file different on every
    # run even when the model had NOT changed at all: the write-to-workspace
    # step rewrote the file, every generated file looked "modified" in the git
    # working tree, and the diff view showed nothing but a one-line date
    # change. Producing THE SAME source from the same model is essential for
    # version control.
    lines = [
        "//" + "=" * 76,
        "// @file    %s" % filename,
        "// @brief   '%s' state machine -- %s" % (ir.name, kind),
        "//",
        "// GENERATED FILE -- DO NOT EDIT BY HAND.",
        "// Generator: %s v%s" % (TOOL_NAME, TOOL_VERSION),
        "// Standard: ISO/IEC 14882:2011 (C++11); no exceptions, no RTTI,",
        "//           no dynamic memory.",
        "//",
    ]
    lines += MISRA_NOTE_CPP
    if ir.description:
        lines += ["//", "// %s" % c_comment(ir.description)]
    lines += ["//" + "=" * 76, ""]
    return lines


class CppGenerator:
    def __init__(self, ir: Ir) -> None:
        self.ir = ir
        self.ns = ir.prefix
        self.cls = pascal(ir.name)

    def _demo_required_note(self) -> List[str]:
        """Counts the functions that come FROM THE MODEL in the MCU example."""
        needed = self.ir.required_functions()
        if not needed:
            return ["// This model's behaviours are self-contained: they call",
                    "// nothing outside the generated code.", ""]
        L = ["// The model's behaviours call these. Write them yourself; the",
             "// state machine calls them at the moments the diagram shows.",
             "//"]
        for sembol in needed:
            L.append("//   %s" % sembol.summary())
            L.append("//     -> %s" % ", ".join(sembol.sites))
        L += [
            "//",
            "// They are NOT declared here on purpose: their real signatures",
            "// live in your project, and a guessed prototype would compile",
            "// yet disagree with the definition. Include your own header.",
            "",
        ]
        return L

    def _required_block(self) -> List[str]:
        """The symbols the user must SUPPLY (same rule as on the C side).

        See c_generator.CGenerator._required_block: NO prototype is written,
        only documentation; the model carries no type information and an
        invented declaration could silently disagree with the real signature.
        """
        needed = self.ir.required_functions()
        L = [section("you must provide")]
        rows = ["@par Functions this machine expects from your project",
                    "",
                    "The action and guard bodies in the model call the symbols",
                    "listed below. They are NOT generated; declare and define",
                    "them in your own sources (or include the header that",
                    "declares them) before linking."]
        if not needed:
            rows += ["",
                         "This model calls none: every behaviour is"
                         " self-contained."]
        else:
            rows.append("")
            for sembol in needed:
                rows.append("- %s" % sembol.summary())
                rows.append("    used by: %s" % ", ".join(sembol.sites))
            if self.ir.has_context():
                rows += ["",
                             "The context type '%s' is also yours: every"
                             % self.ctx_type(),
                             "action and guard sees it as 'ctx'."]
        L += doc(rows)
        L.append("")
        return L

    def ctx_type(self) -> str:
        return self.ir.context_type if self.ir.has_context() else "void"

    # ------------------------------------------------------------------ .hpp #

    def header(self) -> str:
        ir = self.ir
        cls = self.cls
        L: List[str] = []
        L += banner(ir, "%s.hpp" % cls, "class interface")
        guard = "%s_%s_HPP" % (self.ns.upper(), cls.upper())
        L += ["#ifndef %s" % guard, "#define %s" % guard, ""]
        L += ["#include <cstdint>", ""]
        if ir.user_includes:
            L += ["// User-supplied headers (from the model settings)."]
            L += list(ir.user_includes)
            L += [""]
        L += ["namespace %s {" % self.ns, ""]

        L += self._required_block()
        L += doc(["@brief Hierarchical state machine '%s'." % ir.name,
                  "",
                  "Create as many instances as you need; the lookup tables",
                  "are shared and read-only. Copying is deleted on purpose:",
                  "an instance owns its running configuration."])
        L += ["class %s" % cls, "{", "public:"]

        L += doc(["@brief User context type; appears as 'ctx' inside every",
                  "       action and guard body."], "    ")
        L += ["    using Context = %s;" % self.ctx_type(), ""]

        L += [section("dimensions", "    ")]
        L += doc(["@brief Number of vertices in the state table."], "    ")
        L += ["    static constexpr std::uint8_t kStateCount = %uU;"
              % ir.state_count, ""]
        L += doc(["@brief Number of event identifiers, including the",
                  "       internal one."], "    ")
        L += ["    static constexpr std::uint8_t kEventCount = %uU;"
              % ir.event_count, ""]
        L += doc(["@brief Number of regions; the active configuration",
                  "       holds one leaf per region."], "    ")
        L += ["    static constexpr std::uint8_t kRegionCount = %uU;"
              % ir.region_count, ""]
        L += doc(["@brief Deepest nesting level; bounds the entry-path"
                  " buffer."], "    ")
        L += ["    static constexpr std::uint8_t kMaxDepth = %uU;"
              % ir.max_depth, ""]
        L += doc(["@brief Safety bound on the entry/exit walks."], "    ")
        L += ["    static constexpr std::uint16_t kMaxWalkSteps = 4096U;", ""]
        if ir.has_deferred():
            L += doc(["@brief Retained deferred event occurrences;",
                      "       a full pool is reported, never dropped",
                      "       silently."], "    ")
            L += ["    static constexpr std::uint8_t kDeferPoolSize = 16U;", ""]
        L += doc(["@brief Safety bound on the completion loop: 16 steps PER REGION.",
                  "",
                  "@note  The loop services ONE region per step, so a",
                  "       machine-wide budget would be shared out between the",
                  "       regions and run out once several were active, and",
                  "       completion Transitions drawn in the diagram would",
                  "       never be taken. Scaling the bound with the region",
                  "       count gives every region its own budget while still",
                  "       bounding a cyclic model."],
                 "    ")
        L += ["    static constexpr std::uint16_t kMaxRunToCompletionSteps ="]
        L += ["        static_cast<std::uint16_t>(16U * kRegionCount);", ""]

        L += [section("states", "    ")]
        L += doc(["@brief Identifiers of the states and pseudostates.",
                  "",
                  "The values are indices into the generated lookup tables,",
                  "so they must not be reordered by hand."], "    ")
        L += ["    enum class State : std::uint8_t", "    {"]
        rows = []
        for st in ir.states:
            kind_txt = {KIND_SIMPLE: "simple", KIND_COMPOSITE: "composite",
                        KIND_FINAL: "final", KIND_CHOICE: "choice/junction",
                        KIND_TERMINATE: "terminate",
                        KIND_HIST_SHALLOW: "shallow history",
                        KIND_HIST_DEEP: "deep history"}[st.kind]
            # The user's NOTE goes into the code as well (see the C generator).
            aciklama = "%s, depth %d" % (kind_txt, st.depth)
            if st.note:
                aciklama = "%s -- %s" % (aciklama, _single_line(st.note))
            rows.append(("        %s" % st.name, "= %uU," % st.index,
                             "///< %s" % aciklama))
        rows.append(("        None", "= 255U", "///< invalid"))
        L += align_enum(rows)
        L += ["", "    };", ""]

        L += [section("events", "    ")]
        L += doc(["@brief Identifiers of the events accepted by the machine."],
                 "    ")
        L += ["    enum class Event : std::uint8_t", "    {"]
        rows = []
        for i, ev in enumerate(ir.events):
            note = "///< internal: the completion event" if i == 0 else ""
            rows.append(("        %s" % self._ev_name(ev),
                             "= %uU," % i, note))
        rows.append(("        Invalid", "= 255U", ""))
        L += align_enum(rows)
        L += ["", "    };", ""]

        L += [section("API", "    ")]
        L += doc(["@brief  Creates an instance. Does NOT start the machine"
                  " yet.",
                  "@param  ctx  User context handed to every action and"
                  " guard.",
                  "@note   Call start() afterwards to run the initial"
                  " transition."], "    ")
        L += ["    explicit %s(Context* ctx = nullptr) noexcept;" % cls, ""]

        L += doc(["@brief Copying is deleted: an instance owns its running",
                  "       configuration and must not be duplicated silently",
                  "       (MISRA C++ M12-8-1)."], "    ")
        L += ["    %s(const %s&)            = delete;" % (cls, cls)]
        L += ["    %s& operator=(const %s&) = delete;" % (cls, cls), ""]

        L += doc(["@brief  Runs the initial transition and the entry"
                  " behaviours.",
                  "@note   Calling it twice has no effect; the machine"
                  " starts once."], "    ")
        L += ["    void start() noexcept;", ""]

        L += doc(["@brief  Processes one event to completion (UML"
                  " run-to-completion).",
                  "",
                  "Searches the active state and its ancestors for an"
                  " enabled",
                  "transition, executes the exit chain, the effect and the",
                  "entry chain, then resolves any completion transitions.",
                  "",
                  "@param  event  Event identifier from Event.",
                  "@retval true   The event triggered a transition.",
                  "@retval false  The event is not handled in this"
                  " configuration."], "    ")
        L += ["    bool dispatch(Event event) noexcept;", ""]

        L += doc(["@brief  Runs the do-behaviour of the active state and its",
                  "        ancestors.",
                  "@note   Call this once per super-loop pass or timer"
                  " tick."], "    ")
        L += ["    void doActivity() noexcept;", ""]

        L += doc(["@brief  Tests whether the machine is in a state,"
                  " ancestors included.",
                  "@param  state  State to test against.",
                  "@return true when @p state is the active state or one of",
                  "        its parents."], "    ")
        L += ["    bool isIn(State state) const noexcept;", ""]

        L += doc(["@brief  Reports whether the machine has stopped.",
                  "@return true after a terminate pseudostate or the final",
                  "        state of the root region."], "    ")
        L += ["    bool isTerminated() const noexcept;", ""]
        # NO SINGLE-LINE BODY IS WRITTEN: the rule that holds throughout the
        # generated code (the spirit of MISRA C++ M6-3-1) applies here too.
        L += doc(["@brief  Returns the active leaf state.",
                  "@return The active state, or State::None before start()."],
                 "    ")
        L += ["    State state() const noexcept"]
        L += ["    {"]
        L += ["        return state_;"]
        L += ["    }", ""]

        L += doc(["@brief  Returns the user context given to the constructor.",
                  "@return The context pointer; nullptr when none was set."],
                 "    ")
        L += ["    Context* context() const noexcept"]
        L += ["    {"]
        L += ["        return ctx_;"]
        L += ["    }", ""]

        L += doc(["@brief  Human-readable state name, for logging and",
                  "        debugging.",
                  "@param  state  State identifier.",
                  "@return A static string; never NULL."], "    ")
        L += ["    static const char* stateName(State state) noexcept;", ""]
        L += doc(["@brief  Human-readable event name, for logging and",
                  "        debugging.",
                  "@param  event  Event identifier.",
                  "@return A static string; never NULL."], "    ")
        L += ["    static const char* eventName(Event event) noexcept;", ""]

        L += ["private:"]
        L += [section("internal helpers", "    ")]
        gizli = [
            ("    bool          evaluateGuard(std::int16_t id) noexcept;",
             "Evaluates the guard with the given id; true when absent."),
            ("    void          executeAction(std::int16_t id) noexcept;",
             "Executes the effect with the given id; no-op when absent."),
            ("    void          executeEntry(std::uint8_t state) noexcept;",
             "Runs the entry behaviour of one state."),
            ("    void          timersStart(std::uint8_t state) noexcept;",
             "Starts the after() timers of a state."),
            ("    void          timersCancel(std::uint8_t state) noexcept;",
             "Cancels the after() timers of a state."),
            ("    void          executeExit(std::uint8_t state) noexcept;",
             "Runs the exit behaviour of one state."),
            ("    void          executeDoActivity(std::uint8_t state)"
             " noexcept;",
             "Runs the do-behaviour of one state."),
            ("    void          enterPath(std::uint8_t target,"
             " std::uint8_t top) noexcept;",
             "Runs the entry chain from @p top down to @p target."),
            ("    void          enterOne(std::uint8_t state) noexcept;",
             "Activates one state and records it in its region."),
            ("    void          exitOne(std::uint8_t state) noexcept;",
             "Deactivates one state; its regions must already be empty."),
            ("    void          activateBelow(std::uint8_t state) noexcept;",
             "Opens the default substates of every region below a state."),
            ("    std::uint8_t  deepestActiveBelow(std::uint8_t state)"
             " const noexcept;",
             "Deepest active state below @p state."),
            ("    void          exitBelow(std::uint8_t state) noexcept;",
             "Closes everything below @p state."),
            ("    std::uint8_t  leafOf(std::uint8_t state) const noexcept;",
             "Representative leaf, following the first region."),
            ("    bool          completed(std::uint8_t state) const noexcept;",
             "Have ALL regions of a composite state reached a final state?"),
            ("    bool          select(std::uint8_t region, std::uint8_t event,"
             " std::uint8_t& outSource, std::uint16_t& outTransition) noexcept;",
             "First enabled transition seen from one region."),
            ("    std::uint8_t  descend(std::uint8_t state) noexcept;",
             "Descends into the default substates of composite states."),
            ("    void          takeTransition(std::uint16_t transition)"
             " noexcept;",
             "Executes one transition, named by its table index."),
        ]
        if ir.has_fork_join():
            gizli += [
                ("    bool          joinReady(std::uint16_t transition)"
                 " const noexcept;",
                 "Is every incoming segment of a join active right now?"),
                ("    void          enterForked(std::uint16_t transition)"
                 " noexcept;",
                 "Enters the regions a fork names; the rest start by default."),
            ]
        if ir.has_history():
            gizli.append(
                ("    std::uint8_t  resolveHistory(std::uint8_t h) noexcept;",
                 "Resolves a history pseudostate to a real state."))
        gizli += [
            ("    std::uint8_t  land(std::uint8_t target) noexcept;",
             "Determines the real leaf state once a target is reached."),
            ("    bool          tryEvent(std::uint8_t event) noexcept;",
             "Finds and executes the first enabled transition."),
            ("    void          clearCompletion() noexcept;",
             "Clears every pending completion flag."),
            ("    void          runToCompletion() noexcept;",
             "Resolves completion (event-less) transitions to a stable"
             " state."),
        ]
        if ir.has_deferred():
            gizli += [
                ("    bool          isDeferred(std::uint8_t event)"
                 " const noexcept;",
                 "Is this event type deferred by the active configuration?"),
                ("    void          defer(std::uint8_t event) noexcept;",
                 "Retains one occurrence in the deferred pool."),
                ("    void          drainDeferred() noexcept;",
                 "Replays retained occurrences that are no longer deferred."),
            ]
        for bildirim, aciklama in gizli:
            L += doc(["@brief %s" % aciklama], "    ")
            L += [bildirim, ""]

        L += [section("instance state", "    ")]
        uyeler = [("State", "state_;", "active leaf state"),
                  ("Context*", "ctx_;",
                   "user context, visible as 'ctx' in actions"),
                  ("bool", "started_;", "has start() been called"),
                  ("bool", "terminated_;",
                   "stopped by terminate or final"),
                  ("bool", "completionPending_[kRegionCount];",
                   "per region: a state was entered, completion is due"),
                  ("std::uint8_t", "active_[kRegionCount];",
                   "active leaf of each region (kNone = inactive)")]
        if ir.has_deferred():
            uyeler += [
                ("std::uint8_t", "deferred_[kDeferPoolSize];",
                 "retained deferred event occurrences"),
                ("std::uint8_t", "deferredCount_;",
                 "how many of them are in the pool"),
                ("bool", "deferOverflow_;",
                 "the pool was full and one was dropped"),
            ]
        if ir.has_history():
            uyeler.append(("std::uint8_t", "history_[kRegionCount];",
                           "last active substate per region"))
        tip_g = max(len(t) for t, _a, _y in uyeler)
        name_w = max(len(a) for _t, a, _y in uyeler)
        L += ["    %-*s %-*s  ///< %s" % (tip_g, tip, name_w, ad, yorum)
              for tip, ad, yorum in uyeler]
        # A BLANK LINE before the closing brace (same layout as the C side).
        L += ["", "};", ""]
        if ir.has_time_events():
            L += doc(['@brief  Starts a relative time trigger, written by YOU.',
                     '',
                     'The model uses after() triggers. UML treats a TimeEvent',
                     "as a trigger; KEEPING TIME IS NOT THE MACHINE'S JOB, so",
                     'the generated code only says when a timer should run and',
                     'when it must be cancelled. Start your own timer here and',
                     'post the matching event when it expires.',
                     '',
                     'The timer is started on ENTRY to the state and cancelled',
                     'on EXIT, so a pending timer can never fire into a state',
                     'that has already been left. An internal transition runs',
                     'neither, which is exactly what UML requires.'])
            L += ["void timerStart(%s& machine, std::uint8_t state,"
                  " std::uint8_t event, std::uint32_t delay);" % self.cls, ""]
            L += doc(['@brief  Cancels a relative time trigger, written by YOU.'])
            L += ["void timerCancel(%s& machine, std::uint8_t state,"
                  " std::uint8_t event);" % self.cls, ""]
        L += ["}  // namespace %s" % self.ns, ""]
        L += ["#endif  // %s" % guard, ""]
        return "\n".join(allman(L))

    def _ev_name(self, ev: str) -> str:
        return pascal(ev)

    # ------------------------------------------------------------------ .cpp #

    def source(self) -> str:
        ir = self.ir
        cls = self.cls
        L: List[str] = []
        L += banner(ir, "%s.cpp" % cls, "implementation")
        L += ['#include "%s.hpp"' % cls, ""]
        L += ["namespace %s {" % self.ns, "namespace {", ""]

        L += [section("internal constants"), ""]
        L += ["constexpr std::uint8_t kNone      = 255U;"]
        L += ["constexpr std::uint8_t kKindSimple    = 0U;"]
        L += ["constexpr std::uint8_t kKindComposite = 1U;"]
        L += ["constexpr std::uint8_t kKindFinal     = 2U;"]
        L += ["constexpr std::uint8_t kKindChoice    = 3U;  // choice + junction"]
        L += ["constexpr std::uint8_t kKindTerminate = 4U;"]
        L += ["constexpr std::uint8_t kKindHistoryShallow    = 5U;  // shallow history"]
        L += ["constexpr std::uint8_t kKindHistoryDeep  = 6U;  // deep history"]
        L += ["// Invalid region index: a number space of its own, separate"]
        L += ["// from the state index."]
        L += ["constexpr std::uint8_t kRegionNone = 255U;"]
        L += ["constexpr std::uint8_t kTransitionExternal  = 0U;"]
        L += ["constexpr std::uint8_t kTransitionInternal  = 1U;"]
        L += ["constexpr std::uint8_t kTransitionLocal     = 2U;", ""]

        L += [section("transition row"), ""]
        L += ["struct Transition {"]
        L += ["    std::uint8_t  source;"]
        L += ["    std::uint8_t  target;"]
        L += ["    std::uint8_t  event;"]
        L += ["    std::uint8_t  kind;"]
        L += ["    std::int16_t  guard;"]
        L += ["    std::int16_t  action;"]
        if ir.has_fork_join():
            L += ["    std::uint16_t extraFirst;  // index into kExtra[]"]
            L += ["    std::uint8_t  forkCount;   // fork segment targets"]
            L += ["    std::uint8_t  joinCount;   // join segment sources"]
        L += ["};", ""]

        # ---- tables
        L += [section("tables"), ""]
        L += ["constexpr std::uint8_t kParent[] = {"]
        for st in ir.states:
            v = "kNone" if st.parent == NONE else "%uU" % st.parent
            L += ["    %-8s // %-3u %s" % (v + ",", st.index, c_comment(st.name))]
        L += ["};", ""]

        kmap = {KIND_SIMPLE: "kKindSimple", KIND_COMPOSITE: "kKindComposite",
                KIND_FINAL: "kKindFinal", KIND_CHOICE: "kKindChoice",
                KIND_TERMINATE: "kKindTerminate",
                KIND_HIST_SHALLOW: "kKindHistoryShallow",
                KIND_HIST_DEEP: "kKindHistoryDeep"}
        L += ["constexpr std::uint8_t kKind[] = {"]
        for st in ir.states:
            L += ["    %-17s // %s" % (kmap[st.kind] + ",", c_comment(st.name))]
        L += ["};", ""]

        # The per-state "initial child" tables were REMOVED: the default entry is
        # now PER REGION. In an orthogonal state a single field would not have
        # been enough anyway.
        if ir.has_deferred():
            L += ["// Deferred event types per state, as a bit mask."]
            L += ["// UML 2.5.1, 14.2.3.4.4: an occurrence of a deferred type is"]
            L += ["// retained instead of being dispatched, until a state"]
            L += ["// configuration is reached where it is no longer deferred."]
            L += ["constexpr std::uint32_t kDeferMask[] = {"]
            for st in ir.states:
                maske = 0
                for e in st.deferred:
                    maske |= (1 << e)
                names = ", ".join(ir.events[e] for e in st.deferred) or "none"
                L += ["    0x%08XU,  // %-18s %s"
                      % (maske, c_comment(st.name), c_comment(names))]
            L += ["};", ""]

        L += ["// Region each vertex lives in."]
        L += ["constexpr std::uint8_t kStateRegion[] = {"]
        for st in ir.states:
            v = "kRegionNone" if st.region == REGION_NONE else "%uU" % st.region
            L += ["    %-12s // %-3u %s" % (v + ",", st.index, c_comment(st.name))]
        L += ["};", ""]

        L += ["// First region owned by a composite state."]
        L += ["constexpr std::uint8_t kStateFirstRegion[] = {"]
        for st in ir.states:
            v = ("kRegionNone" if st.first_region == REGION_NONE
                 else "%uU" % st.first_region)
            L += ["    %-12s // %s" % (v + ",", c_comment(st.name))]
        L += ["};", ""]

        L += ["// Number of regions owned by a state (0 = not composite)."]
        L += ["constexpr std::uint8_t kStateRegionCount[] = {"]
        for st in ir.states:
            L += ["    %-12s // %s" % ("%uU," % st.region_count,
                                       c_comment(st.name))]
        L += ["};", ""]

        L += ["// Owner of each region (kNone = a root region)."]
        L += ["constexpr std::uint8_t kRegionOwner[] = {"]
        for reg in ir.regions:
            v = "kNone" if reg.owner == NONE else "%uU" % reg.owner
            L += ["    %-8s // %-3u %s" % (v + ",", reg.index,
                                           c_comment(reg.name))]
        L += ["};", ""]

        L += ["// Default entry state of each region."]
        L += ["constexpr std::uint8_t kRegionInitial[] = {"]
        for reg in ir.regions:
            v = "kNone" if reg.initial_state == NONE else "%uU" % reg.initial_state
            L += ["    %-8s // %s" % (v + ",", c_comment(reg.name))]
        L += ["};", ""]

        L += ["// Effect id on the initial transition of each region."]
        L += ["constexpr std::int16_t kRegionInitialAction[] = {"]
        for reg in ir.regions:
            L += ["    %-6s // %s" % ("%d," % reg.initial_action,
                                      c_comment(reg.name))]
        L += ["};", ""]

        if ir.has_history():
            L += ["constexpr std::uint8_t kHistDefault[] = {"]
            for st in ir.states:
                v = "kNone" if st.history_default == NONE else "%uU" % st.history_default
                L += ["    %-8s // %s" % (v + ",", c_comment(st.name))]
            L += ["};", ""]

        L += ["const char* const kStateNames[] = {"]
        for st in ir.states:
            L += ['    "%s",' % st.name]
        L += ["};", ""]

        L += ["const char* const kEventNames[] = {"]
        for ev in ir.events:
            L += ['    "%s",' % ev]
        L += ["};", ""]

        if ir.has_fork_join():
            ekstra = ir.extra_table()
            L += ["// Fork targets and join sources, flattened."]
            L += ["constexpr std::uint8_t kExtra[] = {"]
            if ekstra:
                for i, v in enumerate(ekstra):
                    L += ["    %-6s // %-3u %s" % ("%uU," % v, i,
                                                   c_comment(ir.states[v].name))]
            else:
                L += ["    0U  // unused"]
            L += ["};", ""]

        L += ["constexpr Transition kTransitions[] = {"]
        if ir.tran_count == 0:
            # THE COMMENTS IN THE GENERATED CODE ARE ENGLISH. These three branches
            # (a model with no transition, a model with no effect, a pseudostate exit)
            # never ran on the sample model, so they had been left in Turkish; the C
            # generator already wrote English in the same places.
            if ir.has_fork_join():
                L += ["    { 0U, 0U, 0U, kTransitionExternal, -1, -1,"
                      " 0U, 0U, 0U }  // no transition in the model"]
            else:
                L += ["    { 0U, 0U, 0U, kTransitionExternal, -1, -1 }"
                      "  // no transition in the model"]
        else:
            for t in ir.transitions:
                kind_txt = {TKIND_EXTERNAL: "kTransitionExternal",
                            TKIND_INTERNAL: "kTransitionInternal",
                            TKIND_LOCAL: "kTransitionLocal"}[t.kind]
                if ir.has_fork_join():
                    L += ["    { %3uU, %3uU, %3uU, %-16s %3d, %3d, %3uU,"
                          " %3uU, %3uU },  // %s"
                          % (t.source, t.target, t.event, kind_txt + ",",
                             t.guard, t.action, t.extra_first,
                             len(t.fork_targets), len(t.join_sources),
                             c_comment(t.text))]
                else:
                    L += ["    { %3uU, %3uU, %3uU, %-16s %3d, %3d },  // %s"
                          % (t.source, t.target, t.event, kind_txt + ",",
                             t.guard, t.action, c_comment(t.text))]
        L += ["};", ""]

        L += ["constexpr std::uint16_t kTransitionFirst[] = {"]
        for st in ir.states:
            first, _c = ir.tran_slice.get(st.index, (0, 0))
            L += ["    %-6s // %s" % ("%uU," % first, c_comment(st.name))]
        L += ["};", ""]
        L += ["constexpr std::uint16_t kTransitionCount[] = {"]
        for st in ir.states:
            _f, cnt = ir.tran_slice.get(st.index, (0, 0))
            L += ["    %-6s // %s" % ("%uU," % cnt, c_comment(st.name))]
        L += ["};", ""]

        L += ["constexpr std::uint8_t kInitialState  = %uU;" % ir.root_initial]
        L += ["constexpr std::int16_t kInitialAct    = %d;" % ir.root_initial_action, ""]

        L += [section("file-scope helpers"), ""]
        L += doc(['@brief  Is @p maybe a PROPER ancestor of @p node?'])
        L += ["bool isAncestorOf(std::uint8_t maybe, std::uint8_t node) noexcept"]
        L += ["{"]
        L += ["    std::uint8_t current = kParent[node];"]
        L += ["    std::uint8_t steps = 0U;"]
        L += ["", "    while ((current != kNone) && (steps <= 64U)) {"]
        L += ["        if (current == maybe) {", "            return true;", "        }"]
        L += ["        current = kParent[current];"]
        L += ["        ++steps;"]
        L += ["    }"]
        L += ["    return false;"]
        L += ["}", ""]
        L += doc(['@brief  Nesting depth of a state; the root region is 0.',
                 '@param  state  State identifier.',
                 '@return How many parents the state has.'])
        L += ["std::uint8_t depthOf(std::uint8_t state) noexcept"]
        L += ["{"]
        L += ["    std::uint8_t depth = 0U;"]
        L += ["    std::uint8_t current = state;"]
        L += ["    while ((current != kNone) && (depth <= %s::kMaxDepth)) {" % cls]
        L += ["        current = kParent[current];"]
        L += ["        ++depth;"]
        L += ["    }"]
        L += ["    return depth;"]
        L += ["}", ""]

        L += doc(['@brief  Lowest common ancestor of two states.',
                 '@param  a  First state.',
                 '@param  b  Second state.',
                 '@return The shared parent, or kNone for the root region.'])
        L += ["std::uint8_t leastCommonAncestorOf(std::uint8_t a, std::uint8_t b) noexcept"]
        L += ["{"]
        L += ["    std::uint8_t depthA = depthOf(a);"]
        L += ["    std::uint8_t depthB = depthOf(b);"]
        L += ["    while (depthA > depthB) {", "        a = kParent[a];",
              "        --depthA;", "    }"]
        L += ["    while (depthB > depthA) {", "        b = kParent[b];",
              "        --depthB;", "    }"]
        L += ["    while (a != b) {"]
        L += ["        if ((a == kNone) || (b == kNone)) {", "            return kNone;", "        }"]
        L += ["        a = kParent[a];"]
        L += ["        b = kParent[b];"]
        L += ["    }"]
        L += ["    return a;"]
        L += ["}", ""]
        L += ["}  // namespace", ""]

        L += self._members()
        L += ["}  // namespace %s" % self.ns, ""]
        return "\n".join(allman(L))

    # -------------------------------------------------------------- members #

    def _ctx_preamble(self) -> List[str]:
        return [
            "    Context* ctx = ctx_;",
            "    static_cast<void>(ctx);",
        ]

    def _members(self) -> List[str]:
        ir = self.ir
        cls = self.cls
        L: List[str] = []

        L += ["%s::%s(Context* ctx) noexcept" % (cls, cls)]
        L += ["    : state_(State::None), ctx_(ctx), started_(false), terminated_(false),"]
        if ir.has_deferred():
            L += ["      completionPending_(), active_(), deferred_(),"]
            L += ["      deferredCount_(0U), deferOverflow_(false)"]
        else:
            L += ["      completionPending_(), active_()"]
        if ir.has_history():
            L += ["    , history_()"]
        L += ["{"]

        # `active_` IS FILLED WITH kNone, NOT WITH ZERO.
        #
        # `active_()` in the member initialiser list value-initialises the array,
        # i.e. sets every element to 0. But 0 IS A VALID STATE INDEX; the value
        # that means "empty" is kNone = 255. So on an object that was constructed
        # but whose start() had NOT been called yet, `isIn()` reported state 0 as
        # active in every region -- while the C generator (which writes STATE_NONE
        # explicitly inside construct) and the Python reference both said there
        # was no active state.
        #
        # The trace-comparison test CANNOT SEE this: the comparison begins after
        # start(), and start() already fills every region.
        L += ["    for (std::uint8_t region = 0U; region < kRegionCount; ++region) {"]
        L += ["        active_[region] = kNone;"]
        L += ["    }"]
        if ir.has_history():
            # THE BOUND IS kRegionCount, NOT kStateCount.
            #
            # `history_` keeps one entry per region (the member is declared
            # `history_[kRegionCount]`). Had the loop run up to the state count -- and
            # it did -- every construction would write PAST THE END of the object:
            # history_ is the last member of the class, so the caller's stack or a
            # neighbouring field gets corrupted. The trace-comparison test CANNOT SEE
            # this either; the byte written is never read back.
            # okunmaz.
            L += ["    for (std::uint8_t i = 0U; i < kRegionCount; ++i) {"]
            L += ["        history_[i] = kNone;"]
            L += ["    }"]
        L += ["}", ""]

        # entry / exit / do
        if ir.has_time_events():
            ucler = ir.time_triggers()
            gruplu = {}
            for src, ev, gecikme in ucler:
                gruplu.setdefault(src, []).append((ev, gecikme))
            for label, kanca, fiil in (("Start", "timerStart", "Starts"),
                                        ("Cancel", "timerCancel", "Cancels")):
                L += doc(['@brief %s the after() timers of a state.' % fiil,
                         '',
                         'UML treats a TimeEvent as a trigger; KEEPING TIME IS',
                         "NOT THE MACHINE'S JOB. The generated code only says",
                         'when a timer should run and when it must be',
                         'cancelled; you write the two hooks and post the',
                         'event when your timer expires.'])
                L += ["void %s::timers%s(std::uint8_t state) noexcept"
                      % (cls, label)]
                L += ["{"]
                L += ["    switch (state) {"]
                for src in sorted(gruplu):
                    L += ["    case static_cast<std::uint8_t>(State::%s):"
                          % ir.states[src].name]
                    L += ["    {"]
                    for ev, gecikme in gruplu[src]:
                        if label == "Start":
                            L += ["        %s(*this, state,"
                                  " static_cast<std::uint8_t>(Event::%s),"
                                  " static_cast<std::uint32_t>(%s));"
                                  % (kanca, self._ev_name(ir.events[ev]),
                                     gecikme)]
                        else:
                            L += ["        %s(*this, state,"
                                  " static_cast<std::uint8_t>(Event::%s));"
                                  % (kanca, self._ev_name(ir.events[ev]))]
                    L += ["        break;", "    }"]
                L += ["    default:", "        break;", "    }"]
                L += ["}", ""]

        for tag, attr, meth, title in (("entry", "entry", "executeEntry", "entry"),
                                       ("exit", "exit", "executeExit", "exit"),
                                       ("do", "do", "executeDoActivity", "do")):
            L += [section("%s actions" % title), ""]
            L += doc(["@brief Runs the %s behaviour of one state." % title,
                      "@param state State identifier."])
            L += ["void %s::%s(std::uint8_t state) noexcept" % (cls, meth)]
            L += ["{"]
            L += self._ctx_preamble()
            bodies = [st for st in ir.states if getattr(st, attr)]
            if not bodies:
                L += ["    static_cast<void>(state);"]
                L += ["    // this model declares no %s behaviour" % title]
            else:
                L += ["", "    switch (static_cast<State>(state)) {"]
                for st in bodies:
                    L += ["    case State::%s:" % st.name]
                    L += ["    {"]
                    L += indent_block(as_statement(getattr(st, attr)), "        ")
                    L += ["        break;", "    }"]
                L += ["    default:", "        break;", "    }"]
            L += ["}", ""]

        # guard
        L += [section("guards"), ""]
        L += doc(['@brief  Evaluates the guard with the given id.',
                 '@param  id  Guard identifier; -1 means "no guard".',
                 '@return true when the guard holds or there is none.'])
        L += ["bool %s::evaluateGuard(std::int16_t id) noexcept" % cls]
        L += ["{"]
        L += self._ctx_preamble()
        L += ["", "    if (id < 0) {  // no guard", "        return true;", "    }"]
        if ir.guards:
            L += ["", "    switch (id) {"]
            for i, g in enumerate(ir.guards):
                L += ["    case %d:" % i]
                L += ["        return static_cast<bool>(%s);" % as_expression(g)]
            L += ["    default:", "        break;", "    }"]
        else:
            L += ["", "    // this model has no guards"]
        L += ["", "    return false;", "}", ""]

        # action
        L += [section("effects"), ""]
        L += doc(['@brief  Executes the effect with the given id.',
                 '@param  id  Effect identifier; -1 means "no effect".'])
        L += ["void %s::executeAction(std::int16_t id) noexcept" % cls]
        L += ["{"]
        L += self._ctx_preamble()
        if ir.actions:
            L += ["", "    switch (id) {"]
            for i, a in enumerate(ir.actions):
                L += ["    case %d:" % i]
                L += ["    {"]
                L += indent_block(as_statement(a), "        ")
                L += ["        break;", "    }"]
            L += ["    default:", "        break;  // includes -1: no effect", "    }"]
        else:
            L += ["", "    static_cast<void>(id);"]
            L += ["    // this model has no transition effects"]
        L += ["}", ""]

        # enterPath
        L += [section("hierarchy helpers"), ""]
        L += doc(['@brief  Runs the entry chain from @p top down to @p target.',
                 '@param  target  The state being entered.',
                 '@param  top     The common ancestor already entered.'])
        L += ["void %s::enterOne(std::uint8_t state) noexcept" % cls]
        L += ["{"]
        L += ["    const std::uint8_t region = kStateRegion[state];"]
        L += ["", "    executeEntry(state);"]
        if ir.has_time_events():
            L += ["    timersStart(state);"]
        L += ["    if (region != kRegionNone) {"]
        L += ["        active_[region] = state;"]
        L += ["        // A state was entered: this region's completion is due."]
        L += ["        completionPending_[region] = true;"]
        L += ["    }"]
        L += ["}", ""]

        L += ["void %s::exitOne(std::uint8_t state) noexcept" % cls]
        L += ["{"]
        L += ["    const std::uint8_t region = kStateRegion[state];"]
        L += ["", "    executeExit(state);"]
        if ir.has_time_events():
            L += ["    timersCancel(state);"]
        if ir.has_history():
            L += ["    // Shallow-history record (UML 14.2.3.4.5). It lives HERE so"]
            L += ["    // that a state closed while an orthogonal parent is torn"]
            L += ["    // down is remembered too."]
            L += ["    if (region != kRegionNone) {"]
            L += ["        if ((kKind[state] == kKindSimple) ||"]
            L += ["            (kKind[state] == kKindComposite)) {"]
            L += ["            history_[region] = state;"]
            L += ["        } else if (kKind[state] == kKindFinal) {"]
            L += ["            history_[region] = kNone;"]
            L += ["        } else {"]
            L += ["            // leaving a pseudostate does not change the record"]
            L += ["        }"]
            L += ["    }"]
        L += ["    if ((region != kRegionNone) && (active_[region] == state)) {"]
        L += ["        active_[region] = kNone;"]
        L += ["        completionPending_[region] = false;"]
        L += ["    }"]
        L += ["}", ""]

        L += doc(['@brief Opens every region below an already-entered state.',
                 '',
                 'Regions are activated in ASCENDING order; UML leaves this',
                 'order undefined (14.2.3.8.3) so the generator fixes it. An',
                 'explicit stack replaces recursion: on an embedded target the',
                 'dispatch stack must be bounded by the tables, not the model.'])
        L += ["void %s::activateBelow(std::uint8_t state) noexcept" % cls]
        L += ["{"]
        L += ["    std::uint8_t stackState[kMaxDepth];"]
        L += ["    std::uint8_t stackNext[kMaxDepth];"]
        L += ["    std::uint8_t top = 1U;"]
        L += ["    std::uint16_t steps = 0U;"]
        L += ["", "    stackState[0] = state;"]
        L += ["    stackNext[0] = 0U;"]
        L += ["    while ((top > 0U) && (steps < kMaxWalkSteps)) {"]
        L += ["        const std::uint8_t current = stackState[top - 1U];"]
        L += ["        const std::uint8_t next = stackNext[top - 1U];"]
        L += ["        ++steps;"]
        L += ["        if (next >= kStateRegionCount[current]) {"]
        L += ["            --top;"]
        L += ["            continue;"]
        L += ["        }"]
        L += ["        stackNext[top - 1U] = static_cast<std::uint8_t>(next + 1U);"]
        L += ["        {"]
        L += ["            const std::uint8_t region ="]
        L += ["                static_cast<std::uint8_t>(kStateFirstRegion[current] + next);"]
        L += ["            const std::uint8_t target = kRegionInitial[region];"]
        L += ["            if (target != kNone) {"]
        L += ["                executeAction(kRegionInitialAction[region]);"]
        L += ["                enterOne(target);"]
        L += ["                if (top < kMaxDepth) {"]
        L += ["                    stackState[top] = target;"]
        L += ["                    stackNext[top] = 0U;"]
        L += ["                    ++top;"]
        L += ["                }"]
        L += ["            }"]
        L += ["        }"]
        L += ["    }"]
        L += ["}", ""]

        L += doc(['@brief  Deepest active state below @p state (regions descending).'])
        L += ["std::uint8_t %s::deepestActiveBelow(std::uint8_t state) const noexcept" % cls]
        L += ["{"]
        L += ["    std::uint8_t found = kNone;"]
        L += ["    std::uint8_t node = state;"]
        L += ["    std::uint16_t steps = 0U;"]
        L += ["", "    while (steps < kMaxWalkSteps) {"]
        L += ["        std::uint8_t next = kNone;"]
        L += ["        std::uint8_t regionIndex = kStateRegionCount[node];"]
        L += ["        ++steps;"]
        L += ["        while (regionIndex > 0U) {"]
        L += ["            --regionIndex;"]
        L += ["            const std::uint8_t candidate ="]
        L += ["                active_[kStateFirstRegion[node] + regionIndex];"]
        L += ["            if (candidate != kNone) {"]
        L += ["                next = candidate;"]
        L += ["                break;"]
        L += ["            }"]
        L += ["        }"]
        L += ["        if (next == kNone) {", "            return found;", "        }"]
        L += ["        found = next;"]
        L += ["        node = next;"]
        L += ["    }"]
        L += ["    return found;"]
        L += ["}", ""]

        L += doc(['@brief Closes everything below @p state; the state itself stays.'])
        L += ["void %s::exitBelow(std::uint8_t state) noexcept" % cls]
        L += ["{"]
        L += ["    std::uint16_t steps = 0U;"]
        L += ["", "    while (steps < kMaxWalkSteps) {"]
        L += ["        const std::uint8_t target = deepestActiveBelow(state);"]
        L += ["        ++steps;"]
        L += ["        if (target == kNone) {", "            return;", "        }"]
        L += ["        exitOne(target);"]
        L += ["    }"]
        L += ["}", ""]

        L += doc(['@brief  Representative leaf; always follows the FIRST region.'])
        L += ["std::uint8_t %s::leafOf(std::uint8_t state) const noexcept" % cls]
        L += ["{"]
        L += ["    std::uint8_t current = state;"]
        L += ["    std::uint16_t steps = 0U;"]
        L += ["", "    while ((current != kNone) && (steps < kMaxWalkSteps)) {"]
        L += ["        ++steps;"]
        L += ["        if (kStateRegionCount[current] == 0U) {"]
        L += ["            return current;"]
        L += ["        }"]
        L += ["        const std::uint8_t next = active_[kStateFirstRegion[current]];"]
        L += ["        if ((next == kNone) || (next == current)) {"]
        L += ["            return current;"]
        L += ["        }"]
        L += ["        current = next;"]
        L += ["    }"]
        L += ["    return current;"]
        L += ["}", ""]

        L += doc(['@brief  Have ALL regions of a composite state reached a final state?',
                 '',
                 'UML 2.5.1, 14.2.3.8.3: one region is not enough.'])
        L += ["bool %s::completed(std::uint8_t state) const noexcept" % cls]
        L += ["{"]
        L += ["    const std::uint8_t owned = kStateRegionCount[state];"]
        L += ["", "    if (owned == 0U) {", "        return true;", "    }"]
        L += ["    for (std::uint8_t regionIndex = 0U; regionIndex < owned; ++regionIndex) {"]
        L += ["        const std::uint8_t leaf = active_[kStateFirstRegion[state] + regionIndex];"]
        L += ["        if ((leaf == kNone) || (kKind[leaf] != kKindFinal)) {"]
        L += ["            return false;"]
        L += ["        }"]
        L += ["    }"]
        L += ["    return true;"]
        L += ["}", ""]

        L += doc(['@brief Runs the entry chain from @p top (exclusive) to @p target.',
                 '',
                 'An ORTHOGONAL state on the path starts every region the path',
                 'does not itself pass through (UML 2.5.1, 14.2.3.2). The',
                 "target's own regions are opened by descend(), not here."])
        L += ["void %s::enterPath(std::uint8_t target, std::uint8_t top) noexcept" % cls]
        L += ["{"]
        L += ["    std::uint8_t path[kMaxDepth];"]
        L += ["    std::uint8_t count = 0U;"]
        L += ["    std::uint8_t state = target;"]
        L += ["", "    while ((state != top) && (state != kNone) && (count < kMaxDepth)) {"]
        L += ["        path[count] = state;"]
        L += ["        ++count;"]
        L += ["        state = kParent[state];"]
        L += ["    }"]
        L += ["    for (std::uint8_t i = count; i > 0U; --i) {"]
        L += ["        const std::uint8_t current = path[i - 1U];"]
        L += ["        enterOne(current);"]
        L += ["        if (i > 1U) {"]
        L += ["            const std::uint8_t passed = kStateRegion[path[i - 2U]];"]
        L += ["            const std::uint8_t owned = kStateRegionCount[current];"]
        L += ["            for (std::uint8_t regionIndex = 0U; regionIndex < owned; ++regionIndex) {"]
        L += ["                const std::uint8_t region ="]
        L += ["                    static_cast<std::uint8_t>(kStateFirstRegion[current] + regionIndex);"]
        L += ["                if (region == passed) {", "                    continue;", "                }"]
        L += ["                const std::uint8_t fallback = kRegionInitial[region];"]
        L += ["                if (fallback == kNone) {", "                    continue;", "                }"]
        L += ["                executeAction(kRegionInitialAction[region]);"]
        L += ["                enterOne(fallback);"]
        L += ["                activateBelow(fallback);"]
        L += ["            }"]
        L += ["        }"]
        L += ["    }"]
        L += ["}", ""]

        # descend
        L += doc(['@brief  Descends into the default substates of composite states.',
                 '@param  state  Where the descent starts.',
                 '@return The leaf state the machine settles in.'])
        L += ["std::uint8_t %s::descend(std::uint8_t state) noexcept" % cls]
        L += ["{"]
        L += ["    activateBelow(state);"]
        L += ["    return leafOf(state);"]
        L += ["}", ""]

        # resolveHistory (only when the model has history)
        if ir.has_history():
            L += doc(['@brief  Resolves a history pseudostate to its recorded substate',
                     '        (UML 2.5.1, 14.2.3.4.5).',
                     '@param  h  History pseudostate identifier.',
                     '@return The substate to re-enter, or the default target.'])
            L += ["std::uint8_t %s::resolveHistory(std::uint8_t h) noexcept" % cls]
            L += ["{"]
            L += ["    const std::uint8_t region = kStateRegion[h];"]
            L += ["    const std::uint8_t owner = kParent[h];"]
            L += ["    std::uint8_t stored = kNone;"]
            L += ["    std::uint16_t steps = 0U;"]
            L += [""]
            L += ["    if (region != kRegionNone) {"]
            L += ["        stored = history_[region];"]
            L += ["    }"]
            L += [""]
            L += ["    if (stored == kNone) {", "        stored = kHistDefault[h];", "    }"]
            L += ["    if ((stored == kNone) && (region != kRegionNone)) {"]
            L += ["        stored = kRegionInitial[region];"]
            L += ["    }"]
            L += ["    if (stored == kNone) {", "        return owner;", "    }"]
            L += [""]
            L += ["    enterPath(stored, owner);"]
            L += ["    if (kKind[h] == kKindHistoryDeep) {"]
            L += ["        // DEEP HISTORY RESTORES A CONFIGURATION, NOT A STATE."]
            L += ["        //"]
            L += ["        // UML 2.5.1, 14.2.3.7: a deepHistory Pseudostate represents"]
            L += ['        // "the most recent active state configuration of its owning']
            L += ['        // Region", and a Transition terminating on it implies']
            L += ['        // "restoring the Region to that same state configuration".']
            L += ["        // When a remembered substate is itself orthogonal, EVERY one"]
            L += ["        // of its regions comes back from the record. Activating the"]
            L += ["        // defaults below the record instead would re-run that"]
            L += ["        // region's initial effect -- a real side effect in firmware"]
            L += ["        // -- and silently drop the remembered branch."]
            L += ["        //"]
            L += ["        // The walk is an explicit breadth-first work list: entry runs"]
            L += ["        // outside-in, regions ascend, and two runs give the same"]
            L += ["        // order. There is no recursion, and the queue cannot outgrow"]
            L += ["        // the region count because each region contributes at most"]
            L += ["        // one remembered state."]
            L += ["        std::uint8_t queueState[kRegionCount + 1U];"]
            L += ["        std::uint8_t queueHead = 0U;"]
            L += ["        std::uint8_t queueTail = 0U;"]
            L += [""]
            L += ["        queueState[queueTail] = stored;"]
            L += ["        ++queueTail;"]
            L += ["        while ((queueHead < queueTail) && (steps < kMaxWalkSteps)) {"]
            L += ["            const std::uint8_t current = queueState[queueHead];"]
            L += ["            const std::uint8_t owned = kStateRegionCount[current];"]
            L += ["            const std::uint8_t first = kStateFirstRegion[current];"]
            L += ["            ++queueHead;"]
            L += ["            ++steps;"]
            L += ["            for (std::uint8_t regionIndex = 0U; regionIndex < owned; ++regionIndex) {"]
            L += ["                const std::uint8_t inner ="]
            L += ["                    static_cast<std::uint8_t>(first + regionIndex);"]
            L += ["                const std::uint8_t remembered = history_[inner];"]
            L += ["                if (remembered == kNone) {"]
            L += ["                    const std::uint8_t fallback = kRegionInitial[inner];"]
            L += ["                    if (fallback != kNone) {"]
            L += ["                        executeAction(kRegionInitialAction[inner]);"]
            L += ["                        enterOne(fallback);"]
            L += ["                        activateBelow(fallback);"]
            L += ["                    }"]
            L += ["                    continue;"]
            L += ["                }"]
            L += ["                enterOne(remembered);"]
            L += ["                if (queueTail < static_cast<std::uint8_t>(kRegionCount + 1U)) {"]
            L += ["                    queueState[queueTail] = remembered;"]
            L += ["                    ++queueTail;"]
            L += ["                }"]
            L += ["            }"]
            L += ["        }"]
            L += ["        return leafOf(stored);"]
            L += ["    }"]
            L += ["    return descend(stored);"]
            L += ["}", ""]

        # land: resolve history/terminate/descent on arrival at the target
        L += doc(['@brief  Determines the real leaf state once a target is reached.',
                 '@param  target  The declared transition target.',
                 '@return The settled leaf state; history and terminate resolve here.'])
        L += ["std::uint8_t %s::land(std::uint8_t target) noexcept" % cls]
        L += ["{"]
        L += ["    std::uint8_t leaf;"]
        L += ["    const std::uint8_t kind = kKind[target];"]
        L += [""]
        if ir.has_history():
            L += ["    if ((kind == kKindHistoryShallow) || (kind == kKindHistoryDeep)) {"]
            L += ["        leaf = resolveHistory(target);"]
            L += ["    } else if (kind == kKindTerminate) {"]
        else:
            L += ["    if (kind == kKindTerminate) {"]
        L += ["        terminated_ = true;  // UML terminate: the machine stops"]
        L += ["        leaf = target;"]
        L += ["    } else {"]
        L += ["        leaf = descend(target);"]
        L += ["    }"]
        L += ["    return leaf;"]
        L += ["}", ""]

        # tryEvent
        L += doc(['@brief  Finds and executes the first enabled transition.',
                 '',
                 'The search starts at the active leaf and walks up to the root, so',
                 'a nested state takes priority over its parents (UML 2.5.1, 14.2.3.9).',
                 '',
                 '@param  event  Event identifier.',
                 '@return true when a transition fired.'])
        L += doc(['@brief Executes one transition (exit chain, effect, entry chain).'])
        L += ["// The row is named by INDEX: the Transition type lives in an"]
        L += ["// anonymous namespace in this file and cannot appear in the header."]
        L += ["void %s::takeTransition(std::uint16_t index) noexcept" % cls]
        L += ["{"]
        L += ["    const Transition& transition = kTransitions[index];"]
        L += ["", "    if (transition.kind == kTransitionInternal) {"]
        L += ["        // An internal transition does not change the state, so it"]
        L += ["        // generates no new completion event (UML 2.5.1, 14.2.3.8.3)."]
        L += ["        // Only ITS OWN region is cleared: a machine-wide flag would"]
        L += ["        // also destroy a completion another region is waiting for."]
        L += ["        if (kStateRegion[transition.source] != kRegionNone) {"]
        L += ["            completionPending_[kStateRegion[transition.source]] = false;"]
        L += ["        }"]
        L += ["        executeAction(transition.action);"]
        L += ["        return;"]
        L += ["    }"]
        L += ["", "    // UML 2.5.1, 14.2.3.7: entering terminate leaves no state"]
        L += ["    // at all; exit behaviours do not run."]
        L += ["    if (kKind[transition.target] == kKindTerminate) {"]
        L += ["        executeAction(transition.action);"]
        L += ["        terminated_ = true;"]
        L += ["        clearCompletion();"]
        L += ["        state_ = static_cast<State>(transition.target);"]
        L += ["        return;"]
        L += ["    }"]
        L += ["", "    std::uint8_t top = leastCommonAncestorOf(transition.source, transition.target);"]
        L += ["    if (transition.kind == kTransitionExternal) {"]
        L += ["        // An external transition also leaves its source."]
        L += ["        if ((top == transition.source) || (top == transition.target)) {"]
        L += ["            if (top != kNone) {"]
        L += ["                top = kParent[top];"]
        L += ["            }"]
        L += ["        }"]
        L += ["    }"]
        L += ["", "    // The exit starts in the region BELOW `top` that the transition"]
        L += ["    // actually affects. Starting from the source's own region would"]
        L += ["    // be wrong for a LOCAL transition, whose source IS `top`: the"]
        L += ["    // loop would never run and the current substate would stay."]
        L += ["    std::uint8_t below = transition.target;"]
        L += ["    std::uint16_t walk = 0U;"]
        L += ["    while ((kParent[below] != top) && (kParent[below] != kNone) &&"]
        L += ["           (walk < kMaxWalkSteps)) {"]
        L += ["        below = kParent[below];"]
        L += ["        ++walk;"]
        L += ["    }"]
        L += ["    const std::uint8_t region = kStateRegion[below];"]
        L += ["    std::uint8_t current = kNone;"]
        L += ["    if (region != kRegionNone) {"]
        L += ["        current = active_[region];"]
        L += ["    }"]
        L += ["    while ((current != top) && (current != kNone)) {"]
        L += ["        exitBelow(current);"]
        L += ["        exitOne(current);"]
        L += ["        current = kParent[current];"]
        L += ["    }"]
        L += ["", "    executeAction(transition.action);"]
        L += ["    enterPath(transition.target, top);"]
        if ir.has_fork_join():
            L += ["    if (transition.forkCount > 0U) {"]
            L += ["        enterForked(index);"]
            L += ["        state_ = static_cast<State>(leafOf(transition.target));"]
            L += ["    } else {"]
            L += ["        state_ = static_cast<State>(land(transition.target));"]
            L += ["    }"]
        else:
            L += ["    state_ = static_cast<State>(land(transition.target));"]
        L += ["    // The per-region flags were set inside enterOne()."]
        L += ["    if (terminated_) {"]
        L += ["        clearCompletion();"]
        L += ["    }"]
        L += ["}", ""]

        if ir.has_fork_join():
            L += doc(['@brief  Is EVERY incoming segment of a join active right now?',
                     '',
                     'UML 2.5.1, 14.2.3.7: all incoming Transitions have to',
                     'complete before execution can continue through an',
                     'outgoing Transition.'])
            L += ["bool %s::joinReady(std::uint16_t index) const noexcept" % cls]
            L += ["{"]
            L += ["    const Transition& transition = kTransitions[index];"]
            L += ["", "    for (std::uint8_t i = 0U; i < transition.joinCount; ++i) {"]
            L += ["        const std::uint8_t source ="]
            L += ["            kExtra[transition.extraFirst + transition.forkCount + i];"]
            L += ["        const std::uint8_t region = kStateRegion[source];"]
            L += ["        if ((region == kRegionNone) || (active_[region] != source)) {"]
            L += ["            return false;"]
            L += ["        }"]
            L += ["    }"]
            L += ["    return true;"]
            L += ["}", ""]

            L += doc(['@brief Enters the regions a fork names; the rest start by default.',
                     '',
                     'UML 2.5.1, 14.2.3.7: a fork splits an incoming Transition',
                     'into Transitions terminating on Vertices in orthogonal',
                     'Regions. Entering an orthogonal state still starts ALL of',
                     'its regions, so a region the fork does not name is',
                     'activated by default.'])
            L += ["void %s::enterForked(std::uint16_t index) noexcept" % cls]
            L += ["{"]
            L += ["    const Transition& transition = kTransitions[index];"]
            L += ["    const std::uint8_t owner = transition.target;"]
            L += ["    std::uint8_t covered[kRegionCount];"]
            L += ["    std::uint8_t coveredCount = 0U;"]
            L += ["", "    for (std::uint8_t i = 0U; i < transition.forkCount; ++i) {"]
            L += ["        const std::uint8_t leaf = kExtra[transition.extraFirst + i];"]
            L += ["        std::uint8_t below = leaf;"]
            L += ["        std::uint16_t walk = 0U;"]
            L += ["        while ((kParent[below] != owner) && (kParent[below] != kNone) &&"]
            L += ["               (walk < kMaxWalkSteps)) {"]
            L += ["            below = kParent[below];"]
            L += ["            ++walk;"]
            L += ["        }"]
            L += ["        if (coveredCount < kRegionCount) {"]
            L += ["            covered[coveredCount] = kStateRegion[below];"]
            L += ["            ++coveredCount;"]
            L += ["        }"]
            L += ["        enterPath(leaf, owner);"]
            L += ["        activateBelow(leaf);"]
            L += ["    }"]
            L += ["", "    for (std::uint8_t regionIndex = 0U;"]
            L += ["         regionIndex < kStateRegionCount[owner]; ++regionIndex) {"]
            L += ["        const std::uint8_t region ="]
            L += ["            static_cast<std::uint8_t>(kStateFirstRegion[owner] + regionIndex);"]
            L += ["        bool taken = false;"]
            L += ["        for (std::uint8_t i = 0U; i < coveredCount; ++i) {"]
            L += ["            if (covered[i] == region) {"]
            L += ["                taken = true;"]
            L += ["                break;"]
            L += ["            }"]
            L += ["        }"]
            L += ["        if (taken) {", "            continue;", "        }"]
            L += ["        const std::uint8_t fallback = kRegionInitial[region];"]
            L += ["        if (fallback == kNone) {", "            continue;", "        }"]
            L += ["        executeAction(kRegionInitialAction[region]);"]
            L += ["        enterOne(fallback);"]
            L += ["        activateBelow(fallback);"]
            L += ["    }"]
            L += ["}", ""]

        L += doc(['@brief  First enabled transition seen from one region.'])
        L += ["bool %s::select(std::uint8_t region, std::uint8_t event," % cls]
        L += ["                std::uint8_t& outSource,"
              " std::uint16_t& outTransition) noexcept"]
        L += ["{"]
        L += ["    std::uint8_t state = active_[region];"]
        L += ["    std::uint16_t steps = 0U;"]
        L += ["", "    while ((state != kNone) && (steps < kMaxWalkSteps)) {"]
        L += ["        const std::uint16_t first = kTransitionFirst[state];"]
        L += ["        const std::uint16_t count = kTransitionCount[state];"]
        L += ["        ++steps;"]
        L += ["", "        for (std::uint16_t i = 0U; i < count; ++i) {"]
        L += ["            const Transition& transition = kTransitions[first + i];"]
        L += ["", "            if (transition.event != event) {"]
        L += ["                continue;"]
        L += ["            }"]
        L += ["            // A composite state completes only when ALL of its regions"]
        L += ["            // have reached a final state (UML 2.5.1, 14.2.3.8.3)."]
        if ir.has_fork_join():
            L += ["            if ((event == static_cast<std::uint8_t>(Event::Completion)) &&"]
            L += ["                (kKind[state] == kKindComposite) &&"]
            L += ["                (transition.joinCount == 0U) && (!completed(state))) {"]
            L += ["                continue;"]
            L += ["            }"]
            L += ["            // A join fires only when EVERY incoming segment is"]
            L += ["            // active at the same time (UML 2.5.1, 14.2.3.7)."]
            L += ["            if ((transition.joinCount > 0U) &&"]
            L += ["                (!joinReady(static_cast<std::uint16_t>(first + i)))) {"]
            L += ["                continue;"]
            L += ["            }"]
        else:
            L += ["            if ((event == static_cast<std::uint8_t>(Event::Completion)) &&"]
            L += ["                (kKind[state] == kKindComposite) && (!completed(state))) {"]
            L += ["                continue;"]
            L += ["            }"]
        L += ["            if (!evaluateGuard(transition.guard)) {"]
        L += ["                continue;"]
        L += ["            }"]
        L += ["            outSource = state;"]
        L += ["            outTransition = static_cast<std::uint16_t>(first + i);"]
        L += ["            return true;"]
        L += ["        }"]
        L += ["        state = kParent[state];"]
        L += ["    }"]
        L += ["    return false;"]
        L += ["}", ""]

        L += doc(['@brief  Offers the event to EVERY active region and resolves conflicts.',
                 '',
                 'UML 2.5.1, 14.2.3.9.4: a transition leaving a deeper state',
                 'conflicts with one leaving a state that contains it, and the',
                 'deeper one wins. Two orthogonal regions may also select the',
                 'SAME outer transition; it must then be taken once, not twice.',
                 '',
                 'Regions are visited in ASCENDING order, which UML leaves',
                 'undefined (14.2.3.8.3) and the generator therefore fixes.',
                 '',
                 '@return true when at least one transition fired.'])
        L += ["bool %s::tryEvent(std::uint8_t event) noexcept" % cls]
        L += ["{"]
        L += ["    std::uint8_t chosenRegion[kRegionCount];"]
        L += ["    std::uint8_t chosenSource[kRegionCount];"]
        L += ["    std::uint16_t chosenTransition[kRegionCount];"]
        L += ["    std::uint8_t count = 0U;"]
        L += ["    bool handled = false;"]
        L += ["", "    for (std::uint8_t r = 0U; r < kRegionCount; ++r) {"]
        L += ["        std::uint8_t source = kNone;"]
        L += ["        std::uint16_t transition = 0U;"]
        L += ["        bool duplicate = false;"]
        L += ["", "        if (active_[r] == kNone) {"]
        L += ["            continue;"]
        L += ["        }"]
        L += ["        if (!select(r, event, source, transition)) {"]
        L += ["            continue;"]
        L += ["        }"]
        L += ["        for (std::uint8_t i = 0U; i < count; ++i) {"]
        L += ["            if (chosenTransition[i] == transition) {"]
        L += ["                duplicate = true;"]
        L += ["                break;"]
        L += ["            }"]
        L += ["        }"]
        L += ["        if (duplicate) {", "            continue;", "        }"]
        L += ["        chosenRegion[count] = r;"]
        L += ["        chosenSource[count] = source;"]
        L += ["        chosenTransition[count] = transition;"]
        L += ["        ++count;"]
        L += ["    }"]
        L += ["", "    // Drop a selection whose source CONTAINS another selection's"]
        L += ["    // source: priority belongs to the deeper state."]
        L += ["    for (std::uint8_t i = 0U; i < count; ++i) {"]
        L += ["        for (std::uint8_t j = 0U; j < count; ++j) {"]
        L += ["            if (i == j) {", "                continue;", "            }"]
        L += ["            if (isAncestorOf(chosenSource[i], chosenSource[j])) {"]
        L += ["                chosenRegion[i] = kRegionNone;"]
        L += ["                break;"]
        L += ["            }"]
        L += ["        }"]
        L += ["    }"]
        L += ["", "    for (std::uint8_t i = 0U; i < count; ++i) {"]
        L += ["        // TERMINATE STOPS EVERYTHING (UML 2.5.1, 14.2.3.7)."]
        L += ["        // Once one region has killed the machine, running the"]
        L += ["        // transitions the other regions selected would execute"]
        L += ["        // exit, effect and entry behaviour on a machine that has"]
        L += ["        // already ceased."]
        L += ["        if (terminated_) {", "            break;", "        }"]
        L += ["        if (chosenRegion[i] == kRegionNone) {"]
        L += ["            continue;"]
        L += ["        }"]
        L += ["        const Transition& transition = kTransitions[chosenTransition[i]];"]
        L += ["        // An earlier transition may have closed this region."]
        L += ["        if ((active_[chosenRegion[i]] == kNone) &&"]
        L += ["            (transition.kind != kTransitionInternal)) {"]
        L += ["            continue;"]
        L += ["        }"]
        L += ["        takeTransition(chosenTransition[i]);"]
        L += ["        handled = true;"]
        L += ["    }"]
        L += ["    return handled;"]
        L += ["}", ""]

        # runToCompletion
        L += doc(['@brief Resolves completion (event-less) transitions to a stable state.',
                 '',
                 'A completion event is generated when a state is ENTERED (UML 2.5.1,',
                 '14.2.3.8.3) and is consumed once, whether or not a transition was',
                 'enabled by it. Looping unconditionally would re-fire an event that',
                 'was already consumed: an unrelated internal transition could then',
                 'move the machine to a state the diagram does not show.'])
        L += ["void %s::runToCompletion() noexcept" % cls]
        L += ["{"]
        L += ["    // uint16_t: the bound is 16 per region, so up to 16 * 254."]
        L += ["    std::uint16_t steps = 0U;"]
        L += ["    while ((steps < kMaxRunToCompletionSteps) && (!terminated_)) {"]
        L += ["        std::uint8_t region = kRegionNone;"]
        L += ["        for (std::uint8_t r = 0U; r < kRegionCount; ++r) {"]
        L += ["            if (completionPending_[r]) {"]
        L += ["                region = r;"]
        L += ["                break;"]
        L += ["            }"]
        L += ["        }"]
        L += ["        if (region == kRegionNone) {", "            return;", "        }"]
        L += ["        completionPending_[region] = false;"]
        L += ["        if (active_[region] != kNone) {"]
        L += ["            std::uint8_t source = kNone;"]
        L += ["            std::uint16_t found = 0U;"]
        L += ["            if (select(region,"]
        L += ["                       static_cast<std::uint8_t>(Event::Completion),"]
        L += ["                       source, found)) {"]
        L += ["                takeTransition(found);"]
        L += ["            }"]
        L += ["        }"]
        L += ["        ++steps;"]
        L += ["    }"]
        L += ["}", ""]

        L += doc(['@brief Clears every pending completion flag.'])
        L += ["void %s::clearCompletion() noexcept" % cls]
        L += ["{"]
        L += ["    for (std::uint8_t r = 0U; r < kRegionCount; ++r) {"]
        L += ["        completionPending_[r] = false;"]
        L += ["    }"]
        L += ["}", ""]

        if ir.has_deferred():
            L += doc(['@brief  Is this event type deferred by the active configuration?',
                     '',
                     'UML 2.5.1, 14.2.3.4.4: an Event may be deferred by a',
                     'composite state too, in which case it stays deferred as',
                     'long as that state is in the active configuration -- so',
                     'every active region is checked, not just the leaf.'])
            L += ["bool %s::isDeferred(std::uint8_t event) const noexcept" % cls]
            L += ["{"]
            L += ["    for (std::uint8_t r = 0U; r < kRegionCount; ++r) {"]
            L += ["        std::uint8_t state = active_[r];"]
            L += ["        std::uint16_t steps = 0U;"]
            L += ["        while ((state != kNone) && (steps < kMaxWalkSteps)) {"]
            L += ["            ++steps;"]
            L += ["            if ((kDeferMask[state] &"]
            L += ["                 (static_cast<std::uint32_t>(1U) << event)) != 0U) {"]
            L += ["                return true;"]
            L += ["            }"]
            L += ["            state = kParent[state];"]
            L += ["        }"]
            L += ["    }"]
            L += ["    return false;"]
            L += ["}", ""]

            L += doc(['@brief Retains one occurrence in the deferred pool.',
                     '',
                     'A full pool is REPORTED, never silently dropped: a lost',
                     'event would make the machine look broken for no visible',
                     'reason.'])
            L += ["void %s::defer(std::uint8_t event) noexcept" % cls]
            L += ["{"]
            L += ["    if (deferredCount_ >= kDeferPoolSize) {"]
            L += ["        deferOverflow_ = true;"]
            L += ["        return;"]
            L += ["    }"]
            L += ["    deferred_[deferredCount_] = event;"]
            L += ["    ++deferredCount_;"]
            L += ["}", ""]

            L += doc(['@brief Replays the retained occurrences that are no longer deferred.'])
            L += ["void %s::drainDeferred() noexcept" % cls]
            L += ["{"]
            L += ["    std::uint8_t rounds = 0U;"]
            L += ["", "    while (rounds < static_cast<std::uint8_t>(2U * kDeferPoolSize)) {"]
            L += ["        if (terminated_) {", "            return;", "        }"]
            L += ["        std::uint8_t index = 0U;"]
            L += ["        std::uint8_t found = kEventCount;"]
            L += ["        ++rounds;"]
            L += ["        for (std::uint8_t i = 0U; i < deferredCount_; ++i) {"]
            L += ["            if (!isDeferred(deferred_[i])) {"]
            L += ["                index = i;"]
            L += ["                found = deferred_[i];"]
            L += ["                break;"]
            L += ["            }"]
            L += ["        }"]
            L += ["        if (found == kEventCount) {", "            return;", "        }"]
            L += ["        for (std::uint8_t i = static_cast<std::uint8_t>(index + 1U);"]
            L += ["             i < deferredCount_; ++i) {"]
            L += ["            deferred_[i - 1U] = deferred_[i];"]
            L += ["        }"]
            L += ["        --deferredCount_;"]
            L += ["        if (tryEvent(found)) {"]
            L += ["            runToCompletion();"]
            L += ["        }"]
            L += ["    }"]
            L += ["}", ""]

        # start
        L += [section("public functions"), ""]
        L += doc(['@brief Runs the initial transition and the entry behaviours.'])
        L += ["void %s::start() noexcept" % cls]
        L += ["{"]
        L += ["    if (started_) {", "        return;", "    }"]
        L += ["    started_    = true;"]
        L += ["    terminated_ = false;"]
        L += ["    clearCompletion();"]
        L += ["    state_      = State::None;"]
        L += ["    for (std::uint8_t i = 0U; i < kRegionCount; ++i) {"]
        L += ["        active_[i] = kNone;"]
        L += ["    }"]
        if ir.has_deferred():
            L += ["    deferredCount_ = 0U;"]
            L += ["    deferOverflow_ = false;"]
        if ir.has_history():
            L += ["    for (std::uint8_t i = 0U; i < kRegionCount; ++i) {"]
            L += ["        history_[i] = kNone;"]
            L += ["    }"]
        L += ["", "    // Every ROOT region is started, in ascending order."]
        L += ["    for (std::uint8_t r = 0U; r < kRegionCount; ++r) {"]
        L += ["        if (kRegionOwner[r] != kNone) {"]
        L += ["            continue;  // not a root region"]
        L += ["        }"]
        L += ["        if (kRegionInitial[r] == kNone) {"]
        L += ["            continue;"]
        L += ["        }"]
        L += ["        executeAction(kRegionInitialAction[r]);"]
        L += ["        enterPath(kRegionInitial[r], kNone);"]
        L += ["        const std::uint8_t leaf = land(kRegionInitial[r]);"]
        L += ["        if (state_ == State::None) {"]
        L += ["            state_ = static_cast<State>(leaf);"]
        L += ["        }"]
        L += ["    }"]
        L += ["    if (terminated_) {"]
        L += ["        clearCompletion();"]
        L += ["    }"]
        L += ["    runToCompletion();"]
        if ir.has_deferred():
            L += ["    drainDeferred();"]
        L += ["}", ""]

        # dispatch
        L += doc(['@brief  Processes one event to completion (UML run-to-completion).',
                 '@param  event  Event identifier.',
                 '@return true when the event triggered a transition.'])
        L += ["bool %s::dispatch(Event event) noexcept" % cls]
        L += ["{"]
        L += ["    if (!started_) {", "        start();", "    }"]
        L += ["    if (terminated_) {"]
        L += ["        return false;  // no event is processed after terminate"]
        L += ["    }"]
        L += ["    if ((event == Event::Completion) ||"]
        L += ["        (static_cast<std::uint8_t>(event) >= kEventCount)) {"]
        L += ["        return false;  // completion cannot be dispatched externally"]
        L += ["    }"]
        L += ["", "    // The transition is tried FIRST. UML 2.5.1, 14.2.3.4.4"]
        L += ["    // makes this explicit: if a deferred event type is used in a"]
        L += ["    // trigger of a transition leaving the deferring state, the"]
        L += ["    // transition wins -- it is an override option."]
        L += ["    const bool handled = tryEvent(static_cast<std::uint8_t>(event));"]
        if ir.has_deferred():
            L += ["    if (handled) {"]
            L += ["        runToCompletion();"]
            L += ["        drainDeferred();"]
            L += ["        return true;"]
            L += ["    }"]
            L += ["    if (isDeferred(static_cast<std::uint8_t>(event))) {"]
            L += ["        defer(static_cast<std::uint8_t>(event));"]
            L += ["        return true;  // consumed from the pool, retained inside"]
            L += ["    }"]
        else:
            L += ["    if (handled) {", "        runToCompletion();", "    }"]
        L += ["    return handled;"]
        L += ["}", ""]

        # doActivity
        L += doc(['@brief Runs the do-behaviour of the active state and its ancestors.'])
        L += ["void %s::doActivity() noexcept" % cls]
        L += ["{"]
        L += ["    // A terminated machine runs no behaviour."]
        L += ["    if (!started_ || terminated_) {", "        return;", "    }"]
        L += ["    // Every active region is served, innermost first."]
        L += ["    for (std::uint8_t r = kRegionCount; r > 0U; --r) {"]
        L += ["        const std::uint8_t state = active_[r - 1U];"]
        L += ["        if (state != kNone) {"]
        L += ["            executeDoActivity(state);"]
        L += ["        }"]
        L += ["    }"]
        L += ["}", ""]

        # isIn
        L += doc(['@brief  Tests whether the machine is in a state, ancestors included.',
                 '@param  state  State to test against.',
                 '@return true when @p state is active or is a parent of the active state.'])
        L += ["bool %s::isIn(State state) const noexcept" % cls]
        L += ["{"]
        L += ["    // The configuration is a VECTOR: a state is active when it is"]
        L += ["    // the leaf of any region. Walking one chain would miss the"]
        L += ["    // states of the other regions of an orthogonal state."]
        L += ["    for (std::uint8_t r = 0U; r < kRegionCount; ++r) {"]
        L += ["        if (active_[r] == static_cast<std::uint8_t>(state)) {"]
        L += ["            return true;"]
        L += ["        }"]
        L += ["    }"]
        L += ["    return false;"]
        L += ["}", ""]

        # isTerminated
        L += doc(['@brief  Reports whether the machine has stopped.',
                 '@return true after a terminate pseudostate or the root final state.'])
        L += ["bool %s::isTerminated() const noexcept" % cls]
        L += ["{"]
        L += ["    if (terminated_) {", "        return true;", "    }"]
        L += ["    // The machine is done when EVERY root region sits in a final state."]
        L += ["    bool any = false;"]
        L += ["    for (std::uint8_t r = 0U; r < kRegionCount; ++r) {"]
        L += ["        if (kRegionOwner[r] != kNone) {"]
        L += ["            continue;"]
        L += ["        }"]
        L += ["        any = true;"]
        L += ["        const std::uint8_t state = active_[r];"]
        L += ["        if ((state >= kStateCount) || (kKind[state] != kKindFinal)) {"]
        L += ["            return false;"]
        L += ["        }"]
        L += ["    }"]
        L += ["    return any;"]
        L += ["}", ""]

        # names
        L += doc(['@brief  Human-readable state name, for logging and debugging.',
                 '@param  state  State identifier.',
                 '@return A static string; never NULL.'])
        L += ["const char* %s::stateName(State state) noexcept" % cls]
        L += ["{"]
        L += ["    const std::uint8_t i = static_cast<std::uint8_t>(state);"]
        L += ["    if (i >= kStateCount) {"]
        L += ['        return "<none>";']
        L += ["    }"]
        L += ["    return kStateNames[i];"]
        L += ["}", ""]
        L += doc(['@brief  Human-readable event name, for logging and debugging.',
                 '@param  event  Event identifier.',
                 '@return A static string; never NULL.'])
        L += ["const char* %s::eventName(Event event) noexcept" % cls]
        L += ["{"]
        L += ["    const std::uint8_t i = static_cast<std::uint8_t>(event);"]
        L += ["    if (i >= kEventCount) {"]
        L += ['        return "<invalid>";']
        L += ["    }"]
        L += ["    return kEventNames[i];"]
        L += ["}", ""]
        return L


    # ----------------------------------------------------- MCU integration #

    def demo_source(self) -> str:
        """An example that drives the generated class in an MCU super loop."""
        ir = self.ir
        cls = self.cls
        ns = self.ns
        full = "%s::%s" % (ns, cls)
        has_ctx = ir.has_context()
        events = list(ir.events[1:])

        L: List[str] = []
        L += banner(ir, "%s_main.cpp" % cls, "bare-metal integration example")
        L += [
            "/**",
            " * @details",
            " * This file is a TEMPLATE, not part of the library. It shows the",
            " * three steps a microcontroller application needs:",
            " *",
            " *   1. construct the instance,",
            " *   2. start it, which runs the initial transition,",
            " *   3. loop forever, feeding events in and letting the machine",
            " *      run to completion between them.",
            " *",
            " * @par WHAT YOU HAVE TO WRITE",
            " *",
            " * Everything below marked 'extern' is YOURS. The generator",
            " * never defines it, because only you know the hardware. There",
            " * are two groups, and they are different in kind:",
            " *",
            " *   (a) BOARD HOOKS -- boardInit / boardEventPending /",
            " *       boardNextEvent / boardIdle. They belong to THIS",
            " *       template, not to the state machine. They exist so the",
            " *       example can run without knowing your board. If you",
            " *       already have a scheduler or an interrupt that produces",
            " *       events, drop this file and call dispatch() from there.",
            " *",
            " *   (b) BEHAVIOUR FUNCTIONS -- the calls you typed into the",
            " *       model's entry / exit / do / effect / guard fields.",
            " *       These are NOT optional: the machine calls them at the",
            " *       exact moments the diagram says. They are listed in",
            " *       %s.hpp under 'you must provide'." % cls,
            " *",
            " * If any of them is missing the LINKER fails with",
            " * 'undefined reference'; nothing is detected at compile time.",
            " *",
            " * Build it together with the generated implementation:",
            " *",
            " *   c++ -std=c++11 -Wall -Wextra -pedantic -fno-exceptions -fno-rtti \\",
            " *       %s.cpp %s_main.cpp -o %s_app" % (cls, cls, cls),
            " */",
            "",
            '#include "%s.hpp"' % cls,
            "",
            section("behaviour you provide"),
        ] + self._demo_required_note() + [
            "// ------------------------------------------------- board interface --",
            "// Supply these from your board-support package. They are the ONLY",
            "// place where this example touches the hardware.",
            "",
            "/// @brief Brings up clocks, GPIO and the event source.",
            "extern void boardInit() noexcept;",
            "",
            "/// @brief Returns true while an event waits in the input queue.",
            "extern bool boardEventPending() noexcept;",
            "",
            "/**",
            " * @brief  Removes and returns the next event from the input queue.",
            " * @return One of the %s::Event enumerators." % full,
        ]
        if events:
            L += [" *", " * Events understood by this machine:"]
            for name in events:
                L += [" *   %s::Event::%s" % (full, self._ev_name(name))]
        L += [
            " */",
            "extern %s::Event boardNextEvent() noexcept;" % full,
            "",
            "/// @brief Called once per pass; feed the watchdog here.",
            "extern void boardIdle() noexcept;",
            "",
            "// ---------------------------------------------- application data --",
            "",
        ]
        if has_ctx:
            L += ["/// @brief Data shared by every action and guard of the machine.",
                  "static %s::Context g_ctx;" % full, ""]
        L += [
            "/// @brief The single state-machine instance of this application.",
            "static %s g_machine%s;" % (full, "(&g_ctx)" if has_ctx else ""),
            "",
            "/**",
            " * @brief  Application entry point.",
            " * @return Never returns; the super loop runs until power-down.",
            " */",
            "int main()",
            "{",
            "    boardInit();",
            "",
            "    // start() runs the initial transition and the entry behaviours",
            "    // of the first state configuration.",
            "    g_machine.start();",
            "",
            "    // Super loop. One pass handles at most one event and then runs",
            "    // the do-behaviours. Dispatching is run-to-completion: the call",
            "    // returns only once the machine is stable again, so no event can",
            "    // interrupt a transition halfway through.",
            "    for (;;)",
            "    {",
            "        if (boardEventPending())",
            "        {",
            "            static_cast<void>(g_machine.dispatch(boardNextEvent()));",
            "        }",
            "",
            "        g_machine.doActivity();",
            "",
            "        if (g_machine.isTerminated())",
            "        {",
            "            // A final state or a terminate pseudostate was reached;",
            "            // the machine ignores every further event.",
            "            break;",
            "        }",
            "",
            "        boardIdle();",
            "    }",
            "",
            "    // Stopped, but a bare-metal main() must not return.",
            "    for (;;)",
            "    {",
            "        boardIdle();",
            "    }",
            "}",
            "",
        ]
        return "\n".join(allman(L))


def generate_cpp(sm, with_demo: bool = True, resolve=None) -> Dict[str, str]:
    """Produces a {file_name: content} dictionary from the model.

    With ``with_demo`` on, the third generated file is not a UNIT TEST but an
    integration example driving the class in an MCU super loop.
    """
    ir = build_ir(sm, resolve)
    gen = CppGenerator(ir)
    files = {"%s.hpp" % gen.cls: gen.header(),
             "%s.cpp" % gen.cls: gen.source()}
    if with_demo:
        files["%s_main.cpp" % gen.cls] = gen.demo_source()
    return files
