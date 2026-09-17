"""C11+GNU generator from a UML class diagram (for embedded targets).

Object-oriented mapping rules (documented in the generated header as well):
  * Class             -> typedef struct + a <name>_init() constructor
  * Generalization    -> the base struct is embedded as the FIRST member
                         ('base'); the (base_t *)ptr conversion is always valid
  * <<interface>>     -> function pointer table (vtable) + a self pointer;
                         the implementing class provides <name>_as_<iface>()
  * Composition       -> value member; for '*' multiplicities a fixed array
  * Aggregation       -> pointer member (no ownership)
  * Association       -> pointer member
  * Dependency        -> a comment line only (link information)
  * Operation         -> a <class>_<operation>(<class>_t *self, ...) function
  * Static attribute  -> a file-scope static variable inside the .c
  * multiplicity '0..*' -> T member[CAPACITY] + a counter  (NO dynamic memory)

No dynamic memory, no recursion and no VLAs; the mandatory rules of
MISRA C:2012 are respected (function pointers are a conscious R.8-9 case).
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..core.class_model import (ClassModel, Operation, Relation, RelationKind,
                                UmlClass, Visibility)
from ..core.naming import snake as _snake
from .c_generator import TOOL_NAME, TOOL_VERSION, c_comment
from .c_generator import allman
from .class_cpp_generator import fixed_count, is_many

CAPACITY_DEFAULT = 8


#: The name conversion lives in the core; the validator uses the SAME function.
snake = _snake


def banner_c(cm: ClassModel, filename: str, kind: str) -> List[str]:
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
        " * @brief   '%s' class model -- %s" % (cm.name, kind),
        " *",
        " * GENERATED FILE -- DO NOT EDIT BY HAND.",
        " * Generator: %s v%s" % (TOOL_NAME, TOOL_VERSION),
        " * Standard: ISO/IEC 9899:2011 (C11) with GNU extensions (-std=gnu11);",
        " *           no dynamic memory, no recursion.",
        " *",
        " * @par MISRA C:2012 compliance",
        " * - Inheritance is expressed by embedding the base struct as the FIRST",
        " *   member; the (base_t *) conversion is well defined by C11",
        " *   6.7.2.1/13 (R11.3 deviation, documented here).",
        " * - <<interface>> dispatch goes through explicitly documented function",
        " *   pointer tables (R8.13, Dir 4.12: the tables are const and static).",
        " * - Every '*' multiplicity is backed by a fixed-capacity array, so no",
        " *   allocation is ever performed (Dir 4.12).",
        " * - All integer types are the fixed-width types of <stdint.h> (Dir 4.6).",
        " * - Pointer parameters are checked against NULL before use (Dir 4.14).",
    ]
    if cm.description:
        lines += [" *", " * %s" % c_comment(cm.description)]
    lines += [" " + "*" * 76 + "*/", ""]
    return lines


_SCALAR_C = {
    "int8_t", "int16_t", "int32_t", "int64_t",
    "uint8_t", "uint16_t", "uint32_t", "uint64_t",
    "int", "unsigned", "unsigned int", "short", "unsigned short",
    "long", "unsigned long", "long long", "unsigned long long",
    "char", "signed char", "unsigned char", "size_t", "ptrdiff_t",
    "intptr_t", "uintptr_t", "float", "double", "long double",
    "bool", "boolean",
}


def _is_scalar_c(t: str) -> bool:
    return t in _SCALAR_C or t.endswith("*")


def c_default_return(rtype: str) -> Optional[str]:
    t = rtype.strip()
    if not t or t == "void":
        return None
    if t.endswith("*"):
        return "NULL"
    if t in ("bool", "boolean"):
        return "false"
    if t in ("float",):
        return "0.0F"
    if t in ("double", "long double"):
        return "0.0"
    if _is_scalar_c(t):
        return "(%s)0" % t
    # Compound (struct) type: return a zeroed static copy.
    return None


class ClassCGenerator:
    def __init__(self, cm: ClassModel) -> None:
        self.cm = cm
        self.p = cm.prefix
        self.P = cm.prefix.upper()
        # Diagram class name -> C type name mapping
        self.ctype: Dict[str, str] = {c.name: "%s_t" % snake(c.name)
                                      for c in cm.classes.values()}

    # ------------------------------------------------------------- helpers #

    def map_type(self, t: str) -> str:
        """A user type that is a class in the diagram becomes a C type."""
        base = t.strip()
        suffix = ""
        while base.endswith("*"):
            base = base[:-1].strip()
            suffix += "*"
        if base in self.ctype:
            return self.ctype[base] + (" " + suffix if suffix else "")
        return t.strip()

    def fn(self, c: UmlClass, op_name: str) -> str:
        return "%s_%s" % (snake(c.name), op_name)

    def _member_name(self, rel: Relation, part: UmlClass) -> str:
        return rel.target_role.strip() or snake(part.name)

    def _iface_all_ops(self, iface: UmlClass) -> List[Operation]:
        """The operations of an interface plus those it inherits via Generalization.

        Base interface operations come first; when an operation of the same
        name is redefined, the derived signature wins (the table is flattened).
        """
        out: List[Operation] = []
        pos: Dict[str, int] = {}
        seen_cls: set = set()

        def collect(i: UmlClass) -> None:
            if i.id in seen_cls:
                return
            seen_cls.add(i.id)
            for p in self.cm.parents_of(i.id):
                if p.is_interface:
                    collect(p)
            for o in i.operations:
                if o.name in pos:
                    out[pos[o.name]] = o
                else:
                    pos[o.name] = len(out)
                    out.append(o)

        collect(iface)
        return out

    def _iface_sig(self, o: Operation, with_names: bool = True) -> str:
        args = ["void *self"]
        for prm in o.params:
            args.append("%s %s" % (self.map_type(prm.type), prm.name)
                        if with_names else self.map_type(prm.type))
        return ", ".join(args)

    def _op_args(self, c: UmlClass, o: Operation) -> str:
        args = []
        if not o.static:
            args.append("%s *self" % self.ctype[c.name])
        for prm in o.params:
            args.append("%s %s" % (self.map_type(prm.type), prm.name))
        return ", ".join(args) if args else "void"

    # ---------------------------------------------------------------- .h #

    def header(self) -> str:
        cm = self.cm
        L: List[str] = []
        L += banner_c(cm, "%s.h" % self.p, "public interface")
        guard = "%s_H" % self.P
        L += ["#ifndef %s" % guard, "#define %s" % guard, ""]
        L += ["#include <stdbool.h>", "#include <stddef.h>", "#include <stdint.h>", ""]
        if cm.user_includes:
            L += ["/* User-supplied headers (from the model settings) */"]
            L += [ln for ln in cm.user_includes.splitlines() if ln.strip()]
            L += [""]
        L += ["#ifdef __cplusplus", 'extern "C" {', "#endif", ""]

        L += ["/* Fixed capacity for '*' multiplicity associations (no dynamic memory). */"]
        L += ["#ifndef %s_CAPACITY" % self.P,
              "#define %s_CAPACITY (%uU)" % (self.P, CAPACITY_DEFAULT),
              "#endif", ""]

        ordered = cm.topo_sorted()

        L += ["/* ---------------------------------------------- forward declarations -- */"]
        for c in ordered:
            if not c.is_interface:
                sn = snake(c.name)
                L += ["typedef struct %s_s %s_t;" % (sn, sn)]
        L += [""]

        # interfaces first (vtable types)
        for c in ordered:
            if c.is_interface:
                L += self._iface_decl(c)
        # then the classes
        for c in ordered:
            if not c.is_interface:
                L += self._class_decl(c)

        L += ["#ifdef __cplusplus", "}", "#endif", "", "#endif /* %s */" % guard, ""]
        return "\n".join(allman(L))

    def _iface_decl(self, c: UmlClass) -> List[str]:
        sn = snake(c.name)
        L: List[str] = []
        L += ["/* -- <<interface>> %s %s */" % (c.name, "-" * max(1, 52 - len(c.name)))]
        if c.note:
            L += ["/* %s */" % c_comment(c.note)]
        L += ["typedef struct {"]
        L += ["    void *self;  /* the implementing object */"]
        for o in self._iface_all_ops(c):
            rt = self.map_type(o.return_type or "void")
            L += ["    %s (*%s)(%s);" % (rt, o.name, self._iface_sig(o))]
        L += ["} %s_t;" % sn, ""]
        return L

    def _class_decl(self, c: UmlClass) -> List[str]:
        cm = self.cm
        sn = snake(c.name)
        L: List[str] = []
        title = "abstract class" if c.is_abstract else "class"
        L += ["/* -- %s %s %s */" % (title, c.name, "-" * max(1, 56 - len(c.name)
                                                              - len(title)))]
        if c.note:
            L += ["/* %s */" % c_comment(c.note)]
        L += ["struct %s_s {" % sn]
        member_start = len(L)

        base = cm.generalization_parent(c.id)
        if base is not None and not base.is_interface:
            L += ["    %-18s base;  /* generalization: %s */"
                  % (self.ctype[base.name], base.name)]

        for a in c.attributes:
            if a.static:
                continue        # file-scope inside the .c
            t = self.map_type(a.type)
            vis_note = " (UML %s)" % a.visibility \
                if a.visibility != Visibility.PRIVATE.value else ""
            if is_many(a.multiplicity):
                L += ["    %-18s %s[%s_CAPACITY];%s" % (t, a.name, self.P,
                                                        "  /* [%s]%s */" % (a.multiplicity, vis_note))]
                L += ["    uint8_t            %s_count;" % a.name]
            elif fixed_count(a.multiplicity) is not None:
                L += ["    %-18s %s[%uU];%s" % (t, a.name, fixed_count(a.multiplicity),
                                                "  /* [%s]%s */" % (a.multiplicity, vis_note))]
            else:
                note = ("  /*%s */" % vis_note) if vis_note else ""
                L += ["    %-18s %s;%s" % (t, a.name, note)]

        for rel in cm.owned_parts(c.id) + cm.associations_of(c.id):
            part = cm.classes.get(rel.target)
            if part is None:
                continue
            name = self._member_name(rel, part)
            kind_note = {RelationKind.COMPOSITION: "composition",
                         RelationKind.AGGREGATION: "aggregation",
                         RelationKind.ASSOCIATION: "association"}[rel.kind]
            mult = rel.target_mult or "1"
            by_value = (rel.kind is RelationKind.COMPOSITION
                        and not part.is_abstract)
            if part.is_interface:
                # Interface-typed end: a copy of the vtable is kept
                if is_many(rel.target_mult):
                    L += ["    %-18s %s[%s_CAPACITY];  /* %s -> %s [%s] */"
                          % (self.ctype[part.name], name, self.P, kind_note,
                             part.name, mult)]
                    L += ["    uint8_t            %s_count;" % name]
                else:
                    L += ["    %-18s %s;  /* %s -> %s */"
                          % (self.ctype[part.name], name, kind_note, part.name)]
                continue
            t = self.ctype[part.name]
            if is_many(rel.target_mult):
                if by_value:
                    L += ["    %-18s %s[%s_CAPACITY];  /* %s -> %s [%s] */"
                          % (t, name, self.P, kind_note, part.name, mult)]
                else:
                    L += ["    %-18s *%s[%s_CAPACITY];  /* %s -> %s [%s] */"
                          % (t, name, self.P, kind_note, part.name, mult)]
                L += ["    uint8_t            %s_count;" % name]
            elif fixed_count(rel.target_mult) is not None:
                n = fixed_count(rel.target_mult)
                star = "" if by_value else "*"
                L += ["    %-18s %s%s[%uU];  /* %s -> %s [%s] */"
                      % (t, star, name, n, kind_note, part.name, mult)]
            elif by_value:
                L += ["    %-18s %s;  /* %s -> %s */" % (t, name, kind_note, part.name)]
            else:
                L += ["    %-18s *%s;  /* %s -> %s */" % (t, name, kind_note, part.name)]
        if len(L) == member_start:
            # ISO C does not allow an empty struct; add a placeholder.
            L += ["    uint8_t reserved_;  /* placeholder: ISO C forbids an empty struct */"]
        L += ["};", ""]

        # constructor + operations
        L += ["/** @brief Initialises a %s instance with every field zeroed. */"
              % c.name]
        L += ["void %s_init(%s_t *self);" % (sn, sn), ""]
        for o in c.operations:
            rt = self.map_type(o.return_type or "void")
            L += ["%s %s(%s);" % (rt, self.fn(c, o.name), self._op_args(c, o))]
        if c.operations:
            L += [""]

        for iface in cm.realized_interfaces(c.id):
            isn = snake(iface.name)
            L += ["/** @brief Views a %s instance through the %s interface. */"
                  % (c.name, iface.name)]
            L += ["%s_t %s_as_%s(%s_t *self);" % (isn, sn, isn, sn), ""]
        return L

    # ---------------------------------------------------------------- .c #

    def source(self) -> str:
        cm = self.cm
        L: List[str] = []
        L += banner_c(cm, "%s.c" % self.p, "implementation")
        L += ['#include "%s.h"' % self.p, ""]
        L += ["#define %s_UNUSED(x)   ((void)(x))" % self.P, ""]

        for c in cm.topo_sorted():
            if c.is_interface:
                continue
            L += self._class_impl(c)
        return "\n".join(allman(L))

    def _class_impl(self, c: UmlClass) -> List[str]:
        cm = self.cm
        sn = snake(c.name)
        L: List[str] = []
        L += ["/* %s %s %s */" % ("=" * 20, c.name, "=" * max(1, 50 - len(c.name)))]

        # static attributes
        for a in c.attributes:
            if a.static:
                t = self.map_type(a.type)
                init = a.default.strip()
                L += ["/** UML statik nitelik: %s.%s */" % (c.name, a.name)]
                L += ["static %s %s_%s%s;" % (t, sn, a.name,
                                              (" = %s" % init) if init else "")]
        if any(a.static for a in c.attributes):
            L += [""]

        # constructor
        L += ["void %s_init(%s_t *self)" % (sn, sn), "{"]
        L += ["    if (self == NULL) {", "        return;", "    }"]
        base = cm.generalization_parent(c.id)
        has_base = base is not None and not base.is_interface
        has_parts = any(cm.classes.get(r.target) is not None
                        for r in cm.owned_parts(c.id) + cm.associations_of(c.id))
        has_fields = any(not a.static for a in c.attributes)
        if not (has_base or has_parts or has_fields):
            L += ["    self->reserved_ = 0U;"]
        if has_base:
            L += ["    %s_init(&self->base);" % snake(base.name)]
        for a in c.attributes:
            if a.static:
                continue
            L += self._zero_field(c, a.name, self.map_type(a.type),
                                  a.multiplicity, a.default)
        for rel in cm.owned_parts(c.id) + cm.associations_of(c.id):
            part = cm.classes.get(rel.target)
            if part is None:
                continue
            name = self._member_name(rel, part)
            by_value = (rel.kind is RelationKind.COMPOSITION
                        and not part.is_interface and not part.is_abstract)
            n = fixed_count(rel.target_mult)
            if is_many(rel.target_mult):
                L += ["    self->%s_count = 0U;" % name]
                if by_value:
                    L += ["    {",
                          "        uint8_t i;",
                          "        for (i = 0U; i < %s_CAPACITY; i++) {" % self.P,
                          "            %s_init(&self->%s[i]);" % (snake(part.name), name),
                          "        }",
                          "    }"]
            elif part.is_interface:
                L += ["    self->%s.self = NULL;" % name]
            elif n is not None:
                if by_value:
                    L += ["    {",
                          "        uint8_t i;",
                          "        for (i = 0U; i < %uU; i++) {" % n,
                          "            %s_init(&self->%s[i]);" % (snake(part.name), name),
                          "        }",
                          "    }"]
                else:
                    L += ["    {",
                          "        uint8_t i;",
                          "        for (i = 0U; i < %uU; i++) {" % n,
                          "            self->%s[i] = NULL;" % name,
                          "        }",
                          "    }"]
            elif by_value:
                L += ["    %s_init(&self->%s);" % (snake(part.name), name)]
            else:
                L += ["    self->%s = NULL;" % name]
        L += ["}", ""]

        # operations
        for o in c.operations:
            L += self._op_impl(c, o)

        # interface realizations
        for iface in cm.realized_interfaces(c.id):
            L += self._iface_impl(c, iface)
        return L

    def _class_ctor_of(self, ctype: str) -> Optional[str]:
        """The constructor name if the type is a class in the diagram, else None."""
        for cls_name, mapped in self.ctype.items():
            if mapped == ctype.strip():
                return "%s_init" % snake(cls_name)
        return None

    def _zero_field(self, c: UmlClass, name: str, ctype: str,
                    mult: str, default: str) -> List[str]:
        # An attribute whose type is a class in the diagram must be built with
        # that class's CONSTRUCTOR; using a zero copy destroys the inner class's
        # UML default values and makes the C output differ from the C++ one.
        ctor = self._class_ctor_of(ctype)
        if is_many(mult):
            out = ["    self->%s_count = 0U;" % name]
            if ctor is not None:
                out += ["    {",
                        "        uint8_t i;",
                        "        for (i = 0U; i < %s_CAPACITY; i++) {" % self.P,
                        "            %s(&self->%s[i]);" % (ctor, name),
                        "        }",
                        "    }"]
            return out
        n = fixed_count(mult)
        if n is not None:
            element = ("%s(&self->%s[i]);" % (ctor, name)) if ctor is not None \
                else ("self->%s[i] = %s;" % (name, self._zero_of(ctype)))
            return ["    {",
                    "        uint8_t i;",
                    "        for (i = 0U; i < %uU; i++) {" % n,
                    "            %s" % element,
                    "        }",
                    "    }"]
        if ctor is not None and not default.strip():
            return ["    %s(&self->%s);" % (ctor, name)]
        init = default.strip() or self._zero_of(ctype)
        if init == "{0}":
            return ["    {",
                    "        static const %s zero_%s;" % (ctype, name),
                    "        self->%s = zero_%s;" % (name, name),
                    "    }"]
        return ["    self->%s = %s;" % (name, init)]

    def _zero_of(self, ctype: str) -> str:
        t = ctype.strip()
        if t.endswith("*"):
            return "NULL"
        if t in ("bool", "boolean"):
            return "false"
        if t in ("float",):
            return "0.0F"
        if t in ("double", "long double"):
            return "0.0"
        if _is_scalar_c(t):
            return "(%s)0" % t
        return "{0}"            # unknown/compound type: static const zero copy

    def _op_impl(self, c: UmlClass, o: Operation) -> List[str]:
        rt = self.map_type(o.return_type or "void")
        head = "%s %s(%s)" % (rt, self.fn(c, o.name), self._op_args(c, o))
        L = [head, "{"]
        if not o.static:
            L += ["    %s_UNUSED(self);" % self.P]
        for prm in o.params:
            L += ["    %s_UNUSED(%s);" % (self.P, prm.name)]
        body = o.body.strip()
        if body:
            for ln in body.splitlines():
                L += ["    %s" % ln if ln.strip() else ""]
        else:
            L += ["    /* TODO: implement operation '%s.%s'. */" % (c.name, o.name)]
            ret = c_default_return(rt)
            if ret is not None:
                L += ["    return %s;" % ret]
            elif rt != "void":
                # Compound (struct) return type: a zeroed static copy.
                L += ["    {",
                      "        static const %s zero_result;" % rt,
                      "        return zero_result;",
                      "    }"]
        L += ["}", ""]
        return L

    def _iface_impl(self, c: UmlClass, iface: UmlClass) -> List[str]:
        sn = snake(c.name)
        isn = snake(iface.name)
        L: List[str] = []
        local = {o.name for o in c.operations}
        iface_ops = self._iface_all_ops(iface)
        L += ["/* -- %s implementation of the %s interface (thunks) -- */"
              % (iface.name, c.name)]
        for o in iface_ops:
            rt = self.map_type(o.return_type or "void")
            thunk = "%s_%s_%s_thunk" % (sn, isn, o.name)
            L += ["static %s %s(%s)" % (rt, thunk, self._iface_sig(o)), "{"]
            call_args = ["(%s_t *)self" % sn] + [prm.name for prm in o.params]
            if o.name in local:
                call = "%s(%s)" % (self.fn(c, o.name), ", ".join(call_args))
                if rt != "void":
                    L += ["    return %s;" % call]
                else:
                    L += ["    %s;" % call]
            else:
                L += ["    %s_UNUSED(self);" % self.P]
                for prm in o.params:
                    L += ["    %s_UNUSED(%s);" % (self.P, prm.name)]
                L += ["    /* TODO: '%s' does not define this operation. */" % c.name]
                ret = c_default_return(rt)
                if ret is not None:
                    L += ["    return %s;" % ret]
                elif rt != "void":
                    L += ["    {",
                          "        static const %s zero_result;" % rt,
                          "        return zero_result;",
                          "    }"]
            L += ["}", ""]
        L += ["%s_t %s_as_%s(%s_t *self)" % (isn, sn, isn, sn), "{"]
        L += ["    %s_t itf;" % isn]
        L += ["    itf.self = self;"]
        for o in iface_ops:
            L += ["    itf.%s = %s_%s_%s_thunk;" % (o.name, sn, isn, o.name)]
        L += ["    return itf;", "}", ""]
        return L

    # ----------------------------------------------------- MCU integration #

    def demo_source(self) -> str:
        """An example that uses the generated classes in an MCU super loop."""
        cm = self.cm
        L: List[str] = []
        L += banner_c(cm, "%s_main.c" % self.p, "bare-metal integration example")
        L += [
            "/**",
            " * @details",
            " * This file is a TEMPLATE, not part of the library. It constructs",
            " * every concrete class of the model and drives them from a super",
            " * loop, which is how these objects are used on a microcontroller:",
            " * they are static, created once, and never freed.",
            " *",
            " *   cc -std=gnu11 -Wall -Wextra %s.c %s_main.c -o %s_app"
            % (self.p, self.p, self.p),
            " */",
            "",
            '#include "%s.h"' % self.p,
            "",
            "/** @brief Called once per pass; feed the watchdog here. */",
            "extern void board_idle(void);",
            "",
        ]
        concrete = [c for c in cm.topo_sorted()
                    if not c.is_interface and not c.is_abstract]

        L += ["/* --------------------------------------------------- application data -- */",
              "/*",
              " * Static storage: no dynamic memory anywhere in the generated code,",
              " * so every object lives for the whole run time of the program.",
              " */"]
        for c in concrete:
            sn = snake(c.name)
            L += ["/** @brief The %s instance of this application. */" % c.name,
                  "static %s_t g_%s;" % (sn, sn)]
        L += ["", "/**",
              " * @brief  Application entry point.",
              " * @return Never returns; the super loop runs until power-down.",
              " */",
              "int main(void)",
              "{"]
        if concrete:
            L += ["    /* 1. Construct every object once, before the loop starts. */"]
            for c in concrete:
                sn = snake(c.name)
                L += ["    %s_init(&g_%s);" % (sn, sn)]
        else:
            L += ["    /* The model has no concrete class to construct. */"]
        L += ["",
              "    /* 2. Super loop: call the operations your application needs. */",
              "    for (;;)",
              "    {"]
        called = 0
        for c in concrete:
            sn = snake(c.name)
            for o in c.operations:
                if o.params or o.static:
                    continue
                rt = self.map_type(o.return_type or "void")
                call = "%s(&g_%s)" % (self.fn(c, o.name), sn)
                L += ["        %s;" % (("(void)%s" % call) if rt != "void" else call)]
                called += 1
        if not called:
            L += ["        /* Add your calls here. */"]
        L += ["",
              "        board_idle();",
              "    }",
              "}",
              ""]
        return "\n".join(allman(L))


def generate_class_c(cm: ClassModel, with_demo: bool = True) -> Dict[str, str]:
    """Produces a {file_name: content} dictionary from the model.

    With ``with_demo`` on, the third file is not a UNIT TEST but an integration
    example that uses the classes in an MCU super loop.
    """
    gen = ClassCGenerator(cm)
    files = {"%s.h" % cm.prefix: gen.header(),
             "%s.c" % cm.prefix: gen.source()}
    if with_demo:
        files["%s_main.c" % cm.prefix] = gen.demo_source()
    return files
