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
_GORSEL_ALANLAR = {"x", "y", "w", "h", "waypoints", "label_dx", "label_dy"}


def _yukle(metin: str) -> Optional[dict]:
    try:
        veri = json.loads(metin)
    except ValueError:
        return None
    return veri if isinstance(veri, dict) else None


def _anlamli(d: dict) -> dict:
    """Gorsel alanlari atilmis kopya."""
    return {k: v for k, v in d.items() if k not in _GORSEL_ALANLAR}


def _durum_etiketi(d: dict) -> str:
    return "%s %s" % (str(d.get("kind", "state")).replace("_", " "),
                      d.get("name", "?"))


def _gecis_etiketi(d: dict, durumlar: Dict[str, dict]) -> str:
    kaynak = durumlar.get(d.get("source", ""), {}).get("name", "?")
    hedef = durumlar.get(d.get("target", ""), {}).get("name", "?")
    olay = d.get("event") or "(completion)"
    guard = d.get("guard") or ""
    eylem = d.get("action") or ""
    etiket = "%s --%s--> %s" % (kaynak, olay, hedef)
    if guard:
        etiket += "  [%s]" % guard
    if eylem:
        etiket += "  / %s" % eylem
    return etiket


def _farklar(eski: dict, yeni: dict) -> List[str]:
    """The list of changed fields as 'field: old -> new'."""
    out = []
    for anahtar in sorted(set(eski) | set(yeni)):
        a, b = eski.get(anahtar), yeni.get(anahtar)
        if a == b:
            continue
        out.append("%s: %r -> %r" % (anahtar, a, b))
    return out


def _karsilastir(bolum: str, eski_liste, yeni_liste, etiket_fn) -> List[DiffRow]:
    """Matches by id and produces added / removed / changed rows."""
    eski = {d.get("id"): d for d in eski_liste if isinstance(d, dict)}
    yeni = {d.get("id"): d for d in yeni_liste if isinstance(d, dict)}

    satirlar: List[DiffRow] = []

    for kimlik, d in yeni.items():
        if kimlik not in eski:
            satirlar.append((bolum, "+", etiket_fn(d)))

    for kimlik, d in eski.items():
        if kimlik not in yeni:
            satirlar.append((bolum, "-", etiket_fn(d)))

    for kimlik, y in yeni.items():
        e = eski.get(kimlik)
        if e is None:
            continue
        degisen = _farklar(_anlamli(e), _anlamli(y))
        if not degisen:
            continue
        baslik = etiket_fn(y)
        eski_ad, yeni_ad = e.get("name"), y.get("name")
        if eski_ad != yeni_ad:
            # A name change is reported separately: since the id is the same
            # this is a rename, NOT a "remove + add".
            baslik = "%s  (renamed from '%s')" % (baslik, eski_ad)
        satirlar.append((bolum, "~", baslik))
        for satir in degisen:
            satirlar.append((bolum, " ", "    " + satir))

    return satirlar


def element_status(eski_metin: str, yeni_metin: str) -> dict:
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
    eski = _yukle(eski_metin) or {}
    yeni = _yukle(yeni_metin) or {}

    eklenen, silinen, degisen = set(), {}, set()
    for alan in ("states", "transitions", "classes", "relations"):
        e = {d.get("id"): d for d in (eski.get(alan) or [])
             if isinstance(d, dict)}
        y = {d.get("id"): d for d in (yeni.get(alan) or [])
             if isinstance(d, dict)}
        for kimlik in y:
            if kimlik not in e:
                eklenen.add(kimlik)
            elif _farklar(_anlamli(e[kimlik]), _anlamli(y[kimlik])):
                degisen.add(kimlik)
        for kimlik, d in e.items():
            if kimlik not in y:
                silinen[kimlik] = d
    return {"added": eklenen, "removed": silinen, "changed": degisen}


def state_machine_diff(eski_metin: str, yeni_metin: str) -> List[DiffRow]:
    """Compares two state machine versions."""
    eski = _yukle(eski_metin) or {}
    yeni = _yukle(yeni_metin) or {}

    durumlar = {}
    for d in list(eski.get("states") or []) + list(yeni.get("states") or []):
        if isinstance(d, dict):
            durumlar[d.get("id")] = d

    satirlar: List[DiffRow] = []
    satirlar += _karsilastir("States", eski.get("states") or [],
                             yeni.get("states") or [], _durum_etiketi)
    satirlar += _karsilastir(
        "Transitions", eski.get("transitions") or [],
        yeni.get("transitions") or [],
        lambda d: _gecis_etiketi(d, durumlar))

    satirlar += _makine_ayarlari(eski, yeni)
    return satirlar


def _makine_ayarlari(eski: dict, yeni: dict) -> List[DiffRow]:
    """The machine-level fields (name, prefix, context type, ...)."""
    alanlar = ("name", "prefix", "context_type", "user_includes", "description")
    out: List[DiffRow] = []
    for alan in alanlar:
        a, b = eski.get(alan), yeni.get(alan)
        if a != b:
            out.append(("Machine", "~", "%s: %r -> %r" % (alan, a, b)))
    return out


def class_model_diff(eski_metin: str, yeni_metin: str) -> List[DiffRow]:
    """Compares two class diagram versions."""
    eski = _yukle(eski_metin) or {}
    yeni = _yukle(yeni_metin) or {}

    siniflar = {}
    for d in list(eski.get("classes") or []) + list(yeni.get("classes") or []):
        if isinstance(d, dict):
            siniflar[d.get("id")] = d

    def sinif_etiketi(d: dict) -> str:
        damga = d.get("stereotype") or ""
        on = ("«%s» " % damga) if damga and damga != "none" else ""
        return "%s%s" % (on, d.get("name", "?"))

    def iliski_etiketi(d: dict) -> str:
        kaynak = siniflar.get(d.get("source", ""), {}).get("name", "?")
        hedef = siniflar.get(d.get("target", ""), {}).get("name", "?")
        return "%s  %s  %s" % (kaynak, d.get("kind", "association"), hedef)

    satirlar: List[DiffRow] = []
    satirlar += _karsilastir("Classes", eski.get("classes") or [],
                             yeni.get("classes") or [], sinif_etiketi)
    satirlar += _karsilastir("Relations", eski.get("relations") or [],
                             yeni.get("relations") or [], iliski_etiketi)
    return satirlar


def diff_for(path: str, eski_metin: str, yeni_metin: str) -> Optional[List[DiffRow]]:
    """Semantic diff chosen by file type; ``None`` when not a model file.

    Returning ``None`` means "show the textual diff" -- which is the right
    answer for generated C/C++ files, where a line-based diff already reads
    """
    ad = path.lower()
    if ad.endswith(".usm"):
        return state_machine_diff(eski_metin, yeni_metin)
    if ad.endswith(".ucd"):
        return class_model_diff(eski_metin, yeni_metin)
    if ad.endswith(".json"):
        # Extension is ambiguous: look at the CONTENT.
        veri = _yukle(yeni_metin) or _yukle(eski_metin) or {}
        if isinstance(veri.get("classes"), list):
            return class_model_diff(eski_metin, yeni_metin)
        if isinstance(veri.get("states"), list):
            return state_machine_diff(eski_metin, yeni_metin)
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
    son_bolum = None
    for bolum, isaret, metin in rows:
        if bolum != son_bolum:
            if out:
                out.append("")
            out.append("@@ %s @@" % bolum)
            son_bolum = bolum
        out.append("%s %s" % (isaret, metin) if isaret != " " else "  " + metin)
    return "\n".join(out)


def summary(rows: List[DiffRow]) -> Tuple[int, int, int]:
    """The (added, removed, changed) counts."""
    art = sum(1 for _b, s, _t in rows if s == "+")
    eksi = sum(1 for _b, s, _t in rows if s == "-")
    degisen = sum(1 for _b, s, _t in rows if s == "~")
    return art, eksi, degisen
