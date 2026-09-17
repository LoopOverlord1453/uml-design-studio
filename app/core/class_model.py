"""Data model: the UML class diagram.

This module holds *data* only; it does not depend on Qt.

Semantic basis: UML 2.5.1, clause 9 (Classification) and clause 11
(StructuredClassifiers). Supported elements:
  * Class / Abstract Class / <<interface>>
  * Attribute (visibility, type, default value, static, multiplicity)
  * Operation (visibility, parameters, return type, abstract, static, const)
  * Association / Aggregation / Composition / Generalization /
    Realization / Dependency (with end multiplicities and role names)
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
    """The UML stereotypes of a class."""

    CLASS = "class"
    ABSTRACT = "abstract"      # italic name
    INTERFACE = "interface"    # <<interface>>


class Visibility(str, Enum):
    PUBLIC = "+"
    PRIVATE = "-"
    PROTECTED = "#"
    PACKAGE = "~"


class RelationKind(str, Enum):
    """UML relationship kinds (with the marker at the drawing end)."""

    ASSOCIATION = "association"        # plain line + open arrow
    AGGREGATION = "aggregation"        # hollow diamond (shared whole-part)
    COMPOSITION = "composition"        # filled diamond (owning whole-part)
    GENERALIZATION = "generalization"  # hollow triangle (inheritance)
    REALIZATION = "realization"        # dashed line + hollow triangle
    DEPENDENCY = "dependency"          # dashed line + open arrow


@dataclass
class Attribute:
    """A class attribute:  visibility name : type = default"""

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
    """A class operation:  visibility name(parameters) : return"""

    name: str = "operation"
    return_type: str = "void"
    visibility: str = Visibility.PUBLIC.value
    params: List[Parameter] = field(default_factory=list)
    static: bool = False
    abstract: bool = False      # C++: pure virtual; C: a vtable entry
    const: bool = False
    body: str = ""              # optional body (C/C++ statements)

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
    """A relationship between two classes. The source -> target direction:
       Generalization/Realization : source derives from target.
       Aggregation/Composition    : source is the WHOLE, target the PART.
       Association/Dependency     : source uses target."""

    id: str = field(default_factory=lambda: new_id("r"))
    source: str = ""
    target: str = ""
    kind: RelationKind = RelationKind.ASSOCIATION
    label: str = ""
    source_mult: str = ""       # multiplicity at the source end (e.g. "1")
    target_mult: str = ""       # multiplicity at the target end (e.g. "0..*")
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
    """The document representing the whole class diagram."""

    name: str = "Design"
    prefix: str = "design"      # C symbol prefix / C++ namespace
    description: str = ""
    user_includes: str = ""
    classes: Dict[str, UmlClass] = field(default_factory=dict)
    relations: Dict[str, Relation] = field(default_factory=dict)

    # -- accessors # -------------------------------------------------------- #

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
        """The classes it derives from via Generalization/Realization."""
        out: List[UmlClass] = []
        for r in self.ordered_relations():
            if r.source == cid and r.kind in (RelationKind.GENERALIZATION,
                                              RelationKind.REALIZATION):
                p = self.classes.get(r.target)
                if p is not None:
                    out.append(p)
        return out

    def generalization_parent(self, cid: str) -> Optional[UmlClass]:
        """The first generalization parent, assuming single inheritance."""
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
        """The aggregation/composition relationships where this class is WHOLE."""
        return [r for r in self.ordered_relations()
                if r.source == cid and r.kind in (RelationKind.AGGREGATION,
                                                  RelationKind.COMPOSITION)]

    def associations_of(self, cid: str) -> List[Relation]:
        return [r for r in self.ordered_relations()
                if r.source == cid and r.kind is RelationKind.ASSOCIATION]

    def value_part_deps(self, cid: str) -> List[UmlClass]:
        """Classes embedded BY VALUE; their full type definitions must come
        BEFORE this class in the generated code:
          * a composition part (a value member unless abstract/interface),
          * attributes that use a class from the diagram as a type without
            going through a pointer.
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
        """In dependency order: base classes/interfaces and the parts embedded
        by value first (so the generated headers compile)."""
        visited: Dict[str, int] = {}
        out: List[UmlClass] = []

        def visit(c: UmlClass) -> None:
            state = visited.get(c.id, 0)
            if state == 2:
                return
            if state == 1:      # a cycle - the validator reports it; break anyway
                return
            visited[c.id] = 1
            for p in self.parents_of(c.id) + self.value_part_deps(c.id):
                visit(p)
            visited[c.id] = 2
            out.append(c)

        for c in self.ordered_classes():
            visit(c)
        return out

    # -- serialisation # ----------------------------------------------------- #

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
        """Replaces the content while keeping the same object (undo/load)."""
        self.name = other.name
        self.prefix = other.prefix
        self.description = other.description
        self.user_includes = other.user_includes
        self.classes = other.classes
        self.relations = other.relations
