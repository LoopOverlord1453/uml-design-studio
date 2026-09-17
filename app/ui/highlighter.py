"""Syntax highlighter for the C / C++ code panel.

BEING PLAIN IS DELIBERATE
-------------------------
The panel used to imitate the classic Visual Studio scheme: keywords blue,
types cyan, strings red, macros purple... That scheme assumed a white
background regardless of the interface theme, and in the dark theme the code
panel turned into a glaring white patch.

Now there is a single rule: **only comments are green**, the rest of the code
is in the plain text colour. The colours come from the active theme
(see theme.C.CODE_*).
STRINGS ARE STILL SCANNED
-------------------------
Even though they are not coloured, knowing the boundaries of string and
character literals is MANDATORY: otherwise a string containing
``"http://example"`` or ``"/*"`` would start a comment by accident and the
rest of the line -- or even of the file -- would turn green. The generated MCU
templates carry a compile command inside a comment, so this corner case is real.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from PyQt6.QtGui import QColor, QSyntaxHighlighter, QTextCharFormat

from .theme import C


def _fmt(color: str) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    return f


#: The start of a string / character literal (escape sequences included).
_RE_LITERAL = re.compile(r'"(?:[^"\\\n]|\\.)*"?' + r"|'(?:[^'\\\n]|\\.)*'?")

#: Control-flow and declaration keywords (the C99 + C++11 intersection).
_KEYWORDS = (
    "alignas alignof and and_eq asm auto bitand bitor bool break case catch "
    "class compl const consteval constexpr const_cast continue decltype "
    "default delete do dynamic_cast else enum explicit export extern false "
    "for friend goto if inline mutable namespace new noexcept not not_eq "
    "nullptr operator or or_eq private protected public register "
    "reinterpret_cast restrict return sizeof static static_assert "
    "static_cast struct switch template this thread_local throw true try "
    "typedef typeid typename union using virtual volatile while xor xor_eq"
).split()

#: Type names get a separate colour: in embedded code the sized types
#: (uint8_t, int16_t ...) make up half the code, and reading them apart
#: from keywords makes the structure quick to scan.
_TYPES = (
    "char double float int long short signed unsigned void size_t ssize_t "
    "ptrdiff_t intptr_t uintptr_t wchar_t char16_t char32_t "
    "int8_t int16_t int32_t int64_t uint8_t uint16_t uint32_t uint64_t "
    "int_least8_t uint_least8_t int_fast8_t uint_fast8_t intmax_t uintmax_t"
).split()

_RE_KEYWORD = re.compile(r"\b(?:%s)\b" % "|".join(_KEYWORDS))
_RE_TYPE = re.compile(r"\b(?:%s)\b" % "|".join(_TYPES))

#: Numeric literals: decimal, hex, floating point and suffixes (U, UL, f).
_RE_NUMBER = re.compile(
    r"\b(?:0[xX][0-9a-fA-F]+|\d+\.?\d*(?:[eE][+-]?\d+)?)"
    r"(?:[uUlLfF]+)?\b")

#: A preprocessor line: the '#' at the start and the word right after it.
_RE_PREPROC = re.compile(r"^\s*#\s*\w+")

#: A function call / definition: an identifier followed by '('.
_RE_FUNCTION = re.compile(r"\b([A-Za-z_]\w*)\s*(?=\()")


class CppHighlighter(QSyntaxHighlighter):
    """For both C and C++; it colours comments only."""

    #: The previous line ended inside an unclosed ``/* ... */``.
    IN_COMMENT = 1

    def __init__(self, document) -> None:
        super().__init__(document)
        # The colours are read AT SET-UP; on a theme change CodeEditor.retheme()
        # rebuilds the highlighter (see code_editor.py).
        self.f_comment = _fmt(C.CODE_COMMENT)
        self.f_keyword = _fmt(C.CODE_KEYWORD)
        self.f_type = _fmt(C.CODE_TYPE)
        self.f_string = _fmt(C.CODE_STRING)
        self.f_number = _fmt(C.CODE_NUMBER)
        self.f_preproc = _fmt(C.CODE_PREPROC)
        self.f_function = _fmt(C.CODE_FUNCTION)

    # ------------------------------------------------------------------ API #

    def highlightBlock(self, text: str) -> None:
        # THE ORDER MATTERS. Code first, then strings, comments last -- each step
        # paints over the previous one. In the reverse order the `return` inside
        # `// return x;` would sit on top of the green comment in the keyword
        # colour; there is NO syntax inside a comment.
        self._paint_code(text)

        spans: List[Tuple[int, int]] = []
        literals: List[Tuple[int, int]] = []
        pos = 0
        in_block = self.previousBlockState() == self.IN_COMMENT

        while pos < len(text):
            if in_block:
                end = text.find("*/", pos)
                if end < 0:
                    spans.append((pos, len(text) - pos))
                    pos = len(text)
                    break
                spans.append((pos, end + 2 - pos))
                pos = end + 2
                in_block = False
                continue

            nxt = self._next_token(text, pos)
            if nxt is None:
                break
            kind, start, length = nxt
            if kind == "literal":
                # A string IS PAINTED and SKIPPED OVER: a // or /* inside it must not
                # start a comment (the generated MCU templates carry a compile command
                # inside a string).
                literals.append((start, length))
                pos = start + length
            elif kind == "line":
                spans.append((start, len(text) - start))
                pos = len(text)
            else:                                   # "block"
                pos = start
                in_block = True

        self.setCurrentBlockState(self.IN_COMMENT if in_block else 0)

        for start, length in literals:
            if length > 0:
                self.setFormat(start, length, self.f_string)
        for start, length in spans:
            if length > 0:
                self.setFormat(start, length, self.f_comment)

    # ------------------------------------------------------------- helpers #

    def _paint_code(self, text: str) -> None:
        """Colours the WHOLE line as if it were code.

        String and comment regions are OVERWRITTEN by the caller; trying to
        tell them apart here would mean doing the same scan twice.
        """
        onislemci = _RE_PREPROC.match(text)
        if onislemci is not None:
            self.setFormat(onislemci.start(),
                           onislemci.end() - onislemci.start(), self.f_preproc)

        for m in _RE_FUNCTION.finditer(text):
            self.setFormat(m.start(1), m.end(1) - m.start(1), self.f_function)

        # Keywords and types come AFTER the function names: `if (` and
        # `sizeof (` end with a parenthesis and look like functions, but the
        # right colour is the keyword colour.
        for m in _RE_TYPE.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), self.f_type)
        for m in _RE_KEYWORD.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), self.f_keyword)
        for m in _RE_NUMBER.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), self.f_number)

    @staticmethod
    def _next_token(text: str, pos: int) -> Optional[Tuple[str, int, int]]:
        """Finds the FIRST interesting token after the position.

        Returns: ("literal" | "line" | "block", start, length).
        The length is meaningful for "literal" only.

        The earliest match wins, so all three are searched and compared.
        Searching them in order would find the comment before the string on a
        line such as ``x = "a"; // note``.
        """
        line = text.find("//", pos)
        block = text.find("/*", pos)
        m = _RE_LITERAL.search(text, pos)
        lit = m.start() if m else -1

        best: Optional[Tuple[str, int, int]] = None
        for kind, start, length in (("literal", lit, m.end() - lit if m else 0),
                                    ("line", line, 2),
                                    ("block", block, 2)):
            if start < 0:
                continue
            if best is None or start < best[1]:
                best = (kind, start, length)
        return best
