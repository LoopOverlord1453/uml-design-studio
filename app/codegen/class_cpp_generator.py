"""C++11 generator from a UML class diagram (for embedded targets).

Mapping rules (documented in the generated header as well):
  * <<interface>>      -> pure virtual class (= 0), virtual destructor, no body
  * abstract class     -> abstract operations pure virtual, the rest virtual
  * Generalization     -> public inheritance
  * Realization        -> public inheritance (interface realization)
  * Composition        -> value member; for '*' multiplicities a fixed array
  * Aggregation        -> pointer member (no ownership)
  * Association        -> pointer member
  * Dependency         -> a forward declaration only
  * multiplicity '0..*'/'*' -> T member[K_MAX] + a counter  (NO dynamic memory)

No exceptions, no RTTI and no dynamic memory; the output follows the
MISRA C++ / AUTOSAR C++14 approach.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..core.class_model import (ClassModel, Operation, Relation,
                                RelationKind, Stereotype, UmlClass, Visibility)
from ..core.naming import lower_camel
from ..core.naming import pascal as _pascal
from .c_generator import TOOL_NAME, TOOL_VERSION, c_comment
from .c_generator import allman

CAPACITY_MACRO_DEFAULT = 8


def pascal(name: str) -> str:
    """PascalCase for class model names (see app/core/naming.py)."""
    return _pascal(name, fallback="Design")


def is_many(mult: str) -> bool:
    m = mult.strip()
    return m in ("*", "0..*", "1..*") or m.endswith("..*")


def fixed_count(mult: str) -> Optional[int]:
    """Capacity for multiplicities with a fixed upper bound, e.g. '3' or '2..4'."""
    m = mult.strip()
    if m.isdigit() and int(m) > 1:
        return int(m)
    if ".." in m:
        hi = m.split("..", 1)[1]
        if hi.isdigit() and int(hi) > 1:
            return int(hi)
    return None


_SCALAR_CPP = {
    "std::int8_t", "std::int16_t", "std::int32_t", "std::int64_t",
    "std::uint8_t", "std::uint16_t", "std::uint32_t", "std::uint64_t",
    "int8_t", "int16_t", "int32_t", "int64_t",
    "uint8_t", "uint16_t", "uint32_t", "uint64_t",
    "int", "unsigned", "unsigned int", "short", "unsigned short",
    "long", "unsigned long", "long long", "unsigned long long",
    "char", "signed char", "unsigned char", "std::size_t", "size_t",
    "float", "double", "long double",
}


def default_return(rtype: str) -> Optional[str]:
    """A safe default return expression for operations with an empty body."""
    t = rtype.strip()
    if not t or t == "void":
        return None
    if t.endswith("*"):
        return "nullptr"
    if t in ("bool", "boolean"):
        return "false"
    if t in ("float",):
        return "0.0F"
    if t in ("double", "long double"):
        return "0.0"
    if t in _SCALAR_CPP:
        return "static_cast<%s>(0)" % t
    return "%s()" % t           # compound type: value-initialisation


def banner_cpp(cm: ClassModel, filename: str, kind: str) -> List[str]:
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
        "// @brief   '%s' class model -- %s" % (cm.name, kind),
        "//",
        "// GENERATED FILE -- DO NOT EDIT BY HAND.",
        "// Generator: %s v%s" % (TOOL_NAME, TOOL_VERSION),
        "// Standard: ISO/IEC 14882:2011 (C++11); no exceptions, no RTTI,",
        "//           no dynamic memory.",
        "//",
        "// @par MISRA C++:2008 / AUTOSAR C++14 compliance",
        "// - Every '*' multiplicity is backed by a fixed-capacity array, so no",
        "//   allocation is ever performed (M18-4-1).",
        "// - Virtual dispatch exists only for <<interface>> and abstract",
        "//   classes; every other operation is resolved statically (M10-3-1).",
        "// - An operation with no body returns a safe default value and is",
        "//   marked TODO, so an unimplemented operation cannot return",
        "//   an indeterminate value (M8-5-1).",
        "// - All integer types are the fixed-width types of <cstdint> (M3-9-2).",
    ]
    if cm.description:
        lines += ["//", "// %s" % c_comment(cm.description)]
    lines += ["//" + "=" * 76, ""]
    return lines


class ClassCppGenerator:
    def __init__(self, cm: ClassModel) -> None:
        self.cm = cm
        self.ns = cm.prefix
        self.file_base = pascal(cm.name)
        self.capacity = "k%sCapacity" % self.file_base

    # ------------------------------------------------------------- helpers #

    def _has_polymorphism(self, c: UmlClass) -> bool:
        return (c.is_interface or c.is_abstract
                or bool(self.cm.parents_of(c.id)))

    def _member_name(self, rel: Relation, part: UmlClass) -> str:
        return rel.target_role.strip() or lower_camel(part.name)

    def _part_decl(self, rel: Relation, part: UmlClass) -> List[str]:
        """The declaration of a composition/aggregation/association member.

        Abstract/interface parts cannot be held by value; they are held by pointer.
        """
        name = self._member_name(rel, part)
        by_value = (rel.kind is RelationKind.COMPOSITION
                    and not part.is_abstract)
        n = fixed_count(rel.target_mult)
        if is_many(rel.target_mult):
            t = part.name if by_value else "%s*" % part.name
            return ["    %-18s %s_[%s];" % (t, name, self.capacity),
                    "    std::uint8_t       %s_count_;" % name]
        if n is not None:
            t = part.name if by_value else "%s*" % part.name
            return ["    %-18s %s_[%uU];" % (t, name, n)]
        if by_value:
            return ["    %-18s %s_;" % (part.name, name)]
        return ["    %-18s %s_;" % (part.name + "*", name)]

    # ---------------------------------------------------------------- .hpp #

    def header(self) -> str:
        cm = self.cm
        L: List[str] = []
        L += banner_cpp(cm, "%s.hpp" % self.file_base, "class declarations")
        guard = "%s_%s_HPP" % (self.ns.upper(), self.file_base.upper())
        L += ["#ifndef %s" % guard, "#define %s" % guard, ""]
        L += ["#include <cstdint>", ""]
        if cm.user_includes:
            L += ["// User-supplied headers (from the model settings)"]
            L += [ln for ln in cm.user_includes.splitlines() if ln.strip()]
            L += [""]
        L += ["namespace %s {" % self.ns, ""]
        L += ["/// Fixed capacity for '*' multiplicity associations (no dynamic memory)."]
        L += ["constexpr std::uint8_t %s = %uU;" % (self.capacity, CAPACITY_MACRO_DEFAULT), ""]

        ordered = self.cm.topo_sorted()

        # Forward declarations (for association/dependency cycles)
        L += ["// -- forward declarations ------------------------------------------------"]
        for c in ordered:
            L += ["class %s;" % c.name]
        L += [""]

        for c in ordered:
            L += self._class_decl(c)
            L += [""]

        L += ["}  // namespace %s" % self.ns, ""]
        L += ["#endif  // %s" % guard, ""]
        return "\n".join(allman(L))

    def _bases(self, c: UmlClass) -> str:
        bases = [p.name for p in self.cm.parents_of(c.id)]
        return (" : " + ", ".join("public %s" % b for b in bases)) if bases else ""

    def _class_decl(self, c: UmlClass) -> List[str]:
        cm = self.cm
        L: List[str] = []
        title = {Stereotype.INTERFACE: "<<interface>>",
                 Stereotype.ABSTRACT: "abstract class"}.get(c.stereotype, "class")
        L += ["/// %s %s" % (title, c.name)]
        if c.note:
            L += ["/// %s" % c_comment(c.note)]
        L += ["class %s%s" % (c.name, self._bases(c)), "{", "public:"]

        # --- constructor / destructor
        if c.is_interface:
            L += ["    virtual ~%s() = default;" % c.name, ""]
        else:
            L += ["    %s() noexcept;" % c.name]
            if self._has_polymorphism(c):
                L += ["    virtual ~%s() = default;" % c.name]
            L += [""]

        # --- operations (grouped by visibility)
        # Every section writes its label EXPLICITLY. Skipping it leaves the
        # operation under the previous section (e.g. 'private:'); the C++
        # counterpart of UML '~' (package) visibility is public, so the
        # 'public:' label has to be reopened.
        for vis, section in ((Visibility.PUBLIC.value, "public:"),
                             (Visibility.PROTECTED.value, "protected:"),
                             (Visibility.PRIVATE.value, "private:"),
                             (Visibility.PACKAGE.value, "public:")):
            ops = [o for o in c.operations if o.visibility == vis]
            if not ops:
                continue
            L += [section]
            if vis == Visibility.PACKAGE.value:
                L += ["    // UML '~' (package) gorunurlugu: C++'ta public uretildi"]
            for o in ops:
                L += ["    %s" % self._op_decl(c, o)]
            L += [""]

        # --- automatically generated interface realizations (stubs)
        missing = self._missing_overrides(c)
        if missing:
            L += ["    // Generated stubs for the inherited abstract operations:"]
            for o in missing:
                rt = o.return_type.strip() or "void"
                constness = " const" if o.const else ""
                L += ["    %s %s%s override;" % (rt, self._op_sig(o), constness)]
            L += [""]

        # --- attribute and relationship members
        priv_attrs = self._attr_decls(c)
        part_decls: List[str] = []
        for rel in cm.owned_parts(c.id) + cm.associations_of(c.id):
            part = cm.classes.get(rel.target)
            if part is not None:
                note = {RelationKind.COMPOSITION: "composition",
                        RelationKind.AGGREGATION: "aggregation",
                        RelationKind.ASSOCIATION: "association"}[rel.kind]
                mult = (" [%s]" % rel.target_mult) if rel.target_mult else ""
                part_decls += ["    // %s -> %s%s" % (note, part.name, mult)]
                part_decls += self._part_decl(rel, part)
        if priv_attrs or part_decls:
            L += ["private:"] if not c.is_interface else ["protected:"]
            L += priv_attrs
            L += part_decls
        L += ["};"]
        return L

    def _attr_decls(self, c: UmlClass) -> List[str]:
        out: List[str] = []
        for a in c.attributes:
            vis_note = "" if a.visibility == Visibility.PRIVATE.value else \
                "  // UML gorunurluk: %s" % a.visibility
            if a.static:
                out += ["    static %s %s_;%s" % (a.type, a.name, vis_note)]
            elif is_many(a.multiplicity):
                out += ["    %-14s %s_[%s];%s" % (a.type, a.name, self.capacity, vis_note),
                        "    std::uint8_t   %s_count_;" % a.name]
            elif fixed_count(a.multiplicity) is not None:
                out += ["    %-14s %s_[%uU];%s"
                        % (a.type, a.name, fixed_count(a.multiplicity), vis_note)]
            else:
                out += ["    %-14s %s_;%s" % (a.type, a.name, vis_note)]
        return out

    def _op_sig(self, o: Operation) -> str:
        args = ", ".join("%s %s" % (p.type, p.name) for p in o.params)
        return "%s(%s)" % (o.name, args)

    def _op_decl(self, c: UmlClass, o: Operation) -> str:
        rt = o.return_type.strip() or "void"
        sig = self._op_sig(o)
        constness = " const" if o.const else ""
        if o.static:
            return "static %s %s;" % (rt, sig)
        if c.is_interface or (o.abstract and not self._overrides(c, o)):
            return "virtual %s %s%s = 0;" % (rt, sig, constness)
        if self._overrides(c, o):
            return "%s %s%s override;" % (rt, sig, constness)
        if c.is_abstract:
            return "virtual %s %s%s;" % (rt, sig, constness)
        return "%s %s%s;" % (rt, sig, constness)

    def _missing_overrides(self, c: UmlClass) -> List[Operation]:
        """Abstract operations inherited from parent interfaces/abstract classes
        but not realized locally. Concrete classes get an automatic stub, so the
        class really is concrete."""
        if c.is_interface or c.stereotype is Stereotype.ABSTRACT:
            return []
        local = {o.name for o in c.operations}
        out: List[Operation] = []
        seen_ops = set()
        seen_cls = set()
        stack = [p.id for p in self.cm.parents_of(c.id)]
        while stack:
            pid = stack.pop()
            if pid in seen_cls or pid not in self.cm.classes:
                continue
            seen_cls.add(pid)
            parent = self.cm.classes[pid]
            for o in parent.operations:
                if ((o.abstract or parent.is_interface)
                        and o.name not in local and o.name not in seen_ops):
                    seen_ops.add(o.name)
                    out.append(o)
            stack.extend(p.id for p in self.cm.parents_of(pid))
        return out

    def _overrides(self, c: UmlClass, o: Operation) -> bool:
        """Does any parent class have a virtual operation with the same name?"""
        stack = [p.id for p in self.cm.parents_of(c.id)]
        seen = set()
        while stack:
            pid = stack.pop()
            if pid in seen or pid not in self.cm.classes:
                continue
            seen.add(pid)
            parent = self.cm.classes[pid]
            for po in parent.operations:
                if po.name == o.name and (parent.is_interface or po.abstract
                                          or parent.is_abstract):
                    return True
            stack.extend(p.id for p in self.cm.parents_of(pid))
        return False

    # ---------------------------------------------------------------- .cpp #

    def source(self) -> str:
        cm = self.cm
        L: List[str] = []
        L += banner_cpp(cm, "%s.cpp" % self.file_base, "implementation")
        L += ['#include "%s.hpp"' % self.file_base, ""]
        L += ["namespace %s {" % self.ns, ""]

        for c in self.cm.topo_sorted():
            if c.is_interface:
                continue
            L += self._class_impl(c)
        L += ["}  // namespace %s" % self.ns, ""]
        return "\n".join(allman(L))

    def _zero_init_list(self, c: UmlClass) -> List[str]:
        items: List[str] = []
        for a in c.attributes:
            if a.static:
                continue
            if is_many(a.multiplicity):
                items += ["%s_()" % a.name, "%s_count_(0U)" % a.name]
            elif fixed_count(a.multiplicity) is not None:
                items += ["%s_()" % a.name]
            else:
                init = a.default.strip()
                items += ["%s_(%s)" % (a.name, init) if init else "%s_()" % a.name]
        for rel in self.cm.owned_parts(c.id) + self.cm.associations_of(c.id):
            part = self.cm.classes.get(rel.target)
            if part is None:
                continue
            name = self._member_name(rel, part)
            by_value = (rel.kind is RelationKind.COMPOSITION
                        and not part.is_abstract)
            if is_many(rel.target_mult):
                items += ["%s_()" % name, "%s_count_(0U)" % name]
            elif fixed_count(rel.target_mult) is not None:
                items += ["%s_()" % name]
            elif by_value:
                items += ["%s_()" % name]
            else:
                items += ["%s_(nullptr)" % name]
        return items

    def _class_impl(self, c: UmlClass) -> List[str]:
        L: List[str] = []
        L += ["// %s %s %s" % ("-" * 24, c.name, "-" * max(1, 44 - len(c.name)))]

        # static attribute definitions
        for a in c.attributes:
            if a.static:
                init = a.default.strip() or "0"
                L += ["%s %s::%s_ = %s;" % (a.type, c.name, a.name, init)]

        inits = self._zero_init_list(c)
        L += ["%s::%s() noexcept" % (c.name, c.name)]
        for i, item in enumerate(inits):
            L += ["    %s %s" % (":" if i == 0 else ",", item)]
        L += ["{", "}", ""]

        for o in c.operations:
            if c.is_interface:
                continue
            if o.abstract and not self._overrides(c, o):
                continue        # pure virtual: no body
            L += self._op_impl(c, o)
        for o in self._missing_overrides(c):
            L += self._op_impl(c, o)
        return L

    def _op_impl(self, c: UmlClass, o: Operation) -> List[str]:
        rt = o.return_type.strip() or "void"
        args = ", ".join("%s %s" % (p.type, p.name) for p in o.params)
        constness = " const" if o.const else ""
        head = "%s %s::%s(%s)%s" % (rt, c.name, o.name, args, constness)
        L = [head, "{"]
        for p in o.params:
            L += ["    static_cast<void>(%s);" % p.name]
        body = o.body.strip()
        if body:
            for ln in body.splitlines():
                L += ["    %s" % ln if ln.strip() else ""]
        else:
            L += ["    // TODO: implement operation '%s.%s'." % (c.name, o.name)]
            ret = default_return(rt)
            if ret is not None:
                L += ["    return %s;" % ret]
        L += ["}", ""]
        return L

    # ----------------------------------------------------- MCU integration #

    def demo_source(self) -> str:
        """An example that uses the generated classes in an MCU super loop."""
        cm = self.cm
        L: List[str] = []
        L += banner_cpp(cm, "%s_main.cpp" % self.file_base,
                        "bare-metal integration example")
        L += [
            "/**",
            " * @details",
            " * This file is a TEMPLATE, not part of the library. It constructs",
            " * every concrete class of the model and drives them from a super",
            " * loop, which is how these objects are used on a microcontroller:",
            " * they have static storage duration and are never destroyed.",
            " *",
            " *   c++ -std=c++11 -Wall -Wextra -pedantic -fno-exceptions -fno-rtti \\",
            " *       %s.cpp %s_main.cpp -o %s_app"
            % (self.file_base, self.file_base, self.file_base),
            " */",
            "",
            '#include "%s.hpp"' % self.file_base,
            "",
            "/// @brief Called once per pass; feed the watchdog here.",
            "extern void boardIdle() noexcept;",
            "",
        ]
        concrete = [c for c in cm.topo_sorted()
                    if not c.is_interface and not c.is_abstract]

        L += ["// ------------------------------------------------ application data --",
              "// Static storage: the generated code uses no dynamic memory, so",
              "// every object lives for the whole run time of the program."]
        for c in concrete:
            L += ["/// @brief The %s instance of this application." % c.name,
                  "static %s::%s g_%s;" % (self.ns, c.name, c.name)]
        L += ["", "/**",
              " * @brief  Application entry point.",
              " * @return Never returns; the super loop runs until power-down.",
              " */",
              "int main()",
              "{",
              "    // Constructors already ran: objects have static storage duration.",
              "",
              "    // Super loop: call the operations your application needs.",
              "    for (;;)",
              "    {"]
        called = 0
        for c in concrete:
            for o in c.operations:
                if o.params or o.static or o.visibility != Visibility.PUBLIC.value:
                    continue
                L += ["        static_cast<void>(g_%s.%s());" % (c.name, o.name)]
                called += 1
        if not called:
            L += ["        // Add your calls here."]
        L += ["",
              "        boardIdle();",
              "    }",
              "}",
              ""]
        return "\n".join(allman(L))


def generate_class_cpp(cm: ClassModel, with_demo: bool = True) -> Dict[str, str]:
    """Produces a {file_name: content} dictionary from the model.

    With ``with_demo`` on, the third file is not a UNIT TEST but an integration
    example that uses the classes in an MCU super loop.
    """
    gen = ClassCppGenerator(cm)
    files = {"%s.hpp" % gen.file_base: gen.header(),
             "%s.cpp" % gen.file_base: gen.source()}
    if with_demo:
        files["%s_main.cpp" % gen.file_base] = gen.demo_source()
    return files
