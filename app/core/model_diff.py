"""SEMANTIC comparison of two model versions -- without Qt.

WHY
---
Model files are JSON. `git diff` compares them as TEXT and the result is
unreadable: moving a state by 10 pixels produces a line change that looks
unrelated, and adding a state comes out as dozens of lines full of id and
coordinate fields. The question the user is asking is "WHAT changed on the
diagram" -- not "which JSON line changed".

This module compares the two versions AT MODEL LEVEL:

    + State  LedOn                     added
    - State  Standby                   removed
    ~ State  Running       entry: ...  changed
    + Transition  Off --BUTTON--> Running

Matching happens by id, so a renamed element shows up as "renamed" and NOT
as a removal plus an addition.

The output is a list of (kind, sign, text) triples; the interface does the
colouring: sign "+" -> added (green), "-" -> removed (red), "~" -> changed.
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional, Tuple

#: (kind, sign, text)
DiffRow = Tuple[str, str, str]

#: Fields IGNORED in the comparison. They do not change the MEANING of the
#: diagram; included, every drag of the window would produce a "changed"
#: row and the real changes would drown.
_VISUAL_FIELDS = {"x", "y", "w", "h", "waypoints", "label_dx", "label_dy"}


def _load(text: str) -> Optional[dict]:
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _anlamli(d: dict) -> dict:
    """Gorsel alanlari atilmis kopya."""
    return {k: v for k, v in d.items() if k not in _VISUAL_FIELDS}


def _state_label(d: dict) -> str:
    return "%s %s" % (str(d.get("kind", "state")).replace("_", " "),
                      d.get("name", "?"))


def _transition_label(d: dict, states: Dict[str, dict]) -> str:
    source = states.get(d.get("source", ""), {}).get("name", "?")
    target = states.get(d.get("target", ""), {}).get("name", "?")
    event = d.get("event") or "(completion)"
    guard = d.get("guard") or ""
    action = d.get("action") or ""
    label = "%s --%s--> %s" % (source, event, target)
    if guard:
        label += "  [%s]" % guard
    if action:
        label += "  / %s" % action
    return label


def _farklar(old: dict, new: dict) -> List[str]:
    """The list of changed fields as 'field: old -> new'."""
    out = []
    for key in sorted(set(old) | set(new)):
        a, b = old.get(key), new.get(key)
        if a == b:
            continue
        out.append("%s: %r -> %r" % (key, a, b))
    return out


def _karsilastir(section: str, old_list, new_list, label_fn) -> List[DiffRow]:
    """Matches by id and produces added / removed / changed rows."""
    old = {d.get("id"): d for d in old_list if isinstance(d, dict)}
    new = {d.get("id"): d for d in new_list if isinstance(d, dict)}

    satirlar: List[DiffRow] = []

    for kimlik, d in new.items():
        if kimlik not in old:
            satirlar.append((section, "+", label_fn(d)))

    for kimlik, d in old.items():
        if kimlik not in new:
            satirlar.append((section, "-", label_fn(d)))

    for kimlik, y in new.items():
        e = old.get(kimlik)
        if e is None:
            continue
        degisen = _farklar(_anlamli(e), _anlamli(y))
        if not degisen:
            continue
        title = label_fn(y)
        old_name, new_name = e.get("name"), y.get("name")
        if old_name != new_name:
            # A name change is reported separately: since the id is the same
            # this is a rename, NOT a "remove + add".
            title = "%s  (renamed from '%s')" % (title, old_name)
        satirlar.append((section, "~", title))
        for row in degisen:
            satirlar.append((section, " ", "    " + row))

    return satirlar


def element_status(old_text: str, new_text: str) -> dict:
    """Diff at ID level: in a form the canvas can paint.

    The textual diff rows are for reading; to PAINT the diagram we need to
    know which ELEMENT was added / removed / changed.

    Returns::

        {"added":   {id, ...},          # in the new version, not in the old
         "removed": {id: old_dict},     # was in the old version, gone now
         "changed": {id, ...}}          # ikisinde de var, anlami degismis

    `removed` CARRIES the dictionary: a deleted element is not in the new
    model, so it can only be drawn (as a ghost) the way it was in the old one.
    """
    old = _load(old_text) or {}
    new = _load(new_text) or {}

    added, silinen, degisen = set(), {}, set()
    for field in ("states", "transitions", "classes", "relations"):
        e = {d.get("id"): d for d in (old.get(field) or [])
             if isinstance(d, dict)}
        y = {d.get("id"): d for d in (new.get(field) or [])
             if isinstance(d, dict)}
        for kimlik in y:
            if kimlik not in e:
                added.add(kimlik)
            elif _farklar(_anlamli(e[kimlik]), _anlamli(y[kimlik])):
                degisen.add(kimlik)
        for kimlik, d in e.items():
            if kimlik not in y:
                silinen[kimlik] = d
    return {"added": added, "removed": silinen, "changed": degisen}


def state_machine_diff(old_text: str, new_text: str) -> List[DiffRow]:
    """Compares two state machine versions."""
    old = _load(old_text) or {}
    new = _load(new_text) or {}

    states = {}
    for d in list(old.get("states") or []) + list(new.get("states") or []):
        if isinstance(d, dict):
            states[d.get("id")] = d

    satirlar: List[DiffRow] = []
    satirlar += _karsilastir("States", old.get("states") or [],
                             new.get("states") or [], _state_label)
    satirlar += _karsilastir(
        "Transitions", old.get("transitions") or [],
        new.get("transitions") or [],
        lambda d: _transition_label(d, states))

    satirlar += _makine_ayarlari(old, new)
    return satirlar


def _makine_ayarlari(old: dict, new: dict) -> List[DiffRow]:
    """The machine-level fields (name, prefix, context type, ...)."""
    fields = ("name", "prefix", "context_type", "user_includes", "description")
    out: List[DiffRow] = []
    for field in fields:
        a, b = old.get(field), new.get(field)
        if a != b:
            out.append(("Machine", "~", "%s: %r -> %r" % (field, a, b)))
    return out


def class_model_diff(old_text: str, new_text: str) -> List[DiffRow]:
    """Compares two class diagram versions."""
    old = _load(old_text) or {}
    new = _load(new_text) or {}

    siniflar = {}
    for d in list(old.get("classes") or []) + list(new.get("classes") or []):
        if isinstance(d, dict):
            siniflar[d.get("id")] = d

    def class_label(d: dict) -> str:
        damga = d.get("stereotype") or ""
        on = ("«%s» " % damga) if damga and damga != "none" else ""
        return "%s%s" % (on, d.get("name", "?"))

    def relation_label(d: dict) -> str:
        source = siniflar.get(d.get("source", ""), {}).get("name", "?")
        target = siniflar.get(d.get("target", ""), {}).get("name", "?")
        return "%s  %s  %s" % (source, d.get("kind", "association"), target)

    satirlar: List[DiffRow] = []
    satirlar += _karsilastir("Classes", old.get("classes") or [],
                             new.get("classes") or [], class_label)
    satirlar += _karsilastir("Relations", old.get("relations") or [],
                             new.get("relations") or [], relation_label)
    return satirlar


def diff_for(path: str, old_text: str, new_text: str) -> Optional[List[DiffRow]]:
    """Semantic diff chosen by file type; ``None`` when not a model file.

    Returning ``None`` means "show the textual diff" -- which is the right
    answer for generated C/C++ files, where a line-based diff already reads
    """
    name = path.lower()
    if name.endswith(".usm"):
        return state_machine_diff(old_text, new_text)
    if name.endswith(".ucd"):
        return class_model_diff(old_text, new_text)
    if name.endswith(".json"):
        # Extension is ambiguous: look at the CONTENT.
        data = _load(new_text) or _load(old_text) or {}
        if isinstance(data.get("classes"), list):
            return class_model_diff(old_text, new_text)
        if isinstance(data.get("states"), list):
            return state_machine_diff(old_text, new_text)
    return None


def render(rows: List[DiffRow]) -> str:
    """Turns the rows into plain text the interface can colour.

    The format is COMPATIBLE with `git diff`: the leading '+' / '-' signs let
    the existing highlighter work unchanged; no separate colour path is
    yazmak, iki yerde bakim demek olurdu.
    """
    if not rows:
        return ""
    out: List[str] = []
    last_section = None
    for section, isaret, text in rows:
        if section != last_section:
            if out:
                out.append("")
            out.append("@@ %s @@" % section)
            last_section = section
        out.append("%s %s" % (isaret, text) if isaret != " " else "  " + text)
    return "\n".join(out)


def summary(rows: List[DiffRow]) -> Tuple[int, int, int]:
    """The (added, removed, changed) counts."""
    art = sum(1 for _b, s, _t in rows if s == "+")
    eksi = sum(1 for _b, s, _t in rows if s == "-")
    degisen = sum(1 for _b, s, _t in rows if s == "~")
    return art, eksi, degisen
