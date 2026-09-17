"""Copy / paste core for state machine fragments -- no Qt.

The canvas only supplies "what to copy" and "where to paste"; id generation,
name de-duplication and transition re-binding happen HERE, so the behaviour
can be tested without a user interface.

COPY RULES
----------
1. **The subtree comes with it.** Copying a composite state copies everything
   inside it; otherwise the pasted copy would be an empty shell.
2. **Only INTERNAL transitions are copied.** A transition with both ends in
   the selection is copied; one that leaves the selection has no target in
   the copy, and copying it would leave a broken transition in the model.
3. **Ids are REGENERATED.** If the same id turns up twice, one entry
   overwrites the other in the model dictionary -- silent data loss.
4. **Names are made unique.** In the generated code state names become enum
   constants; using one name twice means the code DOES NOT COMPILE (see V011).
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional, Set, Tuple

from .model import SCHEMA_VERSION, State, Transition, new_id

#: Type signature of the clipboard payload. It stops JSON from another
#: application from entering the model silently.
CLIP_TYPE = "uml_state_fragment"


def collect_subtree(machine, ids) -> List[str]:
    """Returns the given ids and ALL of their subtrees (de-duplicated)."""
    out: List[str] = []
    seen: Set[str] = set()

    def walk(sid: str) -> None:
        if sid in seen or sid not in machine.states:
            return
        seen.add(sid)
        out.append(sid)
        for child in machine.children(sid):
            walk(child.id)

    for sid in ids:
        walk(sid)
    return out


def copy_fragment(machine, ids) -> Optional[str]:
    """Turns the selection into a JSON fragment; ``None`` if nothing to copy.

    :param ids: the selected state and transition ids (they may be mixed)
    """
    state_ids = collect_subtree(
        machine, [i for i in ids if i in machine.states])
    if not state_ids:
        return None

    inside = set(state_ids)

    states = []
    for sid in state_ids:
        d = machine.states[sid].to_dict()
        # A link to a parent state OUTSIDE the selection is not carried over:
        # the paste target may be somewhere else entirely, the parent id would
        # then not exist in the model, and the state would be an orphan (V020).
        if d.get("parent") not in inside:
            d["parent"] = None
        states.append(d)

    # Only transitions with both ends in the selection (see the module docstring).
    trans = [t.to_dict() for t in machine.transitions.values()
             if t.source in inside and t.target in inside]

    return json.dumps({
        "type": CLIP_TYPE,
        "version": SCHEMA_VERSION,
        "states": states,
        "transitions": trans,
    }, ensure_ascii=False)


def _unique_name(base: str, used: Set[str]) -> str:
    """Derives a free name from `base`: Alpha -> Alpha_copy -> Alpha_copy2."""
    aday = "%s_copy" % base
    if aday not in used:
        return aday
    i = 2
    while "%s_copy%d" % (base, i) in used:
        i += 1
    return "%s_copy%d" % (base, i)


def paste_fragment(machine, payload: str,
                   parent: Optional[str] = None,
                   dx: float = 0.0, dy: float = 0.0) -> List[str]:
    """Adds the fragment to the model and returns the NEW state ids.

    :param parent: the composite state the roots go into (None = root region)
    :param dx, dy: position offset -- so the copy does not sit on the original
    :raises ValueError: when the payload is not a fragment of this application
    """
    try:
        data = json.loads(payload)
    except ValueError as exc:
        raise ValueError("Clipboard does not hold a diagram fragment.") from exc

    if not isinstance(data, dict) or data.get("type") != CLIP_TYPE:
        raise ValueError("Clipboard does not hold a diagram fragment.")

    used_names = {s.name for s in machine.states.values()}
    old_new: Dict[str, str] = {}
    new_roots: List[str] = []

    for d in data.get("states", []):
        st = State.from_dict(d)
        old_id = st.id
        st.id = new_id("s")
        old_new[old_id] = st.id

        st.name = _unique_name(st.name, used_names)
        used_names.add(st.name)

        if st.parent is None:
            # The roots of the fragment attach to the paste target and are SHIFTED;
            # inner nodes are positioned relative to the parent, so untouched.
            st.parent = parent
            st.x = round(st.x + dx, 2)
            st.y = round(st.y + dy, 2)
            new_roots.append(st.id)

        machine.add_state(st)

    # Map parent ids in a second pass: the parent may come LATER in the list.
    for old_id, fresh_id in old_new.items():
        st = machine.states[fresh_id]
        if st.parent in old_new:
            st.parent = old_new[st.parent]

    for d in data.get("transitions", []):
        tr = Transition.from_dict(d)
        if tr.source not in old_new or tr.target not in old_new:
            continue          # olmamali; savunmaci
        tr.id = new_id("t")
        tr.source = old_new[tr.source]
        tr.target = old_new[tr.target]
        machine.add_transition(tr)

    return [old_new[k] for k in old_new] if not new_roots else list(
        old_new.values())


def fragment_summary(payload: str) -> Tuple[int, int]:
    """The (state, transition) count in a payload; (0, 0) when invalid."""
    try:
        data = json.loads(payload)
        if data.get("type") != CLIP_TYPE:
            return (0, 0)
        return (len(data.get("states", [])), len(data.get("transitions", [])))
    except Exception:
        return (0, 0)
