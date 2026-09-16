"""Veri modeli: UML durum makinesi (hiyerarsik / HSM).

Bu modul yalnizca *veri* tutar; Qt'ye bagimliligi yoktur. Boylece kod ureteci
ve testler arayuz olmadan da calisabilir.

Semantik dayanak: UML 2.5.1, Bolum 14 (StateMachines) - desteklenen alt kume:
  * Simple state, Composite state (tek bolge / single region)
  * Initial pseudostate, Final state, Choice pseudostate
  * External / Local / Internal gecisler
  * entry / exit / do davranislari
  * Completion (event'siz) gecisleri
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
import re
from typing import Dict, List, Optional

from .text_layout import flatten, split_lines

SCHEMA_VERSION = 1


def new_id(prefix: str) -> str:
    return "%s_%s" % (prefix, uuid.uuid4().hex[:8])


class StateKind(str, Enum):
    """UML Vertex turleri (UML 2.5.1, 14.2.3.4 Pseudostates dahil)."""

    SIMPLE = "simple"                    # basit durum
    COMPOSITE = "composite"              # bilesik durum (icinde alt durumlar var)
    INITIAL = "initial"                  # initial sozde-durumu
    FINAL = "final"                      # final durum
    CHOICE = "choice"                    # choice sozde-durumu (dinamik dallanma)
    JUNCTION = "junction"                # junction sozde-durumu (statik dallanma)
    SHALLOW_HISTORY = "shallow_history"  # H  - sig tarih sozde-durumu
    DEEP_HISTORY = "deep_history"        # H* - derin tarih sozde-durumu
    TERMINATE = "terminate"              # terminate sozde-durumu (X)
    FORK = "fork"                        # fork: bir oku bolgelere dagitir
    JOIN = "join"                        # join: bolgelerden gelenleri birlestirir
    ENTRY_POINT = "entry_point"          # bilesik durumun ADLANDIRILMIS girisi
    EXIT_POINT = "exit_point"            # bilesik durumun ADLANDIRILMIS cikisi
    SUBMACHINE = "submachine"            # baska bir makineye REFERANS

    @property
    def is_pseudo(self) -> bool:
        return self in (StateKind.INITIAL, StateKind.CHOICE, StateKind.JUNCTION,
                        StateKind.SHALLOW_HISTORY, StateKind.DEEP_HISTORY,
                        StateKind.TERMINATE, StateKind.FORK, StateKind.JOIN,
                        StateKind.ENTRY_POINT, StateKind.EXIT_POINT)

    @property
    def is_sync(self) -> bool:
        """Bolgeler arasi dagitim/birlestirme sozde-durumu mu?

        UML 2.5.1, 14.2.3.7 (basili s.313): fork "an incoming Transition
        into two or more Transitions terminating on Vertices in orthogonal
        Regions" boler; join ise "two or more Transitions originating from
        Vertices in different orthogonal Regions" icin ortak hedeftir.
        """
        return self in (StateKind.FORK, StateKind.JOIN)

    @property
    def is_connection_point(self) -> bool:
        """Bilesik durumun SINIRINDAKI adlandirilmis giris/cikis noktasi mi?

        UML 2.5.1, 14.2.3.7 (basili s.313): entryPoint "represents an entry
        point for a StateMachine or a composite State that provides
        encapsulation of the insides of the State or StateMachine";
        exitPoint da onun cikis karsiligidir. Ikisi de icerinin disaridan
        gizlenmesini saglar: disaridaki ok ic dugumu degil, SINIRDAKI
        noktayi hedefler.
        """
        return self in (StateKind.ENTRY_POINT, StateKind.EXIT_POINT)

    @property
    def is_history(self) -> bool:
        return self in (StateKind.SHALLOW_HISTORY, StateKind.DEEP_HISTORY)

    @property
    def is_branch(self) -> bool:
        """Guard'li dallanma sozde-durumu mu (choice/junction)?"""
        return self in (StateKind.CHOICE, StateKind.JUNCTION)

    @property
    def is_submachine(self) -> bool:
        return self is StateKind.SUBMACHINE

    @property
    def is_real_state(self) -> bool:
        """Kod uretiminde 'icinde kalinabilen' durum mu?

        SUBMACHINE de buradadir: UML'de altmakine durumu gercek bir
        durumdur, entry/exit/do tasiyabilir ve icinde kalinabilir
        (14.2.3.4.7). Genisletildiginde siradan bir bilesik duruma doner.
        """
        return self in (StateKind.SIMPLE, StateKind.COMPOSITE,
                        StateKind.FINAL, StateKind.SUBMACHINE)


class TransitionKind(str, Enum):
    EXTERNAL = "external"  # kaynak durumdan cikilir, hedefe girilir
    INTERNAL = "internal"  # durum degismez, yalnizca eylem calisir
    LOCAL = "local"        # kaynak bilesik durumdan cikilmaz


#: ZAMAN OLAYI bicimi: ``after(<ifade>)``.
#:
#: UML 2.5.1'de TimeEvent, Trigger'in bir turudur (clause 13) ve durum
#: makinesi gecisleri onu tetikleyici olarak kullanir. Bu arac yalnizca
#: GORELI bicimi (``after``) destekler: mutlak zaman (``at``) gomulu bir
#: hedefte takvim saati gerektirir ve yarim destek verilmez.
TIME_EVENT_RE = re.compile(r"^\s*after\s*\((?P<delay>.*)\)\s*$",
                           re.IGNORECASE)


def time_event_delay(name: str) -> Optional[str]:
    """``after(N)`` ise gecikme IFADESINI dondurur, degilse None."""
    m = TIME_EVENT_RE.match(name or "")
    if m is None:
        return None
    return m.group("delay").strip()


def is_time_event(name: str) -> bool:
    return time_event_delay(name) is not None


# --------------------------------------------------------------------------- #
#  Elemanlar
# --------------------------------------------------------------------------- #

@dataclass
class State:
    """Bir durum / sozde-durum dugumu."""

    id: str = field(default_factory=lambda: new_id("s"))
    name: str = "State"
    kind: StateKind = StateKind.SIMPLE
    parent: Optional[str] = None          # ust bilesik durumun id'si (None = kok)

    # -- BOLGELER (UML 2.5.1, 14.2.3.2 Region) ------------------------------ #
    #
    # Bir bilesik durum BIR YA DA DAHA COK bolge sahibidir. Birden cok
    # bolgesi olan durum ORTOGONALDIR: bolgeler es zamanli etkindir.
    # Kok (StateMachine) de bolge sahibidir; su an tek bolgesi vardir.
    #
    # Eski dosyalar bu alanlari tasimaz; varsayilanlar (1 ve 0) tam olarak
    # bugunku davranisi verir, dolayisiyla surum yukseltmesi gerekmez.
    #
    #: Bu durumun sahip oldugu bolge sayisi (yalnizca COMPOSITE icin anlamli).
    regions: int = 1
    #: Bu dugumun, EBEVEYNININ kacinci bolgesinde durdugu (0 tabanli).
    region: int = 0

    #: ERTELENEN OLAY turleri (UML deferrableTrigger).
    #:
    #: UML 2.5.1, 14.2.3.4.4 (basili s.309): "A State may specify a set of
    #: Event types that may be deferred in that State ... these Event
    #: occurrences remain in the event pool until: a state configuration is
    #: reached where these Event types are no longer deferred or, if a
    #: deferred Event type is used explicitly in a Trigger of a Transition
    #: whose source is the deferring State (i.e., a kind of override
    #: option)."
    deferred: List[str] = field(default_factory=list)

    #: ALTMAKINE referansi: calisma alanina gore dosya yolu.
    #:
    #: UML 2.5.1, 14.2.3.4.7 (basili s.311): altmakineler "like programming
    #: language macros, distinct Behavior specifications, which may be
    #: defined in a different context than the one where they are used".
    #: Bu yuzden referans BASKA BIR DOSYAYA gider ve kod uretiminden once
    #: yerine konur (bkz. app/core/submachine.py).
    submachine_ref: str = ""

    # Davranislar (kullanicinin yazdigi C/C++ ifadeleri)
    entry: str = ""
    exit: str = ""
    do: str = ""

    # Gorunum
    x: float = 0.0
    y: float = 0.0
    w: float = 170.0
    h: float = 84.0

    note: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kind"] = self.kind.value
        return d

    @staticmethod
    def from_dict(d: dict) -> "State":
        d = dict(d)
        d["kind"] = StateKind(d.get("kind", "simple"))
        # Liste alanlari KOPYALANIR: dosyadan gelen liste paylasilirsa iki
        # durum ayni listeyi gosterir ve birinde yapilan degisiklik
        # otekinde de gorunur.
        d["deferred"] = [str(x) for x in (d.get("deferred") or [])]
        allowed = set(State.__dataclass_fields__)
        return State(**{k: v for k, v in d.items() if k in allowed})


@dataclass
class Transition:
    """Iki dugum arasindaki gecis:  event [guard] / action"""

    id: str = field(default_factory=lambda: new_id("t"))
    source: str = ""
    target: str = ""
    event: str = ""     # bos => completion (event'siz) gecisi
    guard: str = ""     # C/C++ boolean ifadesi
    action: str = ""    # C/C++ deyim(ler)i
    kind: TransitionKind = TransitionKind.EXTERNAL
    priority: int = 0   # kucuk sayi once denenir

    # Gorunum
    waypoints: List[List[float]] = field(default_factory=list)
    label_dx: float = 0.0
    label_dy: float = -16.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kind"] = self.kind.value
        return d

    @staticmethod
    def from_dict(d: dict) -> "Transition":
        d = dict(d)
        d["kind"] = TransitionKind(d.get("kind", "external"))
        d["waypoints"] = [list(p) for p in d.get("waypoints", [])]
        allowed = set(Transition.__dataclass_fields__)
        return Transition(**{k: v for k, v in d.items() if k in allowed})

    def _raw_label(self) -> str:
        """Etiketi satir sonlari KORUNARAK kurar."""
        txt = self.event.strip()
        if self.guard.strip():
            txt = (txt + " " if txt else "") + "[" + self.guard.strip() + "]"
        if self.action.strip():
            act = self.action.strip().rstrip(";")
            txt = (txt + " / " + act) if txt else ("/ " + act)
        return txt

    def label(self) -> str:
        """UML etiketi -- TEK satir.

        PlantUML ciktisi, calisma alani agaci ve ozellik onizlemesi satir
        sonu TASIYAMAZ; oralarda bu kullanilir. Satir sonu isaretleri
        (bkz. core/text_layout) bosluga iner.
        """
        return flatten(self._raw_label())

    def label_lines(self) -> List[str]:
        """Etiketin gorunum satirlari -- tuval icin.

        Kullanici olay / guard / eylem alanlarina satir sonu isaretini
        (text_layout.LINE_BREAK_MARKER) yazarak etiketi birden fazla
        satira boler.
        """
        return split_lines(self._raw_label())


# --------------------------------------------------------------------------- #
#  Dokuman
# --------------------------------------------------------------------------- #

@dataclass
class StateMachine:
    """Tum diyagrami temsil eden dokuman."""

    name: str = "Blinky"
    prefix: str = "blinky"          # uretilen C sembollerinin on eki
    description: str = ""
    context_type: str = "void"      # kullanici baglam tipi; "void" => void *ctx
    user_includes: str = ""         # uretilen basliga aynen eklenecek #include'lar
    states: Dict[str, State] = field(default_factory=dict)
    transitions: Dict[str, Transition] = field(default_factory=dict)

    # -- erisim yardimcilari ------------------------------------------------ #

    def add_state(self, st: State) -> State:
        self.states[st.id] = st
        return st

    def add_transition(self, tr: Transition) -> Transition:
        self.transitions[tr.id] = tr
        return tr

    def remove_state(self, sid: str) -> None:
        """Durumu, tum alt agacini ve ilgili gecisleri siler."""
        for cid in [c.id for c in self.children(sid)]:
            self.remove_state(cid)
        for tid in [t.id for t in self.transitions.values()
                    if t.source == sid or t.target == sid]:
            self.transitions.pop(tid, None)
        self.states.pop(sid, None)

    def remove_transition(self, tid: str) -> None:
        self.transitions.pop(tid, None)

    def children(self, sid: Optional[str]) -> List[State]:
        return [s for s in self.states.values() if s.parent == sid]

    def sorted_children(self, sid: Optional[str]) -> List[State]:
        return sorted(self.children(sid), key=lambda s: (round(s.y, 3), round(s.x, 3), s.name))

    # -- bolgeler ----------------------------------------------------------- #

    def region_count(self, sid: Optional[str]) -> int:
        """Verilen sahibin (kok icin None) bolge sayisi.

        Kok su an TEK bolge tasir; UML birden cok ust duzey bolgeye izin
        verir ama bu aracin uretecinde karsiligi yoktur ve yarim destek
        verilmez (bkz. test_uml_conformance 11. bolum).
        """
        if sid is None:
            return 1
        st = self.states.get(sid)
        if st is None or st.kind is not StateKind.COMPOSITE:
            return 1
        return max(1, int(st.regions))

    def is_orthogonal(self, sid: Optional[str]) -> bool:
        """Sahip BIRDEN COK bolge tasiyor mu?"""
        return self.region_count(sid) > 1

    def region_of(self, sid: str) -> int:
        """Dugumun ebeveyninde durdugu bolgenin dizini (kirpilmis)."""
        st = self.states.get(sid)
        if st is None:
            return 0
        return max(0, min(int(st.region), self.region_count(st.parent) - 1))

    def children_in(self, sid: Optional[str], region: int) -> List[State]:
        """Sahibin BELIRLI bir bolgesindeki cocuklar, kararli sirada."""
        return [s for s in self.sorted_children(sid)
                if self.region_of(s.id) == region]

    def roots(self) -> List[State]:
        return self.children(None)

    def parent_of(self, sid: str) -> Optional[State]:
        st = self.states.get(sid)
        if st is None or st.parent is None:
            return None
        return self.states.get(st.parent)

    def ancestors(self, sid: str) -> List[State]:
        """En yakindan koke dogru ust durumlar."""
        out: List[State] = []
        cur = self.parent_of(sid)
        seen = set()
        while cur is not None and cur.id not in seen:
            seen.add(cur.id)
            out.append(cur)
            cur = self.parent_of(cur.id)
        return out

    def depth(self, sid: str) -> int:
        return len(self.ancestors(sid))

    def max_depth(self) -> int:
        if not self.states:
            return 1
        return max(self.depth(s) for s in self.states) + 1

    def is_descendant(self, sid: str, maybe_ancestor: str) -> bool:
        return any(a.id == maybe_ancestor for a in self.ancestors(sid))

    def lca(self, a: str, b: str) -> Optional[str]:
        """En yakin ortak ust durum (yoksa None = kok bolge)."""
        chain_a = [a] + [s.id for s in self.ancestors(a)]
        chain_b = set([b] + [s.id for s in self.ancestors(b)])
        for x in chain_a:
            if x in chain_b:
                return x
        return None

    def outgoing(self, sid: str) -> List[Transition]:
        """Cikis gecisleri, denenecekleri sirada.

        Guard'siz / 'else' dallar HER ZAMAN en sona konur -- kullanicinin
        verdigi oncelik de bunu degistiremez. UML 2.5.1 14.2.3.4.6: 'else'
        ancak diger butun guard'lar yanlis oldugunda secilebilir. Oncelik,
        yalnizca guard'LI dallarin kendi aralarindaki sirayi belirler; aksi
        halde 'else' dalina kucuk bir oncelik vermek guard'li dallarin
        tamamini ulasilamaz kilardi.
        """
        def key(t: Transition):
            guard = t.guard.strip()
            unguarded = (not guard) or (guard.lower() == "else")
            return (1 if unguarded else 0, t.priority, t.id)
        return sorted((t for t in self.transitions.values() if t.source == sid),
                      key=key)

    def incoming(self, sid: str) -> List[Transition]:
        return [t for t in self.transitions.values() if t.target == sid]

    def initial_of(self, parent: Optional[str],
                   region: int = 0) -> Optional[State]:
        """Verilen sahibin BELIRLI bolgesindeki initial sozde-durumu.

        UML 2.5.1, 14.2.3.2 (basili s.307): her bolgenin kendi varsayilan
        giris noktasi vardir. Ortogonal bir durumda bolge basina AYRI bir
        initial bulunur; tek bolgeli durumlarda davranis degismez.
        """
        for s in self.children_in(parent, region):
            if s.kind is StateKind.INITIAL:
                return s
        return None

    def events(self) -> List[str]:
        """Modelde gecen tum event adlari (alfabetik, tekil)."""
        evs = set()
        for t in self.transitions.values():
            if t.event.strip():
                evs.add(t.event.strip())
        # ERTELENEN olay turleri de tabloya girer: hicbir gecis onu
        # tetiklemese bile makine onu TANIMALI ve havuzda tutmalidir.
        for st in self.states.values():
            for ad in (st.deferred or []):
                if str(ad).strip():
                    evs.add(str(ad).strip())
        return sorted(evs)

    def ordered_states(self) -> List[State]:
        """Kararli (deterministik) siralama: kokten derinlik-oncelikli.

        Ust durumu bulunamayan ya da dairesel hiyerarside kalan durumlar bu
        yurumeyle gezilemez; yine de listeye SONA eklenirler. Aksi halde
        kaydetme (``to_dict``) ve tuval yeniden cizimi bu durumlari sessizce
        dusurur, kullanici da modelinin bir parcasini kaybettigini fark etmezdi.
        Dogrulayici bu durumlari V020/V021 ile hata olarak isaretler.
        """
        out: List[State] = []
        seen: set = set()

        def walk(parent: Optional[str]) -> None:
            for s in self.sorted_children(parent):
                if s.id in seen:
                    continue
                seen.add(s.id)
                out.append(s)
                walk(s.id)

        walk(None)
        orphans = [s for s in self.states.values() if s.id not in seen]
        out.extend(sorted(orphans,
                          key=lambda s: (round(s.y, 3), round(s.x, 3), s.name)))
        return out

    def ordered_transitions(self) -> List[Transition]:
        """Kaynak durum sirasina, sonra oncelige gore kararli siralama."""
        order = {s.id: i for i, s in enumerate(self.ordered_states())}
        return sorted(self.transitions.values(),
                      key=lambda t: (order.get(t.source, 1 << 30), t.priority, t.id))

    # -- serilestirme -------------------------------------------------------- #

    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA_VERSION,
            "type": "state_machine",
            "name": self.name,
            "prefix": self.prefix,
            "description": self.description,
            "context_type": self.context_type,
            "user_includes": self.user_includes,
            "states": [s.to_dict() for s in self.ordered_states()],
            "transitions": [t.to_dict() for t in self.ordered_transitions()],
        }

    @staticmethod
    def from_dict(d: dict) -> "StateMachine":
        sm = StateMachine(
            name=d.get("name", "StateMachine"),
            prefix=d.get("prefix", "sm"),
            description=d.get("description", ""),
            context_type=d.get("context_type", "void") or "void",
            user_includes=d.get("user_includes", ""),
        )
        # AYNI KIMLIK SESSIZCE EZILMEZ.
        #
        # Sozluk atamasi, ayni ``id`` ile gelen ikinci ogenin birincisini
        # -- ve onun altindaki her seyi -- hicbir ileti vermeden yok
        # etmesine yol aciyordu: elle duzenlenmis ya da iki dosyadan
        # birlestirilmis bir modelde durumlarin yarisi kayboluyor,
        # kullanici bunu ancak diyagrama bakinca anliyordu. Bozuk bir
        # dosyayi ACIK BIR ILETIYLE reddetmek, yarisini sessizce yutup
        # acmaktan iyidir.
        for sd in d.get("states", []):
            st = State.from_dict(sd)
            if st.id in sm.states:
                raise ValueError(
                    "The model file lists two states with the same id %r "
                    "(%r and %r). Ids must be unique."
                    % (st.id, sm.states[st.id].name, st.name))
            sm.states[st.id] = st
        for td in d.get("transitions", []):
            tr = Transition.from_dict(td)
            if tr.id in sm.transitions:
                raise ValueError(
                    "The model file lists two transitions with the same id "
                    "%r. Ids must be unique." % tr.id)
            sm.transitions[tr.id] = tr
        return sm

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)

    @staticmethod
    def from_json(text: str) -> "StateMachine":
        return StateMachine.from_dict(json.loads(text))

    def clone(self) -> "StateMachine":
        return StateMachine.from_dict(json.loads(self.to_json()))

    def assign_from(self, other: "StateMachine") -> None:
        """Ayni nesneyi koruyarak icerigi degistirir (undo/yukleme icin)."""
        self.name = other.name
        self.prefix = other.prefix
        self.description = other.description
        self.context_type = other.context_type
        self.user_includes = other.user_includes
        self.states = other.states
        self.transitions = other.transitions
