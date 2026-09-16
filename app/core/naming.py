"""Ad donusumleri -- Qt'den ve uretecten BAGIMSIZ.

Ureteclerin kullandigi donusumler ile dogrulayicinin denetledigi donusumler
AYNI olmak zorundadir: dogrulayici, uretecin gercekte yazacagi sembolu
bilmiyorsa cakismayi goremez ve derlenmeyen kod uretilir. Bu yuzden her iki
taraf da bu modulu kullanir; kopya tanim yoktur.
"""

from __future__ import annotations

import re

#: Tanimlayici olamayan her karakter ad ayiricisi sayilir. Bosluk da buna
#: dahildir: "Traffic Light" -> "TrafficLight". Aksi halde bosluk C++ sinif
#: adina ve include guard'ina aynen gecer ve dosya derlenmez.
_SEPARATORS = re.compile(r"[^0-9A-Za-z]+")


def pascal(name: str, fallback: str = "Sm") -> str:
    """PascalCase'e cevirir; sonuc her zaman gecerli bir C tanimlayicisidir.

    Tamami buyuk harf parcalar kucultulur ("COMPLETION" -> "Completion"),
    karisik yazilmis parcalarin ic buyuk harfleri korunur ("myEvent" ->
    "MyEvent"). Rakamla baslayan ya da bos kalan sonuclara ``fallback`` on eki
    verilir.
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
    """C++ uye adlarinda kullanilan bas harfi kucuk bicim."""
    if not name:
        return name
    return name[:1].lower() + name[1:]


def screaming_snake(name: str, fallback: str = "SM") -> str:
    """UML adindan C enum/makro sabiti turetir:  ``LedOn`` -> ``LED_ON``.

    UML 2.5.1'de durum ve sinyal adlari UpperCamelCase yazilir; C'de karsiligi
    SCREAMING_SNAKE_CASE'tir ve donusum SOZCUK SINIRLARINI KORUMALIDIR.

    Ureteci eskiden ``name.upper()`` cagiriyordu. Iki ayri sorun cikariyordu:

      * SOZCUK SINIRI KAYBI -- ``LedOn`` ve ``LedOff`` sabitleri
        ``..._LEDON`` / ``..._LEDOFF`` oluyordu; okunmasi zor ve C++ ciktisiyla
        (``State::LedOn``) ayrisik.
      * TANIMLAYICI OLMAYAN KARAKTER -- ad bosluk ya da tire tasidiginda
        ``BLINKY_STATE_LED ON`` gibi DERLENMEYEN bir sabit uretiliyordu.
        Dogrulayici bunu V010 ile yakalar, ama uretec dogrulayicidan
        BAGIMSIZ da cagrilabilir; donusum kendi basina guvenli olmalidir.

    Bu yuzden ``pascal()`` ile ayni ayirici kumesini kullanir ve sonucu her
    zaman gecerli bir C tanimlayicisi olarak dondurur.
    """
    out = snake(pascal(name, fallback)).upper()
    if not out:
        return fallback
    if not (out[0].isalpha() or out[0] == "_"):
        out = fallback + "_" + out
    return out
