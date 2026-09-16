# -*- coding: utf-8 -*-
"""OMG UML 2.5.1 PDF'inden bolum -> SAYFA dizini uretir.

    python tools/build_spec_index.py [uml-17-12-05.pdf]

Cikti: app/core/uml_spec_index.json  ({"14.2.3.4.5": 314, ...})

NEDEN AYRI BIR ARAC
-------------------
Arayuz, bir dogrulama bulgusunun yaninda spesifikasyonun KACINCI SAYFASINA
bakilacagini gosterir. Sayfa numaralarini elle yazmak iki sebeple yanlistir:
177 bolum basligi vardir ve belge surumu degisince hepsi kayar. Bu arac
numaralari belgenin KENDISINDEN okur; uygulama calisma aninda PDF'e ihtiyac
duymaz, yalnizca uretilen JSON'u okur.

BASILI SAYFA vs PDF INDEKSI: ikisi ayni degildir (on soz roma rakamlidir).
Kullaniciya gosterilen numara, belgenin sayfa altliginda yazan BASILI
numaradir -- okuyucu PDF'i acip o numarayi arayacaktir.
"""
from __future__ import annotations

import io
import json
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CIKTI = os.path.join(KOK, "app", "core", "uml_spec_index.json")

VARSAYILAN_PDF = os.path.join(os.path.expanduser("~"), "Desktop",
                              "uml-17-12-05.pdf")

#: Sayfa altligi: "305 Unified Modeling Language 2.5.1" ya da tersi.
_ALTLIK = re.compile(
    r"(?:^|\n)[ \t]*(?:(\d{1,3})[ \t]+Unified Modeling Language 2\.5\.1"
    r"|Unified Modeling Language 2\.5\.1[ \t]+(\d{1,3}))[ \t]*$",
    re.M)

#: Bolum basligi: "14.2.3.4.5 Transitions" gibi. Icindekiler satirlari
#: nokta dizisiyle bittigi icin ELENIR (aksi halde dizin, basligin gercek
#: yerini degil icindekiler sayfasini gosterirdi).
_BASLIK = re.compile(r"^(\d{1,2}(?:\.\d{1,3}){0,4})[ \t]+"
                     r"([A-Z][^\n]{2,90}?)[ \t]*$")


def index_uret(pdf_yolu: str) -> dict:
    from pypdf import PdfReader

    okuyucu = PdfReader(pdf_yolu)
    dizin: dict = {}
    basili = 0

    for sayfa in okuyucu.pages:
        metin = sayfa.extract_text() or ""
        if not metin:
            continue

        m = _ALTLIK.search(metin)
        if m is not None:
            basili = int(m.group(1) or m.group(2))
        if basili <= 0:
            continue

        for satir in metin.splitlines():
            satir = satir.rstrip()
            if "...." in satir:
                continue          # icindekiler satiri
            mm = _BASLIK.match(satir)
            if mm is None:
                continue
            bolum = mm.group(1)
            # ILK gorulen yer baglayicidir: sonraki sayfalarda ayni numara
            # capraz atif olarak gecebilir.
            if bolum not in dizin:
                dizin[bolum] = {"page": basili, "title": mm.group(2).strip()}

    return dizin


def main() -> int:
    pdf = sys.argv[1] if len(sys.argv) > 1 else VARSAYILAN_PDF
    if not os.path.isfile(pdf):
        print("PDF bulunamadi: %s" % pdf)
        return 2

    dizin = index_uret(pdf)
    with io.open(CIKTI, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(dizin, fh, ensure_ascii=False, indent=1, sort_keys=True)

    print("%s -> %s  (%d baslik)" % (os.path.basename(pdf), CIKTI, len(dizin)))
    for ornek in ("14", "14.2.3.4.5", "14.2.3.9", "11.4.3", "11.8.3"):
        kayit = dizin.get(ornek)
        if kayit:
            print("  %-12s s.%-4d %s" % (ornek, kayit["page"], kayit["title"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
