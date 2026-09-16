"""Koruma (guard) ifadelerini DEGISKEN DEGERLERINDEN hesaplar.

Benzetim panelinde her koruma icin elle bir anahtar cevirmek, modelin
gercekten ne yaptigini gostermez: `ctx->temperature_mdeg >= 30000` gibi
bir kosul, sicakligi 31000 yazip sonucun kendiliginden cikmasiyla cok
daha anlasilirdir. Bu modul o hesabi yapar.

Neyi hesaplar:
  - karsilastirmalar, mantik baglaclari, aritmetik ve bit islemleri;
  - `ctx->alan`, `ctx.alan`, `me->alan` yazimlarini duz `alan` degiskenine
    indirger (uretilen kodda baglam hep `ctx` adiyla gelir).

Neyi hesaplamaz -- ve BUNU ACIKCA SOYLER:
  - islev cagrilari (`app_over_limit(ctx)`), isaretci erisimleri, atama,
    ya da taninmayan her sey. Bu durumda `evaluate()` None doner ve panel
    kullanicinin elle verdigi degere duser. Sessizce True varsaymak,
    korumali her gecisin alinmasina ve ekranda yanlisin dogru gorunmesine
    yol acardi.
"""

from __future__ import annotations

import ast
import re
from typing import Dict, List, Optional, Tuple

__all__ = ["normalize", "identifiers", "evaluate", "parse_value",
           "format_value"]

#: `ctx->alan`, `ctx.alan`, `me->alan` -> `alan`
_CONTEXT = re.compile(r"\b(?:ctx|me|self)\s*(?:->|\.)\s*([A-Za-z_]\w*)")
#: `->` kalanlari (baska bir isaretci): degiskene indirgenemez, isaretle.
_ARROW = re.compile(r"->")

_WORDS = (
    (re.compile(r"&&"), " and "),
    (re.compile(r"\|\|"), " or "),
    (re.compile(r"\btrue\b"), "True"),
    (re.compile(r"\bfalse\b"), "False"),
    (re.compile(r"\bTRUE\b"), "True"),
    (re.compile(r"\bFALSE\b"), "False"),
    (re.compile(r"\bNULL\b"), "None"),
    (re.compile(r"\bnullptr\b"), "None"),
)

#: `!` -- ama `!=` DEGIL.
_NOT = re.compile(r"!(?!=)")

#: Hesaplanmasina izin verilen dugumler.
_NODES = (
    ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not,
    ast.USub, ast.UAdd, ast.Invert, ast.BinOp, ast.Add, ast.Sub, ast.Mult,
    ast.Div, ast.FloorDiv, ast.Mod, ast.LShift, ast.RShift, ast.BitAnd,
    ast.BitOr, ast.BitXor, ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE,
    ast.Gt, ast.GtE, ast.Name, ast.Load, ast.Constant,
)


def normalize(expr: str) -> str:
    """C/C++ yazimini Python ifadesine cevirir (deger hesaplamaz)."""
    text = expr.strip()
    text = _CONTEXT.sub(r"\1", text)
    for kalip, yerine in _WORDS:
        text = kalip.sub(yerine, text)
    text = _NOT.sub(" not ", text)
    return text.strip()


def _tree(expr: str) -> Optional[ast.Expression]:
    try:
        return ast.parse(normalize(expr), mode="eval")
    except SyntaxError:
        return None


def _supported(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, _NODES):
            return False
    return True


def identifiers(expr: str) -> List[str]:
    """Ifadenin okudugu degisken adlari; hesaplanamiyorsa bos liste.

    Sira KORUNUR ve yinelenenler atilir, cunku panel bu listeden bir
    tablo kurar ve satirlarin her yenilemede yer degistirmesi istenmez.
    """
    if _ARROW.search(_CONTEXT.sub(r"\1", expr)):
        return []
    tree = _tree(expr)
    if tree is None or not _supported(tree):
        return []
    out: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id not in out:
            out.append(node.id)
    return out


def evaluate(expr: str, values: Dict[str, object]) -> Optional[bool]:
    """Ifadeyi verilen degerlerle hesaplar.

    @return True/False, ya da hesaplanamiyorsa None (islev cagrisi,
            taninmayan sozdizimi, tanimsiz degisken, sifira bolme...).
    """
    if expr.strip().lower() == "else":
        return True
    if _ARROW.search(_CONTEXT.sub(r"\1", expr)):
        return None
    tree = _tree(expr)
    if tree is None or not _supported(tree):
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id not in values:
            return None
    try:
        sonuc = eval(compile(tree, "<guard>", "eval"),      # noqa: S307
                     {"__builtins__": {}}, dict(values))
    except Exception:                                        # noqa: BLE001
        return None
    try:
        return bool(sonuc)
    except Exception:                                        # noqa: BLE001
        return None


def parse_value(text: str) -> Tuple[bool, object]:
    """Kullanicinin yazdigi metni sayiya/mantiksala cevirir.

    @return (basarili, deger). Basarisizsa deger metnin kendisidir; panel
            onu kirmizi gosterir, cunku bir koruma icinde ise sonuc
            hesaplanamaz.
    """
    ham = text.strip()
    if not ham:
        return False, ""
    dusuk = ham.lower()
    if dusuk in ("true", "1", "yes", "on"):
        return True, True
    if dusuk in ("false", "0", "no", "off"):
        return True, False
    try:
        return True, int(ham, 0)
    except ValueError:
        pass
    try:
        return True, float(ham)
    except ValueError:
        return False, ham


def format_value(value: object) -> str:
    """parse_value()'nun tersi: degeri kullaniciya gosterilecek metne."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
