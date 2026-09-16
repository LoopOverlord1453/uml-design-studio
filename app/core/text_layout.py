"""Diyagramda gosterilen metinlerin satirlara ayrilmasi.

Qt'ye BAGIMLI DEGILDIR (app/core kurali): hem tuval cizimi hem kod ureteci
ayni kurali kullansin diye buradadir.

Satir sonu isareti
------------------
Kullanici, herhangi bir metin alaninda ``\\n`` (ters bolu + n) yazarak satiri
boler. Ayni kural her yerde gecerlidir: durum davranislari (entry / exit / do),
gecis olayi / guard / eylemi ve not alanlari. Cok satirli alanlarda gercek
Enter tusu da ayni isi yapar; tek satirlik alanlarda Enter yazilamadigi icin
isaret gereklidir.

Neden ``\\n``
-------------
Diyagram araclarinin ortak kuralidir (PlantUML, Graphviz) ve C yazan birine
aciklama gerektirmez. Tek gercekci carpisma C dizgeleridir::

    printf("Fault\\n");

Bu yuzden isaret YALNIZCA dizge ve karakter sabitlerinin DISINDA satir sonu
sayilir; yukaridaki satir tek parca kalir.
"""

from __future__ import annotations

from typing import List

#: Kullanicinin yazacagi iki karakter.  Arayuzde ipucu olarak gosterilir.
LINE_BREAK_MARKER = "\\n"


def _scan(text: str) -> List[str]:
    """Metni ham parcalara ayirir; dizge/karakter sabitleri korunur.

    Dondurulen parcalarda gercek satir sonu ve isaret ayrimi kalmaz --
    ikisi de parca siniri olmustur.
    """
    parts: List[str] = []
    buf: List[str] = []
    quote = ""          # icinde bulundugumuz sabitin tirnagi ("" = disarida)
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]

        if quote:
            buf.append(ch)
            if ch == "\\" and i + 1 < n:
                # Kacis dizisi: bir sonraki karakter tirnak olsa bile
                # sabiti KAPATMAZ.
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
    """Gorunum satirlarini dondurur.

    Her satirin ic bosluklari tek boslua indirilir (kod alanlarindaki
    girinti tuvalde yalnizca yer kaplar) ve bos satirlar atilir.
    """
    if not text:
        return []
    lines = [" ".join(part.split()) for part in _scan(text)]
    return [ln for ln in lines if ln]


def flatten(text: str) -> str:
    """Metni TEK satira indirir.

    PlantUML etiketi, agac satiri gibi satir sonu tasiyamayan yerler icin.
    """
    return " ".join(split_lines(text))


def expand_breaks(text: str) -> str:
    """Isareti GERCEK satir sonuna cevirir; gerisine dokunmaz.

    Uretilen koda giden metinler bundan gecer: ``\\n`` C'de dizge disinda
    gecerli degildir, oldugu gibi yazilirsa uretilen kod derlenmez.
    Girinti ve bosluklar KORUNUR -- kullanicinin yazdigi kod aynen kalmali.
    """
    if not text:
        return text
    return "\n".join(_scan(text))
