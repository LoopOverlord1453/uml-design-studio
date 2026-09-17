"""Evaluates guard expressions FROM VARIABLE VALUES.

Flipping a switch by hand for every guard in the simulation panel does not
show what the model really does: a condition such as
`ctx->temperature_mdeg >= 30000` is far clearer when you type 31000 for the
temperature and watch the result follow. This module does that evaluation.

What it evaluates:
  - comparisons, logical connectives, arithmetic and bitwise operations;
  - `ctx->field`, `ctx.field` and `me->field` are reduced to a plain `field`
    variable (in the generated code the context is always called `ctx`).

What it does NOT evaluate -- and SAYS SO EXPLICITLY:
  - function calls (`app_over_limit(ctx)`), pointer accesses, assignment, or
    anything unrecognised. Then `evaluate()` returns None and the panel falls
    back to the value the user set by hand. Silently assuming True would take
    every guarded transition and make something wrong look right on the
    screen.
"""

from __future__ import annotations

import ast
import re
from typing import Dict, List, Optional, Tuple

__all__ = ["normalize", "identifiers", "evaluate", "parse_value",
           "format_value"]

#: `ctx->field`, `ctx.field`, `me->field` -> `field`
_CONTEXT = re.compile(r"\b(?:ctx|me|self)\s*(?:->|\.)\s*([A-Za-z_]\w*)")
#: A remaining `->` (some other pointer): not reducible, so flag it.
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

#: `!` -- but NOT `!=`.
_NOT = re.compile(r"!(?!=)")

#: The node types that are allowed to be evaluated.
_NODES = (
    ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not,
    ast.USub, ast.UAdd, ast.Invert, ast.BinOp, ast.Add, ast.Sub, ast.Mult,
    ast.Div, ast.FloorDiv, ast.Mod, ast.LShift, ast.RShift, ast.BitAnd,
    ast.BitOr, ast.BitXor, ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE,
    ast.Gt, ast.GtE, ast.Name, ast.Load, ast.Constant,
)


def normalize(expr: str) -> str:
    """Converts C/C++ spelling into a Python expression (evaluates nothing)."""
    text = expr.strip()
    text = _CONTEXT.sub(r"\1", text)
    for kalip, instead in _WORDS:
        text = kalip.sub(instead, text)
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
    """The variable names the expression reads; empty when not evaluable.

    Order is PRESERVED and duplicates are dropped, because the panel builds a
    table from this list and the rows should not move on every refresh.
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
    """Evaluates the expression with the given values.

    @return True/False, or None when it cannot be evaluated (function call,
            unrecognised syntax, undefined variable, division by zero...).
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
        result = eval(compile(tree, "<guard>", "eval"),      # noqa: S307
                     {"__builtins__": {}}, dict(values))
    except Exception:                                        # noqa: BLE001
        return None
    try:
        return bool(result)
    except Exception:                                        # noqa: BLE001
        return None


def parse_value(text: str) -> Tuple[bool, object]:
    """Converts text typed by the user into a number or a boolean.

    @return (ok, value). On failure the value is the text itself; the panel
            shows it in red, because inside a guard the result then cannot
            be evaluated.
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
    """The inverse of parse_value(): a value as text shown to the user."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
