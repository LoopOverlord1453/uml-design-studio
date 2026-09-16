"""Anlamsal ayrimsal test: Python referansi  vs  uretilen C  vs  uretilen C++.

    python tools/test_semantics.py [--fuzz 300]

Yontem
------
Zor UML durumlarini kapsayan makineler kurulur. Her durumun entry/exit'i ve
her gecisin eylemi bir *iz damgasi* birakir. Ayni olay dizisi uc gerceklestirme
uzerinde kosturulur ve uretilen izler BIREBIR karsilastirilir:

    Python (app/core/simulator.py)  ==  gcc ile derlenmis C  ==  g++ ile C++

Boylece "derleniyor ama yanlis calisiyor" sinifindaki hatalar yakalanir; UML
semantiginin en ince noktalari (external self-transition, local gecis,
bilesik durumun tamamlanma olayi, choice zinciri) somut olarak dogrulanir.
"""

from __future__ import annotations



import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Dict, List, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tmpdir import remove_tree   # noqa: E402

from app.codegen.c_generator import generate_c                    # noqa: E402
from app.codegen.cpp_generator import generate_cpp, pascal        # noqa: E402
from app.core.model import (State, StateKind, StateMachine,       # noqa: E402
                            Transition, TransitionKind, is_time_event)
from app.core.naming import screaming_snake                      # noqa: E402
from app.core.simulator import Simulator                          # noqa: E402
from app.core.validator import has_errors, validate               # noqa: E402

C_FLAGS = ["-std=gnu11", "-Wall", "-Wextra", "-Werror", "-O1"]
CXX_FLAGS = ["-std=c++11", "-Wall", "-Wextra", "-pedantic", "-Werror", "-O1",
             "-fno-exceptions", "-fno-rtti"]

_failures: List[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    if ok:
        print("  [ TAMAM ] %s" % label)
    else:
        _failures.append(label)
        print("  [ HATA  ] %s" % label)
        for line in detail.splitlines():
            print("           | %s" % line)


# --------------------------------------------------------------------------- #
#  Makine kurucu yardimcilari
# --------------------------------------------------------------------------- #

class Builder:
    """Iz damgalari otomatik eklenmis kucuk makineler kurar."""

    def __init__(self, name: str, prefix: str) -> None:
        self.sm = StateMachine(name=name, prefix=prefix, context_type="void",
                               user_includes="void trace_mark(const char *m);")
        self._y = 0
        self._act = 0

    def state(self, sid: str, kind=StateKind.SIMPLE, parent=None,
              mark: bool = True, regions: int = 1, region: int = 0) -> str:
        name = sid
        # UML 2.5.1: final durumlar ve sozde-durumlar entry/exit tasiyamaz.
        behaves = mark and kind in (StateKind.SIMPLE, StateKind.COMPOSITE)
        entry = 'trace_mark("E:%s");' % name if behaves else ""
        exit_ = 'trace_mark("X:%s");' % name if behaves else ""
        self._y += 100
        self.sm.add_state(State(id=sid, name=name, kind=kind, parent=parent,
                                entry=entry, exit=exit_,
                                regions=regions, region=region,
                                x=40.0, y=float(self._y)))
        return sid

    def tran(self, src: str, dst: str, event: str = "", guard: str = "",
             kind=TransitionKind.EXTERNAL, priority: int = 0,
             mark: bool = True) -> str:
        self._act += 1
        action = 'trace_mark("A:%d");' % self._act if mark else ""
        tid = "t%d" % self._act
        self.sm.add_transition(Transition(id=tid, source=src, target=dst,
                                          event=event, guard=guard, kind=kind,
                                          priority=priority, action=action))
        return tid


def machine_flat() -> Tuple[StateMachine, List[str]]:
    """Duz dongu + external self-transition (exit ve entry yeniden calisir)."""
    b = Builder("Flat", "flat")
    b.state("I", StateKind.INITIAL)
    b.state("A")
    b.state("B")
    b.tran("I", "A")
    b.tran("A", "B", "GO")
    b.tran("B", "A", "GO")
    b.tran("A", "A", "SELF")            # external self: X:A sonra E:A
    b.tran("B", "B", "SELF", kind=TransitionKind.INTERNAL)   # internal: sessiz
    b.tran("A", "B", "NOPE", guard="0")   # guard hep yanlis -> hicbir sey olmaz
    return b.sm, ["GO", "SELF", "GO", "SELF", "GO", "NOPE", "NOPE"]


def machine_composite() -> Tuple[StateMachine, List[str]]:
    """Bilesik duruma giris/cikis + ust durumdan yakalanan olay."""
    b = Builder("Comp", "comp")
    b.state("I", StateKind.INITIAL)
    b.state("Idle")
    b.state("Work", StateKind.COMPOSITE)
    b.state("WI", StateKind.INITIAL, parent="Work")
    b.state("W1", parent="Work")
    b.state("W2", parent="Work")
    b.tran("I", "Idle")
    b.tran("WI", "W1")
    b.tran("Idle", "Work", "START")
    b.tran("W1", "W2", "STEP")
    b.tran("W2", "W1", "STEP")
    b.tran("Work", "Idle", "STOP")           # ust durumdan yakalanir
    b.tran("Work", "Work", "RESTART")        # external: Work'ten cikip yeniden gir
    b.tran("Work", "W2", "JUMP", kind=TransitionKind.LOCAL)  # local: Work'ten cikma
    return b.sm, ["START", "STEP", "STOP", "START", "RESTART", "JUMP",
                  "STEP", "STOP"]


def machine_nested() -> Tuple[StateMachine, List[str]]:
    """Iki seviyeli hiyerarsi: derin giris/cikis zinciri."""
    b = Builder("Nest", "nest")
    b.state("I", StateKind.INITIAL)
    b.state("Out")
    b.state("L1", StateKind.COMPOSITE)
    b.state("L1I", StateKind.INITIAL, parent="L1")
    b.state("L2", StateKind.COMPOSITE, parent="L1")
    b.state("L2I", StateKind.INITIAL, parent="L2")
    b.state("Deep", parent="L2")
    b.state("Deep2", parent="L2")
    b.state("Side", parent="L1")
    b.tran("I", "Out")
    b.tran("L1I", "L2")
    b.tran("L2I", "Deep")
    b.tran("Out", "L1", "IN")
    b.tran("Deep", "Deep2", "NEXT")
    b.tran("Deep2", "Side", "UP")            # L2'den cikip L1 icinde kal
    b.tran("Side", "Deep", "DOWN")           # L2'ye tekrar dal
    b.tran("L1", "Out", "OUT")               # iki seviye birden cik
    return b.sm, ["IN", "NEXT", "UP", "DOWN", "OUT", "IN", "OUT"]


def machine_choice() -> Tuple[StateMachine, List[str]]:
    """Choice zinciri + else dali + guard onceligi."""
    b = Builder("Choice", "choice")
    b.state("I", StateKind.INITIAL)
    b.state("S")
    b.state("C1", StateKind.CHOICE)
    b.state("C2", StateKind.CHOICE)
    b.state("Yes")
    b.state("No")
    b.tran("I", "S")
    b.tran("S", "C1", "ASK")
    b.tran("C1", "C2", guard="1", priority=0)     # her zaman dogru
    b.tran("C1", "No", guard="else", priority=1)
    b.tran("C2", "No", guard="0", priority=0)     # her zaman yanlis
    b.tran("C2", "Yes", guard="else", priority=1)
    b.tran("Yes", "S", "BACK")
    b.tran("No", "S", "BACK")
    return b.sm, ["ASK", "BACK", "ASK", "BACK"]


def machine_final() -> Tuple[StateMachine, List[str]]:
    """Bilesik durumun icindeki final -> ust durumun tamamlanma gecisi."""
    b = Builder("Fin", "fin")
    b.state("I", StateKind.INITIAL)
    b.state("Job", StateKind.COMPOSITE)
    b.state("JI", StateKind.INITIAL, parent="Job")
    b.state("Step1", parent="Job")
    b.state("Step2", parent="Job")
    b.state("JEnd", StateKind.FINAL, parent="Job")
    b.state("Done")
    b.tran("I", "Job")
    b.tran("JI", "Step1")
    b.tran("Step1", "Step2", "NEXT")
    b.tran("Step2", "JEnd", "NEXT")
    b.tran("Job", "Done")            # completion: yalnizca JEnd'e varilinca
    return b.sm, ["NEXT", "NEXT", "NEXT"]


def machine_history() -> Tuple[StateMachine, List[str]]:
    """Sig tarih (H): duraklat/surdur son alt durumu geri yukler."""
    b = Builder("Hist", "hist")
    b.state("I", StateKind.INITIAL)
    b.state("Off")
    b.state("Run", StateKind.COMPOSITE)
    b.state("RI", StateKind.INITIAL, parent="Run")
    b.state("H", StateKind.SHALLOW_HISTORY, parent="Run", mark=False)
    b.state("A", parent="Run")
    b.state("B", parent="Run")
    b.tran("I", "Off")
    b.tran("RI", "A")
    b.tran("Off", "H", "RESUME", mark=False)     # tarih uzerinden gir
    b.tran("Run", "Off", "PAUSE")
    b.tran("A", "B", "STEP")
    b.tran("B", "A", "STEP")
    # RESUME(ilk: tarih yok -> A), STEP(B), PAUSE, RESUME(B geri), PAUSE, RESUME(B)
    return b.sm, ["RESUME", "STEP", "PAUSE", "RESUME", "PAUSE", "RESUME", "STEP"]


def machine_deep_history() -> Tuple[StateMachine, List[str]]:
    """Derin tarih (H*): ic ice bilesiklerde yaprak durumu geri yukler."""
    b = Builder("Deep", "deep")
    b.state("I", StateKind.INITIAL)
    b.state("Off")
    b.state("Outer", StateKind.COMPOSITE)
    b.state("OI", StateKind.INITIAL, parent="Outer")
    b.state("HD", StateKind.DEEP_HISTORY, parent="Outer", mark=False)
    b.state("Inner", StateKind.COMPOSITE, parent="Outer")
    b.state("II", StateKind.INITIAL, parent="Inner")
    b.state("P", parent="Inner")
    b.state("Q", parent="Inner")
    b.state("Side", parent="Outer")
    b.tran("I", "Off")
    b.tran("OI", "Inner")
    b.tran("II", "P")
    b.tran("Off", "HD", "RESUME", mark=False)
    b.tran("Outer", "Off", "PAUSE")
    b.tran("P", "Q", "STEP")
    b.tran("Q", "P", "STEP")
    b.tran("Inner", "Side", "UP")
    return b.sm, ["RESUME", "STEP", "PAUSE", "RESUME",     # Q derinden geri
                  "UP", "PAUSE", "RESUME", "STEP"]         # Side geri, sonra?


def machine_nested_final() -> Tuple[StateMachine, List[str]]:
    """IC bolgedeki final DIS composite'i tamamlamamali (UML 14.2.3.8.3).

    'go' sonrasi yaprak FB'dir (B'nin final'i); A'nin bolgesi tamamlanmadigi
    icin A'nin completion gecisi ATESLENMEMELIDIR. 'fin' B'yi terk edip A'nin
    kendi final'ine goturur; ancak o zaman A tamamlanir.
    """
    b = Builder("NestFin", "nestfin")
    b.state("I", StateKind.INITIAL)
    b.state("A", StateKind.COMPOSITE)
    b.state("AI", StateKind.INITIAL, parent="A")
    b.state("B", StateKind.COMPOSITE, parent="A")
    b.state("BI", StateKind.INITIAL, parent="B")
    b.state("S1", parent="B")
    b.state("FB", StateKind.FINAL, parent="B")
    b.state("AF", StateKind.FINAL, parent="A")
    b.state("Cout")
    b.tran("I", "A")
    b.tran("AI", "B")
    b.tran("BI", "S1")
    b.tran("S1", "FB", "GO")
    b.tran("B", "AF", "FIN")
    b.tran("A", "Cout")                  # completion: yalnizca AF'e varilinca
    return b.sm, ["GO", "FIN"]


def machine_else_order() -> Tuple[StateMachine, List[str]]:
    """Ayni oncelikte 'else' dali guard'li daldan ONCE eklenmis olsa da
    guard'li dal once denenmelidir (UML 14.2.3.4.6)."""
    b = Builder("ElseOrd", "elseord")
    b.state("I", StateKind.INITIAL)
    b.state("S")
    b.state("C", StateKind.CHOICE)
    b.state("Yes")
    b.state("No")
    b.tran("I", "S")
    b.tran("S", "C", "ASK")
    b.tran("C", "No", guard="else")      # once eklendi (kucuk id): yine de sona
    b.tran("C", "Yes", guard="1")        # guard dogru -> Yes secilmeli
    b.tran("Yes", "S", "BACK")
    b.tran("No", "S", "BACK")
    return b.sm, ["ASK", "BACK", "ASK"]


def machine_history_after_final() -> Tuple[StateMachine, List[str]]:
    """Bolge final ile tamamlandiktan sonra tarih SILINMELI; H'ye gelen gecis
    varsayilan tarih gecisini almali (UML 14.2.3.4.5)."""
    b = Builder("HistFin", "histfin")
    b.state("I", StateKind.INITIAL)
    b.state("S0")
    b.state("P", StateKind.COMPOSITE)
    b.state("PI", StateKind.INITIAL, parent="P")
    b.state("H", StateKind.SHALLOW_HISTORY, parent="P", mark=False)
    b.state("S1", parent="P")
    b.state("S2", parent="P")
    b.state("FP", StateKind.FINAL, parent="P")
    b.state("S3")
    b.tran("I", "S0")
    b.tran("PI", "S1")
    b.tran("H", "S1", mark=False)        # tarih varsayilani
    b.tran("S0", "P", "ENTER")
    b.tran("S0", "H", "RESUME", mark=False)   # tarih uzerinden gir
    b.tran("S1", "S2", "STEP")
    b.tran("S2", "FP", "FIN")
    b.tran("P", "S3")                    # completion
    b.tran("S3", "H", "BACK", mark=False)
    b.tran("P", "S0", "PAUSE")
    # ENTER(S1), STEP(S2), PAUSE(S0; tarih=S2), RESUME(tarih -> S2),
    # FIN(FP -> completion -> S3; final cikisi tarihi SILER),
    # BACK(tarih bos -> varsayilan S1), STEP(S2).
    return b.sm, ["ENTER", "STEP", "PAUSE", "RESUME", "FIN", "BACK", "STEP"]


def machine_junction_terminate() -> Tuple[StateMachine, List[str]]:
    """Junction dallanmasi + terminate sozde-durumu."""
    b = Builder("Junc", "junc")
    b.state("I", StateKind.INITIAL)
    b.state("S")
    b.state("J", StateKind.JUNCTION)
    b.state("Yes")
    b.state("No")
    b.state("Kill", StateKind.TERMINATE)
    b.tran("I", "S")
    b.tran("S", "J", "ASK")
    b.tran("J", "Yes", guard="1", priority=0)
    b.tran("J", "No", guard="else", priority=1)
    b.tran("Yes", "S", "BACK")
    b.tran("S", "Kill", "DIE")
    # DIE sonrasi ASK islenmemeli (terminate)
    return b.sm, ["ASK", "BACK", "ASK", "BACK", "DIE", "ASK", "BACK"]


MACHINES = [
    ("duz + self-transition", machine_flat),
    ("bilesik + local/external", machine_composite),
    ("iki seviyeli hiyerarsi", machine_nested),
    ("choice zinciri", machine_choice),
    ("ic final -> tamamlanma", machine_final),
    ("ic final DIS composite'i tamamlamaz", machine_nested_final),
    ("else dali her zaman en son", machine_else_order),
    ("final sonrasi tarih silinir", machine_history_after_final),
    ("sig tarih (H)", machine_history),
    ("derin tarih (H*)", machine_deep_history),
    ("junction + terminate", machine_junction_terminate),
]


def machine_completion_once() -> Tuple[StateMachine, List[str]]:
    """Tamamlanma olayi GIRIS BASINA BIR KEZ dogar.

    UML 2.5.1, 14.2.3.8.3 (basili s.314): "If no such Behaviors are
    defined, the completion event is generated upon entry into the State."

    Tamamlanma olayiyla tetiklenen bir IC gecis, durumu degistirmedigi
    icin yeni bir tamamlanma olayi DOGURMAZ; dolayisiyla tam olarak bir
    kez calismalidir. Eski surumde tamamlanma dongusu kosulsuz donuyor
    ve ayni eylemi calistirma-limitine (16) kadar TEKRARLIYORDU.
    """
    b = Builder("Once", "once")
    b.state("I", StateKind.INITIAL)
    b.state("A")
    b.state("B")
    b.tran("I", "A")
    b.tran("A", "A", kind=TransitionKind.INTERNAL)   # tamamlanma + ic gecis
    b.tran("A", "B", "GO")
    b.tran("B", "B", kind=TransitionKind.INTERNAL)   # tamamlanma + ic gecis
    return b.sm, ["GO"]


def machine_orthogonal() -> Tuple[StateMachine, List[str]]:
    """ORTOGONAL bolgeler: es zamanli giris, es zamanli olay, VE ile tamamlanma.

    UML 2.5.1, 14.2.3.2 (basili s.307) ve 14.2.3.8.3 (basili s.315):
    bir ortogonal duruma girmek BUTUN bolgelerini baslatir, tek olay
    her bolgeye sunulur ve durum ancak HER bolge final'e vardiginda
    tamamlanir.
    """
    b = Builder("Ortho", "ortho")
    b.state("I", StateKind.INITIAL)
    b.state("Idle")
    b.state("Run", StateKind.COMPOSITE, regions=2)
    b.state("MI", StateKind.INITIAL, parent="Run", region=0)
    b.state("M1", parent="Run", region=0)
    b.state("M2", parent="Run", region=0)
    b.state("MF", StateKind.FINAL, parent="Run", region=0)
    b.state("LI", StateKind.INITIAL, parent="Run", region=1)
    b.state("L1", parent="Run", region=1)
    b.state("L2", parent="Run", region=1)
    b.state("LF", StateKind.FINAL, parent="Run", region=1)
    b.state("Done")
    b.tran("I", "Idle")
    b.tran("MI", "M1")
    b.tran("LI", "L1")
    b.tran("Idle", "Run", "GO")
    b.tran("M1", "M2", "TICK")       # ayni olay IKI bolgede de gecis yapar
    b.tran("L1", "L2", "TICK")
    b.tran("M2", "MF", "FIN")
    b.tran("L2", "LF", "FIN")
    b.tran("Run", "Done")            # tamamlanma: HER IKI bolge de final
    b.tran("Run", "Idle", "ABORT")   # dis gecis: iki bolgeyi de kapatir
    b.tran("M2", "M1", "ABORT")      # DERIN gecis dis gecise ustun gelmeli
    return b.sm, ["GO", "TICK", "ABORT", "TICK", "FIN", "GO"]


def machine_orthogonal_history() -> Tuple[StateMachine, List[str]]:
    """Ortogonal durumda TARIH: her bolge KENDI kaydini tutar."""
    b = Builder("OrthoH", "orthoh")
    b.state("I", StateKind.INITIAL)
    b.state("Out")
    b.state("Top", StateKind.COMPOSITE, regions=2)
    b.state("AI", StateKind.INITIAL, parent="Top", region=0)
    b.state("A1", parent="Top", region=0)
    b.state("A2", parent="Top", region=0)
    b.state("AH", StateKind.SHALLOW_HISTORY, parent="Top", region=0)
    b.state("BI", StateKind.INITIAL, parent="Top", region=1)
    b.state("B1", parent="Top", region=1)
    b.state("B2", parent="Top", region=1)
    b.tran("I", "Out")
    b.tran("AI", "A1")
    b.tran("BI", "B1")
    b.tran("Out", "Top", "IN")
    b.tran("A1", "A2", "STEP")
    b.tran("B1", "B2", "STEP")
    b.tran("Top", "Out", "OUT")
    b.tran("Out", "AH", "BACK")      # tarihten geri don
    return b.sm, ["IN", "STEP", "OUT", "BACK", "STEP", "OUT"]


def machine_fork_join() -> Tuple[StateMachine, List[str]]:
    """FORK ile bolgelere dagilim, JOIN ile bulusma.

    UML 2.5.1, 14.2.3.7 (basili s.313): fork "an incoming Transition into
    two or more Transitions terminating on Vertices in orthogonal Regions"
    boler; join ise "all incoming Transitions have to complete before
    execution can continue through an outgoing Transition" der.
    """
    b = Builder("ForkJoin", "forkjoin")
    b.state("I", StateKind.INITIAL)
    b.state("Idle")
    b.state("FK", StateKind.FORK, mark=False)
    b.state("Work", StateKind.COMPOSITE, regions=2)
    b.state("MI", StateKind.INITIAL, parent="Work", region=0)
    b.state("M1", parent="Work", region=0)
    b.state("M2", parent="Work", region=0)
    b.state("LI", StateKind.INITIAL, parent="Work", region=1)
    b.state("L1", parent="Work", region=1)
    b.state("L2", parent="Work", region=1)
    b.state("JN", StateKind.JOIN, mark=False)
    b.state("Done")
    b.tran("I", "Idle")
    b.tran("MI", "M1")
    b.tran("LI", "L1")
    b.tran("Idle", "FK", "GO")
    b.tran("FK", "M2")            # bolge 0: ACIKCA M2
    b.tran("FK", "L1")            # bolge 1: ACIKCA L1
    b.tran("L1", "L2", "STEP")
    b.tran("M2", "JN")            # join segmenti (tamamlanma)
    b.tran("L2", "JN")
    b.tran("JN", "Done")
    b.tran("Done", "Idle", "RESET")
    return b.sm, ["GO", "STEP", "RESET", "GO", "STEP"]


def machine_connection_points() -> Tuple[StateMachine, List[str]]:
    """ADLANDIRILMIS giris ve cikis noktalari.

    UML 2.5.1, 14.2.3.7 (basili s.313): entryPoint ve exitPoint bir
    bilesik durumun icini disariya kapatir. NOTE: "If the owning State has
    an associated entry Behavior, this Behavior is executed before any
    behavior associated with the outgoing Transition."
    """
    b = Builder("Conn", "conn")
    b.state("I", StateKind.INITIAL)
    b.state("Out")
    b.state("Proc", StateKind.COMPOSITE)
    b.state("PI", StateKind.INITIAL, parent="Proc")
    b.state("S1", parent="Proc")
    b.state("S2", parent="Proc")
    b.state("EP", StateKind.ENTRY_POINT, parent="Proc", mark=False)
    b.state("XP", StateKind.EXIT_POINT, parent="Proc", mark=False)
    b.state("Err")
    b.tran("I", "Out")
    b.tran("PI", "S1")
    b.tran("S1", "S2", "NEXT")
    b.tran("Out", "Proc", "GO")        # varsayilan giris
    b.tran("Out", "EP", "FAST")        # adlandirilmis giris
    b.tran("EP", "S2")
    b.tran("S2", "XP", "FAIL")         # adlandirilmis cikis
    b.tran("XP", "Err")
    b.tran("Err", "Out", "RESET")
    b.tran("Proc", "Out", "STOP")
    return b.sm, ["GO", "NEXT", "FAIL", "RESET", "FAST", "FAIL", "RESET",
                  "GO", "STOP"]


def machine_deferred() -> Tuple[StateMachine, List[str]]:
    """ERTELENEN olaylar.

    UML 2.5.1, 14.2.3.4.4 (basili s.309): ertelenen turdeki olaylar
    "remain in the event pool until: a state configuration is reached
    where these Event types are no longer deferred or, if a deferred
    Event type is used explicitly in a Trigger of a Transition whose
    source is the deferring State".
    """
    b = Builder("Defer", "defer")
    b.state("I", StateKind.INITIAL)
    b.state("Busy")
    b.state("Idle")
    b.state("Extra")
    b.sm.states["Busy"].deferred = ["PRINT"]
    b.tran("I", "Busy")
    b.tran("Busy", "Idle", "DONE")
    b.tran("Idle", "Busy", "PRINT")        # ertelenen olay burada tuketilir
    b.tran("Idle", "Extra", "GO")
    b.tran("Extra", "Busy", "BACK")
    return b.sm, ["PRINT", "DONE", "PRINT", "GO", "BACK", "PRINT", "DONE"]


def machine_time_event() -> Tuple[StateMachine, List[str]]:
    """after(N) ZAMAN OLAYI.

    Olayin kendisi siradan bir olaydir; fark, uretilen kodun durumun
    GIRISINDE zamanlayiciyi baslatip CIKISINDA iptal etmesidir. Anlamsal
    karsilastirma acisindan bu, adi `after(...)` olan bir olaydir.
    """
    b = Builder("Timed", "timed")
    b.state("I", StateKind.INITIAL)
    b.state("Wait")
    b.state("Done")
    b.tran("I", "Wait")
    b.tran("Wait", "Done", "after(500)")
    b.tran("Done", "Wait", "AGAIN")
    b.tran("Wait", "Wait", "POKE", kind=TransitionKind.INTERNAL)
    return b.sm, ["POKE", "after(500)", "AGAIN", "after(500)"]


def machine_region_completion() -> Tuple[StateMachine, List[str]]:
    """BIR bolgedeki IC GECIS, OTEKININ tamamlanmasini bozmamali.

    Tek bir makine capinda tamamlanma bayragi vardi: yuksek numarali
    bolgedeki bir ic gecis, dusuk numarali bolgenin girisinden dogan
    bayragi siliyor ve o bolgenin diyagramda cizili tamamlanma gecisi
    HIC alinmiyordu. Sonuc, bolgelerin cizim SIRASINA bagliydi.
    """
    b = Builder("RegComp", "regcomp")
    b.state("I", StateKind.INITIAL)
    b.state("Run", StateKind.COMPOSITE, regions=2)
    b.state("AI", StateKind.INITIAL, parent="Run", region=0)
    b.state("A0", parent="Run", region=0)
    b.state("A1", parent="Run", region=0)
    b.state("A2", parent="Run", region=0)
    b.state("BI", StateKind.INITIAL, parent="Run", region=1)
    b.state("B0", parent="Run", region=1)
    b.tran("I", "Run")
    b.tran("AI", "A0")
    b.tran("BI", "B0")
    b.tran("A0", "A1", "TICK")
    b.tran("A1", "A2")                              # TAMAMLANMA gecisi
    b.tran("B0", "B0", "TICK", kind=TransitionKind.INTERNAL)
    b.tran("A2", "A0", "RESET")
    return b.sm, ["TICK", "RESET", "TICK"]


def machine_region_terminate() -> Tuple[StateMachine, List[str]]:
    """Bir bolgedeki TERMINATE butun makineyi durdurmali.

    Alma dongusunde `terminated` bakilmiyordu: bir bolge makineyi
    oldurdukten sonra otekilerin secilmis gecisleri isleniyor, olmus bir
    makinede exit/effect/entry calisiyordu.
    """
    b = Builder("RegTerm", "regterm")
    b.state("I", StateKind.INITIAL)
    b.state("Run", StateKind.COMPOSITE, regions=2)
    b.state("AI", StateKind.INITIAL, parent="Run", region=0)
    b.state("A0", parent="Run", region=0)
    b.state("Kill", StateKind.TERMINATE, parent="Run", region=0, mark=False)
    b.state("BI", StateKind.INITIAL, parent="Run", region=1)
    b.state("B0", parent="Run", region=1)
    b.state("B1", parent="Run", region=1)
    b.tran("I", "Run")
    b.tran("AI", "A0")
    b.tran("BI", "B0")
    b.tran("A0", "Kill", "BOOM")
    b.tran("B0", "B1", "BOOM")
    return b.sm, ["BOOM", "BOOM"]


def machine_deep_history_regions() -> Tuple[StateMachine, List[str]]:
    """DERIN TARIH, hatirlanan ORTOGONAL durumun HER bolgesini geri yukler.

    UML 2.5.1, 14.2.3.7 (basili s.312), deepHistory maddesi: "This type of
    Pseudostate is a kind of variable that represents the most recent
    active state configuration of its owning Region. ... a Transition
    terminating on this Pseudostate implies restoring the Region to that
    same state configuration".

    Geri yuklenen sey bir DURUM degil, bir KONFIGURASYONDUR: hatirlanan
    alt durum ortogonalse onun butun bolgeleri kayittan doner.

    KARDES bolgeler bu kuralin disindadir. Tarih sozde-durumu YALNIZCA
    kendi bolgesini temsil eder; bilesik durumun oteki bolgeleri, ona
    girilirken varsayilan etkinlestirmeyle acilir (14.2.3.2). Model bu
    yuzden tarihi TEK bolgeli `Top`a koyar ve ortogonalligi bir alt
    seviyeye, `Par`a indirir -- olculen sey tam olarak kusurun oldugu
    yerdir.

    YASANAN HATA: derin yuruyus tek ardil izliyordu; `Par` geri gelince
    yalnizca ILK bolgesi kayittan, ikincisi VARSAYILANDAN aciliyordu.
    Ikinci bolgenin hatirlanan alt agaci kayboluyor ve initial eylemi --
    gomulu kodda gercek bir yan etki -- bir daha calisiyordu.
    """
    b = Builder("DeepReg", "deepreg")
    b.state("I", StateKind.INITIAL)
    b.state("Out")
    b.state("Top", StateKind.COMPOSITE)
    b.state("TI", StateKind.INITIAL, parent="Top")
    b.state("DH", StateKind.DEEP_HISTORY, parent="Top", mark=False)
    b.state("Par", StateKind.COMPOSITE, parent="Top", regions=2)
    # Her IKI bolgenin hatirlanani da BILESIKTIR. Ikinci bolgenin icini
    # varsayilandan acan bir gerceklestirme ancak boyle yakalanir: alt
    # durumlar basit olsaydi "varsayilani ac" ile "kaydi geri yukle"
    # ayni izi verir ve kusur testten gorunmez kalirdi.
    b.state("PAI", StateKind.INITIAL, parent="Par", region=0)
    b.state("Ac", StateKind.COMPOSITE, parent="Par", region=0)
    b.state("AI", StateKind.INITIAL, parent="Ac")
    b.state("A1", parent="Ac")
    b.state("A2", parent="Ac")
    b.state("PBI", StateKind.INITIAL, parent="Par", region=1)
    b.state("Bc", StateKind.COMPOSITE, parent="Par", region=1)
    b.state("BI", StateKind.INITIAL, parent="Bc")
    b.state("B1", parent="Bc")
    b.state("B2", parent="Bc")
    b.tran("I", "Out")
    b.tran("TI", "Par")
    b.tran("PAI", "Ac")
    b.tran("PBI", "Bc")
    b.tran("AI", "A1")
    b.tran("BI", "B1")
    b.tran("Out", "Top", "IN")
    b.tran("A1", "A2", "STEP")
    b.tran("B1", "B2", "STEP")       # IKINCI bolge de ilerler
    b.tran("Top", "Out", "OUT")
    b.tran("Out", "DH", "BACK", mark=False)
    return b.sm, ["IN", "STEP", "OUT", "BACK", "OUT", "BACK"]


# MACHINES yukarida tanimlandigi icin buraya EKLENIR; liste icinde
# adlandirilsaydi islev henuz tanimli olmadigindan NameError verirdi.
MACHINES.append(("tamamlanma olayi GIRIS BASINA BIR KEZ",
                 machine_completion_once))
MACHINES.append(("ORTOGONAL bolgeler", machine_orthogonal))
MACHINES.append(("ORTOGONAL bolgelerde tarih", machine_orthogonal_history))
MACHINES.append(("FORK ve JOIN", machine_fork_join))
MACHINES.append(("GIRIS / CIKIS noktalari", machine_connection_points))
MACHINES.append(("ERTELENEN olaylar", machine_deferred))
MACHINES.append(("ZAMAN olayi after(N)", machine_time_event))
MACHINES.append(("BOLGE tamamlanmasi ic gecisle bozulmaz",
                 machine_region_completion))
MACHINES.append(("bir bolgede TERMINATE hepsini durdurur",
                 machine_region_terminate))
MACHINES.append(("DERIN TARIH butun bolgeleri geri yukler",
                 machine_deep_history_regions))


# --------------------------------------------------------------------------- #
#  Kosturma
# --------------------------------------------------------------------------- #

def guard_eval(expr: str) -> bool:
    """Testlerde guard'lar yalnizca '1' veya '0' sabitidir."""
    return expr.strip() not in ("0", "false")


def python_trace(sm: StateMachine, events: List[str]) -> List[str]:
    sim = Simulator(sm, guard_eval=guard_eval)
    sim.start()
    for ev in events:
        sim.dispatch(ev)
    # 'code' ve 'A' kayitlari ayni damgayi tasiyor; yalnizca damgalari birakalim.
    # Bir kayit BIRDEN COK damga tasiyabilir: junction yollari duzlestirilirken
    # yol uzerindeki eylemler tek bir eylem metninde birlestirilir.
    out: List[str] = []
    for item in sim.trace:
        kind, _, detail = item.partition(":")
        if kind in ("code", "A"):
            marks = re.findall(r'"([^"]*)"', detail)
            out.extend(marks or [detail])
    return out


def c_main(prefix: str, events: List[str], timers: bool = False) -> str:
    lines = [
        "#include <stdio.h>",
        '#include "%s.h"' % prefix,
        "",
        "void trace_mark(const char *m) { printf(\"%s\\n\", m); }",
        "",
    ]
    if timers:
        # ZAMANLAYICI KANCALARI testte BOS birakilir: olayi test
        # dizisi zaten elle gonderiyor. Amac uretilen kodun
        # DERLENDIGINI ve Python ile ayni davrandigini gostermek.
        lines += [
            "void %s_timer_start(%s_t *me, uint8_t state, uint8_t event,"
            % (prefix, prefix),
            "                    uint32_t delay)",
            "{ (void)me; (void)state; (void)event; (void)delay; }",
            "void %s_timer_cancel(%s_t *me, uint8_t state, uint8_t event)"
            % (prefix, prefix),
            "{ (void)me; (void)state; (void)event; }",
            "",
        ]
    lines += [
        "int main(void)",
        "{",
        "    %s_t sm;" % prefix,
        "    %s_construct(&sm, 0);" % prefix,
        "    %s_start(&sm);" % prefix,
    ]
    for ev in events:
        # Olay ADI ureteclerdeki KURALLA uretilir: after(500) gibi bir
        # zaman olayi buyuk harfe cevrilerek gecerli bir C adi olmaz
        # (TIMED_EVENT_AFTER(500) bir islev cagrisi gorunurdu).
        lines.append("    (void)%s_dispatch(&sm, %s_EVENT_%s);"
                     % (prefix, prefix.upper(), screaming_snake(ev)))
    lines += ["    return 0;", "}", ""]
    return "\n".join(lines)


def cpp_main(cls: str, ns: str, events: List[str],
             event_names: Dict[str, str], timers: bool = False) -> str:
    lines = [
        "#include <cstdio>",
        '#include "%s.hpp"' % cls,
        "",
        "// Bildirim uretilen .hpp icinde (user_includes) C++ baglantisiyla yer alir.",
        'void trace_mark(const char *m) { std::printf("%s\\n", m); }',
        "",
    ]
    if timers:
        lines += [
            "namespace %s {" % ns,
            "void timerStart(%s& machine, std::uint8_t state," % cls,
            "                std::uint8_t event, std::uint32_t delay)",
            "{ (void)machine; (void)state; (void)event; (void)delay; }",
            "void timerCancel(%s& machine, std::uint8_t state," % cls,
            "                 std::uint8_t event)",
            "{ (void)machine; (void)state; (void)event; }",
            "}  // namespace %s" % ns,
            "",
        ]
    lines += [
        "int main()",
        "{",
        "    %s::%s sm;" % (ns, cls),
        "    sm.start();",
    ]
    for ev in events:
        lines.append("    static_cast<void>(sm.dispatch(%s::%s::Event::%s));"
                     % (ns, cls, event_names[ev]))
    lines += ["    return 0;", "}", ""]
    return "\n".join(lines)


def compile_and_run(work: str, compiler: str, flags, sources, exe: str):
    out_path = os.path.join(work, exe)
    proc = subprocess.run([compiler] + list(flags) + ["-I", work] +
                          list(sources) + ["-o", out_path],
                          cwd=work, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        return None, proc.stdout
    run = subprocess.run([out_path], cwd=work, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace")
    if run.returncode != 0:
        return None, run.stdout
    return [ln.strip() for ln in run.stdout.splitlines() if ln.strip()], ""


def run_machine(label: str, sm: StateMachine, events: List[str],
                work: str, have_gcc: bool, have_gxx: bool) -> None:
    issues = validate(sm)
    if has_errors(issues):
        check(False, "%s: model gecerli" % label,
              "\n".join(str(i) for i in issues if i.is_error))
        return

    expected = python_trace(sm, events)
    zamanli = any(is_time_event(ev) for ev in sm.events())

    files = {}
    files.update(generate_c(sm))
    files.update(generate_cpp(sm))
    for name, text in files.items():
        with open(os.path.join(work, name), "w", encoding="utf-8",
                  newline="\n") as fh:
            fh.write(text)

    # ---- C
    if have_gcc:
        main_name = "%s_main.c" % sm.prefix
        with open(os.path.join(work, main_name), "w", encoding="utf-8",
                  newline="\n") as fh:
            fh.write(c_main(sm.prefix, events, timers=zamanli))
        actual, err = compile_and_run(work, "gcc", C_FLAGS,
                                      ["%s.c" % sm.prefix, main_name],
                                      "%s_c.exe" % sm.prefix)
        if actual is None:
            check(False, "%s: C derleme/kosum" % label, err)
        else:
            check(actual == expected, "%s: C izi Python referansiyla ayni" % label,
                  "C      : %s\nPython : %s" % (actual, expected))

    # ---- C++
    if have_gxx:
        cls = pascal(sm.name)
        ev_map = {ev: pascal(ev) for ev in sm.events()}
        main_name = "%s_main.cpp" % sm.prefix
        with open(os.path.join(work, main_name), "w", encoding="utf-8",
                  newline="\n") as fh:
            fh.write(cpp_main(cls, sm.prefix, events, ev_map,
                              timers=zamanli))
        actual, err = compile_and_run(work, "g++", CXX_FLAGS,
                                      ["%s.cpp" % cls, main_name],
                                      "%s_cpp.exe" % sm.prefix)
        if actual is None:
            check(False, "%s: C++ derleme/kosum" % label, err)
        else:
            check(actual == expected, "%s: C++ izi Python referansiyla ayni" % label,
                  "C++    : %s\nPython : %s" % (actual, expected))


def machine_canary() -> Tuple[StateMachine, List[str]]:
    """Kanarya icin model: TARIH var ve durum sayisi bolge sayisindan COK.

    Kusur tam olarak bu oranda ortaya cikiyordu: bolge basina bir kayit
    tutan `history_` dizisi, DURUM sayisi kadar donen bir dongude
    siliniyordu. Durum sayisi bolge sayisini ne kadar asarsa tasma o
    kadar buyuk olur.
    """
    b = Builder("Canary", "canary")
    b.state("I", StateKind.INITIAL)
    b.state("Off")
    b.state("Run", StateKind.COMPOSITE)
    b.state("RI", StateKind.INITIAL, parent="Run")
    b.state("H", StateKind.SHALLOW_HISTORY, parent="Run", mark=False)
    for ad in ("S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7"):
        b.state(ad, parent="Run")
    b.tran("I", "Off")
    b.tran("RI", "S0")
    b.tran("Off", "Run", "GO")
    for a, z in (("S0", "S1"), ("S1", "S2"), ("S2", "S3"), ("S3", "S4"),
                 ("S4", "S5"), ("S5", "S6"), ("S6", "S7")):
        b.tran(a, z, "STEP")
    b.tran("Run", "Off", "STOP")
    b.tran("Off", "H", "RESUME", mark=False)
    return b.sm, ["GO", "STEP", "STEP", "STOP", "RESUME"]


#: Kanarya bayti. 0x00 ya da 0xFF SECILMEZ: ikisi de "zaten oyleydi"
#: diye yorumlanabilecek siradan degerlerdir.
KANARYA = "0xA5"


def canary_main_cpp(cls: str, ns: str) -> str:
    """Nesneyi bir tamponun ICINE kurar ve ARKASINDAKI baytlara bakar."""
    return "\n".join([
        '#include <cstdio>',
        '#include <cstring>',
        '#include <new>',
        '#include "%s.hpp"' % cls,
        '',
        '// Uretilen sinif, on ekle adlandirilmis bir ISIM ALANI icindedir.',
        'using %s::%s;' % (ns, cls),
        '',
        '// Model davranislari bu kancayi cagirir. Kanarya kosumunda iz',
        '// BASILMAZ: tek olculen sey nesnenin arkasindaki baytlardir ve',
        '// ciktinin tek satir olmasi karsilastirmayi kesinlestirir.',
        'void trace_mark(const char *m) { (void)m; }',
        '',
        '/* Nesnenin arkasinda birakilan bekci alani. Sinifi son uyesinden',
        '   tasan bir yazma buraya duser; gercek programda bu, cagiranin',
        '   yigini ya da komsu bir nesne olurdu. */',
        'static const std::size_t kGuard = 128U;',
        '',
        'int main()',
        '{',
        '    alignas(%s) static unsigned char buffer[sizeof(%s) + kGuard];'
        % (cls, cls),
        '    std::memset(buffer, %s, sizeof(buffer));' % KANARYA,
        '',
        '    %s *machine = new (buffer) %s();' % (cls, cls),
        '',
        '    // START ONCESI: hicbir durum etkin olmamalidir. Dizi',
        '    // deger-baslatilirsa her bolge 0 numarali durumu etkin',
        '    // gosterir -- 0 GECERLI bir durum indisidir.',
        '    if (machine->isIn(static_cast<%s::State>(0))) {' % cls,
        '        std::printf("PRESTART-ACTIVE\\n");',
        '    } else {',
        '        std::printf("PRESTART-CLEAR\\n");',
        '    }',
        '',
        '    machine->start();',
        '',
        '    std::size_t bozuk = 0U;',
        '    for (std::size_t i = sizeof(%s); i < sizeof(buffer); ++i) {' % cls,
        '        if (buffer[i] != static_cast<unsigned char>(%s)) {' % KANARYA,
        '            ++bozuk;',
        '        }',
        '    }',
        '    machine->~%s();' % cls,
        '    if (bozuk == 0U) {',
        '        std::printf("CANARY-OK\\n");',
        '    } else {',
        '        std::printf("CANARY-BROKEN %lu\\n",',
        '                    static_cast<unsigned long>(bozuk));',
        '    }',
        '    return 0;',
        '}',
        '',
    ])


def canary_main_c(prefix: str) -> str:
    """C icin ayni kanarya: yapinin ARKASINDAKI baytlar bozulmamali."""
    return "\n".join([
        '#include <stdio.h>',
        '#include <string.h>',
        '#include <stddef.h>',
        '#include "%s.h"' % prefix,
        '',
        '/* Model davranislari bu kancayi cagirir. Kanarya kosumunda iz',
        '   BASILMAZ: tek olculen sey yapinin arkasindaki baytlardir. */',
        'void trace_mark(const char *m) { (void)m; }',
        '',
        'static const size_t kGuard = 128U;',
        '',
        'int main(void)',
        '{',
        '    static unsigned char buffer[sizeof(%s_t) + 128U];' % prefix,
        '    %s_t *machine = (%s_t *)(void *)buffer;' % (prefix, prefix),
        '    size_t i;',
        '    size_t bozuk = 0U;',
        '',
        '    memset(buffer, %s, sizeof(buffer));' % KANARYA,
        '    %s_construct(machine, NULL);' % prefix,
        '',
        '    /* START ONCESI: hicbir durum etkin olmamalidir. */',
        '    if (%s_is_in(machine, (%s_state_t)0)) {' % (prefix, prefix),
        '        printf("PRESTART-ACTIVE\\n");',
        '    } else {',
        '        printf("PRESTART-CLEAR\\n");',
        '    }',
        '',
        '    %s_start(machine);' % prefix,
        '',
        '    for (i = sizeof(%s_t); i < sizeof(buffer); i++) {' % prefix,
        '        if (buffer[i] != (unsigned char)%s) {' % KANARYA,
        '            bozuk++;',
        '        }',
        '    }',
        '    (void)kGuard;',
        '    if (bozuk == 0U) {',
        '        printf("CANARY-OK\\n");',
        '    } else {',
        '        /* %zu KULLANILMAZ: mingw printf onu tanimaz ve',
        '           -Werror=format ile derleme durur. */',
        '        printf("CANARY-BROKEN %lu\\n", (unsigned long)bozuk);',
        '    }',
        '    return 0;',
        '}',
        '',
    ])


def canary(work: str, have_gcc: bool, have_gxx: bool) -> None:
    """Uretilen kod NESNESININ DISINA yaziyor mu.

    NEDEN AYRI BIR TEST: iz karsilastirmasi bu kusuru YAPISAL OLARAK
    goremez. Nesnenin sonundan tasan bir yazma hicbir durum degisikligi
    uretmez, hicbir entry/exit izini degistirmez ve yazilan bayt bir daha
    okunmaz. Uc gerceklestirme de birebir ayni izi verirken uretilen C++
    kurucusu her kurulusta cagiranin yigin bellegini bozuyordu.
    """
    print("\n== nesne sinirlari (kanarya) ==")
    sm, _olaylar = machine_canary()
    dosyalar = {}
    dosyalar.update(generate_c(sm))
    dosyalar.update(generate_cpp(sm))
    for ad, metin in dosyalar.items():
        with open(os.path.join(work, ad), "w", encoding="utf-8",
                  newline="\n") as fh:
            fh.write(metin)

    if have_gcc:
        ad = "%s_canary.c" % sm.prefix
        with open(os.path.join(work, ad), "w", encoding="utf-8",
                  newline="\n") as fh:
            fh.write(canary_main_c(sm.prefix))
        cikti, hata = compile_and_run(work, "gcc", C_FLAGS,
                                      ["%s.c" % sm.prefix, ad],
                                      "%s_canary_c.exe" % sm.prefix)
        if cikti is None:
            check(False, "kanarya: C derleme/kosum", hata)
        else:
            check(cikti == ["PRESTART-CLEAR", "CANARY-OK"],
                  "C: start oncesi etkin durum yok, tampon bozulmuyor",
                  "cikti = %s" % cikti)

    if have_gxx:
        cls = pascal(sm.name)
        ad = "%s_canary.cpp" % sm.prefix
        with open(os.path.join(work, ad), "w", encoding="utf-8",
                  newline="\n") as fh:
            fh.write(canary_main_cpp(cls, sm.prefix))
        cikti, hata = compile_and_run(work, "g++", CXX_FLAGS,
                                      ["%s.cpp" % cls, ad],
                                      "%s_canary_cpp.exe" % sm.prefix)
        if cikti is None:
            check(False, "kanarya: C++ derleme/kosum", hata)
        else:
            check(cikti == ["PRESTART-CLEAR", "CANARY-OK"],
                  "C++: start oncesi etkin durum yok, tampon bozulmuyor",
                  "cikti = %s" % cikti)


def fuzz(work: str, have_gcc: bool, rounds: int) -> None:
    """Rastgele uzun olay dizileriyle ayni karsilastirmayi tekrarlar."""
    if not have_gcc:
        print("  [ATLANDI] fuzz: gcc yok")
        return
    random.seed(20260910)
    sm, _ = machine_nested()
    all_events = sm.events()
    mismatches = 0
    for round_no in range(4):
        events = [random.choice(all_events) for _ in range(rounds // 4)]
        expected = python_trace(sm, events)
        sub = os.path.join(work, "fuzz%d" % round_no)
        os.makedirs(sub, exist_ok=True)
        for name, text in generate_c(sm).items():
            with open(os.path.join(sub, name), "w", encoding="utf-8",
                      newline="\n") as fh:
                fh.write(text)
        with open(os.path.join(sub, "m.c"), "w", encoding="utf-8",
                  newline="\n") as fh:
            fh.write(c_main(sm.prefix, events))
        actual, err = compile_and_run(sub, "gcc", C_FLAGS,
                                      ["%s.c" % sm.prefix, "m.c"], "f.exe")
        if actual != expected:
            mismatches += 1
            print("           | tur %d ayristi (%s)" % (round_no, err[:200]))
    check(mismatches == 0,
          "fuzz: %d rastgele olay, C ile Python birebir ayni" % rounds)


def main() -> int:
    rounds = 400
    if "--fuzz" in sys.argv:
        rounds = int(sys.argv[sys.argv.index("--fuzz") + 1])

    have_gcc = shutil.which("gcc") is not None
    have_gxx = shutil.which("g++") is not None
    if not have_gcc:
        print("UYARI: gcc bulunamadi, yalnizca Python referansi calisacak.")

    work = tempfile.mkdtemp(prefix="usd_sem_")
    print("Calisma dizini: %s\n" % work)

    for label, factory in MACHINES:
        print("== %s ==" % label)
        sm, events = factory()
        run_machine(label, sm, events, work, have_gcc, have_gxx)
        print()

    print("== rastgele olay dizileri ==")
    canary(work, have_gcc, have_gxx)
    fuzz(work, have_gcc, rounds)

    print("\n== Ozet ==")
    if _failures:
        print("  %d kontrol basarisiz. Dizin korundu: %s" % (len(_failures), work))
        return 1
    print("  Uc gerceklestirme de birebir ayni davraniyor.")
    remove_tree(work)
    return 0


if __name__ == "__main__":
    sys.exit(main())
