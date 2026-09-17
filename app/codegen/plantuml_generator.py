"""PlantUML export -- IDENTICAL TO THE DRAWING ON THE CANVAS.

Turns the diagram into a textual form that can be embedded in documents.
Because it is fed by the same model as the code generation, the document and
the code cannot drift apart.

TWO MISTAKES WE ACTUALLY MADE:

1. Only `-->` was written for every transition. PlantUML stacks transitions
   that carry no direction from top to bottom; the user drew the machine
   HORIZONTALLY and got it back VERTICAL and tangled.
2. The first fix added `left to right direction` for wide models, and this
   time the drawing ROTATED 90 DEGREES -- because PlantUML passes that
   directive to GraphViz as `rankdir=LR`, while a `-right->` arrow means
   "the same rank": in a vertical flow the same rank is side by side, in a
   horizontal flow it is ONE BELOW THE OTHER. LedOn/LedOff, drawn side by
   side, ended up stacked. A global directive and per-arrow hints DO NOT MIX.
History/junction/terminate pseudostates were also drawn as plain boxes and
internal transitions as arrows -- both contrary to UML and to the generated
code; a free-floating `note` scattered the layout too.

This version uses the POSITIONS in the model:

  * for each transition a direction is chosen from the real coordinates of
    the source and the target (`-right->`, `-down->`, ...),
  * states are emitted in the reading order of the canvas.

So the picture PlantUML produces follows the layout on the canvas.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from ..core.model import (State, StateKind, StateMachine, Transition,
                          TransitionKind)

#: A name that can be written without quotes in PlantUML.
_PLAIN_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: The PlantUML counterparts of the pseudostates.
#:
#: PlantUML has no separate notation for a junction; it uses the same
#: diamond as a choice. A body line keeps the distinction visible.
_STEREOTIP = {
    StateKind.CHOICE: "<<choice>>",
    StateKind.JUNCTION: "<<choice>>",
    StateKind.TERMINATE: "<<end>>",
    # PlantUML carries a separate style for fork/join; it draws them as a
    # thick bar, so the picture matches what the canvas shows.
    StateKind.FORK: "<<fork>>",
    StateKind.JOIN: "<<join>>",
    StateKind.ENTRY_POINT: "<<entryPoint>>",
    StateKind.EXIT_POINT: "<<exitPoint>>",
    StateKind.SUBMACHINE: "<<sdlreceive>>",
}

#: Pseudostates that appear as an arrow end and are NOT declared separately.
_UC_OLARAK = (StateKind.INITIAL, StateKind.FINAL,
              StateKind.SHALLOW_HISTORY, StateKind.DEEP_HISTORY)


def _esc(text: str) -> str:
    return " ".join(str(text).split()).replace('"', "'")


def _kimlik(name: str, used: Dict[str, str]) -> str:
    """Name -> PlantUML identifier (so names with spaces work too)."""
    if name in used:
        return used[name]
    if _PLAIN_NAME.match(name):
        used[name] = name
        return name
    temiz = re.sub(r"[^A-Za-z0-9_]", "_", name) or "S"
    if temiz[0].isdigit():
        temiz = "S" + temiz
    aday, i = temiz, 2
    while aday in used.values():
        aday = "%s_%d" % (temiz, i)
        i += 1
    used[name] = aday
    return aday


def _abs_center(sm: StateMachine, s: State) -> Tuple[float, float]:
    """The centre of the state ON THE SCENE.

    Substate x/y is relative to the inside of the parent; the direction
    calculation needs absolute positions, or nesting gets the directions wrong.
    """
    x, y = s.x, s.y
    top = sm.parent_of(s.id)
    gorulen = set()
    while top is not None and top.id not in gorulen:
        gorulen.add(top.id)
        x += top.x
        y += top.y
        top = sm.parent_of(top.id)
    return x + s.w / 2.0, y + s.h / 2.0


def _dikey_aralik(sm: StateMachine, s: State):
    """The vertical range of the state ON THE SCENE (top, bottom)."""
    _cx, cy = _abs_center(sm, s)
    return cy - s.h / 2.0, cy + s.h / 2.0


#: The "middle band" of a box: this much of the middle of its height.
#:
#: Using the full range made a 290 pixel composite state overlap almost
#: everything on screen; the Check diamond counted as "the same band"
#: because it touched the BOTTOM EDGE of Running. The middle band is a
#: better answer to "does this look like the same row".
_MIDDLE_BAND = 0.6


def yatay_mi(ust0: float, alt0: float, ust1: float, alt1: float) -> bool:
    """Do the two boxes sit in the SAME HORIZONTAL BAND?

    In PlantUML `-right->`/`-left->` means "the same rank", while
    `-down->`/`-up->` means "the next rank". So the right question is not
    "which axis is farther" but "are they on the same row".

    A wrong answer produces CONTRADICTORY CONSTRAINTS: Off and Running on the
    same rank, Running and Check on the same rank, but Check one rank above
    Off. GraphViz drops one of them and the layout falls apart.
    """
    def band(top: float, bottom: float):
        middle = (top + bottom) / 2.0
        half = (bottom - top) * _MIDDLE_BAND / 2.0
        return middle - half, middle + half

    a0, a1 = band(ust0, alt0)
    b0, b1 = band(ust1, alt1)
    return min(a1, b1) >= max(a0, b0)


def _yon(sm: StateMachine, src: State, tgt: State) -> str:
    """Marks the arrow between two states with the direction ON THE CANVAS."""
    x0, y0 = _abs_center(sm, src)
    x1, y1 = _abs_center(sm, tgt)
    ust0, alt0 = _dikey_aralik(sm, src)
    ust1, alt1 = _dikey_aralik(sm, tgt)
    if yatay_mi(ust0, alt0, ust1, alt1):
        return "-right->" if (x1 - x0) >= 0 else "-left->"
    return "-down->" if (y1 - y0) >= 0 else "-up->"


def _label(t: Transition) -> str:
    lbl = t.label()
    return (" : %s" % _esc(lbl)) if lbl else ""


def generate_plantuml(sm: StateMachine, resolve=None) -> Dict[str, str]:
    # SUBMACHINE REFERENCES ARE EXPANDED IN THE PICTURE TOO.
    #
    # Drawing a closed box would make the picture tell a DIFFERENT story from
    # the generated code: the code generates the expanded machine. UML also
    # defines a submachine as a "macro-like insertion" (14.2.3.4.7).
    from ..core.submachine import has_submachine, flatten
    if resolve is not None and has_submachine(sm):
        try:
            sm = flatten(sm, resolve)
        except Exception:                       # noqa: BLE001
            pass                                # drawing generation is never blocked

    kimlikler: Dict[str, str] = {}

    alias: Dict[str, str] = {}
    for s in sm.states.values():
        if s.kind in (StateKind.INITIAL, StateKind.FINAL):
            alias[s.id] = "[*]"
        elif s.kind.is_history:
            continue            # second pass: qualified BY ITS OWNER
        else:
            alias[s.id] = _kimlik(s.name, kimlikler)

    # A HISTORY NODE IS QUALIFIED BY ITS OWNER.
    #
    # A bare `[H]` is the history of the region it is WRITTEN IN. Because the
    # arrows are emitted at the top level, every history was attaching to the
    # same ROOT node: the histories of two different composite states
    # collapsed into one circle and the picture told the model WRONG. The
    # `GrpA[H]` form is valid everywhere and says whose history it is.
    #
    # The second pass is required: the alias of the owner is produced in the
    # first pass and the states may arrive in any order in the dictionary.
    for s in sm.states.values():
        if not s.kind.is_history:
            continue
        isaret = "[H*]" if s.kind is StateKind.DEEP_HISTORY else "[H]"
        sahip = alias.get(s.parent) if s.parent else None
        if sahip:
            alias[s.id] = "%s%s" % (sahip, isaret)
        else:
            # A history in the root region is invalid in UML (V064); still, putting
            # something into the drawing beats dropping it silently.
            alias[s.id] = isaret

    # -- Layout direction # -------------------------------------------------- #
    #
    # `left to right direction` IS NOT WRITTEN. We tried it once and the
    # drawing ROTATED 90 degrees: PlantUML passes the directive to GraphViz
    # as `rankdir=LR`, while the `-right->` arrow means "the same rank". In a
    # vertical flow the same rank is side by side, in a horizontal flow ONE
    # BELOW THE OTHER. LedOn/LedOff ended up stacked and Fault moved from the
    #bottom left to the top right.
    # EVERY ARROW already carries its own direction (see _direction); a global
    # directive is both unnecessary and harmful. `horizontal` is used only for
    # the DECLARATION ORDER: all else being equal PlantUML keeps writing order.
    kutular = [s for s in sm.states.values()
               if s.kind.is_real_state or s.kind.is_branch]
    yatay = False
    if kutular:
        xs = [_abs_center(sm, s)[0] for s in kutular]
        ys = [_abs_center(sm, s)[1] for s in kutular]
        yatay = (max(xs) - min(xs)) > (max(ys) - min(ys))

    L: List[str] = ["@startuml"]
    if sm.name:
        L.append("title %s" % _esc(sm.name))
    L += [
        "hide empty description",
        "skinparam backgroundColor #2B2D30",
        "skinparam defaultFontColor #A9B7C6",
        "skinparam ArrowFontColor #A9B7C6",
        "skinparam TitleFontColor #A9B7C6",
        "skinparam CaptionFontColor #A9B7C6",
        "skinparam state {",
        "  BackgroundColor #3C3F41",
        "  BorderColor #6B7079",
        "  FontColor #A9B7C6",
        "  ArrowColor #CC7832",
        "  StartColor #A9B7C6",
        "  EndColor #A9B7C6",
        "}",
        "",
    ]

    # Transitions that stay INSIDE a region are written into that block; they
    # are marked beforehand so they are not emitted a second time outside.
    inner_region = set()
    for t in sm.transitions.values():
        src = sm.states.get(t.source)
        tgt = sm.states.get(t.target)
        if src is None or tgt is None or t.kind is TransitionKind.INTERNAL:
            continue
        if src.parent is not None and src.parent == tgt.parent:
            inner_region.add(t.id)

    def inner_transition_rows(s: State) -> List[str]:
        """The INTERNAL transitions of a state: not an arrow, a body line.

        Per UML 2.5.1 an internal transition runs neither the exit nor the entry
        behaviour, and the generated C/C++ behaves the same way. Drawing it as
        an arrow would put the picture in CONFLICT with the code.
        """
        out = []
        for t in sm.outgoing(s.id):
            if t.kind is TransitionKind.INTERNAL:
                out.append("%s : %s" % (alias[s.id],
                                        _esc(t.label()) or "internal"))
        return out

    def body(s: State, pad: str) -> None:
        for beh, tag in ((s.entry, "entry"), (s.exit, "exit"), (s.do, "do")):
            if beh.strip():
                L.append("%s%s : %s / %s" % (pad, alias[s.id], tag, _esc(beh)))
        for row in inner_transition_rows(s):
            L.append("%s%s" % (pad, row))

    def head_row(s: State, pad: str, acik: bool) -> str:
        """Builds the `state X` / `state "Name" as X` line."""
        if alias[s.id] == s.name:
            metin = "%sstate %s" % (pad, s.name)
        else:
            metin = '%sstate "%s" as %s' % (pad, _esc(s.name), alias[s.id])
        stereo = _STEREOTIP.get(s.kind, "")
        if stereo:
            metin += " " + stereo
        if acik:
            metin += " {"
        return metin

    def draw_order(parent: Optional[str],
                     region: Optional[int] = None) -> List[State]:
        """The children in the reading order of the CANVAS.

        With ``region`` given, ONLY the ones in that region are returned; the
        regions of an orthogonal state are written separately with "--".
        """
        if region is None:
            children = list(sm.sorted_children(parent))
        else:
            children = list(sm.children_in(parent, region))
        if yatay:
            return sorted(children, key=lambda s: (s.x, s.y))
        return sorted(children, key=lambda s: (s.y, s.x))

    def emit_region(parent: Optional[str], pad: str,
                    region: Optional[int] = None) -> None:
        for s in draw_order(parent, region):
            if s.kind in _UC_OLARAK:
                # These have no separate declaration in PlantUML; they only appear as
                # arrow ends ([*], [H], [H*]).
                continue
            if s.kind is StateKind.COMPOSITE:
                L.append(head_row(s, pad, acik=True))
                # The regions of an ORTHOGONAL state are separated with "--"; PlantUML
                # draws that as concurrent regions. For a single-region state no
                # separator is written and the output stays as it was.
                bolge_sayisi = sm.region_count(s.id)

                def inner_transitions(sahip: str, which: Optional[int]) -> None:
                    """The inner arrows of an owner (optionally of one region).

                    The arrows must be written INSIDE THEIR OWN region block: if
                    they were all written at the end, PlantUML would put them in
                    the LAST region and the picture would misrepresent the model.
                    """
                    for t in sm.ordered_transitions():
                        if t.id not in inner_region:
                            continue
                        src = sm.states[t.source]
                        tgt = sm.states[t.target]
                        if src.parent != sahip:
                            continue
                        if which is not None and sm.region_of(src.id) != which:
                            continue
                        L.append("%s  %s %s %s%s"
                                 % (pad, alias[src.id], _yon(sm, src, tgt),
                                    alias[tgt.id], _label(t)))

                if bolge_sayisi > 1:
                    for r in range(bolge_sayisi):
                        if r > 0:
                            L.append("%s  --" % pad)
                        emit_region(s.id, pad + "  ", r)
                        inner_transitions(s.id, r)
                else:
                    emit_region(s.id, pad + "  ")
                    inner_transitions(s.id, None)
                L.append("%s}" % pad)
            else:
                L.append(head_row(s, pad, acik=False))
                if s.kind is StateKind.JUNCTION:
                    # The diamond is the same as a choice; keep the distinction in text.
                    L.append("%s%s : <<junction>>" % (pad, alias[s.id]))
            body(s, pad)

    emit_region(None, "")
    L.append("")

    for t in sm.ordered_transitions():
        if t.id in inner_region or t.kind is TransitionKind.INTERNAL:
            continue
        src = sm.states.get(t.source)
        tgt = sm.states.get(t.target)
        if src is None or tgt is None:
            continue
        L.append("%s %s %s%s" % (alias[src.id], _yon(sm, src, tgt),
                                 alias[tgt.id], _label(t)))

    if sm.description:
        # A free-floating `note` broke the layout: PlantUML kept it in a random
        # corner and stretched the arrows. A `caption` goes BELOW the picture.
        L += ["", "caption %s" % _esc(sm.description)]

    L += ["", "@enduml", ""]
    return {"%s.puml" % sm.prefix: "\n".join(L)}
