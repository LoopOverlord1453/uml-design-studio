"""Iki model surumunun ANLAMSAL karsilastirmasi -- Qt'siz.

NEDEN
-----
Model dosyalari JSON'dur. `git diff` onlari METIN olarak karsilastirir ve
sonuc okunamaz: bir durumu 10 piksel tasimak, ilgisiz gorunen bir satir
degisikligi uretir; bir durum eklemek ise kimlik/koordinat alanlariyla
birlikte onlarca satir olarak cikar. Kullanicinin sordugu soru
"diyagramda NE degisti" -- "hangi JSON satiri degisti" degil.

Bu modul iki surumu MODEL DUZEYINDE karsilastirir:

    + State  LedOn                     eklendi
    - State  Standby                   silindi
    ~ State  Running       entry: ...  degisti
    + Transition  Off --BUTTON--> Running

Kimlik (id) uzerinden eslestirme yapilir; ad degisikligi "yeniden
adlandirildi" olarak gorunur, silme + ekleme olarak DEGIL.

Cikti (kind, sign, text) uclulerinden olusur; renklendirmeyi arayuz yapar:
  sign "+" -> eklendi (yesil), "-" -> silindi (kirmizi), "~" -> degisti.
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional, Tuple

#: (bolum, isaret, metin)
DiffRow = Tuple[str, str, str]

#: Karsilastirmada GOZ ARDI EDILEN alanlar. Bunlar diyagramin ANLAMINI
#: degistirmez; dahil edilirse pencereyi surukleyen her hareket "degisti"
#: satiri uretir ve gercek degisiklikler kaybolur.
_GORSEL_ALANLAR = {"x", "y", "w", "h", "waypoints", "label_dx", "label_dy"}


def _yukle(metin: str) -> Optional[dict]:
    try:
        veri = json.loads(metin)
    except ValueError:
        return None
    return veri if isinstance(veri, dict) else None


def _anlamli(d: dict) -> dict:
    """Gorsel alanlari atilmis kopya."""
    return {k: v for k, v in d.items() if k not in _GORSEL_ALANLAR}


def _durum_etiketi(d: dict) -> str:
    return "%s %s" % (str(d.get("kind", "state")).replace("_", " "),
                      d.get("name", "?"))


def _gecis_etiketi(d: dict, durumlar: Dict[str, dict]) -> str:
    kaynak = durumlar.get(d.get("source", ""), {}).get("name", "?")
    hedef = durumlar.get(d.get("target", ""), {}).get("name", "?")
    olay = d.get("event") or "(completion)"
    guard = d.get("guard") or ""
    eylem = d.get("action") or ""
    etiket = "%s --%s--> %s" % (kaynak, olay, hedef)
    if guard:
        etiket += "  [%s]" % guard
    if eylem:
        etiket += "  / %s" % eylem
    return etiket


def _farklar(eski: dict, yeni: dict) -> List[str]:
    """Degisen alanlarin 'alan: eski -> yeni' listesi."""
    out = []
    for anahtar in sorted(set(eski) | set(yeni)):
        a, b = eski.get(anahtar), yeni.get(anahtar)
        if a == b:
            continue
        out.append("%s: %r -> %r" % (anahtar, a, b))
    return out


def _karsilastir(bolum: str, eski_liste, yeni_liste, etiket_fn) -> List[DiffRow]:
    """Kimlige gore eslestirip ekle / sil / degistir satirlari uretir."""
    eski = {d.get("id"): d for d in eski_liste if isinstance(d, dict)}
    yeni = {d.get("id"): d for d in yeni_liste if isinstance(d, dict)}

    satirlar: List[DiffRow] = []

    for kimlik, d in yeni.items():
        if kimlik not in eski:
            satirlar.append((bolum, "+", etiket_fn(d)))

    for kimlik, d in eski.items():
        if kimlik not in yeni:
            satirlar.append((bolum, "-", etiket_fn(d)))

    for kimlik, y in yeni.items():
        e = eski.get(kimlik)
        if e is None:
            continue
        degisen = _farklar(_anlamli(e), _anlamli(y))
        if not degisen:
            continue
        baslik = etiket_fn(y)
        eski_ad, yeni_ad = e.get("name"), y.get("name")
        if eski_ad != yeni_ad:
            # Ad degisikligi ayrica gosterilir: kimlik ayni oldugu icin
            # bu bir "sil + ekle" DEGIL, yeniden adlandirmadir.
            baslik = "%s  (renamed from '%s')" % (baslik, eski_ad)
        satirlar.append((bolum, "~", baslik))
        for satir in degisen:
            satirlar.append((bolum, " ", "    " + satir))

    return satirlar


def element_status(eski_metin: str, yeni_metin: str) -> dict:
    """KIMLIK duzeyinde fark: tuvalde boyanabilecek bicimde.

    Metin fark satirlari okumak icindir; diyagrami BOYAMAK icin hangi
    ELEMANIN eklendigi / silindigi / degistigi gerekir.

    Doner::

        {"added":   {id, ...},          # yeni surumde var, eskisinde yok
         "removed": {id: eski_sozluk},  # eski surumde vardi, simdi yok
         "changed": {id, ...}}          # ikisinde de var, anlami degismis

    `removed` sozluk TASIR: silinen eleman yeni modelde bulunmadigi icin
    tuvale ancak eski surumdeki haliyle (hayalet olarak) cizilebilir.
    """
    eski = _yukle(eski_metin) or {}
    yeni = _yukle(yeni_metin) or {}

    eklenen, silinen, degisen = set(), {}, set()
    for alan in ("states", "transitions", "classes", "relations"):
        e = {d.get("id"): d for d in (eski.get(alan) or [])
             if isinstance(d, dict)}
        y = {d.get("id"): d for d in (yeni.get(alan) or [])
             if isinstance(d, dict)}
        for kimlik in y:
            if kimlik not in e:
                eklenen.add(kimlik)
            elif _farklar(_anlamli(e[kimlik]), _anlamli(y[kimlik])):
                degisen.add(kimlik)
        for kimlik, d in e.items():
            if kimlik not in y:
                silinen[kimlik] = d
    return {"added": eklenen, "removed": silinen, "changed": degisen}


def state_machine_diff(eski_metin: str, yeni_metin: str) -> List[DiffRow]:
    """Iki durum makinesi surumunu karsilastirir."""
    eski = _yukle(eski_metin) or {}
    yeni = _yukle(yeni_metin) or {}

    durumlar = {}
    for d in list(eski.get("states") or []) + list(yeni.get("states") or []):
        if isinstance(d, dict):
            durumlar[d.get("id")] = d

    satirlar: List[DiffRow] = []
    satirlar += _karsilastir("States", eski.get("states") or [],
                             yeni.get("states") or [], _durum_etiketi)
    satirlar += _karsilastir(
        "Transitions", eski.get("transitions") or [],
        yeni.get("transitions") or [],
        lambda d: _gecis_etiketi(d, durumlar))

    satirlar += _makine_ayarlari(eski, yeni)
    return satirlar


def _makine_ayarlari(eski: dict, yeni: dict) -> List[DiffRow]:
    """Makine duzeyindeki alanlar (ad, on ek, baglam tipi, ...)."""
    alanlar = ("name", "prefix", "context_type", "user_includes", "description")
    out: List[DiffRow] = []
    for alan in alanlar:
        a, b = eski.get(alan), yeni.get(alan)
        if a != b:
            out.append(("Machine", "~", "%s: %r -> %r" % (alan, a, b)))
    return out


def class_model_diff(eski_metin: str, yeni_metin: str) -> List[DiffRow]:
    """Iki sinif diyagrami surumunu karsilastirir."""
    eski = _yukle(eski_metin) or {}
    yeni = _yukle(yeni_metin) or {}

    siniflar = {}
    for d in list(eski.get("classes") or []) + list(yeni.get("classes") or []):
        if isinstance(d, dict):
            siniflar[d.get("id")] = d

    def sinif_etiketi(d: dict) -> str:
        damga = d.get("stereotype") or ""
        on = ("«%s» " % damga) if damga and damga != "none" else ""
        return "%s%s" % (on, d.get("name", "?"))

    def iliski_etiketi(d: dict) -> str:
        kaynak = siniflar.get(d.get("source", ""), {}).get("name", "?")
        hedef = siniflar.get(d.get("target", ""), {}).get("name", "?")
        return "%s  %s  %s" % (kaynak, d.get("kind", "association"), hedef)

    satirlar: List[DiffRow] = []
    satirlar += _karsilastir("Classes", eski.get("classes") or [],
                             yeni.get("classes") or [], sinif_etiketi)
    satirlar += _karsilastir("Relations", eski.get("relations") or [],
                             yeni.get("relations") or [], iliski_etiketi)
    return satirlar


def diff_for(path: str, eski_metin: str, yeni_metin: str) -> Optional[List[DiffRow]]:
    """Dosya turune gore anlamsal fark; model dosyasi degilse ``None``.

    ``None`` donmesi "metin farkini goster" demektir -- uretilen C/C++
    dosyalari icin dogru olan budur, orada satir bazli fark zaten okunur.
    """
    ad = path.lower()
    if ad.endswith(".usm"):
        return state_machine_diff(eski_metin, yeni_metin)
    if ad.endswith(".ucd"):
        return class_model_diff(eski_metin, yeni_metin)
    if ad.endswith(".json"):
        # Uzanti belirsiz: ICERIGE bak.
        veri = _yukle(yeni_metin) or _yukle(eski_metin) or {}
        if isinstance(veri.get("classes"), list):
            return class_model_diff(eski_metin, yeni_metin)
        if isinstance(veri.get("states"), list):
            return state_machine_diff(eski_metin, yeni_metin)
    return None


def render(rows: List[DiffRow]) -> str:
    """Satirlari, arayuzun renklendirebilecegi duz metne cevirir.

    Bicim `git diff` ile UYUMLUDUR: satir basindaki '+' / '-' isaretleri
    mevcut renklendiriciyi oldugu gibi kullanir; ayri bir renk yolu
    yazmak, iki yerde bakim demek olurdu.
    """
    if not rows:
        return ""
    out: List[str] = []
    son_bolum = None
    for bolum, isaret, metin in rows:
        if bolum != son_bolum:
            if out:
                out.append("")
            out.append("@@ %s @@" % bolum)
            son_bolum = bolum
        out.append("%s %s" % (isaret, metin) if isaret != " " else "  " + metin)
    return "\n".join(out)


def summary(rows: List[DiffRow]) -> Tuple[int, int, int]:
    """(eklenen, silinen, degisen) sayilari."""
    art = sum(1 for _b, s, _t in rows if s == "+")
    eksi = sum(1 for _b, s, _t in rows if s == "-")
    degisen = sum(1 for _b, s, _t in rows if s == "~")
    return art, eksi, degisen
