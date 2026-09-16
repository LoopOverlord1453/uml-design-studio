"""Submachine durumlari: baska bir makineyi MAKRO gibi yerine koyar.

UML 2.5.1, 14.2.3.4.7 (basili s.311):

    "A submachine State implies a macro-like insertion of the specification
     of the corresponding submachine StateMachine."

Ayni bolum, altmakinelerin "like programming language macros, distinct
Behavior specifications, which may be defined in a different context than
the one where they are used" oldugunu soyler. Bu yuzden referans BASKA BIR
DOSYAYA gider ve kod uretiminden ONCE yerine konur.

NEDEN AYRI MODUL URETMEK YERINE DUZLESTIRME
-------------------------------------------
Altmakineyi ayri bir C modulu olarak uretmek ilk bakista daha ekonomik
gorunur, ama uc somut sorunu vardir:

* Gonderim OZYINELEMELI olurdu: dis makinenin `take` islevi ic makinenin
  `dispatch` islevini cagirir. Yigin tuketimi MODELE baglanir ve gomulu
  hedefte statik olarak siniranamaz -- bu aracin butun tasariminin
  dayandigi guvence tam da budur.
* Oncelik kurali KURESELDIR (14.2.3.9.4): derin bir durumdan cikan gecis,
  onu kapsayandan cikanla cakisir ve derin olan kazanir. Iki ayri modul
  bunu ancak elle kurulmus iki asamali bir protokolle taklit edebilirdi.
* Ayni altmakineye yapilan IKI REFERANS AYRI ornektir (14.2.3.4.7 NOTE:
  "Each submachine State represents a distinct instantiation of a
  submachine"). Paylasilan modul durumu ikisini birbirine baglar; bu,
  sessizce yanlis davranan bir hatadir.

Duzlestirmenin bedeli GERCEKTIR: N referans, K dugumlu bir makineyi N kez
cogaltir. Bu bedel gizlenmez -- genisleme sonrasi dugum ve gecis sayisi
uretilen dosyanin basligina yazilir.

NE DESTEKLENMEZ
---------------
Altmakine durumunun UZERINDEKI adlandirilmis baglanti noktalari
(ConnectionPointReference, 14.2.3.5) su an DESTEKLENMEZ ve acikca
REDDEDILIR. Referans edilen makinenin giris/cikis noktalarini disaridaki
bir oka baglamak, tuvalde o noktalari altmakine durumunun sinirinda
gosterecek bir arayuz gerektirir; yarim bir destek, kullanicinin cizdigi
okun sessizce baska bir yere gitmesi demek olurdu. Belge de bu notasyonun
varsayilan giris ve tamamlanmayla cikis icin GEREKMEDIGINI soyler
(14.2.4.4.2, basili s.323).
"""

from __future__ import annotations

import copy
import os
from typing import Callable, Dict, List, Optional, Set

from .model import State, StateKind, StateMachine

#: Ic ice altmakine derinligi ust siniri.
#:
#: UML boyle bir sinir KOYMAZ; bu bir ARAC KURALIDIR. Makro genislemesi
#: dongusel bir referans grafiginde durmaz, ayrica her seviye dugum
#: sayisini carpar. Sinir, kullaniciya anlasilir bir hata vermek icindir.
MAX_SUBMACHINE_DEPTH = 8


class SubmachineError(Exception):
    """Altmakine referansi cozulemedi."""


#: `resolve(ref) -> StateMachine | None` bicimindeki cozumleyici.
Resolver = Callable[[str], Optional[StateMachine]]


def submachine_states(sm: StateMachine) -> List[State]:
    """Makinedeki altmakine durumlari (kararli sirada)."""
    return [s for s in sm.ordered_states()
            if s.kind is StateKind.SUBMACHINE]


def has_submachine(sm: StateMachine) -> bool:
    return any(s.kind is StateKind.SUBMACHINE for s in sm.states.values())


def normalise_ref(ref: str) -> str:
    """Referansi karsilastirilabilir tek bir bicime indirger."""
    return os.path.normcase(os.path.normpath((ref or "").strip()))


def qualified_name(disari: str, iceri: str) -> str:
    """Genisletilmis dugumun adi.

    Ad, uretilen C tanimlayicisinin parcasi oldugu icin GECERLI BIR
    TANIMLAYICI kalmalidir; bu yuzden belgenin gosterimdeki "::" ayraci
    yerine alt cizgi kullanilir. Ayni altmakineye yapilan iki referans
    boylece farkli adlar uretir ve V010 ad cakismasi kurali ikisini
    birbirinden ayirabilir.
    """
    return "%s_%s" % (disari, iceri)


def flatten(sm: StateMachine, resolve: Resolver,
            _derinlik: int = 0,
            _yigin: Optional[Set[str]] = None) -> StateMachine:
    """Altmakine referanslarini YERINE KOYAR; yeni bir makine dondurur.

    Girdi DEGISTIRILMEZ: cagiran taraf kullanicinin belgesini elinde
    tutar, uretim ise genisletilmis kopyayla calisir.
    """
    if not has_submachine(sm):
        return sm
    if _derinlik >= MAX_SUBMACHINE_DEPTH:
        raise SubmachineError(
            "Submachine references are nested more than %d levels deep."
            % MAX_SUBMACHINE_DEPTH)

    yigin = set(_yigin or set())
    sonuc = copy.deepcopy(sm)

    for dis in submachine_states(sm):
        ref = (dis.submachine_ref or "").strip()
        if not ref:
            raise SubmachineError(
                "The submachine state '%s' does not reference a machine."
                % dis.name)
        anahtar = normalise_ref(ref)
        if anahtar in yigin:
            raise SubmachineError(
                "The submachine reference of '%s' is circular: '%s' is "
                "already being expanded." % (dis.name, ref))

        ic = resolve(ref)
        if ic is None:
            raise SubmachineError(
                "The machine referenced by '%s' could not be found: %s"
                % (dis.name, ref))
        ic = flatten(ic, resolve, _derinlik + 1, yigin | {anahtar})

        _yerine_koy(sonuc, dis.id, ic)

    return sonuc


def _yerine_koy(hedef: StateMachine, dis_id: str, ic: StateMachine) -> None:
    """Altmakine durumunu, ic makinenin icerigiyle doldurulmus bir
    BILESIK duruma cevirir."""
    dis = hedef.states[dis_id]
    dis_ad = dis.name

    # Disaridaki dugum artik siradan bir bilesik durumdur. entry/exit/do
    # KORUNUR: UML'de altmakine durumu bunlari tasiyabilir.
    dis.kind = StateKind.COMPOSITE
    dis.regions = max(1, ic.region_count(None))

    ad_esleme: Dict[str, str] = {}
    for s in ic.ordered_states():
        yeni = copy.deepcopy(s)
        yeni.id = "%s__%s" % (dis_id, s.id)
        yeni.name = qualified_name(dis_ad, s.name)
        # Ic makinenin KOK dugumleri, disaridaki durumun cocuklari olur.
        yeni.parent = ("%s__%s" % (dis_id, s.parent)) if s.parent else dis_id
        # Konum, disaridaki durumun icine tasinir; tuvalde makul dursun.
        yeni.x = float(s.x) + 14.0
        yeni.y = float(s.y) + 34.0
        ad_esleme[s.id] = yeni.id
        hedef.add_state(yeni)

    for t in ic.ordered_transitions():
        yeni = copy.deepcopy(t)
        yeni.id = "%s__%s" % (dis_id, t.id)
        yeni.source = ad_esleme.get(t.source, t.source)
        yeni.target = ad_esleme.get(t.target, t.target)
        hedef.add_transition(yeni)

    _includes_birlestir(hedef, ic)


def _includes_birlestir(hedef: StateMachine, ic: StateMachine) -> None:
    """Ic makinenin include satirlarini disaridakine EKLER.

    Ic makinenin entry / exit / do metinleri ve koruma ifadeleri AYNEN
    kopyalanir. O metinlerin cagirdigi islevler ic makinenin KENDI
    include satirlarinda bildirilmistir; satirlar tasinmazsa uretilen
    dosya bildirimi olmayan islevleri cagirir.

    Bu kayip, arac hicbir sey soylemeden MUSTERININ DERLEYICISINDE
    ortaya cikardi. Kullanicinin iki diyagrami da tek basina kusursuz
    uretim yapar; yalnizca biri otekinin icine kondugunda bozulur.

    Ayni satir iki kez yazilmaz ve SIRA korunur: once disaridaki
    makinenin satirlari, sonra ic makineden gelen yeni olanlar.
    """
    satirlar = (hedef.user_includes or "").splitlines()
    gorulen = {ln.strip() for ln in satirlar if ln.strip()}
    eklenen = []
    for ln in (ic.user_includes or "").splitlines():
        anahtar = ln.strip()
        if anahtar and anahtar not in gorulen:
            gorulen.add(anahtar)
            eklenen.append(ln)
    if eklenen:
        hedef.user_includes = "\n".join(satirlar + eklenen).strip("\n")


def workspace_resolver(ws, acik: Optional[Dict[str, StateMachine]] = None
                       ) -> Resolver:
    """Calisma alanindaki dosyalari cozen bir cozumleyici uretir.

    ACIK BELGELER ONCELIKLIDIR. Kullanici referans edilen makineyi baska
    bir sekmede DEGISTIRDIYSE ve henuz kaydetmediyse, diskteki eski kopya
    kullanilirsa uretilen kod kullanicinin ekranda gordugu diyagrama
    uymaz. Bu, fark edilmesi en zor hata turudur.
    """
    onbellek: Dict[str, StateMachine] = {}
    acik_map = {normalise_ref(k): v for k, v in (acik or {}).items()}

    def cozumle(ref: str) -> Optional[StateMachine]:
        anahtar = normalise_ref(ref)
        if anahtar in acik_map:
            return acik_map[anahtar]
        if anahtar in onbellek:
            return onbellek[anahtar]
        if ws is None:
            return None
        try:
            yol = ws.resolve(ref)
        except Exception:                       # noqa: BLE001
            return None
        if not os.path.isfile(yol):
            return None
        try:
            with open(yol, encoding="utf-8") as fh:
                makine = StateMachine.from_json(fh.read())
        except Exception:                       # noqa: BLE001
            return None
        onbellek[anahtar] = makine
        return makine

    return cozumle
