"""Example gallery: a SEPARATE, working model for every tool.

A single "demo" model showed every feature at once and so taught none of
them. Here each example focuses on ONE SUBJECT, is valid (it goes through
code generation), and states in its `teaches` field which tools it uses.
The menu shows that field as a tooltip.

Source: UML 2.5.1 (formal/2017-12-05) -- the relevant clause numbers are in
each example's `reference` field; Help > UML Specification inside the tool
jumps to the same numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List

from .class_model import (Attribute, ClassModel, Operation, Parameter,
                          Relation, RelationKind, Stereotype, UmlClass)
from .model import State, StateKind, StateMachine, Transition, TransitionKind

__all__ = ["Example", "STATE_EXAMPLES", "CLASS_EXAMPLES", "all_examples",
           "find"]


@dataclass
class Example:
    """One row in the menu; `build()` produces the model."""

    key: str
    title: str
    kind: str                     # "state" | "class"
    teaches: str                  # which tools/concepts
    summary: str                  # a one-sentence description
    reference: str                # UML 2.5.1 clause numbers
    build: Callable = field(repr=False, default=None)


# --------------------------------------------------------------------------- #
#   Small helpers
# --------------------------------------------------------------------------- #

def _machine(name, prefix, description, context="void", includes=""):
    return StateMachine(name=name, prefix=prefix, description=description,
                        context_type=context, user_includes=includes)


#: The pixel BUDGET per character.
#:
#: Measured: JetBrains Mono takes exactly 7.0 px at this size. But that font
#: DOES NOT SHIP with the application because `assets/fonts/` is empty; when
#: it is missing the canvas falls back down the chain (Cascadia Mono ~7.4,
#: Consolas ~7.4, Courier New ~7.2). The budget was chosen for the widest
#: fallback so the example boxes do not clip the text on those machines either.
_CH_BODY = 7.6
_CH_TITLE = 8.6
#: The line height of the behaviour strip (fm.height() + 1).
_LINE_H = 15.0


def _fit(nm, w, h, kind, kw):
    """GROWS the box to fit its content (it never shrinks it).

    When a behaviour line does not fit the box the canvas clips it and the
    example reads only half; so the width is computed from the longest line
    and the height from the number of lines.
    """
    if kind not in (StateKind.SIMPLE, StateKind.COMPOSITE,
                    StateKind.SUBMACHINE):
        return w, h
    rows = [prefix_text + kw[slot] for slot, prefix_text in
                (("entry", "entry / "), ("exit", "exit  / "),
                 ("do", "do    / ")) if kw.get(slot)]
    needed = [len(nm) * _CH_TITLE + 20.0]
    needed += [len(t) * _CH_BODY + 20.0 for t in rows]
    w = max(w, max(needed))
    if rows:
        h = max(h, 34.0 + len(rows) * _LINE_H + 12.0)
    return w, h


def _adder(sm):
    def st(sid, nm, kind, parent, x, y, w=170.0, h=84.0, **kw):
        w, h = _fit(nm, w, h, kind, kw)
        return sm.add_state(State(id=sid, name=nm, kind=kind, parent=parent,
                                  x=x, y=y, w=w, h=h, **kw))

    def tr(tid, src, dst, **kw):
        return sm.add_transition(Transition(id=tid, source=src, target=dst,
                                            **kw))
    return st, tr


def _class_helpers():
    def attr(name, type_, vis="-", default="", mult=""):
        return Attribute(name=name, type=type_, visibility=vis,
                         default=default, multiplicity=mult)

    def op(name, ret="void", vis="+", params=(), abstract=False, const=False,
           static=False, body=""):
        return Operation(name=name, return_type=ret, visibility=vis,
                         params=[Parameter(name=n, type=t) for n, t in params],
                         abstract=abstract, const=const, static=static,
                         body=body)
    return attr, op


# --------------------------------------------------------------------------- #
#   STATE MACHINE EXAMPLES
# --------------------------------------------------------------------------- #

def traffic_light() -> StateMachine:
    """A time event (after) plus a simple chain of states."""
    sm = _machine("TrafficLight", "traffic",
                  "Fixed-time traffic light: four states driven only by "
                  "time events.", "traffic_ctx_t",
                  '#include "traffic_api.h"')
    st, tr = _adder(sm)
    # The gaps follow the longest label: "after(RED_AMBER_MS)" is 19 characters,
    # about 143 px; a 200 px gap carries it comfortably.
    st("i", "Start", StateKind.INITIAL, None, 96, 20, 24, 24)
    st("red", "Red", StateKind.SIMPLE, None, 40, 90,
       entry="lamp_set(LAMP_RED);",
       note="Every transition out of a lamp state is a time event; no "
            "external signal is needed.")
    st("redamber", "RedAmber", StateKind.SIMPLE, None, 450, 90,
       entry="lamp_set(LAMP_RED | LAMP_AMBER);")
    st("green", "Green", StateKind.SIMPLE, None, 950, 90,
       entry="lamp_set(LAMP_GREEN);")
    st("amber", "Amber", StateKind.SIMPLE, None, 500, 330,
       entry="lamp_set(LAMP_AMBER);")
    tr("t0", "i", "red")
    tr("t1", "red", "redamber", event="after(RED_MS)")
    tr("t2", "redamber", "green", event="after(RED_AMBER_MS)")
    tr("t3", "green", "amber", event="after(GREEN_MS)")
    tr("t4", "amber", "red", event="after(AMBER_MS)")
    return sm


def composite_state() -> StateMachine:
    """A composite state plus a completion transition."""
    sm = _machine("Washer", "washer",
                  "Washing machine: a composite state holds the whole cycle "
                  "and completes into a final state.", "washer_ctx_t")
    st, tr = _adder(sm)
    st("i", "Start", StateKind.INITIAL, None, 96, 20, 24, 24)
    st("idle", "Idle", StateKind.SIMPLE, None, 40, 90,
       entry="door_lock(false);",
       note="START opens the cycle; the cycle completes on its own.")
    st("cyc", "Cycle", StateKind.COMPOSITE, None, 380, 40, 900, 340,
       entry="door_lock(true);", exit="door_lock(false);",
       note="A composite state. When its region reaches the final state a "
            "COMPLETION event is generated (14.2.3.8.3).")
    st("ci", "CycleStart", StateKind.INITIAL, "cyc", 40, 96, 24, 24)
    # Three steps in a row; the 150 px between them is enough for "after(FILL_MS)".
    st("fill", "Fill", StateKind.SIMPLE, "cyc", 26, 150, 174, 78,
       entry="valve_open();", exit="valve_close();")
    st("wash", "Wash", StateKind.SIMPLE, "cyc", 350, 150, 174, 78,
       entry="motor_run();")
    st("spin", "Spin", StateKind.SIMPLE, "cyc", 674, 150, 174, 78,
       entry="motor_spin();")
    st("cend", "CycleDone", StateKind.FINAL, "cyc", 750, 272, 28, 28)
    st("done", "Done", StateKind.SIMPLE, None, 40, 430,
       entry="buzzer_beep();")
    tr("t0", "i", "idle")
    tr("t1", "idle", "cyc", event="START")
    tr("t2", "ci", "fill")
    tr("t3", "fill", "wash", event="after(FILL_MS)")
    tr("t4", "wash", "spin", event="after(WASH_MS)")
    tr("t5", "spin", "cend", event="after(SPIN_MS)")
    # An event-less transition is a COMPLETION transition: taken when Cycle's
    tr("t6", "cyc", "done")
    tr("t7", "done", "idle", event="ACK")
    return sm


def orthogonal_regions() -> StateMachine:
    """Two regions running at the same time."""
    sm = _machine("Player", "player",
                  "Media player: playback and the display clock run at the "
                  "same time in two orthogonal regions.", "player_ctx_t")
    st, tr = _adder(sm)
    st("i", "Start", StateKind.INITIAL, None, 96, 20, 24, 24)
    st("on", "On", StateKind.COMPOSITE, None, 40, 90, 760, 420, regions=2,
       entry="panel_on();", exit="panel_off();",
       note="Two regions: the upper one plays, the lower one keeps the "
            "clock. Both are active at once (14.2.3.2).")
    # --- region 0: playback
    st("ai", "PlayStart", StateKind.INITIAL, "on", 40, 70, 24, 24, region=0)
    st("play", "Playing", StateKind.SIMPLE, "on", 26, 116, 170, 70, region=0,
       entry="audio_start();", exit="audio_stop();")
    st("pause", "Paused", StateKind.SIMPLE, "on", 330, 116, 170, 70, region=0,
       entry="audio_pause();")
    # --- region 1: clock
    st("bi", "ClockStart", StateKind.INITIAL, "on", 40, 270, 24, 24, region=1)
    st("show", "ShowTime", StateKind.SIMPLE, "on", 26, 316, 170, 70, region=1,
       entry="display_time();")
    st("dim", "Dimmed", StateKind.SIMPLE, "on", 330, 316, 170, 70, region=1,
       entry="display_dim();")
    tr("t0", "i", "on")
    tr("t1", "ai", "play")
    tr("t2", "play", "pause", event="PAUSE")
    tr("t3", "pause", "play", event="PLAY")
    tr("t4", "bi", "show")
    tr("t5", "show", "dim", event="after(DIM_MS)")
    tr("t6", "dim", "show", event="KEY")
    return sm


def choice_and_junction() -> StateMachine:
    """Dynamic (choice) and static (junction) branching side by side."""
    sm = _machine("Grader", "grader",
                  "Choice branches AFTER the effect runs; junction branches "
                  "BEFORE the source is left.", "grader_ctx_t")
    st, tr = _adder(sm)
    st("i", "Start", StateKind.INITIAL, None, 96, 20, 24, 24)
    st("idle", "Idle", StateKind.SIMPLE, None, 40, 200)
    # "MEASURE / value = read_sensor()" is 31 characters (~227 px); that much
    # clear space was left between Idle and the diamond.
    st("ch", "Choice", StateKind.CHOICE, None, 560, 96, 40, 40,
       note="DYNAMIC: the guards are evaluated after the incoming effect "
            "has run, so they see the fresh measurement (14.2.3.7).")
    st("hi", "High", StateKind.SIMPLE, None, 900, 40, 170, 70,
       entry="flag_high();")
    st("lo", "Low", StateKind.SIMPLE, None, 900, 170, 170, 70,
       entry="flag_low();")
    st("ju", "Junction", StateKind.JUNCTION, None, 560, 396, 34, 34,
       note="STATIC: the guards are evaluated before Idle is left, so they "
            "cannot see anything the transition effect produced.")
    st("save", "Save", StateKind.SIMPLE, None, 900, 340, 174, 70,
       entry="store_value();")
    st("drop", "Drop", StateKind.SIMPLE, None, 900, 470, 188, 70,
       entry="discard_value();")
    tr("t0", "i", "idle")
    tr("t1", "idle", "ch", event="MEASURE", action="value = read_sensor();")
    tr("t2", "ch", "hi", guard="value > threshold")
    tr("t3", "ch", "lo", guard="else")
    tr("t4", "idle", "ju", event="COMMIT")
    tr("t5", "ju", "save", guard="value > 0")
    tr("t6", "ju", "drop", guard="else")
    tr("t7", "hi", "idle", event="ACK", waypoints=[[700.0, -40.0]])
    tr("t8", "lo", "idle", event="ACK", waypoints=[[760.0, 280.0]])
    tr("t9", "save", "idle", event="ACK", waypoints=[[700.0, 330.0]])
    tr("t10", "drop", "idle", event="ACK", waypoints=[[700.0, 600.0]])
    return sm


def history_states() -> StateMachine:
    """Shallow (H) and deep (H*) history pseudostates."""
    sm = _machine("Menu", "menu",
                  "An interrupt returns the user to the exact screen they "
                  "were on: shallow history restores the page, deep history "
                  "restores the page AND its sub-page.", "menu_ctx_t")
    st, tr = _adder(sm)
    st("i", "Start", StateKind.INITIAL, None, 96, 20, 24, 24)
    st("ui", "Browsing", StateKind.COMPOSITE, None, 40, 90, 780, 420,
       note="H remembers which page was open; H* remembers the sub-page "
            "inside it as well (14.2.3.4.5).")
    st("bi", "UiStart", StateKind.INITIAL, "ui", 40, 62, 24, 24)
    # The name becomes a C identifier: "H*" is invalid and would fall on the
    # same constant as "H". The drawing still shows the H / H* symbol.
    # The two history vertices stay FAR APART: when the two transitions from
    # Alarm nearly overlapped, their labels collided as well.
    st("h", "Resume", StateKind.SHALLOW_HISTORY, "ui", 150, 58, 30, 30)
    st("hs", "ResumeDeep", StateKind.DEEP_HISTORY, "ui", 420, 58, 30, 30)
    st("p1", "Settings", StateKind.COMPOSITE, "ui", 26, 130, 390, 210,
       note="Has its own sub-pages; only H* restores which one.")
    st("p1i", "SetStart", StateKind.INITIAL, "p1", 30, 70, 22, 22)
    st("p1a", "Network", StateKind.SIMPLE, "p1", 20, 118, 120, 58)
    st("p1b", "Display", StateKind.SIMPLE, "p1", 230, 118, 120, 58)
    st("p2", "About", StateKind.SIMPLE, "ui", 470, 200, 180, 70)
    st("alarm", "Alarm", StateKind.SIMPLE, None, 1000, 210,
       entry="siren_on();", exit="siren_off();",
       note="The interrupt. ACK_SHALLOW comes back through H, "
            "ACK_DEEP through H*.")
    tr("t0", "i", "ui")
    tr("t1", "bi", "p1")
    tr("t2", "p1i", "p1a")
    # NO offset: the 90 px between the two boxes already carries the labels,
    # and the canvas places parallel transitions at SEPARATE points along the
    # arc. Offsetting by hand put NEXT on the top right corner of Network.
    # sag ust kosesine biniyordu.
    tr("t3", "p1a", "p1b", event="NEXT")
    tr("t4", "p1b", "p1a", event="PREV")
    tr("t5", "p1", "p2", event="ABOUT")
    tr("t6", "p2", "p1", event="BACK")
    tr("t7", "ui", "alarm", event="FAULT", waypoints=[[900.0, 420.0]])
    # The two return paths run at DIFFERENT heights, and so do their labels.
    tr("t8", "alarm", "h", event="ACK_SHALLOW",
       waypoints=[[880.0, 60.0]], label_dy=-16.0)
    tr("t9", "alarm", "hs", event="ACK_DEEP",
       waypoints=[[760.0, 140.0]], label_dy=18.0)
    return sm


def fork_and_join() -> StateMachine:
    """Spread across regions with a fork, merge them with a join."""
    sm = _machine("Startup", "startup",
                  "A fork starts two independent bring-up branches; the join "
                  "waits until BOTH are finished.", "startup_ctx_t")
    st, tr = _adder(sm)
    st("i", "Start", StateKind.INITIAL, None, 96, 20, 24, 24)
    st("boot", "PowerOn", StateKind.SIMPLE, None, 40, 150)
    st("fk", "Fork", StateKind.FORK, None, 320, 120, 14, 220,
       note="One incoming transition, one target per region "
            "(14.2.3.7 fork).")
    st("par", "BringUp", StateKind.COMPOSITE, None, 420, 80, 560, 320,
       regions=2)
    # Every region must be able to fall to a default when it is entered from
    # OUTSIDE the fork too (14.2.3.2); the tool requires that.
    st("pa0", "RadioStart", StateKind.INITIAL, "par", 30, 48, 22, 22,
       region=0)
    st("pa1", "StorageStart", StateKind.INITIAL, "par", 30, 178, 22, 22,
       region=1)
    st("a1", "Radio", StateKind.SIMPLE, "par", 26, 88, 174, 66, region=0,
       entry="radio_init();")
    st("a2", "RadioReady", StateKind.SIMPLE, "par", 330, 88, 180, 66,
       region=0)
    st("b1", "Storage", StateKind.SIMPLE, "par", 26, 218, 174, 66, region=1,
       entry="flash_mount();")
    st("b2", "StorageReady", StateKind.SIMPLE, "par", 330, 218, 190, 66,
       region=1)
    st("jn", "Join", StateKind.JOIN, None, 1050, 120, 14, 220,
       note="Fires only when EVERY incoming segment is active "
            "(14.2.3.7 join).")
    st("run", "Running", StateKind.SIMPLE, None, 1140, 190,
       entry="app_start();")
    tr("t0", "i", "boot")
    tr("t1", "boot", "fk", event="POWER")
    tr("t2", "fk", "a1")
    tr("t3", "fk", "b1")
    tr("t4", "a1", "a2", event="RADIO_OK")
    tr("t5", "b1", "b2", event="FLASH_OK")
    tr("t6", "a2", "jn")
    tr("t7", "b2", "jn")
    tr("t8", "jn", "run")
    tr("t9", "pa0", "a1")
    tr("t10", "pa1", "b1")
    # The return path goes UNDER the composite state; otherwise its label sat
    # on Radio's title strip.
    tr("t11", "run", "boot", event="SHUTDOWN",
       waypoints=[[1220.0, 470.0], [120.0, 470.0]])
    return sm


def deferred_events() -> StateMachine:
    """A deferred event: an event that cannot be handled now is kept."""
    sm = _machine("Printer", "printer",
                  "A PRINT request arriving during calibration is not lost: "
                  "the state defers it and it is delivered afterwards.",
                  "printer_ctx_t")
    st, tr = _adder(sm)
    st("i", "Start", StateKind.INITIAL, None, 96, 20, 24, 24)
    st("idle", "Idle", StateKind.SIMPLE, None, 40, 110)
    st("cal", "Calibrating", StateKind.SIMPLE, None, 360, 110, 220, 96,
       entry="calibrate_start();", deferred=["PRINT"],
       note="PRINT is in the deferrable trigger set: it is kept in the "
            "pool while this state is active (14.2.3.4.4).")
    st("prt", "Printing", StateKind.SIMPLE, None, 760, 110,
       entry="print_start();")
    tr("t0", "i", "idle")
    tr("t1", "idle", "cal", event="CALIBRATE")
    tr("t2", "cal", "idle", event="after(CAL_MS)", waypoints=[[300.0, 40.0]])
    # PRINT passes UNDER Calibrating: drawn straight, its label landed exactly
    # on that title strip.
    tr("t3", "idle", "prt", event="PRINT", waypoints=[[460.0, 330.0]])
    tr("t4", "prt", "idle", event="DONE", waypoints=[[460.0, 430.0]])
    return sm


def internal_transition() -> StateMachine:
    """An internal transition: the effect runs, the state IS NOT EXITED."""
    sm = _machine("Counter", "counter",
                  "An internal transition runs its effect without leaving "
                  "the state, so entry and exit behaviours do NOT re-run.",
                  "counter_ctx_t")
    st, tr = _adder(sm)
    st("i", "Start", StateKind.INITIAL, None, 96, 20, 24, 24)
    st("arm", "Armed", StateKind.SIMPLE, None, 120, 140, 260, 110,
       entry="led_on();", exit="led_off();",
       note="PULSE is INTERNAL: the LED stays on. RESTART is an external "
            "self-transition: exit and entry both run, so the LED blinks.")
    st("done", "Done", StateKind.SIMPLE, None, 660, 140)
    tr("t0", "i", "arm")
    # The two self-transitions sit on the same arc; their labels separate
    tr("t1", "arm", "arm", event="PULSE", kind=TransitionKind.INTERNAL,
       action="ctx->pulses++;", label_dx=-120.0)
    tr("t2", "arm", "arm", event="RESTART", action="ctx->pulses = 0U;",
       label_dx=130.0)
    tr("t3", "arm", "done", event="STOP")
    tr("t4", "done", "arm", event="ARM", waypoints=[[520.0, 330.0]])
    return sm


def terminate_and_final() -> StateMachine:
    """The difference between a final state and a terminate pseudostate."""
    sm = _machine("Session", "session",
                  "A final state completes the region; a terminate "
                  "pseudostate stops the whole machine WITHOUT running any "
                  "exit behaviour.", "session_ctx_t")
    st, tr = _adder(sm)
    st("i", "Start", StateKind.INITIAL, None, 96, 20, 24, 24)
    st("open", "Open", StateKind.SIMPLE, None, 40, 100, 210, 96,
       entry="session_open();", exit="session_close();",
       note="LOGOUT goes to the final state and session_close() runs. "
            "PANIC goes to terminate and it does NOT (14.2.3.7).")
    st("fin", "Closed", StateKind.FINAL, None, 420, 110, 30, 30)
    st("kill", "Halt", StateKind.TERMINATE, None, 420, 240, 32, 32)
    tr("t0", "i", "open")
    tr("t1", "open", "fin", event="LOGOUT")
    tr("t2", "open", "kill", event="PANIC", action="log_panic();")
    return sm


def entry_exit_points() -> StateMachine:
    """NAMED entry/exit points on a composite state."""
    sm = _machine("Pump", "pump",
                  "Named entry and exit points let a caller jump straight to "
                  "a chosen substate instead of the default one.",
                  "pump_ctx_t")
    st, tr = _adder(sm)
    st("i", "Start", StateKind.INITIAL, None, 96, 20, 24, 24)
    st("idle", "Idle", StateKind.SIMPLE, None, 40, 170)
    st("op", "Operating", StateKind.COMPOSITE, None, 420, 60, 600, 330,
       note="Two ways in: the default initial pseudostate, or the named "
            "entry point 'Priming' (14.2.3.7 entryPoint).")
    # The entry and exit points sit at DIFFERENT heights: both stay on the edge
    # of the parent state, but the incoming/outgoing labels must not overlap.
    st("ep", "Priming", StateKind.ENTRY_POINT, "op", -9, 240, 18, 18)
    st("xp", "Overheat", StateKind.EXIT_POINT, "op", 591, 96, 18, 18)
    st("oi", "OpStart", StateKind.INITIAL, "op", 60, 76, 22, 22)
    st("slow", "SlowRun", StateKind.SIMPLE, "op", 40, 130, 200, 66,
       entry="pump_speed(SLOW);")
    st("fast", "FastRun", StateKind.SIMPLE, "op", 360, 130, 200, 66,
       entry="pump_speed(FAST);")
    st("cool", "Cooling", StateKind.SIMPLE, None, 1120, 60,
       entry="fan_on();")
    tr("t0", "i", "idle")
    tr("t1", "idle", "op", event="RUN", waypoints=[[300.0, 90.0]])
    tr("t2", "idle", "ep", event="PRIME", waypoints=[[300.0, 320.0]])
    tr("t3", "ep", "fast")
    tr("t4", "oi", "slow")
    # The labels of the two-way transition separate horizontally along the arc.
    tr("t5", "slow", "fast", event="BOOST", label_dx=-34.0)
    tr("t6", "fast", "slow", event="EASE", label_dx=34.0)
    tr("t7", "fast", "xp", event="HOT")
    tr("t8", "xp", "cool")
    tr("t9", "cool", "idle", event="COOLED",
       waypoints=[[1220.0, 470.0], [120.0, 470.0]])
    return sm


def button_debounce() -> StateMachine:
    """Real work: debouncing a button."""
    sm = _machine("Debounce", "debounce",
                  "Contact bounce filter: a level change is believed only "
                  "after it has been stable for the whole settle time.",
                  "debounce_ctx_t", '#include "debounce_api.h"')
    st, tr = _adder(sm)
    # The longest label, "after(SETTLE_MS) [pin_is_low(ctx)]", is 34 characters,
    # about 248 px; the horizontal gaps were opened to match.
    st("i", "Start", StateKind.INITIAL, None, 96, 20, 24, 24)
    st("rel", "Released", StateKind.SIMPLE, None, 40, 120, 230, 84,
       entry="ctx->pressed = false;")
    st("mdown", "MaybePressed", StateKind.SIMPLE, None, 420, 120, 220, 96,
       note="The settle window. Any bounce back to the old level cancels "
            "the candidate reading.")
    st("prs", "Pressed", StateKind.SIMPLE, None, 960, 120, 330, 84,
       entry="ctx->pressed = true;  on_press(ctx);")
    st("mup", "MaybeReleased", StateKind.SIMPLE, None, 420, 400, 220, 96)
    tr("t0", "i", "rel")
    tr("t1", "rel", "mdown", event="EDGE_LOW", label_dx=-40.0)
    tr("t2", "mdown", "rel", event="EDGE_HIGH", label_dx=40.0)
    tr("t3", "mdown", "prs", event="after(SETTLE_MS)",
       guard="pin_is_low(ctx)")
    # A failed wait returns to Released; its path goes round ABOVE.
    tr("t4", "mdown", "rel", event="after(SETTLE_MS)", guard="else",
       waypoints=[[360.0, 40.0]])
    tr("t5", "prs", "mup", event="EDGE_HIGH", waypoints=[[820.0, 448.0]])
    tr("t6", "mup", "prs", event="EDGE_LOW", waypoints=[[860.0, 300.0]])
    tr("t7", "mup", "rel", event="after(SETTLE_MS)",
       guard="pin_is_high(ctx)", waypoints=[[180.0, 330.0]])
    tr("t8", "mup", "prs", event="after(SETTLE_MS)", guard="else",
       waypoints=[[880.0, 470.0]])
    return sm


def guard_playground() -> StateMachine:
    """For trying out the VARIABLES tab in the simulation panel."""
    sm = _machine("Thermostat", "thermostat",
                  "Every guard is written in plain variables, so the "
                  "Variables tab of the simulator can drive the whole run.",
                  "thermostat_ctx_t")
    st, tr = _adder(sm)
    # "[temperature < setpoint - hysteresis]" is 37 characters (~269 px): that
    # much room was left between the diamond and the heating/waiting states.
    st("i", "Start", StateKind.INITIAL, None, 96, 20, 24, 24)
    st("off", "Off", StateKind.SIMPLE, None, 40, 220,
       entry="heater_off();")
    st("meas", "Measure", StateKind.SIMPLE, None, 380, 220, 200, 84,
       note="Open the simulator, put numbers into 'temperature' and "
            "'setpoint' and the branches resolve by themselves.")
    st("dec", "Decide", StateKind.CHOICE, None, 760, 238, 40, 40)
    st("heat", "Heating", StateKind.SIMPLE, None, 1120, 90, 180, 70,
       entry="heater_on();")
    st("hold", "Holding", StateKind.SIMPLE, None, 1120, 390, 180, 70,
       entry="heater_off();")
    tr("t0", "i", "off")
    tr("t1", "off", "meas", event="ON", label_dy=-22.0)
    tr("t2", "meas", "dec", event="after(SAMPLE_MS)")
    tr("t3", "dec", "heat", guard="temperature < setpoint - hysteresis")
    tr("t4", "dec", "hold", guard="else")
    # The return paths run at different heights.
    tr("t5", "heat", "meas", event="after(SAMPLE_MS)",
       waypoints=[[860.0, 60.0]])
    tr("t6", "hold", "meas", event="after(SAMPLE_MS)",
       waypoints=[[860.0, 520.0]])
    tr("t7", "meas", "off", event="OFF", label_dy=26.0)
    tr("t8", "heat", "off", event="OFF", waypoints=[[300.0, 60.0]])
    tr("t9", "hold", "off", event="OFF", waypoints=[[300.0, 520.0]])
    return sm


def association_example() -> ClassModel:
    attr, op = _class_helpers()
    cm = ClassModel(name="Ordering", prefix="ordering",
                    description="Association with multiplicities and role "
                                "names at both ends.")
    cm.add_class(UmlClass(id="c_cust", name="Customer", x=60, y=60,
                          w=240, h=150,
                          attributes=[attr("id", "uint32_t"),
                                      attr("name", "char", mult="32")],
                          operations=[op("placeOrder", ret="bool",
                                         params=[("order", "Order *")])]))
    cm.add_class(UmlClass(id="c_order", name="Order", x=460, y=60,
                          w=240, h=150,
                          attributes=[attr("number", "uint32_t"),
                                      attr("total", "int32_t")],
                          operations=[op("cancel"),
                                      op("isOpen", ret="bool", const=True)],
                          note="An association says the two classes know "
                               "each other; the numbers say how many "
                               "(9.5.3 / 11.5)."))
    cm.add_relation(Relation(id="r1", source="c_cust", target="c_order",
                             kind=RelationKind.ASSOCIATION,
                             label="places",
                             source_mult="1", target_mult="0..*",
                             source_role="buyer", target_role="orders"))
    return cm


def generalization_example() -> ClassModel:
    attr, op = _class_helpers()
    cm = ClassModel(name="Shapes", prefix="shapes",
                    description="Generalization: a hollow triangle points at "
                                "the more general class.")
    cm.add_class(UmlClass(id="c_shape", name="Shape", x=320, y=40,
                          w=250, h=150, stereotype=Stereotype.ABSTRACT,
                          attributes=[attr("origin_x", "int16_t"),
                                      attr("origin_y", "int16_t")],
                          operations=[op("area", ret="uint32_t", const=True,
                                         abstract=True),
                                      op("draw", abstract=True)],
                          note="Abstract: the name is italic and the two "
                               "abstract operations have no body (9.2.3.2)."))
    cm.add_class(UmlClass(id="c_rect", name="Rectangle", x=60, y=300,
                          w=230, h=140,
                          attributes=[attr("width", "uint16_t"),
                                      attr("height", "uint16_t")],
                          operations=[op("area", ret="uint32_t", const=True),
                                      op("draw")]))
    cm.add_class(UmlClass(id="c_circ", name="Circle", x=340, y=300,
                          w=220, h=140,
                          attributes=[attr("radius", "uint16_t")],
                          operations=[op("area", ret="uint32_t", const=True),
                                      op("draw")]))
    cm.add_class(UmlClass(id="c_sq", name="Square", x=620, y=300,
                          w=220, h=110,
                          attributes=[attr("side", "uint16_t")],
                          operations=[op("area", ret="uint32_t", const=True),
                                      op("draw")]))
    for rid, src in (("g1", "c_rect"), ("g2", "c_circ"), ("g3", "c_sq")):
        cm.add_relation(Relation(id=rid, source=src, target="c_shape",
                                 kind=RelationKind.GENERALIZATION))
    return cm


def interface_example() -> ClassModel:
    attr, op = _class_helpers()
    cm = ClassModel(name="Drivers", prefix="drivers",
                    description="Interface realization: a dashed line with a "
                                "hollow triangle.")
    cm.add_class(UmlClass(id="c_if", name="ByteSink",
                          stereotype=Stereotype.INTERFACE,
                          x=360, y=40, w=250, h=130,
                          operations=[op("write", ret="bool",
                                         params=[("byte", "uint8_t")],
                                         abstract=True),
                                      op("flush", abstract=True)],
                          note="An interface declares operations only; the "
                               "realizing classes supply the bodies."))
    cm.add_class(UmlClass(id="c_uart", name="UartSink", x=100, y=290,
                          w=240, h=140,
                          attributes=[attr("base", "void *")],
                          operations=[op("write", ret="bool",
                                         params=[("byte", "uint8_t")]),
                                      op("flush")]))
    cm.add_class(UmlClass(id="c_file", name="RamSink", x=520, y=290,
                          w=240, h=140,
                          attributes=[attr("buffer", "uint8_t *"),
                                      attr("used", "size_t")],
                          operations=[op("write", ret="bool",
                                         params=[("byte", "uint8_t")]),
                                      op("flush")]))
    cm.add_relation(Relation(id="r1", source="c_uart", target="c_if",
                             kind=RelationKind.REALIZATION))
    cm.add_relation(Relation(id="r2", source="c_file", target="c_if",
                             kind=RelationKind.REALIZATION))
    return cm


def aggregation_composition_example() -> ClassModel:
    attr, op = _class_helpers()
    cm = ClassModel(name="Vehicle", prefix="vehicle",
                    description="Hollow diamond = shared whole-part; filled "
                                "diamond = the whole owns the part.")
    cm.add_class(UmlClass(id="c_car", name="Car", x=380, y=40, w=250, h=140,
                          attributes=[attr("plate", "char", mult="8")],
                          operations=[op("start", ret="bool"), op("stop")],
                          note="The engine is OWNED: destroy the car and the "
                               "engine goes with it. The trailer is only "
                               "SHARED and outlives the car (11.5.4)."))
    cm.add_class(UmlClass(id="c_engine", name="Engine", x=120, y=300,
                          w=230, h=130,
                          attributes=[attr("displacement_cc", "uint16_t")],
                          operations=[op("ignite", ret="bool")]))
    cm.add_class(UmlClass(id="c_trailer", name="Trailer", x=640, y=300,
                          w=230, h=130,
                          attributes=[attr("load_kg", "uint16_t")],
                          operations=[op("hitch"), op("unhitch")]))
    cm.add_relation(Relation(id="r1", source="c_car", target="c_engine",
                             kind=RelationKind.COMPOSITION,
                             source_mult="1", target_mult="1",
                             target_role="engine"))
    cm.add_relation(Relation(id="r2", source="c_car", target="c_trailer",
                             kind=RelationKind.AGGREGATION,
                             source_mult="0..1", target_mult="0..1",
                             target_role="trailer"))
    return cm


def dependency_example() -> ClassModel:
    attr, op = _class_helpers()
    cm = ClassModel(name="Logging", prefix="logging",
                    description="Dependency: the source merely USES the "
                                "target, it does not keep a reference.")
    cm.add_class(UmlClass(id="c_svc", name="SensorService", x=80, y=80,
                          w=270, h=150,
                          attributes=[attr("last_reading", "int32_t")],
                          operations=[op("sample", ret="int32_t"),
                                      op("report", params=[("out", "Logger *")])],
                          note="report() takes a Logger as a PARAMETER — a "
                               "use, not a structural link. That is exactly "
                               "what a dependency records (7.8.4)."))
    cm.add_class(UmlClass(id="c_log", name="Logger", x=520, y=80,
                          w=250, h=150,
                          attributes=[attr("level", "uint8_t")],
                          operations=[op("info", params=[("msg", "const char *")]),
                                      op("error", params=[("msg", "const char *")])]))
    cm.add_relation(Relation(id="r1", source="c_svc", target="c_log",
                             kind=RelationKind.DEPENDENCY, label="uses"))
    return cm


def debouncer_class() -> ClassModel:
    """Real work: a button debouncer class (with bodies)."""
    attr, op = _class_helpers()
    cm = ClassModel(
        name="ButtonKit", prefix="buttonkit",
        description="A working button debouncer: attributes, operations "
                    "with real bodies, and the port it talks to.",
        user_includes='#include "board_gpio.h"')
    cm.add_class(UmlClass(
        id="c_port", name="GpioPort", stereotype=Stereotype.INTERFACE,
        x=80, y=60, w=250, h=130,
        operations=[op("read", ret="bool", const=True, abstract=True)],
        note="The debouncer depends on a level source, not on a specific "
             "pin. Swap the port and the logic is unchanged."))
    cm.add_class(UmlClass(
        id="c_deb", name="Debouncer", x=470, y=40, w=330, h=250,
        attributes=[attr("stable", "bool", default="false"),
                    attr("candidate", "bool", default="false"),
                    attr("ticks", "uint16_t", default="0"),
                    attr("settle_ticks", "uint16_t", vis="#",
                         default="25")],
        operations=[
            op("poll", ret="bool", params=[("level", "bool")],
               body="if (level != this->candidate)\n"
                    "{\n"
                    "    this->candidate = level;\n"
                    "    this->ticks = 0U;\n"
                    "    return false;\n"
                    "}\n"
                    "if (this->ticks < this->settle_ticks)\n"
                    "{\n"
                    "    this->ticks++;\n"
                    "    return false;\n"
                    "}\n"
                    "if (this->candidate != this->stable)\n"
                    "{\n"
                    "    this->stable = this->candidate;\n"
                    "    return true;\n"
                    "}\n"
                    "return false;"),
            op("isPressed", ret="bool", const=True,
               body="return this->stable;"),
            op("reset", body="this->ticks = 0U;\n"
                             "this->candidate = this->stable;")],
        note="poll() is called once per tick with the raw level and "
             "returns true only on a settled change."))
    cm.add_class(UmlClass(
        id="c_btn", name="Button", x=470, y=360, w=330, h=150,
        # "port" and "filter" members are generated from the ROLE names of the
        # relationships; writing them as attributes too would be a clash.
        attributes=[],
        operations=[op("tick", ret="bool",
                       body="return this->filter.poll("
                            "this->port->read());")]))
    cm.add_relation(Relation(id="r1", source="c_btn", target="c_deb",
                             kind=RelationKind.COMPOSITION,
                             source_mult="1", target_mult="1",
                             target_role="filter"))
    cm.add_relation(Relation(id="r2", source="c_btn", target="c_port",
                             kind=RelationKind.ASSOCIATION,
                             source_mult="1", target_mult="1",
                             target_role="port"))
    cm.add_relation(Relation(id="r3", source="c_deb", target="c_port",
                             kind=RelationKind.DEPENDENCY, label="reads"))
    return cm


def visibility_example() -> ClassModel:
    """Visibility markers, static members and multiplicity."""
    attr, op = _class_helpers()
    cm = ClassModel(name="Registry", prefix="registry",
                    description="Every visibility marker, a static member "
                                "and an attribute multiplicity in one class.")
    cm.add_class(UmlClass(
        id="c_reg", name="DeviceRegistry", x=120, y=60, w=380, h=260,
        attributes=[attr("instance", "DeviceRegistry *", vis="+",
                         default="nullptr"),
                    # NOTE: the "slots" member is generated by the relationship BELOW;
                    # writing it once more here would define the same member twice.
                    # tanimlardi.
                    attr("used", "uint8_t", vis="#", default="0"),
                    attr("magic", "uint32_t", vis="~", default="0xC0FFEE")],
        operations=[op("add", ret="bool", vis="+",
                       params=[("device", "Device *")]),
                    op("find", ret="Device *", vis="+", const=True,
                       params=[("id", "uint16_t")]),
                    op("compact", vis="#"),
                    op("checkMagic", ret="bool", vis="-", const=True)],
        note="+ public   - private   # protected   ~ package. "
             "'slots' carries multiplicity 16, so it generates an array."))
    cm.add_class(UmlClass(
        id="c_dev", name="Device", x=600, y=120, w=250, h=140,
        attributes=[attr("id", "uint16_t"), attr("ready", "bool")],
        operations=[op("open", ret="bool"), op("close")]))
    cm.add_relation(Relation(id="r1", source="c_reg", target="c_dev",
                             kind=RelationKind.AGGREGATION,
                             source_mult="1", target_mult="0..16",
                             target_role="slots"))
    return cm


# --------------------------------------------------------------------------- #
#   Catalogue
# --------------------------------------------------------------------------- #

STATE_EXAMPLES: List[Example] = [
    Example("traffic", "Traffic Light — time events", "state",
            "State · Initial · after(N) time event",
            "Four lamp states driven only by after() triggers.",
            "13.3.3.4, 14.2.3.8", traffic_light),
    Example("composite", "Washer — composite state", "state",
            "Composite · Final · completion transition",
            "A composite state runs a whole cycle and completes on its own.",
            "14.2.3.2, 14.2.3.8.3", composite_state),
    Example("orthogonal", "Media Player — orthogonal regions", "state",
            "Composite with 2 regions · concurrent substates",
            "Playback and the clock run at the same time in two regions.",
            "14.2.3.2", orthogonal_regions),
    Example("choice", "Grader — choice vs junction", "state",
            "Choice · Junction · guard · else",
            "Dynamic branching after the effect, static branching before "
            "the source is left.",
            "14.2.3.7", choice_and_junction),
    Example("history", "Menu — shallow and deep history", "state",
            "Shallow history (H) · Deep history (H*)",
            "An interrupt returns the user to the screen they were on.",
            "14.2.3.4.5", history_states),
    Example("forkjoin", "Start-up — fork and join", "state",
            "Fork · Join · orthogonal regions",
            "Two bring-up branches start together and are synchronised.",
            "14.2.3.7", fork_and_join),
    Example("deferred", "Printer — deferred events", "state",
            "Deferrable trigger · event pool",
            "A request that arrives at a bad moment is kept, not dropped.",
            "14.2.3.4.4", deferred_events),
    Example("internal", "Counter — internal transition", "state",
            "Internal transition vs external self-transition",
            "The same event, once without and once with entry/exit.",
            "14.2.3.8.1", internal_transition),
    Example("terminate", "Session — final vs terminate", "state",
            "Final state · Terminate pseudostate",
            "One completes the region, the other stops the machine cold.",
            "14.2.3.7, 14.5.2.5", terminate_and_final),
    Example("entrypoints", "Pump — entry and exit points", "state",
            "Entry point · Exit point on a composite",
            "Named ways into and out of a composite state.",
            "14.2.3.7", entry_exit_points),
    Example("debounce", "Button Debounce — a real filter", "state",
            "Time event · guard · else · settle window",
            "Contact bounce rejected by requiring a stable level.",
            "13.3.3.4, 14.2.3.8", button_debounce),
    Example("thermostat", "Thermostat — guards you can drive", "state",
            "Choice · guards written in plain variables",
            "Built for the simulator: type numbers and watch it branch.",
            "14.2.3.7", guard_playground),
]

CLASS_EXAMPLES: List[Example] = [
    Example("association", "Ordering — association", "class",
            "Association · multiplicity · role names",
            "Who knows whom, and how many of each.",
            "9.5.3, 11.5", association_example),
    Example("generalization", "Shapes — generalization", "class",
            "Generalization · abstract class · abstract operation",
            "One base, three specialisations.",
            "9.2.3.2", generalization_example),
    Example("interface", "Drivers — interface realization", "class",
            "Interface · realization",
            "Two classes fulfilling the same declared contract.",
            "10.4", interface_example),
    Example("aggregation", "Vehicle — aggregation vs composition", "class",
            "Aggregation (hollow) · Composition (filled)",
            "Shared parts against owned parts.",
            "11.5.4", aggregation_composition_example),
    Example("dependency", "Logging — dependency", "class",
            "Dependency",
            "A use relationship that keeps no reference.",
            "7.8.4", dependency_example),
    Example("visibility", "Registry — visibility and multiplicity", "class",
            "+ - # ~ · attribute multiplicity · operation bodies",
            "Every visibility marker in one readable class.",
            "7.4, 9.5.4", visibility_example),
    Example("debouncer", "Button Kit — a class that really works", "class",
            "Composition · association · dependency · operation bodies",
            "A debouncer with real bodies, ready to generate and compile.",
            "11.5", debouncer_class),
]


def all_examples() -> List[Example]:
    return list(STATE_EXAMPLES) + list(CLASS_EXAMPLES)


def find(key: str):
    for ex in all_examples():
        if ex.key == key:
            return ex
    return None
