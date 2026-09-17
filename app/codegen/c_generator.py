"""C11 + GNU extensions hierarchical state machine generator - MISRA C:2012.

The generated code:
  * uses no dynamic memory and contains no recursion,
  * keeps every table `static const` (it lives in flash),
  * is reentrant through a single `<prefix>_t` instance,
  * compiles without warnings under -std=gnu11 -Wall -Wextra -Werror,
  * is produced with the mandatory/required rules of MISRA C:2012 in mind.

The runtime algorithm follows the UML 2.5.1 run-to-completion semantics:
exit path -> transition effect -> entry path -> descent into the default
substate -> resolution of completion (event-less) transitions. It also
supports shallow/deep history and terminate pseudostates.
"""

from __future__ import annotations

import re
from typing import Dict, List

from ..core.naming import screaming_snake
from .ir import as_statement as _as_statement
from .ir import (Ir, IrState, KIND_CHOICE, KIND_COMPOSITE, KIND_FINAL,
                 KIND_HIST_DEEP, KIND_HIST_SHALLOW, KIND_SIMPLE,
                 KIND_TERMINATE, NONE, REGION_NONE, TKIND_EXTERNAL,
                 TKIND_INTERNAL, TKIND_LOCAL, build_ir)

TOOL_NAME = "UML Design Studio"
TOOL_VERSION = "2.0.0"

MISRA_NOTE = [
    " * @par MISRA C:2012 compliance",
    " * - No dynamic memory (Dir 4.12), no recursion (R17.2), no goto (R15.1).",
    " * - All integer types are the fixed-width types of <stdint.h> (Dir 4.6).",
    " * - Every switch has a default label (R16.4); every if / else-if chain",
    " *   ends with an else (R15.7).",
    " * - Every control-statement body is a compound statement (R15.6).",
    " * - Pointer parameters are checked against NULL before use (Dir 4.14).",
    " * - Known deviations: R15.5 (multiple return statements, used for",
    " *   defensive early exits) and R8.9 (lookup tables kept at file scope so",
    " *   they live in flash rather than on the stack).",
]


# --------------------------------------------------------------------------- #
#   Text helpers
# --------------------------------------------------------------------------- #

def c_comment(text: str) -> str:
    """Makes a text safe to place inside a single-line C comment."""
    one = " ".join(str(text).split())
    return one.replace("*/", "* /")


#: The statement-termination rule lives in the IR layer; the definition is
#: single because effects merged along junction paths use the SAME rule.
as_statement = _as_statement


def as_expression(expr: str) -> str:
    """Turns guard text into a valid C expression (a trailing ; is dropped)."""
    s = expr.strip()
    while s.endswith(";"):
        s = s[:-1].rstrip()
    return s


def align_rows(rows, gap: int = 2) -> List[str]:
    """Writes (code, comment) pairs so THE COMMENTS START IN THE SAME COLUMN.

    Fixed-width "%-38s" formats broke down as the names grew longer: the
    BLINKY_TRANSITION_COUNT line drifted away from the others and the file
    looked untidy. The width is COMPUTED from the longest code in THAT BLOCK.
    """
    kodlar = [k for k, _c in rows]
    en_uzun = max((len(k) for k in kodlar), default=0)
    out = []
    for kod, yorum in rows:
        if yorum:
            out.append("%s%s%s" % (kod, " " * (en_uzun - len(kod) + gap), yorum))
        else:
            out.append(kod)
    return out


def align_enum(rows, gap: int = 1) -> List[str]:
    """Aligns (name, "= value,", comment) triples into THREE COLUMNS.

    Inside an enum body both the "=" signs and the comments stay in the same
    column; aligning a single column left the values ragged.
    """
    name_w = max((len(a) for a, _d, _y in rows), default=0)
    value_w = max((len(d) for _a, d, _y in rows), default=0)
    out = []
    for name, value, yorum in rows:
        left = "%-*s %-*s" % (name_w, name, value_w, value)
        out.append((left + " " * gap + yorum).rstrip() if yorum else left.rstrip())
    return out


#: EVERY line that leaves an OPENING brace at its end.
#:
#: The rule is single and general: on a line whose code ends with `{` the
#: brace moves to ITS OWN LINE. Writing a separate pattern per keyword left
#: one style out every time -- first control statements, then multi-line
#: conditions, then array initialisers and `extern "C"`.
#:
#: String and character literals are stripped FIRST: the brace at the end
#: of a `printf("{")` line is NOT code.
_SONDA_SUSLU = re.compile(r"^(?P<pad>\s*)(?P<govde>.*\S)\s*\{\s*$")

#: A trailing `// ...` note.
_ROW_NOTE = re.compile(r"\s*(//[^\n]*)$")

#: The START of a control statement (its parenthesis may be on another line).
_CONTROL_START = re.compile(
    r"^(?P<pad>\s*)(?:\}\s*)?"
    r"(?:if|else\s+if|for|while|switch)\b")

#: String and character literals -- dropped when counting parentheses.
_SABIT = re.compile(r'"(?:[^"\\]|\\.)*"' + r"|'(?:[^'\\]|\\.)*'")


def _paren_farki(kod: str) -> int:
    """The OPEN parenthesis balance of the line (strings are not counted)."""
    duz = _SABIT.sub("", kod)
    return duz.count("(") - duz.count(")")


#: `} name;` -- the NAMED closing line of a typedef whose body has ended.
#:
#: `};` (an unnamed close) and `} while (...);` are OUT OF SCOPE: one has
#: no name, the other has a parenthesis after the name.
_ADLI_KAPANIS = re.compile(r"^\s*\}\s*[A-Za-z_]\w*\s*;")


def blank_before_close(lines: List[str]) -> List[str]:
    """Puts a blank line BEFORE a `} name;` line.

    The user asked for a breath between the last member of a type body and
    the typedef name, so the eye can pick out where the body ends AT ONCE.
    The rule is applied in one place, because there are many emission points
    and forgetting one would leave two styles in the same file -- which is
    exactly how `blinky_transition_t` and `drawable_t` got missed at first.
    """
    out: List[str] = []
    for line in lines:
        if (_ADLI_KAPANIS.match(line) and out and out[-1].strip()
                and out[-1].strip() != "{"):
            out.append("")
        out.append(line)
    return out


def _single_line(text: str) -> str:
    """Reduces a user note to a SINGLE-LINE comment.

    The note field may be written over several lines, while `///<` and
    `/**< ... */` are single-line. Dropping the raw text in would break the
    comment -- and the code after it. `c_comment` also defuses a comment close.
    """
    return c_comment(" ".join((text or "").split()))


def allman(lines: List[str]) -> List[str]:
    """Moves opening braces ONTO THEIR OWN LINE.

    Functions in the generated code were already in Allman style, but control
    statements stayed K&R (`if (x) {`); two styles sat in the same file. The
    user asked to "open the braces".

    THERE IS ONE RULE: on every line whose code ends with `{`, the brace moves
    to the next line. Patterns written per keyword came first, and each time
    one style was left out -- multi-line conditions, array initialisers,
    `extern "C"`. A single rule covers all of them and needs no rethinking for
    a style added later.

    IN A MULTI-LINE CONDITION the indentation is taken from the line the
    statement STARTS on:
        while ((steps < LIMIT) &&
               (!me->terminated))
        {

    For that the open-parenthesis balance is tracked; looking at how the
    continuation line begins was not enough (a wrapped call list such as
    `source, found)) {` begins with an identifier).

    The transformation works on TEXT: instead of changing more than eighty
    emission points one by one, a single testable pass is used. Comment lines
    and the INSIDE of block comments are left alone.

    The same pass also puts a blank line before closing `} name;` lines (see
    :func:`blank_before_close`); every generator already goes through here, so
    the layout rules have a single door.
    """
    out: List[str] = []
    blok_yorumda = False
    #: Indentation and paren balance of a control statement not yet finished.
    acik_pad = None
    acik_derinlik = 0

    for line in lines:
        if blok_yorumda:
            out.append(line)
            if "*/" in line:
                blok_yorumda = False
            continue
        siyade = line.strip()
        if siyade.startswith("/*") and "*/" not in siyade:
            blok_yorumda = True
            out.append(line)
            continue
        if siyade.startswith(("*", "//", "/*")) or not siyade:
            out.append(line)
            continue

        # A trailing `// ...` note stays WITH THE STATEMENT; sticking it next to
        # the brace would make the note meaningless.
        notu = ""
        kod = line
        eslesme = _ROW_NOTE.search(line)
        if eslesme is not None:
            notu = "  " + eslesme.group(1)
            kod = line[:eslesme.start()]

        m = _SONDA_SUSLU.match(kod)
        if m is None:
            # No brace: if an open condition is running, update its balance.
            if acik_pad is not None:
                acik_derinlik += _paren_farki(kod)
                if acik_derinlik <= 0:
                    acik_pad = None
            else:
                bas = _CONTROL_START.match(kod)
                if bas is not None:
                    fark = _paren_farki(kod)
                    if fark > 0:
                        acik_pad = bas.group("pad")
                        acik_derinlik = fark
            out.append(line)
            continue

        body = m.group("govde").rstrip()
        # Indentation of the brace: the indentation the statement STARTS at when
        # we are inside a multi-line condition, otherwise the line's own.
        if acik_pad is not None:
            pad = acik_pad
            acik_pad = None
        else:
            pad = m.group("pad")

        if body.startswith("}"):
            # `} else {`  ->  `}` / `else` / `{`
            out.append(m.group("pad") + "}")
            body = body[1:].strip()
            if body:
                out.append(pad + body + notu)
            elif notu:
                out.append(pad + notu.strip())
        else:
            out.append(m.group("pad") + body + notu)
        out.append(pad + "{")
    return blank_before_close(out)


def indent_block(code: str, pad: str) -> List[str]:
    return [pad + ln if ln.strip() else "" for ln in code.splitlines()]


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
        "/*" + "*" * 76,
        " * @file    %s" % filename,
        " * @brief   '%s' state machine -- %s" % (ir.name, kind),
        " *",
        " * GENERATED FILE -- DO NOT EDIT BY HAND.",
        " * Generator: %s v%s" % (TOOL_NAME, TOOL_VERSION),
        " * Standard: ISO/IEC 9899:2011 (C11) with GNU extensions (-std=gnu11);",
        " *           no dynamic memory, no recursion.",
        " *",
    ]
    lines += MISRA_NOTE
    if ir.description:
        lines += [" *", " * %s" % c_comment(ir.description)]
    lines += [" " + "*" * 76 + "*/", ""]
    return lines


# --------------------------------------------------------------------------- #
#   The generator
# --------------------------------------------------------------------------- #

class CGenerator:
    """IR -> the (.h, .c) pair."""

    def __init__(self, ir: Ir) -> None:
        self.ir = ir
        self.p = ir.prefix                 # blinky
        self.P = ir.prefix.upper()         # BLINKY

    # -- naming # ----------------------------------------------------------- #
    #
    # UML names are UpperCamelCase (UML 2.5.1); C constants become
    # SCREAMING_SNAKE_CASE and the conversion PRESERVES THE WORD BOUNDARY:
    #     LedOn -> BLINKY_STATE_LED_ON   (formerly BLINKY_STATE_LEDON)
    # The conversion lives in app/core/naming.py; the validator checks for
    # collisions with the SAME function, or it could not see the real symbol.

    def state_enum(self, st: IrState) -> str:
        return "%s_STATE_%s" % (self.P, screaming_snake(st.name))

    def event_enum(self, name: str) -> str:
        return "%s_EVENT_%s" % (self.P, screaming_snake(name))

    def type_state(self) -> str:
        return "%s_state_t" % self.p

    def type_event(self) -> str:
        return "%s_event_t" % self.p

    def _demo_required_note(self) -> List[str]:
        """Counts the functions that come FROM THE MODEL in the MCU example."""
        needed = self.ir.required_functions()
        if not needed:
            return ["/*",
                    " * This model's behaviours are self-contained: they call",
                    " * nothing outside the generated code.",
                    " */",
                    ""]
        L = ["/*",
             " * The model's behaviours call these. Write them yourself; the",
             " * state machine calls them at the moments the diagram shows.",
             " *"]
        for sembol in needed:
            L.append(" *   %s" % sembol.summary())
            L.append(" *     -> %s" % ", ".join(sembol.sites))
        L += [
            " *",
            " * They are NOT declared here on purpose: their real signatures",
            " * live in your project, and a guessed prototype would compile",
            " * yet disagree with the definition. Include your own header.",
            " */",
            "",
        ]
        return L

    def _required_block(self) -> List[str]:
        """Documents the symbols the user has to SUPPLY.

        The entry/exit/do bodies and guard expressions in the model are C
        texts written by the user; the generator does NOT DEFINE the calls
        inside them. Unless we say so, the gap only surfaces at link time as
        "undefined reference to `led_write`" -- on the target hardware, late
        and in a form that is hard to read.

        NO PROTOTYPE IS WRITTEN, only DOCUMENTATION. The model carries no type
        information; an invented `void led_write();` declaration can silently
        disagree with the real signature, which is worse than declaring nothing.
        """
        needed = self.ir.required_functions()
        L: List[str] = [
            "/* %s you must provide -- */"
            % ("-" * (79 - len("/*  you must provide -- */"))),
            "/**",
            " * @par Functions this machine expects from your project",
            " *",
            " * The action and guard bodies in the model call the symbols",
            " * listed below. They are NOT generated; declare and define them",
            " * in your own sources (or include the header that declares",
            " * them) before linking.",
        ]
        if not needed:
            L += [" *",
                  " * This model calls none: every behaviour is self-contained.",
                  " */", ""]
            return L
        L += [" *"]
        for sembol in needed:
            L.append(" * - %s" % sembol.summary())
            L.append(" *     used by: %s" % ", ".join(sembol.sites))
        if self.ir.has_context():
            L += [" *",
                  " * The context type '%s' is also yours: every action and"
                  % self.ctx_type(),
                  " * guard sees it as 'ctx'. Define it in %s."
                  % self._first_include_name()]
        L += [" */", ""]
        return L

    def _first_include_name(self) -> str:
        """Extracts the FIRST header name from the user include lines.

        The line does not have to be QUOTED: an angle-bracket include is valid
        too, and the first line may be a comment. This name used to be taken by
        splitting unconditionally on the quote and picking the second part; for
        every first line without a quote, code generation crashed with
        IndexError. What the user saw was that NO code was generated at all,
        merely because one explanatory sentence could not be built.

        The FIRST quoted or angled line is scanned; when there is none, a
        neutral expression is returned.
        """
        for row in self.ir.user_includes:
            eslesme = re.search(r'[<"]([^>"]+)[>"]', row)
            if eslesme is not None:
                return eslesme.group(1)
        return "your own header"

    def type_obj(self) -> str:
        return "%s_t" % self.p

    def ctx_type(self) -> str:
        return self.ir.context_type if self.ir.has_context() else "void"

    # -- header # ----------------------------------------------------------- #

    def header(self) -> str:
        ir = self.ir
        L: List[str] = []
        L += banner(ir, "%s.h" % self.p, "public interface")
        guard = "%s_H" % self.P
        L += ["#ifndef %s" % guard, "#define %s" % guard, ""]
        L += ["#include <stdbool.h>", "#include <stdint.h>", ""]
        if ir.user_includes:
            L += ["/* User-supplied headers (from the model settings). */"]
            L += list(ir.user_includes)
            L += [""]
        L += ["#ifdef __cplusplus", 'extern "C" {', "#endif", ""]

        # The SAME place as on the C++ side: at the start of the sections.
        L += self._required_block()
        L += ["/* ----------------------------------------------------------- dimensions -- */"]
        # VALUES IN THE SAME COLUMN. Written with fixed padding, long names
        # (BLINKY_MAX_RUN_TO_COMPLETION_STEPS) pushed the line out of line.
        measures = [
            ("%s_STATE_COUNT" % self.P, "(%uU)" % ir.state_count,
             "Number of vertices in the state table."),
            ("%s_EVENT_COUNT" % self.P, "(%uU)" % ir.event_count,
             "Number of event identifiers, including the internal one."),
            ("%s_TRANSITION_COUNT" % self.P,
             "(%uU)" % max(ir.tran_count, 1),
             "Number of rows in the transition table."),
            ("%s_REGION_COUNT" % self.P, "(%uU)" % ir.region_count,
             "Number of regions; the active configuration has one leaf per region."),
            ("%s_MAX_DEPTH" % self.P, "(%uU)" % ir.max_depth,
             "Deepest nesting level; bounds the entry-path buffer."),
            ("%s_MAX_RUN_TO_COMPLETION_STEPS" % self.P,
             "(16U * %s_REGION_COUNT)" % self.P,
             "Safety bound on the completion loop: 16 steps PER REGION."),
            ("%s_MAX_WALK_STEPS" % self.P, "(4096U)",
             "Safety bound on the entry/exit walks; a sane model never reaches it."),
            ("%s_REGION_NONE" % self.P, "(255U)",
             "Invalid region index. A separate number space from the state index."),
        ]
        if ir.has_deferred():
            measures.append(
                ("%s_DEFER_POOL_SIZE" % self.P, "(16U)",
                 "Retained deferred event occurrences; a full pool is reported."))
        width = max(len(name) for name, _d, _a in measures)
        for name, value, description in measures:
            L += ["/** @brief %s */" % description]
            L += ["#define %-*s %s" % (width, name, value)]
        L += [""]

        L += ["/* --------------------------------------------------------------- states -- */"]
        L += ["/**", " * @brief Identifiers of the states and pseudostates.",
              " *",
              " * The values are indices into the generated lookup tables, so they",
              " * must not be reordered by hand.", " */"]
        L += ["typedef enum", "{"]
        # Columns are computed PER BLOCK (see align_rows): fixed "%-38s" padding
        # shifted the line as soon as a name grew.
        rows = []
        for st in ir.states:
            kind_txt = {KIND_SIMPLE: "simple", KIND_COMPOSITE: "composite",
                        KIND_FINAL: "final", KIND_CHOICE: "choice/junction",
                        KIND_TERMINATE: "terminate",
                        KIND_HIST_SHALLOW: "shallow history",
                        KIND_HIST_DEEP: "deep history"}[st.kind]
            # THE USER'S NOTE GOES INTO THE CODE TOO.
            #
            # The note field is collected in two separate editors and travelled all
            # the way to the IR, but NO output used it: the user's explanation of
            # "why this state exists" stayed on the diagram and never reached the
            # embedded engineer reading the code. The class diagram already emits its
            # notes this way.
            description = "%s, depth %d" % (kind_txt, st.depth)
            if st.note:
                description = "%s -- %s" % (description, _single_line(st.note))
            rows.append(
                ("    %s" % self.state_enum(st), "= %uU," % st.index,
                 "/**< %s */" % description))
        rows.append(("    %s_STATE_NONE" % self.P, "= 255U",
                         "/**< invalid / no state */"))
        L += align_enum(rows)
        # A BLANK LINE before the close: the body and "} type;" were stuck
        # together.
        L += ["", "} %s;" % self.type_state(), ""]

        L += ["/* --------------------------------------------------------------- events -- */"]
        L += ["/**", " * @brief Identifiers of the events accepted by the machine.", " */"]
        L += ["typedef enum", "{"]
        rows = []
        for i, ev in enumerate(ir.events):
            note = "/**< internal: completion event */" if i == 0 else ""
            rows.append(("    %s" % self.event_enum(ev),
                             "= %uU," % i, note))
        rows.append(("    %s_EVENT_INVALID" % self.P, "= 255U", ""))
        L += align_enum(rows)
        L += ["", "} %s;" % self.type_event(), ""]

        L += ["/* ------------------------------------------------------------- instance -- */"]
        L += ["/**", " * @brief One running instance of the state machine.",
              " *",
              " * Create as many instances as you need; the tables are shared and",
              " * read-only. Do not write to the fields from outside -- use the",
              " * functions below.", " */"]
        L += ["typedef struct", "{"]
        # The type, the field name and the comment are aligned as THREE SEPARATE
        # COLUMNS. With fixed padding a long name such as "terminated" shifted the
        # comment and the struct looked untidy.
        fields = [
            (self.type_state(), "state;", "/**< active leaf state */"),
            (self.ctx_type(), "*ctx;",
             "/**< user context, visible as 'ctx' in actions */"),
            ("bool", "started;", "/**< has %s_start() been called */" % self.p),
            ("bool", "terminated;", "/**< stopped by terminate or final */"),
            ("bool",
             "completion_pending[%s_REGION_COUNT];" % self.P,
             "/**< per region: a state was entered, completion is due */"),
        ]
        fields.append(("uint8_t",
                        "active[%s_REGION_COUNT];" % self.P,
                        "/**< active leaf of each region (255 = inactive) */"))
        if self.ir.has_deferred():
            fields.append(("uint8_t",
                            "deferred[%s_DEFER_POOL_SIZE];" % self.P,
                            "/**< retained deferred event occurrences */"))
            fields.append(("uint8_t", "deferred_count;",
                            "/**< how many of them are in the pool */"))
            fields.append(("bool", "defer_overflow;",
                            "/**< the pool was full and one was dropped */"))
        if self.ir.has_history():
            fields.append(("uint8_t",
                            "history[%s_REGION_COUNT];" % self.P,
                            "/**< last active substate PER REGION */"))
        tip_g = max(len(t) for t, _a, _y in fields)
        L += align_rows([("    %-*s %s" % (tip_g, tip, name), yorum)
                         for tip, name, yorum in fields])
        # A BLANK LINE before the close: the body and "} type;" were stuck
        # together.
        L += ["", "} %s;" % self.type_obj(), ""]

        L += ["/* ------------------------------------------------------------------ API -- */", ""]
        L += ["/**",
              " * @brief  Initialises an instance. Does NOT start the machine yet.",
              " * @param  me   Instance to initialise; ignored when NULL.",
              " * @param  ctx  User context handed to every action and guard.",
              " * @note   Call %s_start() afterwards to run the initial transition."
              % self.p,
              " */"]
        L += ["void %s_construct(%s *me, %s *ctx);" % (self.p, self.type_obj(), self.ctx_type()), ""]
        L += ["/**",
              " * @brief  Runs the initial transition and the entry behaviours.",
              " * @param  me  Instance previously initialised with %s_construct()." % self.p,
              " * @note   Calling it twice has no effect; the machine starts once.",
              " */"]
        L += ["void %s_start(%s *me);" % (self.p, self.type_obj()), ""]
        L += ["/**",
              " * @brief  Processes one event to completion (UML run-to-completion).",
              " *",
              " * Searches the active state and its ancestors for an enabled",
              " * transition, executes the exit chain, the effect and the entry",
              " * chain, then resolves any completion transitions.",
              " *",
              " * @param  me     Instance; the machine is started implicitly if needed.",
              " * @param  event  Event identifier from " + self.type_event() + ".",
              " * @retval true   The event triggered a transition.",
              " * @retval false  The event is not handled in this configuration.",
              " */"]
        L += ["bool %s_dispatch(%s *me, %s event);"
              % (self.p, self.type_obj(), self.type_event()), ""]
        L += ["/**",
              " * @brief  Runs the do-behaviour of every active state, once.",
              " *",
              " * WHAT THIS IS, AND WHAT IT IS NOT.",
              " *",
              " * UML 2.5.1, 14.5.9.6 defines doActivity as a Behavior that",
              " * \"is executed while being in the State. The execution starts",
              " * when this State is entered, and ceases either by itself when",
              " * done, or when the State is exited, whichever comes first.\"",
              " * Two properties follow from that wording:",
              " *",
              " *   - it runs CONCURRENTLY with the rest of the machine, and",
              " *   - it is ABORTED mid-execution when the state is left.",
              " *",
              " * NEITHER IS PROVIDED HERE, and neither can be without a second",
              " * flow of control. Adding one would mean a thread or an",
              " * interrupt, which breaks run-to-completion and forces stack",
              " * sizing, scheduling and reentrancy requirements onto the",
              " * caller. This generator refuses that rather than implying",
              " * parallelism it cannot deliver.",
              " *",
              " * What IS provided is a COOPERATIVE slice: each call runs one",
              " * short, non-blocking step of the do-behaviour of every state",
              " * that is currently active, outermost region last. Write each",
              " * body so that it returns promptly; it is never preempted and",
              " * never interrupted by an exit.",
              " *",
              " * @param  me  Instance; ignored when not started or terminated.",
              " * @note   Call this once per super-loop pass or timer tick.",
              " */"]
        L += ["void %s_do(%s *me);" % (self.p, self.type_obj()), ""]
        L += ["/**",
              " * @brief  Tests whether the machine is in a state, ancestors included.",
              " * @param  me     Instance to query.",
              " * @param  state  State to test against.",
              " * @return true when @p state is the active state or one of its parents.",
              " */"]
        L += ["bool %s_is_in(const %s *me, %s state);"
              % (self.p, self.type_obj(), self.type_state()), ""]
        L += ["/**",
              " * @brief  Reports whether the machine has stopped.",
              " * @param  me  Instance to query.",
              " * @return true after a terminate pseudostate or a top-level final state.",
              " */"]
        L += ["bool %s_is_terminated(const %s *me);" % (self.p, self.type_obj()), ""]

        if self.ir.has_time_events():
            L += ["/* ------------------------------------------------- timer hooks -- */"]
            L += ["/**",
                  " * @brief  Starts a relative time trigger, written by YOU.",
                  " *",
                  " * The model uses after() triggers. UML treats a TimeEvent as a",
                  " * trigger; KEEPING TIME IS NOT THE MACHINE'S JOB, so the",
                  " * generated code only says when a timer should run and when it",
                  " * must be cancelled. You start your own timer here and post the",
                  " * matching event when it expires:",
                  " *",
                  " *     %s_dispatch(me, (%s)event);"
                  % (self.p, self.type_event()),
                  " *",
                  " * The timer is started on ENTRY to the state and cancelled on",
                  " * EXIT, so a pending timer can never fire into a state that has",
                  " * already been left. An internal transition runs neither, which",
                  " * is exactly what UML requires: it does not restart the timer.",
                  " *",
                  " * @param  me     Instance the timer belongs to.",
                  " * @param  state  The state that is being entered.",
                  " * @param  event  The event identifier to post when it expires.",
                  " * @param  delay  The delay written in the model.",
                  " */"]
            L += ["void %s_timer_start(%s *me, uint8_t state, uint8_t event,"
                  " uint32_t delay);" % (self.p, self.type_obj()), ""]
            L += ["/**",
                  " * @brief  Cancels a relative time trigger, written by YOU.",
                  " * @param  me     Instance the timer belongs to.",
                  " * @param  state  The state that is being left.",
                  " * @param  event  The event identifier that will not be posted.",
                  " */"]
            L += ["void %s_timer_cancel(%s *me, uint8_t state, uint8_t event);"
                  % (self.p, self.type_obj()), ""]
        L += ["/**",
              " * @brief  Returns the active leaf state.",
              " * @param  me  Instance to query.",
              " * @return The active state, or %s_STATE_NONE before the machine starts."
              % self.P,
              " */"]
        L += ["%s %s_state(const %s *me);" % (self.type_state(), self.p, self.type_obj()), ""]
        L += ["/**",
              " * @brief  Human-readable state name, for logging and debugging.",
              " * @param  state  State identifier.",
              " * @return A static string; never NULL.",
              " */"]
        L += ["const char *%s_state_name(%s state);" % (self.p, self.type_state()), ""]
        L += ["/**",
              " * @brief  Human-readable event name, for logging and debugging.",
              " * @param  event  Event identifier.",
              " * @return A static string; never NULL.",
              " */"]
        L += ["const char *%s_event_name(%s event);" % (self.p, self.type_event()), ""]

        L += ["#ifdef __cplusplus", "}", "#endif", "", "#endif /* %s */" % guard, ""]
        return "\n".join(allman(L))

    # -- source # ----------------------------------------------------------- #

    def source(self) -> str:
        ir = self.ir
        L: List[str] = []
        L += banner(ir, "%s.c" % self.p, "implementation")
        L += ["#include <stddef.h>   /* NULL */"]
        L += ['#include "%s.h"' % self.p, ""]

        # ---- internal constants
        L += ["/* ------------------------------------------------------ internal constants -- */"]
        L += ["#define %s_KIND_SIMPLE     (0U)" % self.P]
        L += ["#define %s_KIND_COMPOSITE  (1U)" % self.P]
        L += ["#define %s_KIND_FINAL      (2U)" % self.P]
        L += ["#define %s_KIND_CHOICE     (3U)  /* choice + junction */" % self.P]
        L += ["#define %s_KIND_TERMINATE  (4U)" % self.P]
        L += ["#define %s_KIND_HISTORY_SHALLOW    (5U)  /* shallow history */" % self.P]
        L += ["#define %s_KIND_HISTORY_DEEP  (6U)  /* deep history */" % self.P]
        L += ["#define %s_TRANSITION_EXTERNAL   (0U)" % self.P]
        L += ["#define %s_TRANSITION_INTERNAL   (1U)" % self.P]
        L += ["#define %s_TRANSITION_LOCAL      (2U)" % self.P]
        L += ["#define %s_NO_ID           (-1)" % self.P]
        L += [""]

        # ---- transition record
        L += ["/* ------------------------------------------------------ transition row -- */"]
        L += ["typedef struct", "{"]
        L += ["    uint8_t source;    /**< source state            */"]
        L += ["    uint8_t target;    /**< target state            */"]
        L += ["    uint8_t event;    /**< triggering event        */"]
        L += ["    uint8_t kind;   /**< external/internal/local */"]
        L += ["    int16_t guard;  /**< guard id,  -1 = none    */"]
        L += ["    int16_t action; /**< effect id, -1 = none    */"]
        if self.ir.has_fork_join():
            L += ["    uint16_t extra_first;"
                  " /**< index into the shared extra[] */"]
            L += ["    uint8_t fork_count;"
                  "  /**< fork segment targets         */"]
            L += ["    uint8_t join_count;"
                  "  /**< join segment sources         */"]
        L += ["} %s_transition_t;" % self.p, ""]

        L += self._tables()
        L += self._forward_decls()
        L += self._behaviour_switches()
        L += self._guard_action_switches()
        L += self._runtime()
        L += self._public_api()
        return "\n".join(allman(L))

    # -------------------------------------------------------------------- tables #

    def _tables(self) -> List[str]:
        ir = self.ir
        L: List[str] = []

        L += ["/* --------------------------------------------------------------- tables -- */", ""]

        L += ["/** @brief Parent of each state (255 = root region). */"]
        L += ["static const uint8_t %s_parent[%s_STATE_COUNT] = {" % (self.p, self.P)]
        for st in ir.states:
            par = "255U" if st.parent == NONE else "%uU" % st.parent
            L += ["    %-5s /* %-3u %s */" % (par + ",", st.index, c_comment(st.name))]
        L += ["};", ""]

        L += ["/** @brief Kind of each state (simple/composite/final/choice/terminate/history). */"]
        L += ["static const uint8_t %s_kind[%s_STATE_COUNT] = {" % (self.p, self.P)]
        kmap = {KIND_SIMPLE: "%s_KIND_SIMPLE" % self.P,
                KIND_COMPOSITE: "%s_KIND_COMPOSITE" % self.P,
                KIND_FINAL: "%s_KIND_FINAL" % self.P,
                KIND_CHOICE: "%s_KIND_CHOICE" % self.P,
                KIND_TERMINATE: "%s_KIND_TERMINATE" % self.P,
                KIND_HIST_SHALLOW: "%s_KIND_HISTORY_SHALLOW" % self.P,
                KIND_HIST_DEEP: "%s_KIND_HISTORY_DEEP" % self.P}
        for st in ir.states:
            L += ["    %-26s /* %s */" % (kmap[st.kind] + ",", c_comment(st.name))]
        L += ["};", ""]

        # NOTE: the per-state "initial_child" / "initial_action" tables were
        # REMOVED. The default entry is now PER REGION
        # (<p>_region_initial / <p>_region_initial_action); a single field was
        # never enough for an orthogonal state anyway. Keeping both tables would
        # mean storing the same information twice and would produce a
        # -Wunused-const-variable warning.

        if ir.has_history():
            L += ["/** @brief Default target of a history pseudostate (255 = none). */"]
            L += ["static const uint8_t %s_hist_default[%s_STATE_COUNT] = {"
                  % (self.p, self.P)]
            for st in ir.states:
                v = "255U" if st.history_default == NONE else "%uU" % st.history_default
                L += ["    %-5s /* %s */" % (v + ",", c_comment(st.name))]
            L += ["};", ""]

        L += ["/** @brief State names, for logging and debugging. */"]
        L += ["static const char *const %s_state_names[%s_STATE_COUNT] = {" % (self.p, self.P)]
        for st in ir.states:
            L += ['    "%s",' % st.name]
        L += ["};", ""]

        L += ["static const char *const %s_event_names[%s_EVENT_COUNT] = {" % (self.p, self.P)]
        for ev in ir.events:
            L += ['    "%s",' % ev]
        L += ["};", ""]

        # -- transition table
        if ir.has_fork_join():
            ekstra = ir.extra_table()
            L += ["/** @brief Fork targets and join sources, flattened. */"]
            L += ["static const uint8_t %s_extra[%uU] = {"
                  % (self.p, max(1, len(ekstra)))]
            if ekstra:
                for i, v in enumerate(ekstra):
                    L += ["    %-5s /* %-3u %s */"
                          % ("%uU," % v, i, c_comment(ir.states[v].name))]
            else:
                L += ["    0U  /* unused; ISO C forbids a zero-length array */"]
            L += ["};", ""]

        L += ["/** @brief Transition table, ordered by source state. */"]
        L += ["static const %s_transition_t %s_transitions[%s_TRANSITION_COUNT] = {" % (self.p, self.p, self.P)]
        if ir.tran_count == 0:
            L += ["    /* No transition in the model; ISO C forbids a zero-length array. */"]
            if ir.has_fork_join():
                L += ["    { 0U, 0U, 0U, %s_TRANSITION_EXTERNAL, -1, -1,"
                      " 0U, 0U, 0U }" % self.P]
            else:
                L += ["    { 0U, 0U, 0U, %s_TRANSITION_EXTERNAL, -1, -1 }"
                      % self.P]
        else:
            for t in ir.transitions:
                kind_txt = {TKIND_EXTERNAL: "%s_TRANSITION_EXTERNAL" % self.P,
                            TKIND_INTERNAL: "%s_TRANSITION_INTERNAL" % self.P,
                            TKIND_LOCAL: "%s_TRANSITION_LOCAL" % self.P}[t.kind]
                if ir.has_fork_join():
                    L += ["    { %3uU, %3uU, %3uU, %-22s %3d, %3d, %3uU, %3uU,"
                          " %3uU },  /* %s */"
                          % (t.source, t.target, t.event, kind_txt + ",",
                             t.guard, t.action, t.extra_first,
                             len(t.fork_targets), len(t.join_sources),
                             c_comment(t.text))]
                else:
                    L += ["    { %3uU, %3uU, %3uU, %-22s %3d, %3d },  /* %s */"
                          % (t.source, t.target, t.event, kind_txt + ",",
                             t.guard, t.action, c_comment(t.text))]
        L += ["};", ""]

        L += ["/** @brief Transition range per state: [first, first+count). */"]
        L += ["static const uint16_t %s_transition_first[%s_STATE_COUNT] = {" % (self.p, self.P)]
        for st in ir.states:
            first, _cnt = ir.tran_slice.get(st.index, (0, 0))
            L += ["    %-6s /* %s */" % ("%uU," % first, c_comment(st.name))]
        L += ["};", ""]
        L += ["static const uint16_t %s_transition_count[%s_STATE_COUNT] = {" % (self.p, self.P)]
        for st in ir.states:
            _first, cnt = ir.tran_slice.get(st.index, (0, 0))
            L += ["    %-6s /* %s */" % ("%uU," % cnt, c_comment(st.name))]
        L += ["};", ""]

        # -- REGION TABLES ----------------------------------------------- #
        if ir.has_deferred():
            L += ["/** @brief Deferred event types per state, as a bit mask."]
            L += [" *"]
            L += [" * UML 2.5.1, 14.2.3.4.4: an occurrence of a deferred type is"]
            L += [" * retained instead of being dispatched, until a state"]
            L += [" * configuration is reached where it is no longer deferred."]
            L += [" */"]
            L += ["static const uint32_t %s_defer_mask[%s_STATE_COUNT] = {"
                  % (self.p, self.P)]
            for st in ir.states:
                maske = 0
                for e in st.deferred:
                    maske |= (1 << e)
                names = ", ".join(ir.events[e] for e in st.deferred) or "none"
                L += ["    0x%08XU,  /* %-20s %s */"
                      % (maske, c_comment(st.name), c_comment(names))]
            L += ["};", ""]

        L += ["/** @brief Region each vertex lives in. */"]
        L += ["static const uint8_t %s_state_region[%s_STATE_COUNT] = {"
              % (self.p, self.P)]
        for st in ir.states:
            v = "255U" if st.region == REGION_NONE else "%uU" % st.region
            L += ["    %-5s /* %-3u %s */" % (v + ",", st.index,
                                              c_comment(st.name))]
        L += ["};", ""]

        L += ["/** @brief First region owned by a composite state (255 = none). */"]
        L += ["static const uint8_t %s_state_first_region[%s_STATE_COUNT] = {"
              % (self.p, self.P)]
        for st in ir.states:
            v = "255U" if st.first_region == REGION_NONE else "%uU" % st.first_region
            L += ["    %-5s /* %s */" % (v + ",", c_comment(st.name))]
        L += ["};", ""]

        L += ["/** @brief Number of regions owned by a state (0 = not composite). */"]
        L += ["static const uint8_t %s_state_region_count[%s_STATE_COUNT] = {"
              % (self.p, self.P)]
        for st in ir.states:
            L += ["    %-5s /* %s */" % ("%uU," % st.region_count,
                                         c_comment(st.name))]
        L += ["};", ""]

        L += ["/** @brief Owner of each region (255 = a root region). */"]
        L += ["static const uint8_t %s_region_owner[%s_REGION_COUNT] = {"
              % (self.p, self.P)]
        for reg in ir.regions:
            v = "255U" if reg.owner == NONE else "%uU" % reg.owner
            L += ["    %-5s /* %-3u %s */" % (v + ",", reg.index,
                                              c_comment(reg.name))]
        L += ["};", ""]

        L += ["/** @brief Default entry state of each region. */"]
        L += ["static const uint8_t %s_region_initial[%s_REGION_COUNT] = {"
              % (self.p, self.P)]
        for reg in ir.regions:
            v = "255U" if reg.initial_state == NONE else "%uU" % reg.initial_state
            L += ["    %-5s /* %s */" % (v + ",", c_comment(reg.name))]
        L += ["};", ""]

        L += ["/** @brief Effect id on the initial transition of each region. */"]
        L += ["static const int16_t %s_region_initial_action[%s_REGION_COUNT] = {"
              % (self.p, self.P)]
        for reg in ir.regions:
            L += ["    %-5s /* %s */" % ("%d," % reg.initial_action,
                                         c_comment(reg.name))]
        L += ["};", ""]

        L += ["/** @brief Initial state and effect of the root region. */"]
        L += ["#define %s_INITIAL_STATE   (%uU)" % (self.P, ir.root_initial)]
        L += ["#define %s_INITIAL_ACTION  (%d)" % (self.P, ir.root_initial_action)]
        L += ["/** @brief The root region index. */"]
        L += ["#define %s_ROOT_REGION     (%uU)"
              % (self.P, ir.root_regions[0] if ir.root_regions else 0)]
        L += [""]
        return L

    # ------------------------------------------------------------- declarations #

    def _forward_decls(self) -> List[str]:
        p, P = self.p, self.P
        obj = self.type_obj()
        L = [
            "/* ------------------------------------------- internal function prototypes -- */",
            "static bool    %s_evaluate_guard(%s *me, int16_t id);" % (p, obj),
            "static void    %s_execute_action(%s *me, int16_t id);" % (p, obj),
            "static void    %s_execute_entry(%s *me, uint8_t state);" % (p, obj),
            "static void    %s_execute_exit(%s *me, uint8_t state);" % (p, obj),
            "static void    %s_execute_do_activity(%s *me, uint8_t state);" % (p, obj),
        ] + ([
            "static void    %s_timers_start(%s *me, uint8_t state);" % (p, obj),
            "static void    %s_timers_cancel(%s *me, uint8_t state);" % (p, obj),
        ] if self.ir.has_time_events() else []) + [
            "static uint8_t %s_depth(uint8_t state);" % p,
            "static uint8_t %s_least_common_ancestor(uint8_t a, uint8_t b);" % p,
            "static bool    %s_is_ancestor(uint8_t maybe, uint8_t node);" % p,
            "static void    %s_enter_one(%s *me, uint8_t state);" % (p, obj),
            "static void    %s_exit_one(%s *me, uint8_t state);" % (p, obj),
            "static void    %s_activate_below(%s *me, uint8_t state);" % (p, obj),
            "static uint8_t %s_deepest_active_below(const %s *me, uint8_t state);" % (p, obj),
            "static void    %s_exit_below(%s *me, uint8_t state);" % (p, obj),
            "static uint8_t %s_leaf_of(const %s *me, uint8_t state);" % (p, obj),
            "static bool    %s_completed(const %s *me, uint8_t state);" % (p, obj),
            "static bool    %s_select(%s *me, uint8_t region, uint8_t event,"
            " uint8_t *out_source, uint16_t *out_transition);" % (p, obj),
        ] + ([
            "static bool    %s_join_ready(const %s *me,"
            " const %s_transition_t *transition);" % (p, obj, p),
            "static void    %s_enter_forked(%s *me,"
            " const %s_transition_t *transition);" % (p, obj, p),
        ] if self.ir.has_fork_join() else []) + [
            "static void    %s_enter_path(%s *me, uint8_t target, uint8_t top);" % (p, obj),
            "static uint8_t %s_descend(%s *me, uint8_t state);" % (p, obj),
        ]
        if self.ir.has_history():
            L += ["static uint8_t %s_resolve_history(%s *me, uint8_t h);" % (p, obj)]
        L += [
            "static uint8_t %s_land(%s *me, uint8_t target);" % (p, obj),
            "static void    %s_take(%s *me, const %s_transition_t *transition);"
            % (p, obj, p),
            "static bool    %s_try_event(%s *me, uint8_t event);" % (p, obj),
            "static void    %s_clear_completion(%s *me);" % (p, obj),
            "static void    %s_run_to_completion(%s *me);" % (p, obj),
        ] + ([
            "static bool    %s_is_deferred(const %s *me, uint8_t event);" % (p, obj),
            "static void    %s_defer(%s *me, uint8_t event);" % (p, obj),
            "static void    %s_drain_deferred(%s *me);" % (p, obj),
        ] if self.ir.has_deferred() else []) + [
            "",
            "/* In every action and guard body, 'ctx' is the user context and",
            "   'me' is the machine instance. */",
            "#define %s_UNUSED(x)   ((void)(x))" % P,
            "",
        ]
        return L

    # --------------------------------------------------------- entry/exit/do #

    def _ctx_preamble(self) -> List[str]:
        """Defines the local 'ctx' variable inside the action bodies."""
        return [
            "    %s *ctx = me->ctx;" % self.ctx_type(),
            "    %s_UNUSED(me);" % self.P,
            "    %s_UNUSED(ctx);" % self.P,
        ]

    def _behaviour_switches(self) -> List[str]:
        ir = self.ir
        L: List[str] = []
        # The name has to match the one in the DECLARATION exactly. When the
        # abbreviations were spelled out ("exec" -> "execute", "do" ->
        # "do_activity") this spot was missed because it is computed, and the
        # declared functions stayed UNDEFINED; -Werror broke the build.
        for tag, attr, title in (("entry", "entry", "entry"),
                                 ("exit", "exit", "exit"),
                                 ("do_activity", "do", "do")):
            fname = "%s_execute_%s" % (self.p, tag)
            L += ["/* ------------------------------------------------------------ %s actions -- */"
                  % title]
            L += ["static void %s(%s *me, uint8_t state)" % (fname, self.type_obj())]
            L += ["{"]
            L += self._ctx_preamble()
            bodies = [st for st in ir.states if getattr(st, attr)]
            if not bodies:
                L += ["    %s_UNUSED(state);" % self.P]
                L += ["    /* this model defines no %s behaviour */" % title]
            else:
                L += ["", "    switch (state) {"]
                for st in bodies:
                    L += ["    case %s:" % self.state_enum(st)]
                    L += ["    {"]
                    L += indent_block(as_statement(getattr(st, attr)), "        ")
                    L += ["        break;", "    }"]
                L += ["    default:", "        break;", "    }"]
            L += ["}", ""]
        return L + self._timer_switches()

    def _timer_switches(self) -> List[str]:
        """The switches that start/cancel the after(N) timers.

        In UML a TimeEvent is a trigger; KEEPING TIME is not the machine's job.
        So the generated code calls two HOOKS and the user implements them with
        their own timer. Posting the event is the user's job as well -- which
        leaves the interrupt context, the clock source and the resolution
        entirely in their hands.

        The hooks start ON ENTRY and are cancelled ON EXIT: a pending timer
        firing after the state was left, and sending the wrong event, is a
        common and hard-to-find bug.
        """
        ir = self.ir
        ucler = ir.time_triggers()
        if not ucler:
            return []
        p = self.p
        obj = self.type_obj()
        L: List[str] = []
        for label, kanca, description in (
                ("start", "%s_timer_start" % p, "starts"),
                ("cancel", "%s_timer_cancel" % p, "cancels")):
            L += ["/* --------------------------------------------------- timer %s -- */"
                  % label]
            L += ["/**"]
            L += [" * @brief %s the after() timers of a state."
                  % ("Starts" if label == "start" else "Cancels")]
            L += [" */"]
            L += ["static void %s_timers_%s(%s *me, uint8_t state)"
                  % (p, label, obj)]
            L += ["{"]
            L += ["    switch (state) {"]
            gruplu = {}
            for src, ev, gecikme in ucler:
                gruplu.setdefault(src, []).append((ev, gecikme))
            for src in sorted(gruplu):
                L += ["    case %s:" % self.state_enum(ir.states[src])]
                L += ["    {"]
                for ev, gecikme in gruplu[src]:
                    if label == "start":
                        # THE DELAY IS CAST TO THE PARAMETER TYPE. The expression is not
                        # always a constant: an int value such as `after(timeout_ms())`
                        # or `after(g_timeout)`, passed untouched into a uint32_t
                        # parameter, STOPPED the build with -Wsign-conversion -- with
                        # exactly the flags the README guarantees.
                        # da README'nin garanti ettigi bayraklarla.
                        L += ["        %s(me, (uint8_t)%s, (uint8_t)%s, (uint32_t)(%s));"
                              % (kanca, self.state_enum(ir.states[src]),
                                 self.event_enum(ir.events[ev]), gecikme)]
                    else:
                        L += ["        %s(me, (uint8_t)%s, (uint8_t)%s);"
                              % (kanca, self.state_enum(ir.states[src]),
                                 self.event_enum(ir.events[ev]))]
                L += ["        break;", "    }"]
            L += ["    default:", "        break;", "    }"]
            L += ["}", ""]
        return L

    # ------------------------------------------------------------ guard/action #

    def _guard_action_switches(self) -> List[str]:
        ir = self.ir
        L: List[str] = []

        L += ["/* --------------------------------------------------------------- guards -- */"]
        L += ["static bool %s_evaluate_guard(%s *me, int16_t id)" % (self.p, self.type_obj())]
        L += ["{"]
        L += self._ctx_preamble()
        L += ["", "    if (id < 0) {", "        return true;  /* no guard means always enabled */", "    }"]
        if ir.guards:
            L += ["", "    switch (id) {"]
            for i, g in enumerate(ir.guards):
                L += ["    case %d:" % i]
                L += ["        return (bool)(%s);" % as_expression(g)]
            L += ["    default:", "        break;", "    }"]
        else:
            L += ["", "    /* this model has no guards */"]
        L += ["", "    return false;  /* unknown guard id: fail safe */", "}", ""]

        L += ["/* -------------------------------------------------------------- effects -- */"]
        L += ["static void %s_execute_action(%s *me, int16_t id)" % (self.p, self.type_obj())]
        L += ["{"]
        L += self._ctx_preamble()
        if ir.actions:
            L += ["", "    switch (id) {"]
            for i, a in enumerate(ir.actions):
                L += ["    case %d:" % i]
                L += ["    {"]
                L += indent_block(as_statement(a), "        ")
                L += ["        break;", "    }"]
            L += ["    default:", "        break;  /* includes -1: no effect */", "    }"]
        else:
            L += ["", "    %s_UNUSED(id);" % self.P]
            L += ["    /* this model has no transition effects */"]
        L += ["}", ""]
        return L

    # ---------------------------------------------------------------- runtime #

    def _history_helpers(self) -> List[str]:
        """resolve_history + land: history/terminate resolution."""
        p, P = self.p, self.P
        obj = self.type_obj()
        L: List[str] = []
        if self.ir.has_history():
            L += [
                "/**",
                " * @brief  Resolves a history pseudostate to the remembered substate.",
                " * @return The leaf state to become active (UML 2.5.1, 14.2.3.4.5).",
                " */",
                "static uint8_t %s_resolve_history(%s *me, uint8_t h)" % (p, obj),
                "{",
                "    const uint8_t region = %s_state_region[h];" % p,
                "    const uint8_t owner = %s_parent[h];" % p,
                "    uint8_t stored = %s_STATE_NONE;" % P,
                "    uint16_t steps = 0U;",
                "",
                "    if (region != %s_REGION_NONE) {" % P,
                "        stored = me->history[region];",
                "    }",
                "",
                "    if (stored == %s_STATE_NONE) {" % P,
                "        stored = %s_hist_default[h];" % p,
                "    }",
                "    if ((stored == %s_STATE_NONE) && (region != %s_REGION_NONE)) {" % (P, P),
                "        stored = %s_region_initial[region];" % p,
                "    }",
                "    if (stored == %s_STATE_NONE) {" % P,
                "        return owner;  /* fail safe: the owner of the region */",
                "    }",
                "",
                "    %s_enter_path(me, stored, owner);" % p,
                "    if (%s_kind[h] == %s_KIND_HISTORY_DEEP) {" % (p, P),
                "        /* DEEP HISTORY RESTORES A CONFIGURATION, NOT A STATE.",
                "",
                "           UML 2.5.1, 14.2.3.7: a deepHistory Pseudostate represents",
                "           \"the most recent active state configuration of its owning",
                "           Region\", and a Transition terminating on it implies",
                "           \"restoring the Region to that same state configuration\".",
                "           When a remembered substate is itself orthogonal, EVERY one",
                "           of its regions comes back from the record. Activating the",
                "           defaults below the record instead would re-run that",
                "           region's initial effect -- a real side effect in firmware",
                "           -- and silently drop the remembered branch.",
                "",
                "           The walk is an explicit breadth-first work list: entry runs",
                "           outside-in, regions ascend, and two runs give the same",
                "           order. There is no recursion, and the queue cannot outgrow",
                "           the region count because each region contributes at most",
                "           one remembered state. */",
                "        uint8_t queue_state[%s_REGION_COUNT + 1U];" % P,
                "        uint8_t queue_head = 0U;",
                "        uint8_t queue_tail = 0U;",
                "",
                "        queue_state[queue_tail] = stored;",
                "        queue_tail++;",
                "        while ((queue_head < queue_tail) && (steps < %s_MAX_WALK_STEPS)) {" % P,
                "            const uint8_t current = queue_state[queue_head];",
                "            const uint8_t owned = %s_state_region_count[current];" % p,
                "            const uint8_t first = %s_state_first_region[current];" % p,
                "            uint8_t region_index;",
                "            queue_head++;",
                "            steps++;",
                "            for (region_index = 0U; region_index < owned; region_index++) {",
                "                const uint8_t inner = (uint8_t)(first + region_index);",
                "                const uint8_t remembered = me->history[inner];",
                "                if (remembered == (uint8_t)%s_STATE_NONE) {" % P,
                "                    const uint8_t fallback = %s_region_initial[inner];" % p,
                "                    if (fallback != (uint8_t)%s_STATE_NONE) {" % P,
                "                        %s_execute_action(me, %s_region_initial_action[inner]);" % (p, p),
                "                        %s_enter_one(me, fallback);" % p,
                "                        %s_activate_below(me, fallback);" % p,
                "                    }",
                "                    continue;",
                "                }",
                "                %s_enter_one(me, remembered);" % p,
                "                if (queue_tail < (uint8_t)(%s_REGION_COUNT + 1U)) {" % P,
                "                    queue_state[queue_tail] = remembered;",
                "                    queue_tail++;",
                "                }",
                "            }",
                "        }",
                "        return %s_leaf_of(me, stored);" % p,
                "    }",
                "    return %s_descend(me, stored);" % p,
                "}",
                "",
            ]
        # land: resolve history/terminate/descent on arrival at the target
        L += [
            "/**",
            " * @brief  Determines the real leaf state once a target is reached.",
            " * @return The settled leaf state; history and terminate are resolved here.",
            " */",
            "static uint8_t %s_land(%s *me, uint8_t target)" % (p, obj),
            "{",
            "    uint8_t leaf;",
            "    const uint8_t kind = %s_kind[target];" % p,
            "",
        ]
        cond = []
        if self.ir.has_history():
            cond += [
                "    if ((kind == %s_KIND_HISTORY_SHALLOW) || (kind == %s_KIND_HISTORY_DEEP)) {" % (P, P),
                "        leaf = %s_resolve_history(me, target);" % p,
                "    } else if (kind == %s_KIND_TERMINATE) {" % P,
            ]
        else:
            cond += [
                "    if (kind == %s_KIND_TERMINATE) {" % P,
            ]
        cond += [
            "        me->terminated = true;  /* UML terminate: the machine stops */",
            "        leaf = target;",
            "    } else {",
            "        leaf = %s_descend(me, target);" % p,
            "    }",
            "    return leaf;",
            "}",
            "",
        ]
        return L + cond

    def _runtime(self) -> List[str]:
        p, P = self.p, self.P
        obj = self.type_obj()
        return [
            "/* ------------------------------------------------------ hierarchy helpers -- */",
            "",
            "static uint8_t %s_depth(uint8_t state)" % p,
            "{",
            "    uint8_t depth = 0U;",
            "    uint8_t current = state;",
            "    while ((current != %s_STATE_NONE) && (depth <= %s_MAX_DEPTH)) {" % (P, P),
            "        current = %s_parent[current];" % p,
            "        depth++;",
            "    }",
            "    return depth;",
            "}",
            "",
            "/**",
            " * @brief  Lowest common ancestor of two states.",
            " * @param  a  First state.",
            " * @param  b  Second state.",
            " * @return The shared parent, or %s_STATE_NONE for the root region." % P,
            " */",
            "static uint8_t %s_least_common_ancestor(uint8_t a, uint8_t b)" % p,
            "{",
            "    uint8_t depth_a = %s_depth(a);" % p,
            "    uint8_t depth_b = %s_depth(b);" % p,
            "",
            "    while (depth_a > depth_b) {",
            "        a = %s_parent[a];" % p,
            "        depth_a--;",
            "    }",
            "    while (depth_b > depth_a) {",
            "        b = %s_parent[b];" % p,
            "        depth_b--;",
            "    }",
            "    while (a != b) {",
            "        if ((a == %s_STATE_NONE) || (b == %s_STATE_NONE)) {" % (P, P),
            "            return %s_STATE_NONE;" % P,
            "        }",
            "        a = %s_parent[a];" % p,
            "        b = %s_parent[b];" % p,
            "    }",
            "    return a;",
            "}",
            "",
            "/**",
            " * @brief  Is `maybe` a PROPER ancestor of `node`?",
            " */",
            "static bool %s_is_ancestor(uint8_t maybe, uint8_t node)" % p,
            "{",
            "    uint8_t current = %s_parent[node];" % p,
            "    uint8_t steps = 0U;",
            "",
            "    while ((current != %s_STATE_NONE) && (steps <= %s_MAX_DEPTH)) {" % (P, P),
            "        if (current == maybe) {",
            "            return true;",
            "        }",
            "        current = %s_parent[current];" % p,
            "        steps++;",
            "    }",
            "    return false;",
            "}",
            "",
            "/**",
            " * @brief Activates ONE state: runs its entry and records it in its region.",
            " */",
            "static void %s_enter_one(%s *me, uint8_t state)" % (p, obj),
            "{",
            "    const uint8_t region = %s_state_region[state];" % p,
            "",
            "    %s_execute_entry(me, state);" % p,
        ] + ([
            "    %s_timers_start(me, state);" % p,
        ] if self.ir.has_time_events() else []) + [
            "    if (region != %s_REGION_NONE) {" % P,
            "        me->active[region] = state;",
            "        /* A state was entered: this region's completion is due. */",
            "        me->completion_pending[region] = true;",
            "    }",
            "}",
            "",
            "/**",
            " * @brief Deactivates ONE state; its regions must already be empty.",
            " */",
            "static void %s_exit_one(%s *me, uint8_t state)" % (p, obj),
            "{",
            "    const uint8_t region = %s_state_region[state];" % p,
            "",
            "    %s_execute_exit(me, state);" % p,
        ] + ([
            "    %s_timers_cancel(me, state);" % p,
        ] if self.ir.has_time_events() else []) + ([
            "    /* Shallow-history record (UML 14.2.3.4.5): only real states are",
            "       stored; a region completed by a final state clears it. The",
            "       record lives HERE, not in the transition loop, so that a state",
            "       closed while an orthogonal parent is torn down is remembered",
            "       too. */",
            "    if (region != %s_REGION_NONE) {" % P,
            "        if ((%s_kind[state] == %s_KIND_SIMPLE) ||" % (p, P),
            "            (%s_kind[state] == %s_KIND_COMPOSITE)) {" % (p, P),
            "            me->history[region] = state;",
            "        } else if (%s_kind[state] == %s_KIND_FINAL) {" % (p, P),
            "            me->history[region] = (uint8_t)%s_STATE_NONE;" % P,
            "        } else {",
            "            /* leaving a pseudostate does not change the record */",
            "        }",
            "    }",
        ] if self.ir.has_history() else []) + [
            "    if ((region != %s_REGION_NONE) && (me->active[region] == state)) {" % P,
            "        me->active[region] = (uint8_t)%s_STATE_NONE;" % P,
            "        me->completion_pending[region] = false;",
            "    }",
            "}",
            "",
            "/**",
            " * @brief Activates everything BELOW an already-entered state, by default.",
            " *",
            " * Regions are activated in ASCENDING index order. UML does not define",
            " * this order (14.2.3.8.3); the generator fixes it so that two builds of",
            " * the same model behave identically.",
            " *",
            " * An explicit stack is used instead of recursion: on an embedded target",
            " * the dispatch stack must be bounded by the generated tables, never by",
            " * the shape of the model.",
            " */",
            "static void %s_activate_below(%s *me, uint8_t state)" % (p, obj),
            "{",
            "    uint8_t stack_state[%s_MAX_DEPTH];" % P,
            "    uint8_t stack_next[%s_MAX_DEPTH];" % P,
            "    uint8_t top = 1U;",
            "    uint16_t steps = 0U;",
            "",
            "    stack_state[0] = state;",
            "    stack_next[0] = 0U;",
            "    while ((top > 0U) && (steps < %s_MAX_WALK_STEPS)) {" % P,
            "        const uint8_t current = stack_state[top - 1U];",
            "        const uint8_t next = stack_next[top - 1U];",
            "        steps++;",
            "        if (next >= %s_state_region_count[current]) {" % p,
            "            top--;",
            "            continue;",
            "        }",
            "        stack_next[top - 1U] = (uint8_t)(next + 1U);",
            "        {",
            "            const uint8_t region ="
            " (uint8_t)(%s_state_first_region[current] + next);" % p,
            "            const uint8_t target = %s_region_initial[region];" % p,
            "            if (target != %s_STATE_NONE) {" % P,
            "                %s_execute_action(me, %s_region_initial_action[region]);" % (p, p),
            "                %s_enter_one(me, target);" % p,
            "                if (top < (uint8_t)%s_MAX_DEPTH) {" % P,
            "                    stack_state[top] = target;",
            "                    stack_next[top] = 0U;",
            "                    top++;",
            "                }",
            "            }",
            "        }",
            "    }",
            "}",
            "",
            "/**",
            " * @brief  Deepest active state below `state` (regions in DESCENDING order).",
            " * @return %s_STATE_NONE when nothing below is active." % P,
            " */",
            "static uint8_t %s_deepest_active_below(const %s *me, uint8_t state)" % (p, obj),
            "{",
            "    uint8_t found = (uint8_t)%s_STATE_NONE;" % P,
            "    uint8_t node = state;",
            "    uint16_t steps = 0U;",
            "",
            "    while (steps < %s_MAX_WALK_STEPS) {" % P,
            "        uint8_t next = (uint8_t)%s_STATE_NONE;" % P,
            "        uint8_t region_index = %s_state_region_count[node];" % p,
            "        steps++;",
            "        while (region_index > 0U) {",
            "            uint8_t candidate;",
            "            region_index--;",
            "            candidate ="
            " me->active[%s_state_first_region[node] + region_index];" % p,
            "            if (candidate != (uint8_t)%s_STATE_NONE) {" % P,
            "                next = candidate;",
            "                break;",
            "            }",
            "        }",
            "        if (next == (uint8_t)%s_STATE_NONE) {" % P,
            "            return found;",
            "        }",
            "        found = next;",
            "        node = next;",
            "    }",
            "    return found;",
            "}",
            "",
            "/**",
            " * @brief Closes everything below `state`; `state` itself stays active.",
            " */",
            "static void %s_exit_below(%s *me, uint8_t state)" % (p, obj),
            "{",
            "    uint16_t steps = 0U;",
            "",
            "    while (steps < %s_MAX_WALK_STEPS) {" % P,
            "        const uint8_t target = %s_deepest_active_below(me, state);" % p,
            "        steps++;",
            "        if (target == (uint8_t)%s_STATE_NONE) {" % P,
            "            return;",
            "        }",
            "        %s_exit_one(me, target);" % p,
            "    }",
            "}",
            "",
        ] + ([
            "/**",
            " * @brief  Is EVERY incoming segment of a join active right now?",
            " *",
            " * UML 2.5.1, 14.2.3.7: all incoming Transitions have to complete",
            " * before execution can continue through an outgoing Transition.",
            " */",
            "static bool %s_join_ready(const %s *me,"
            " const %s_transition_t *transition)" % (p, obj, p),
            "{",
            "    uint8_t i;",
            "",
            "    for (i = 0U; i < transition->join_count; i++) {",
            "        const uint8_t source = %s_extra[transition->extra_first"
            " + transition->fork_count + i];" % p,
            "        const uint8_t region = %s_state_region[source];" % p,
            "        if ((region == %s_REGION_NONE) ||" % P,
            "            (me->active[region] != source)) {",
            "            return false;",
            "        }",
            "    }",
            "    return true;",
            "}",
            "",
            "/**",
            " * @brief Enters the regions a fork names; the rest start by default.",
            " *",
            " * UML 2.5.1, 14.2.3.7: a fork splits an incoming Transition into",
            " * Transitions terminating on Vertices in orthogonal Regions.",
            " * Entering an orthogonal state still starts ALL of its regions, so",
            " * a region the fork does not name is activated by default.",
            " */",
            "static void %s_enter_forked(%s *me,"
            " const %s_transition_t *transition)" % (p, obj, p),
            "{",
            "    const uint8_t owner = transition->target;",
            "    uint8_t covered[%s_REGION_COUNT];" % P,
            "    uint8_t covered_count = 0U;",
            "    uint8_t i;",
            "    uint8_t region_index;",
            "",
            "    for (i = 0U; i < transition->fork_count; i++) {",
            "        const uint8_t leaf = %s_extra[transition->extra_first + i];" % p,
            "        uint8_t below = leaf;",
            "        uint16_t walk = 0U;",
            "        while ((%s_parent[below] != owner) &&" % p,
            "               (%s_parent[below] != %s_STATE_NONE) &&" % (p, P),
            "               (walk < %s_MAX_WALK_STEPS)) {" % P,
            "            below = %s_parent[below];" % p,
            "            walk++;",
            "        }",
            "        if (covered_count < (uint8_t)%s_REGION_COUNT) {" % P,
            "            covered[covered_count] = %s_state_region[below];" % p,
            "            covered_count++;",
            "        }",
            "        %s_enter_path(me, leaf, owner);" % p,
            "        %s_activate_below(me, leaf);" % p,
            "    }",
            "",
            "    for (region_index = 0U;",
            "         region_index < %s_state_region_count[owner];" % p,
            "         region_index++) {",
            "        const uint8_t region = (uint8_t)"
            "(%s_state_first_region[owner] + region_index);" % p,
            "        bool taken = false;",
            "        uint8_t fallback;",
            "        for (i = 0U; i < covered_count; i++) {",
            "            if (covered[i] == region) {",
            "                taken = true;",
            "                break;",
            "            }",
            "        }",
            "        if (taken) {",
            "            continue;",
            "        }",
            "        fallback = %s_region_initial[region];" % p,
            "        if (fallback == (uint8_t)%s_STATE_NONE) {" % P,
            "            continue;",
            "        }",
            "        %s_execute_action(me, %s_region_initial_action[region]);" % (p, p),
            "        %s_enter_one(me, fallback);" % p,
            "        %s_activate_below(me, fallback);" % p,
            "    }",
            "}",
            "",
        ] if self.ir.has_fork_join() else []) + [
            "/**",
            " * @brief Runs the entry chain from 'top' (exclusive) down to 'target'.",
            " *",
            " * The path is collected on a bounded stack buffer and replayed outside-in,",
            " * so entry behaviours execute in UML order without recursion.",
            " *",
            " * An ORTHOGONAL state on the path starts every region the path does not",
            " * itself pass through (UML 2.5.1, 14.2.3.2): entering such a state starts",
            " * ALL of its regions. The target's own regions are opened by",
            " * %s_descend(), not here." % p,
            " */",
            "static void %s_enter_path(%s *me, uint8_t target, uint8_t top)" % (p, obj),
            "{",
            "    uint8_t path[%s_MAX_DEPTH];" % P,
            "    uint8_t count = 0U;",
            "    uint8_t state = target;",
            "    uint8_t i;",
            "",
            "    while ((state != top) && (state != %s_STATE_NONE) && (count < %s_MAX_DEPTH)) {" % (P, P),
            "        path[count] = state;",
            "        count++;",
            "        state = %s_parent[state];" % p,
            "    }",
            "    for (i = count; i > 0U; i--) {",
            "        const uint8_t current = path[i - 1U];",
            "        %s_enter_one(me, current);" % p,
            "        if (i > 1U) {",
            "            const uint8_t passed = %s_state_region[path[i - 2U]];" % p,
            "            const uint8_t owned = %s_state_region_count[current];" % p,
            "            uint8_t region_index;",
            "            for (region_index = 0U; region_index < owned; region_index++) {",
            "                const uint8_t region ="
            " (uint8_t)(%s_state_first_region[current] + region_index);" % p,
            "                uint8_t fallback;",
            "                if (region == passed) {",
            "                    continue;",
            "                }",
            "                fallback = %s_region_initial[region];" % p,
            "                if (fallback == (uint8_t)%s_STATE_NONE) {" % P,
            "                    continue;",
            "                }",
            "                %s_execute_action(me, %s_region_initial_action[region]);" % (p, p),
            "                %s_enter_one(me, fallback);" % p,
            "                %s_activate_below(me, fallback);" % p,
            "            }",
            "        }",
            "    }",
            "}",
            "",
            "/**",
            " * @brief  Representative leaf for reporting: always follows the FIRST region.",
            " */",
            "static uint8_t %s_leaf_of(const %s *me, uint8_t state)" % (p, obj),
            "{",
            "    uint8_t current = state;",
            "    uint16_t steps = 0U;",
            "",
            "    while ((current != (uint8_t)%s_STATE_NONE) && (steps < %s_MAX_WALK_STEPS)) {" % (P, P),
            "        uint8_t next;",
            "        steps++;",
            "        if (%s_state_region_count[current] == 0U) {" % p,
            "            return current;",
            "        }",
            "        next = me->active[%s_state_first_region[current]];" % p,
            "        if ((next == (uint8_t)%s_STATE_NONE) || (next == current)) {" % P,
            "            return current;",
            "        }",
            "        current = next;",
            "    }",
            "    return current;",
            "}",
            "",
            "/**",
            " * @brief  Opens the default substates of a state that was just entered.",
            " * @return The representative leaf the machine settles in.",
            " */",
            "static uint8_t %s_descend(%s *me, uint8_t state)" % (p, obj),
            "{",
            "    %s_activate_below(me, state);" % p,
            "    return %s_leaf_of(me, state);" % p,
            "}",
            "",
            "/**",
            " * @brief  Has the completion event of a composite state been generated?",
            " *",
            " * UML 2.5.1, 14.2.3.8.3: ALL orthogonal regions must have reached a",
            " * final state. One region is not enough.",
            " */",
            "static bool %s_completed(const %s *me, uint8_t state)" % (p, obj),
            "{",
            "    const uint8_t owned = %s_state_region_count[state];" % p,
            "    uint8_t region_index;",
            "",
            "    if (owned == 0U) {",
            "        return true;",
            "    }",
            "    for (region_index = 0U; region_index < owned; region_index++) {",
            "        const uint8_t leaf ="
            " me->active[%s_state_first_region[state] + region_index];" % p,
            "        if ((leaf == (uint8_t)%s_STATE_NONE) ||" % P,
            "            (%s_kind[leaf] != %s_KIND_FINAL)) {" % (p, P),
            "            return false;",
            "        }",
            "    }",
            "    return true;",
            "}",
            "",
        ] + self._history_helpers() + [
            "/**",
            " * @brief Executes one transition.",
            " *",
            " * Order follows UML run-to-completion: exit chain, effect, entry chain,",
            " * then descent into the default substates.",
            " *",
            " * @param  me          Instance.",
            " * @param  transition  The transition row being taken.",
            " */",
            "static void %s_take(%s *me, const %s_transition_t *transition)"
            % (p, obj, p),
            "{",
            "    uint8_t top;",
            "    uint8_t state;",
            "    uint8_t region;",
            "",
            "    if (transition->kind == %s_TRANSITION_INTERNAL) {" % P,
            "        /* An internal transition does not change the state, so it",
            "           generates no new completion event (UML 2.5.1, 14.2.3.8.3).",
            "           Only ITS OWN region is cleared: a machine-wide flag would",
            "           also destroy a completion another region is waiting for. */",
            "        if (%s_state_region[transition->source] != %s_REGION_NONE) {" % (p, P),
            "            me->completion_pending"
            "[%s_state_region[transition->source]] = false;" % p,
            "        }",
            "        %s_execute_action(me, transition->action);  /* the state does not change */" % p,
            "        return;",
            "    }",
            "",
            "    /* UML 2.5.1, 14.2.3.7: on entering a terminate pseudostate the",
            "       machine exits no state; exit behaviours are not executed. */",
            "    if (%s_kind[transition->target] == %s_KIND_TERMINATE) {" % (p, P),
            "        %s_execute_action(me, transition->action);" % p,
            "        me->terminated = true;",
            "        %s_clear_completion(me);" % p,
            "        me->state = (%s)transition->target;" % self.type_state(),
            "        return;",
            "    }",
            "",
            "    top = %s_least_common_ancestor(transition->source, transition->target);" % p,
            "    if (transition->kind == %s_TRANSITION_EXTERNAL) {" % P,
            "        /* An external transition also leaves its source, so the",
            "           common ancestor is lifted one level. */",
            "        if ((top == transition->source) || (top == transition->target)) {",
            "            if (top != %s_STATE_NONE) {" % P,
            "                top = %s_parent[top];" % p,
            "            }",
            "        }",
            "    }",
            "",
            "    /* The exit starts in the region BELOW `top` that the transition",
            "       actually affects. Starting from the source's own region would be",
            "       wrong for a LOCAL transition, whose source IS `top`: the loop",
            "       would never run and the current substate would stay active. The",
            "       target's path names the affected region. */",
            "    {",
            "        uint8_t below = transition->target;",
            "        uint16_t walk = 0U;",
            "        while ((%s_parent[below] != top) &&" % p,
            "               (%s_parent[below] != %s_STATE_NONE) &&" % (p, P),
            "               (walk < %s_MAX_WALK_STEPS)) {" % P,
            "            below = %s_parent[below];" % p,
            "            walk++;",
            "        }",
            "        region = %s_state_region[below];" % p,
            "    }",
            "    if (region != %s_REGION_NONE) {" % P,
            "        state = me->active[region];",
            "    } else {",
            "        state = (uint8_t)%s_STATE_NONE;" % P,
            "    }",
            "    while ((state != top) && (state != %s_STATE_NONE)) {" % P,
            "        %s_exit_below(me, state);" % p,
            "        %s_exit_one(me, state);" % p,
            "        state = %s_parent[state];" % p,
            "    }",
            "",
            "    %s_execute_action(me, transition->action);" % p,
            "",
            "    %s_enter_path(me, transition->target, top);" % p,
        ] + ([
            "    if (transition->fork_count > 0U) {",
            "        %s_enter_forked(me, transition);" % p,
            "        me->state = (%s)%s_leaf_of(me, transition->target);"
            % (self.type_state(), p),
            "    } else {",
            "        me->state = (%s)%s_land(me, transition->target);"
            % (self.type_state(), p),
            "    }",
        ] if self.ir.has_fork_join() else [
            "    me->state = (%s)%s_land(me, transition->target);"
            % (self.type_state(), p),
        ]) + [
            "    /* The per-region flags were set inside enter_one(). */",
            "    if (me->terminated) {",
            "        %s_clear_completion(me);" % p,
            "    }",
            "}",
            "",
            "/**",
            " * @brief  Finds and executes the first enabled transition.",
            " *",
            " * The search starts at the active leaf and walks up to the root, so the",
            " * innermost state wins -- this is the UML transition priority rule.",
            " *",
            " * @return true when a transition was taken.",
            " */",
            "static bool %s_select(%s *me, uint8_t region, uint8_t event," % (p, obj),
            "                      uint8_t *out_source, uint16_t *out_transition)",
            "{",
            "    uint8_t state = me->active[region];",
            "    uint16_t steps = 0U;",
            "",
            "    while ((state != (uint8_t)%s_STATE_NONE) && (steps < %s_MAX_WALK_STEPS)) {" % (P, P),
            "        const uint16_t first = %s_transition_first[state];" % p,
            "        const uint16_t count = %s_transition_count[state];" % p,
            "        uint16_t i;",
            "        steps++;",
            "",
            "        for (i = 0U; i < count; i++) {",
            "            const %s_transition_t *transition ="
            " &%s_transitions[first + i];" % (p, p),
            "",
            "            if (transition->event != event) {",
            "                continue;",
            "            }",
            "            /* A composite state completes only when ALL of its regions",
            "               have reached a final state (UML 2.5.1, 14.2.3.8.3). */",
            "            if ((event == (uint8_t)%s_EVENT_COMPLETION) &&" % P,
            "                (%s_kind[state] == %s_KIND_COMPOSITE) &&" % (p, P),
        ] + ([
            "                (transition->join_count == 0U) &&",
        ] if self.ir.has_fork_join() else []) + [
            "                (!%s_completed(me, state))) {" % p,
            "                continue;",
            "            }",
        ] + ([
            "            /* A join fires only when EVERY incoming segment is",
            "               active at the same time (UML 2.5.1, 14.2.3.7). */",
            "            if ((transition->join_count > 0U) &&",
            "                (!%s_join_ready(me, transition))) {" % p,
            "                continue;",
            "            }",
        ] if self.ir.has_fork_join() else []) + [
            "            if (!%s_evaluate_guard(me, transition->guard)) {" % p,
            "                continue;",
            "            }",
            "            *out_source = state;",
            "            *out_transition = (uint16_t)(first + i);",
            "            return true;",
            "        }",
            "        state = %s_parent[state];" % p,
            "    }",
            "    return false;",
            "}",
            "",
            "/**",
            " * @brief  Offers the event to EVERY active region and resolves conflicts.",
            " *",
            " * UML 2.5.1, 14.2.3.9.4: a transition leaving a deeper state conflicts",
            " * with one leaving a state that contains it, and the deeper one wins.",
            " * Two orthogonal regions may also select the SAME outer transition; it",
            " * must then be taken once, not twice.",
            " *",
            " * Regions are visited in ASCENDING index order, which UML leaves",
            " * undefined (14.2.3.8.3) and the generator therefore fixes.",
            " *",
            " * @return true when at least one transition fired.",
            " */",
            "static bool %s_try_event(%s *me, uint8_t event)" % (p, obj),
            "{",
            "    uint8_t chosen_region[%s_REGION_COUNT];" % P,
            "    uint8_t chosen_source[%s_REGION_COUNT];" % P,
            "    uint16_t chosen_transition[%s_REGION_COUNT];" % P,
            "    uint8_t count = 0U;",
            "    uint8_t r;",
            "    uint8_t i;",
            "    uint8_t j;",
            "    bool handled = false;",
            "",
            "    for (r = 0U; r < (uint8_t)%s_REGION_COUNT; r++) {" % P,
            "        uint8_t source = (uint8_t)%s_STATE_NONE;" % P,
            "        uint16_t transition = 0U;",
            "        bool duplicate = false;",
            "",
            "        if (me->active[r] == (uint8_t)%s_STATE_NONE) {" % P,
            "            continue;",
            "        }",
            "        if (!%s_select(me, r, event, &source, &transition)) {" % p,
            "            continue;",
            "        }",
            "        for (i = 0U; i < count; i++) {",
            "            if (chosen_transition[i] == transition) {",
            "                duplicate = true;",
            "                break;",
            "            }",
            "        }",
            "        if (duplicate) {",
            "            continue;",
            "        }",
            "        chosen_region[count] = r;",
            "        chosen_source[count] = source;",
            "        chosen_transition[count] = transition;",
            "        count++;",
            "    }",
            "",
            "    /* Drop a selection whose source CONTAINS another selection's source:",
            "       priority belongs to the deeper state. */",
            "    for (i = 0U; i < count; i++) {",
            "        for (j = 0U; j < count; j++) {",
            "            if (i == j) {",
            "                continue;",
            "            }",
            "            if (%s_is_ancestor(chosen_source[i], chosen_source[j])) {" % p,
            "                chosen_region[i] = (uint8_t)%s_REGION_NONE;" % P,
            "                break;",
            "            }",
            "        }",
            "    }",
            "",
            "    for (i = 0U; i < count; i++) {",
            "        const %s_transition_t *transition;" % p,
            "        /* TERMINATE STOPS EVERYTHING (UML 2.5.1, 14.2.3.7). Once one",
            "           region has killed the machine, running the transitions the",
            "           other regions selected would execute exit, effect and entry",
            "           behaviour on a machine that has already ceased. */",
            "        if (me->terminated) {",
            "            break;",
            "        }",
            "        if (chosen_region[i] == (uint8_t)%s_REGION_NONE) {" % P,
            "            continue;",
            "        }",
            "        transition = &%s_transitions[chosen_transition[i]];" % p,
            "        /* An earlier transition may have closed this region. */",
            "        if ((me->active[chosen_region[i]] == (uint8_t)%s_STATE_NONE) &&" % P,
            "            (transition->kind != %s_TRANSITION_INTERNAL)) {" % P,
            "            continue;",
            "        }",
            "        %s_take(me, transition);" % p,
            "        handled = true;",
            "    }",
            "    return handled;",
            "}",
            "",
        ] + ([
            "/**",
            " * @brief  Is this event type deferred by the active configuration?",
            " *",
            " * UML 2.5.1, 14.2.3.4.4: an Event may be deferred by a composite",
            " * state too, in which case it stays deferred as long as that state",
            " * is in the active configuration -- so every active region is",
            " * checked, not just the leaf.",
            " */",
            "static bool %s_is_deferred(const %s *me, uint8_t event)" % (p, obj),
            "{",
            "    uint8_t r;",
            "",
            "    for (r = 0U; r < (uint8_t)%s_REGION_COUNT; r++) {" % P,
            "        uint8_t state = me->active[r];",
            "        uint16_t steps = 0U;",
            "        while ((state != (uint8_t)%s_STATE_NONE) &&" % P,
            "               (steps < %s_MAX_WALK_STEPS)) {" % P,
            "            steps++;",
            "            if ((%s_defer_mask[state] &" % p,
            "                 ((uint32_t)1U << event)) != 0U) {",
            "                return true;",
            "            }",
            "            state = %s_parent[state];" % p,
            "        }",
            "    }",
            "    return false;",
            "}",
            "",
            "/**",
            " * @brief Retains one occurrence in the deferred pool.",
            " *",
            " * A full pool is REPORTED, never silently dropped: a lost event",
            " * would make the machine look broken for no visible reason.",
            " */",
            "static void %s_defer(%s *me, uint8_t event)" % (p, obj),
            "{",
            "    if (me->deferred_count >= (uint8_t)%s_DEFER_POOL_SIZE) {" % P,
            "        me->defer_overflow = true;",
            "        return;",
            "    }",
            "    me->deferred[me->deferred_count] = event;",
            "    me->deferred_count++;",
            "}",
            "",
            "/**",
            " * @brief Replays the retained occurrences that are no longer deferred.",
            " */",
            "static void %s_drain_deferred(%s *me)" % (p, obj),
            "{",
            "    uint8_t rounds = 0U;",
            "",
            "    while (rounds < (uint8_t)(2U * %s_DEFER_POOL_SIZE)) {" % P,
            "        uint8_t index = 0U;",
            "        uint8_t found = (uint8_t)%s_EVENT_COUNT;" % P,
            "        uint8_t i;",
            "",
            "        /* A TERMINATED MACHINE PROCESSES NO EVENTS. Draining the",
            "           pool after one region has entered a terminate",
            "           pseudostate would run exit, effect and entry behaviour",
            "           on a machine that has already ceased executing",
            "           (UML 2.5.1, 14.2.3.7). */",
            "        if (me->terminated) {",
            "            return;",
            "        }",
            "        rounds++;",
            "        for (i = 0U; i < me->deferred_count; i++) {",
            "            if (!%s_is_deferred(me, me->deferred[i])) {" % p,
            "                index = i;",
            "                found = me->deferred[i];",
            "                break;",
            "            }",
            "        }",
            "        if (found == (uint8_t)%s_EVENT_COUNT) {" % P,
            "            return;",
            "        }",
            "        for (i = (uint8_t)(index + 1U); i < me->deferred_count; i++) {",
            "            me->deferred[i - 1U] = me->deferred[i];",
            "        }",
            "        me->deferred_count--;",
            "        if (%s_try_event(me, found)) {" % p,
            "            %s_run_to_completion(me);" % p,
            "        }",
            "    }",
            "}",
            "",
        ] if self.ir.has_deferred() else []) + [
            "/**",
            " * @brief Clears every pending completion flag.",
            " */",
            "static void %s_clear_completion(%s *me)" % (p, obj),
            "{",
            "    uint8_t r;",
            "",
            "    for (r = 0U; r < (uint8_t)%s_REGION_COUNT; r++) {" % P,
            "        me->completion_pending[r] = false;",
            "    }",
            "}",
            "",
            "/**",
            " * @brief Resolves completion (event-less) transitions until stable.",
            " *",
            " * A completion event is generated when a state is ENTERED (UML 2.5.1,",
            " * 14.2.3.8.3) and is consumed once, whether or not a transition was",
            " * enabled by it. Looping unconditionally would re-fire an event that",
            " * was already consumed: an unrelated internal transition could then",
            " * move the machine to a state the diagram does not show.",
            " *",
            " * Bounded by %s_MAX_RUN_TO_COMPLETION_STEPS (16 per region) so a"
            % P,
            " * cyclic model cannot hang the caller.",
            " */",
            "static void %s_run_to_completion(%s *me)" % (p, obj),
            "{",
            "    /* uint16_t: the bound is 16 per region, so up to 16 * 254. */",
            "    uint16_t steps = 0U;",
            "    while ((steps < (uint16_t)%s_MAX_RUN_TO_COMPLETION_STEPS)" % P,
            "           && (!me->terminated)) {",
            "        uint8_t region = (uint8_t)%s_REGION_NONE;" % P,
            "        uint8_t r;",
            "        for (r = 0U; r < (uint8_t)%s_REGION_COUNT; r++) {" % P,
            "            if (me->completion_pending[r]) {",
            "                region = r;",
            "                break;",
            "            }",
            "        }",
            "        if (region == (uint8_t)%s_REGION_NONE) {" % P,
            "            return;",
            "        }",
            "        me->completion_pending[region] = false;",
            "        if (me->active[region] != (uint8_t)%s_STATE_NONE) {" % P,
            "            uint8_t source = (uint8_t)%s_STATE_NONE;" % P,
            "            uint16_t found = 0U;",
            "            if (%s_select(me, region,"
            " (uint8_t)%s_EVENT_COMPLETION, &source, &found)) {" % (p, P),
            "                %s_take(me, &%s_transitions[found]);" % (p, p),
            "            }",
            "        }",
            "        steps++;",
            "    }",
            "}",
            "",
        ]

    # ------------------------------------------------------------ public API #

    def _public_api(self) -> List[str]:
        p, P = self.p, self.P
        obj = self.type_obj()
        st_t = self.type_state()
        ev_t = self.type_event()
        defer_reset = ([
            "    me->deferred_count = 0U;",
            "    me->defer_overflow = false;",
        ] if self.ir.has_deferred() else [])
        active_reset = [
            "    {",
            "        uint8_t i;",
            "        for (i = 0U; i < (uint8_t)%s_REGION_COUNT; i++) {" % P,
            "            me->active[i] = (uint8_t)%s_STATE_NONE;" % P,
            "        }",
            "    }",
        ]
        hist_reset = ([
            "    {",
            "        uint8_t i;",
            "        for (i = 0U; i < (uint8_t)%s_REGION_COUNT; i++) {" % P,
            "            me->history[i] = (uint8_t)%s_STATE_NONE;" % P,
            "        }",
            "    }",
        ] if self.ir.has_history() else [])
        return [
            "/* --------------------------------------------------------- public functions -- */",
            "",
            "void %s_construct(%s *me, %s *ctx)" % (p, obj, self.ctx_type()),
            "{",
            "    if (me == NULL) {",
            "        return;",
            "    }",
            "    me->state      = %s_STATE_NONE;" % P,
            "    me->ctx        = ctx;",
            "    me->started    = false;",
            "    me->terminated = false;",
            "    %s_clear_completion(me);" % p,
        ] + defer_reset + active_reset + hist_reset + [
            "}",
            "",
            "void %s_start(%s *me)" % (p, obj),
            "{",
            "    if ((me == NULL) || me->started) {",
            "        return;",
            "    }",
            "    me->started    = true;",
            "    me->terminated = false;",
            "    %s_clear_completion(me);" % p,
            "    me->state      = %s_STATE_NONE;" % P,
        ] + defer_reset + active_reset + hist_reset + [
            "",
            "    /* Every ROOT region is started, in ascending order. */",
            "    {",
            "        uint8_t r;",
            "        for (r = 0U; r < (uint8_t)%s_REGION_COUNT; r++) {" % P,
            "            uint8_t leaf;",
            "            if (%s_region_owner[r] != (uint8_t)%s_STATE_NONE) {" % (p, P),
            "                continue;  /* not a root region */",
            "            }",
            "            if (%s_region_initial[r] == (uint8_t)%s_STATE_NONE) {" % (p, P),
            "                continue;",
            "            }",
            "            %s_execute_action(me, %s_region_initial_action[r]);" % (p, p),
            "            %s_enter_path(me, %s_region_initial[r], (uint8_t)%s_STATE_NONE);" % (p, p, P),
            "            leaf = %s_land(me, %s_region_initial[r]);" % (p, p),
            "            if (me->state == (%s)%s_STATE_NONE) {" % (st_t, P),
            "                me->state = (%s)leaf;" % st_t,
            "            }",
            "        }",
            "    }",
            "    if (me->terminated) {",
            "        %s_clear_completion(me);" % p,
            "    }",
            "    %s_run_to_completion(me);" % p,
        ] + ([
            "    %s_drain_deferred(me);" % p,
        ] if self.ir.has_deferred() else []) + [
            "}",
            "",
            "bool %s_dispatch(%s *me, %s event)" % (p, obj, ev_t),
            "{",
            "    bool handled;",
            "",
            "    if (me == NULL) {",
            "        return false;",
            "    }",
            "    if (!me->started) {",
            "        %s_start(me);" % p,
            "    }",
            "    if (me->terminated) {",
            "        return false;  /* no event is processed after termination */",
            "    }",
            "    if ((event == %s_EVENT_COMPLETION) || ((uint8_t)event >= %s_EVENT_COUNT)) {" % (P, P),
            "        return false;  /* the completion event is internal only */",
            "    }",
            "",
            "    /* The transition is tried FIRST. UML 2.5.1, 14.2.3.4.4 makes",
            "       this explicit: if a deferred event type is used in a trigger",
            "       of a transition leaving the deferring state, the transition",
            "       wins -- it is an override option. */",
            "    handled = %s_try_event(me, (uint8_t)event);" % p,
            "    if (handled) {",
            "        %s_run_to_completion(me);" % p,
        ] + ([
            "        %s_drain_deferred(me);" % p,
            "        return true;",
            "    }",
            "    if (%s_is_deferred(me, (uint8_t)event)) {" % p,
            "        %s_defer(me, (uint8_t)event);" % p,
            "        return true;  /* consumed from the pool, retained inside */",
        ] if self.ir.has_deferred() else []) + [
            "    }",
            "    return handled;",
            "}",
            "",
            "void %s_do(%s *me)" % (p, obj),
            "{",
            "    uint8_t r;",
            "",
            "    if ((me == NULL) || !me->started || me->terminated) {",
            "        return;  /* a terminated machine runs no behaviour */",
            "    }",
            "    /* Every active region is served, innermost first. */",
            "    for (r = (uint8_t)%s_REGION_COUNT; r > 0U; r--) {" % P,
            "        const uint8_t state = me->active[r - 1U];",
            "        if (state != (uint8_t)%s_STATE_NONE) {" % P,
            "            %s_execute_do_activity(me, state);" % p,
            "        }",
            "    }",
            "}",
            "",
            "bool %s_is_in(const %s *me, %s state)" % (p, obj, st_t),
            "{",
            "    uint8_t r;",
            "",
            "    if (me == NULL) {",
            "        return false;",
            "    }",
            "    /* The configuration is a VECTOR: a state is active when it is the",
            "       leaf of any region. Walking one chain would miss the states of",
            "       the other regions of an orthogonal state. */",
            "    for (r = 0U; r < (uint8_t)%s_REGION_COUNT; r++) {" % P,
            "        if (me->active[r] == (uint8_t)state) {",
            "            return true;",
            "        }",
            "    }",
            "    return false;",
            "}",
            "",
            "bool %s_is_terminated(const %s *me)" % (p, obj),
            "{",
            "    uint8_t state;",
            "",
            "    if (me == NULL) {",
            "        return false;",
            "    }",
            "    if (me->terminated) {",
            "        return true;",
            "    }",
            "    /* The machine is done when EVERY root region sits in a final state. */",
            "    {",
            "        uint8_t r;",
            "        bool any = false;",
            "        for (r = 0U; r < (uint8_t)%s_REGION_COUNT; r++) {" % P,
            "            if (%s_region_owner[r] != (uint8_t)%s_STATE_NONE) {" % (p, P),
            "                continue;",
            "            }",
            "            any = true;",
            "            state = me->active[r];",
            "            if ((state >= (uint8_t)%s_STATE_COUNT) ||" % P,
            "                (%s_kind[state] != %s_KIND_FINAL)) {" % (p, P),
            "                return false;",
            "            }",
            "        }",
            "        return any;",
            "    }",
            "}",
            "",
            "%s %s_state(const %s *me)" % (st_t, p, obj),
            "{",
            "    if (me == NULL) {",
            "        return %s_STATE_NONE;" % P,
            "    }",
            "    return me->state;",
            "}",
            "",
            "const char *%s_state_name(%s state)" % (p, st_t),
            "{",
            "    if ((uint8_t)state >= %s_STATE_COUNT) {" % P,
            "        return \"<none>\";",
            "    }",
            "    return %s_state_names[(uint8_t)state];" % p,
            "}",
            "",
            "const char *%s_event_name(%s event)" % (p, ev_t),
            "{",
            "    if ((uint8_t)event >= %s_EVENT_COUNT) {" % P,
            "        return \"<invalid>\";",
            "    }",
            "    return %s_event_names[(uint8_t)event];" % p,
            "}",
            "",
        ]


    # ----------------------------------------------------- MCU integration #

    def demo_source(self) -> str:
        """An example that uses the generated machine in an MCU super loop.

        What the user asked for is not a unit-test run but a template showing
        how the code is driven on the real target: set-up, then an endless loop
        feeding events.
        """
        ir = self.ir
        p = self.p
        obj = self.type_obj()
        ctx = self.ctx_type()
        has_ctx = ir.has_context()
        events = [ev for ev in ir.events[1:]]

        L: List[str] = []
        L += banner(ir, "%s_main.c" % p, "bare-metal integration example")
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
            " *   (a) BOARD HOOKS -- board_init / board_event_pending /",
            " *       board_next_event / board_idle. They belong to THIS",
            " *       template, not to the state machine. They exist so the",
            " *       example can run without knowing your board: the loop",
            " *       asks 'is there an event?', takes it, and hands it to",
            " *       the machine. If you already have a scheduler or an",
            " *       interrupt that produces events, drop this file and",
            " *       call %s_dispatch() from there instead." % p,
            " *",
            " *   (b) BEHAVIOUR FUNCTIONS -- the calls you typed into the",
            " *       model's entry / exit / do / effect / guard fields.",
            " *       These are NOT optional: the machine calls them at the",
            " *       exact moments the diagram says. They are listed in",
            " *       %s.h under 'you must provide'." % p,
            " *",
            " * If any of them is missing the LINKER fails with",
            " * 'undefined reference'; nothing is detected at compile time.",
            " *",
            " * Build it together with the generated implementation:",
            " *",
            " *   cc -std=gnu11 -Wall -Wextra %s.c %s_main.c -o %s_app"
            % (p, p, p),
            " */",
            "",
            "/* NULL, <stddef.h>'den gelir: baglam tipi 'void' oldugunda",
            "   construct() cagrisi NULL alir ve baslik onu tanimlamaz. */",
            "#include <stddef.h>",
            '#include "%s.h"' % p,
            "",
            "/* ------------------------------------------ behaviour you provide -- */",
        ] + self._demo_required_note() + [
            "/* --------------------------------------------------- board interface -- */",
            "/*",
            " * Supply these from your board-support package. They are the ONLY",
            " * place where this example touches the hardware. They drive the",
            " * loop below; the state machine itself never calls them.",
            " */",
            "",
            "/** @brief Brings up clocks, GPIO and the event source. */",
            "extern void board_init(void);",
            "",
            "/** @brief Returns true while an event waits in the input queue. */",
            "extern bool board_event_pending(void);",
            "",
            "/**",
            " * @brief  Removes and returns the next event from the input queue.",
            " * @return One of the %s_event_t constants." % p,
        ]
        if events:
            L += [" *", " * Events understood by this machine:"]
            for name in events:
                L += [" *   %s" % self.event_enum(name)]
        L += [
            " */",
            "extern %s board_next_event(void);" % self.type_event(),
            "",
            "/** @brief Called once per pass; feed the watchdog here. */",
            "extern void board_idle(void);",
            "",
            "/* ---------------------------------------------------- application data -- */",
            "",
        ]
        if has_ctx:
            L += ["/** @brief Data shared by every action and guard of the machine. */",
                  "static %s g_ctx;" % ctx, ""]
        L += [
            "/** @brief The single state-machine instance of this application. */",
            "static %s g_machine;" % obj,
            "",
            "/**",
            " * @brief  Application entry point.",
            " * @return 0 once the machine has terminated.",
            " *",
            " * The super loop ends when the machine reaches a final state or a",
            " * terminate pseudostate. On a bare-metal target that normally never",
            " * happens, so the return is reached only on a deliberate shutdown.",
            " */",
            "int main(void)",
            "{",
            "    board_init();",
            "",
            "    /* Construct, then start. %s_start() runs the initial transition" % p,
            "       and the entry behaviours of the first state configuration. */",
            "    %s_construct(&g_machine, %s);" % (p, "&g_ctx" if has_ctx else "NULL"),
            "    %s_start(&g_machine);" % p,
            "",
            "    /* Super loop. One pass handles at most one event and then runs",
            "       the do-behaviours. Dispatching is run-to-completion: the call",
            "       returns only once the machine is stable again, so no event can",
            "       interrupt a transition halfway through. */",
            "    for (;;)",
            "    {",
            "        if (board_event_pending())",
            "        {",
            "            (void)%s_dispatch(&g_machine, board_next_event());" % p,
            "        }",
            "",
            "        %s_do(&g_machine);" % p,
            "",
            "        if (%s_is_terminated(&g_machine))" % p,
            "        {",
            "            /* A final state or a terminate pseudostate was reached;",
            "               the machine ignores every further event. */",
            "            break;",
            "        }",
            "",
            "        board_idle();",
            "    }",
            "",
            "    return 0;",
            "}",
            "",
        ]
        return "\n".join(allman(L))


# --------------------------------------------------------------------------- #

def generate_c(sm, with_demo: bool = True, resolve=None) -> Dict[str, str]:
    """Produces a {file_name: content} dictionary from the model.

    With ``with_demo`` on, the third generated file is not a UNIT TEST but an
    integration example driving the machine in an MCU super loop.
    """
    ir = build_ir(sm, resolve)
    gen = CGenerator(ir)
    files = {"%s.h" % ir.prefix: gen.header(),
             "%s.c" % ir.prefix: gen.source()}
    if with_demo:
        files["%s_main.c" % ir.prefix] = gen.demo_source()
    return files
