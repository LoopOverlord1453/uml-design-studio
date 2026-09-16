"""Breaking the texts shown on the diagram into lines.

It does NOT depend on Qt (the app/core rule): it lives here so the canvas
drawing and the code generator follow the very same rule.

The line-break marker
---------------------
In any text field the user breaks a line by typing ``\\n`` (backslash + n).
The rule is the same everywhere: state behaviours (entry / exit / do), the
transition event / guard / effect, and note fields. In multi-line fields the
real Enter key does the same; in single-line fields Enter cannot be typed,
so the marker is what makes it possible.

Why ``\\n``
-----------
It is the shared convention of diagram tools (PlantUML, Graphviz) and needs
no explaining to anyone who writes C. The only realistic clash is C strings::

    printf("Fault\\n");

That is why the marker counts as a line break ONLY OUTSIDE string and
character literals; the line above stays in one piece.
"""

from __future__ import annotations

from typing import List

#: The two characters the user types.  Shown as a hint in the interface.
LINE_BREAK_MARKER = "\\n"


def _scan(text: str) -> List[str]:
    """Splits the text into raw parts; string/char literals stay intact.

    In the returned parts there is no longer a distinction between a real
    newline and the marker -- both have become part boundaries.
    """
    parts: List[str] = []
    buf: List[str] = []
    quote = ""          # quote of the literal we are inside ("" = outside)
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]

        if quote:
            buf.append(ch)
            if ch == "\\" and i + 1 < n:
                # Escape sequence: the next character does NOT close the
                # literal, even when it is a quote.
                buf.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = ""
            i += 1
            continue

        if ch in ('"', "'"):
            quote = ch
            buf.append(ch)
            i += 1
            continue

        if ch == "\n":
            parts.append("".join(buf))
            buf = []
            i += 1
            continue

        if ch == "\\" and i + 1 < n and text[i + 1] == "n":
            parts.append("".join(buf))
            buf = []
            i += 2
            continue

        buf.append(ch)
        i += 1

    parts.append("".join(buf))
    return parts


def split_lines(text: str) -> List[str]:
    """Returns the display lines.

    Runs of whitespace inside each line collapse to a single space (indentation
    in code fields only takes up room on the canvas) and empty lines are dropped.
    """
    if not text:
        return []
    lines = [" ".join(part.split()) for part in _scan(text)]
    return [ln for ln in lines if ln]


def flatten(text: str) -> str:
    """Reduces the text to a SINGLE line.

    For places that cannot carry a line break: PlantUML labels, tree rows.
    """
    return " ".join(split_lines(text))


def expand_breaks(text: str) -> str:
    """Turns the marker into a REAL newline; leaves everything else alone.

    Texts that go into the generated code pass through here: ``\\n`` is not
    valid outside a C string, and written as is the generated code would not
    compile. Indentation and spacing are PRESERVED -- the code the user wrote
    """
    if not text:
        return text
    return "\n".join(_scan(text))
