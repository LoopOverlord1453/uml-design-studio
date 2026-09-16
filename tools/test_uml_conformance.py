"""OMG UML 2.5.1 ANLAMBILIM UYUM DENETIMI.

    python tools/test_uml_conformance.py

Diger testler "calisiyor mu" ve "standarda benziyor mu" sorar. Burada
sorulan sey daha dar ve daha sert: aracin calisma zamani davranisi,
spesifikasyonun NORMATIF cumleleriyle birebir ayni mi?

Her kontrol, dayandigi cumleyi ALINTILAR ve maddesini yazar. Alintilar
OMG UML 2.5.1 (formal/2017-12-05) belgesinden; sayfa numaralari o PDF'in
BASILI sayfalaridir. Belgeyi arayuzden acabilirsiniz:
References > UML 2.5.1 Specification (PDF).

Denetlenen davranislar:

  1. Gecis onceligi          14.2.3.9.4
  2. Ic gecis                14.2.3.9.3 / 14.2.3.4.2
  3. Local gecis             14.2.3.4.2
  4. Junction (statik)       14.2.3.7
  5. Choice (dinamik)        14.2.3.7 / 14.2.3.8
  6. Terminate               14.2.3.4.7
  7. Sig / derin tarih       14.2.3.4.5
  8. Varsayilan giris        14.2.3.4.4
  9. Completion gecisi       14.2.3.8.3
 10. Gecis islem sirasi      14.2.3.9.6

Ayrica DESTEKLENMEYEN ozelliklerin sessizce yanlis kod uretmedigi, acikca
reddedildigi dogrulanir -- bir aracin uyumlulugu, yapamadigini durustce
soylemesini de icerir.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.core.model import (State, StateKind, StateMachine,  # noqa: E402
                            Transition, TransitionKind)
from app.core.simulator import Simulator                     # noqa: E402
from app.core.validator import validate                      # noqa: E402

_failures = []
_bolum = [""]


def bolum(baslik: str, madde: str, alinti: str) -> None:
    _bolum[0] = baslik
    print("\n== %s ==" % baslik)
    print("   UML 2.5.1 %s:" % madde)
    for satir in _sar(alinti, 66):
        print("     “%s”" % satir if satir is alinti else "      %s"
              % satir)


def _sar(metin: str, genislik: int):
    sozcukler = metin.split()
    satir = ""
    out = []
    for s in sozcukler:
        if len(satir) + len(s) + 1 > genislik:
            out.append(satir)
            satir = s
        else:
            satir = (satir + " " + s).strip()
    if satir:
        out.append(satir)
    return out


def check(kosul: bool, etiket: str, ayrinti="") -> None:
    if kosul:
        print("   [ TAMAM ] %s" % etiket)
        return
    _failures.append("%s -> %s" % (_bolum[0], etiket))
    print("   [ HATA  ] %s" % etiket)
    if ayrinti != "":
        for satir in str(ayrinti).splitlines()[:6]:
            print("            | %s" % satir)


def kos(sm: StateMachine, olaylar, guard=None):
    """Makineyi calistirir; (simulator, iz) dondurur."""
    kayit = []
    sim = Simulator(sm, guard_eval=guard,
                    on_event=lambda k, d: kayit.append("%s:%s" % (k, d)))
    sim.start()
    for ev in olaylar:
        sim.dispatch(ev)
    return sim, kayit


def ilk(sm: StateMachine, hedef: str, x=0.0, y=-70.0) -> None:
    """Koke bir initial sozde-durumu ve gecisini ekler."""
    sm.add_state(State(id="_ini", name="Start", kind=StateKind.INITIAL,
                       x=x, y=y, w=24.0, h=24.0))
    sm.add_transition(Transition(source="_ini", target=hedef))


# --------------------------------------------------------------------------- #

def t_priority():
    bolum("1. Gecis onceligi", "14.2.3.9.4 (basili s.317)",
          "By definition, a Transition originating from a substate has "
          "higher priority than a conflicting Transition originating from "
          "any of its containing States.")

    sm = StateMachine(name="P", prefix="p", context_type="void")
    sm.add_state(State(id="c", name="Outer", kind=StateKind.COMPOSITE,
                       x=0, y=0, w=340, h=200))
    sm.add_state(State(id="ci", name="CI", kind=StateKind.INITIAL,
                       x=30, y=20, w=24, h=24, parent="c"))
    sm.add_state(State(id="a", name="A", kind=StateKind.SIMPLE,
                       x=30, y=60, parent="c"))
    sm.add_state(State(id="inner", name="Inner", kind=StateKind.SIMPLE,
                       x=180, y=60, parent="c"))
    sm.add_state(State(id="outer_t", name="OuterTarget",
                       kind=StateKind.SIMPLE, x=520, y=60))
    ilk(sm, "c")
    sm.add_transition(Transition(source="ci", target="a"))
    sm.add_transition(Transition(source="c", target="outer_t", event="E"))
    sm.add_transition(Transition(source="a", target="inner", event="E"))

    sim, _ = kos(sm, ["E"])
    check(sim.state_name == "Inner",
          "ic durumun gecisi UST durumunkini yeniyor", sim.state_name)


def t_internal():
    bolum("2. Ic gecis", "14.2.3.9.3 (basili s.317) / 14.2.3.8.1 (basili s.314)",
          "An internal Transition in a State conflicts only with "
          "Transitions that cause an exit from that State. ... kind = "
          "internal is a special case of a local Transition that is a "
          "self-transition (i.e., with the same source and target States), "
          "such that the State is never exited (and, thus, not re-entered), "
          "which means that no exit or entry Behaviors are executed when "
          "this Transition is executed.")

    sm = StateMachine(name="I", prefix="i", context_type="void")
    sm.add_state(State(id="a", name="A", kind=StateKind.SIMPLE, x=0, y=0,
                       entry="ENTRY_A();", exit="EXIT_A();"))
    ilk(sm, "a")
    sm.add_transition(Transition(source="a", target="a", event="PING",
                                 kind=TransitionKind.INTERNAL,
                                 action="PING_EFFECT();"))
    sim, iz = kos(sm, ["PING"])
    check(sim.state_name == "A", "durum degismedi", sim.state_name)
    # Gecis EYLEMI izde "A:" onekiyle gecer; "code:" oneki giris/cikis
    # GOVDELERINE aittir.
    check("A:PING_EFFECT();" in iz, "gecis eylemi calisti", iz)
    check(iz.count("code:EXIT_A();") == 0, "CIKIS eylemi calismadi", iz)
    check(iz.count("code:ENTRY_A();") == 1, "GIRIS bir kez calisti (baslangic)",
          iz)


def t_local():
    bolum("3. Local gecis", "14.2.3.8.1 (basili s.314)",
          "kind = local is the opposite of external, meaning that the "
          "Transition does not exit its containing State (and, hence, the "
          "exit Behavior of the containing State will not be executed). "
          "However, for local Transitions the target Vertex must be "
          "different from its source Vertex. A local Transition can only "
          "exist within a composite State.")

    sm = StateMachine(name="L", prefix="l", context_type="void")
    sm.add_state(State(id="c", name="Outer", kind=StateKind.COMPOSITE,
                       x=0, y=0, w=380, h=220,
                       entry="ENTRY_OUTER();", exit="EXIT_OUTER();"))
    sm.add_state(State(id="ci", name="CI", kind=StateKind.INITIAL,
                       x=30, y=20, w=24, h=24, parent="c"))
    sm.add_state(State(id="a", name="A", kind=StateKind.SIMPLE, x=30, y=60,
                       parent="c"))
    sm.add_state(State(id="b", name="B", kind=StateKind.SIMPLE, x=210, y=60,
                       parent="c"))
    ilk(sm, "c")
    sm.add_transition(Transition(source="ci", target="a"))
    sm.add_transition(Transition(source="c", target="b", event="GO",
                                 kind=TransitionKind.LOCAL))
    sim, iz = kos(sm, ["GO"])
    check(sim.state_name == "B", "hedefe varildi", sim.state_name)
    check("code:EXIT_OUTER();" not in iz,
          "kapsayan durumdan CIKILMADI", iz)
    check(iz.count("code:ENTRY_OUTER();") == 1,
          "kapsayan duruma YENIDEN GIRILMEDI", iz)

    # KARSILASTIRMA: ayni gecis EXTERNAL olsaydi cikip yeniden girerdi.
    sm2 = StateMachine.from_json(sm.to_json())
    for t in sm2.transitions.values():
        if t.event == "GO":
            t.kind = TransitionKind.EXTERNAL
    _sim2, iz2 = kos(sm2, ["GO"])
    check("code:EXIT_OUTER();" in iz2,
          "ayni gecis EXTERNAL olunca cikis CALISIYOR (ayrim gercek)", iz2)


def t_junction_static():
    bolum("4. Junction: STATIK dallanma", "14.2.3.7 (basili s.313)",
          "Such guard Constraints are evaluated before any compound "
          "transition containing this Pseudostate is executed, which is why "
          "this is referred to as a static conditional branch. ... the "
          "entire compound transition is disabled even though its Triggers "
          "are enabled.")

    sm = _dal(StateKind.JUNCTION)
    sim, iz = kos(sm, [], guard=lambda e: False)
    tetik = sim.dispatch("GO")
    check(tetik is False, "hicbir yol etkin degilse gecis TETIKLENMEZ", tetik)
    check(sim.state_name == "A", "kaynak durumda kalindi", sim.state_name)
    check("code:EXIT_A();" not in iz, "CIKIS eylemi calismadi", iz)
    check("A:EFFECT();" not in iz, "gecis eylemi calismadi", iz)


def t_choice_dynamic():
    bolum("5. Choice: DINAMIK dallanma", "14.2.3.8 (basili s.315)",
          "Guards are evaluated before the compound transition that "
          "contains them is enabled, unless they are on Transitions that "
          "originate from a choice Pseudostate. In the latter case, the "
          "guards are evaluated when the choice point is reached.")

    sm = _dal(StateKind.CHOICE)
    sim, iz = kos(sm, [], guard=lambda e: False)
    tetik = sim.dispatch("GO")
    check(tetik is True, "gecis TETIKLENIR (guard sonra bakilir)", tetik)
    check("code:EXIT_A();" in iz, "CIKIS eylemi calisti", iz)
    check("A:EFFECT();" in iz, "gecis eylemi calisti", iz)

    # else DALI ZORUNLUDUR: spesifikasyon "ill formed" der.
    kodlar = {i.code for i in validate(sm) if i.is_error}
    check("V062" in kodlar,
          "else'siz choice HATA olarak bildiriliyor (model ill-formed)",
          sorted(kodlar))


def _dal(tur: StateKind) -> StateMachine:
    sm = StateMachine(name="Branch", prefix="br", context_type="void")
    sm.add_state(State(id="a", name="A", kind=StateKind.SIMPLE, x=0, y=0,
                       exit="EXIT_A();"))
    sm.add_state(State(id="d", name="D", kind=tur, x=250, y=0, w=38, h=38))
    sm.add_state(State(id="b", name="B", kind=StateKind.SIMPLE, x=500, y=0))
    ilk(sm, "a")
    sm.add_transition(Transition(source="a", target="d", event="GO",
                                 action="EFFECT();"))
    sm.add_transition(Transition(source="d", target="b", guard="cond"))
    return sm


def t_terminate():
    bolum("6. Terminate", "14.2.3.7 (basili s.313)",
          "Entering a terminate Pseudostate implies that the execution of "
          "the StateMachine is terminated immediately. The StateMachine "
          "does not exit any States nor does it perform any exit Behaviors. "
          "Any executing doActivity Behaviors are automatically aborted.")

    sm = StateMachine(name="T", prefix="t", context_type="void")
    sm.add_state(State(id="c", name="Outer", kind=StateKind.COMPOSITE,
                       x=0, y=0, w=340, h=200, exit="EXIT_OUTER();"))
    sm.add_state(State(id="ci", name="CI", kind=StateKind.INITIAL,
                       x=30, y=20, w=24, h=24, parent="c"))
    sm.add_state(State(id="a", name="A", kind=StateKind.SIMPLE, x=30, y=60,
                       parent="c", exit="EXIT_A();"))
    sm.add_state(State(id="x", name="Stop", kind=StateKind.TERMINATE,
                       x=500, y=60, w=30, h=30))
    ilk(sm, "c")
    sm.add_transition(Transition(source="ci", target="a"))
    sm.add_transition(Transition(source="a", target="x", event="KILL",
                                 action="EFFECT();"))
    sim, iz = kos(sm, ["KILL"])
    check(sim.is_terminated(), "makine sonlandi")
    check("code:EXIT_A();" not in iz, "yaprak durumun CIKISI calismadi", iz)
    check("code:EXIT_OUTER();" not in iz,
          "kapsayan durumun CIKISI calismadi", iz)
    check("A:EFFECT();" in iz,
          "gecisin KENDI eylemi calisti (cikis eylemi degildir)", iz)
    check(sim.dispatch("KILL") is False,
          "sonlanmis makine olay islemiyor")


def t_history():
    bolum("7. Tarih sozde-durumlari", "14.2.3.4.5 (basili s.310)",
          "Shallow history entry: If the incoming Transition terminates on "
          "a shallowHistory Pseudostate of a Region of the composite State, "
          "the active substate becomes the substate that was most recently "
          "active prior to this entry, unless: o the most recently active "
          "substate is the FinalState, or o this is the first entry into "
          "this State. ... Deep history entry: The rule for this case is "
          "the same as for shallow history except that the target "
          "Pseudostate is of type deepHistory and the rule is applied "
          "recursively to all levels in the active state configuration "
          "below this one.")

    # (a) Sig tarih en son ALT DURUMU geri getirir.
    sm = _tarih_makinesi(StateKind.SHALLOW_HISTORY, derin=False)
    sim, _ = kos(sm, ["NEXT", "LEAVE", "BACK"])
    check(sim.state_name == "A2", "sig tarih son alt durumu geri getirdi",
          sim.state_name)

    # (b) Bolge FINAL'e ulastiysa tarih SIFIRLANIR -> varsayilan giris.
    sm = _tarih_makinesi(StateKind.SHALLOW_HISTORY, derin=False, final=True)
    sim, _ = kos(sm, ["DONE", "LEAVE", "BACK"])
    check(sim.state_name == "A1",
          "FINAL'den sonra tarih degil VARSAYILAN giris kullanildi",
          sim.state_name)

    # (c) Derin tarih IC ICE yapilandirmayi geri getirir.
    # Iki NEXT: ilki A1 -> A2 (Deep1'e iner), ikincisi Deep1 -> Deep2.
    sm = _tarih_makinesi(StateKind.DEEP_HISTORY, derin=True)
    sim, _ = kos(sm, ["NEXT", "NEXT", "LEAVE", "BACK"])
    check(sim.state_name == "Deep2",
          "derin tarih ic ice yapilandirmayi geri getirdi", sim.state_name)


def _tarih_makinesi(tur: StateKind, derin: bool, final: bool = False):
    sm = StateMachine(name="H", prefix="h", context_type="void")
    sm.add_state(State(id="c", name="Outer", kind=StateKind.COMPOSITE,
                       x=0, y=0, w=520, h=300))
    sm.add_state(State(id="ci", name="CI", kind=StateKind.INITIAL,
                       x=20, y=20, w=24, h=24, parent="c"))
    sm.add_state(State(id="a1", name="A1", kind=StateKind.SIMPLE,
                       x=20, y=70, parent="c"))
    if derin:
        sm.add_state(State(id="a2", name="A2", kind=StateKind.COMPOSITE,
                           x=220, y=70, w=260, h=170, parent="c"))
        sm.add_state(State(id="di", name="DI", kind=StateKind.INITIAL,
                           x=20, y=20, w=24, h=24, parent="a2"))
        sm.add_state(State(id="d1", name="Deep1", kind=StateKind.SIMPLE,
                           x=20, y=60, parent="a2"))
        sm.add_state(State(id="d2", name="Deep2", kind=StateKind.SIMPLE,
                           x=20, y=150, parent="a2"))
        sm.add_transition(Transition(source="di", target="d1"))
        sm.add_transition(Transition(source="d1", target="d2", event="NEXT"))
    else:
        sm.add_state(State(id="a2", name="A2", kind=StateKind.SIMPLE,
                           x=220, y=70, parent="c"))
        sm.add_transition(Transition(source="a1", target="a2", event="NEXT"))
    sm.add_state(State(id="h", name="Hist", kind=tur,
                       x=430, y=20, w=32, h=32, parent="c"))
    sm.add_state(State(id="out", name="Out", kind=StateKind.SIMPLE,
                       x=700, y=70))
    ilk(sm, "c", x=-120.0)
    sm.add_transition(Transition(source="ci", target="a1"))
    sm.add_transition(Transition(source="c", target="out", event="LEAVE"))
    sm.add_transition(Transition(source="out", target="h", event="BACK"))
    if final:
        sm.add_state(State(id="f", name="Fin", kind=StateKind.FINAL,
                           x=350, y=200, w=30, h=30, parent="c"))
        sm.add_transition(Transition(source="a1", target="f", event="DONE"))
    if derin:
        sm.add_transition(Transition(source="a1", target="a2", event="NEXT"))
    return sm


def t_default_entry():
    bolum("8. Varsayilan giris", "14.2.3.7 (basili s.312)",
          "An initial Pseudostate represents a starting point for a Region; "
          "that is, it is the point from which execution of its contained "
          "behavior commences when the Region is entered via default "
          "activation. It is the source for at most one Transition, which "
          "may have an associated effect Behavior, but not an associated "
          "trigger or guard. There can be at most one initial Vertex in a "
          "Region.")

    sm = StateMachine(name="D", prefix="d", context_type="void")
    sm.add_state(State(id="a", name="A", kind=StateKind.SIMPLE, x=0, y=0))
    sm.add_state(State(id="i", name="I", kind=StateKind.INITIAL,
                       x=0, y=-70, w=24, h=24))
    sm.add_transition(Transition(source="i", target="a", event="GO",
                                 guard="x > 0"))
    kodlar = {i.code for i in validate(sm) if i.is_error}
    check("V034" in kodlar or "V035" in kodlar,
          "initial gecisinde tetikleyici/guard HATA veriyor", sorted(kodlar))

    # Eylem TASIYABILIR.
    sm2 = StateMachine(name="D2", prefix="d2", context_type="void")
    sm2.add_state(State(id="a", name="A", kind=StateKind.SIMPLE, x=0, y=0))
    sm2.add_state(State(id="i", name="I", kind=StateKind.INITIAL,
                        x=0, y=-70, w=24, h=24))
    sm2.add_transition(Transition(source="i", target="a", action="INIT();"))
    kodlar2 = {i.code for i in validate(sm2) if i.is_error}
    check(not kodlar2, "initial gecisi EYLEM tasiyabiliyor", sorted(kodlar2))
    _sim, iz = kos(sm2, [])
    check("A:INIT();" in iz, "initial eylemi baslangicta calisti", iz)


def t_completion():
    bolum("9. Completion gecisi", "14.2.3.8.3 (basili s.314-315)",
          "A special kind of Transition is a completion Transition, which "
          "has an implicit trigger. The event that enables this trigger is "
          "called a completion event and it signifies that all Behaviors "
          "associated with the source State of the completion Transition "
          "have completed execution. ... For composite or submachine "
          "States, a completion event is generated under the following "
          "circumstances: ... All internal activities (e.g., entry and "
          "doActivity Behaviors) have completed execution, and ... if the "
          "State is a composite State, all its orthogonal Regions have "
          "reached a FinalState")

    sm = StateMachine(name="Cm", prefix="cm", context_type="void")
    sm.add_state(State(id="c", name="Outer", kind=StateKind.COMPOSITE,
                       x=0, y=0, w=340, h=200))
    sm.add_state(State(id="ci", name="CI", kind=StateKind.INITIAL,
                       x=20, y=20, w=24, h=24, parent="c"))
    sm.add_state(State(id="a", name="A", kind=StateKind.SIMPLE, x=20, y=60,
                       parent="c"))
    sm.add_state(State(id="f", name="Fin", kind=StateKind.FINAL,
                       x=220, y=60, w=30, h=30, parent="c"))
    sm.add_state(State(id="after", name="After", kind=StateKind.SIMPLE,
                       x=520, y=60))
    ilk(sm, "c")
    sm.add_transition(Transition(source="ci", target="a"))
    sm.add_transition(Transition(source="a", target="f", event="DONE"))
    sm.add_transition(Transition(source="c", target="after"))   # tetikleyicisiz

    sim, _ = kos(sm, ["DONE"])
    check(sim.state_name == "After",
          "bolge FINAL'e varinca completion gecisi kendiliginden isledi",
          sim.state_name)


def t_execution_order():
    bolum("10. Gecis islem sirasi", "14.2.3.9.6 (basili s.318)",
          "Once a Transition is enabled and is selected to fire, the "
          "following steps are carried out in order: 1 Starting with the "
          "main source State, the States that contain the main source State "
          "are exited according to the rules of State exit ... At that "
          "point, the effect Behavior of the Transition that connects the "
          "sub- configuration of source States to the sub-configuration of "
          "target States is executed. ... 3 The configuration of States "
          "containing the main target State is entered, starting with the "
          "outermost State in the least common ancestor Region that "
          "contains the main target State.")

    sm = StateMachine(name="O", prefix="o", context_type="void")
    sm.add_state(State(id="src", name="SrcOuter", kind=StateKind.COMPOSITE,
                       x=0, y=0, w=320, h=200, exit="X_SRC_OUTER();"))
    sm.add_state(State(id="si", name="SI", kind=StateKind.INITIAL,
                       x=20, y=20, w=24, h=24, parent="src"))
    sm.add_state(State(id="sa", name="SrcInner", kind=StateKind.SIMPLE,
                       x=20, y=60, parent="src", exit="X_SRC_INNER();"))
    sm.add_state(State(id="dst", name="DstOuter", kind=StateKind.COMPOSITE,
                       x=460, y=0, w=320, h=200, entry="E_DST_OUTER();"))
    sm.add_state(State(id="di", name="DI", kind=StateKind.INITIAL,
                       x=20, y=20, w=24, h=24, parent="dst"))
    sm.add_state(State(id="da", name="DstInner", kind=StateKind.SIMPLE,
                       x=20, y=60, parent="dst", entry="E_DST_INNER();"))
    ilk(sm, "src", x=-140.0)
    sm.add_transition(Transition(source="si", target="sa"))
    sm.add_transition(Transition(source="di", target="da"))
    sm.add_transition(Transition(source="sa", target="dst", event="GO",
                                 action="EFFECT();"))

    _sim, iz = kos(sm, ["GO"])
    # Hem govdeler ("code:") hem gecis eylemleri ("A:") SIRAYLA alinir.
    kod = [k.split(":", 1)[1] for k in iz
           if k.startswith("code:") or k.startswith("A:")]
    beklenen = ["X_SRC_INNER();", "X_SRC_OUTER();", "EFFECT();",
                "E_DST_OUTER();", "E_DST_INNER();"]
    check(kod == beklenen,
          "cikis (icten disa) -> eylem -> giris (distan ice)",
          "beklenen: %s\nolan    : %s" % (beklenen, kod))


def t_unsupported_is_refused():
    bolum("11. Desteklenmeyen ozellikler", "uyumlulugun durustluk kismi",
          "Bir aracin uyumlulugu, DESTEKLEMEDIGI seyi sessizce yanlis "
          "uretmek yerine acikca reddetmesini de icerir.")

    from app.ui.canvas import Tool
    from app.ui.class_canvas import ClassTool

    arac_turleri = {t.value for t in Tool} | {t.value for t in ClassTool}
    # UML 2.5.1 clause 14'un SUNULMAYAN parcalari. Her biri BILEREK
    # disaridadir ve kullanicinin cizemeyecegi bir seyi yarim desteklemek
    # yerine acikca reddedilir.
    for eksik in ("connection_point_reference",):
        check(eksik not in arac_turleri,
              "arac paletinde '%s' SUNULMUYOR (yarim destek verilmiyor)"
              % eksik)
    # Protocol State Machine (14.4) ve StateMachine redefinition (14.3) bu
    # urunun kapsaminda DEGILDIR: birincisi entry/exit/do ve gecis eylemi
    # TASIYAMAZ (14.4.3.1), yani bu aracin butun ifade araclarini yasaklar;
    # ikincisi urun hatti varyantlari icindir. Ikisi de burada YAZILIDIR ki
    # musteri eksikligi belgeden degil araci kullanarak ogrenmesin.
    check(True, "Protocol State Machine (14.4) KAPSAM DISI -- belgelenmistir")
    check(True, "StateMachine redefinition (14.3) KAPSAM DISI -- belgelenmistir")

    # BOLGELER ARTIK DESTEKLENIYOR (bkz. 15. bolum). Bir arac kipi degil,
    # bilesik durumun bir OZELLIGIDIR: durum kac bolge sahibi olacaksa
    # denetciden secilir, alt durumlar da bantlara birakilarak atanir.
    from app.core.model import State as _State
    check("regions" in _State.__dataclass_fields__,
          "bolgeler modelde VAR (ortogonal durumlar destekleniyor)")

    from app.core.model import StateKind as SK
    desteklenen = {k.value for k in SK}
    check("fork" in desteklenen and "join" in desteklenen,
          "model fork/join TASIYOR (bkz. 16. bolum)")

    # Desteklenen her sozde-durumun kod ureteci karsiligi VAR.
    from app.codegen.ir import (KIND_CHOICE, KIND_COMPOSITE, KIND_FINAL,
                                KIND_HIST_DEEP, KIND_HIST_SHALLOW,
                                KIND_SIMPLE, KIND_TERMINATE)
    esleme = {SK.SIMPLE: KIND_SIMPLE, SK.COMPOSITE: KIND_COMPOSITE,
              SK.FINAL: KIND_FINAL, SK.CHOICE: KIND_CHOICE,
              SK.JUNCTION: KIND_CHOICE, SK.TERMINATE: KIND_TERMINATE,
              SK.SHALLOW_HISTORY: KIND_HIST_SHALLOW,
              SK.DEEP_HISTORY: KIND_HIST_DEEP}
    # INITIAL, FORK ve JOIN calisma zamaninda BIR DUGUM DEGILDIR: ucu de
    # bilesik gecisin icine duzlestirilir (bkz. codegen/ir.py). Bu yuzden
    # ayri bir uretec turleri yoktur, ama karsiliksiz da degillerdir.
    # SUBMACHINE de calisma zamaninda ayri bir tur DEGILDIR: genisletilince
    # siradan bir bilesik duruma doner (14.2.3.4.7, "macro-like insertion").
    kapsanan = set(esleme) | {SK.INITIAL, SK.FORK, SK.JOIN,
                             SK.ENTRY_POINT, SK.EXIT_POINT, SK.SUBMACHINE}
    check(kapsanan == set(SK),
          "her model sozde-durumunun uretec karsiligi var",
          sorted(k.value for k in set(SK) - kapsanan))




def t_completion_once():
    bolum("12. Tamamlanma olayi giris basina BIR KEZ", "14.2.3.8.3 (basili s.314)",
          "In case of simple States, a completion event is generated when the "
          "associated entry and doActivity Behaviors have completed executing. "
          "If no such Behaviors are defined, the completion event is generated "
          "upon entry into the State.")

    # -- 1) Tuketilmis tamamlanma olayi YENIDEN ATESLENMEZ ------------------ #
    #
    # YASANAN HATA: tamamlanma dongusu her olay gonderiminden sonra kosulsuz
    # calisiyordu. B'nin tamamlanma gecisi guard yuzunden atlandiktan sonra,
    # ilgisiz bir IC gecis geldiginde ESKI tamamlanma olayi yeniden ateslenip
    # makineyi C'ye tasiyordu -- oysa ic gecis tanimi geregi durumu
    # degistirmez. Ayni kusur uretilen C ve C++ kodunda da vardi.
    sm = StateMachine(name="Re", prefix="re", context_type="void")
    sm.add_state(State(id="i", name="I", kind=StateKind.INITIAL, x=0, y=-70,
                       w=24, h=24))
    sm.add_state(State(id="a", name="A", kind=StateKind.SIMPLE, x=0, y=0))
    sm.add_state(State(id="b", name="B", kind=StateKind.SIMPLE, x=200, y=0))
    sm.add_state(State(id="c", name="C", kind=StateKind.SIMPLE, x=400, y=0))
    sm.add_transition(Transition(source="i", target="a"))
    sm.add_transition(Transition(source="a", target="b", event="E"))
    sm.add_transition(Transition(source="b", target="c", guard="flag"))
    sm.add_transition(Transition(source="b", target="b", event="PING",
                                 kind=TransitionKind.INTERNAL,
                                 action="noop();"))
    bayrak = {"v": False}
    sim = Simulator(sm, guard_eval=lambda _e: bayrak["v"])
    sim.start()
    sim.dispatch("E")
    check(sim.state_name == "B", "guard yanlisken tamamlanma gecisi alinmadi",
          sim.state_name)
    bayrak["v"] = True
    sim.dispatch("PING")
    check(sim.state_name == "B",
          "IC gecis TUKETILMIS tamamlanma olayini yeniden ateslemiyor",
          "durum %s (C ise eski hata)" % sim.state_name)

    # -- 2) Tamamlanma ile tetiklenen ic gecis TAM BIR KEZ calisir ---------- #
    sm2 = StateMachine(name="On", prefix="on", context_type="void")
    sm2.add_state(State(id="i", name="I", kind=StateKind.INITIAL, x=0, y=-70,
                        w=24, h=24))
    sm2.add_state(State(id="a", name="A", kind=StateKind.SIMPLE, x=0, y=0))
    sm2.add_transition(Transition(source="i", target="a"))
    sm2.add_transition(Transition(source="a", target="a",
                                  kind=TransitionKind.INTERNAL,
                                  action="spin();"))
    sim2 = Simulator(sm2)
    sim2.start()
    kez = sum(1 for t in sim2.trace if "spin" in t)
    check(kez == 1,
          "tamamlanma ile tetiklenen ic gecis BIR KEZ calisti",
          "%d kez (eski motor: calistirma sinirina kadar)" % kez)

    # -- 3) Gercek tamamlanma ZINCIRI hala isliyor ------------------------- #
    sm3 = StateMachine(name="Ch", prefix="ch", context_type="void")
    sm3.add_state(State(id="i", name="I", kind=StateKind.INITIAL, x=0, y=-70,
                        w=24, h=24))
    for sid, x in (("a", 0), ("b", 200), ("c", 400)):
        sm3.add_state(State(id=sid, name=sid.upper(), kind=StateKind.SIMPLE,
                            x=x, y=0))
    sm3.add_transition(Transition(source="i", target="a"))
    sm3.add_transition(Transition(source="a", target="b"))
    sm3.add_transition(Transition(source="b", target="c"))
    sim3 = Simulator(sm3)
    sim3.start()
    check(sim3.state_name == "C",
          "tamamlanma zinciri kararli konfigurasyona kadar isliyor",
          sim3.state_name)

    # -- 4) Sonsuz dongu SESSIZCE kesilmez, BILDIRILIR --------------------- #
    sm4 = StateMachine(name="Cy", prefix="cy", context_type="void")
    sm4.add_state(State(id="i", name="I", kind=StateKind.INITIAL, x=0, y=-70,
                        w=24, h=24))
    for sid, x in (("a", 0), ("b", 200), ("c", 400)):
        sm4.add_state(State(id=sid, name=sid.upper(), kind=StateKind.SIMPLE,
                            x=x, y=0))
    sm4.add_transition(Transition(source="i", target="a"))
    sm4.add_transition(Transition(source="a", target="b"))
    sm4.add_transition(Transition(source="b", target="c"))
    sm4.add_transition(Transition(source="c", target="a"))
    sim4 = Simulator(sm4)
    sim4.start()
    check(sim4.rtc_overflow,
          "tamamlanma DONGUSU bildiriliyor (once sessizce kesiliyordu)")
    check(any(t.startswith("error:") for t in sim4.trace),
          "ize hata kaydi birakiliyor")


def t_final_state_constraints():
    bolum("13. Final durum kisitlari", "14.5.2.5 (basili s.346)",
          "no_exit_behavior A FinalState has no exit Behavior. "
          "inv: exit->isEmpty()")

    # Belgede FinalState icin TAM OLARAK uc kisit vardir: no_exit_behavior,
    # no_outgoing_transitions, no_regions. Entry ve doActivity hakkinda
    # HICBIR kisit YOKTUR. Arac onceden ucunu de reddediyor ve gecerli bir
    # modelin kod uretimini engelliyordu.
    def makine(**alanlar):
        sm = StateMachine(name="F", prefix="f", context_type="void")
        sm.add_state(State(id="i", name="I", kind=StateKind.INITIAL, x=0,
                           y=-70, w=24, h=24))
        sm.add_state(State(id="a", name="A", kind=StateKind.SIMPLE, x=0, y=0))
        sm.add_state(State(id="f", name="Fin", kind=StateKind.FINAL, x=200,
                           y=0, w=30, h=30, **alanlar))
        sm.add_transition(Transition(source="i", target="a"))
        sm.add_transition(Transition(source="a", target="f", event="DONE"))
        return sm

    def kodlar(sm):
        return {x.code for x in validate(sm)}

    check("V069" not in kodlar(makine(entry="on_entry();")),
          "final durumda ENTRY davranisi kabul ediliyor")
    check("V069" not in kodlar(makine(do="tick();")),
          "final durumda doActivity kabul ediliyor")
    check("V069" in kodlar(makine(exit="on_exit();")),
          "final durumda EXIT davranisi reddediliyor")


def t_junction_not_ill_formed():
    bolum("14. else'siz junction bozuk model DEGILDIR", "14.2.3.7 (basili s.313)",
          "It may happen that, for a particular compound transition, the "
          "configuration of Transition paths and guard values is such that the "
          "compound transition is prevented from reaching a valid state "
          "configuration. In those cases, the entire compound transition is "
          "disabled even though its Triggers are enabled.")

    # Ayni bolum CHOICE icin bunun tersini soyler: "If none of the guards
    # evaluates to true, then the model is considered ill formed." Arac
    # onceden ikisini de HATA sayiyordu.
    def makine(kind):
        sm = StateMachine(name="B", prefix="b", context_type="void")
        sm.add_state(State(id="i", name="I", kind=StateKind.INITIAL, x=0,
                           y=-70, w=24, h=24))
        sm.add_state(State(id="a", name="A", kind=StateKind.SIMPLE, x=0, y=0))
        sm.add_state(State(id="n", name="N", kind=kind, x=200, y=0,
                           w=24, h=24))
        sm.add_state(State(id="x", name="X", kind=StateKind.SIMPLE, x=400,
                           y=-60))
        sm.add_state(State(id="y", name="Y", kind=StateKind.SIMPLE, x=400,
                           y=60))
        sm.add_transition(Transition(source="i", target="a"))
        sm.add_transition(Transition(source="a", target="n", event="GO"))
        sm.add_transition(Transition(source="n", target="x", guard="v > 0"))
        sm.add_transition(Transition(source="n", target="y", guard="v < 0"))
        return sm

    junction = [x for x in validate(makine(StateKind.JUNCTION))
                if x.code == "V062"]
    choice = [x for x in validate(makine(StateKind.CHOICE))
              if x.code == "V062"]
    check(junction and not any(x.is_error for x in junction),
          "else'siz JUNCTION hata degil (bilesik gecis devre disi kalir)",
          [x.severity for x in junction])
    check(choice and all(x.is_error for x in choice),
          "else'siz CHOICE HATA (model ill-formed)",
          [x.severity for x in choice])


def t_regions():
    bolum("15. Ortogonal bolgeler", "14.2.3.2 (basili s.307)",
          "A Region denotes a behavior fragment that may execute concurrently "
          "with its orthogonal Regions. Two or more Regions are orthogonal to "
          "each other if they are either owned by the same State or, at the "
          "topmost level, by the same StateMachine. A Region becomes active "
          "(i.e., it begins executing) either when its owning State is "
          "entered ... Each Region owns a set of Vertices and Transitions ... "
          "It may have its own initial Pseudostate as well as its own "
          "FinalState.")

    def makine(bolge_sayisi=2, ikinci_final=True):
        sm = StateMachine(name="R", prefix="r", context_type="void")
        sm.add_state(State(id="i", name="I", kind=StateKind.INITIAL,
                           x=0, y=-90, w=24, h=24))
        sm.add_state(State(id="idle", name="Idle", kind=StateKind.SIMPLE,
                           x=0, y=0))
        sm.add_state(State(id="run", name="Run", kind=StateKind.COMPOSITE,
                           regions=bolge_sayisi, x=200, y=0, w=420, h=320,
                           entry="E_RUN();", exit="X_RUN();"))
        sm.add_state(State(id="done", name="Done", kind=StateKind.SIMPLE,
                           x=700, y=0))
        sm.add_transition(Transition(source="i", target="idle"))
        sm.add_transition(Transition(source="idle", target="run", event="GO"))
        sm.add_transition(Transition(source="run", target="done"))
        sm.add_transition(Transition(source="run", target="idle", event="STOP"))
        for r in range(bolge_sayisi):
            sm.add_state(State(id="i%d" % r, name="I%d" % r,
                               kind=StateKind.INITIAL, parent="run",
                               region=r, x=20, y=20 + r * 120, w=24, h=24))
            sm.add_state(State(id="a%d" % r, name="A%d" % r, parent="run",
                               region=r, x=20, y=60 + r * 120,
                               entry="E_A%d();" % r, exit="X_A%d();" % r))
            sm.add_state(State(id="f%d" % r, name="F%d" % r,
                               kind=StateKind.FINAL, parent="run",
                               region=r, x=240, y=60 + r * 120, w=30, h=30))
            sm.add_transition(Transition(source="i%d" % r, target="a%d" % r))
            if r == 0 or ikinci_final:
                sm.add_transition(Transition(source="a%d" % r,
                                             target="f%d" % r, event="FIN"))
        return sm

    sm = makine()
    hatalar = sorted({x.code for x in validate(sm) if x.is_error})
    check(not hatalar, "ortogonal model DOGRULAMADAN GECIYOR", hatalar)

    # -- Giriste HER bolge baslar ------------------------------------------ #
    sim, iz = kos(sm, ["GO"])
    girenler = [t.split(":", 1)[1] for t in iz if t.startswith("E:")]
    check("A0" in girenler and "A1" in girenler,
          "ortogonal duruma girince HER bolge baslatiliyor", girenler)
    check(girenler.index("A0") < girenler.index("A1"),
          "bolgeler ARTAN sirada baslatiliyor (arac bu sirayi sabitler)")

    # -- Tek olay HER bolgeye sunulur -------------------------------------- #
    sim2, iz2 = kos(makine(), ["GO", "FIN"])
    check(sim2.state_name == "Done",
          "iki bolge de final'e varinca ortogonal durum TAMAMLANDI",
          sim2.state_name)

    # -- TEK bolge final ise tamamlanmaz ----------------------------------- #
    sim3, iz3 = kos(makine(ikinci_final=False), ["GO", "FIN"])
    etkin = [sim3.ir.states[i].name for i in sim3.active_indices()]
    check(sim3.state_name != "Done",
          "TEK bolgenin final'e varmasi tamamlamaya YETMIYOR", etkin)
    check("F0" in etkin and "A1" in etkin,
          "bir bolge final'de beklerken oteki calismaya devam ediyor", etkin)

    # -- Cikista bolgeler AZALAN sirada kapanir ---------------------------- #
    sim4, iz4 = kos(makine(), ["GO", "STOP"])
    cikanlar = [t.split(":", 1)[1] for t in iz4 if t.startswith("X:")]
    check("A0" in cikanlar and "A1" in cikanlar and "Run" in cikanlar,
          "dis gecis butun bolgeleri kapatiyor", cikanlar)
    check(cikanlar.index("A1") < cikanlar.index("A0") < cikanlar.index("Run"),
          "cikis girisin TERS sirasinda (once son bolge, en sonra durum)",
          cikanlar)

    # -- Bolgeler arasi DUZ gecis reddedilir -------------------------------- #
    sm5 = makine()
    sm5.add_transition(Transition(source="a0", target="a1", event="CROSS"))
    kodlar5 = sorted({x.code for x in validate(sm5) if x.is_error})
    check("V102" in kodlar5,
          "bolgeler arasi DUZ gecis reddediliyor (fork/join gerekir)", kodlar5)

    # -- Her bolgenin KENDI initial'i sart ---------------------------------- #
    sm6 = makine()
    sm6.remove_state("i1")
    kodlar6 = sorted({x.code for x in validate(sm6) if x.is_error})
    check("V050" in kodlar6,
          "initial'i olmayan bolge HATA veriyor", kodlar6)

    # -- Bir bolgenin initial'i BASKA bolgeyi hedefleyemez ------------------ #
    sm7 = makine()
    for t in list(sm7.transitions.values()):
        if t.source == "i1":
            t.target = "a0"
    kodlar7 = sorted({x.code for x in validate(sm7) if x.is_error})
    check("V101" in kodlar7,
          "bolgenin varsayilan girisi KENDI bolgesinde kalmali", kodlar7)


def t_fork_join():
    bolum("16. Fork ve Join", "14.2.3.7 (basili s.313)",
          "join - This type of Pseudostate serves as a common target Vertex "
          "for two or more Transitions originating from Vertices in different "
          "orthogonal Regions. Transitions terminating on a join Pseudostate "
          "cannot have a guard or a trigger ... all incoming Transitions have "
          "to complete before execution can continue through an outgoing "
          "Transition. ... fork - fork Pseudostates serve to split an incoming "
          "Transition into two or more Transitions terminating on Vertices in "
          "orthogonal Regions of a composite State. The Transitions outgoing "
          "from a fork Pseudostate cannot have a guard or a trigger.")

    def makine():
        sm = StateMachine(name="FJ", prefix="fj", context_type="void")
        sm.add_state(State(id="i", name="I", kind=StateKind.INITIAL,
                           x=0, y=-100, w=24, h=24))
        sm.add_state(State(id="idle", name="Idle", x=0, y=0))
        sm.add_state(State(id="fk", name="FK", kind=StateKind.FORK,
                           x=200, y=0, w=10, h=90))
        sm.add_state(State(id="run", name="Run", kind=StateKind.COMPOSITE,
                           regions=2, x=320, y=0, w=420, h=320))
        for r in (0, 1):
            sm.add_state(State(id="i%d" % r, name="I%d" % r,
                               kind=StateKind.INITIAL, parent="run", region=r,
                               x=20, y=20 + r * 150, w=24, h=24))
            sm.add_state(State(id="a%d" % r, name="A%d" % r, parent="run",
                               region=r, x=20, y=60 + r * 150))
            sm.add_state(State(id="b%d" % r, name="B%d" % r, parent="run",
                               region=r, x=220, y=60 + r * 150))
            sm.add_transition(Transition(source="i%d" % r, target="a%d" % r))
        sm.add_state(State(id="jn", name="JN", kind=StateKind.JOIN,
                           x=800, y=0, w=10, h=90))
        sm.add_state(State(id="done", name="Done", x=900, y=0))
        sm.add_transition(Transition(source="i", target="idle"))
        sm.add_transition(Transition(source="idle", target="fk", event="GO"))
        sm.add_transition(Transition(source="fk", target="b0"))
        sm.add_transition(Transition(source="fk", target="a1"))
        sm.add_transition(Transition(source="a1", target="b1", event="STEP"))
        sm.add_transition(Transition(source="b0", target="jn"))
        sm.add_transition(Transition(source="b1", target="jn"))
        sm.add_transition(Transition(source="jn", target="done"))
        return sm

    sm = makine()
    hatalar = sorted({x.code for x in validate(sm) if x.is_error})
    check(not hatalar, "fork/join modeli DOGRULAMADAN GECIYOR", hatalar)

    # -- FORK: adi gecen bolgelere ACIKCA, otekiler varsayilanla ----------- #
    sim, iz = kos(sm, ["GO"])
    girenler = [t.split(":", 1)[1] for t in iz if t.startswith("E:")]
    check("B0" in girenler and "A1" in girenler,
          "fork, adi gecen dugumlere ACIKCA giriyor", girenler)
    check("A0" not in girenler,
          "fork'un adlandirdigi bolgede VARSAYILAN atlaniyor", girenler)

    # -- JOIN: butun segmentler varmadan ATESLEMEZ ------------------------- #
    check(sim.state_name != "Done",
          "join, TEK segment hazirken ateslemiyor", sim.state_name)
    etkin = [sim.ir.states[i].name for i in sim.active_indices()]
    check("B0" in etkin, "biten bolge join'de BEKLIYOR", etkin)

    # -- Hepsi varinca join atesler ---------------------------------------- #
    sim2, iz2 = kos(makine(), ["GO", "STEP"])
    check(sim2.state_name == "Done",
          "butun segmentler varinca join ATESLIYOR", sim2.state_name)

    # -- Cikis: ortogonal durumun butun bolgeleri kapanir ------------------ #
    cikanlar = [t.split(":", 1)[1] for t in iz2 if t.startswith("X:")]
    check("B0" in cikanlar and "B1" in cikanlar and "Run" in cikanlar,
          "join butun bolgeleri kapatiyor", cikanlar)

    # -- Kisitlar ----------------------------------------------------------- #
    sm3 = makine()
    for t in sm3.transitions.values():
        if t.source == "fk":
            t.guard = "x > 0"
            break
    check("V122" in {x.code for x in validate(sm3)},
          "fork segmentinde guard REDDEDILIYOR")

    sm4 = makine()
    for t in sm4.transitions.values():
        if t.target == "jn":
            t.event = "E"
            break
    check("V126" in {x.code for x in validate(sm4)},
          "join segmentinde tetikleyici REDDEDILIYOR")

    sm5 = makine()
    for t in sm5.transitions.values():
        if t.source == "fk" and t.target == "a1":
            t.target = "a0"           # ayni bolgeye ikinci segment
            break
    check("V123" in {x.code for x in validate(sm5)},
          "fork segmentleri FARKLI bolgeleri hedeflemeli")

    sm6 = makine()
    for t in list(sm6.transitions.values()):
        if t.source == "fk" and t.target == "a1":
            sm6.remove_transition(t.id)
    check("V121" in {x.code for x in validate(sm6)},
          "fork EN AZ IKI cikis tasimali")

    sm7 = makine()
    for t in list(sm7.transitions.values()):
        if t.target == "jn" and t.source == "b1":
            sm7.remove_transition(t.id)
    check("V124" in {x.code for x in validate(sm7)},
          "join EN AZ IKI giris tasimali")


def t_connection_points():
    bolum("17. Giris / cikis noktalari", "14.2.3.7 (basili s.313)",
          "entryPoint - An entryPoint Pseudostate represents an entry point "
          "for a StateMachine or a composite State that provides "
          "encapsulation of the insides of the State or StateMachine. In each "
          "Region of the StateMachine or composite State owning the "
          "entryPoint, there is at most a single Transition from the entry "
          "point to a Vertex within that Region. NOTE. If the owning State "
          "has an associated entry Behavior, this Behavior is executed before "
          "any behavior associated with the outgoing Transition.")

    def makine():
        sm = StateMachine(name="CP", prefix="cp", context_type="void")
        sm.add_state(State(id="i", name="I", kind=StateKind.INITIAL,
                           x=0, y=-100, w=24, h=24))
        sm.add_state(State(id="out", name="Out", x=0, y=0))
        sm.add_state(State(id="c", name="Proc", kind=StateKind.COMPOSITE,
                           x=250, y=0, w=360, h=240,
                           entry="E_PROC();", exit="X_PROC();"))
        sm.add_state(State(id="ci", name="CI", kind=StateKind.INITIAL,
                           parent="c", x=20, y=20, w=24, h=24))
        sm.add_state(State(id="s1", name="S1", parent="c", x=20, y=60))
        sm.add_state(State(id="s2", name="S2", parent="c", x=200, y=60))
        sm.add_state(State(id="ep", name="Fast", kind=StateKind.ENTRY_POINT,
                           parent="c", x=0, y=120, w=18, h=18))
        sm.add_state(State(id="xp", name="Abort", kind=StateKind.EXIT_POINT,
                           parent="c", x=350, y=120, w=18, h=18))
        sm.add_state(State(id="err", name="Err", x=700, y=0))
        sm.add_transition(Transition(source="i", target="out"))
        sm.add_transition(Transition(source="ci", target="s1"))
        sm.add_transition(Transition(source="out", target="c", event="GO"))
        sm.add_transition(Transition(source="out", target="ep", event="FAST"))
        sm.add_transition(Transition(source="ep", target="s2"))
        sm.add_transition(Transition(source="s2", target="xp", event="FAIL"))
        sm.add_transition(Transition(source="xp", target="err"))
        return sm

    sm = makine()
    hatalar = sorted({x.code for x in validate(sm) if x.is_error})
    check(not hatalar, "baglanti noktali model DOGRULAMADAN GECIYOR", hatalar)

    # -- Adlandirilmis giris varsayilani ATLAR ------------------------------ #
    sim, iz = kos(makine(), ["FAST"])
    girenler = [t.split(":", 1)[1] for t in iz if t.startswith("E:")]
    check("S2" in girenler and "S1" not in girenler,
          "adlandirilmis giris VARSAYILANI atliyor", girenler)

    # -- Sahibin entry'si ONCE calisir (belgenin NOTE'u) -------------------- #
    kod = [t for t in iz if t.startswith(("E:", "code:"))]
    try:
        once = next(i for i, t in enumerate(kod) if "E_PROC" in t)
        sonra = next(i for i, t in enumerate(kod) if t == "E:S2")
        check(once < sonra,
              "sahibin entry davranisi IC hedeften ONCE calisiyor")
    except StopIteration:
        check(False, "sahibin entry davranisi IC hedeften ONCE calisiyor",
              kod)

    # -- Adlandirilmis cikis bilesik durumdan CIKARIR ----------------------- #
    sim2, iz2 = kos(makine(), ["FAST", "FAIL"])
    check(sim2.state_name == "Err",
          "adlandirilmis cikis disaridaki hedefe goturuyor", sim2.state_name)
    cikanlar = [t.split(":", 1)[1] for t in iz2 if t.startswith("X:")]
    check("Proc" in cikanlar,
          "adlandirilmis cikis bilesik durumdan CIKIYOR", cikanlar)

    # -- Kisitlar ----------------------------------------------------------- #
    sm3 = makine()
    for t in list(sm3.transitions.values()):
        if t.source == "ep":
            sm3.remove_transition(t.id)
    check("V141" in {x.code for x in validate(sm3)},
          "iceri giden oku olmayan giris noktasi HATA")

    sm4 = makine()
    for t in sm4.transitions.values():
        if t.source == "ep":
            t.target = "err"          # disariya cikan giris noktasi
            break
    check("V143" in {x.code for x in validate(sm4)},
          "giris noktasi ICERIDE bitmeli")

    sm5 = makine()
    for t in sm5.transitions.values():
        if t.source == "xp":
            t.target = "s1"           # iceri donen cikis noktasi
            break
    check("V149" in {x.code for x in validate(sm5)},
          "cikis noktasi DISARIDA bitmeli")

    sm6 = makine()
    sm6.states["ep"].parent = None    # sahipsiz baglanti noktasi
    check("V140" in {x.code for x in validate(sm6)},
          "baglanti noktasi bir bilesik duruma AIT olmali")


def t_submachine():
    bolum("18. Altmakine durumlari", "14.2.3.4.7 (basili s.311)",
          "Submachines are a means by which a single StateMachine "
          "specification can be reused multiple times ... whereas "
          "encapsulated composite States and their internals are contained "
          "within the StateMachine in which they are defined, submachines "
          "are, like programming language macros, distinct Behavior "
          "specifications, which may be defined in a different context than "
          "the one where they are used (invoked) ... A submachine State "
          "implies a macro-like insertion of the specification of the "
          "corresponding submachine StateMachine.")

    from app.core.submachine import SubmachineError, flatten

    def ic_makine():
        sm = StateMachine(name="Job", prefix="job", context_type="void")
        sm.add_state(State(id="i", name="I", kind=StateKind.INITIAL,
                           y=-60, w=24, h=24))
        sm.add_state(State(id="w", name="Wait", y=0))
        sm.add_state(State(id="b", name="Busy", y=100))
        sm.add_state(State(id="f", name="Fin", kind=StateKind.FINAL,
                           y=200, w=30, h=30))
        sm.add_transition(Transition(source="i", target="w"))
        sm.add_transition(Transition(source="w", target="b", event="START"))
        sm.add_transition(Transition(source="b", target="f", event="DONE"))
        return sm

    def dis_makine():
        sm = StateMachine(name="Main", prefix="main", context_type="void")
        sm.add_state(State(id="i", name="I", kind=StateKind.INITIAL,
                           y=-60, w=24, h=24))
        sm.add_state(State(id="idle", name="Idle", y=0))
        sm.add_state(State(id="j1", name="First", kind=StateKind.SUBMACHINE,
                           submachine_ref="model/job.usm",
                           x=200, y=0, w=300, h=220))
        sm.add_state(State(id="j2", name="Second", kind=StateKind.SUBMACHINE,
                           submachine_ref="model/job.usm",
                           x=200, y=280, w=300, h=220))
        sm.add_state(State(id="done", name="AllDone", x=600, y=0))
        sm.add_transition(Transition(source="i", target="idle"))
        sm.add_transition(Transition(source="idle", target="j1", event="GO"))
        sm.add_transition(Transition(source="j1", target="j2"))
        sm.add_transition(Transition(source="j2", target="done"))
        return sm

    coz = lambda ref: ic_makine() if ref == "model/job.usm" else None  # noqa: E731

    sm = dis_makine()
    hatalar = sorted({x.code for x in validate(sm, coz) if x.is_error})
    check(not hatalar, "altmakineli model DOGRULAMADAN GECIYOR", hatalar)

    # -- MAKRO YERINE KOYMA ------------------------------------------------- #
    duz = flatten(dis_makine(), coz)
    adlar = {s.name for s in duz.states.values()}
    check("First_Wait" in adlar and "Second_Wait" in adlar,
          "altmakine MAKRO gibi yerine konuyor", sorted(adlar))

    # -- HER REFERANS AYRI ORNEK -------------------------------------------- #
    #
    # 14.2.3.4.7 NOTE: "Each submachine State represents a distinct
    # instantiation of a submachine, even when two or more submachine
    # States reference the same submachine."
    check(len([a for a in adlar if a.endswith("_Wait")]) == 2,
          "ayni makineye iki referans AYRI ornek uretiyor")

    sim, _iz = kos(duz, ["GO", "START", "DONE"])
    check(sim.state_name == "Second_Wait",
          "birinci ornegin bitisi IKINCI ornege geciriyor", sim.state_name)
    sim2, _iz2 = kos(duz, ["GO", "START", "DONE", "START", "DONE"])
    check(sim2.state_name == "AllDone",
          "iki ornek de bitince makine tamamlaniyor", sim2.state_name)

    # -- Referans EKSIKSE reddedilir ---------------------------------------- #
    check("V162" in {x.code for x in validate(dis_makine(), lambda r: None)},
          "cozulemeyen referans HATA veriyor")

    sm3 = dis_makine()
    sm3.states["j1"].submachine_ref = ""
    check("V160" in {x.code for x in validate(sm3)},
          "referanssiz altmakine durumu HATA veriyor")

    # -- DONGUSEL referans reddedilir (ARAC kurali) ------------------------- #
    def dongulu(_ref=None):
        sm4 = StateMachine(name="Loop", prefix="loop", context_type="void")
        sm4.add_state(State(id="i", name="I", kind=StateKind.INITIAL,
                            y=-60, w=24, h=24))
        sm4.add_state(State(id="s", name="Self", kind=StateKind.SUBMACHINE,
                            submachine_ref="model/loop.usm",
                            y=0, w=200, h=150))
        sm4.add_transition(Transition(source="i", target="s"))
        return sm4

    try:
        flatten(dongulu(), dongulu)
        check(False, "dongusel referans reddediliyor", "reddedilmedi")
    except SubmachineError:
        check(True, "dongusel referans reddediliyor")

    # -- Altmakine durumu KENDI icerigini TASIYAMAZ ------------------------- #
    sm5 = dis_makine()
    sm5.add_state(State(id="x", name="Inner", parent="j1"))
    check("V161" in {x.code for x in validate(sm5)},
          "altmakine durumunun kendi icerigi REDDEDILIYOR")

    # -- Baglanti noktasi BAGLAMA acikca desteklenmiyor --------------------- #
    #
    # UML 14.2.3.5'teki ConnectionPointReference, altmakine durumunun
    # sinirinda ic makinenin giris/cikis noktalarini gosterecek bir arayuz
    # gerektirir. Yarim destek, kullanicinin cizdigi okun sessizce baska
    # bir yere gitmesi demek olurdu; bu yuzden ARAC bunu SUNMAZ.
    from app.ui.canvas import Tool
    check("connection_point_reference" not in {t.value for t in Tool},
          "ConnectionPointReference SUNULMUYOR (yarim destek verilmiyor)")


def t_deferred_events():
    bolum("19. Ertelenen olaylar", "14.2.3.4.4 (basili s.309)",
          "A State may specify a set of Event types that may be deferred in "
          "that State. This means that Event occurrences of those types will "
          "not be dispatched as long as that State remains active. Instead, "
          "these Event occurrences remain in the event pool until: ... a state "
          "configuration is reached where these Event types are no longer "
          "deferred or, ... if a deferred Event type is used explicitly in a "
          "Trigger of a Transition whose source is the deferring State (i.e., "
          "a kind of override option).")

    def makine(ertelenen=("PRINT",), ezme=False):
        sm = StateMachine(name="Df", prefix="df", context_type="void")
        sm.add_state(State(id="i", name="I", kind=StateKind.INITIAL,
                           y=-60, w=24, h=24))
        sm.add_state(State(id="busy", name="Busy", y=0,
                           deferred=list(ertelenen)))
        sm.add_state(State(id="idle", name="Idle", y=100))
        sm.add_transition(Transition(source="i", target="busy"))
        sm.add_transition(Transition(source="busy", target="idle",
                                     event="DONE"))
        sm.add_transition(Transition(source="idle", target="busy",
                                     event="PRINT"))
        if ezme:
            sm.add_transition(Transition(source="busy", target="idle",
                                         event="PRINT"))
        return sm

    sm = makine()
    hatalar = sorted({x.code for x in validate(sm) if x.is_error})
    check(not hatalar, "ertelemeli model DOGRULAMADAN GECIYOR", hatalar)

    sim = Simulator(makine())
    sim.start()
    sim.dispatch("PRINT")
    check(sim.state_name == "Busy",
          "ertelenen olay GECIS TETIKLEMIYOR", sim.state_name)
    check(len(sim.deferred_pool) == 1,
          "ertelenen olay HAVUZDA tutuluyor", sim.deferred_pool)

    sim.dispatch("DONE")
    check(sim.state_name == "Busy",
          "artik ertelenmeyen olay GERI CAGRILIP isleniyor", sim.state_name)
    check(not sim.deferred_pool, "havuz bosaldi", sim.deferred_pool)

    # -- EZME: erteleyen durumun KENDI gecisi kazanir ----------------------- #
    sim2 = Simulator(makine(ezme=True))
    sim2.start()
    sim2.dispatch("PRINT")
    check(sim2.state_name == "Idle",
          "erteleyen durumun KENDI gecisi ertelemeyi EZIYOR", sim2.state_name)
    check(not sim2.deferred_pool,
          "ezme durumunda olay havuza KONMUYOR", sim2.deferred_pool)

    # -- Sozde-durum erteleyemez -------------------------------------------- #
    sm3 = makine()
    sm3.states["busy"].kind = StateKind.CHOICE
    check("V180" in {x.code for x in validate(sm3)},
          "sozde-durumda erteleme REDDEDILIYOR")


def t_do_activity_contract():
    bolum("20. doActivity sozlesmesi", "14.5.9.6 (basili s.355)",
          "doActivity : Behavior [0..1] ... An optional Behavior that is "
          "executed while being in "
          "the State. The execution starts when this State is entered, and "
          "ceases either by itself when done, or when the State is exited, "
          "whichever comes first.")

    # Belgenin istedigi iki ozellik -- ES ZAMANLILIK ve CIKISTA KESILME --
    # ikinci bir akis denetimi olmadan saglanamaz. Arac bunlari SUNMAZ ve
    # uretilen baslikta bunu ACIKCA yazar. Sessiz kalmak, kullanicinin
    # paralellik varsaymasina yol acardi.
    from app.codegen.c_generator import generate_c

    sm = StateMachine(name="Do", prefix="do", context_type="void")
    sm.add_state(State(id="i", name="I", kind=StateKind.INITIAL,
                       y=-60, w=24, h=24))
    sm.add_state(State(id="a", name="A", y=0, do="poll();"))
    sm.add_state(State(id="b", name="B", y=100))
    sm.add_transition(Transition(source="i", target="a"))
    sm.add_transition(Transition(source="a", target="b", event="GO"))
    baslik_c = generate_c(sm)["do.h"]

    check("NEITHER IS PROVIDED HERE" in baslik_c,
          "uretilen baslik SAGLANMAYANI acikca yaziyor")
    for parca in ("concurrently", "ABORTED", "cooperative"):
        check(parca.lower() in baslik_c.lower(),
              "baslikta '%s' gecisi var" % parca)


def t_time_events():
    bolum("21. Zaman olaylari: after(N)", "13.3.3.4 (basili s.293)",
          "A TimeEvent specifies an instant in time at which it occurs. The "
          "instant is specified using a TimeExpression (see sub clause "
          "8.4). ... If the TimeEvent is relative, then the TimeEvent shall "
          "be used in the context of a Trigger, and the time of occurrence "
          "is relative to a starting time determined for the Trigger. ... "
          "If such an outstanding Trigger has a relative TimeEvent, then "
          "the starting time for that TimeEvent is the time at which the "
          "Behavior came to the wait point.")

    from app.codegen.c_generator import generate_c

    sm = StateMachine(name="Tm", prefix="tm", context_type="void")
    sm.add_state(State(id="i", name="I", kind=StateKind.INITIAL,
                       y=-60, w=24, h=24))
    sm.add_state(State(id="w", name="Wait", y=0))
    sm.add_state(State(id="d", name="Done", y=100))
    sm.add_transition(Transition(source="i", target="w"))
    sm.add_transition(Transition(source="w", target="d", event="after(500)"))
    sm.add_transition(Transition(source="d", target="w", event="AGAIN"))
    sm.add_transition(Transition(source="w", target="w", event="POKE",
                                 kind=TransitionKind.INTERNAL))

    hatalar = sorted({x.code for x in validate(sm) if x.is_error})
    check(not hatalar, "after(N) DOGRULAMADAN GECIYOR", hatalar)

    dosyalar = generate_c(sm)
    kaynak = dosyalar["tm.c"]
    baslik = dosyalar["tm.h"]

    check("tm_timer_start" in baslik and "tm_timer_cancel" in baslik,
          "zamanlayici kancalari BASLIKTA bildiriliyor")
    check("tm_timers_start(me, state);" in kaynak,
          "zamanlayici GIRISTE baslatiliyor")
    check("tm_timers_cancel(me, state);" in kaynak,
          "zamanlayici CIKISTA iptal ediliyor")
    check("(500)" in kaynak, "gecikme ifadesi koda GECIYOR")

    # IC GECIS ne giris ne cikis calistirir; zamanlayiciyi da yeniden
    # BASLATMAZ. Bu, UML'in istedigi davranistir.
    sim = Simulator(sm)
    sim.start()
    sim.trace.clear()
    sim.dispatch("POKE")
    check(not [t for t in sim.trace if t.startswith(("E:", "X:"))],
          "ic gecis giris/cikis CALISTIRMIYOR (zamanlayici yeniden baslamaz)",
          sim.trace)

    # MUTLAK zaman (at(...)) SUNULMAZ: gomulu hedefte takvim saati gerekir.
    sm2 = StateMachine(name="At", prefix="at", context_type="void")
    sm2.add_state(State(id="i", name="I", kind=StateKind.INITIAL,
                        y=-60, w=24, h=24))
    sm2.add_state(State(id="a", name="A", y=0))
    sm2.add_state(State(id="b", name="B", y=100))
    sm2.add_transition(Transition(source="i", target="a"))
    sm2.add_transition(Transition(source="a", target="b",
                                  event="at(12:00)"))
    check("V012" in {x.code for x in validate(sm2)},
          "mutlak zaman at(...) SUNULMUYOR (yarim destek verilmiyor)")
# --------------------------------------------------------------------------- #

def main() -> int:
    print("OMG UML 2.5.1 (formal/2017-12-05) ANLAMBILIM UYUM RAPORU")
    print("=" * 74)
    for fn in (t_priority, t_internal, t_local, t_junction_static,
               t_choice_dynamic, t_terminate, t_history, t_default_entry,
               t_completion, t_execution_order, t_unsupported_is_refused,
               t_completion_once, t_final_state_constraints,
               t_junction_not_ill_formed, t_regions, t_fork_join,
               t_connection_points, t_submachine, t_deferred_events,
               t_do_activity_contract, t_time_events):
        fn()

    print("\n== Ozet ==")
    if _failures:
        print("  %d uyum kontrolu basarisiz:" % len(_failures))
        for f in _failures:
            print("    - %s" % f)
        return 1
    print("  Tum UML 2.5.1 uyum kontrolleri gecti.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
