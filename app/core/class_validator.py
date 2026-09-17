"""The class diagram validator.

Runs BEFORE any code is generated; a single ERROR stops code generation.
The codes have the form C001... (they cannot be confused with the state
"""

from __future__ import annotations

import re
from typing import Dict, List, Set

from .class_model import ClassModel, RelationKind, Stereotype
from .naming import lower_camel as _lower_camel
from .naming import snake as _snake
from .validator import C_KEYWORDS, Issue

IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MULT_RE = re.compile(r"^$|^\*$|^\d+$|^\d+\.\.(\d+|\*)$")


def _is_many(mult: str) -> bool:
    m = mult.strip()
    return m in ("*", "0..*", "1..*") or m.endswith("..*")


def _valid_ident(name: str) -> bool:
    return bool(IDENT_RE.match(name)) and name not in C_KEYWORDS


def validate_classes(cm: ClassModel) -> List[Issue]:
    issues: List[Issue] = []

    def err(code, msg, eid=None):
        issues.append(Issue("error", code, msg, eid))

    def warn(code, msg, eid=None):
        issues.append(Issue("warning", code, msg, eid))

    # -------------------------------------------------------------- general #
    if not _valid_ident(cm.prefix):
        err("C001", "Symbol prefix '%s' is not a valid C identifier." % cm.prefix)
    if not cm.classes:
        err("C002", "Diagram is empty: at least one class is required.")
        return issues

    # -------------------------------------------------------------- naming #
    seen: Dict[str, str] = {}
    seen_snake: Dict[str, str] = {}
    for c in cm.ordered_classes():
        if not _valid_ident(c.name):
            err("C010", "'%s' is not a valid class name." % c.name, c.id)
        key = c.name.upper()
        if key in seen:
            err("C011", "The class name '%s' is used more than once." % c.name, c.id)
        seen[key] = c.id
        # The C generator derives type and function names with snake(name): 'MyClass'
        # and 'my_class' produce the same 'my_class_t' and the header will not compile.
        skey = _snake(c.name)
        if skey in seen_snake and seen_snake[skey] != c.name:
            err("C037", "The class names '%s' and '%s' produce the same '%s_t' "
                        "type in the C output; rename one of them."
                % (seen_snake[skey], c.name, skey), c.id)
        seen_snake[skey] = c.name

        attr_seen: Set[str] = set()
        for a in c.attributes:
            if not _valid_ident(a.name):
                err("C012", "'%s.%s' is not a valid attribute name."
                    % (c.name, a.name), c.id)
            if a.name in attr_seen:
                err("C013", "Class '%s' has a duplicate '%s' attribute."
                    % (c.name, a.name), c.id)
            attr_seen.add(a.name)
            if not a.type.strip():
                err("C014", "The '%s.%s' attribute has no type." % (c.name, a.name), c.id)
            if not MULT_RE.match(a.multiplicity.strip()):
                warn("C015", "The '%s.%s' multiplicity '%s' is not in a standard "
                             "format (e.g. 1, 0..1, 0..*)."
                     % (c.name, a.name, a.multiplicity), c.id)

        op_seen: Set[str] = set()
        for o in c.operations:
            if not _valid_ident(o.name):
                err("C016", "'%s.%s()' is not a valid operation name."
                    % (c.name, o.name), c.id)
            if o.name in op_seen:
                err("C017", "Class '%s' defines the '%s()' operation more than "
                            "once; the function names clash in C."
                    % (c.name, o.name), c.id)
            op_seen.add(o.name)
            if c.is_interface and o.name == "self":
                err("C063", "The 'self()' operation in <<interface>> '%s' clashes "
                            "with the 'self' pointer of the generated function "
                            "table; choose another name." % c.name, c.id)
            for p in o.params:
                if not _valid_ident(p.name):
                    err("C018", "The '%s.%s()' parameter '%s' is not valid."
                        % (c.name, o.name, p.name), c.id)
            if o.abstract and not c.is_abstract and c.stereotype is Stereotype.CLASS:
                warn("C019", "'%s.%s()' is abstract but the class appears "
                             "concrete; the class will be marked abstract."
                     % (c.name, o.name), c.id)

        if c.is_interface:
            if c.attributes and any(not a.static for a in c.attributes):
                warn("C020", "<<interface>> '%s' carries an instance attribute; in "
                             "UML, interfaces define only operations/constants."
                     % c.name, c.id)

    # -------------------------------------------------------- relationships #
    gen_count: Dict[str, int] = {}
    seen_edges: Set = set()
    for r in cm.ordered_relations():
        if r.kind in (RelationKind.GENERALIZATION, RelationKind.REALIZATION):
            edge = (r.source, r.target, r.kind)
            if edge in seen_edges:
                err("C039", "The same %s relationship is drawn more than once; "
                            "delete the extra ones." % r.kind.value, r.id)
            seen_edges.add(edge)
        src = cm.classes.get(r.source)
        tgt = cm.classes.get(r.target)
        if src is None or tgt is None:
            err("C030", "One end of a relationship is undefined (not connected).", r.id)
            continue
        if r.source == r.target and r.kind in (RelationKind.GENERALIZATION,
                                               RelationKind.REALIZATION,
                                               RelationKind.COMPOSITION):
            err("C031", "'%s' cannot have a %s relationship with itself."
                % (src.name, r.kind.value), r.id)

        if r.kind is RelationKind.GENERALIZATION:
            gen_count[r.source] = gen_count.get(r.source, 0) + 1
            # UML 2.5.1: interfaces may derive from other interfaces through
            # Generalization; only CLASS -> interface inheritance is forbidden.
            if tgt.is_interface and not src.is_interface:
                err("C032", "'%s' cannot inherit from <<interface>> '%s'; use "
                            "Realization instead." % (src.name, tgt.name), r.id)
            if src.is_interface and not tgt.is_interface:
                err("C038", "<<interface>> '%s' can only be derived from another "
                            "interface ('%s' is not an interface)."
                    % (src.name, tgt.name), r.id)
        if r.kind is RelationKind.REALIZATION and not tgt.is_interface:
            # UML 2.5.1: the supplier of an InterfaceRealization has to be an Interface.
            # The generator also writes an interface as a function table; if the target
            # is not an interface, '<class>_as_<target>' does not compile.
            err("C033", "Class '%s' has the realization target '%s', which is not "
                        "an <<interface>>; use Generalization for inheritance."
                % (src.name, tgt.name), r.id)
        if r.kind is RelationKind.COMPOSITION and tgt.is_abstract:
            warn("C034", "The composition part ('%s') is abstract/an interface; the "
                         "generated code holds it by pointer/vtable instead of by "
                         "value." % tgt.name, r.id)
        for label, mult in (("source", r.source_mult), ("target", r.target_mult)):
            if not MULT_RE.match(mult.strip()):
                warn("C035", "The %s multiplicity '%s' of the relationship is not "
                             "in a standard format (e.g. 1, 0..1, 0..*)."
                     % (label, mult), r.id)

    for cid, n in gen_count.items():
        if n > 1:
            warn("C036", "'%s' inherits from more than one class; only the first is "
                         "embedded in the C output (single inheritance)."
                 % cm.classes[cid].name, cid)

    # --------------------------------------------- generated FILE SCOPE names #
    # The C generator writes file-scope symbols of the form '<snake(class)>_*'
    # for every class: the constructor, the operations, STATIC attributes and
    # the interface adapters. All share one namespace; a clash breaks the .c.
    for c in cm.ordered_classes():
        prefix = _snake(c.name)
        file_scope: Dict[str, str] = {"%s_init" % prefix: "the generated constructor"}

        def claim_symbol(symbol: str, what: str, eid: str) -> None:
            if symbol in file_scope:
                err("C060", "In class '%s', %s and %s produce the same '%s' C "
                            "symbol; rename one of them."
                    % (c.name, file_scope[symbol], what, symbol), eid)
            else:
                file_scope[symbol] = what

        for o in c.operations:
            claim_symbol("%s_%s" % (prefix, o.name),
                         "the '%s()' operation" % o.name, c.id)
        for a in c.attributes:
            if a.static:
                claim_symbol("%s_%s" % (prefix, a.name),
                             "the static '%s' attribute" % a.name, c.id)
        for iface in cm.realized_interfaces(c.id):
            claim_symbol("%s_as_%s" % (prefix, _snake(iface.name)),
                         "the '%s' interface adapter" % iface.name, c.id)

    # ------------------------------------------- generated member name clashes #
    for c in cm.ordered_classes():
        base = cm.generalization_parent(c.id)
        # Static attributes do not go into the struct (they are file-scope), so
        # only INSTANCE attributes can clash with the embedded 'base' member.
        if base is not None and not base.is_interface \
                and any(a.name == "base" and not a.static for a in c.attributes):
            err("C061", "The 'base' attribute in class '%s' clashes with the "
                        "embedded 'base' member generated for inheritance; "
                        "choose another name." % c.name, c.id)

        # The member names derived by the C and C++ generators fall into the same
        # namespace; clashing names produce code that does not compile.
        c_names: Dict[str, str] = {}
        cpp_names: Dict[str, str] = {}
        reported: Set = set()

        def _claim(table: Dict[str, str], name: str, what: str, eid: str) -> None:
            if name in table:
                key = (table[name], what, name)
                if key not in reported:
                    reported.add(key)
                    err("C062", "In class '%s', %s and %s produce the same '%s' "
                                "member; give a target role name or rename one "
                                "of them." % (c.name, table[name], what, name),
                        eid)
            else:
                table[name] = what

        for a in c.attributes:
            what = "the '%s' attribute" % a.name
            if not a.static:
                _claim(c_names, a.name, what, c.id)
                if _is_many(a.multiplicity):
                    _claim(c_names, a.name + "_count", what, c.id)
            _claim(cpp_names, a.name, what, c.id)
            if _is_many(a.multiplicity):
                _claim(cpp_names, a.name + "_count", what, c.id)

        for r in cm.owned_parts(c.id) + cm.associations_of(c.id):
            part = cm.classes.get(r.target)
            if part is None:
                continue
            what = "the '%s' relationship" % (r.target_role.strip() or part.name)
            role = r.target_role.strip()
            cname = role or _snake(part.name)
            cppname = role or _lower_camel(part.name)
            _claim(c_names, cname, what, r.id)
            _claim(cpp_names, cppname, what, r.id)
            if _is_many(r.target_mult):
                _claim(c_names, cname + "_count", what, r.id)
                _claim(cpp_names, cppname + "_count", what, r.id)

    # -------------------- signature compatibility of inherited operations ---- #
    # The generators match operations BY NAME: 'override' in C++, an interface
    # thunk in C. With a different signature the C++ 'override' fails and the C
    # thunk calls the function with the wrong arguments. In UML too, a
    def _sig(op) -> tuple:
        return (tuple(p.type.strip() for p in op.params),
                (op.return_type or "void").strip(),
                bool(op.const))

    for c in cm.ordered_classes():
        local = {o.name: o for o in c.operations}
        if not local:
            continue
        stack = [p.id for p in cm.parents_of(c.id)]
        seen_par: Set[str] = set()
        while stack:
            pid = stack.pop()
            if pid in seen_par or pid not in cm.classes:
                continue
            seen_par.add(pid)
            parent = cm.classes[pid]
            for po in parent.operations:
                mine = local.get(po.name)
                if mine is not None and _sig(mine) != _sig(po):
                    err("C065", "The signature of '%s.%s()' does not match "
                                "'%s.%s()' in the supertype; the generated code "
                                "will not compile."
                        % (c.name, mine.name, parent.name, po.name), c.id)
            stack.extend(p.id for p in cm.parents_of(pid))

    # ------------------- realizing two interfaces from one inheritance chain #
    # In C++ 'public Base, public Derived' creates a double base object and the
    # conversions become ambiguous; in C it produces a needless second vtable.
    for c in cm.ordered_classes():
        realized_ids = {i.id: i for i in cm.realized_interfaces(c.id)}
        for iface in realized_ids.values():
            stack = [p.id for p in cm.parents_of(iface.id) if p.is_interface]
            seen_anc: Set[str] = set()
            while stack:
                pid = stack.pop()
                if pid in seen_anc:
                    continue
                seen_anc.add(pid)
                if pid in realized_ids:
                    err("C064", "'%s' realizes both the '%s' interface and its "
                                "ancestor interface '%s'; connect only the "
                                "derived interface."
                        % (c.name, iface.name, realized_ids[pid].name), c.id)
                parent = cm.classes.get(pid)
                if parent is not None:
                    stack.extend(p.id for p in cm.parents_of(pid)
                                 if p.is_interface)

    # ------------------------------------------- value-member (by-value) cycles #
    v_visiting: Set[str] = set()
    v_done: Set[str] = set()

    def value_cycle(cid: str) -> bool:
        if cid in v_done:
            return False
        if cid in v_visiting:
            return True
        v_visiting.add(cid)
        for p in cm.value_part_deps(cid):
            if p.id == cid or value_cycle(p.id):
                return True
        v_visiting.discard(cid)
        v_done.add(cid)
        return False

    for c in cm.ordered_classes():
        if value_cycle(c.id):
            err("C041", "Class '%s' has a cycle in its by-value "
                        "(composition/attribute type) dependencies; make the part "
                        "a pointer (aggregation/association)." % c.name, c.id)
            break

    # ---------------------------------------------------- inheritance cycles #
    visiting: Set[str] = set()
    done: Set[str] = set()

    def has_cycle(cid: str) -> bool:
        if cid in done:
            return False
        if cid in visiting:
            return True
        visiting.add(cid)
        for p in cm.parents_of(cid):
            if has_cycle(p.id):
                return True
        visiting.discard(cid)
        done.add(cid)
        return False

    for c in cm.ordered_classes():
        if has_cycle(c.id):
            err("C040", "Class '%s' has a cycle in its inheritance chain." % c.name, c.id)
            break

    # ---------------------------------------- abstract operation realization #
    for c in cm.ordered_classes():
        if c.is_abstract or c.is_interface:
            continue
        pending = _unimplemented_ops(cm, c.id)
        for op_name, owner in pending:
            warn("C050", "'%s' does not implement the inherited operation "
                         "'%s.%s()'; the generated code will contain an empty "
                         "body." % (c.name, owner, op_name), c.id)

    order = {"error": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda i: (order.get(i.severity, 9), i.code))
    return issues


def _unimplemented_ops(cm: ClassModel, cid: str) -> List:
    """Abstract operations inherited from parents but not found locally."""
    c = cm.classes[cid]
    local = {o.name for o in c.operations}
    out = []
    seen: Set[str] = set()
    stack = [p.id for p in cm.parents_of(cid)]
    while stack:
        pid = stack.pop()
        if pid in seen or pid not in cm.classes:
            continue
        seen.add(pid)
        parent = cm.classes[pid]
        for o in parent.operations:
            if (o.abstract or parent.is_interface) and o.name not in local:
                out.append((o.name, parent.name))
        stack.extend(p.id for p in cm.parents_of(pid))
    return out
