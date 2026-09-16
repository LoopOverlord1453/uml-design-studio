"""PlantUML disa aktarimi -- TUVALDEKI CIZIMIN AYNISI.

Diyagrami dokumanlara gomulebilecek metinsel bir bicime cevirir. Kod
uretimiyle ayni modelden beslendigi icin dokuman ile kod arasinda kayma
olmaz.

YASANAN IKI HATA:

1. Her gecis icin yalnizca `-->` yaziliyordu. PlantUML yon bilgisi
   olmayan gecisleri yukaridan asagiya dizer; kullanici YATAY cizdigi
   makineyi DIKEY ve karisik bir sekilde geri aliyordu.
2. Duzeltmenin ilk halinde genis modellere `left to right direction`
   ekledik ve cizim bu kez 90 DERECE DONDU. Cunku PlantUML bu
   yonergeyi GraphViz'e `rankdir=LR` diye gecirir, `-right->` oku ise
   "ayni rank" demektir: dikey akista ayni rank yan yanadir, yatay
   akista ALT ALTA. Yan yana cizilen LedOn/LedOff alt alta indi.
   Kuresel yon yonergesi ile ok yonu ipuclari BIRLIKTE KULLANILMAZ.
Ayrica tarih/junction/terminate sozde-durumlari duz kutu olarak, ic
gecisler ise ok olarak ciziliyordu -- ikisi de UML'e ve uretilen koda
aykiri; serbest `note` de yerlesimi daginitiyordu.

Bu surum modeldeki KONUMLARI kullanir:

  * her gecis icin kaynak ve hedefin gercek koordinatlarindan bir yon
    secilir (`-right->`, `-down->`, ...),
  * durumlar tuvaldeki okuma sirasina gore yayimlanir.

Boylece PlantUML'in urettigi resim tuvaldeki yerlesimi izler.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from ..core.model import (State, StateKind, StateMachine, Transition,
                          TransitionKind)

#: PlantUML'de tirnaksiz yazilabilecek ad.
_SADE_AD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: Sozde-durumlarin PlantUML karsiliklari.
#:
#: PlantUML'de junction icin ayri bir gosterim yoktur; choice ile ayni
#: elmas kullanilir. Ayrimi kaybetmemek icin govdeye bir satir dusulur.
_STEREOTIP = {
    StateKind.CHOICE: "<<choice>>",
    StateKind.JUNCTION: "<<choice>>",
    StateKind.TERMINATE: "<<end>>",
    # PlantUML fork/join icin ayri bir bicem tasir; kalin cubuk olarak
    # cizer ve boylece resim, tuvaldeki gosterimle ortusur.
    StateKind.FORK: "<<fork>>",
    StateKind.JOIN: "<<join>>",
    StateKind.ENTRY_POINT: "<<entryPoint>>",
    StateKind.EXIT_POINT: "<<exitPoint>>",
    StateKind.SUBMACHINE: "<<sdlreceive>>",
}

#: Ok ucu olarak gorunen, ayrica BILDIRILMEYEN sozde-durumlar.
_UC_OLARAK = (StateKind.INITIAL, StateKind.FINAL,
              StateKind.SHALLOW_HISTORY, StateKind.DEEP_HISTORY)


def _esc(text: str) -> str:
    return " ".join(str(text).split()).replace('"', "'")


def _kimlik(name: str, used: Dict[str, str]) -> str:
    """Ad -> PlantUML tanimlayicisi (bosluklu adlar da calissin)."""
    if name in used:
        return used[name]
    if _SADE_AD.match(name):
        used[name] = name
        return name
    temiz = re.sub(r"[^A-Za-z0-9_]", "_", name) or "S"
    if temiz[0].isdigit():
        temiz = "S" + temiz
    aday, i = temiz, 2
    while aday in used.values():
        aday = "%s_%d" % (temiz, i)
        i += 1
    used[name] = aday
    return aday


def _abs_center(sm: StateMachine, s: State) -> Tuple[float, float]:
    """Durumun SAHNEDEKI merkezi.

    Alt durumlarin x/y'si ust durumun icine goredir; yon hesabi mutlak
    konum ister, yoksa ic ice yapilarda yonler yanlis cikardi.
    """
    x, y = s.x, s.y
    ust = sm.parent_of(s.id)
    gorulen = set()
    while ust is not None and ust.id not in gorulen:
        gorulen.add(ust.id)
        x += ust.x
        y += ust.y
        ust = sm.parent_of(ust.id)
    return x + s.w / 2.0, y + s.h / 2.0


def _dikey_aralik(sm: StateMachine, s: State):
    """Durumun SAHNEDEKI dikey araligi (ust, alt)."""
    _cx, cy = _abs_center(sm, s)
    return cy - s.h / 2.0, cy + s.h / 2.0


#: Kutunun "orta bandi": yuksekliginin ortadaki bu kadarlik dilimi.
#:
#: Tam aralik kullanmak, 290 piksellik bir bilesik durumun ekrandaki
#: hemen her seyle ortusmesine yol aciyordu; Check elmasi Running'in ALT
#: KENARINA degdigi icin "ayni bant" sayiliyordu. Orta band, "gozle ayni
#: satirda mi" sorusunun daha iyi bir karsiligidir.
_ORTA_BAND = 0.6


def yatay_mi(ust0: float, alt0: float, ust1: float, alt1: float) -> bool:
    """Iki kutu AYNI YATAY BANTTA mi duruyor?

    PlantUML'de `-right->`/`-left->` "ayni rank", `-down->`/`-up->` ise
    "sonraki rank" demektir. Bu yuzden dogru soru "hangi eksende daha
    uzak" degil, "ayni satirda mi" sorusudur.

    Yanlis cevap CELISKILI KISIT uretir: Off ile Running ayni rank,
    Running ile Check ayni rank, ama Check'in bir ust ranki Off deniyordu.
    GraphViz boyle bir kisit kumesinde birini atar ve yerlesim bozulur.
    """
    def band(ust: float, alt: float):
        orta = (ust + alt) / 2.0
        yari = (alt - ust) * _ORTA_BAND / 2.0
        return orta - yari, orta + yari

    a0, a1 = band(ust0, alt0)
    b0, b1 = band(ust1, alt1)
    return min(a1, b1) >= max(a0, b0)


def _yon(sm: StateMachine, src: State, tgt: State) -> str:
    """Iki durum arasindaki oku TUVALDEKI yone gore isaretler."""
    x0, y0 = _abs_center(sm, src)
    x1, y1 = _abs_center(sm, tgt)
    ust0, alt0 = _dikey_aralik(sm, src)
    ust1, alt1 = _dikey_aralik(sm, tgt)
    if yatay_mi(ust0, alt0, ust1, alt1):
        return "-right->" if (x1 - x0) >= 0 else "-left->"
    return "-down->" if (y1 - y0) >= 0 else "-up->"


def _label(t: Transition) -> str:
    lbl = t.label()
    return (" : %s" % _esc(lbl)) if lbl else ""


def generate_plantuml(sm: StateMachine, resolve=None) -> Dict[str, str]:
    # ALTMAKINE REFERANSLARI RESIMDE DE ACILIR.
    #
    # Kapali bir kutu cizmek, resmin uretilen koddan BASKA bir seyi
    # anlatmasi demek olurdu: kod genisletilmis makineyi uretir. UML de
    # altmakineyi "macro-like insertion" olarak tanimlar (14.2.3.4.7).
    from ..core.submachine import has_submachine, flatten
    if resolve is not None and has_submachine(sm):
        try:
            sm = flatten(sm, resolve)
        except Exception:                       # noqa: BLE001
            pass                                # cizim uretimi engellenmez

    kimlikler: Dict[str, str] = {}

    alias: Dict[str, str] = {}
    for s in sm.states.values():
        if s.kind in (StateKind.INITIAL, StateKind.FINAL):
            alias[s.id] = "[*]"
        elif s.kind.is_history:
            continue            # ikinci turda: SAHIBIYLE nitelenir
        else:
            alias[s.id] = _kimlik(s.name, kimlikler)

    # TARIH DUGUMU SAHIBIYLE NITELENIR.
    #
    # Ciplak `[H]`, PlantUML'de ICINDE YAZILDIGI bolgenin tarihidir. Oklar
    # en ust duzeyde yayimlandigi icin her tarih ayni KOK dugume
    # baglaniyordu: iki ayri bilesik durumun tarihi tek bir daireye
    # cokuyor ve resim modeli YANLIS anlatiyordu. `GrpA[H]` bicimi
    # her yerde gecerlidir ve hangi durumun tarihi oldugunu soyler.
    #
    # Ikinci tur sart: sahibin takma adi ilk turda uretilir ve durumlar
    # sozlukte herhangi bir sirada gelebilir.
    for s in sm.states.values():
        if not s.kind.is_history:
            continue
        isaret = "[H*]" if s.kind is StateKind.DEEP_HISTORY else "[H]"
        sahip = alias.get(s.parent) if s.parent else None
        if sahip:
            alias[s.id] = "%s%s" % (sahip, isaret)
        else:
            # Kok bolgede tarih UML'de gecersizdir (V064); yine de
            # cizime bir sey koymak, sessizce atmaktan iyidir.
            alias[s.id] = isaret

    # -- Yerlesim yonu ------------------------------------------------------- #
    #
    # `left to right direction` YAZILMAZ. Ilk denemede yazmistik ve cizim
    # 90 derece DONDU: PlantUML bu yonergeyi GraphViz'e `rankdir=LR` diye
    # gecirir; `-right->` oku ise "ayni rank" demektir. Dikey akista ayni
    # rank yan yanadir, yatay akista ALT ALTA. Sonucta yan yana cizilen
    # LedOn/LedOff alt alta dizildi, Fault sol altta iken sag uste cikti.
    #
    # Yon bilgisini zaten HER OK kendisi tasiyor (bkz. _yon); kuresel
    # yonerge hem gereksiz hem zararli. `yatay` yalnizca BILDIRIM SIRASI
    # icin kullanilir: PlantUML esit kosullarda yazim sirasini korur.
    kutular = [s for s in sm.states.values()
               if s.kind.is_real_state or s.kind.is_branch]
    yatay = False
    if kutular:
        xs = [_abs_center(sm, s)[0] for s in kutular]
        ys = [_abs_center(sm, s)[1] for s in kutular]
        yatay = (max(xs) - min(xs)) > (max(ys) - min(ys))

    L: List[str] = ["@startuml"]
    if sm.name:
        L.append("title %s" % _esc(sm.name))
    L += [
        "hide empty description",
        "skinparam backgroundColor #2B2D30",
        "skinparam defaultFontColor #A9B7C6",
        "skinparam ArrowFontColor #A9B7C6",
        "skinparam TitleFontColor #A9B7C6",
        "skinparam CaptionFontColor #A9B7C6",
        "skinparam state {",
        "  BackgroundColor #3C3F41",
        "  BorderColor #6B7079",
        "  FontColor #A9B7C6",
        "  ArrowColor #CC7832",
        "  StartColor #A9B7C6",
        "  EndColor #A9B7C6",
        "}",
        "",
    ]

    # Bir bolgenin ICINDE kalan gecisler o blogun icine yazilir; disarida
    # ikinci kez yayimlanmamalari icin onceden isaretlenir.
    ic_bolge = set()
    for t in sm.transitions.values():
        src = sm.states.get(t.source)
        tgt = sm.states.get(t.target)
        if src is None or tgt is None or t.kind is TransitionKind.INTERNAL:
            continue
        if src.parent is not None and src.parent == tgt.parent:
            ic_bolge.add(t.id)

    def ic_gecis_satirlari(s: State) -> List[str]:
        """Durumun IC gecisleri: ok degil, govdede bir satir.

        UML 2.5.1'e gore ic gecis ne cikis ne giris eylemi calistirir;
        uretilen C/C++ kodu da boyle davranir. Ok olarak cizmek resmi
        kodla CELISKIYE dusururdu.
        """
        out = []
        for t in sm.outgoing(s.id):
            if t.kind is TransitionKind.INTERNAL:
                out.append("%s : %s" % (alias[s.id],
                                        _esc(t.label()) or "internal"))
        return out

    def govde(s: State, pad: str) -> None:
        for beh, tag in ((s.entry, "entry"), (s.exit, "exit"), (s.do, "do")):
            if beh.strip():
                L.append("%s%s : %s / %s" % (pad, alias[s.id], tag, _esc(beh)))
        for satir in ic_gecis_satirlari(s):
            L.append("%s%s" % (pad, satir))

    def bas_satiri(s: State, pad: str, acik: bool) -> str:
        """`state X` / `state "Ad" as X` satirini kurar."""
        if alias[s.id] == s.name:
            metin = "%sstate %s" % (pad, s.name)
        else:
            metin = '%sstate "%s" as %s' % (pad, _esc(s.name), alias[s.id])
        stereo = _STEREOTIP.get(s.kind, "")
        if stereo:
            metin += " " + stereo
        if acik:
            metin += " {"
        return metin

    def cizim_sirasi(parent: Optional[str],
                     bolge: Optional[int] = None) -> List[State]:
        """Cocuklari TUVALDEKI okuma sirasina gore verir.

        `bolge` verilirse YALNIZCA o bolgedekiler dondurulur; ortogonal
        bir durumun bolgeleri PlantUML'de "--" ile ayri ayri yazilir.
        """
        if bolge is None:
            cocuklar = list(sm.sorted_children(parent))
        else:
            cocuklar = list(sm.children_in(parent, bolge))
        if yatay:
            return sorted(cocuklar, key=lambda s: (s.x, s.y))
        return sorted(cocuklar, key=lambda s: (s.y, s.x))

    def emit_region(parent: Optional[str], pad: str,
                    bolge: Optional[int] = None) -> None:
        for s in cizim_sirasi(parent, bolge):
            if s.kind in _UC_OLARAK:
                # Bunlarin PlantUML'de ayri bir bildirimi yoktur; yalnizca
                # ok ucu olarak ([*], [H], [H*]) gorunurler.
                continue
            if s.kind is StateKind.COMPOSITE:
                L.append(bas_satiri(s, pad, acik=True))
                # ORTOGONAL durumun bolgeleri "--" ile ayrilir; PlantUML
                # bunu es zamanli bolge olarak cizer. Tek bolgeli durumda
                # ayirici yazilmaz ve cikti eskisiyle ayni kalir.
                bolge_sayisi = sm.region_count(s.id)

                def ic_gecisler(sahip: str, hangi: Optional[int]) -> None:
                    """Sahibin (istege bagli olarak bir bolgesinin) ic oklari.

                    Oklar KENDI bolge blogunda yazilmalidir: hepsi en sona
                    yazilsaydi PlantUML onlari SON bolgeye koyar ve resim
                    modeli yanlis anlatirdi.
                    """
                    for t in sm.ordered_transitions():
                        if t.id not in ic_bolge:
                            continue
                        src = sm.states[t.source]
                        tgt = sm.states[t.target]
                        if src.parent != sahip:
                            continue
                        if hangi is not None and sm.region_of(src.id) != hangi:
                            continue
                        L.append("%s  %s %s %s%s"
                                 % (pad, alias[src.id], _yon(sm, src, tgt),
                                    alias[tgt.id], _label(t)))

                if bolge_sayisi > 1:
                    for r in range(bolge_sayisi):
                        if r > 0:
                            L.append("%s  --" % pad)
                        emit_region(s.id, pad + "  ", r)
                        ic_gecisler(s.id, r)
                else:
                    emit_region(s.id, pad + "  ")
                    ic_gecisler(s.id, None)
                L.append("%s}" % pad)
            else:
                L.append(bas_satiri(s, pad, acik=False))
                if s.kind is StateKind.JUNCTION:
                    # Elmas choice ile ayni; ayrimi yazi ile koru.
                    L.append("%s%s : <<junction>>" % (pad, alias[s.id]))
            govde(s, pad)

    emit_region(None, "")
    L.append("")

    for t in sm.ordered_transitions():
        if t.id in ic_bolge or t.kind is TransitionKind.INTERNAL:
            continue
        src = sm.states.get(t.source)
        tgt = sm.states.get(t.target)
        if src is None or tgt is None:
            continue
        L.append("%s %s %s%s" % (alias[src.id], _yon(sm, src, tgt),
                                 alias[tgt.id], _label(t)))

    if sm.description:
        # Serbest bir `note` yerlesimi bozuyordu: PlantUML onu rastgele bir
        # kosede tutup oklari uzatiyordu. `caption` resmin ALTINA yazilir.
        L += ["", "caption %s" % _esc(sm.description)]

    L += ["", "@enduml", ""]
    return {"%s.puml" % sm.prefix: "\n".join(L)}
