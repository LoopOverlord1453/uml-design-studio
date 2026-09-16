"""Veri modeli: UML sinif diyagrami.

Bu modul yalnizca *veri* tutar; Qt'ye bagimliligi yoktur.

Semantik dayanak: UML 2.5.1, Bolum 9 (Classification) ve Bolum 11
(StructuredClassifiers). Desteklenen oğeler:
  * Class / Abstract Class / <<interface>>
  * Attribute (gorunurluk, tip, varsayilan deger, statik, coklugu)
  * Operation (gorunurluk, parametreler, donus tipi, soyut, statik, const)
  * Association / Aggregation / Composition / Generalization /
    Realization / Dependency (uc coklugu ve rol adlariyla)
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Dict, List, Optional

CLASS_SCHEMA_VERSION = 1


def new_id(prefix: str) -> str:
    return "%s_%s" % (prefix, uuid.uuid4().hex[:8])


class Stereotype(str, Enum):
    """Sinifin UML kaliplari."""

    CLASS = "class"
    ABSTRACT = "abstract"      # italik ad
    INTERFACE = "interface"    # <<interface>>


class Visibility(str, Enum):
    PUBLIC = "+"
    PRIVATE = "-"
    PROTECTED = "#"
    PACKAGE = "~"


class RelationKind(str, Enum):
    """UML iliski turleri (cizim ucundaki isaretle birlikte)."""

    ASSOCIATION = "association"        # duz cizgi + acik ok
    AGGREGATION = "aggregation"        # ici bos elmas (paylasilan butun-parca)
    COMPOSITION = "composition"        # dolu elmas (sahiplenen butun-parca)
    GENERALIZATION = "generalization"  # ici bos ucgen (kalitim)
    REALIZATION = "realization"        # kesikli cizgi + ici bos ucgen
    DEPENDENCY = "dependency"          # kesikli cizgi + acik ok


@dataclass
class Attribute:
    """Sinif niteliyi:  gorunurluk ad : tip = varsayilan"""

    name: str = "attr"
    type: str = "int32_t"
    visibility: str = Visibility.PRIVATE.value
    default: str = ""
    static: bool = False
    multiplicity: str = ""      # "", "1", "0..1", "0..*", "N"

    def label(self) -> str:
        txt = "%s %s : %s" % (self.visibility, self.name, self.type)
        if self.multiplicity:
            txt += " [%s]" % self.multiplicity
        if self.default:
            txt += " = %s" % self.default
        return txt


@dataclass
class Parameter:
    name: str = "value"
    type: str = "int32_t"


@dataclass
class Operation:
    """Sinif islemi:  gorunurluk ad(parametreler) : donus"""

    name: str = "operation"
    return_type: str = "void"
    visibility: str = Visibility.PUBLIC.value
    params: List[Parameter] = field(default_factory=list)
    static: bool = False
    abstract: bool = False      # C++: saf sanal; C: vtable girisi
    const: bool = False
    body: str = ""              # istege bagli govde (C/C++ deyimleri)

    def label(self) -> str:
        args = ", ".join("%s : %s" % (p.name, p.type) for p in self.params)
        txt = "%s %s(%s)" % (self.visibility, self.name, args)
        if self.return_type and self.return_type != "void":
            txt += " : %s" % self.return_type
        return txt


@dataclass
class UmlClass:
    id: str = field(default_factory=lambda: new_id("c"))
    name: str = "NewClass"
    stereotype: Stereotype = Stereotype.CLASS
    attributes: List[Attribute] = field(default_factory=list)
    operations: List[Operation] = field(default_factory=list)

    x: float = 0.0
    y: float = 0.0
    w: float = 220.0
    h: float = 140.0
    note: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["stereotype"] = self.stereotype.value
        return d

    @staticmethod
    def from_dict(d: dict) -> "UmlClass":
        d = dict(d)
        d["stereotype"] = Stereotype(d.get("stereotype", "class"))
        d["attributes"] = [Attribute(**{k: v for k, v in a.items()
                                        if k in Attribute.__dataclass_fields__})
                           for a in d.get("attributes", [])]
        ops = []
        for o in d.get("operations", []):
            o = dict(o)
            o["params"] = [Parameter(**{k: v for k, v in p.items()
                                        if k in Parameter.__dataclass_fields__})
                           for p in o.get("params", [])]
            ops.append(Operation(**{k: v for k, v in o.items()
                                    if k in Operation.__dataclass_fields__}))
        d["operations"] = ops
        allowed = set(UmlClass.__dataclass_fields__)
        return UmlClass(**{k: v for k, v in d.items() if k in allowed})

    @property
    def is_interface(self) -> bool:
        return self.stereotype is Stereotype.INTERFACE

    @property
    def is_abstract(self) -> bool:
        return self.stereotype in (Stereotype.ABSTRACT, Stereotype.INTERFACE) \
            or any(op.abstract for op in self.operations)


@dataclass
class Relation:
    """Iki sinif arasindaki iliski. source -> target yonu:
       Generalization/Realization : source, target'tan turer.
       Aggregation/Composition    : source BUTUN, target PARCA'dir.
       Association/Dependency     : source, target'i kullanir."""

    id: str = field(default_factory=lambda: new_id("r"))
    source: str = ""
    target: str = ""
    kind: RelationKind = RelationKind.ASSOCIATION
    label: str = ""
    source_mult: str = ""       # kaynak uctaki cokluk (or. "1")
    target_mult: str = ""       # hedef uctaki cokluk (or. "0..*")
    source_role: str = ""
    target_role: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kind"] = self.kind.value
        return d

    @staticmethod
    def from_dict(d: dict) -> "Relation":
        d = dict(d)
        d["kind"] = RelationKind(d.get("kind", "association"))
        allowed = set(Relation.__dataclass_fields__)
        return Relation(**{k: v for k, v in d.items() if k in allowed})


@dataclass
class ClassModel:
    """Tum sinif diyagramini temsil eden dokuman."""

    name: str = "Design"
    prefix: str = "design"      # C sembol on eki / C++ ad uzayi
    description: str = ""
    user_includes: str = ""
    classes: Dict[str, UmlClass] = field(default_factory=dict)
    relations: Dict[str, Relation] = field(default_factory=dict)

    # -- erisim yardimcilari ------------------------------------------------ #

    def add_class(self, c: UmlClass) -> UmlClass:
        self.classes[c.id] = c
        return c

    def add_relation(self, r: Relation) -> Relation:
        self.relations[r.id] = r
        return r

    def remove_class(self, cid: str) -> None:
        for rid in [r.id for r in self.relations.values()
                    if r.source == cid or r.target == cid]:
            self.relations.pop(rid, None)
        self.classes.pop(cid, None)

    def remove_relation(self, rid: str) -> None:
        self.relations.pop(rid, None)

    def ordered_classes(self) -> List[UmlClass]:
        return sorted(self.classes.values(),
                      key=lambda c: (round(c.y, 3), round(c.x, 3), c.name))

    def ordered_relations(self) -> List[Relation]:
        order = {c.id: i for i, c in enumerate(self.ordered_classes())}
        return sorted(self.relations.values(),
                      key=lambda r: (order.get(r.source, 1 << 30),
                                     order.get(r.target, 1 << 30), r.id))

    def parents_of(self, cid: str) -> List[UmlClass]:
        """Generalization/Realization ile turedigi siniflar."""
        out: List[UmlClass] = []
        for r in self.ordered_relations():
            if r.source == cid and r.kind in (RelationKind.GENERALIZATION,
                                              RelationKind.REALIZATION):
                p = self.classes.get(r.target)
                if p is not None:
                    out.append(p)
        return out

    def generalization_parent(self, cid: str) -> Optional[UmlClass]:
        """Tek kalitim varsayimiyla ilk generalization ebeveyni."""
        for r in self.ordered_relations():
            if r.source == cid and r.kind is RelationKind.GENERALIZATION:
                return self.classes.get(r.target)
        return None

    def realized_interfaces(self, cid: str) -> List[UmlClass]:
        out: List[UmlClass] = []
        for r in self.ordered_relations():
            if r.source == cid and r.kind is RelationKind.REALIZATION:
                p = self.classes.get(r.target)
                if p is not None:
                    out.append(p)
        return out

    def owned_parts(self, cid: str) -> List[Relation]:
        """Bu sinifin BUTUN oldugu aggregation/composition iliskileri."""
        return [r for r in self.ordered_relations()
                if r.source == cid and r.kind in (RelationKind.AGGREGATION,
                                                  RelationKind.COMPOSITION)]

    def associations_of(self, cid: str) -> List[Relation]:
        return [r for r in self.ordered_relations()
                if r.source == cid and r.kind is RelationKind.ASSOCIATION]

    def value_part_deps(self, cid: str) -> List[UmlClass]:
        """DEGER (by-value) olarak gomulen siniflar; tam tip tanimlari uretilen
        kodda bu siniftan ONCE gelmek zorundadir:
          * Composition parcasi (soyut/arayuz degilse deger uyesidir),
          * diyagramdaki bir sinifi gosterici olmadan tip olarak kullanan
            nitelikler.
        """
        c = self.classes.get(cid)
        if c is None:
            return []
        out: List[UmlClass] = []
        seen: set = set()
        for r in self.owned_parts(cid):
            if r.kind is RelationKind.COMPOSITION:
                p = self.classes.get(r.target)
                if p is not None and not p.is_abstract and p.id not in seen:
                    seen.add(p.id)
                    out.append(p)
        by_name = {cc.name: cc for cc in self.classes.values()}
        for a in c.attributes:
            t = a.type.strip()
            if t and not t.endswith("*"):
                p = by_name.get(t)
                if p is not None and p.id not in seen:
                    seen.add(p.id)
                    out.append(p)
        return out

    def topo_sorted(self) -> List[UmlClass]:
        """Bagimlilik sirasina gore: once temel siniflar/arayuzler ve deger
        olarak gomulen parcalar (uretilen basliklarin derlenebilmesi icin)."""
        visited: Dict[str, int] = {}
        out: List[UmlClass] = []

        def visit(c: UmlClass) -> None:
            state = visited.get(c.id, 0)
            if state == 2:
                return
            if state == 1:      # dongu - dogrulayici hata verir; yine de kir
                return
            visited[c.id] = 1
            for p in self.parents_of(c.id) + self.value_part_deps(c.id):
                visit(p)
            visited[c.id] = 2
            out.append(c)

        for c in self.ordered_classes():
            visit(c)
        return out

    # -- serilestirme -------------------------------------------------------- #

    def to_dict(self) -> dict:
        return {
            "schema": CLASS_SCHEMA_VERSION,
            "type": "class_diagram",
            "name": self.name,
            "prefix": self.prefix,
            "description": self.description,
            "user_includes": self.user_includes,
            "classes": [c.to_dict() for c in self.ordered_classes()],
            "relations": [r.to_dict() for r in self.ordered_relations()],
        }

    @staticmethod
    def from_dict(d: dict) -> "ClassModel":
        cm = ClassModel(
            name=d.get("name", "Design"),
            prefix=d.get("prefix", "design"),
            description=d.get("description", ""),
            user_includes=d.get("user_includes", ""),
        )
        for cd in d.get("classes", []):
            c = UmlClass.from_dict(cd)
            cm.classes[c.id] = c
        for rd in d.get("relations", []):
            r = Relation.from_dict(rd)
            cm.relations[r.id] = r
        return cm

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)

    @staticmethod
    def from_json(text: str) -> "ClassModel":
        return ClassModel.from_dict(json.loads(text))

    def assign_from(self, other: "ClassModel") -> None:
        """Ayni nesneyi koruyarak icerigi degistirir (undo/yukleme icin)."""
        self.name = other.name
        self.prefix = other.prefix
        self.description = other.description
        self.user_includes = other.user_includes
        self.classes = other.classes
        self.relations = other.relations
