"""Kod uretimi icin ara temsil (Intermediate Representation).

Diyagram modelini, hedef dilden bagimsiz, *duz* ve *deterministik* tablolara
cevirir. Bu adim sayesinde C ve C++ ureteci ayni anlamsal cikarimi paylasir;
iki ureteclerin davranisi ayrisamaz.

Onemli donusumler:
  * Initial sozde-durumlari elenir; her bolge icin (initial_child, initial_action)
    tablolarina gomulur.
  * Choice sozde-durumu gercek (gecici) bir duruma donusur; cikislari
    "completion" olayi ile tetiklenir.
  * Ayni guard/action metni tek bir kimlige indirgenir (kod boyutu).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..core.model import StateKind, StateMachine, TransitionKind
from ..core.text_layout import expand_breaks

NONE = 0xFF          # gecersiz durum indeksi (uint8 sentinel)
MAX_VERTICES = 254   # NONE sentinel'i icin bir yer birakiyoruz

#: Gecersiz BOLGE indeksi. Durum indeksinden AYRI bir sayi uzayidir;
#: ayni sentinel'i iki uzayda paylasmak, iki uzaydan biri buyudugunde
#: fark edilmesi cok zor bir birim kaymasi uretirdi.
REGION_NONE = 0xFF
MAX_REGIONS = 254

#: En cok olay turu. Olay indisi uretilen kodda `uint8_t`tir; 0 numara
#: COMPLETION'a ayrilmistir ve 255 INVALID sentinel'idir. Sinir
#: DENETLENMIYORDU: 255. kullanici olayi sessizce INVALID ile ayni sayiyi
#: aliyor, otesi ise uint8_t'ye sigmiyordu. Uretilen kod derleniyor ama
#: yanlis olaya bakiyordu -- gomulu bir aygitta bulunmasi en zor hata
#: turu.
MAX_EVENTS = 254

MAX_JUNCTION_DEPTH = 8    # ic ice junction zincirinde en fazla adim
MAX_JUNCTION_PATHS = 64   # tek bir gecisin acilabilecegi en fazla yol

KIND_SIMPLE = 0
KIND_COMPOSITE = 1
KIND_FINAL = 2
KIND_CHOICE = 3        # choice ve junction ayni calisma zamani anlamina sahiptir
KIND_TERMINATE = 4     # terminate sozde-durumu: girilince makine sonlanir
KIND_HIST_SHALLOW = 5  # H  sozde-durumu (gecici; tarihce cozulur)
KIND_HIST_DEEP = 6     # H* sozde-durumu (gecici; tarihce cozulur)

TKIND_EXTERNAL = 0
TKIND_INTERNAL = 1
TKIND_LOCAL = 2


class CodegenError(Exception):
    """Kod uretimi sirasinda olusan, dogrulamayla yakalanamamis hata."""


def as_statement(code: str) -> str:
    """Kullanicinin yazdigi eylem metnini gecerli bir C/C++ deyimine cevirir.

    Arac, eylem alanlarinda sondaki noktali virgulu istege bagli birakir.
    Tanim burada, ureteclerin ORTAK katmanindadir: hem tek eylemler hem de
    junction yollarinda birlestirilen eylemler ayni kurala uymak zorundadir.
    """
    text = code.strip()
    if not text:
        return ""
    if text.endswith(";") or text.endswith("}"):
        return text
    return text + ";"


@dataclass
class IrState:
    index: int
    model_id: str
    name: str                     # kullanicinin verdigi ad (C tanimlayicisi)
    kind: int                     # KIND_*
    parent: int                   # NONE = kok
    depth: int
    entry: str = ""
    exit: str = ""
    do: str = ""
    initial_child: int = NONE     # bilesik durumun ILK bolgesinin varsayilani
    initial_action: int = -1      # initial gecisinin eylem kimligi
    history_default: int = NONE   # tarih sozde-durumunun varsayilan hedefi
    note: str = ""
    #: Bu durumda ERTELENEN olay indeksleri (UML deferrableTrigger).
    deferred: List[int] = field(default_factory=list)

    # -- bolgeler ----------------------------------------------------------- #
    #: Bu dugumun ICINDE durdugu bolgenin KURESEL indeksi.
    region: int = 0
    #: Bilesik durumun sahip oldugu ILK bolgenin kuresel indeksi.
    first_region: int = REGION_NONE
    #: Bilesik durumun sahip oldugu bolge sayisi (0 = bilesik degil).
    region_count: int = 0


@dataclass
class IrRegion:
    """Bir bolge (UML 2.5.1, 14.2.3.2 Region).

    Bolge, es zamanli etkin olabilen bir alt-konfigurasyonun kabidir.
    Calisma zamaninda her bolgenin KENDI etkin yaprak durumu ve KENDI
    tarih kaydi vardir.

    TARIH BOLGEYE gore anahtarlanir, ust duruma gore DEGIL: ortogonal bir
    durumda iki bolge ayni ust duruma aittir; tek bir kayit paylasilsaydi
    derin tarihle geri donuste bolgelerden biri otekinin durumunu geri
    yuklerdi.
    """

    index: int
    owner: int                    # NONE = kok bolge; degilse bilesik durum
    initial_state: int = NONE     # bolgenin varsayilan alt durumu
    initial_action: int = -1      # initial gecisinin eylem kimligi
    name: str = ""                # tanilar icin okunakli ad


@dataclass
class IrTransition:
    index: int
    model_id: str
    source: int
    target: int
    event: int                    # 0 = completion
    guard: int = -1
    action: int = -1
    kind: int = TKIND_EXTERNAL
    text: str = ""                # yorum satiri icin okunakli etiket

    # -- fork / join (UML 2.5.1, 14.2.3.7, basili s.313) -------------------- #
    #
    # Ikisi de junction gibi TABLOYA DUZLESTIRILIR; calisma zamaninda
    # ayri bir dugum turu yoktur. Boylece uretilen kod yeni bir durum
    # sinifi ogrenmek zorunda kalmaz.
    #
    #: FORK: girilen ortogonal durumun bolgelerinde ACIKCA girilecek
    #: dugumler. Adi gecmeyen bolgeler varsayilanlariyla baslar.
    fork_targets: List[int] = field(default_factory=list)
    #: JOIN: gecisin etkin olmasi icin AYNI ANDA etkin olmasi gereken
    #: kaynaklar. Bos ise siradan bir gecistir.
    join_sources: List[int] = field(default_factory=list)
    #: `Ir.extra_table()` tarafindan doldurulur: bu satirin fork/join
    #: ogelerinin ortak tablodaki baslangic dizini.
    extra_first: int = 0


#: C/C++ anahtar kelimeleri ve cagri gibi gorunen yapilar.
#:
#: `if (x)` bir islev cagrisi DEGILDIR; ayiklanmazsa kullaniciya
#: "if islevini yazmalisiniz" denirdi.
_ANAHTAR = {
    "if", "else", "for", "while", "switch", "case", "default", "do",
    "return", "break", "continue", "goto", "sizeof", "typedef", "struct",
    "union", "enum", "static", "const", "volatile", "extern", "inline",
    "signed", "unsigned", "void", "char", "short", "int", "long", "float",
    "double", "bool", "true", "false", "NULL", "nullptr", "static_cast",
    "reinterpret_cast", "const_cast", "dynamic_cast", "new", "delete",
    "this", "and", "or", "not",
}

#: `ad(` bicimindeki cagrilar. Once gelen `.`/`->`/`::` varsa UYE
#: cagrisidir (ctx->reset() gibi) ve kullanicinin baglamina aittir.
_CAGRI = re.compile(r"(?<![\w.>:])([A-Za-z_]\w*)\s*\(")


def _argument_sayisi(metin: str, acilis: int) -> int:
    """`(` konumundan baslayarak ust duzey virgullerle argumani sayar."""
    derinlik = 0
    sayi = 0
    gorulen = False
    i = acilis
    while i < len(metin):
        ch = metin[i]
        if ch in "([{":
            derinlik += 1
        elif ch in ")]}":
            derinlik -= 1
            if derinlik == 0:
                return (sayi + 1) if gorulen else 0
        elif ch == "," and derinlik == 1:
            sayi += 1
        elif not ch.isspace() and derinlik == 1:
            gorulen = True
        i += 1
    return sayi


def _dizgileri_bosalt(metin: str) -> str:
    """Dizgi ve karakter sabitlerinin ICINI bosaltir.

    Regex yerine elle taranir: kacis dizilerini (`"a\\"b"`) dogru ele alan
    bir desen yazmak, hem okunmasi zor hem de bu dosyada bir kez daha
    kacis kacirmaya acik. Uzunluk korunur, boylece konumlar kaymaz.
    """
    out = []
    tirnak = ""
    kacis = False
    for ch in metin:
        if tirnak:
            out.append(" " if ch != tirnak or kacis else ch)
            if kacis:
                kacis = False
            elif ch == chr(92):
                kacis = True
            elif ch == tirnak:
                tirnak = ""
            continue
        if ch in ('"', "'"):
            tirnak = ch
            out.append(ch)
            continue
        out.append(ch)
    return "".join(out)


def _cagrilar(metin: str):
    """Metindeki (ad, arguman_sayisi) cagrilarini verir."""
    # Dizgi icindeki parantezler arguman sayimini bozardi.
    temiz = _dizgileri_bosalt(metin)
    for m in _CAGRI.finditer(temiz):
        ad = m.group(1)
        if ad in _ANAHTAR:
            continue
        yield ad, _argument_sayisi(temiz, m.end() - 1)


@dataclass
class RequiredSymbol:
    """Kullanicinin saglamasi gereken bir dis islev."""

    name: str
    argc: int = 0
    in_guard: bool = False
    sites: List[str] = field(default_factory=list)

    def summary(self) -> str:
        """Belgeye yazilacak tek satirlik ozet."""
        arg = "no arguments" if self.argc == 0 else (
            "1 argument" if self.argc == 1 else "%d arguments" % self.argc)
        rol = ("used in a guard, so it must RETURN a value"
               if self.in_guard else "called as a statement")
        return "%s(): %s, %s" % (self.name, arg, rol)


@dataclass
class Ir:
    name: str
    prefix: str
    description: str
    context_type: str
    user_includes: List[str]

    states: List[IrState] = field(default_factory=list)
    regions: List[IrRegion] = field(default_factory=list)
    transitions: List[IrTransition] = field(default_factory=list)
    events: List[str] = field(default_factory=list)     # [0] = COMPLETION
    guards: List[str] = field(default_factory=list)
    actions: List[str] = field(default_factory=list)

    # Kaynak durumlara gore gruplanmis gecis araligi: state -> (first, count)
    tran_slice: Dict[int, Tuple[int, int]] = field(default_factory=dict)

    #: Kok bolgelerin kuresel indeksleri (su an tek oge).
    root_regions: List[int] = field(default_factory=list)
    max_depth: int = 1

    # ---------------------------------------------------------------- yardim #
    # Eski cagri noktalari icin: kok bolgenin varsayilani.
    @property
    def root_initial(self) -> int:
        if not self.root_regions:
            return NONE
        return self.regions[self.root_regions[0]].initial_state

    @property
    def root_initial_action(self) -> int:
        if not self.root_regions:
            return -1
        return self.regions[self.root_regions[0]].initial_action

    @property
    def region_count(self) -> int:
        return len(self.regions)

    def has_orthogonal(self) -> bool:
        """Modelde BIRDEN COK bolgeli bir durum var mi?"""
        return any(st.region_count > 1 for st in self.states)

    def regions_of(self, state_index: int) -> List[int]:
        """Bir bilesik durumun sahip oldugu bolgelerin kuresel indeksleri."""
        st = self.states[state_index]
        if st.region_count <= 0:
            return []
        return list(range(st.first_region, st.first_region + st.region_count))

    @property
    def state_count(self) -> int:
        return len(self.states)

    @property
    def event_count(self) -> int:
        return len(self.events)

    @property
    def tran_count(self) -> int:
        return len(self.transitions)

    def state_by_index(self, i: int) -> IrState:
        return self.states[i]

    def has_context(self) -> bool:
        return self.context_type.strip() not in ("", "void")

    def has_history(self) -> bool:
        return any(st.kind in (KIND_HIST_SHALLOW, KIND_HIST_DEEP)
                   for st in self.states)

    def required_functions(self) -> List["RequiredSymbol"]:
        """Kullanicinin KENDISININ yazmasi gereken islevler.

        Model icindeki entry/exit/do govdeleri, gecis eylemleri ve guard
        ifadeleri kullanicinin yazdigi C/C++ metinleridir; icindeki
        cagrilar uretec tarafindan TANIMLANMAZ. Bunlari soylemezsek
        kullanici eksikligi ancak baglama (link) asamasinda
        "undefined reference to `led_write`" diye ogrenir.

        Ad, KAC ARGUMANLA cagrildigi ve nerede gectigi toplanir. Tip
        cikarimi YAPILMAZ: yanlis bir prototip yazmak, hic yazmamaktan
        daha kotudur (ornegin `void f()` bildirimi gercek imzayi
        gizleyip sessiz bir uyumsuzluk yaratabilir). Bu yuzden uretilen
        dosyalarda bunlar BELGELENIR, bildirilmez.
        """
        bulunan: Dict[str, RequiredSymbol] = {}

        def tara(metin: str, nerede: str, guard: bool) -> None:
            for ad, argc in _cagrilar(metin):
                kayit = bulunan.get(ad)
                if kayit is None:
                    kayit = RequiredSymbol(name=ad, argc=argc)
                    bulunan[ad] = kayit
                kayit.argc = max(kayit.argc, argc)
                kayit.in_guard = kayit.in_guard or guard
                if nerede not in kayit.sites:
                    kayit.sites.append(nerede)

        for st in self.states:
            for govde, etiket in ((st.entry, "entry"), (st.exit, "exit"),
                                  (st.do, "do")):
                if govde.strip():
                    tara(govde, "%s / %s" % (st.name, etiket), False)
        for eylem in self.actions:
            tara(eylem, "transition effect", False)
        for kosul in self.guards:
            tara(kosul, "guard", True)
        return sorted(bulunan.values(), key=lambda r: r.name)

    def time_triggers(self):
        """(durum, olay, gecikme_ifadesi) uclulerinin listesi.

        Bir durumdan cikan `after(N)` tetikleyicili her gecis icin bir
        kayit. Uretilen kod, durumun GIRISINDE zamanlayiciyi baslatir ve
        CIKISINDA iptal eder; olayi posta eden kullanicinin kendisidir.

        IC GECISLER DE BURADADIR. Iki ayri soru vardir ve eskiden
        birbirine karistiriliyordu:

        1. Gecis atesleyince zamanlayici YENIDEN BASLAR MI? Ic gecis ne
           giris ne cikis calistirdigi icin baslamaz -- ve bu dogrudur.
        2. Duruma girilirken zamanlayici HIC BASLAR MI? Baslamalidir:
           durumun `after(N)` tetikleyicili bir gecisi vardir.

        Ikinci soru da "hayir" diye yanitlaniyordu: ic gecis listeden
        tumden ELENIYORDU. Sonuc, benzetimde calisan ama gomulu kodda
        HICBIR SEY YAPMAYAN bir diyagramdi. Uretilen dosyada o olayi
        posta etmesi gereken kanca yoktu, dolayisiyla olay hic dogmuyor
        ve ic gecis hic ateslenmiyordu. Aygitta sessizce olu kod, arayuzde
        calisir gorunen bir cizim.
        """
        from ..core.model import time_event_delay
        out = []
        gorulen = set()
        for t in self.transitions:
            if t.event == 0:
                continue
            gecikme = time_event_delay(self.events[t.event])
            if gecikme is None:
                continue
            anahtar = (t.source, t.event)
            if anahtar in gorulen:
                continue
            gorulen.add(anahtar)
            out.append((t.source, t.event, gecikme))
        return sorted(out)

    def has_time_events(self) -> bool:
        return bool(self.time_triggers())

    def has_deferred(self) -> bool:
        """Modelde ERTELENEN olay var mi?"""
        return any(st.deferred for st in self.states)

    def has_fork_join(self) -> bool:
        """Modelde fork ya da join var mi?

        Yoksa uretilen kodda ilgili tablolar ve dallar HIC yazilmaz;
        boylece basit modellerin ciktisi aynen eskisi gibi kalir.
        """
        return any(t.fork_targets or t.join_sources for t in self.transitions)

    def extra_table(self) -> List[int]:
        """Fork hedefleri ve join kaynaklarinin duzlestirilmis tablosu."""
        out: List[int] = []
        for t in self.transitions:
            t.extra_first = len(out)
            out.extend(t.fork_targets)
            out.extend(t.join_sources)
        return out

    def has_terminate(self) -> bool:
        return any(st.kind == KIND_TERMINATE for st in self.states)


# --------------------------------------------------------------------------- #

def _dedup_add(pool: List[str], text: str) -> int:
    """Ayni metni tekrar eklemez; kimligini dondurur. Bos metin -> -1."""
    t = text.strip()
    if not t:
        return -1
    try:
        return pool.index(t)
    except ValueError:
        pool.append(t)
        return len(pool) - 1


_KIND_MAP = {
    StateKind.SIMPLE: KIND_SIMPLE,
    StateKind.COMPOSITE: KIND_COMPOSITE,
    StateKind.FINAL: KIND_FINAL,
    StateKind.CHOICE: KIND_CHOICE,
    StateKind.JUNCTION: KIND_CHOICE,   # junction = statik choice; ayni tablo anlami
    StateKind.TERMINATE: KIND_TERMINATE,
    StateKind.SHALLOW_HISTORY: KIND_HIST_SHALLOW,
    StateKind.DEEP_HISTORY: KIND_HIST_DEEP,
}

_TKIND_MAP = {
    TransitionKind.EXTERNAL: TKIND_EXTERNAL,
    TransitionKind.INTERNAL: TKIND_INTERNAL,
    TransitionKind.LOCAL: TKIND_LOCAL,
}


def build_ir(sm: StateMachine, resolve=None) -> Ir:
    """Dogrulanmis bir modelden IR uretir.

    Not: `validate()` hatasiz gecmis olmali. Yine de burada savunmaci
    kontroller var; tutarsizlik CodegenError firlatir.

    ``resolve``: altmakine referanslarini cozen islev
    (``ref -> StateMachine``). Modelde altmakine varsa makine ONCE
    duzlestirilir: UML 2.5.1, 14.2.3.4.7 altmakineyi "macro-like
    insertion" olarak tanimlar, yani genisleme anlamin KENDISIDIR.
    """
    from ..core.submachine import SubmachineError, flatten, has_submachine

    if has_submachine(sm):
        if resolve is None:
            raise CodegenError(
                "This model references a submachine, which can only be "
                "resolved inside a workspace.")
        try:
            sm = flatten(sm, resolve)
        except SubmachineError as exc:
            raise CodegenError(str(exc))

    ir = Ir(
        name=sm.name,
        prefix=sm.prefix,
        description=sm.description,
        context_type=(sm.context_type or "void").strip() or "void",
        user_includes=[ln.strip() for ln in (sm.user_includes or "").splitlines() if ln.strip()],
    )

    # --- 1) Indekslenecek dugumler: initial DISINDA her sey ------------------ #
    # INITIAL gibi FORK ve JOIN de tabloya girmez: ucu de bilesik gecisin
    # icine duzlestirilir (bkz. asagidaki 5. adim).
    _ELENEN = (StateKind.INITIAL, StateKind.FORK, StateKind.JOIN,
               StateKind.ENTRY_POINT, StateKind.EXIT_POINT)
    vertices = [s for s in sm.ordered_states() if s.kind not in _ELENEN]
    if len(vertices) > MAX_VERTICES:
        raise CodegenError("This model has %d states; at most %d are supported."
                           % (len(vertices), MAX_VERTICES))
    index_of: Dict[str, int] = {s.id: i for i, s in enumerate(vertices)}

    for i, s in enumerate(vertices):
        parent_idx = NONE
        if s.parent is not None:
            if s.parent not in index_of:
                raise CodegenError("The parent of state '%s' could not be indexed." % s.name)
            parent_idx = index_of[s.parent]
        ir.states.append(IrState(
            index=i,
            model_id=s.id,
            name=s.name,
            kind=_KIND_MAP[s.kind],
            parent=parent_idx,
            depth=sm.depth(s.id),
            # SATIR SONU ISARETI GERCEK SATIR SONUNA CEVRILIR.
            # Isaret C'de dizge disinda gecerli degildir; oldugu gibi
            # yazilirsa uretilen kod derlenmez. Dizge icindekilere
            # dokunulmaz (bkz. core/text_layout).
            entry=expand_breaks(s.entry).strip(),
            exit=expand_breaks(s.exit).strip(),
            do=expand_breaks(s.do).strip(),
            note=s.note.strip(),
        ))

    ir.max_depth = max([st.depth for st in ir.states] or [0]) + 1

    # --- 2) Olay tablosu ----------------------------------------------------- #
    ir.events = ["COMPLETION"] + sm.events()
    if len(ir.events) - 1 > MAX_EVENTS:
        raise CodegenError(
            "This model declares %d event types; at most %d are supported."
            % (len(ir.events) - 1, MAX_EVENTS))
    event_index = {name: i for i, name in enumerate(ir.events)}

    # Erteleme listeleri olay indekslerine cevrilir.
    for i, s_ in enumerate(vertices):
        indeksler = []
        for ad in (s_.deferred or []):
            ad = str(ad).strip()
            if not ad:
                continue
            if ad not in event_index:
                raise CodegenError(
                    "State '%s' defers an unknown event: %s" % (s_.name, ad))
            if event_index[ad] not in indeksler:
                indeksler.append(event_index[ad])
        ir.states[i].deferred = sorted(indeksler)

    # ERTELEME MASKESI 32 BITLIKTIR.
    #
    # Maske, uretilen kodda durum basina TEK bir uint32 alanidir. Daha
    # genis bir olay tablosu icin bayt matrisi gerekirdi; bu, gomulu
    # hedefte STATE_COUNT x EVENT_COUNT bayt demek. Sinir ACIKCA
    # soylenir -- sessizce yanlis maske uretmektense reddedilir.
    if any(st.deferred for st in ir.states) and len(ir.events) > 32:
        raise CodegenError(
            "Deferred events are supported for up to 32 event types; this "
            "model has %d." % (len(ir.events) - 1))

    # --- 2b) BOLGE TABLOSU --------------------------------------------------- #
    #
    # Bolgeler KURESEL olarak numaralanir. Sira DETERMINISTIKTIR ve
    # calisma zamanindaki isleme sirasini da belirler: once kok bolgeler,
    # sonra her bilesik durumun bolgeleri, durum indeksi sirasiyla.
    # UML, ortogonal bolgelerin hangi sirayla islenecegini TANIMLAMAZ
    # (14.2.3.8.3); arac bu sirayi sabitler ve uretilen baslikta yazar,
    # boylece iki uretim ayni davranisi verir.
    for _ in range(sm.region_count(None)):
        ir.root_regions.append(len(ir.regions))
        ir.regions.append(IrRegion(index=len(ir.regions), owner=NONE,
                                   name="(root)"))
    for st in ir.states:
        if st.kind == KIND_COMPOSITE:
            st.first_region = len(ir.regions)
            st.region_count = sm.region_count(st.model_id)
            for r in range(st.region_count):
                ir.regions.append(IrRegion(index=len(ir.regions),
                                           owner=st.index,
                                           name="%s.%d" % (st.name, r)))
    if len(ir.regions) > MAX_REGIONS:
        raise CodegenError("This model has %d regions; at most %d are supported."
                           % (len(ir.regions), MAX_REGIONS))

    # Her dugum, icinde durdugu bolgenin KURESEL indeksini tasir.
    for i, s in enumerate(vertices):
        st = ir.states[i]
        if s.parent is None:
            st.region = ir.root_regions[min(sm.region_of(s.id),
                                            len(ir.root_regions) - 1)]
            continue
        ust = ir.states[index_of[s.parent]]
        if ust.region_count <= 0:
            raise CodegenError(
                "State '%s' is inside '%s', which owns no region; only a "
                "composite state can contain states." % (st.name, ust.name))
        st.region = ust.first_region + min(sm.region_of(s.id),
                                           ust.region_count - 1)

    # --- 3) Initial gecisleri tablolara gom ---------------------------------- #
    def compile_initial(region: Optional[str],
                        region_index: int = 0) -> Tuple[int, int]:
        init = sm.initial_of(region, region_index)
        if init is None:
            return NONE, -1
        outs = sm.outgoing(init.id)
        if not outs:
            return NONE, -1
        tr = outs[0]
        if tr.target not in index_of:
            raise CodegenError("The target of the initial transition could not be resolved.")
        tgt = sm.states.get(tr.target)
        if tgt is not None and (tgt.kind.is_history or tgt.kind is StateKind.TERMINATE):
            # Dogrulayici V054 bunu engeller; savunmaci kontrol.
            raise CodegenError(
                "An initial transition cannot target a history or terminate pseudostate.")
        return index_of[tr.target], _dedup_add(ir.actions,
                                              expand_breaks(tr.action))

    # HER BOLGENIN kendi varsayilan girisi vardir (14.2.3.2, basili s.307).
    for reg in ir.regions:
        if reg.owner == NONE:
            sahip_id = None
            yerel = reg.index - ir.root_regions[0]
        else:
            sahip = ir.states[reg.owner]
            sahip_id = sahip.model_id
            yerel = reg.index - sahip.first_region
        cocuk, eylem = compile_initial(sahip_id, yerel)
        if cocuk == NONE:
            if sahip_id is None:
                raise CodegenError("The root region has no initial pseudostate.")
            raise CodegenError(
                "Region %d of composite state '%s' has no initial pseudostate."
                % (yerel + 1, ir.states[reg.owner].name))
        reg.initial_state = cocuk
        reg.initial_action = eylem

    # Eski alanlar ILK bolgeyi gosterir; tek bolgeli modellerde bu tam
    # olarak onceki davranistir.
    for st in ir.states:
        if st.kind == KIND_COMPOSITE and st.region_count > 0:
            ilk = ir.regions[st.first_region]
            st.initial_child = ilk.initial_state
            st.initial_action = ilk.initial_action

    # --- 3b) Tarih (history) sozde-durumlarinin varsayilan hedefleri --------- #
    for st in ir.states:
        if st.kind in (KIND_HIST_SHALLOW, KIND_HIST_DEEP):
            if st.parent == NONE:
                raise CodegenError(
                    "History pseudostate '%s' must live inside a composite state." % st.name)
            outs = sm.outgoing(st.model_id)
            if outs:
                if outs[0].target not in index_of:
                    raise CodegenError(
                        "The default transition target of history '%s' could not be resolved." % st.name)
                st.history_default = index_of[outs[0].target]

    # --- 4) Junction zincirlerinin duzlestirilmesi --------------------------- #
    # UML 2.5.1, 14.2.3.4.4: junction *statik* bir dallanmadir -- guard'lari
    # bilesik gecis ISLETILMEDEN once degerlendirilir. Choice ise dinamiktir
    # (guard'lar gelen gecisin effect'inden SONRA bakilir). Bu farki calisma
    # zamaninda tasimak yerine junction yollari burada duzlestirilir: her yol,
    # guard'lari VE ile birlestirilmis tek bir gecise doner. Boylece motorun
    # "guard'i dogrula, sonra isle" akisi junction icin dogru semantigi verir
    # ve Python / C / C++ gerceklestirmeleri kendiliginden ayni davranir.
    junction_ids = {s.id for s in sm.states.values()
                    if s.kind is StateKind.JUNCTION}

    def _branch_guard(text: str) -> str:
        guard = expand_breaks(text).strip()
        return "" if guard.lower() == "else" else guard

    def _walk(target: str, guards: List[str], actions: List[str],
              depth: int, seen: frozenset):
        """Junction hedefini gercek hedeflere cozer (derinlik-oncelikli).

        Cozulemeyen bir yol SESSIZCE atilamaz: atilirsa kullanicinin cizdigi
        gecis IR'den dusup olay hicbir uyari olmadan yok sayilir ya da makine
        yanlis duruma gider. Bu yuzden dongu ve derinlik asimi CodegenError
        yukseltir (dogrulayici V074/V075 ile zaten daha once uyarir).
        """
        if target not in junction_ids:
            yield target, guards, actions
            return
        if target in seen:
            raise CodegenError(
                "The junction chain through '%s' loops; it never reaches "
                "a state." % sm.states[target].name)
        if depth >= MAX_JUNCTION_DEPTH:
            raise CodegenError(
                "The junction chain through '%s' is %d steps deep; at most "
                "%d are supported." % (sm.states[target].name, depth + 1,
                                       MAX_JUNCTION_DEPTH))
        for branch in sm.outgoing(target):
            yield from _walk(branch.target,
                             guards + [_branch_guard(branch.guard)],
                             actions + [expand_breaks(branch.action).strip()],
                             depth + 1, seen | {target})

    def _combine_guard(parts: List[str]) -> str:
        kept = [p for p in parts if p]
        if not kept:
            return ""
        if len(kept) == 1:
            return kept[0]
        return " && ".join("(%s)" % p for p in kept)

    def _combine_action(parts: List[str]) -> str:
        """Yol uzerindeki eylemleri tek bir govdede birlestirir.

        Her parca AYRI AYRI sonlandirilir. Arac noktali virgulsuz eylem
        yazmaya izin verir (uretecler sonuna kendisi ekler); parcalar ham
        haliyle alt alta yapistirilirsa 'cnt++' + 'hits++;' birlesir ve
        uretilen kod derlenmez.
        """
        return "\n".join(as_statement(p) for p in parts if p.strip())

    # --- 5) Gecis tablosu (kaynak durum sirasina gore gruplu) ---------------- #
    initial_ids = {s.id for s in sm.states.values() if s.kind is StateKind.INITIAL}
    history_ids = {s.id for s in sm.states.values() if s.kind.is_history}
    fork_ids = {s.id for s in sm.states.values() if s.kind is StateKind.FORK}
    join_ids = {s.id for s in sm.states.values() if s.kind is StateKind.JOIN}

    def _ortak_sahip(idler: List[str]) -> Optional[str]:
        """Verilen dugumlerin ORTAK ortogonal sahibi (yoksa None)."""
        if not idler:
            return None
        ortak = idler[0]
        for baska in idler[1:]:
            ortak = sm.lca(ortak, baska)
            if ortak is None:
                return None
        return ortak

    entry_ids = {s.id for s in sm.states.values()
                 if s.kind is StateKind.ENTRY_POINT}
    exit_ids = {s.id for s in sm.states.values()
                if s.kind is StateKind.EXIT_POINT}

    def _entry_cozumle(entry_id: str):
        """Giris noktasini (sahip, ic hedefler, segmentler) olarak cozer.

        UML 2.5.1, 14.2.3.7 (basili s.313) NOTE: "If multiple Regions are
        involved, the entry point acts as a fork Pseudostate." Bu yuzden
        giris noktasi TAM OLARAK fork gibi derlenir; tek bolgede de ayni
        makineyi kullanmak iki ayri kod yolu tutmaktan iyidir.
        """
        nokta = sm.states[entry_id]
        sahip = nokta.parent
        if sahip is None or sahip not in index_of:
            raise CodegenError(
                "The entry point '%s' is not owned by a composite state."
                % nokta.name)
        segmentler = sm.outgoing(entry_id)
        if not segmentler:
            raise CodegenError(
                "The entry point '%s' has no transition into the state."
                % nokta.name)
        hedefler = [t.target for t in segmentler]
        if any(h not in index_of for h in hedefler):
            raise CodegenError(
                "A transition leaving entry point '%s' could not be resolved."
                % nokta.name)
        sirali = sorted(hedefler, key=lambda h: sm.region_of(h))
        return sahip, [index_of[h] for h in sirali], segmentler

    def _exit_cozumle(exit_id: str):
        """Cikis noktasinin disariya giden gecisini dondurur."""
        nokta = sm.states[exit_id]
        cikislar = sm.outgoing(exit_id)
        if not cikislar:
            raise CodegenError(
                "The exit point '%s' has no transition out of the state."
                % nokta.name)
        cikis = cikislar[0]
        if cikis.target not in index_of:
            raise CodegenError(
                "The target of the transition leaving exit point '%s' could "
                "not be resolved." % nokta.name)
        return cikis

    def _fork_cozumle(fork_id: str):
        """Fork segmentlerini (sahip, hedef listesi) olarak cozer."""
        segmentler = sm.outgoing(fork_id)
        hedefler = [t.target for t in segmentler]
        sahip = _ortak_sahip(hedefler)
        if sahip is None or sahip not in index_of:
            raise CodegenError(
                "The fork '%s' does not target vertices inside one "
                "orthogonal state." % sm.states[fork_id].name)
        gecersiz = [h for h in hedefler if h not in index_of]
        if gecersiz:
            raise CodegenError(
                "A fork segment of '%s' could not be resolved."
                % sm.states[fork_id].name)

        def _bolge_sirasi(hedef: str) -> int:
            """Hedefin `sahip` altinda dustugu bolge."""
            cur = hedef
            adim = 0
            while cur is not None and adim <= len(sm.states) + 1:
                st_ = sm.states.get(cur)
                if st_ is None:
                    return 0
                if st_.parent == sahip:
                    return sm.region_of(cur)
                cur = st_.parent
                adim += 1
            return 0

        # Hedefler BOLGE sirasina gore girilir. Model dosyasindaki ok
        # sirasina birakilsaydi ayni diyagram iki farkli giris sirasi
        # uretebilir ve kod uretimi DETERMINISTIK olmazdi.
        sirali = sorted(hedefler, key=_bolge_sirasi)
        return sahip, [index_of[h] for h in sirali], segmentler

    def _join_cozumle(join_id: str):
        """Join segmentlerini (sahip, kaynak listesi, cikis gecisi) olarak cozer."""
        segmentler = [t for t in sm.transitions.values() if t.target == join_id]
        kaynaklar = [t.source for t in segmentler]
        sahip = _ortak_sahip(kaynaklar)
        if sahip is None or sahip not in index_of:
            raise CodegenError(
                "The join '%s' does not collect vertices from inside one "
                "orthogonal state." % sm.states[join_id].name)
        cikislar = sm.outgoing(join_id)
        if not cikislar:
            raise CodegenError(
                "The join '%s' has no outgoing transition."
                % sm.states[join_id].name)
        gecersiz = [k for k in kaynaklar if k not in index_of]
        if gecersiz:
            raise CodegenError(
                "A join segment of '%s' could not be resolved."
                % sm.states[join_id].name)
        sirali = sorted(kaynaklar, key=lambda k: index_of[k])
        return sahip, [index_of[k] for k in sirali], cikislar[0]
    idx = 0
    for st in ir.states:
        first = idx
        for tr in sm.outgoing(st.model_id):
            if tr.source in initial_ids:
                continue                      # initial gecisleri zaten gomuldu
            if tr.source in history_ids:
                continue                      # tarih varsayilanlari tabloya girmez
            if tr.target in join_ids:
                continue                      # join segmenti: cikista uretilir

            # CIKIS NOKTASI: icerideki ok sinirdaki noktayi hedefler.
            # UML 2.5.1, 14.2.3.7 (basili s.313): "Transitions terminating
            # on an exit point ... implies exiting of this composite State".
            # Derlemede ok, noktanin DISARIDAKI hedefine baglanir; bilesik
            # durumdan cikisi zaten LCA hesabi saglar.
            if tr.target in exit_ids:
                cikis = _exit_cozumle(tr.target)
                ev_name = tr.event.strip()
                if ev_name and ev_name not in event_index:
                    raise CodegenError("Unknown event: %s" % ev_name)
                ir.transitions.append(IrTransition(
                    index=idx,
                    model_id=tr.id,
                    source=st.index,
                    target=index_of[cikis.target],
                    event=event_index[ev_name] if ev_name else 0,
                    guard=_dedup_add(ir.guards, _branch_guard(tr.guard)),
                    action=_dedup_add(ir.actions, _combine_action(
                        [expand_breaks(tr.action).strip(),
                         expand_breaks(cikis.action).strip()])),
                    kind=TKIND_EXTERNAL,
                    text="%s --> exit %s --> %s"
                         % (st.name, sm.states[tr.target].name,
                            sm.states[cikis.target].name),
                ))
                idx += 1
                continue

            # GIRIS NOKTASI: fork gibi derlenir (belgenin kendi NOTE'u).
            if tr.target in entry_ids:
                sahip, hedefler, segmentler = _entry_cozumle(tr.target)
                ev_name = tr.event.strip()
                if ev_name and ev_name not in event_index:
                    raise CodegenError("Unknown event: %s" % ev_name)
                eylemler = [expand_breaks(tr.action).strip()]
                eylemler += [expand_breaks(x.action).strip() for x in segmentler]
                ir.transitions.append(IrTransition(
                    index=idx,
                    model_id=tr.id,
                    source=st.index,
                    target=index_of[sahip],
                    event=event_index[ev_name] if ev_name else 0,
                    guard=_dedup_add(ir.guards, _branch_guard(tr.guard)),
                    action=_dedup_add(ir.actions, _combine_action(eylemler)),
                    kind=_TKIND_MAP[tr.kind],
                    fork_targets=hedefler,
                    text="%s --> entry %s" % (st.name,
                                              sm.states[tr.target].name),
                ))
                idx += 1
                continue

            # FORK: tek satir uretilir. Hedef, segmentlerin ORTAK ortogonal
            # sahibidir; segment hedefleri `fork_targets` olarak tasinir ve
            # calisma zamaninda ilgili bolgelerde ACIKCA girilir. Adi
            # gecmeyen bolgeler varsayilanlariyla baslar.
            if tr.target in fork_ids:
                sahip, hedefler, segmentler = _fork_cozumle(tr.target)
                ev_name = tr.event.strip()
                if ev_name and ev_name not in event_index:
                    raise CodegenError("Unknown event: %s" % ev_name)
                eylemler = [expand_breaks(tr.action).strip()]
                eylemler += [expand_breaks(x.action).strip() for x in segmentler]
                ir.transitions.append(IrTransition(
                    index=idx,
                    model_id=tr.id,
                    source=st.index,
                    target=index_of[sahip],
                    event=event_index[ev_name] if ev_name else 0,
                    guard=_dedup_add(ir.guards, _branch_guard(tr.guard)),
                    action=_dedup_add(ir.actions, _combine_action(eylemler)),
                    kind=_TKIND_MAP[tr.kind],
                    fork_targets=hedefler,
                    text="%s --> fork %s" % (st.name,
                                             sm.states[tr.target].name),
                ))
                idx += 1
                continue

            if tr.target not in index_of:
                raise CodegenError("The target of a transition leaving '%s' could not be resolved." % st.name)
            ev_name = tr.event.strip()
            if ev_name and ev_name not in event_index:
                raise CodegenError("Unknown event: %s" % ev_name)

            paths = list(_walk(tr.target, [_branch_guard(tr.guard)],
                               [expand_breaks(tr.action).strip()], 0, frozenset()))
            if len(paths) > MAX_JUNCTION_PATHS:
                raise CodegenError(
                    "The junction chain of a transition leaving '%s' opens "
                    "into %d paths; at most %d are supported."
                    % (st.name, len(paths), MAX_JUNCTION_PATHS))

            for final_target, guard_parts, action_parts in paths:
                if final_target not in index_of:
                    raise CodegenError(
                        "The target of a transition leaving '%s' could not "
                        "be resolved." % st.name)
                ir.transitions.append(IrTransition(
                    index=idx,
                    model_id=tr.id,
                    source=st.index,
                    target=index_of[final_target],
                    event=event_index[ev_name] if ev_name else 0,
                    guard=_dedup_add(ir.guards, _combine_guard(guard_parts)),
                    action=_dedup_add(ir.actions, _combine_action(action_parts)),
                    kind=_TKIND_MAP[tr.kind],
                    text="%s --> %s : %s" % (st.name,
                                             sm.states[final_target].name,
                                             tr.label() or "(completion)"),
                ))
                idx += 1
        # JOIN: cikis gecisi, ortogonal SAHIBIN satirlarina eklenir.
        # Boylece bolgelerden herhangi birinden yukari yuruyen arama onu
        # bulur; etkinlik kosulu ise butun kaynaklarin AYNI ANDA etkin
        # olmasidir (14.2.3.7: "all incoming Transitions have to complete
        # before execution can continue through an outgoing Transition").
        for join_id in sorted(join_ids):
            sahip, kaynaklar, cikis = _join_cozumle(join_id)
            if index_of.get(sahip) != st.index:
                continue
            if cikis.target not in index_of:
                raise CodegenError(
                    "The target of the transition leaving join '%s' could "
                    "not be resolved." % sm.states[join_id].name)
            segment_eylemleri = [
                expand_breaks(t.action).strip()
                for t in sorted((x for x in sm.transitions.values()
                                 if x.target == join_id),
                                key=lambda x: x.id)]
            segment_eylemleri.append(expand_breaks(cikis.action).strip())
            ir.transitions.append(IrTransition(
                index=idx,
                model_id=cikis.id,
                source=st.index,
                target=index_of[cikis.target],
                event=0,                      # join tetikleyici tasiyamaz
                guard=-1,                     # join guard tasiyamaz
                action=_dedup_add(ir.actions,
                                  _combine_action(segment_eylemleri)),
                kind=TKIND_EXTERNAL,
                join_sources=kaynaklar,
                text="join %s --> %s" % (sm.states[join_id].name,
                                         sm.states[cikis.target].name),
            ))
            idx += 1

        ir.tran_slice[st.index] = (first, idx - first)

    return ir
