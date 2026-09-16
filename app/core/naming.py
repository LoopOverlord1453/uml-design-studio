"""Name conversions -- INDEPENDENT of Qt and of the generators.

The conversions the generators use and the ones the validator checks must
be IDENTICAL: a validator that does not know the symbol the generator will
actually write cannot see a collision, and code that does not compile gets
produced. That is why both sides use this module; there is no second copy.
"""

from __future__ import annotations

import re

#: Every character that cannot appear in an identifier counts as a name
#: separator, the space included: "Traffic Light" -> "TrafficLight". Otherwise
#: the space reaches the C++ class name and the include guard, and it fails.
_SEPARATORS = re.compile(r"[^0-9A-Za-z]+")


def pascal(name: str, fallback: str = "Sm") -> str:
    """Converts to PascalCase; the result is always a valid C identifier.

    All-uppercase parts are lowered ("COMPLETION" -> "Completion"), while the
    inner capitals of mixed-case parts are preserved ("myEvent" ->
    "MyEvent"). Results starting with a digit, or coming out empty, are given
    the ``fallback`` prefix.
    """
    parts = [p for p in _SEPARATORS.split(name) if p]
    if not parts:
        return fallback
    chunks = []
    for part in parts:
        body = part[1:].lower() if part.isupper() else part[1:]
        chunks.append(part[:1].upper() + body)
    out = "".join(chunks)
    if not (out[0].isalpha() or out[0] == "_"):
        out = fallback + out
    return out


def snake(name: str) -> str:
    """PascalCase / camelCase -> snake_case."""
    s = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    return s.replace("-", "_").lower()


def lower_camel(name: str) -> str:
    """The lower-initial spelling used for C++ member names."""
    if not name:
        return name
    return name[:1].lower() + name[1:]


def screaming_snake(name: str, fallback: str = "SM") -> str:
    """Derives a C enum/macro constant from a UML name: ``LedOn`` -> ``LED_ON``.

    UML 2.5.1 writes state and signal names in UpperCamelCase; the C equivalent
    is SCREAMING_SNAKE_CASE, and the conversion MUST PRESERVE WORD BOUNDARIES.

    The generator used to call ``name.upper()``. That caused two problems:

      * LOST WORD BOUNDARY -- the constants ``LedOn`` and ``LedOff`` turned
        into ``..._LEDON`` / ``..._LEDOFF``; hard to read and inconsistent
        with the C++ output (``State::LedOn``).
      * NON-IDENTIFIER CHARACTER -- when a name carried a space or a dash it
        produced a constant such as ``BLINKY_STATE_LED ON``, which DOES NOT
        COMPILE. The validator catches that with V010, but the generator can
        also be called INDEPENDENTLY of it; the conversion must be safe alone.

    So it uses the same separator set as ``pascal()`` and always returns a
    valid C identifier.
    """
    out = snake(pascal(name, fallback)).upper()
    if not out:
        return fallback
    if not (out[0].isalpha() or out[0] == "_"):
        out = fallback + "_" + out
    return out
