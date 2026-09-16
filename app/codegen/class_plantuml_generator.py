"""Sinif diyagrami PlantUML disa aktarimi -- TUVALDEKI CIZIMIN AYNISI.

Durum makinesi ureteciyle AYNI kurallar gecerlidir (bkz.
plantuml_generator.py): yon bilgisi olmayan bir PlantUML metni, tuvalde
yatay cizilmis bir diyagrami dikey ve karisik bir resme cevirir. Burada
da iliski oklari modeldeki KONUMLARDAN yon alir, diyagram genisse
`left to right direction` yazilir ve siniflar tuvaldeki okuma sirasina
gore yayimlanir.

Ayrica: bosluklu sinif adlari tirnaklanir (aksi halde PlantUML metni
ayristiramaz), uc adlari (role) ve `{static}` isaretleri kaybolmaz,
serbest `note` yerine yerlesimi bozmayan `caption` kullanilir.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

from ..core.class_model import ClassModel, RelationKind, Stereotype, UmlClass
from .plantuml_generator import yatay_mi

#: Iliski turu -> PlantUML ok govdesi (yon eki ayrica eklenir).
_ARROWS = {
    RelationKind.ASSOCIATION: "-->",
    RelationKind.AGGREGATION: "o--",
    RelationKind.COMPOSITION: "*--",
    RelationKind.GENERALIZATION: "--|>",
    RelationKind.REALIZATION: "..|>",
    RelationKind.DEPENDENCY: "..>",
}

#: Ok govdesine yon eki yerlestirme kaliplari.
#:
#: PlantUML'de yon, cizginin ORTASINA yazilir: `-->` -> `-right->`,
#: `o--` -> `o-right-`, `..|>` -> `.right.|>`. Duz bir metin eki yeterli
#: degildir, bu yuzden her tur icin kalip ayri tutulur.
_YONLU = {
    RelationKind.ASSOCIATION: "-%s->",
    RelationKind.AGGREGATION: "o-%s-",
    RelationKind.COMPOSITION: "*-%s-",
    RelationKind.GENERALIZATION: "-%s-|>",
    RelationKind.REALIZATION: ".%s.|>",
    RelationKind.DEPENDENCY: ".%s.>",
}

_SADE_AD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _esc(text: str) -> str:
    return " ".join(str(text).split()).replace('"', "'")


def _kimlik(name: str, used: Dict[str, str]) -> str:
    """Ad -> PlantUML tanimlayicisi (bosluklu adlar da calissin)."""
    if name in used:
        return used[name]
    if _SADE_AD.match(name):
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
    """Iliski okunu TUVALDEKI yone gore isaretler.

    PlantUML'de yatay yon "ayni rank", dikey yon "sonraki rank" demektir;
    bu yuzden olcut "hangi eksende daha uzak" degil, iki kutunun AYNI
    BANTTA olup olmadigidir (bkz. plantuml_generator._yatay_mi).
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

    # `left to right direction` YAZILMAZ -- durum diyagramindaki ile ayni
    # gerekce: PlantUML onu `rankdir=LR` diye gecirir ve `-right-` ("ayni
    # rank") yatay olmaktan cikip DIKEY olur; cizim 90 derece doner.
    # Yon bilgisi zaten her okun kendisindedir (bkz. _yon).
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

    # Siniflari TUVALDEKI okuma sirasina gore yayimla: PlantUML esit
    # kosullarda bildirim sirasini korur, bu da resmi cizime yaklastirir.
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
        # PlantUML'de butun-parca oklari "Butun *-- Parca" yonundedir.
        sol = "%s " % alias[src.id]
        if r.source_mult:
            sol += '"%s" ' % _esc(r.source_mult)
        sag = " "
        if r.target_mult:
            sag += '"%s" ' % _esc(r.target_mult)
        sag += alias[tgt.id]
        satir = sol + _yon(src, tgt, r.kind) + sag

        # Uc adlari (role) ve etiket TEK bir ":" bolumunde toplanir;
        # PlantUML ikinci bir ":" kabul etmez.
        parcalar = []
        if r.label:
            parcalar.append(_esc(r.label))
        if r.source_role:
            parcalar.append("%s (source)" % _esc(r.source_role))
        if r.target_role:
            parcalar.append("%s (target)" % _esc(r.target_role))
        if parcalar:
            satir += " : " + " / ".join(parcalar)
        L.append(satir)

    if cm.description:
        L += ["", "caption %s" % _esc(cm.description)]

    L += ["", "@enduml", ""]
    return {"%s_classes.puml" % cm.prefix: "\n".join(L)}
