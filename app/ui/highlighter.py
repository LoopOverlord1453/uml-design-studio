"""C / C++ kod paneli renklendiricisi.

SADE OLMASI BILINCLIDIR
-----------------------
Panel eskiden Visual Studio klasik semasini taklit ediyordu: anahtar
kelimeler mavi, tipler camgobegi, dizeler kirmizi, makrolar mor... Bu sema
arayuz temasindan bagimsiz olarak beyaz zemin varsayiyordu ve koyu temada
kod paneli goz alici bir beyaz lekeye donusuyordu.

Simdi tek bir kural var: **yalnizca yorumlar yesildir**, kodun geri kalani
duz metin rengindedir. Renkler etkin temadan gelir (bkz. theme.C.CODE_*).

DIZELER YINE DE TARANIR
-----------------------
Renklendirilmeseler bile dize ve karakter sabitlerinin sinirlarini bilmek
ZORUNLUDUR: aksi halde ``"http://example"`` ya da ``"/*"`` iceren bir dize
yanlislikla yorum baslatir ve satirin geri kalani -- hatta dosyanin geri
kalani -- yesile boyanirdi. Uretilen MCU sablonlari derleme komutunu bir
yorum icinde tasidigi icin bu kose durum gercekten olusur.
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


#: Dize / karakter sabiti baslangici (kacis dizileri dahil).
_RE_LITERAL = re.compile(r'"(?:[^"\\\n]|\\.)*"?' + r"|'(?:[^'\\\n]|\\.)*'?")

#: Denetim akisi ve bildirim anahtar kelimeleri (C99 + C++11 kesisimi).
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

#: Tip adlari ayri bir renk alir: gomulu kodda genislikli tipler
#: (uint8_t, int16_t ...) kodun yarisini olusturur ve anahtar
#: kelimelerden ayri okunmalari yapiyi hizli taratir.
_TYPES = (
    "char double float int long short signed unsigned void size_t ssize_t "
    "ptrdiff_t intptr_t uintptr_t wchar_t char16_t char32_t "
    "int8_t int16_t int32_t int64_t uint8_t uint16_t uint32_t uint64_t "
    "int_least8_t uint_least8_t int_fast8_t uint_fast8_t intmax_t uintmax_t"
).split()

_RE_KEYWORD = re.compile(r"\b(?:%s)\b" % "|".join(_KEYWORDS))
_RE_TYPE = re.compile(r"\b(?:%s)\b" % "|".join(_TYPES))

#: Sayi sabitleri: ondalik, onaltilik, kayan nokta ve sonekler (U, UL, f).
_RE_NUMBER = re.compile(
    r"\b(?:0[xX][0-9a-fA-F]+|\d+\.?\d*(?:[eE][+-]?\d+)?)"
    r"(?:[uUlLfF]+)?\b")

#: Onislemci satiri: satirin basindaki '#' ve hemen ardindaki sozcuk.
_RE_PREPROC = re.compile(r"^\s*#\s*\w+")

#: Islev cagrisi / tanimi: '(' ile biten tanimlayici.
_RE_FUNCTION = re.compile(r"\b([A-Za-z_]\w*)\s*(?=\()")


class CppHighlighter(QSyntaxHighlighter):
    """Hem C hem C++ icin; yalnizca yorumlari boyar."""

    #: Onceki satir kapanmamis bir ``/* ... */`` icinde bitti.
    IN_COMMENT = 1

    def __init__(self, document) -> None:
        super().__init__(document)
        # Renkler KURULUMDA okunur; tema degisiminde CodeEditor.retheme()
        # renklendiriciyi yeniden kurar (bkz. code_editor.py).
        self.f_comment = _fmt(C.CODE_COMMENT)
        self.f_keyword = _fmt(C.CODE_KEYWORD)
        self.f_type = _fmt(C.CODE_TYPE)
        self.f_string = _fmt(C.CODE_STRING)
        self.f_number = _fmt(C.CODE_NUMBER)
        self.f_preproc = _fmt(C.CODE_PREPROC)
        self.f_function = _fmt(C.CODE_FUNCTION)

    # ------------------------------------------------------------------ API #

    def highlightBlock(self, text: str) -> None:
        # SIRA ONEMLI. Once kod boyanir, sonra dizeler, en son yorumlar --
        # her adim oncekinin uzerine yazar. Ters sirada `// return x;`
        # icindeki `return` anahtar kelime rengiyle yesil yorumun ustune
        # binerdi; yorum icinde sozdizimi YOKTUR.
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
                # Dize BOYANIR ve uzerinden ATLANIR: icindeki // ya da /*
                # yorum baslatmamalidir (uretilen MCU sablonlari derleme
                # komutunu bir dizede tasir).
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

    # ------------------------------------------------------------ yardimci #

    def _paint_code(self, text: str) -> None:
        """Satirin TAMAMINI kod varsayarak boyar.

        Dize ve yorum bolgeleri cagiran tarafta UZERINE YAZILIR; burada
        onlari ayirt etmeye calismak ayni taramayi iki kez yapmak olurdu.
        """
        onislemci = _RE_PREPROC.match(text)
        if onislemci is not None:
            self.setFormat(onislemci.start(),
                           onislemci.end() - onislemci.start(), self.f_preproc)

        for m in _RE_FUNCTION.finditer(text):
            self.setFormat(m.start(1), m.end(1) - m.start(1), self.f_function)

        # Anahtar kelime ve tipler islev adlarindan SONRA gelir: `if (` ve
        # `sizeof (` parantezle bittigi icin islev sanilir, dogru renk
        # anahtar kelime rengidir.
        for m in _RE_TYPE.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), self.f_type)
        for m in _RE_KEYWORD.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), self.f_keyword)
        for m in _RE_NUMBER.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), self.f_number)

    @staticmethod
    def _next_token(text: str, pos: int) -> Optional[Tuple[str, int, int]]:
        """Konumdan sonraki ILK ilgin belirteci bulur.

        Doner: ("literal" | "line" | "block", baslangic, uzunluk).
        Uzunluk yalnizca "literal" icin anlamlidir.

        En erken eslesme kazanir; bu yuzden ucu de aranip karsilastirilir.
        Sadece sirayla aramak ``x = "a"; // not`` gibi bir satirda yorumu
        dizeden once bulurdu.
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
