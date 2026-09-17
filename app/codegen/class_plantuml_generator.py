"""Class diagram PlantUML export -- IDENTICAL TO THE DRAWING ON THE CANVAS.

The SAME rules apply as in the state machine generator (see
plantuml_generator.py): PlantUML text without direction information turns a
diagram drawn horizontally on the canvas into a vertical, tangled picture.
Here too the relationship arrows take their direction from the POSITIONS in
the model, `left to right direction` is written when the diagram is wide, and
the classes are emitted in the reading order of the canvas.

Also: class names containing spaces are quoted (PlantUML cannot parse the
text otherwise), end names (roles) and `{static}` markers are not lost, and
a layout-friendly `caption` is used instead of a free-floating `note`.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

from ..core.class_model import ClassModel, RelationKind, Stereotype, UmlClass
from .plantuml_generator import yatay_mi

#: Relationship kind -> PlantUML arrow body (the direction is added later).
_ARROWS = {
    RelationKind.ASSOCIATION: "-->",
    RelationKind.AGGREGATION: "o--",
    RelationKind.COMPOSITION: "*--",
    RelationKind.GENERALIZATION: "--|>",
    RelationKind.REALIZATION: "..|>",
    RelationKind.DEPENDENCY: "..>",
}

#: Patterns for placing the direction infix into the arrow body.
#:
#: In PlantUML the direction goes in the MIDDLE of the line: `-->` ->
#: `-right->`, `o--` -> `o-right-`, `..|>` -> `.right.|>`. A plain text
#: suffix is not enough, so each kind keeps its own pattern.
_YONLU = {
    RelationKind.ASSOCIATION: "-%s->",
    RelationKind.AGGREGATION: "o-%s-",
    RelationKind.COMPOSITION: "*-%s-",
    RelationKind.GENERALIZATION: "-%s-|>",
    RelationKind.REALIZATION: ".%s.|>",
    RelationKind.DEPENDENCY: ".%s.>",
}

_PLAIN_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _esc(text: str) -> str:
    return " ".join(str(text).split()).replace('"', "'")


def _kimlik(name: str, used: Dict[str, str]) -> str:
    """Name -> PlantUML identifier (so names with spaces work too)."""
    if name in used:
        return used[name]
    if _PLAIN_NAME.match(name):
        used[name] = name
        return name
    temiz = re.sub(r"[^A-Za-z0-9_]", "_", name) or "C"
    if temiz[0].isdigit():
        temiz = "C" + temiz
    aday, i = temiz, 2
    while aday in used.values():
        aday = "%s_%d" % (temiz, i)
        i += 1
    used[name] = aday
    return aday


def _center(c: UmlClass) -> Tuple[float, float]:
    return c.x + c.w / 2.0, c.y + c.h / 2.0


def _yon(src: UmlClass, tgt: UmlClass, kind: RelationKind) -> str:
    """Marks the relationship arrow with the direction ON THE CANVAS.

    In PlantUML a horizontal direction means "the same rank" and a vertical
    one means "the next rank"; so the criterion is not "which axis is farther"
    but whether the two boxes sit in the SAME BAND (see _is_horizontal there).
    """
    x0, y0 = _center(src)
    x1, y1 = _center(tgt)
    if yatay_mi(src.y, src.y + src.h, tgt.y, tgt.y + tgt.h):
        yon = "right" if (x1 - x0) >= 0 else "left"
    else:
        yon = "down" if (y1 - y0) >= 0 else "up"
    return _YONLU[kind] % yon


def generate_class_plantuml(cm: ClassModel) -> Dict[str, str]:
    kimlikler: Dict[str, str] = {}
    alias = {c.id: _kimlik(c.name, kimlikler)
             for c in cm.ordered_classes()}

    siniflar = list(cm.ordered_classes())
    yatay = False
    if siniflar:
        xs = [_center(c)[0] for c in siniflar]
        ys = [_center(c)[1] for c in siniflar]
        yatay = (max(xs) - min(xs)) > (max(ys) - min(ys))

    # `left to right direction` IS NOT WRITTEN -- same reason as in the state
    # diagram: PlantUML turns it into `rankdir=LR`, and `-right-` ("the same
    # rank") stops being horizontal and becomes VERTICAL; the drawing rotates
    # 90 degrees. The direction is already carried by each arrow (see _direction).
    L: List[str] = ["@startuml"]
    if cm.name:
        L.append("title %s" % _esc(cm.name))
    L += [
        "skinparam classAttributeIconSize 0",
        "skinparam backgroundColor #2B2D30",
        "skinparam defaultFontColor #A9B7C6",
        "skinparam ArrowFontColor #A9B7C6",
        "skinparam TitleFontColor #A9B7C6",
        "skinparam CaptionFontColor #A9B7C6",
        "skinparam class {",
        "  BackgroundColor #3C3F41",
        "  BorderColor #6B7079",
        "  FontColor #A9B7C6",
        "  ArrowColor #CC7832",
        "}",
        "",
    ]

    # Emit the classes in the reading order of the CANVAS: all else being equal
    # PlantUML keeps the declaration order, which brings the picture closer.
    sirali = sorted(siniflar,
                    key=(lambda c: (c.x, c.y)) if yatay
                    else (lambda c: (c.y, c.x)))

    for c in sirali:
        if c.stereotype is Stereotype.INTERFACE:
            tur = "interface"
        elif c.is_abstract:
            tur = "abstract class"
        else:
            tur = "class"
        if alias[c.id] == c.name:
            bas = "%s %s" % (tur, c.name)
        else:
            bas = '%s "%s" as %s' % (tur, _esc(c.name), alias[c.id])
        L.append(bas + " {")
        for a in c.attributes:
            isaret = "{static} " if a.static else ""
            L.append("  %s%s" % (isaret, a.label()))
        for o in c.operations:
            isaret = ""
            if o.abstract:
                isaret += "{abstract} "
            if o.static:
                isaret += "{static} "
            L.append("  %s%s" % (isaret, o.label()))
        L.append("}")
        L.append("")

    for r in cm.ordered_relations():
        src = cm.classes.get(r.source)
        tgt = cm.classes.get(r.target)
        if src is None or tgt is None:
            continue
        # In PlantUML whole-part arrows point "Whole *-- Part".
        sol = "%s " % alias[src.id]
        if r.source_mult:
            sol += '"%s" ' % _esc(r.source_mult)
        right = " "
        if r.target_mult:
            right += '"%s" ' % _esc(r.target_mult)
        right += alias[tgt.id]
        row = sol + _yon(src, tgt, r.kind) + right

        # End names (roles) and the label are collected into ONE ":" section;
        # PlantUML does not accept a second ":".
        parts = []
        if r.label:
            parts.append(_esc(r.label))
        if r.source_role:
            parts.append("%s (source)" % _esc(r.source_role))
        if r.target_role:
            parts.append("%s (target)" % _esc(r.target_role))
        if parts:
            row += " : " + " / ".join(parts)
        L.append(row)

    if cm.description:
        L += ["", "caption %s" % _esc(cm.description)]

    L += ["", "@enduml", ""]
    return {"%s_classes.puml" % cm.prefix: "\n".join(L)}
