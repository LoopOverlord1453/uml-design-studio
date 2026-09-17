"""Bulunup duzeltilmis hatalarin geri gelmedigini dogrular.

    python tools/test_regressions.py

Buradaki her vaka, gercekten yasanmis bir hatanin en kucuk yeniden uretimidir:
derlenmeyen kod, sessiz veri kaybi ya da cokme. Bir vaka basarisiz olursa o
hata geri gelmis demektir.

Derleyici (gcc/g++) ya da git yoksa ilgili bolumler ATLANDI sayilir.
"""

from __future__ import annotations



import json
import os

os.environ.setdefault("UMLSTUDIO_SETTINGS_SCOPE", "test")   # kullanici ayarlarina DOKUNMA
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tmpdir import make_tree, remove_tree   # noqa: E402
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.codegen.c_generator import generate_c                    # noqa: E402
from app.codegen.class_c_generator import generate_class_c        # noqa: E402
from app.codegen.class_cpp_generator import generate_class_cpp    # noqa: E402
from app.codegen.cpp_generator import generate_cpp, pascal        # noqa: E402
from app.core.class_model import (Attribute, ClassModel,          # noqa: E402
                                  Operation, Parameter, Relation,
                                  RelationKind, Stereotype, UmlClass)
from app.core.class_validator import validate_classes             # noqa: E402
from app.core.git_backend import Repo                             # noqa: E402
from app.core.model import (State, StateKind, StateMachine,       # noqa: E402
                            Transition, TransitionKind)
from app.core import uml_spec                                     # noqa: E402
from app.core.clipboard import (copy_fragment, fragment_summary,  # noqa: E402
                                paste_fragment)
from app.core.naming import screaming_snake                       # noqa: E402
from app.core.samples import demo_machine                         # noqa: E402
from app.core.simulator import Simulator                          # noqa: E402
from app.core.validator import validate                           # noqa: E402
from app.core.workspace import Workspace, WorkspaceError          # noqa: E402

_failures = []
_skipped = 0


def _yaz(metin: str) -> None:
    """Konsol kodlamasi ne olursa olsun yazar.

    Windows konsolu cp1254 oldugunda arayuzden gelen bir sembol
    (ornegin "▣") `UnicodeEncodeError` ile SURECI DUSURUYORDU: kusuru
    BILDIREN satir, kusurun kendisinden once testi cokertiyordu.
    Tanilama ciktisi asla testi oldurmemeli.
    """
    try:
        print(metin)
    except UnicodeEncodeError:
        kodlama = getattr(sys.stdout, "encoding", None) or "ascii"
        print(metin.encode(kodlama, "replace").decode(kodlama, "replace"))


def check(ok: bool, label: str, detail: str = "") -> None:
    if ok:
        _yaz("  [ TAMAM ] %s" % label)
    else:
        _failures.append(label)
        _yaz("  [ HATA  ] %s" % label)
        for line in str(detail).splitlines():
            _yaz("           | %s" % line)


def skip(label: str) -> None:
    global _skipped
    _skipped += 1
    print("  [ATLANDI] %s" % label)


def codes(sm: StateMachine):
    return {i.code for i in validate(sm) if i.is_error}


def class_codes(cm: ClassModel):
    return {i.code for i in validate_classes(cm) if i.is_error}


# --------------------------------------------------------------------------- #
#  1) Uretecin kendi sembolleriyle cakisan adlar
# --------------------------------------------------------------------------- #

def machine(state_name: str = "Alpha", event_name: str = "GO",
            name: str = "Probe") -> StateMachine:
    sm = StateMachine(name=name, prefix="probe", context_type="void")
    sm.add_state(State(id="i", name="Init", kind=StateKind.INITIAL, y=0))
    sm.add_state(State(id="a", name="Beta", kind=StateKind.SIMPLE, y=100))
    sm.add_state(State(id="b", name=state_name, kind=StateKind.SIMPLE, y=200))
    sm.add_transition(Transition(id="t0", source="i", target="a"))
    sm.add_transition(Transition(id="t1", source="a", target="b",
                                 event=event_name))
    sm.add_transition(Transition(id="t2", source="b", target="a", event="BACK"))
    return sm


def test_reserved_names() -> None:
    print("== 1. Uretec sembolleriyle cakisan adlar ==")

    # <PREFIX>_STATE_COUNT / _STATE_NONE makrolari durum enum'unu bozardi.
    check("V013" in codes(machine(state_name="Count")),
          "durum adi 'Count' hata veriyor (V013)")
    check("V013" in codes(machine(state_name="None")),
          "durum adi 'None' hata veriyor (V013)")

    # <PREFIX>_EVENT_COUNT / _INVALID / _COMPLETION
    for bad in ("Count", "Invalid", "Completion"):
        check("V014" in codes(machine(event_name=bad)),
              "olay adi '%s' hata veriyor (V014)" % bad)

    # C++ 'Event::Completion' ile cakisan PascalCase
    check("V017" in codes(machine(event_name="Completion_")),
          "olay adi 'Completion_' C++ cakismasi olarak yakalaniyor (V017)")

    # Iki olay ayni PascalCase'e duserse C++ enum'u bozulur.
    sm = machine()
    sm.add_state(State(id="c", name="Gamma", kind=StateKind.SIMPLE, y=300))
    sm.add_transition(Transition(id="t3", source="b", target="c",
                                 event="MY_EVENT"))
    sm.add_transition(Transition(id="t4", source="c", target="a",
                                 event="MyEvent"))
    check("V016" in codes(sm),
          "'MY_EVENT' + 'MyEvent' ayni C++ sabitine dustugu icin hata (V016)")

    # Gecerli adlar engellenmemeli (asiri kisitlama denetimi)
    for good in ("State", "Event", "Context", "Running", "Idle"):
        check(not codes(machine(state_name=good)),
              "gecerli durum adi '%s' engellenmiyor" % good)

    # Bosluklu / simgeli makine adi C++ sinif adini bozmamali.
    check(pascal("Traffic Light") == "TrafficLight",
          "bosluklu makine adi PascalCase'e cevriliyor", pascal("Traffic Light"))
    check(not codes(machine(name="Traffic Light")),
          "bosluklu makine adi hata uretmiyor")


def test_reserved_names_compile() -> None:
    """Sinir adlarla uretilen kod GERCEKTEN derleniyor mu."""
    print("\n== 2. Sinir adlarla uretilen kodun derlenmesi ==")
    if not (shutil.which("gcc") and shutil.which("g++")):
        skip("gcc/g++ bulunamadi")
        return

    work = tempfile.mkdtemp(prefix="usd_reg_")
    sm = machine(name="Traffic Light")
    files = {}
    files.update(generate_c(sm))
    files.update(generate_cpp(sm))
    for fname, text in files.items():
        with open(os.path.join(work, fname), "w", encoding="utf-8",
                  newline="\n") as fh:
            fh.write(text)

    c_flags = ["-std=c99", "-Wall", "-Wextra", "-pedantic", "-Werror", "-c"]
    cxx_flags = ["-std=c++11", "-Wall", "-Wextra", "-pedantic", "-Werror",
                 "-fno-exceptions", "-fno-rtti", "-c"]
    c = subprocess.run(["gcc"] + c_flags + ["probe.c", "-o", "c.o"], cwd=work,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True)
    check(c.returncode == 0, "bosluklu makine adiyla C derlendi", c.stdout)
    cls = pascal(sm.name)
    cpp = subprocess.run(["g++"] + cxx_flags + ["%s.cpp" % cls, "-o", "x.o"],
                         cwd=work, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True)
    check(cpp.returncode == 0, "bosluklu makine adiyla C++ derlendi", cpp.stdout)
    remove_tree(work)


# --------------------------------------------------------------------------- #
#  UML 2.5.1 semantigi
# --------------------------------------------------------------------------- #

def test_uml_semantics() -> None:
    print("\n== 3. UML 2.5.1 semantigi ==")

    # -- 'else' dali, kullanicinin verdigi oncelik ne olursa olsun EN SONDA
    #    denenmelidir (14.2.3.4.6). Aksi halde guard'li dallar olu koda doner.
    sm = StateMachine(name="Grade", prefix="grade", context_type="void")
    sm.add_state(State(id="i", name="I", kind=StateKind.INITIAL, y=0))
    sm.add_state(State(id="idle", name="Idle", kind=StateKind.SIMPLE, y=100))
    sm.add_state(State(id="pick", name="Pick", kind=StateKind.CHOICE, y=200))
    sm.add_state(State(id="hi", name="High", kind=StateKind.SIMPLE, y=300))
    sm.add_state(State(id="lo", name="Low", kind=StateKind.SIMPLE, y=400))
    sm.add_transition(Transition(id="t0", source="i", target="idle"))
    sm.add_transition(Transition(id="t1", source="idle", target="pick",
                                 event="ASK"))
    # 'else' dalina KUCUK oncelik verilmis: yine de en sona konmali.
    sm.add_transition(Transition(id="t_else", source="pick", target="lo",
                                 guard="else", priority=0))
    sm.add_transition(Transition(id="t_hi", source="pick", target="hi",
                                 guard="v > 10", priority=5))
    check(not codes(sm), "oncelikli else modeli gecerli")
    order = [t.id for t in sm.outgoing("pick")]
    check(order[-1] == "t_else", "else dali en sonda deneniyor", str(order))
    sim = Simulator(sm, guard_eval=lambda _e: True)
    sim.start()
    sim.dispatch("ASK")
    check(sim.state_name == "High",
          "guard dogruyken guard'li dal secildi", sim.state_name)

    # -- terminate: makine hicbir durumdan CIKMAZ (14.2.3.4.7)
    sm = StateMachine(name="Term", prefix="term", context_type="void")
    sm.add_state(State(id="i", name="I", kind=StateKind.INITIAL, y=0))
    sm.add_state(State(id="a", name="A", kind=StateKind.SIMPLE, y=100,
                       exit="on_exit();", do="on_do();"))
    sm.add_state(State(id="k", name="Kill", kind=StateKind.TERMINATE, y=200))
    sm.add_transition(Transition(id="t0", source="i", target="a"))
    sm.add_transition(Transition(id="t1", source="a", target="k", event="DIE"))
    sim = Simulator(sm)
    sim.start()
    sim.trace.clear()
    sim.dispatch("DIE")
    check(not any(t.startswith("X:") for t in sim.trace),
          "terminate'e girerken exit davranisi calismadi", str(sim.trace))
    check(sim.is_terminated(), "makine sonlandi")
    sim.trace.clear()
    sim.do_activity()
    check(not sim.trace, "sonlanmis makinede do davranisi calismadi",
          str(sim.trace))

    # -- junction STATIK dallanmadir: guard'lar kaynak durumdan CIKMADAN
    #    once degerlendirilir (14.2.3.4.4). Choice ise dinamiktir.
    sm = StateMachine(name="Junc", prefix="junc", context_type="void")
    sm.add_state(State(id="i", name="I", kind=StateKind.INITIAL, y=0))
    sm.add_state(State(id="s", name="S", kind=StateKind.SIMPLE, y=100))
    sm.add_state(State(id="j", name="J", kind=StateKind.JUNCTION, y=200))
    sm.add_state(State(id="x", name="X", kind=StateKind.SIMPLE, y=300))
    sm.add_state(State(id="y", name="Y", kind=StateKind.SIMPLE, y=400))
    sm.add_transition(Transition(id="t0", source="i", target="s"))
    sm.add_transition(Transition(id="t1", source="s", target="j", event="GO",
                                 action="step_one();"))
    sm.add_transition(Transition(id="t2", source="j", target="x", guard="flag",
                                 action="step_two();"))
    sm.add_transition(Transition(id="t3", source="j", target="y", guard="else"))
    check(not codes(sm), "junction modeli gecerli")
    from app.codegen.ir import build_ir
    ir = build_ir(sm)
    from_s = [t for t in ir.transitions if ir.states[t.source].name == "S"]
    targets = sorted(ir.states[t.target].name for t in from_s)
    check(targets == ["X", "Y"],
          "junction zinciri gercek hedeflere duzlestirildi", str(targets))
    check(all(ir.states[t.target].name != "J" for t in ir.transitions),
          "gecis tablosunda artik junction hedefi yok")
    sim = Simulator(sm, guard_eval=lambda _e: False)
    sim.start()
    sim.trace.clear()
    sim.dispatch("GO")
    check(sim.state_name == "Y", "guard yanlisken else dali alindi",
          sim.state_name)
    check(sim.trace and sim.trace[0] == "X:S",
          "karar S'ten cikmadan ONCE verildi", str(sim.trace))

    # -- final durumda YALNIZCA exit yasaktir (14.5.2.5, basili s.346)
    #
    # BU KONTROL DUZELTILDI. Onceden entry/exit/do ucunun de reddedilmesi
    # bekleniyordu; belgeden birebir okundu, FinalState kisitlari TAM OLARAK
    # uctur ve entry ile doActivity ONLARDAN BIRI DEGILDIR:
    #
    #   no_exit_behavior        - "A FinalState has no exit Behavior."
    #   no_outgoing_transitions
    #   no_regions
    #
    # Entry'yi reddetmek GECERLI bir modelin kod uretimini engelliyordu.
    def _final_makinesi(**alanlar):
        sm = StateMachine(name="Fin", prefix="fin", context_type="void")
        sm.add_state(State(id="i", name="I", kind=StateKind.INITIAL, y=0))
        sm.add_state(State(id="a", name="A", kind=StateKind.SIMPLE, y=100))
        sm.add_state(State(id="f", name="F", kind=StateKind.FINAL, y=200,
                           **alanlar))
        sm.add_transition(Transition(id="t0", source="i", target="a"))
        sm.add_transition(Transition(id="t1", source="a", target="f",
                                     event="END"))
        return sm

    check("V069" not in codes(_final_makinesi(entry="on_entry();")),
          "final durumda ENTRY davranisi kabul ediliyor (UML yasaklamaz)")
    check("V069" not in codes(_final_makinesi(do="tick();")),
          "final durumda doActivity kabul ediliyor (UML yasaklamaz)")
    check("V069" in codes(_final_makinesi(exit="on_exit();")),
          "final durumda EXIT davranisi reddediliyor (no_exit_behavior)")

    # -- sozde-duruma kendine gecis makineyi asili birakir
    sm = StateMachine(name="Loop", prefix="loop", context_type="void")
    sm.add_state(State(id="i", name="I", kind=StateKind.INITIAL, y=0))
    sm.add_state(State(id="a", name="A", kind=StateKind.SIMPLE, y=100))
    sm.add_state(State(id="c", name="C", kind=StateKind.CHOICE, y=200))
    sm.add_state(State(id="b", name="B", kind=StateKind.SIMPLE, y=300))
    sm.add_transition(Transition(id="t0", source="i", target="a"))
    sm.add_transition(Transition(id="t1", source="a", target="c", event="GO"))
    sm.add_transition(Transition(id="t2", source="c", target="c", guard="x"))
    sm.add_transition(Transition(id="t3", source="c", target="b", guard="else"))
    check("V070" in codes(sm), "sozde-duruma self-transition reddedildi")

    # -- Junction yolundaki eylemler AYRI AYRI sonlandirilmali. Arac noktali
    #    virgulsuz eylem yazmaya izin verir; parcalar ham yapistirilirsa
    #    'cnt++' + 'hits++;' birlesir ve uretilen kod derlenmez.
    sm = StateMachine(name="JSemi", prefix="jsemi", context_type="void",
                      user_includes="extern int cnt; extern int hits;")
    sm.add_state(State(id="i", name="I", kind=StateKind.INITIAL, y=0))
    sm.add_state(State(id="a", name="A", kind=StateKind.SIMPLE, y=100))
    sm.add_state(State(id="j", name="J", kind=StateKind.JUNCTION, y=200))
    sm.add_state(State(id="b", name="B", kind=StateKind.SIMPLE, y=300))
    sm.add_transition(Transition(id="t0", source="i", target="a"))
    sm.add_transition(Transition(id="t1", source="a", target="j", event="GO",
                                 action="cnt++"))       # noktali virgul YOK
    sm.add_transition(Transition(id="t2", source="j", target="b", guard="else",
                                 action="hits++;"))
    sm.add_transition(Transition(id="t3", source="b", target="a", event="BACK"))
    check(not codes(sm), "junction eylem modeli gecerli", str(codes(sm)))
    from app.codegen.ir import CodegenError, build_ir
    combined = build_ir(sm).actions
    check(any(a.startswith("cnt++;") for a in combined),
          "birlesik eylemde her parca ayri sonlandirildi", str(combined))
    if shutil.which("gcc") and shutil.which("g++"):
        work = tempfile.mkdtemp(prefix="usd_reg_j_")
        files = dict(generate_c(sm))
        files.update(generate_cpp(sm))
        for fname, text in files.items():
            with open(os.path.join(work, fname), "w", encoding="utf-8",
                      newline="\n") as fh:
                fh.write(text)
        proc = subprocess.run(
            ["gcc", "-std=c99", "-Wall", "-Wextra", "-pedantic", "-Werror",
             "-c", "jsemi.c", "-o", "o.o"], cwd=work, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True)
        check(proc.returncode == 0, "junction eylemli model C olarak derlendi",
              proc.stdout)
        proc = subprocess.run(
            ["g++", "-std=c++11", "-Wall", "-Wextra", "-pedantic", "-Werror",
             "-fno-exceptions", "-fno-rtti", "-c", "%s.cpp" % pascal(sm.name),
             "-o", "x.o"], cwd=work, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True)
        check(proc.returncode == 0, "junction eylemli model C++ olarak derlendi",
              proc.stdout)
        remove_tree(work)
    else:
        skip("gcc/g++ yok: junction eylem derlemesi")

    # -- Cozulemeyen junction zinciri SESSIZCE atilamaz: atilirsa kullanicinin
    #    cizdigi gecis kaybolur ve olay uyarisiz yok sayilir.
    sm = StateMachine(name="JCyc", prefix="jcyc", context_type="void")
    sm.add_state(State(id="i", name="I", kind=StateKind.INITIAL, y=0))
    sm.add_state(State(id="s", name="S", kind=StateKind.SIMPLE, y=100))
    sm.add_state(State(id="j1", name="J1", kind=StateKind.JUNCTION, y=200))
    sm.add_state(State(id="j2", name="J2", kind=StateKind.JUNCTION, y=300))
    sm.add_transition(Transition(id="t0", source="i", target="s"))
    sm.add_transition(Transition(id="t1", source="s", target="j1", event="ASK"))
    sm.add_transition(Transition(id="t2", source="j1", target="j2", guard="x"))
    sm.add_transition(Transition(id="t3", source="j1", target="j2", guard="else"))
    sm.add_transition(Transition(id="t4", source="j2", target="j1", guard="y"))
    sm.add_transition(Transition(id="t5", source="j2", target="j1", guard="else"))
    check("V074" in codes(sm), "dongusel junction zinciri reddedildi",
          str(codes(sm)))
    try:
        build_ir(sm)
        check(False, "dongusel zincirde IR uretimi durdu")
    except CodegenError:
        check(True, "dongusel zincirde IR CodegenError yukseltti")

    def junction_chain(length: int) -> StateMachine:
        sm = StateMachine(name="JDeep", prefix="jdeep", context_type="void")
        sm.add_state(State(id="i", name="I", kind=StateKind.INITIAL, y=0))
        sm.add_state(State(id="s", name="S", kind=StateKind.SIMPLE, y=100))
        sm.add_state(State(id="end", name="End", kind=StateKind.SIMPLE, y=900))
        sm.add_transition(Transition(id="t0", source="i", target="s"))
        sm.add_transition(Transition(id="t1", source="s", target="j0",
                                     event="ASK"))
        for k in range(length):
            sm.add_state(State(id="j%d" % k, name="J%d" % k,
                               kind=StateKind.JUNCTION, y=200 + k * 40))
            sm.add_state(State(id="n%d" % k, name="N%d" % k,
                               kind=StateKind.SIMPLE, x=400, y=200 + k * 40))
            nxt = "j%d" % (k + 1) if k + 1 < length else "end"
            sm.add_transition(Transition(id="g%d" % k, source="j%d" % k,
                                         target=nxt, guard="g%d" % k))
            sm.add_transition(Transition(id="e%d" % k, source="j%d" % k,
                                         target="n%d" % k, guard="else"))
            sm.add_transition(Transition(id="b%d" % k, source="n%d" % k,
                                         target="s", event="BACK%d" % k))
        sm.add_transition(Transition(id="tb", source="end", target="s",
                                     event="DONE"))
        return sm

    deep = junction_chain(8)
    check(not codes(deep), "8 adimli junction zinciri gecerli", str(codes(deep)))
    sim = Simulator(deep, guard_eval=lambda _e: True)
    sim.start()
    sim.dispatch("ASK")
    check(sim.state_name == "End",
          "8 adimli zincir dogru hedefe vardi", sim.state_name)

    too_deep = junction_chain(9)
    check("V075" in codes(too_deep), "9 adimli junction zinciri reddedildi",
          str(codes(too_deep)))
    try:
        build_ir(too_deep)
        check(False, "cok derin zincirde IR uretimi durdu")
    except CodegenError:
        check(True, "cok derin zincirde IR CodegenError yukseltti")


# --------------------------------------------------------------------------- #
#  Sinif diyagrami: uretilen sembol ad uzayi
# --------------------------------------------------------------------------- #

def _compile_class_c(cm: ClassModel):
    """Uretilen C'yi derler; (rc, cikti) verir. Derleyici yoksa (None, '')."""
    if not shutil.which("gcc"):
        return None, ""
    work = tempfile.mkdtemp(prefix="usd_reg_cls_")
    files = generate_class_c(cm)
    for name, text in files.items():
        with open(os.path.join(work, name), "w", encoding="utf-8",
                  newline="\n") as fh:
            fh.write(text)
    proc = subprocess.run(["gcc", "-std=c99", "-Wall", "-Wextra", "-pedantic",
                           "-c", "%s.c" % cm.prefix, "-o", "out.o"], cwd=work,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True)
    remove_tree(work)
    return proc.returncode, proc.stdout


def test_class_symbols() -> None:
    print("\n== 4. Sinif diyagrami: uretilen sembol ad uzayi ==")

    # snake() ayni sembole dusen iki sinif adi
    cm = ClassModel(name="Demo", prefix="demo")
    cm.add_class(UmlClass(id="c1", name="MyClass", x=0, y=0))
    cm.add_class(UmlClass(id="c2", name="my_class", x=0, y=200))
    check("C037" in class_codes(cm),
          "snake() cakismasi yapan sinif adlari reddedildi")

    # statik nitelik ile ayni adli islem: her ikisi de dosya kapsamli
    cm = ClassModel(name="Demo", prefix="demo")
    cm.add_class(UmlClass(id="c1", name="Counter", x=0, y=0,
                          attributes=[Attribute(name="total", type="int32_t",
                                                static=True)],
                          operations=[Operation(name="total",
                                                return_type="int32_t")]))
    check("C060" in class_codes(cm),
          "statik nitelik / islem sembol cakismasi reddedildi")

    # '<sinif>_as_<arayuz>' adaptoruyle cakisan islem adi
    cm = ClassModel(name="Demo", prefix="demo")
    cm.add_class(UmlClass(id="p", name="Printer",
                          stereotype=Stereotype.INTERFACE, x=0, y=0,
                          operations=[Operation(name="emit")]))
    cm.add_class(UmlClass(id="d", name="Doc", x=0, y=200,
                          operations=[Operation(name="emit"),
                                      Operation(name="as_printer")]))
    cm.add_relation(Relation(id="r", source="d", target="p",
                             kind=RelationKind.REALIZATION))
    check("C060" in class_codes(cm),
          "arayuz adaptoruyle cakisan islem adi reddedildi")

    # realization hedefi arayuz olmali (UML: InterfaceRealization)
    cm = ClassModel(name="Demo", prefix="demo")
    cm.add_class(UmlClass(id="b", name="Base", x=0, y=0,
                          operations=[Operation(name="run")]))
    cm.add_class(UmlClass(id="d", name="Impl", x=0, y=200,
                          operations=[Operation(name="run")]))
    cm.add_relation(Relation(id="r", source="d", target="b",
                             kind=RelationKind.REALIZATION))
    check("C033" in class_codes(cm),
          "arayuz olmayan realization hedefi reddedildi")

    # kalitilan islemin imzasi uyusmali
    cm = ClassModel(name="Demo", prefix="demo")
    cm.add_class(UmlClass(id="i", name="IRun", stereotype=Stereotype.INTERFACE,
                          x=0, y=0, operations=[Operation(name="go")]))
    cm.add_class(UmlClass(id="m", name="Motor", x=0, y=200,
                          operations=[Operation(name="go", params=[
                              Parameter(name="speed", type="int32_t")])]))
    cm.add_relation(Relation(id="r", source="m", target="i",
                             kind=RelationKind.REALIZATION))
    check("C065" in class_codes(cm), "farkli imzali override reddedildi")

    # STATIK 'base' niteligi struct'a girmez -> engellenmemeli (yanlis pozitif)
    cm = ClassModel(name="Demo", prefix="demo")
    cm.add_class(UmlClass(id="s", name="Shape", x=0, y=0))
    cm.add_class(UmlClass(id="c", name="Circle", x=0, y=200,
                          attributes=[Attribute(name="base", type="int32_t",
                                                static=True, default="7")]))
    cm.add_relation(Relation(id="r", source="c", target="s",
                             kind=RelationKind.GENERALIZATION))
    check(not class_codes(cm), "statik 'base' niteligi engellenmiyor",
          str(class_codes(cm)))
    rc, out = _compile_class_c(cm)
    if rc is None:
        skip("gcc yok: statik 'base' derlemesi")
    else:
        check(rc == 0, "statik 'base' niteligiyle C derlendi", out)

    # sinif tipli nitelik, parcasinin KURUCUSUYLA kurulmali (C == C++)
    cm = ClassModel(name="Demo", prefix="demo")
    cm.add_class(UmlClass(id="cf", name="Conf", x=0, y=0,
                          attributes=[Attribute(name="k", type="int32_t",
                                                default="5")]))
    cm.add_class(UmlClass(id="ap", name="App", x=0, y=300,
                          attributes=[Attribute(name="cfg", type="Conf")]))
    check(not class_codes(cm), "sinif tipli nitelik modeli gecerli")
    source = generate_class_c(cm)["demo.c"]
    body = source[source.find("void app_init"):]
    body = body[:body.find("\n}")]
    check("conf_init(&self->cfg);" in body,
          "sinif tipli nitelik conf_init() ile kuruldu", body)
    rc, out = _compile_class_c(cm)
    if rc is not None:
        check(rc == 0, "sinif tipli nitelikle C derlendi", out)

    # UML '~' (package) gorunurlugu C++'ta public bolume dusmeli
    cm = ClassModel(name="Demo", prefix="demo")
    cm.add_class(UmlClass(id="c", name="Channel", x=0, y=0,
                          operations=[Operation(name="trim", visibility="-"),
                                      Operation(name="reset",
                                                visibility="~")]))
    header = generate_class_cpp(cm)["Demo.hpp"]
    decl = header[header.find("class Channel\n"):]
    before_reset = decl[:decl.find("void reset();")]
    check(before_reset.rstrip().endswith("public uretildi")
          and "public:" in before_reset.split("private:")[-1],
          "'~' gorunurlugu icin public: etiketi acildi", before_reset[-160:])


# --------------------------------------------------------------------------- #
#  2) Model kalicilig
# --------------------------------------------------------------------------- #

def test_persistence() -> None:
    print("\n== 5. Model kaliciligi ==")

    # Ust durumu kopuk bir durum, kaydetmede SESSIZCE silinmemeli.
    src = os.path.join(ROOT, "examples", "blinky.usm")
    with open(src, encoding="utf-8") as fh:
        data = json.load(fh)
    for st in data["states"]:
        if st["id"] == "s_on":
            st["parent"] = "s_missing"
    sm = StateMachine.from_dict(data)
    saved = [s["id"] for s in json.loads(sm.to_json())["states"]]
    check("s_on" in saved, "kopuk ust durumlu durum kaydedilen dosyada duruyor")
    check(len(saved) == len(sm.states), "hicbir durum dusmedi",
          "%d != %d" % (len(saved), len(sm.states)))

    # Dairesel hiyerarside de kayip olmamali (dogrulayici V021 ile uyarir).
    cyc = {
        "schema": 1, "type": "state_machine", "name": "C", "prefix": "c",
        "description": "", "context_type": "void", "user_includes": "",
        "states": [
            {"id": "i", "name": "I", "kind": "initial", "parent": None},
            {"id": "a", "name": "A", "kind": "simple", "parent": "b"},
            {"id": "b", "name": "B", "kind": "simple", "parent": "a"},
        ],
        "transitions": [{"id": "t", "source": "i", "target": "a"}],
    }
    out = [s["id"] for s in json.loads(StateMachine.from_dict(cyc).to_json())["states"]]
    check(sorted(out) == ["a", "b", "i"], "dongudeki durumlar da kaydedildi",
          str(out))

    # Gidis-donus birebir olmali.
    with open(src, encoding="utf-8") as fh:
        original = StateMachine.from_json(fh.read())
    once = original.to_json()
    check(once == StateMachine.from_json(once).to_json(),
          "to_json -> from_json -> to_json birebir ayni")

    # Belge turu icerikten anlasilmali (uzantidan degil).
    from app.ui.document import KIND_CLASS, KIND_STATE, detect_kind
    from app.core.samples import demo_class_model
    check(detect_kind(once) == KIND_STATE, "durum makinesi taniniyor")
    check(detect_kind(demo_class_model().to_json()) == KIND_CLASS,
          "sinif diyagrami taniniyor")
    check(detect_kind("{}") is None, "bos JSON taninmiyor")
    check(detect_kind("bu json degil") is None, "JSON olmayan metin taninmiyor")
    # 'type' alani olmayan ESKI dosyalar da taninmali (geriye donuk uyumluluk)
    legacy = json.loads(once)
    legacy.pop("type", None)
    check(detect_kind(json.dumps(legacy)) == KIND_STATE,
          "tur alani olmayan eski dosya da taniniyor")
    legacy_class = json.loads(demo_class_model().to_json())
    legacy_class.pop("type", None)
    check(detect_kind(json.dumps(legacy_class)) == KIND_CLASS,
          "tur alani olmayan eski sinif dosyasi da taniniyor")


def test_workspace_encoding() -> None:
    print("\n== 6. Calisma alani: bozuk kodlamali dosya ==")
    root = tempfile.mkdtemp(prefix="usd_reg_ws_")
    ws = Workspace(root=root)
    ws.ensure_layout()
    ws.write_generated({"m.c": "int x;\n"})

    # Ekipten biri uretilen dosyayi baska bir kodlamayla kaydetmis olabilir.
    with open(os.path.join(ws.generated_path, "m.c"), "wb") as fh:
        fh.write(b"/* g\xfcncellendi */\n")            # cp1254
    try:
        written = ws.write_generated({"m.c": "int x;\n"})
        check(written == ["generated/m.c"],
              "UTF-8 olmayan dosyanin uzerine yazildi (cokme yok)", str(written))
    except (WorkspaceError, Exception) as exc:          # noqa: BLE001
        check(False, "UTF-8 olmayan dosyada istisna: %s" % type(exc).__name__,
              str(exc))

    with open(os.path.join(ws.generated_path, "m.c"), "wb") as fh:
        fh.write(b"\x00\x01\x02\xff")
    try:
        ws.write_generated({"m.c": "int x;\n"})
        check(True, "ikili icerikli dosyada da cokme yok")
    except Exception as exc:                            # noqa: BLE001
        check(False, "ikili dosyada istisna: %s" % type(exc).__name__, str(exc))

    check(ws.write_generated({"m.c": "int x;\n"}) == [],
          "degismeyen dosya yeniden yazilmadi")
    remove_tree(root)


# --------------------------------------------------------------------------- #
#  3) Git arka ucu
# --------------------------------------------------------------------------- #

def _new_repo():
    root = tempfile.mkdtemp(prefix="usd_reg_git_")
    repo = Repo(root)
    repo.init()
    for key, value in (("user.name", "Tester"), ("user.email", "t@e.st")):
        subprocess.run(["git", "config", key, value], cwd=root,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return root, repo


def _write(root: str, name: str, text: str) -> None:
    with open(os.path.join(root, name), "w", encoding="utf-8",
              newline="\n") as fh:
        fh.write(text)


def test_git() -> None:
    print("\n== 7. Git arka ucu ==")
    if not shutil.which("git"):
        skip("git bulunamadi")
        return

    # -- joker karakterli dosya adi pathspec olarak yorumlanmamali
    root, repo = _new_repo()
    _write(root, "foo[1].c", "int a;\n")
    _write(root, "foo1.c", "int b;\n")
    repo.stage(["foo[1].c", "foo1.c"])
    repo.commit("ilk")
    _write(root, "foo[1].c", "int a = 1;\n")
    _write(root, "foo1.c", "int b = 2;\n")

    repo.stage(["foo[1].c"])
    staged = sorted(f.path for f in repo.status().staged())
    check(staged == ["foo[1].c"], "stage yalnizca istenen dosyayi hazirladi",
          str(staged))
    repo.unstage(["foo[1].c"])
    text = repo.diff("foo[1].c")
    check(text.count("diff --git") == 1, "diff tek dosya gosterdi")

    repo.discard(["foo[1].c"])
    with open(os.path.join(root, "foo1.c"), encoding="utf-8") as fh:
        other = fh.read()
    check(other == "int b = 2;\n",
          "discard BASKA dosyanin degisikligini korudu", repr(other))
    remove_tree(root)

    # -- kontrol karakterli mesaj/yazar commit'i dusurmemeli
    root, repo = _new_repo()
    _write(root, "a.txt", "1\n")
    repo.stage(["a.txt"])
    repo.commit("bir")
    _write(root, "a.txt", "2\n")
    repo.stage(["a.txt"])
    repo.commit("iki", author_name="Ad\x01Soyad", author_email="x@y.z")
    log = repo.log()
    check(len(log) == 2, "kontrol karakterli yazar adiyla commit gecmiste",
          str(len(log)))
    check(sum(1 for c in log if c.is_head) == 1, "HEAD isaretlendi")

    _write(root, "a.txt", "3\n")
    repo.stage(["a.txt"])
    repo.commit("konu\x01devam\x02son")
    check(repo.log()[0].subject == "konu\x01devam\x02son",
          "konudaki kontrol karakterleri korundu",
          repr(repo.log()[0].subject))

    # -- ayrik HEAD yer tutucusu dal sayilmamali
    first = repo.log()[-1].sha
    repo.checkout(first)
    branches = repo.branches()
    check(all(not b.startswith("(") for b in branches),
          "ayrik HEAD yer tutucusu dal listesinde yok", str(branches))
    remove_tree(root)

    # -- virgullu dal adi tek ref olmali
    root, repo = _new_repo()
    _write(root, "a.txt", "1\n")
    repo.stage(["a.txt"])
    repo.commit("bir")
    code = subprocess.run(["git", "branch", "a,b"], cwd=root,
                          stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode
    if code == 0:
        refs = repo.log()[0].refs
        check("a,b" in refs and "a" not in refs,
              "virgullu dal adi tek ref olarak okundu", str(refs))
    else:
        skip("bu git surumu 'a,b' dal adini kabul etmiyor")
    remove_tree(root)


# --------------------------------------------------------------------------- #
#  4) Arayuz
# --------------------------------------------------------------------------- #

def test_ui() -> None:
    print("\n== 8. Arayuz ==")
    from PyQt6.QtCore import QPointF
    from PyQt6.QtWidgets import QApplication, QPlainTextEdit

    import app.ui.main_window as mw
    from app.core.samples import demo_class_model, demo_machine
    from app.ui.main_window import MainWindow, app_settings
    from app.ui.theme import stylesheet, ui_font

    messages = []

    class _Dialog:
        next_open = ""
        next_save = ""

        @staticmethod
        def getOpenFileName(*_a, **_k):
            return _Dialog.next_open, "x"

        @staticmethod
        def getSaveFileName(*_a, **_k):
            return _Dialog.next_save, "x"

        @staticmethod
        def getExistingDirectory(*_a, **_k):
            return ""

    class _Box:
        Icon = type("Icon", (), {"Warning": 0, "Critical": 1})

        class StandardButton:
            Yes = 1
            No = 0

        @staticmethod
        def critical(*a, **_k):
            messages.append(a[1] if len(a) > 1 else "")
            return 0

        @staticmethod
        def information(*a, **_k):
            messages.append(a[1] if len(a) > 1 else "")
            return 0

        @staticmethod
        def warning(*a, **_k):
            return 0

        @staticmethod
        def question(*_a, **_k):
            return 0

    real_dialog, real_box = mw.QFileDialog, mw.QMessageBox
    mw.QFileDialog, mw.QMessageBox = _Dialog, _Box

    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setFont(ui_font(9))
    app.setStyleSheet(stylesheet())
    win = MainWindow()
    win.resize(1600, 900)
    win.show()
    win.canvas.auto_edit = False
    win.class_canvas.auto_edit = False
    app.processEvents()
    win._rebuild_views()
    app.processEvents()

    try:
        # -- Depo sekmesinde gorunmeyen diyagram silinmemeli
        before = len(win.doc.machine.states)
        win.mode_tabs.setCurrentIndex(2)
        app.processEvents()
        win.select_all()
        win._delete_selection()
        app.processEvents()
        check(len(win.doc.machine.states) == before,
              "Depo sekmesinde Ctrl+A + Del diyagrami silmiyor",
              "%d -> %d" % (before, len(win.doc.machine.states)))
        check(not win.a_select_all.isEnabled() and not win.a_delete.isEnabled(),
              "Depo sekmesinde tuval eylemleri kapali")
        win.mode_tabs.setCurrentIndex(0)
        app.processEvents()
        check(win.a_select_all.isEnabled(),
              "diyagram kipine donunce eylemler yeniden acildi")

        # -- Inspector: alandan alana gecerken odak ve icerik korunmali
        win.doc.replace(demo_machine(), None)
        app.processEvents()
        win.canvas.set_selected_ids(["t_start"])
        app.processEvents()
        guard = action = None
        for widget in win.inspector.findChildren(QPlainTextEdit):
            hint = widget.placeholderText()
            if hint.startswith("ctx->count > 3"):
                guard = widget
            elif hint.startswith("ctx->count++"):
                action = widget
        if guard is None or action is None:
            check(False, "inspector alanlari bulundu")
        else:
            guard.setFocus()
            app.processEvents()
            guard.setPlainText("a == 1")
            action.setFocus()                    # alan degistir -> guard commit
            for _ in range(6):
                app.processEvents()
            check(QApplication.focusWidget() is action,
                  "alan degistirince odak yeni alanda kaldi")
            action.setPlainText("ctx->n++;")
            action.clearFocus()
            for _ in range(6):
                app.processEvents()
            tran = win.doc.machine.transitions["t_start"]
            check(tran.guard == "a == 1" and tran.action == "ctx->n++;",
                  "ard arda iki alan da modele islendi",
                  "guard=%r action=%r" % (tran.guard, tran.action))

        # -- Yanlis turde dosya: icerige gore yonlendirilmeli, ezilmemeli
        tmp = tempfile.mkdtemp(prefix="usd_reg_ui_")
        class_path = os.path.join(tmp, "roomplan.json")
        with open(class_path, "w", encoding="utf-8") as fh:
            fh.write(demo_class_model().to_json())
        win.doc.mark_clean()
        win.class_doc.mark_clean()
        win.mode_tabs.setCurrentIndex(0)
        app.processEvents()
        states_before = len(win.doc.machine.states)
        _Dialog.next_open = class_path
        win.file_open()
        for _ in range(4):
            app.processEvents()
        with open(class_path, encoding="utf-8") as fh:
            still = json.load(fh)
        check(win.mode_tabs.currentIndex() == 1,
              "sinif diyagrami sinif kipine yonlendirildi")
        check(len(win.doc.machine.states) == states_before,
              "durum makinesi bozulmadi")
        check("classes" in still, "ozgun dosya icerigi korundu")

        # -- Taninmayan dosya modeli bosaltmamali
        bad = os.path.join(tmp, "bos.json")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("{}")
        messages.clear()
        win.mode_tabs.setCurrentIndex(0)
        app.processEvents()
        states_before = len(win.doc.machine.states)
        _Dialog.next_open = bad
        win.file_open()
        for _ in range(4):
            app.processEvents()
        check(len(win.doc.machine.states) == states_before,
              "taninmayan dosya modeli bosaltmadi")
        check(bool(messages), "taninmayan dosya icin uyari gosterildi")

        # -- Alanda YAZARKEN Kaydet: yazilan metin diske gitmeli
        win.doc.replace(demo_machine(), None)
        app.processEvents()
        model_path = os.path.join(tmp, "m.usm")
        win.doc.save(model_path)
        state_id = next(s.id for s in win.doc.machine.states.values()
                        if s.name == "Off")
        win.canvas.set_selected_ids([state_id])
        app.processEvents()
        entry = None
        for widget in win.inspector.findChildren(QPlainTextEdit):
            if widget.placeholderText().startswith("when the state becomes"):
                entry = widget
                break
        if entry is None:
            check(False, "entry alani bulundu")
        else:
            entry.setFocus()
            app.processEvents()
            entry.setPlainText("led_on();")
            # Odak HALA alanda; kullanici Ctrl+S'e basiyor.
            win.a_save.trigger()
            for _ in range(4):
                app.processEvents()
            with open(model_path, encoding="utf-8") as fh:
                on_disk = json.load(fh)
            saved_entry = next(s.get("entry", "") for s in on_disk["states"]
                               if s["id"] == state_id)
            check(saved_entry == "led_on();",
                  "alanda yazarken Kaydet metni diske yazdi", repr(saved_entry))
            check(not win.doc.is_dirty(), "kaydetme sonrasi belge temiz")

        # -- Bilesik duruma birakilan durum ebeveyninin ICINDE kalmali
        win.doc.replace(demo_machine(), None)
        app.processEvents()
        group = next(i for i in win.canvas.state_items.values()
                     if i.state.name == "Running")
        loose = next(i for i in win.canvas.state_items.values()
                     if i.state.name == "Fault")
        win.canvas._pre_drag = win.doc.machine.to_json()
        # Fault kok bolgede; sahne konumu dogrudan pos()'tur.
        target = group.mapToScene(group.content_rect().topLeft()) \
            + QPointF(20.0, 20.0)
        loose.setSelected(True)
        loose.setPos(target)
        moved = win.canvas._apply_reparenting()
        win.canvas._write_geometry_to_model(skip=moved)
        win.doc.edit_from("tasi", win.canvas._pre_drag)
        for _ in range(4):
            app.processEvents()
        child = win.doc.machine.states[loose.state.id]
        check(child.parent == group.state.id, "durum bilesik durumun icine tasindi")
        # Duzenleme tuvali YENIDEN KURDU: eski `group` ogesinin C++ tarafi
        # silindi. Ic alani taze ogeden oku.
        group = next(i for i in win.canvas.state_items.values()
                     if i.state.name == "Running")
        area = group.content_rect()
        check(area.left() <= child.x <= area.right()
              and area.top() <= child.y <= area.bottom(),
              "koordinat ebeveynin ic alaninda kaldi",
              "x=%s y=%s alan=%s" % (child.x, child.y, area))

        # -- Dogrulama hatasi varken bayat kod disa aktarilmamali
        win.doc.replace(demo_machine(), None)
        app.processEvents()
        win.build()
        check(bool(win._last_files), "gecerli modelde dosyalar uretildi")
        bad_id = next(t.id for t in win.doc.machine.transitions.values()
                      if t.guard.strip())
        win.doc.edit("boz", lambda m: setattr(m.transitions[bad_id], "guard",
                                              "a > (b"))
        for _ in range(4):
            app.processEvents()
        win.build()
        check(not win._last_files,
              "dogrulama hatasinda bayat dosyalar temizlendi",
              str(list(win._last_files)))
        remove_tree(tmp)
    finally:
        win.doc.mark_clean()
        win.class_doc.mark_clean()
        win.settings.clear()
        win.close()
        app.processEvents()
        mw.QFileDialog, mw.QMessageBox = real_dialog, real_box


# --------------------------------------------------------------------------- #
#  9) Uretilen kodun BICIM kurallari
# --------------------------------------------------------------------------- #

#: Ternary operatoru: `?` ile `:` ayni satirda. Etiketler (`case x:`),
#: erisim belirteclerinin (`public:`) ve `::` kapsam operatorunun yanlis
#: eslesmemesi icin once yorumlar ve dizgiler atilir.
_TERNARY = re.compile(r"\?[^?\n]*(?<!:):(?!:)")

#: Tek satirlik blok: `if (...) { ... }` govdesi ayni satirda.
_TEK_SATIR_BLOK = re.compile(
    r"^\s*(?:\}\s*)?(?:if|else if|else|for|while|switch)\b[^\n]*\{[^\n{}]*\}")

#: TEK SATIRLIK ISLEV GOVDESI.
#:
#: Kullanici "hicbir seyi tek satirda yazma" dedi; yalnizca kontrol
#: deyimlerini denetlemek yetmiyordu. C++ basliginda
#: ``State state() const noexcept { return state_; }`` gibi satir-ici
#: govdeler kaliyordu.
_TEK_SATIR_GOVDE = re.compile(
    r"^\s*[A-Za-z_][\w:<>,*& ]*\([^;{}]*\)[\w\s:]*\{[^\n{}]*\}")

#: SATIR SONUNDA acilis suslu parantezi (K&R). Dizi baslaticilar
#: (``... = {``) haric tutulur; onlarda parantezi indirmek okunurlugu
#: dusururdu.
#: Satir sonunda ACILIS parantezi birakan HER satir.
#:
#: Kural tek ve genel: uretilen kodda hicbir satir `{` ile BITMEZ --
#: parantez kendi satirindadir (Allman). Once anahtar kelimeye gore
#: desenler yazilmisti (`if (x) {`, `struct {` ...) ve her seferinde bir
#: bicim disarida kaliyordu: cok satirli kosullar, dizi baslaticilar,
#: `extern "C"`. Ayni dosyada iki brace bicimi birden duruyordu.
#:
#: Yalnizca `{` olan satir KURALIN KENDISIDIR, ihlali degil.
_SONDA_SUSLU = re.compile(r"\S\s*\{\s*$")


def _kod_satirlari(metin: str):
    """Yorum ve dizgi ICERIGI atilmis satirlari (numara, satir) verir.

    Bicim denetimi kodun KENDISINE bakmalidir: aciklama satirindaki bir
    soru isareti yada dizgi icindeki iki nokta, ternary sanilirdi.
    """
    blok_yorumda = False
    for no, ham in enumerate(metin.splitlines(), start=1):
        satir = ham
        if blok_yorumda:
            son = satir.find("*/")
            if son < 0:
                continue
            satir = satir[son + 2:]
            blok_yorumda = False
        bas = satir.find("/*")
        while bas >= 0:
            son = satir.find("*/", bas + 2)
            if son < 0:
                satir = satir[:bas]
                blok_yorumda = True
                break
            satir = satir[:bas] + " " + satir[son + 2:]
            bas = satir.find("/*")
        satir = re.sub(r"//.*$", "", satir)
        satir = re.sub(r'"(?:\\.|[^"\\])*"', '""', satir)
        satir = re.sub(r"'(?:\\.|[^'\\])*'", "''", satir)
        if satir.strip():
            yield no, satir


def _stil_ortogonal() -> StateMachine:
    """ORTOGONAL bolgeler + FORK + ERTELEME.

    Bolge basina tamamlanma dongusunu, fork tablosunu ve erteleme
    yardimcilarini uretime sokar.
    """
    sm = StateMachine(name="StyleOrtho", prefix="styortho", context_type="void")
    sm.add_state(State(id="i", name="I", kind=StateKind.INITIAL))
    sm.add_state(State(id="out", name="Out", kind=StateKind.SIMPLE))
    sm.add_state(State(id="fk", name="FK", kind=StateKind.FORK))
    sm.add_state(State(id="p", name="Par", kind=StateKind.COMPOSITE, regions=2))
    sm.add_transition(Transition(id="t0", source="i", target="out"))
    for bolge, on in ((0, "a"), (1, "b")):
        buyuk = on.upper()
        sm.add_state(State(id=on + "i", name=buyuk + "I",
                           kind=StateKind.INITIAL, parent="p", region=bolge))
        sm.add_state(State(id=on + "0", name=buyuk + "0",
                           kind=StateKind.SIMPLE, parent="p", region=bolge))
        sm.add_state(State(id=on + "1", name=buyuk + "1",
                           kind=StateKind.SIMPLE, parent="p", region=bolge))
        sm.add_transition(Transition(id="ti" + on, source=on + "i",
                                     target=on + "0"))
        sm.add_transition(Transition(id="ts" + on, source=on + "0",
                                     target=on + "1", event="TICK"))
        sm.add_transition(Transition(id="tf" + on, source="fk",
                                     target=on + "1"))
    sm.add_transition(Transition(id="tg", source="out", target="fk",
                                 event="GO"))
    sm.add_transition(Transition(id="tb", source="p", target="out",
                                 event="LATE"))
    sm.states["a0"].deferred = ["LATE"]
    return sm


def _stil_tarih() -> StateMachine:
    """DERIN TARIH, hatirlanani ORTOGONAL olan bir agacta.

    Tarih tablolarini ve genislik oncelikli geri yukleme kuyrugunu
    uretime sokar.
    """
    sm = StateMachine(name="StyleHist", prefix="styhist", context_type="void")
    sm.add_state(State(id="i", name="I", kind=StateKind.INITIAL))
    sm.add_state(State(id="out", name="Out", kind=StateKind.SIMPLE))
    sm.add_state(State(id="top", name="Top", kind=StateKind.COMPOSITE))
    sm.add_state(State(id="ti", name="TI", kind=StateKind.INITIAL,
                       parent="top"))
    sm.add_state(State(id="dh", name="DH", kind=StateKind.DEEP_HISTORY,
                       parent="top"))
    sm.add_state(State(id="par", name="Par", kind=StateKind.COMPOSITE,
                       parent="top", regions=2))
    sm.add_transition(Transition(id="t0", source="i", target="out"))
    sm.add_transition(Transition(id="t1", source="ti", target="par"))
    for bolge, on in ((0, "a"), (1, "b")):
        buyuk = on.upper()
        sm.add_state(State(id=on + "i", name=buyuk + "I",
                           kind=StateKind.INITIAL, parent="par", region=bolge))
        sm.add_state(State(id=on + "c", name=buyuk + "c",
                           kind=StateKind.COMPOSITE, parent="par",
                           region=bolge))
        sm.add_state(State(id=on + "ci", name=buyuk + "CI",
                           kind=StateKind.INITIAL, parent=on + "c"))
        sm.add_state(State(id=on + "0", name=buyuk + "0",
                           kind=StateKind.SIMPLE, parent=on + "c"))
        sm.add_state(State(id=on + "1", name=buyuk + "1",
                           kind=StateKind.SIMPLE, parent=on + "c"))
        sm.add_transition(Transition(id="ti" + on, source=on + "i",
                                     target=on + "c"))
        sm.add_transition(Transition(id="tc" + on, source=on + "ci",
                                     target=on + "0"))
        sm.add_transition(Transition(id="ts" + on, source=on + "0",
                                     target=on + "1", event="STEP"))
    sm.add_transition(Transition(id="tin", source="out", target="top",
                                 event="IN"))
    sm.add_transition(Transition(id="tout", source="top", target="out",
                                 event="OUT"))
    sm.add_transition(Transition(id="tback", source="out", target="dh",
                                 event="BACK"))
    return sm


def _stil_zamanli() -> StateMachine:
    """IC GECISTE after(N): zamanlayici kancalarini uretime sokar."""
    sm = StateMachine(name="StyleTimed", prefix="stytimed", context_type="void")
    sm.add_state(State(id="i", name="I", kind=StateKind.INITIAL))
    sm.add_state(State(id="r", name="Run", kind=StateKind.SIMPLE))
    sm.add_state(State(id="d", name="Done", kind=StateKind.SIMPLE))
    sm.add_transition(Transition(id="t0", source="i", target="r"))
    sm.add_transition(Transition(id="t1", source="r", target="r",
                                 event="after(50)",
                                 kind=TransitionKind.INTERNAL,
                                 action="sample();"))
    sm.add_transition(Transition(id="t2", source="r", target="d",
                                 event="after(500)"))
    sm.add_transition(Transition(id="t3", source="d", target="r",
                                 event="AGAIN"))
    return sm


def test_generated_style() -> None:
    """Uretilen C/C++ ternary ve tek satirlik blok ICERMEZ.

    Ikisi de gomulu kodlama kilavuzlarinda (MISRA C:2012 R15.6 -- her
    if/else govdesi bilesik deyim olmali) ve projenin kendi kuralinda
    yasaklidir. Denetim URETILEN metne bakar, ureteci kaynagina degil:
    kural ancak ciktida saglandiysa saglanmistir.
    """
    print("== 9. Uretilen kodun bicim kurallari ==")

    sm = demo_machine()
    uretilen = dict(generate_c(sm))
    uretilen.update(generate_cpp(sm))
    # SINIF diyagrami ciktisi da ayni bicim kurallarina uyar.
    from app.core.samples import demo_class_model
    cm = demo_class_model()
    uretilen.update(generate_class_c(cm))
    uretilen.update(generate_class_cpp(cm))

    # DEMO MODEL, KOSULLU TABLOLARIN HICBIRINI ACMIYOR.
    #
    # Tarih geri yukleme kuyrugu, bolge basina tamamlanma dongusu,
    # fork/join, erteleme ve zamanlayici kancalari yalnizca o ozelligi
    # KULLANAN modelde uretilir. Bicim kurallari orada da gecerli, ama
    # yalnizca demo modele bakan bir denetim o kod yollarini HIC gormez:
    # uretecin en karmasik dallari denetimsiz kaliyordu.
    for _kur in (_stil_ortogonal, _stil_tarih, _stil_zamanli):
        _ek = _kur()
        uretilen.update(generate_c(_ek))
        uretilen.update(generate_cpp(_ek))

    for ad in sorted(uretilen):
        metin = uretilen[ad]

        ternary = [(no, s.strip()) for no, s in _kod_satirlari(metin)
                   if _TERNARY.search(s)]
        check(not ternary, "%s: ternary operatoru yok" % ad,
              "\n".join("satir %d: %s" % (no, s) for no, s in ternary[:5]))

        tek = [(no, s.strip()) for no, s in _kod_satirlari(metin)
               if _TEK_SATIR_BLOK.match(s) or _TEK_SATIR_GOVDE.match(s)]

        # ACILIS SUSLU PARANTEZI KENDI SATIRINDA (Allman).
        #
        # Islevler zaten boyle uretiliyordu ama kontrol deyimleri ve tip
        # tanimlari K&R kaliyordu; ayni dosyada iki bicim birden vardi.
        knr = [(no, s.strip()) for no, s in _kod_satirlari(metin)
               if _SONDA_SUSLU.search(s)]
        check(not knr, "%s: acilis parantezi kendi satirinda" % ad,
              "\n".join("satir %d: %s" % (no, s) for no, s in knr[:5]))
        check(not tek, "%s: tek satirlik blok yok" % ad,
              "\n".join("satir %d: %s" % (no, s) for no, s in tek[:5]))


# --------------------------------------------------------------------------- #
#  10) UML adlandirmasi -> C sabiti
# --------------------------------------------------------------------------- #

def test_uml_naming() -> None:
    """UML UpperCamelCase adlari C'de SOZCUK SINIRI KORUNARAK yazilir.

    Uretec eskiden `name.upper()` cagiriyordu: 'LedOn' -> '..._LEDON' (sozcuk
    siniri kayboluyordu) ve bosluklu bir ad '..._LED ON' gibi DERLENMEYEN bir
    sabit uretiyordu. Dogrulayici da cakismayi `.upper()` ile ariyordu, yani
    uretecin gercekte yazacagi sembolu bilmiyordu.
    """
    print("== 10. UML adlandirmasi -> C sabiti ==")

    check(screaming_snake("LedOn") == "LED_ON",
          "'LedOn' -> 'LED_ON' (sozcuk siniri korunuyor)",
          "uretilen: %s" % screaming_snake("LedOn"))
    check(screaming_snake("Led On") == "LED_ON",
          "bosluklu ad da ayni sabite duser")
    check(screaming_snake("LED-Off") == "LED_OFF",
          "tire ayirici sayilir")

    # Uretecin yazdigi sabit gercekten gecerli bir C tanimlayicisi olmali.
    ident = re.compile(r"^[A-Za-z_][0-9A-Za-z_]*$")
    for ham in ("Led On", "LED-Off", "2Fast", "", "  bos  ", "a.b"):
        check(bool(ident.match(screaming_snake(ham))),
              "'%s' -> gecerli C tanimlayicisi" % ham,
              "uretilen: %r" % screaming_snake(ham))

    # Uretec ile dogrulayici AYNI donusumu kullanmali: aksi halde dogrulayici
    # cakismayi goremez ve derlenmeyen kod uretilir.
    sm = machine(state_name="LedOn")
    hdr = generate_c(sm)["probe.h"]
    check("PROBE_STATE_LED_ON" in hdr,
          "uretilen baslikta 'PROBE_STATE_LED_ON' var")

    çakisan = StateMachine(name="Probe", prefix="probe", context_type="void")
    çakisan.add_state(State(id="i", name="Init", kind=StateKind.INITIAL))
    çakisan.add_state(State(id="a", name="LedOn", kind=StateKind.SIMPLE, y=100))
    çakisan.add_state(State(id="b", name="Led_On", kind=StateKind.SIMPLE, y=200))
    çakisan.add_transition(Transition(id="t0", source="i", target="a"))
    çakisan.add_transition(Transition(id="t1", source="a", target="b", event="GO"))
    check("V011" in codes(çakisan),
          "'LedOn' ile 'Led_On' ayni sabite dustugu icin hata (V011)",
          "kodlar: %s" % sorted(codes(çakisan)))


# --------------------------------------------------------------------------- #
#  11) Kopyala / yapistir
# --------------------------------------------------------------------------- #

def test_clipboard() -> None:
    """Kopyalanan parca modele GECERLI olarak girmeli.

    Uc sessiz veri kaybi burada kapatilir: ayni kimligin modeli ezmesi,
    ayni adin derlenmeyen kod uretmesi (V011) ve secim disina cikan bir
    gecisin kirik olarak kopyalanmasi.
    """
    print("\n== 11. Kopyala / yapistir ==")

    sm = demo_machine()
    once_durum = len(sm.states)
    once_gecis = len(sm.transitions)

    run = next(s for s in sm.states.values() if s.name == "Running")
    payload = copy_fragment(sm, [run.id])
    check(payload is not None, "bilesik durum kopyalandi")

    n_state, n_tran = fragment_summary(payload)
    check(n_state == 4, "alt agac birlikte kopyalandi (4 durum)",
          "kopyalanan: %d" % n_state)

    yeni = paste_fragment(sm, payload, parent=None, dx=40, dy=40)
    check(len(sm.states) - once_durum == 4, "4 durum eklendi")
    check(len(sm.transitions) - once_gecis == n_tran,
          "ic gecisler de eklendi (%d)" % n_tran)

    # Kimlikler yeniden uretilmis olmali.
    check(len(set(yeni)) == len(yeni) and run.id not in yeni,
          "yapistirilan kimlikler YENI")

    # Ad tekrari uretilen enum'u bozar (V011).
    adlar = [s.name for s in sm.states.values()]
    check(len(set(adlar)) == len(adlar), "ad tekrari yok",
          "tekrar edenler: %s"
          % [a for a in set(adlar) if adlar.count(a) > 1])

    check(not codes(sm), "yapistirma sonrasi model gecerli",
          "kodlar: %s" % sorted(codes(sm)))

    # Secim disina cikan gecis KOPYALANMAMALI.
    tek = next(s for s in sm.states.values() if s.name == "Off")
    p2 = copy_fragment(sm, [tek.id])
    check(fragment_summary(p2) == (1, 0),
          "secim disina cikan gecis kopyalanmiyor",
          "olcum: %s" % (fragment_summary(p2),))

    # Yabanci yuk reddedilmeli.
    hata = False
    try:
        paste_fragment(sm, "just some text")
    except ValueError:
        hata = True
    check(hata, "yabanci pano metni ValueError ile reddediliyor")


# --------------------------------------------------------------------------- #
#  11b) Arayuz: gorunum ve dil
# --------------------------------------------------------------------------- #

def test_layout_recovery() -> None:
    """Kaydedilmis BOZUK yerlesim tuvali yok edemez.

    Yasanan hata: ayar dosyasinda splitter paylari ['230', '0', '1364']
    olarak kaliyordu. Diyagram bolmesi 0 piksel genisligindeydi ve uc kip de
    (durum makinesi, sinif diyagrami, depo) o bolmenin icinde oldugu icin
    uygulama "hicbir sey acilmiyor" gibi gorunuyordu. Ustelik tutulacak
    gorunur bir ayirici kalmadigi icin kullanici geri getiremiyordu ve pay
    kayitli oldugu icin sorun her acilista tekrarliyordu.
    """
    print("\n== 16. Bozuk yerlesimden kurtarma ==")

    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import QApplication
    from app.ui.main_window import MainWindow, app_settings
    from app.ui.theme import stylesheet

    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(stylesheet())

    ayar = app_settings()

    def pencere(kayitli_paylar):
        ayar.clear()
        if kayitli_paylar is not None:
            ayar.setValue("splitter", kayitli_paylar)
        ayar.sync()
        win = MainWindow()
        win.settings = ayar
        win._restore_state()
        win.resize(1900, 1000)
        win.show()
        app.processEvents()
        return win

    # -- Diyagram bolmesi 0: kayit TAMAMEN atilmali.
    win = pencere(["230", "0", "1364"])
    paylar = win.main_splitter.sizes()
    index = win.main_splitter.indexOf(win.mode_tabs)
    oran = paylar[index] / float(sum(paylar) or 1)
    check(paylar[index] > 0, "diyagram bolmesi 0 piksel degil",
          "paylar: %s" % paylar)
    check(oran >= MainWindow.MIN_DIAGRAM_SHARE,
          "diyagram bolmesi calisilabilir bir pay aliyor (%.0f%%)" % (oran * 100),
          "paylar: %s" % paylar)
    check(win.canvas.width() > 200, "tuval gercekten genis",
          "tuval genisligi: %d" % win.canvas.width())

    # -- Ayirici SONUNA KADAR surukleyerek bolme yok EDILEMEZ.
    check(not win.main_splitter.childrenCollapsible(),
          "ana bolucude cocuklar cokertilemez")
    win.main_splitter.setSizes([2000, 0, 0])
    app.processEvents()
    check(win.main_splitter.sizes()[index] > 0,
          "sifir pay atansa bile bolme ekranda kaliyor",
          "paylar: %s" % win.main_splitter.sizes())

    # -- Reset Layout her seyi geri getirir.
    win.code_panel.setVisible(False)
    win.reset_layout()
    app.processEvents()
    check(win.code_panel.isVisible(), "Reset Layout gizli paneli geri getiriyor")
    paylar = win.main_splitter.sizes()
    oran = paylar[index] / float(sum(paylar) or 1)
    check(oran >= MainWindow.MIN_DIAGRAM_SHARE,
          "Reset Layout sonrasi diyagram payi makul (%.0f%%)" % (oran * 100))
    win.close()

    # -- SAGLIKLI kayit korunmali (asiri duzeltme denetimi).
    win2 = pencere(["300", "900", "700"])
    check(win2.main_splitter.sizes()[index] > 600,
          "gecerli kayit oldugu gibi geri yukleniyor",
          "paylar: %s" % win2.main_splitter.sizes())
    win2.close()
    ayar.clear()


def test_no_clipped_text() -> None:
    """Varsayilan yerlesimde HICBIR metin kirpilmamali.

    Kullanici bildirdi: ozellikler formundaki alanlar ('blinky_ctx_t',
    '#include "blinky_ctx.h"') ve kod paneli baslik seridi
    ("GENERA' C (C99) files |opy| por") kirpiliyordu.
    """
    print("\n== 17. Kirpilan metin yok ==")

    from PyQt6.QtWidgets import QApplication, QLineEdit, QSizePolicy
    from app.ui.inspector import MiniCodeEdit
    from app.ui.main_window import MainWindow, app_settings
    from app.ui.theme import stylesheet

    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(stylesheet())
    win = MainWindow()
    # AYRI AYAR DEPOSU. MainWindow kapanirken geometriyi ve bolucu
    # paylarini KAYDEDER; testin kendi penceresi kullanicinin gercek
    # ayarlarina yazarsa hem onlari bozar hem de bir sonraki kosumun
    # baslangic durumunu degistirir (iki ardisik kosumda biri duserdi).
    from PyQt6.QtCore import QSettings
    win.settings = app_settings()
    win.resize(1900, 1000)
    win.show()
    app.processEvents()

    # -- Ozellikler formu: her alan ICERIGINI gosterebilmeli.
    kirpik = []
    for alan in win.inspector.findChildren(QLineEdit):
        metin = alan.text()
        if not metin:
            continue
        gerekli = alan.fontMetrics().horizontalAdvance(metin) + 12
        if gerekli > alan.width():
            kirpik.append("%r (%d px gerekli, %d px var)"
                          % (metin, gerekli, alan.width()))
    check(not kirpik, "ozellikler formunda kirpilan alan yok",
          "\n".join(kirpik))

    # -- Cok satirli kutular (aciklama, ek include) TAM icerigi gostermeli.
    #
    #    Iki ayri kusur bir aradaydi: (1) yukseklik `satir * rows + 8` ile
    #    sabitti ve yatay kaydirma cubugu bu sabitin ICINDEN yer aliyordu,
    #    son satir kesiliyordu; (2) sarilan metinde kutu icerige gore hic
    #    buyumuyordu -- cunku olcum widget yerlesmeden once yapiliyor ve
    #    `QPlainTextDocumentLayout.documentSize()` yuksekligi PIKSEL degil
    #    SATIR olarak veriyor.
    app.processEvents()          # ertelenmis olcum bu turda calisir
    tasan = []
    for kutu in win.inspector.findChildren(MiniCodeEdit):
        if not kutu.toPlainText():
            continue
        if kutu.verticalScrollBar().maximum() > 0:
            tasan.append("%r (%d px, %d sigmayan)"
                         % (kutu.toPlainText()[:40], kutu.height(),
                            kutu.verticalScrollBar().maximum()))
    check(not tasan, "cok satirli kutular icerigi TAM gosteriyor",
          "\n".join(tasan))

    # -- Kod paneli baslik seridi. Olcut, seridin TAMAMI degil ZORUNLU
    #    ogeleridir: baslik ve "3 files · 929 lines" bilgidir ve kisalabilir.
    #    Tamamini asgariye katmak paneli 777 px'e zorluyor, tuvale yer
    #    birakmiyordu. Dugmeler ve dil secimi ise kisalamaz -- kirpilmis bir
    #    dugme kullanilamaz.
    zorunlu = win.code_panel.language.minimumWidth()
    for dugme in win.code_panel._header_buttons:
        zorunlu += dugme.sizeHint().width()
    check(win.code_panel.minimumWidth() >= zorunlu,
          "kod paneli asgarisi zorunlu baslik ogelerini karsiliyor",
          "asgari %d < zorunlu %d" % (win.code_panel.minimumWidth(), zorunlu))

    # Kisalabilen ogeler GERCEKTEN kisalabilmeli; aksi halde ustteki
    # hesap dogru olsa da Qt paneli yine genis tutar.
    for ad, w in (("title", win.code_panel._title),
                  ("status", win.code_panel.status)):
        check(w.sizePolicy().horizontalPolicy()
              == QSizePolicy.Policy.Ignored,
              "baslik seridindeki '%s' kisalabiliyor" % ad)

    # -- Pencere asgarisi, bolmelerin asgarilerini karsilamali.
    gerekli = (win.left_col.minimumWidth() + win.mode_tabs.minimumWidth()
               + win.code_panel.minimumWidth())
    check(win.minimumWidth() >= gerekli,
          "pencere asgarisi bolme asgarilerini karsiliyor",
          "pencere %d < toplam %d" % (win.minimumWidth(), gerekli))
    win.close()
    win.settings.clear()


def test_ui_appearance() -> None:
    """Kullanicinin bildirdigi gorunum hatalarinin geri gelmemesi.

    Hepsi ayni pencerede olculur: pencere kurmak pahalidir ve bu kontroller
    birbirinden bagimsizdir.
    """
    print("\n== 11b. Arayuz gorunumu ==")
    from PyQt6.QtWidgets import QApplication, QWidget

    from app.ui.contrast import ContrastDelegate
    from app.ui.main_window import MainWindow, app_settings
    from app.ui.theme import C, stylesheet, ui_font

    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setFont(ui_font(9))
    app.setStyleSheet(stylesheet())
    win = MainWindow()
    win.resize(1600, 900)
    win.show()
    win.canvas.auto_edit = False
    win.class_canvas.auto_edit = False
    # ORNEK MODELLERI ACIKCA YUKLE: uygulama artik acilista demo
    # yuklemiyor, bos sablonla basliyor. Bu vaka agacin sinif
    # ozniteliklerini/iliskilerini gosterdigini sinadigi icin dolu bir
    # modele ihtiyaci var.
    win.load_demo()
    win.load_demo_class()
    win.mode_tabs.setCurrentIndex(0)
    app.processEvents()
    win._rebuild_views()
    app.processEvents()

    try:
        # -- "Show code panel" gercekten GORUNUR bir pay vermeli ------------
        # Eskiden QSslitter gizlenen panelin payini komsuya devrediyordu;
        # dugme yeniden isaretlenince panel "gorunur" ama 0 piksel genisti,
        # yani kullaniciya dugme hic calismiyor gibi geliyordu.
        win.a_code_panel.setChecked(False)
        win.toggle_code_panel(False)
        app.processEvents()
        win.a_code_panel.setChecked(True)
        win.toggle_code_panel(True)
        app.processEvents()
        index = win.main_splitter.indexOf(win.code_panel)
        width = win.main_splitter.sizes()[index]
        check(win.code_panel.isVisible() and width > 40,
              "kod paneli acilinca gorunur bir pay aliyor",
              "genislik = %d px" % width)

        # -- Ayni hata simulasyon panelinde de vardi ------------------------
        win.a_sim_panel.setChecked(False)
        win.toggle_sim_panel(False)
        app.processEvents()
        win.a_sim_panel.setChecked(True)
        win.toggle_sim_panel(True)
        app.processEvents()
        index = win.state_center.indexOf(win.sim_panel)
        height = win.state_center.sizes()[index]
        check(win.sim_panel.isVisible() and height > 40,
              "simulasyon paneli acilinca gorunur bir pay aliyor",
              "yukseklik = %d px" % height)

        # -- "Snap to grid" her IKI tuvale de islemeli ----------------------
        win.toggle_snap(False)
        check(not win.canvas.snap_enabled and not win.class_canvas.snap_enabled,
              "izgaraya hizalama kapatilinca iki tuval de kapaniyor")
        win.toggle_snap(True)
        check(win.canvas.snap_enabled and win.class_canvas.snap_enabled,
              "izgaraya hizalama acilinca iki tuval de aciliyor")

        # -- Secili satir zit renkte ve KALIN olmali ------------------------
        # -- MODEL TREE paneli kaldirildi: bilgi CALISMA ALANI agacinda ---
        # Kullanici "model tree kaldir, hepsini workspace gosteriyorsun
        # zaten" dedi. Bu ancak calisma alani agaci GERCEKTEN her seyi
        # gosteriyorsa dogrudur; eskiden sinif modelleri icin YALNIZCA
        # sinif adlarini listeliyordu ve oznitelikler, islemler ve
        # iliskiler kayboluyordu.
        check(not hasattr(win, "outline") and not hasattr(win, "class_outline"),
              "ayri model agaci panelleri kaldirildi")
        from app.ui import panels as panels_mod
        check(not hasattr(panels_mod, "OutlinePanel")
              and not hasattr(panels_mod, "ClassOutlinePanel"),
              "OutlinePanel siniflari kaldirildi")

        # Agac artik YALNIZCA DISKTEKI dosyalari gosterir: kaydedilmemis
        # belgeler icin uydurulan "◆ untitled.usm (not saved)" satirlari
        # kaldirildi (kullanici: "su untitled her zaman cikiyor, bunlar
        # asla cikmasin"). Bu yuzden iki ornek belge once GERCEKTEN
        # calisma alanina yazilir; kontrol edilen sey degismez -- agac
        # modelin ICERIGINI (durum, sinif, oznitelik, islem, iliski)
        # gosteriyor mu.
        from app.core.workspace import Workspace
        ws_kok = os.path.join(make_tree(), "appearance")
        win.apply_workspace(Workspace.create(ws_kok))
        for belge, ad in ((win.doc, "sample.usm"),
                          (win.class_doc, "sample.ucd")):
            hedef = os.path.join(ws_kok, "model", ad)
            with open(hedef, "w", encoding="utf-8") as fh:
                fh.write(belge.machine.to_json())
            belge.path = hedef
        win.refresh_workspace_tree()
        app.processEvents()

        ws_labels = _tree_labels(win.ws_tree)
        cm = win.class_doc.machine
        sm_open = win.doc.machine
        for st in sm_open.states.values():
            if st.kind.is_pseudo:
                continue
            check(any(st.name in t for t in ws_labels),
                  "calisma alani agacinda durum '%s' var" % st.name)
        for c in cm.ordered_classes():
            check(any(c.name in t for t in ws_labels),
                  "calisma alani agacinda sinif '%s' var" % c.name)
            for attr in c.attributes:
                check(any(attr.label() in t for t in ws_labels),
                      "'%s.%s' ozniteligi agacta" % (c.name, attr.name))
            for op in c.operations:
                check(any(op.label() in t for t in ws_labels),
                      "'%s.%s' islemi agacta" % (c.name, op.name))
        check(sum(1 for t in ws_labels if "Realization" in t
                  or "Composition" in t or "Association" in t) > 0,
              "calisma alani agacinda sinif iliskileri de var")

        # Tuvaldeki secim CALISMA ALANI agacinda isaretlenmeli.
        from app.ui.panels import ID_ROLE
        target = next(s for s in sm_open.states.values()
                      if not s.kind.is_pseudo)
        win.canvas.set_selected_ids([target.id])
        for _ in range(10):
            app.processEvents()
        picked = win.ws_tree.selectedItems()
        check(bool(picked) and picked[0].data(0, ID_ROLE) == target.id,
              "tuvaldeki secim calisma alani agacinda isaretlendi",
              "secili = %s" % ([p.text(0) for p in picked],))

        for label, view in (("depo agaci", win.ws_tree),):
            delegate = getattr(view, "_contrast_delegate", None)
            check(isinstance(delegate, ContrastDelegate),
                  "%s zitlik temsilcisi kullaniyor" % label,
                  "temsilci = %r" % (delegate,))

        # Simulasyonda etkin durum ayni bicimde -- kalin ve zit renkte --
        # gosterilmeli. Panelde oge listesi yok, dolayisiyla temsilci degil
        # dogrudan etiketin bicimi kontrol edilir.
        style = win.sim_panel.lbl_state.styleSheet()
        check("font-weight: 700" in style,
              "simulasyondaki etkin durum kalin yaziliyor", "stil = %r" % style)
        check(C.SIM_ACTIVE.lower() in style.lower(),
              "simulasyondaki etkin durum tema vurgu rengini kullaniyor",
              "stil = %r" % style)

        # -- Tool simgeleri temaya gore murekkep rengi almali ---------------
        # Acik temada beyaz cizgili simgeler beyaz zeminde gorunmuyordu.
        from app.ui import icons
        seen = {}
        for name in ("dark", "light"):
            win.set_theme(name)
            app.processEvents()
            seen[name] = icons.ink()
            check(icons.ink() == C.TEXT_BRIGHT,
                  "%s temada simge murekkebi temadan geliyor" % name,
                  "ink=%s TEXT_BRIGHT=%s" % (icons.ink(), C.TEXT_BRIGHT))
        check(seen["dark"] != seen["light"],
              "simge murekkebi tema ile degisiyor",
              "dark=%s light=%s" % (seen["dark"], seen["light"]))

        # -- Kip degisiminde model tuvale sigdirilmali ----------------------
        # Kullanici "acinca otomatik fit yapsin" dedi.
        win.set_theme("dark")
        win.mode_tabs.setCurrentIndex(1)          # sinif diyagrami
        app.processEvents()
        win.mode_tabs.setCurrentIndex(0)          # durum makinesi
        for _ in range(40):                       # zoom_fit singleShot ile gelir
            app.processEvents()
        rect = win.canvas.mapToScene(
            win.canvas.viewport().rect()).boundingRect()
        model = win.canvas.scene().itemsBoundingRect()
        check(rect.contains(model.center()),
              "kipe donunce model gorunur alana sigdirildi",
              "gorunur=%s model=%s" % (rect, model))

        # -- Yeniden kurulan paneller HAYALET bilesen birakmamali -----------
        # deleteLater() silmeyi olay dongusune erteler; bilesen o ana kadar
        # hala panelin cocugudur ve yerlesimden ciktigi icin varsayilan
        # 640x480 geometrisiyle panelin uzerine cizilir. Kullanici secimi ya
        # da modeli hizlica degistirdiginde ust uste binmis formlar ve
        # paneli kaplayan dev dugmeler goruluyordu.
        from app.core.samples import demo_machine as _demo

        def ghost_count(host, layout):
            managed = {layout.itemAt(i).widget() for i in range(layout.count())}
            return sum(1 for w in host.findChildren(QWidget)
                       if w.parent() is host and w not in managed
                       and w.isVisible())

        for _ in range(5):                       # olay dongusu ARAYA GIRMEDEN
            win.inspector.show_selection([])
        count = ghost_count(win.inspector._host, win.inspector._layout)
        check(count == 0, "ozellikler paneli hayalet form birakmiyor",
              "%d hayalet" % count)

        for _ in range(5):
            win.doc.replace(_demo(), None)
            win.sim_panel.rebuild()
        count = ghost_count(win.sim_panel.events_host,
                            win.sim_panel.events_layout)
        check(count == 0,
              "simulasyon olay dugmeleri hayalet bilesen birakmiyor",
              "%d hayalet" % count)

        # Guard ve degisken listeleri artik QTableWidget: hucre bilesenleri
        # (QComboBox / QLineEdit) yerlesimle degil, tabloyla yonetilir.
        # Ayni kusur sinifi burada da olabilir -- satirlar yenilenirken
        # eski hucre bilesenleri birikirse panel ust uste cizilir.
        from PyQt6.QtWidgets import QComboBox, QLineEdit
        for label, table, tur in (
                ("guard tablosu", win.sim_panel.guard_table, QComboBox),
                ("degisken tablosu", win.sim_panel.var_table, QLineEdit)):
            fazla = len(table.findChildren(tur)) - table.rowCount()
            check(fazla <= 0,
                  "simulasyon %s hayalet hucre bileseni birakmiyor" % label,
                  "%d fazla bilesen (%d satir)"
                  % (fazla, table.rowCount()))

        # -- KOD URETIMI yalnizca Build ile ---------------------------------
        # Kullanici: "sen durum diyagramini surekli her degisiklikte
        # derleme, arayuz cok yavas isliyor". Model degisimi artik uretim
        # (ve DISKE YAZMA) tetiklemez; yalnizca dogrulama calisir.
        check(not hasattr(win, "_codegen_timer"),
              "kendiliginden uretim zamanlayicisi kaldirildi")
        check(hasattr(win, "a_build") and win.a_build.shortcut().toString(),
              "Build eylemi ve kisayolu var",
              "kisayol = %r" % (win.a_build.shortcut().toString(),))

        win.build()
        for _ in range(10):
            app.processEvents()
        before = dict(win._last_files)
        check(bool(before), "Build kod uretti", "%d dosya" % len(before))

        # Olcum TEMIZ zeminden baslasin: kurulumda ornek modeller
        # yuklendigi icin her iki kip de zaten bayat isaretli.
        win._stale_modes.clear()

        # Modeli degistir: panel ESKI kodu gostermeye devam etmeli ve
        # kendini BAYAT olarak isaretlemeli.
        target = next(s for s in win.doc.machine.states.values()
                      if not s.kind.is_pseudo)
        win.doc.edit("Rename", lambda m, sid=target.id:
                     setattr(m.states[sid], "name", "RenamedForTest"))
        for _ in range(30):
            app.processEvents()
        check(win._last_files == before,
              "model degisimi kod URETMEDI (panel eski ciktiyi tutuyor)")
        check(win._is_stale("state"), "durum kodu BAYAT isaretlendi")
        check(not win._is_stale("class"),
              "durum modelini duzenlemek SINIF kodunu bayatlatmadi")
        check("RenamedForTest" not in "".join(win._last_files.values()),
              "yeni ad henuz uretilen kodda YOK (derlenmedi)")

        # Build dedikten sonra guncellenmeli.
        win.build()
        for _ in range(10):
            app.processEvents()
        check("RenamedForTest" in "".join(win._last_files.values()),
              "Build sonrasi yeni ad uretilen kodda")
        check(not win._is_stale("state"),
              "Build sonrasi bayatlik isareti kalkti")

        # -- Uretim BASARISIZ olunca onbellek de atilmali -------------------
        # Aksi halde sekme degistirip geri gelmek BAYAT dosyalari geri
        # getirir ve Ctrl+E onlari diske yazardi.
        win._drop_build()
        check(not win._last_files and not win._built.get(win._build_key()),
              "uretim basarisiz olunca onbellek de temizlendi")

        # -- Onbellek DILE gore ayrilmali -----------------------------------
        # Ayrilmazsa C++ secip Build dedikten sonra diger sekmede hala C kodu
        # gorunur ve disa aktarma yanlis dosyalari yazardi.
        win.set_language("c")
        win.build()
        for _ in range(10):
            app.processEvents()
        c_key = win._build_key()
        win.set_language("cpp")
        win.build()
        for _ in range(10):
            app.processEvents()
        cpp_key = win._build_key()
        check(c_key != cpp_key, "onbellek anahtari dili iceriyor",
              "%r == %r" % (c_key, cpp_key))
        check(win._built.get(c_key) != win._built.get(cpp_key),
              "C ve C++ ciktilari ayri onbelleklerde")
        win.set_language("c")
        win.build()
        for _ in range(10):
            app.processEvents()

        # Ilerleme cubugu durum cubugunun EN SAGINDA olmali.
        from PyQt6.QtWidgets import QProgressBar
        bars = win.statusBar().findChildren(QProgressBar)
        check(win.progress in bars,
              "ilerleme cubugu durum cubugunda")
        labels = [w for w in (win.lbl_workspace, win.lbl_git, win.lbl_counts,
                              win.lbl_issues, win.lbl_zoom) if w.isVisible()]
        rightmost = max(w.x() + w.width() for w in labels)
        check(win.progress.x() >= rightmost,
              "ilerleme cubugu kalici etiketlerin SAGINDA",
              "cubuk x=%d, etiketlerin sag kenari=%d"
              % (win.progress.x(), rightmost))

        # -- Eski surumden kalan bolucu ayari sol sutunu YUTMAMALI ----------
        # Sol sutun ana bolucuye SONRADAN eklendi; onceki surumun kaydettigi
        # 2 elemanli boyut listesi 3 cocuklu bolucuye uygulanirsa kalan cocuk
        # 0 piksel kalir ve calisma alani agaci acilista gorunmez olur.
        win.settings.setValue("splitter", [1010, 670])      # ESKI bicim
        win._restore_state()
        for _ in range(10):
            app.processEvents()
        index = win.main_splitter.indexOf(win.left_col)
        check(win.main_splitter.sizes()[index] > 40,
              "eski bolucu ayarindan sonra sol sutun gorunur",
              "genislik = %d px" % win.main_splitter.sizes()[index])

        # -- Calisma alani agaci HER diyagram kipinde gorunur ---------------
        # Model agaci kaldirildigi icin tek gezinme aracidir; sinif
        # sekmesine gecince kaybolmasi kullaniciyi modelsiz birakirdi.
        for index, mode in ((0, "state"), (1, "class")):
            win.mode_tabs.setCurrentIndex(index)
            for _ in range(20):
                app.processEvents()
            check(win.ws_tree.isVisible() and win.ws_tree.width() > 40,
                  "%s kipinde calisma alani agaci gorunur" % mode,
                  "genislik = %d px" % win.ws_tree.width())
        # PROPERTIES yalnizca durum kipinde anlamli.
        win.mode_tabs.setCurrentIndex(1)
        for _ in range(20):
            app.processEvents()
        check(not win._props_section.isVisible(),
              "sinif kipinde ozellikler paneli gizli (diyalogla duzenlenir)")
        win.mode_tabs.setCurrentIndex(0)
        for _ in range(20):
            app.processEvents()
        check(win._props_section.isVisible(),
              "durum kipinde ozellikler paneli geri geldi")

        # -- Arayuz metinleri tamamen Ingilizce olmali ---------------------
        turkish = [t for t in _visible_texts(win) if _has_turkish(t)]
        check(not turkish, "gorunur arayuz metinleri Ingilizce",
              "Turkce kalan: %s" % turkish[:8])
    finally:
        # Bu vaka modeli DEGISTIRIR (Build tazeligi sinaniyor). Kapanista
        # "kaydedilsin mi?" diyalogu acilirsa ekransiz kosum SONSUZA KADAR
        # bekler -- diyalogu kimse kapatamaz.
        win.doc.mark_clean()
        win.class_doc.mark_clean()
        win.settings.clear()
        win.close()
        app.processEvents()
        remove_tree(os.path.dirname(ws_kok))


def test_composite_behaviors() -> None:
    """BILESIK durumun exit/do davranislari GORUNMELI.

    Tuval, bilesik durum icin yalnizca `lines[0]` ciziyordu: entry
    gorunuyor, exit ve do sessizce dusuyordu. `Running` durumunun
    `exit / led_write(false);` ve `do / ctx->uptime_ticks++;` davranislari
    modelde ve URETILEN KODDA vardi ama diyagramda hicbir izi yoktu --
    diyagrama bakan bir gozden geciren durumun cikista LED'i sondurdugunu
    goremiyordu.
    """
    print("\n== 16. Bilesik durumun davranislari ==")
    from PyQt6.QtWidgets import QApplication

    from app.core.model import StateKind
    from app.ui.diagram_items import StateItem
    from app.ui.main_window import MainWindow
    from app.ui.theme import stylesheet, ui_font

    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setFont(ui_font(9))
    app.setStyleSheet(stylesheet())
    win = MainWindow()
    win.resize(1400, 900)
    win.show()
    win.canvas.auto_edit = False
    win.class_canvas.auto_edit = False
    app.processEvents()
    win.doc.replace(demo_machine(), None)
    for _ in range(20):
        app.processEvents()

    sm = win.doc.machine
    items = {it.state.id: it for it in win.canvas.scene().items()
             if isinstance(it, StateItem)}
    composite = next(s for s in sm.states.values()
                     if s.kind is StateKind.COMPOSITE)
    check(composite.exit.strip() and composite.do.strip(),
          "ornek bilesik durumun exit ve do davranisi var",
          "exit=%r do=%r" % (composite.exit, composite.do))

    item = items.get(composite.id)
    if item is None:
        check(False, "bilesik durum tuvalde bulundu")
        win.doc.mark_clean()
        win.class_doc.mark_clean()
        win.settings.clear()
        win.close()
        return

    lines = item.behavior_lines()
    for ad, metin in (("entry", composite.entry), ("exit", composite.exit),
                      ("do", composite.do)):
        ozet = " ".join(metin.split())
        check(any(ozet in ln for ln in lines),
              "bilesik durumda '%s' davranisi ciziliyor" % ad,
              "cizilen satirlar: %s" % lines)

    # Serit yer ayirmali ki alt durumlar uzerine binmesin.
    strip = item.behavior_strip_height()
    check(strip > 0, "davranis seridi dikey yer ayiriyor",
          "yukseklik = %.1f" % strip)
    content = item.content_rect()
    check(content.top() >= item.rect().top() + 34.0 + strip - 0.5,
          "alt durum bolgesi seridin ALTINDAN basliyor",
          "content.top=%.1f rect.top=%.1f strip=%.1f"
          % (content.top(), item.rect().top(), strip))

    # Ornek modeldeki alt durumlar seride BINMEMELI.
    binen = [s.name for s in sm.states.values()
             if s.parent == composite.id and s.y < strip + 34.0 - 10.0]
    check(not binen, "ornek modelde alt durumlar seride binmiyor",
          "binen: %s" % binen)

    # Basit durumda davranis sayisi da tam olmali (gerileme korumasi).
    simple = next(s for s in sm.states.values()
                  if s.kind is StateKind.SIMPLE and s.entry and s.exit)
    sitem = items.get(simple.id)
    if sitem is not None:
        slines = sitem.behavior_lines()
        check(len(slines) >= 2,
              "basit durumda entry ve exit birlikte ciziliyor",
              "satirlar: %s" % slines)

    win.doc.mark_clean()
    win.class_doc.mark_clean()
    win.settings.clear()
    win.close()
    app.processEvents()


def test_simulation_default_and_labels() -> None:
    """Simulasyon paneli acilista KAPALI; anahtari seritte; dil etiketleri."""
    print("\n== 22. Simulasyon varsayilani ve dil etiketleri ==")
    from PyQt6.QtWidgets import QApplication, QToolButton

    from app.ui.code_panel import LANGUAGES
    from app.ui.main_window import MainWindow, MENU_POINT, app_settings
    from app.ui.theme import stylesheet, ui_font

    # Kalici tercih olcumu bozmasin: varsayilan sinaniyor.
    app_settings().clear()

    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setFont(ui_font(9))
    app.setStyleSheet(stylesheet())
    win = MainWindow()
    win.resize(1500, 900)
    win.show()
    win.canvas.auto_edit = False
    app.processEvents()

    try:
        # -- Acilista KAPALI -----------------------------------------------
        check(not win.a_sim_panel.isChecked(),
              "acilista simulasyon anahtari kapali")
        check(not win.sim_panel.isVisible(),
              "acilista simulasyon paneli gorunmuyor")

        # -- Anahtar UST SERITTE -------------------------------------------
        # Panel kapali geldigi icin acmanin GORUNUR bir yolu olmali.
        check(win.a_sim_panel in win.top_bar.actions(),
              "simulasyon anahtari ust seritte")

        # -- ARAC DUGMELERI YALNIZCA SIMGE ---------------------------------
        # Secili arac adiyla da yazilinca serit her arac degisiminde
        # genisleyip daraliyor, yanindaki dugmeler kayiyor ve tek bir
        # "Select" yazisi araclarin arasinda yamalik gibi duruyordu.
        from PyQt6.QtCore import Qt as _Qt
        yazili = []
        for act in win._state_tool_actions:
            d = win.top_bar.widgetForAction(act)
            if isinstance(d, QToolButton) and \
                    d.toolButtonStyle() != _Qt.ToolButtonStyle.ToolButtonIconOnly:
                yazili.append(act.text())
        check(not yazili, "arac dugmelerinde yazi yok (yalnizca simge)",
              "yazili: %s" % yazili)
        win.set_tool(win.canvas.tool)       # secim degisimi yaziyi geri getirmemeli
        for _ in range(10):
            app.processEvents()
        yazili = [a.text() for a in win._state_tool_actions
                  if isinstance(win.top_bar.widgetForAction(a), QToolButton)
                  and win.top_bar.widgetForAction(a).toolButtonStyle()
                  != _Qt.ToolButtonStyle.ToolButtonIconOnly]
        check(not yazili, "arac secildikten sonra da yazi yok",
              "yazili: %s" % yazili)

        # -- Acilinca gercekten gorunur bir pay almali ----------------------
        win.a_sim_panel.setChecked(True)
        win.toggle_sim_panel(True)
        for _ in range(20):
            app.processEvents()
        index = win.state_center.indexOf(win.sim_panel)
        check(win.sim_panel.isVisible()
              and win.state_center.sizes()[index] > 40,
              "acilinca simulasyon paneli gorunur pay aliyor",
              "yukseklik = %d px" % win.state_center.sizes()[index])

        # -- "Reset Layout" ILK ACILISA donmeli -----------------------------
        win.reset_layout()
        for _ in range(20):
            app.processEvents()
        check(not win.sim_panel.isVisible(),
              "Reset Layout simulasyon panelini kapatiyor (ilk acilis gibi)")

        # -- Menu yazisi biraz KUCULTULDU ----------------------------------
        check(MENU_POINT == 10, "menu yazisi 10 punto",
              "MENU_POINT = %d" % MENU_POINT)
        # pointSize() ORTAMA BAGLIDIR: ekransiz kosumda gercek yazi tipi
        # yok ve Qt ikame ederken punto bir kademe dusuyor. Bu yuzden
        # mutlak sayi degil, AYNI ortamda uretilen yazi tipiyle
        # karsilastirilir.
        beklenen = ui_font(MENU_POINT).pointSize()
        menu_btns = [b for b in win.menu_buttons if isinstance(b, QToolButton)]
        check(menu_btns and all(b.font().pointSize() == beklenen
                                for b in menu_btns),
              "menu dugmeleri menu puntosunu kullaniyor",
              str([b.font().pointSize() for b in menu_btns]))
        check(beklenen < ui_font(11).pointSize(),
              "menu yazisi onceki 11 puntodan KUCUK",
              "%d < %d" % (beklenen, ui_font(11).pointSize()))

        # -- Dil etiketi: "C++ 11", parantez yok ---------------------------
        etiketler = [t for t, _k in LANGUAGES]
        check("C++ 11" in etiketler, "dil listesi 'C++ 11' diyor",
              str(etiketler))
        check(not any("(C++11" in t for t in etiketler),
              "C++ etiketinde parantezli tekrar yok", str(etiketler))
        # ERISIM HARFI (&) kullaniciya GORUNMEZ; menude altini cizili
        # harf olarak cikar ve Alt ile menude gezinmeyi saglar. Bu yuzden
        # etiket karsilastirmasi isareti atarak yapilir.
        menu_etiketleri = [a.text().replace("&", "")
                           for a in win.lang_actions.values()]
        check("C++ 11" in menu_etiketleri,
              "Code menusunde de 'C++ 11'", str(menu_etiketleri))
        check(all("&" in a.text() for a in win.lang_actions.values()),
              "dil ogeleri erisim harfi tasiyor (Alt ile secilebilir)",
              [a.text() for a in win.lang_actions.values()])
    finally:
        win.doc.mark_clean()
        win.class_doc.mark_clean()
        win.settings.clear()
        win.close()
        app.processEvents()


def test_tool_menu_and_design_window() -> None:
    """Tool menusu TUM araclari listeler; tasarim penceresi ayrilabilir."""
    print("\n== 21. Tool menusu ve ayri tasarim penceresi ==")
    from PyQt6.QtWidgets import QApplication

    from app.ui.main_window import MainWindow
    from app.ui.theme import stylesheet, ui_font

    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setFont(ui_font(9))
    app.setStyleSheet(stylesheet())
    win = MainWindow()
    win.resize(1400, 900)
    win.show()
    win.canvas.auto_edit = False
    win.class_canvas.auto_edit = False
    app.processEvents()
    win.load_demo()
    for _ in range(20):
        app.processEvents()

    try:
        # -- TOOL menusu: her iki kipin butun araclari ---------------------
        # Ust seritte araclar yalnizca SIMGE; hangi simgenin hangi arac
        # oldugunu ogrenmenin tek yolu ipucunu beklemekti.
        menu = next((m for m in win._menus
                     if m.title().replace("&", "") == "Tool"), None)
        check(menu is not None, "menude Tool bolumu var")
        if menu is not None:
            eylemler = set(menu.actions())
            eksik = [a.text() for a in list(win.tool_actions.values())
                     + list(win.class_tool_actions.values())
                     if a not in eylemler]
            check(not eksik, "Tool menusu her iki kipin TUM araclarini tasiyor",
                  "eksik: %s" % eksik)

        # -- EDIT menusunde gorunum araclari -------------------------------
        edit = next(m for m in win._menus
                    if m.title().replace("&", "") == "Edit")
        for eylem, ad in ((win.a_undo, "Undo"), (win.a_redo, "Redo"),
                          (win.a_zoom_fit, "Fit"), (win.a_zoom_in, "Zoom In")):
            check(eylem in edit.actions(), "Edit menusunde %s var" % ad)

        # -- AYRI tasarim penceresi ----------------------------------------
        win.mode_tabs.setCurrentIndex(0)
        for _ in range(10):
            app.processEvents()
        win.a_tool_window.setChecked(True)
        win.toggle_design_window(True)
        for _ in range(20):
            app.processEvents()

        kayit = win._detached.get("state")
        check(kayit is not None, "diyagram sayfasi ayri pencereye alindi")
        if kayit is not None:
            pencere, sayfa = kayit
            check(pencere.isWindow(), "ayrilan pencere UST DUZEY bir pencere")
            check(sayfa.window() is pencere,
                  "sayfa gercekten o pencerenin icinde")
            check(win._state_stack.currentWidget() is not sayfa,
                  "sekmede yer tutucu gorunuyor")

            # DIYAGRAM GERCEKTEN CIZILMELI.
            #
            # setParent() (setCentralWidget icinden) bileseni GIZLI
            # isaretler ve ust bilesen gosterilince bu isaret KALKMAZ:
            # ayri pencere bombos aciliyordu. Ebeveynlik dogruydu, ama
            # ekranda hicbir sey yoktu -- o yuzden GORUNURLUK ve BOYUT
            # ayrica olculur.
            check(sayfa.isVisible(), "ayri penceredeki sayfa GORUNUR")
            check(win.canvas.isVisible(), "ayri penceredeki tuval GORUNUR")
            check(win.canvas.width() > 100 and win.canvas.height() > 100,
                  "ayri penceredeki tuval gercek bir alan kapliyor",
                  "%dx%d" % (win.canvas.width(), win.canvas.height()))
            check(len(win.canvas.state_items) == len(win.doc.machine.states),
                  "ayri penceredeki tuval modeli tam cizdi",
                  "%d / %d" % (len(win.canvas.state_items),
                               len(win.doc.machine.states)))
            # Simulasyon paneli kullanicinin tercihine UYMALI; ayirirken
            # toplu "goster" onu da acmamali.
            check(win.sim_panel.isVisible() == win.a_sim_panel.isChecked(),
                  "ayirma simulasyon tercihini bozmuyor")

            # GIZLI KALMASI GEREKENLER GIZLI KALMALI.
            #
            # Ilk duzeltmede butun alt bilesenler toplu gosteriliyordu ve
            # tuvalin QRubberBand'i de aciliyordu: gorunmez bir secim
            # kaplamasi tuvalin ustune yayilip tiklamalari yutuyor,
            # kullaniciya "fare takildi" gibi geliyordu.
            from PyQt6.QtWidgets import QRubberBand
            acik_bantlar = [b for b in win.findChildren(QRubberBand)
                            if b.isVisible()]
            check(not acik_bantlar,
                  "ayirma gizli lastik bandi acmiyor (fare takilmasi)",
                  "%d gorunur QRubberBand" % len(acik_bantlar))
            check(not win.canvas._band.isVisible(),
                  "tuvalin secim bandi kapali")

            # ANA pencere serbest kalmali: depo sekmesine gecilebilmeli.
            win.mode_tabs.setCurrentIndex(2)
            for _ in range(15):
                app.processEvents()
            check(win.active_mode() == "git",
                  "sayfa ayriyken ana pencerede depoya bakilabiliyor")
            win.mode_tabs.setCurrentIndex(0)
            for _ in range(10):
                app.processEvents()

            # Pencere KAPANINCA sayfa geri donmeli; aksi halde kullanici
            # modelini kaybetmis sanirdi.
            pencere.close()
            for _ in range(30):
                app.processEvents()
            check("state" not in win._detached,
                  "pencere kapaninca kayit temizlendi")
            check(win._state_stack.currentWidget() is sayfa,
                  "sayfa sekmeye geri dondu")
            check(len(win.canvas.state_items) == len(win.doc.machine.states),
                  "geri donen tuval modeli tam cizdi",
                  "%d / %d" % (len(win.canvas.state_items),
                               len(win.doc.machine.states)))
            check(not win.a_tool_window.isChecked(),
                  "menu isareti de kalkti (dugme bozuk gorunmesin)")
    finally:
        win.doc.mark_clean()
        win.class_doc.mark_clean()
        win.settings.clear()
        win.close()
        app.processEvents()


def test_codegen_naming_and_standard() -> None:
    """Uretilen kodda KISALTMA olmamali; C standardi gnu11 olmali."""
    print("\n== 19. Uretilen kod: adlar ve standart ==")
    from app.codegen.cpp_generator import generate_cpp

    c_files = generate_c(demo_machine())
    cpp_files = generate_cpp(demo_machine())
    hepsi = " ".join(list(c_files.values()) + list(cpp_files.values()))

    # Kullanici: "blinky_trans yazmissin, bu tarz teknik terimleri
    # kisaltmadan uzun yazman lazim."
    yasak = {
        r"\btran\b": "tran -> transition",
        r"_tran_": "_tran_ -> _transition_",
        r"TRAN_": "TRAN_ -> TRANSITION_",
        r"HIST_SH\b": "HIST_SH -> HISTORY_SHALLOW",
        r"HIST_DEEP\b": "HIST_DEEP -> HISTORY_DEEP",
        r"_lca\b": "_lca -> _least_common_ancestor",
        r"_ctor\b": "_ctor -> _construct",
        r"RTC_STEPS": "RTC -> RUN_TO_COMPLETION",
        r"_exec_": "_exec_ -> _execute_",
        r"_eval_guard": "_eval_ -> _evaluate_",
        r"\bkTran[A-Z]": "kTran... -> kTransition...",
        r"\bexec[AEDX][a-z]": "execXxx -> executeXxx",
        r"\bevalGuard\b": "evalGuard -> evaluateGuard",
        r"\blcaOf\b": "lcaOf -> leastCommonAncestorOf",
    }
    for desen, aciklama in yasak.items():
        bulunan = re.findall(desen, hepsi)
        check(not bulunan, "uretilen kodda kisaltma yok: %s" % aciklama,
              "%d gecis, ornek: %s" % (len(bulunan), bulunan[:3]))

    # Uzun adlar GERCEKTEN uretilmis olmali (yukaridaki kontroller bos bir
    # ciktida da gecerdi).
    for beklenen in ("_TRANSITION_COUNT", "_MAX_RUN_TO_COMPLETION_STEPS",
                     "_construct(", "_execute_entry", "_least_common_ancestor",
                     "kTransitionCount", "executeEntry",
                     "leastCommonAncestorOf"):
        check(beklenen in hepsi, "uzun ad uretiliyor: %s" % beklenen)

    # C standardi: C99 DEGIL, C11 + GNU uzantilari.
    c_metin = " ".join(c_files.values())
    check("gnu11" in c_metin, "C ciktisi -std=gnu11 diyor")
    check("9899:2011" in c_metin, "C ciktisi C11 standardina atifta bulunuyor")
    check("9899:1999" not in c_metin and "(C99)" not in c_metin,
          "C ciktisinda C99 atfi kalmadi")

    # Arayuzdeki dil etiketi: "C++ 11" yeter, parantez gereksiz.
    from app.ui.code_panel import LANGUAGES
    etiketler = [t for t, _k in LANGUAGES]
    check(any("C11" in t and "GNU" in t for t in etiketler),
          "dil listesi C11 + GNU diyor", str(etiketler))

    # -- URETIM BELIRLENIMCI OLMALI ------------------------------------
    # Basliktaki saniyelik "Date" damgasi, model hic degismese bile her
    # uretimde dosyayi farkli kiliyordu: calisma alanina yazma adimi
    # dosyayi yeniden yaziyor, git calisma agacinda TUM uretilen dosyalar
    # "degismis" gorunuyor ve fark ekraninda tek satirlik bir tarih
    # degisikliginden baska bir sey cikmiyordu.
    from app.codegen.class_c_generator import generate_class_c as _gcc
    from app.codegen.class_cpp_generator import generate_class_cpp as _gcx
    from app.core.samples import demo_class_model
    for ad, uretec, model in (("C", generate_c, demo_machine()),
                              ("C++", generate_cpp, demo_machine()),
                              ("sinif C", _gcc, demo_class_model()),
                              ("sinif C++", _gcx, demo_class_model())):
        ilk = uretec(model)
        time.sleep(1.05)                   # SANIYE siniri asilsin
        ikinci = uretec(model)
        check(ilk == ikinci,
              "%s: ayni modelden ayni kaynak uretiliyor" % ad,
              "farkli dosyalar: %s"
              % [k for k in ilk if ilk[k] != ikinci.get(k)])
    check(not any("Date" in t for t in c_files.values()),
          "baslikta uretim zamani damgasi yok")

    # -- HIZALAMA ------------------------------------------------------
    # Sabit genislikli "%-38s" dolgulari, adlar uzadikca satiri
    # kaydiriyordu: #define degerleri, enum "=" isaretleri ve struct
    # yorumlari tirtikli duruyordu.
    basliklar = c_files["blinky.h"].splitlines()

    def _sutun(satirlar, isaret):
        """Isaretin basladigi SUTUNLAR kumesi (hepsi ayni olmali)."""
        return {ln.index(isaret) for ln in satirlar if isaret in ln}

    defines = [ln for ln in basliklar
               if ln.startswith("#define BLINKY_") and "(" in ln]
    check(len(defines) >= 4 and len(_sutun(defines, "(")) == 1,
          "#define degerleri tek sutunda hizali",
          "sutunlar: %s" % sorted(_sutun(defines, "(")))

    # Acilis parantezi artik KENDI SATIRINDA: blok "typedef enum" degil
    # ondan sonraki "{" ile baslar.
    for ad, bas, bit in (("durum", "typedef enum", "} blinky_state_t;"),
                         ("olay", "typedef enum", "} blinky_event_t;")):
        i = basliklar.index(bit)
        j = max(k for k in range(i) if basliklar[k] == bas)
        govde = [ln for ln in basliklar[j + 1:i] if "=" in ln]
        check(len(govde) >= 2 and len(_sutun(govde, "=")) == 1,
              "%s enum'unda '=' tek sutunda" % ad,
              "sutunlar: %s" % sorted(_sutun(govde, "=")))
        yorumlu = [ln for ln in govde if "/**<" in ln]
        if len(yorumlu) >= 2:
            check(len(_sutun(yorumlu, "/**<")) == 1,
                  "%s enum'unda yorumlar tek sutunda" % ad,
                  "sutunlar: %s" % sorted(_sutun(yorumlu, "/**<")))

    i = basliklar.index("} blinky_t;")
    j = max(k for k in range(i) if basliklar[k] == "typedef struct")
    alanlar = [ln for ln in basliklar[j + 1:i] if "/**<" in ln]
    check(len(alanlar) >= 3 and len(_sutun(alanlar, "/**<")) == 1,
          "struct yorumlari tek sutunda hizali",
          "sutunlar: %s" % sorted(_sutun(alanlar, "/**<")))
    check(basliklar[i - 1].strip() == "",
          "struct kapanistan once bos satir var",
          "onceki satir: %r" % basliklar[i - 1])
    check(any("*ctx;" in ln for ln in alanlar),
          "gosterici yildizi ADA yapisik (C gelenegi)",
          "alanlar: %s" % alanlar)


def test_diagram_readability() -> None:
    """Choice'in kenari eksik olmamali; yazilar okunur olmali."""
    print("\n== 20. Diyagram okunabilirligi ==")
    from PyQt6.QtWidgets import QApplication

    from app.core.model import StateKind
    from app.ui.diagram_items import StateItem
    from app.ui.main_window import MainWindow
    from app.ui.theme import DARK, LIGHT, stylesheet, ui_font

    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setFont(ui_font(9))
    app.setStyleSheet(stylesheet())
    win = MainWindow()
    win.resize(1400, 900)
    win.show()
    win.canvas.auto_edit = False
    app.processEvents()
    win.doc.replace(demo_machine(), None)
    for _ in range(20):
        app.processEvents()

    # (a) Choice elmasi KAPALI bir yol olmali. QPainterPath.addPolygon
    #     cokgeni kapatmaz; secim halesinin bir kenari cizilmiyordu.
    choice = next(s for s in win.doc.machine.states.values()
                  if s.kind is StateKind.CHOICE)
    item = next(it for it in win.canvas.scene().items()
                if isinstance(it, StateItem) and it.state.id == choice.id)
    for ad, yol in (("shape", item.shape()), ("hale", item._halo_path())):
        # Kapali bir cokgenin ilk ve son noktasi AYNIDIR.
        ilk = yol.elementAt(0)
        son = yol.elementAt(yol.elementCount() - 1)
        check(abs(ilk.x - son.x) < 0.01 and abs(ilk.y - son.y) < 0.01,
              "choice %s yolu kapali (dort kenar da ciziliyor)" % ad,
              "ilk=(%.1f,%.1f) son=(%.1f,%.1f)" % (ilk.x, ilk.y, son.x, son.y))

    # (b) Gecis etiketi: acik temada SIMSIYAH, zemin TUVAL rengi.
    check(LIGHT["LABEL_TEXT"] == "#000000",
          "acik temada gecis etiketi simsiyah",
          "LABEL_TEXT = %s" % LIGHT["LABEL_TEXT"])
    check(LIGHT["LABEL_BG"] == LIGHT["CANVAS_BG"],
          "acik temada etiket zemini beyaz kutucuk DEGIL",
          "LABEL_BG=%s CANVAS_BG=%s"
          % (LIGHT["LABEL_BG"], LIGHT["CANVAS_BG"]))
    check(DARK["LABEL_BG"] == DARK["CANVAS_BG"],
          "koyu temada da etiket zemini tuval rengi")

    # (c) Durum govdesi yazisi BUYUTULDU ve tam kontrastla yaziliyor.
    # pointSize() ORTAMA BAGLIDIR: ekransiz kosumda gercek yazi tipi yok,
    # Qt ikame ediyor ve mono_font(9) 8 punto olarak geri donuyor. Bu yuzden
    # mutlak punto degil, AYNI ortamda olculen satir yuksekligi karsilastirilir.
    from PyQt6.QtGui import QFontMetricsF
    from app.ui.theme import mono_font
    simple = next(it for it in win.canvas.scene().items()
                  if isinstance(it, StateItem)
                  and it.state.kind is StateKind.SIMPLE)
    onceki = QFontMetricsF(mono_font(8)).height()
    simdiki = QFontMetricsF(simple.f_body).height()
    check(simdiki > onceki,
          "durum govdesi yazisi BUYUTULDU",
          "satir yuksekligi %.1f -> %.1f" % (onceki, simdiki))
    yol = os.path.join(ROOT, "app", "ui", "diagram_items.py")
    with open(yol, encoding="utf-8") as fh:
        govde = fh.read()
    check("C.TEXT_DIM if composite" not in govde,
          "bilesik durum davranislari SILIK renkle yazilmiyor")

    win.doc.mark_clean()
    win.class_doc.mark_clean()
    win.settings.clear()
    win.close()
    app.processEvents()


def test_no_demo_on_startup() -> None:
    """Acilista ORNEK MODEL yuklenmemeli.

    Uygulama her iki kipi de hazir demo modelle (Blinky / RoomPlan)
    aciyordu. Calisma alaninda tek model yokken -- panel "No models here
    yet" derken -- tuvalde dokuz durumluk bir diyagram duruyor, ozellikler
    panelinde "Blinky" yaziyordu; kullanici bunu kendi projesinin parcasi
    saniyordu. Ornekler yalnizca File > Sample... ile gelmeli.
    """
    print("\n== 18. Acilista ornek model yok ==")
    from PyQt6.QtWidgets import QApplication

    from app.core.samples import (demo_class_model, demo_machine,
                                  empty_class_model, empty_machine)
    from app.ui.main_window import MainWindow
    from app.ui.theme import stylesheet, ui_font

    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setFont(ui_font(9))
    app.setStyleSheet(stylesheet())
    win = MainWindow()
    win.resize(1400, 900)
    win.show()
    win.canvas.auto_edit = False
    win.class_canvas.auto_edit = False
    app.processEvents()

    try:
        sm, cm = win.doc.machine, win.class_doc.machine
        check(len(sm.states) == len(empty_machine().states),
              "durum kipi BOS sablonla aciliyor",
              "%d durum: %s" % (len(sm.states),
                                [s.name for s in sm.states.values()]))
        check(len(sm.states) != len(demo_machine().states),
              "acilista Blinky ornegi YUKLU DEGIL")
        check(sm.name != demo_machine().name,
              "makine adi ornekten gelmiyor", "ad = %r" % sm.name)
        check(len(cm.classes) == len(empty_class_model().classes),
              "sinif kipi BOS sablonla aciliyor",
              "%d sinif" % len(cm.classes))
        check(len(cm.classes) != len(demo_class_model().classes),
              "acilista RoomPlan ornegi YUKLU DEGIL")

        # Ornek MENUDEN gelmeli ve gercekten yuklemeli.
        check(hasattr(win, "a_demo") and hasattr(win, "a_demo_class"),
              "ornek yukleme eylemleri menude var")
        win.load_demo()
        for _ in range(10):
            app.processEvents()
        check(len(win.doc.machine.states) == len(demo_machine().states),
              "File > Sample State Machine ornegi yukluyor",
              "%d durum" % len(win.doc.machine.states))
        win.load_demo_class()
        for _ in range(10):
            app.processEvents()
        check(len(win.class_doc.machine.classes)
              == len(demo_class_model().classes),
              "File > Sample Class Diagram ornegi yukluyor",
              "%d sinif" % len(win.class_doc.machine.classes))
    finally:
        win.doc.mark_clean()
        win.class_doc.mark_clean()
        win.settings.clear()
        win.close()
        app.processEvents()


def test_removed_model_and_bends() -> None:
    """Silinen model tuvalde KALMAMALI; ok her noktasindan bukulebilmeli."""
    print("\n== 17. Model silme ve cok noktali ok ==")
    import tempfile

    from PyQt6.QtCore import QPointF
    from PyQt6.QtWidgets import QApplication

    import app.ui.workspace_tree as wt
    from app.core.samples import empty_machine
    from app.core.workspace import Workspace
    from app.ui.diagram_items import TransitionItem
    from app.ui.main_window import MainWindow
    from app.ui.theme import stylesheet, ui_font

    class _Yes:
        class StandardButton:
            Yes = 1
            No = 0

        @staticmethod
        def warning(*_a, **_k):
            return 1

    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setFont(ui_font(9))
    app.setStyleSheet(stylesheet())

    work = tempfile.mkdtemp(prefix="usd_rm_")
    ws = Workspace.create(os.path.join(work, "rm-demo"))
    model_path = os.path.join(ws.model_path, "blinky.usm")
    with open(model_path, "w", encoding="utf-8") as fh:
        fh.write(demo_machine().to_json())

    win = MainWindow()
    win.resize(1400, 900)
    win.show()
    win.canvas.auto_edit = False
    win.class_canvas.auto_edit = False
    app.processEvents()
    win.apply_workspace(ws)
    for _ in range(30):
        app.processEvents()

    try:
        # -- Calisma alanindan SILINEN model tuvalde durmamali -------------
        # Onceki surum yalnizca doc.path'i temizliyordu: dosya agactan
        # kalkiyor ama diyagram tuvalde kaliyor ve baslikta artik var
        # OLMAYAN dosyanin adi yaziyordu.
        win.open_model_path(model_path)
        for _ in range(30):
            app.processEvents()
        check(len(win.doc.machine.states) == len(demo_machine().states),
              "model acildi", "%d durum" % len(win.doc.machine.states))

        gercek = wt.QMessageBox
        wt.QMessageBox = _Yes
        try:
            hedef = next(it for it in win.ws_tree._all_items()
                         if it.data(0, wt.NODE_ROLE) == "model")
            win.ws_tree.setCurrentItem(hedef)
            win.ws_tree.remove_selected()
            for _ in range(40):
                app.processEvents()
        finally:
            wt.QMessageBox = gercek

        check(not os.path.exists(model_path), "dosya diskten silindi")
        check(win.doc.path is None, "belge artik silinen dosyaya bagli degil",
              "path = %r" % win.doc.path)
        check(len(win.doc.machine.states) == len(empty_machine().states),
              "tuval bos sablona dondu (silinen diyagram kalmadi)",
              "%d durum" % len(win.doc.machine.states))
        check("blinky.usm" not in win.windowTitle(),
              "baslikta silinen dosyanin adi yok",
              "baslik = %r" % win.windowTitle())

        # -- Ok HER noktasindan bukulebilmeli ------------------------------
        # Onceki surum, nereden cekilirse cekilsin butun kirilma
        # noktalarini TEK noktayla degistiriyordu; ikinci bir kivrim
        # eklemek mumkun degildi.
        win.doc.replace(demo_machine(), None)
        for _ in range(20):
            app.processEvents()
        item = next(it for it in win.canvas.scene().items()
                    if isinstance(it, TransitionItem) and it.src is not it.dst
                    and not it.transition.waypoints)
        tr = item.transition

        route = item.route_points()
        check(len(route) == 2, "bukumsuz okun yolu iki uctan olusuyor",
              "%d nokta" % len(route))
        orta = (route[0] + route[-1]) / 2.0
        kip, indis = item.grab_at(orta, 9.0)
        check(kip == "insert" and indis == 0,
              "bos okun ortasi YENI nokta ekler", "%s %d" % (kip, indis))
        tr.waypoints.insert(indis, [orta.x() + 40.0, orta.y() + 60.0])
        item.update_path()

        route = item.route_points()
        check(len(route) == 3, "tek bukumlu yol uc noktali",
              "%d nokta" % len(route))
        ikinci = (route[1] + route[2]) / 2.0
        kip2, indis2 = item.grab_at(ikinci, 9.0)
        check(kip2 == "insert" and indis2 == 1,
              "IKINCI parca da yeni nokta ekleyebiliyor",
              "%s %d" % (kip2, indis2))
        tr.waypoints.insert(indis2, [ikinci.x() - 30.0, ikinci.y() + 30.0])
        item.update_path()
        check(len(tr.waypoints) == 2, "ok iki kirilma noktasi tasiyabiliyor",
              "%d nokta" % len(tr.waypoints))

        mevcut = QPointF(*tr.waypoints[0])
        kip3, indis3 = item.grab_at(mevcut, 9.0)
        check(kip3 == "move" and indis3 == 0,
              "var olan nokta TASINIYOR (yenisi eklenmiyor)",
              "%s %d" % (kip3, indis3))

        # Uzaktaki bir tik o noktayi TUTMUS sayilmamali.
        uzak = QPointF(mevcut.x() + 500.0, mevcut.y() + 500.0)
        kip4, _i4 = item.grab_at(uzak, 9.0)
        check(kip4 == "insert", "uzak tik noktayi tutmuyor", "kip = %s" % kip4)
    finally:
        win.doc.mark_clean()
        win.class_doc.mark_clean()
        win.settings.clear()
        win.close()
        app.processEvents()


def _tree_labels(tree):
    """Bir QTreeWidget'taki TUM satir metinlerini toplar (tembel dugumler acilir)."""
    stack = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
    out = []
    seen = 0
    while stack and seen < 5000:
        item = stack.pop()
        seen += 1
        if item is None:
            continue
        # Model dugumleri icerigi ACILINCA yukler (tembel yukleme).
        item.setExpanded(True)
        out.append(item.text(0))
        stack.extend(item.child(i) for i in range(item.childCount()))
    return out


#: Ingilizce'de bulunmayan Turkce harfler.
_TR_LETTERS = set("çğıöşüÇĞİÖŞÜ")

#: Aksansiz yazilmis Turkce kelimeler (arayuzde bunlar da kalmamali).
_TR_WORDS = {
    "durum", "gecis", "olay", "kaydet", "ac", "yeni", "sil", "duzenle",
    "dosya", "ayarlar", "yardim", "kapat", "calistir", "baslat", "adim",
    "temizle", "uygula", "iptal", "tamam", "hata", "uyari", "sinif",
    "makine", "tuval", "kod", "uret", "dogrula", "depo", "degisiklik",
    "secim", "arac", "araclar", "gorunum", "simge", "yazi", "tema",
}


def _has_turkish(text: str) -> bool:
    if set(text) & _TR_LETTERS:
        return True
    words = re.findall(r"[A-Za-z]+", text.lower())
    return any(w in _TR_WORDS for w in words)


from PyQt6 import sip


def _canli(nesne) -> bool:
    """C++ tarafi hala duruyor mu? (tek basina YETMEZ, bkz. _menuler)"""
    return not sip.isdeleted(nesne)


def _menuler(win):
    """Menu cubugundan ULASILABILEN menuleri toplar.

    `findChildren(QMenu)` KULLANILMAZ. QMenuBar, sigmayan ogeler icin
    kendi ozel tasma menusunu (`qt_menubar_ext_button`in acilir menusu)
    tutar; Qt bunu yerlesim sirasinda silip yeniden yaratir. Anlik
    goruntude kalan olu isaretci uzerinde `title()` cagirmak sureci
    SEGFAULT ile dusuruyordu -- `sip.isdeleted` bile fark etmiyor,
    cunku nesne C++ tarafinda silindigi halde sarmalayici bilgilenmiyor.

    Menu cubugunun EYLEMLERINDEN yurumek yalnizca bizim koydugumuz --
    yani kullaniciya gercekten gorunen -- menuleri verir.
    """
    bar = win.menuBar()
    if bar is None:
        return []
    bulunan = []
    yigin = [eylem.menu() for eylem in bar.actions()]
    while yigin:
        menu = yigin.pop()
        if menu is None or menu in bulunan or not _canli(menu):
            continue
        bulunan.append(menu)
        yigin.extend(eylem.menu() for eylem in menu.actions())
    return bulunan


def _eylemler(win):
    """Kullaniciya gorunen QAction'lari toplar.

    `findChildren(QAction)` ayni tuzaga duser (bkz. _menuler): listeye
    Qt'nin gecici ic eylemleri de girer ve olulerinden birinde
    `toolTip()` okumak ya segfault verir ya da bozuk uzunluk yuzunden
    "Negative size passed to PyUnicode_New" ile patlar. Menu cubugu,
    arac cubuklari ve pencerenin kendi eylemleri KULLANICIYA GORUNEN
    kumeyi tam olarak verir; testin sordugu sey de budur.
    """
    from PyQt6.QtWidgets import QToolBar

    bulunan = []
    kaynaklar = [win.menuBar()] if win.menuBar() is not None else []
    kaynaklar.extend(cubuk for cubuk in win.findChildren(QToolBar)
                     if _canli(cubuk))
    kaynaklar.append(win)

    yigin = []
    for kaynak in kaynaklar:
        yigin.extend(kaynak.actions())
    for menu in _menuler(win):
        yigin.extend(menu.actions())

    for eylem in yigin:
        if eylem is None or not _canli(eylem) or eylem in bulunan:
            continue
        bulunan.append(eylem)
    return bulunan


def _visible_texts(win):
    """Pencerede kullaniciya gorunen tum metinleri toplar.

    Gezinti boyunca COP TOPLAYICI DURDURULUR. `findChildren` bir anlik
    goruntu verir; listeyi dolasirken `out.append` her ayirma isleminde
    bir gc turunu tetikleyebilir ve gc, Python'un sahibi oldugu bir
    QObject sarmalayicisini toplarken ALTTAKI C++ NESNESINI DE siler.
    Elimizdeki bayat isaretcide `text()` cagirmak sureci segfault ile
    dusuruyordu -- `sip.isdeleted` bunu gormuyor, cunku silme sip'e
    bildirilmeden gerceklesiyor. Gezinti icinde olay islenmedigi icin
    gc'yi kisa sureligine durdurmak nesnelerin canli kalmasini garanti
    eder; `finally` onu her kosulda geri acar.
    """
    import gc

    onceden_aciktik = gc.isenabled()
    gc.disable()
    try:
        return _collect_visible_texts(win)
    finally:
        if onceden_aciktik:
            gc.enable()


def _collect_visible_texts(win):
    from PyQt6.QtGui import QAction
    from PyQt6.QtWidgets import (QAbstractButton, QGroupBox, QLabel, QLineEdit,
                                 QMenu, QPlainTextEdit, QTabWidget, QTreeWidget)

    def cocuklar(tur):
        return [nesne for nesne in win.findChildren(tur) if _canli(nesne)]

    out = []
    for widget in cocuklar(QLabel):
        out.append(widget.text())
    for widget in cocuklar(QAbstractButton):
        out.append(widget.text())
        out.append(widget.toolTip())
    for widget in cocuklar(QGroupBox):
        out.append(widget.title())
    for widget in cocuklar(QLineEdit):
        out.append(widget.placeholderText())
    for widget in cocuklar(QPlainTextEdit):
        out.append(widget.placeholderText())
    for widget in _menuler(win):
        out.append(widget.title())
    for action in _eylemler(win):
        out.append(action.text())
        out.append(action.toolTip())
        out.append(action.statusTip())
    for tabs in cocuklar(QTabWidget):
        for i in range(tabs.count()):
            out.append(tabs.tabText(i))
            out.append(tabs.tabToolTip(i))
    for tree in cocuklar(QTreeWidget):
        head = tree.headerItem()
        for i in range(tree.columnCount()):
            out.append(head.text(i))
        stack = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            if item is None:
                continue
            for i in range(tree.columnCount()):
                out.append(item.text(i))
            stack.extend(item.child(i) for i in range(item.childCount()))
    return [t for t in out if t and t.strip()]


# --------------------------------------------------------------------------- #
#  12) Spesifikasyon atfi
# --------------------------------------------------------------------------- #

def test_spec_citations() -> None:
    """Her dogrulama kodunun tek bir kural karsiligi olmali."""
    print("\n== 12. UML 2.5.1 spesifikasyon atiflari ==")

    kodlar = set()
    for ad in ("validator.py", "class_validator.py"):
        yol = os.path.join(ROOT, "app", "core", ad)
        with open(yol, encoding="utf-8") as fh:
            kodlar |= set(re.findall(r'\b(?:err|warn|info)\(\s*"([A-Z]\d{3})"',
                                     fh.read()))

    eksik = sorted(kodlar - set(uml_spec.ALL_RULES))
    check(not eksik, "her dogrulama kodunun spec kaydi var (%d kod)" % len(kodlar),
          "kaydi olmayan: %s" % eksik)

    fazla = sorted(set(uml_spec.ALL_RULES) - kodlar)
    check(not fazla, "tabloda olu kayit yok", "kullanilmayan: %s" % fazla)

    # UML atfi olan her bolum, belgeden uretilen dizinde bulunmali; aksi
    # halde panelde sayfa numarasi BOS cikar ve atif ise yaramaz.
    sayfasiz = [(k, r.section) for k, r in uml_spec.ALL_RULES.items()
                if not r.is_tool_rule and r.page is None]
    check(not sayfasiz, "her UML atfi bir sayfa numarasina cozuluyor",
          "cozulmeyen: %s" % sayfasiz)

    # Ayni kod iki farkli kurala verilemez: V070/V061 boyle bozulmustu.
    for kod, ref in uml_spec.ALL_RULES.items():
        if ref.is_tool_rule:
            continue
        check(bool(ref.rule.strip()), "%s: kural cumlesi bos degil" % kod) \
            if not ref.rule.strip() else None


# --------------------------------------------------------------------------- #
#  13) Tema
# --------------------------------------------------------------------------- #

def test_theme() -> None:
    """Iki palet ayni anahtarlari tasimali ve gecis renkleri degistirmeli."""
    print("\n== 13. Koyu / acik tema ==")

    from app.ui import theme

    check(set(theme.DARK) == set(theme.LIGHT),
          "iki palet ayni anahtar kumesini tasiyor (%d renk)" % len(theme.DARK),
          "yalniz DARK: %s | yalniz LIGHT: %s"
          % (sorted(set(theme.DARK) - set(theme.LIGHT)),
             sorted(set(theme.LIGHT) - set(theme.DARK))))

    # Gecersiz bir renk Qt tarafindan SESSIZCE yok sayilir; import aninda
    # yakalanmasi icin bicim burada da dogrulanir.
    bozuk = [(ad, k, v) for ad, palet in (("DARK", theme.DARK),
                                          ("LIGHT", theme.LIGHT))
             for k, v in palet.items()
             if not (isinstance(v, str) and len(v) == 7 and v[0] == "#"
                     and all(c in "0123456789abcdefABCDEF" for c in v[1:]))]
    check(not bozuk, "butun renk degerleri gecerli #RRGGBB", "%s" % bozuk)

    onceki = theme.active_theme()
    try:
        theme.apply_theme("light")
        acik_panel, acik_metin = theme.C.PANEL, theme.C.TEXT
        theme.apply_theme("dark")
        koyu_panel, koyu_metin = theme.C.PANEL, theme.C.TEXT

        check(acik_panel != koyu_panel and acik_metin != koyu_metin,
              "tema degisimi renkleri gercekten degistiriyor")

        # Acik temada metin zeminden KOYU olmali; ters cevrilmis bir palet
        # okunmaz bir arayuz uretir.
        def parlaklik(hexs):
            r, g, b = (int(hexs[i:i + 2], 16) for i in (1, 3, 5))
            return 0.299 * r + 0.587 * g + 0.114 * b

        check(parlaklik(theme.LIGHT["TEXT"]) < parlaklik(theme.LIGHT["PANEL"]),
              "acik temada metin zeminden koyu")
        check(parlaklik(theme.DARK["TEXT"]) > parlaklik(theme.DARK["PANEL"]),
              "koyu temada metin zeminden acik")

        check(theme.apply_theme("bilinmeyen") == "dark",
              "bilinmeyen tema adi koyuya duser")
    finally:
        theme.apply_theme(onceki)


# --------------------------------------------------------------------------- #
#  Tema zitligi (okunabilirlik)
# --------------------------------------------------------------------------- #

def _luminance(colour: str) -> float:
    """WCAG bagil parlaklik."""
    value = colour.lstrip("#")
    parts = [int(value[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
              for c in parts]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(fg: str, bg: str) -> float:
    """WCAG kontrast orani (1 = ayni renk, 21 = siyah/beyaz)."""
    a, b = _luminance(fg), _luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def test_theme_contrast() -> None:
    """Her tema, metni zeminden AYIRT EDILEBILIR tutmali.

    Stil sayfasinda bir zamanlar sabit renkler vardi (beyaz secim metni, koyu
    'alternate-background-color'); acik temada satirlar okunmuyordu. Bu vaka
    her iki paleti de olcerek ayni hatanin geri gelmesini engeller.
    """
    print("\n== 15. Tema zitligi ==")
    from app.ui import theme as theme_mod
    from app.ui.theme import DARK, LIGHT

    # (on plan, zemin, en az oran, aciklama)
    pairs = [
        ("TEXT", "PANEL", 4.5, "govde metni panel uzerinde"),
        ("TEXT", "EDITOR_BG", 4.5, "govde metni liste zemininde"),
        ("TEXT", "ALT_ROW", 4.5, "govde metni siralamali satirda"),
        ("TEXT_BRIGHT", "PANEL", 4.5, "vurgulu metin panel uzerinde"),
        ("TEXT_DIM", "PANEL", 3.0, "soluk metin panel uzerinde"),
        ("SELECTED_TEXT", "ACCENT_DARK", 4.5, "secili satir metni"),
        ("ON_ACCENT", "ACCENT_DARK", 4.5, "vurgu zemini uzerindeki metin"),
        ("SELECTION_TEXT", "SELECTION", 4.5, "metin alanindaki secim"),
        ("TEXT", "HOVER", 3.0, "fare altindaki satir"),
        ("SIM_ACTIVE", "PANEL_DARK", 4.5, "simulasyondaki etkin durum"),
        ("CYAN", "PANEL_DARK", 4.5, "simulasyon panel basligi"),
        ("TEXT_DIM", "PANEL_DARK", 3.0, "simulasyon alan etiketleri"),
        ("GREEN", "PANEL_DARK", 3.0, "simulasyon Start dugmesi"),
        ("STATE_TITLE", "STATE_HEADER", 3.0, "durum basligi"),
        ("STATE_TITLE", "STATE_HEADER_ALT", 3.0, "bilesik durum basligi"),
        ("STATE_TEXT", "STATE_FILL", 4.5, "durum govdesi metni"),
        ("CLASS_TEXT", "CLASS_FILL", 4.5, "sinif govdesi metni"),
        ("STATE_TITLE", "CLASS_HEADER", 3.0, "sinif basligi"),
        ("STATE_TITLE", "IFACE_HEADER", 3.0, "arayuz basligi"),
        ("TRANSITION", "CANVAS_BG", 3.0, "gecis oku tuval uzerinde"),
        # Initial / Final, UML'in tek renk gosterimiyle cizilir. Duz siyah
        # koyu tuval uzerinde 1.2:1 kalirdi; murekkep temaya gore doner ama
        # HER IKI temada da tuvalden ayrilmak ZORUNDA.
        ("INITIAL_FILL", "CANVAS_BG", 4.5, "initial sozde-durumu"),
        ("FINAL_RING", "CANVAS_BG", 4.5, "final durum"),
        ("INITIAL_FILL", "STATE_FILL_ALT", 3.0,
         "bilesik durum icindeki initial"),
        ("STATE_TEXT", "LABEL_BG", 4.5, "gecis etiketi"),
        ("RED", "PANEL", 3.0, "hata rengi"),
        ("WARN", "PANEL", 3.0, "uyari rengi"),
        ("GREEN", "PANEL", 3.0, "basari rengi"),
        ("GIT_ADD", "GIT_ADD_BG", 3.0, "eklenen fark satiri"),
        ("GIT_DEL", "GIT_DEL_BG", 3.0, "silinen fark satiri"),
        # Uretilen kod paneli ARTIK TEMAYI IZLER. Eskiden Visual Studio
        # klasik semasi sabitti ve koyu temada beyaz kaliyordu.
        ("CODE_TEXT", "CODE_BG", 4.5, "uretilen kod metni"),
        ("CODE_COMMENT", "CODE_BG", 4.5, "kod yorumlari"),
        ("CODE_TEXT", "CODE_CURRENT_LINE", 4.5, "etkin kod satiri"),
        ("CODE_COMMENT", "CODE_CURRENT_LINE", 4.0, "etkin satirdaki yorum"),
        ("CODE_GUTTER_TEXT", "CODE_GUTTER_BG", 4.5, "satir numaralari"),
        ("CODE_GUTTER_CUR", "CODE_GUTTER_BG", 4.5, "etkin satir numarasi"),
        ("SELECTION_TEXT", "SELECTION", 4.5, "kod panelindeki secim"),
        # Ilerleme cubugunun DOLU kismi bos izinden ayirt edilebilmeli
        # (uzerinde yazi YOKTUR; bkz. theme.py QProgressBar aciklamasi).
        ("ACCENT", "EDITOR_BG", 3.0, "ilerleme cubugu dolgusu"),

        # -- Kullanici: "hem light hem dark modda arayuzdeki BUTUN yazilarin
        #    gozukmesi lazim". Asagidaki ciftler kaynaktan cikarilmistir:
        #    her setStyleSheet("color: ...") cagrisinin on plani, o bilesenin
        #    GERCEK zemini ile eslestirilmistir. Zeminler theme.stylesheet()
        #    kurallarindan gelir: QMainWindow/QDialog/QToolBar/QMenuBar =
        #    PANEL, QStatusBar = PANEL_DARK, kod cubugu ve arama cubugu =
        #    PANEL_DARK, simulasyon paneli = PANEL_DARK.
        ("TEXT_DIM", "PANEL_DARK", 3.0, "durum cubugu etiketleri"),
        ("RED", "PANEL_DARK", 3.0, "durum cubugu hata sayaci"),
        ("WARN", "PANEL_DARK", 3.0, "durum cubugu uyari sayaci"),
        ("GREEN", "PANEL_DARK", 3.0, "durum cubugu 'validated'"),
        ("TEXT_BRIGHT", "PANEL_DARK", 4.5, "arac ipucu metni"),
        ("TEXT_DIM", "PANEL_DARK", 3.0, "kod cubugu basligi"),
        ("GREEN", "PANEL_DARK", 3.0, "kod cubugu 'N files' bilgisi"),
        ("RED", "PANEL_DARK", 3.0, "kod cubugu 'not generated'"),
        ("WARN", "PANEL_DARK", 3.0, "kod cubugu 'out of date'"),
        ("TEXT_DIM", "PANEL_DARK", 3.0, "arama cubugu etiketleri"),
        ("RED", "PANEL_DARK", 3.0, "arama cubugu 'eslesme yok'"),
        ("ACCENT", "PANEL", 3.0, "diyalog vurgu metni"),
        ("TEXT_DIM", "PANEL", 3.0, "diyalog ipucu metni"),
        ("GREEN", "PANEL", 3.0, "inspector 'gecerli' isareti"),
        ("WARN", "PANEL", 3.0, "depo paneli uyari bandi"),
        ("BORDER_LIGHT", "PANEL", 1.5, "ayirici cizgi (metin degil)"),
        ("RED", "BANNER_ERROR_BG", 4.5, "kod paneli hata bandi"),
        ("WARN", "BANNER_WARN_BG", 4.5, "kod paneli uyari bandi"),
        ("TEXT", "EDITOR_BG", 4.5, "fark goruntuleyici metni"),
    ]
    for name, palette in (("dark", DARK), ("light", LIGHT)):
        worst = None
        for fg, bg, minimum, label in pairs:
            got = _contrast(palette[fg], palette[bg])
            if got < minimum:
                check(False, "%s: %s okunur (>=%.1f)" % (name, label, minimum),
                      "%s(%s) / %s(%s) = %.2f"
                      % (fg, palette[fg], bg, palette[bg], got))
            elif worst is None or got < worst[0]:
                worst = (got, label)
        if worst is not None:
            check(True, "%s paleti: %d cift okunur (en dusuk %.2f, %s)"
                  % (name, len(pairs), worst[0], worst[1]))

    # Kod paneli TEMAYI IZLEMELI: koyu temada koyu, acik temada acik.
    # Eskiden sabit Visual Studio klasik semasiydi ve koyu arayuzde ekranin
    # yarisi beyaz parliyordu.
    check(DARK["CODE_BG"] == DARK["EDITOR_BG"],
          "koyu temada kod zemini panel zeminiyle ayni",
          "CODE_BG=%s EDITOR_BG=%s" % (DARK["CODE_BG"], DARK["EDITOR_BG"]))
    check(_luminance(DARK["CODE_BG"]) < 0.2,
          "koyu temada kod paneli KOYU",
          "parlaklik = %.3f" % _luminance(DARK["CODE_BG"]))
    check(_luminance(LIGHT["CODE_BG"]) > 0.8,
          "acik temada kod paneli ACIK",
          "parlaklik = %.3f" % _luminance(LIGHT["CODE_BG"]))
    check(not hasattr(theme_mod, "VS_CLASSIC") and not hasattr(theme_mod, "VS"),
          "Visual Studio klasik semasi kaldirildi")

    # SOZDIZIMI SEMASI. Kullanici "sadece siyah-yesil" gorunumu reddetti:
    # panel anahtar kelime / tip / dize / sayi / onislemci ayrimi yapmali.
    hl_path = os.path.join(ROOT, "app", "ui", "highlighter.py")
    with open(hl_path, encoding="utf-8") as fh:
        hl_body = fh.read()
    used = set(re.findall(r"\bC\.(CODE_[A-Z_]+)\b", hl_body))
    beklenen = {"CODE_COMMENT", "CODE_KEYWORD", "CODE_TYPE", "CODE_STRING",
                "CODE_NUMBER", "CODE_PREPROC", "CODE_FUNCTION"}
    check(beklenen <= used,
          "renklendirici tam sozdizimi semasi kullaniyor",
          "eksik: %s" % sorted(beklenen - used))
    # Her renk TEMADAN gelmeli: sabit yazilmis bir renk temalardan birinde
    # okunmaz olur (eski Visual Studio semasinin sorunu tam olarak buydu).
    sabit = re.findall(r'_fmt\(\s*"#', hl_body)
    check(not sabit, "renklendiricide sabit renk yok",
          "%d sabit renk" % len(sabit))

    _check_highlighter_on_real_code()


def _comment_mask(text):
    """Her karakter bir yorumun ICINDE mi? (renklendiriciden BAGIMSIZ tarayici)"""
    mask = [False] * len(text)
    i, n, state = 0, len(text), "code"
    while i < n:
        c, two = text[i], text[i:i + 2]
        if state == "code":
            if two in ("//", "/*"):
                state = "line" if two == "//" else "block"
                mask[i] = mask[i + 1] = True
                i += 2
                continue
            if c == '"':
                state = "str"
            elif c == "'":
                state = "chr"
            i += 1
        elif state == "line":
            if c == "\n":
                state = "code"
            else:
                mask[i] = True
            i += 1
        elif state == "block":
            mask[i] = True
            if two == "*/":
                mask[i + 1] = True
                state = "code"
                i += 2
                continue
            i += 1
        else:                                   # str / chr
            if c == "\\":
                i += 2
                continue
            if (state == "str" and c == '"') or (state == "chr" and c == "'") \
                    or c == "\n":
                state = "code"
            i += 1
    return mask


def _check_highlighter_on_real_code() -> None:
    """URETILEN her dosyada: yesil == yorum, yorum == yesil (karakter karakter).

    Desen testleri kose durumlari yakalar; bu kontrol ise kullanicinin
    GERCEKTEN gordugu metni sinar. Ornegin MCU sablonlari derleme komutunu
    bir yorum icinde tasir ve icinde '//' gecer.
    """
    from PyQt6.QtGui import QColor, QTextDocument
    from PyQt6.QtWidgets import QApplication

    from app.codegen.cpp_generator import generate_cpp
    from app.core.samples import demo_class_model, demo_machine
    from app.ui.highlighter import CppHighlighter
    from app.ui.theme import C

    QApplication.instance() or QApplication([])

    files = {}
    files.update(generate_c(demo_machine()))
    files.update(generate_cpp(demo_machine()))
    files.update(generate_class_c(demo_class_model()))
    files.update(generate_class_cpp(demo_class_model()))

    green = QColor(C.CODE_COMMENT)
    bad = []
    for name, text in sorted(files.items()):
        text = text.replace("\r\n", "\n")
        expected = _comment_mask(text)
        painted = [False] * len(text)
        doc = QTextDocument()
        doc.setPlainText(text)
        hl = CppHighlighter(doc)
        hl.rehighlight()
        offset = 0
        block = doc.firstBlock()
        while block.isValid():
            for fr in block.layout().formats():
                if fr.format.foreground().color() == green:
                    for k in range(fr.start, fr.start + fr.length):
                        if 0 <= offset + k < len(painted):
                            painted[offset + k] = True
            offset += len(block.text()) + 1
            block = block.next()

        wrong = sum(1 for i in range(len(text))
                    if painted[i] != expected[i] and text[i] != "\n")
        if wrong:
            bad.append("%s (%d karakter)" % (name, wrong))
    check(not bad, "uretilen %d dosyada yesil == yorum (karakter karakter)"
          % len(files), "uyusmayan: %s" % bad)

    # TUVAL renkleri PANEL zemininde kullanilamaz. STATE_TITLE, tuvaldeki
    # RENKLI baslik seridi icin secilmistir ve iki temada da beyazdir; agac
    # koklerinde kullanildiginda acik temada beyaz uzerine beyaz yaziyordu ve
    # model agacinin kok satiri tamamen gorunmez oluyordu.
    canvas_only = ("STATE_TITLE", "STATE_FILL", "STATE_HEADER",
                   "STATE_HEADER_ALT", "CLASS_HEADER", "IFACE_HEADER",
                   "ABSTRACT_HEADER", "CLASS_FILL", "LABEL_BG")
    ui_files = ("panels.py", "workspace_tree.py", "inspector.py",
                "sim_panel.py", "git_panel.py", "code_panel.py")
    for name in ui_files:
        path = os.path.join(ROOT, "app", "ui", name)
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
        used = [c for c in canvas_only if re.search(r"\bC\.%s\b" % c, body)]
        check(not used, "%s: panel zemininde tuval rengi kullanmiyor" % name,
              "tuval rengi: %s" % used)


# --------------------------------------------------------------------------- #
#  Paketleme (EXE)
# --------------------------------------------------------------------------- #

def test_packaging() -> None:
    print("\n== 14. Paketleme ==")
    import struct

    # -- Cok cozunurluklu simge: tek kareli ICO'da Windows kucuk simgeleri
    #    256 px'ten olcekler ve gorev cubugunda bulanik gorunur.
    ico_path = os.path.join(ROOT, "docs", "app.ico")
    check(os.path.isfile(ico_path), "app.ico uretilmis")
    if os.path.isfile(ico_path):
        with open(ico_path, "rb") as fh:
            data = fh.read()
        _reserved, kind, count = struct.unpack("<HHH", data[:6])
        check(kind == 1, "gecerli ICO basligi")
        widths = []
        for i in range(count):
            entry = data[6 + i * 16:6 + i * 16 + 16]
            width, _h, _c, _r, _p, _bpp, size, offset = struct.unpack(
                "<BBBBHHII", entry)
            widths.append(width or 256)
            check(0 < offset and offset + size <= len(data),
                  "kare %d dosya sinirlari icinde" % i)
        check(count >= 5, "ICO cok cozunurluklu (%d kare)" % count, str(widths))
        for needed in (16, 32, 48, 256):
            check(needed in widths, "%d px kare var" % needed, str(widths))

    # -- Paketleme tarifi surum kontrolunden DISLANMAMALI
    ignore_path = os.path.join(ROOT, ".gitignore")
    if os.path.isfile(ignore_path):
        with open(ignore_path, encoding="utf-8") as fh:
            patterns = [ln.strip() for ln in fh if ln.strip()
                        and not ln.startswith("#")]
        check("*.spec" not in patterns,
              ".gitignore paketleme tarifini (*.spec) dislamiyor", str(patterns))
    check(os.path.isfile(os.path.join(ROOT, "UML-Design-Studio.spec")),
          "paketleme tarifi mevcut")
    check(os.path.isfile(os.path.join(ROOT, "LICENSE")), "GPL metni mevcut")

    # -- Cokme koruyucusu KONSOLSUZ pakette de calismali. PyInstaller
    #    --noconsole kipinde sys.stderr NULL'dur; korumasiz bir yazma
    #    excepthook'un kendisini cokertir ve diyalog hic acilmaz.
    from PyQt6.QtWidgets import QApplication
    import app.ui.main_window as mw

    shown = []

    class _Box:
        Icon = type("Icon", (), {"Critical": 1})

        def setIcon(self, *_a):
            pass

        def setWindowTitle(self, *_a):
            pass

        def setText(self, *_a):
            pass

        def setInformativeText(self, text):
            shown.append(text)

        def setDetailedText(self, *_a):
            pass

        def exec(self):
            return 0

    _app = QApplication.instance() or QApplication([])
    real_box, real_hook = mw.QMessageBox, sys.excepthook
    real_err, real_out = sys.stderr, sys.stdout
    try:
        mw.QMessageBox = _Box
        mw.install_crash_guard()
        sys.stderr = None                 # --noconsole kipini taklit et
        sys.stdout = None
        try:
            raise ValueError("regresyon denemesi")
        except ValueError:
            sys.excepthook(*sys.exc_info())
        sys.stderr, sys.stdout = real_err, real_out
        check(bool(shown), "stderr yokken de hata diyalogu gosterildi",
              str(shown))
    except Exception as exc:                       # noqa: BLE001
        sys.stderr, sys.stdout = real_err, real_out
        check(False, "cokme koruyucusu istisna sizdirdi: %s"
              % type(exc).__name__, str(exc))
    finally:
        sys.stderr, sys.stdout = real_err, real_out
        mw.QMessageBox, sys.excepthook = real_box, real_hook


# --------------------------------------------------------------------------- #
#  14) Model duzeyinde fark
# --------------------------------------------------------------------------- #

def test_model_diff() -> None:
    """Model farki ANLAMI karsilastirmali, JSON satirlarini degil."""
    print("\n== 14. Model duzeyinde fark ==")

    from app.core import model_diff

    eski = demo_machine()
    eski_json = eski.to_json()

    # -- YALNIZCA konum degisikligi HICBIR satir uretmemeli. Aksi halde
    #    diyagrami duzenlemek (kutulari tasimak) sahte fark yagmuruna
    #    donusur ve gercek degisiklikler kaybolur.
    tasinmis = demo_machine()
    for st in tasinmis.states.values():
        st.x += 37.0
        st.y -= 11.0
    rows = model_diff.diff_for("m.usm", eski_json, tasinmis.to_json())
    check(rows == [], "salt konum degisikligi fark uretmiyor",
          "uretilen: %s" % rows)

    # -- ekle / sil / degistir / yeniden adlandir
    yeni = demo_machine()
    yeni.add_state(State(id="x1", name="Standby", kind=StateKind.SIMPLE))
    off = next(s for s in yeni.states.values() if s.name == "Off")
    off.entry = "led_write(true);"
    ledon = next(s for s in yeni.states.values() if s.name == "LedOn")
    ledon.name = "LampOn"

    rows = model_diff.diff_for("m.usm", eski_json, yeni.to_json())
    metin = model_diff.render(rows)
    art, eksi, degisen = model_diff.summary(rows)

    check(art == 1, "eklenen durum sayiliyor (+1)", "olcum: %d" % art)
    check(degisen >= 2, "degisen durumlar sayiliyor (~>=2)",
          "olcum: %d" % degisen)
    check("+ simple Standby" in metin, "eklenen durum '+' ile isaretli", metin)
    check("renamed from 'LedOn'" in metin,
          "yeniden adlandirma sil+ekle degil, YENIDEN ADLANDIRMA", metin)
    check("entry:" in metin, "degisen alan adiyla gosteriliyor", metin)

    # -- silinen durum ve onun gecisleri
    silinmis = demo_machine()
    fault = next(s for s in silinmis.states.values() if s.name == "Fault")
    silinmis.remove_state(fault.id)
    rows = model_diff.diff_for("m.usm", eski_json, silinmis.to_json())
    metin = model_diff.render(rows)
    check("- simple Fault" in metin, "silinen durum '-' ile isaretli", metin)
    check("@@ Transitions @@" in metin,
          "silinen durumun gecisleri de raporlaniyor", metin)

    # -- kod dosyasi model farkina GIRMEZ (metin farki dogru olan)
    check(model_diff.diff_for("blinky.c", "int a;", "int b;") is None,
          "kod dosyasi metin farkina birakiliyor")

    # -- bozuk JSON cokertmemeli
    bozuk = model_diff.diff_for("m.usm", "{ bozuk", eski_json)
    check(isinstance(bozuk, list), "bozuk JSON istisna atmiyor")


# --------------------------------------------------------------------------- #
#  18) Calisma alani paneli
# --------------------------------------------------------------------------- #

def test_workspace_panel() -> None:
    """WORKSPACE paneli YALNIZCA calisma alanini gostermeli.

    Kullanici bildirdi: agacin tepesinde her zaman
    "◆ untitled.usm (not saved)" ve "◆ untitled.ucd (not saved)"
    duruyordu. Uygulama acilista her iki kip icin birer ornek belge
    kuruyor, ikisinin de dosyasi olmadigi icin bu satirlar KALICI hale
    geliyordu -- kullanici blinky.usm uzerinde calisirken bile.
    """
    print("\n== 18. Calisma alani paneli ==")

    from PyQt6.QtWidgets import QApplication
    from app.core.workspace import Workspace
    from app.ui.main_window import MainWindow, app_settings
    from app.ui.theme import stylesheet
    from app.ui import workspace_tree as WT

    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(stylesheet())

    # -- Ayarlar YALITILMIS olmali. Testler MainWindow kurup
    #    apply_workspace cagirinca "son kullanilanlar" ve "last_workspace"
    #    yazilir; bunlar kullanicinin gercek deposuna giderse testin
    #    gecici klasoru kullanicinin acilis listesine sizar (gercekten
    #    oldu: .../umlui_9z0xku7g/calisma).
    check(os.environ.get("UMLSTUDIO_SETTINGS_SCOPE", "") != "",
          "test sureci YALITILMIS ayar kapsami kullaniyor")
    check("UmlStateDiagramTool-" in app_settings().applicationName(),
          "app_settings kapsami uyguluyor",
          app_settings().applicationName())

    win = MainWindow()
    win.settings = app_settings()
    win.resize(1500, 900)
    win.show()
    app.processEvents()

    def satirlar():
        out = []
        yigin = [win.ws_tree.topLevelItem(i)
                 for i in range(win.ws_tree.topLevelItemCount())]
        while yigin:
            it = yigin.pop()
            out.append(it.text(0))
            yigin.extend(it.child(i) for i in range(it.childCount()))
        return out

    # -- Calisma alani YOKKEN panel bos kalmamali, yol tarif etmeli.
    bos = satirlar()
    check(bos, "calisma alani yokken panel bos degil")
    check(any("No workspace is open" in t for t in bos),
          "durum aciklaniyor", str(bos))
    check(any(WT.OPEN_WORKSPACE_HINT in t for t in bos),
          "ne yapilacagi soyleniyor", str(bos))

    # Ipucundaki menu adi ve kisayol GERCEK eylemle ayni olmali; ilk
    # yazimda "Open Workspace... (Ctrl+Shift+O)" yaziyordu, oysa eylem
    # "Workspace..." ve kisayol Ctrl+Shift+W idi.
    check(win.a_workspace.text().replace("&", "")
          in WT.OPEN_WORKSPACE_MENU,
          "ipucundaki menu adi gercek eylemle ayni",
          "%r vs %r" % (win.a_workspace.text(), WT.OPEN_WORKSPACE_MENU))
    check(win.a_workspace.shortcut().toString() == WT.OPEN_WORKSPACE_KEY,
          "ipucundaki kisayol gercek eylemle ayni",
          "%r vs %r" % (win.a_workspace.shortcut().toString(),
                        WT.OPEN_WORKSPACE_KEY))

    # -- BOS bir calisma alani: "untitled" YOK, yol tarifi VAR.
    kok = os.path.join(make_tree(), "empty-space")
    try:
        win.apply_workspace(Workspace.create(kok))
        app.processEvents()
        metinler = satirlar()
        check(not any("untitled" in t.lower() for t in metinler),
              "bos calisma alaninda 'untitled' satiri YOK", str(metinler))
        check(not any("not saved" in t.lower() for t in metinler),
              "'(not saved)' satiri YOK", str(metinler))
        check(any("empty-space" in t for t in metinler),
              "calisma alani adi gorunuyor", str(metinler))
        check(any("Ctrl+S" in t for t in metinler),
              "bos alan ne yapilacagini soyluyor", str(metinler))

        # -- Icinde model OLAN calisma alani: yalnizca o dosya listelenir.
        model_dosyasi = os.path.join(kok, "model", "pump.usm")
        with open(model_dosyasi, "w", encoding="utf-8") as fh:
            fh.write(win.doc.machine.to_json())
        win.refresh_workspace_tree()
        app.processEvents()
        metinler = satirlar()
        check(any("pump.usm" in t for t in metinler),
              "diskteki model listeleniyor", str(metinler))
        check(not any("untitled" in t.lower() for t in metinler),
              "model varken de 'untitled' YOK", str(metinler))
    finally:
        win.doc.mark_clean()
        win.class_doc.mark_clean()
        win.settings.clear()
        win.close()
        app.processEvents()
        remove_tree(os.path.dirname(kok))


# --------------------------------------------------------------------------- #
#  19) Tek sirali ust bar, tekerlek kazasi, ok bukme
# --------------------------------------------------------------------------- #

def test_single_top_bar() -> None:
    """Uc serit tek serit oldu; araclar tasmiyor, kipler ayri."""
    print("\n== 19. Tek sirali ust bar ==")

    from PyQt6.QtWidgets import QApplication, QToolButton
    from app.ui.main_window import MainWindow, app_settings
    from app.ui.theme import active_theme, apply_theme, stylesheet, ui_font

    # OLCUM KALICI AYARLARDAN ETKILENMEMELI.
    #
    # Serit genisligi TEMAYA baglidir ve MainWindow.__init__ icindeki
    # _restore_state(), KAYITLI temayi uygular. Onceki bir kosumdan kalan
    # "light" degeri seridi farkli metriklerle kuruyor ve tasma olcumu
    # kosumdan kosuma degisiyordu: bir kez ['Choice','Terminate'], bir kez
    # ['History'], bir kez ['Composite'] dusuyordu. Tek basina kosunca ayni
    # olcum 5/5 kararliydi -- yani kusur seritte degil, testin kalintili
    # ayarlardan etkilenmesindeydi.
    app_settings().clear()
    apply_theme("dark")

    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setFont(ui_font(9))
    app.setStyleSheet(stylesheet())
    win = MainWindow()
    win.resize(1600, 950)
    win.show()
    app.processEvents()
    try:
        check(active_theme() == "dark",
              "olcum sabit temada yapiliyor", "tema = %s" % active_theme())
        check(not win.menuBar().isVisible(),
              "ayri menu cubugu serit olarak gorunmuyor")
        check([b.text() for b in win.menu_buttons]
              == [m.title() for m in win._menus],
              "menuler ust barda acilir dugme olarak duruyor",
              str([b.text() for b in win.menu_buttons]))
        check(not hasattr(win, "tb_state") and not hasattr(win, "tb_class"),
              "ikinci ve ucuncu arac seritleri kaldirildi")

        # SERIDIN GENISLIK BUTCESI.
        #
        # Mutlak piksel esigi kullanilamaz: ekransiz ("offscreen") kosumda
        # gercek yazi tipleri yuklu olmadigi icin ayni serit 1547 px
        # isterken gercek Windows oturumunda 1299 px yetiyor. Sabit bir
        # sayi yazmak testi platforma baglardi. Olculen dogru sudur:
        # seridin ISTEDIGI genislikte hicbir arac tasma dugmesine dusmez.
        # SERIDIN GENISLIK BUTCESI.
        #
        # NEDEN CIZILMIS DURUMA DEGIL, BOYUT TALEBINE BAKILIYOR:
        # Ilk yazim pencereyi "sizeHint+60"a buyutup dugmelerin
        # isVisible() / geometry() degerlerine bakiyordu. Bu olcum ekransiz
        # kosumda KARARSIZDI -- ayni kod arka arkaya kosuldugunda bir kez
        # ['Choice','Terminate'], bir kez ['History'], bir kez
        # ['Transition'], bir kez ['Select','History'] "tasti" diyordu.
        # Yakalanan kanit: serit 1867 px almisken (gerekli 1547) ilk dugme
        # 'Select' bile tasmis gorunuyordu; yani yer sorunu yoktu, serit
        # cocuklari o olay turunda henuz YERLESMEMISTI. Ekransiz surucude
        # yerlesim gercek bir boyama cevrimine bagli ve processEvents ile
        # zorlanamiyor.
        #
        # Zamanlamadan BAGIMSIZ ve ayni iddiayi tasiyan olcut: her aracin
        # seritte gercek bir dugmesi var mi, ve araclarin toplam talebi
        # seridin talebinin icinde mi. Tasma dugmesine dusen bir arac bu
        # toplamda YER ALMAZ.
        # (a) Butun araclar TEK seridin icinde mi? Bu bir VERI sorusudur;
        #     widget yerlesimine hic bakmaz, dolayisiyla kararlidir.
        #     `widgetForAction` yeniden yerlesim sirasinda QToolButton
        #     DONDURMEYEBILIR (komsu vakada belgelenmis: elde QScrollBar
        #     kalabiliyor), o yuzden dugme varligiyla olcmek kararsizdi.
        serit_eylemleri = set(win.top_bar.actions())
        disarida = [a.text() for a in win._state_tool_actions
                    if a not in serit_eylemleri]
        check(not disarida, "butun araclar TEK ust seridin icinde",
              "disarida kalan: %s" % disarida)

        # (b) Genislik butcesi: araclarin toplam talebi seridin talebinin
        #     icinde kalmali. Olculebilen dugmelerle hesaplanir; olculemeyen
        #     olursa (yukaridaki not) atlanir -- (a) zaten kaybi yakalar.
        gerekli = win.top_bar.sizeHint().width()
        olculen = [win.top_bar.widgetForAction(a).sizeHint().width()
                   for a in win._state_tool_actions
                   if isinstance(win.top_bar.widgetForAction(a), QToolButton)]
        check(olculen and sum(olculen) <= gerekli,
              "araclarin tamami seridin genislik butcesine siginiyor",
              "araclar %d px (%d dugme), serit %d px"
              % (sum(olculen), len(olculen), gerekli))

        # Araclarin ADLARIYLA durmasi seridi ~1.4 kat genisletiyordu ve
        # yarisi tasma dugmesinin arkasinda kaliyordu; simge kipi bunu
        # olculebilir sekilde daraltir.
        from PyQt6.QtCore import Qt as _Qt2
        def arac_dugmesi(act):
            """Eylemin ARAC DUGMESI (baska bir widget gelirse None).

            `widgetForAction` her zaman bir QToolButton dondurmez: serit
            yeniden yerlesirken elde baska bir cocuk (olculdu: QScrollBar)
            kalabiliyor ve uzerinde `toolButtonStyle()` cagirmak testi
            AttributeError ile dusuruyordu.
            """
            d = win.top_bar.widgetForAction(act)
            return d if isinstance(d, QToolButton) else None

        genis = dar = 0
        for act in win._state_tool_actions:
            d = arac_dugmesi(act)
            if d is None:
                continue
            dar += d.sizeHint().width()
            eski = d.toolButtonStyle()
            d.setToolButtonStyle(_Qt2.ToolButtonStyle.ToolButtonTextBesideIcon)
            genis += d.sizeHint().width()
            d.setToolButtonStyle(eski)
        check(genis > 0, "arac dugmeleri olculebildi")
        check(dar < genis * 0.75,
              "simge kipi arac seridini belirgin daraltiyor",
              "simge %d px vs adli %d px" % (dar, genis))

        # Dar pencerede hicbir sey ERISILEMEZ olmamali: QToolBar kendi
        # tasma dugmesini gostermeli.
        win.resize(1100, 900)
        for _ in range(3):
            app.processEvents()
        ext = [c for c in win.top_bar.children()
               if c.objectName() == "qt_toolbar_ext_button"]
        check(bool(ext) and ext[0].isVisible(),
              "dar pencerede tasma dugmesi devreye giriyor")

        # Kipler AYRISMALI: eylem gorunurlugu kullanilmazsa QToolBar kendi
        # yerlesiminde dugmeyi geri gosteriyor ve iki "Select" beliriyordu.
        win.resize(1600, 950)
        for _ in range(3):
            app.processEvents()
        gorunur = lambda liste: [a.text() for a in liste if a.isVisible()]
        check(not gorunur(win._class_tool_actions),
              "durum kipinde sinif araclari gizli",
              str(gorunur(win._class_tool_actions)))
        win.mode_tabs.setCurrentIndex(1)
        for _ in range(3):
            app.processEvents()
        check(not gorunur(win._state_tool_actions),
              "sinif kipinde durum araclari gizli",
              str(gorunur(win._state_tool_actions)))
        win.mode_tabs.setCurrentIndex(0)
        app.processEvents()

        # SECILI ARAC yalnizca BASILI gorunumuyle belli olur.
        #
        # Bir ara secili arac adiyla da yaziliyordu; serit her arac
        # degisiminde genisleyip daraliyor, dugmeler kayiyor ve tek bir
        # "Select" yazisi araclarin arasinda yamalik gibi duruyordu.
        # Hangi aracin secili oldugu ipucunda ve Tool menusunde yazili.
        from PyQt6.QtCore import Qt as _Qt
        from app.ui.canvas import Tool
        win.set_tool(Tool.TRANSITION)
        for _ in range(3):
            app.processEvents()
        secili = [a for a in win._state_tool_actions if a.isChecked()]
        check(len(secili) == 1 and secili[0].text() == "Transition",
              "secili arac tektir ve dogru araçtir")
        dugme = win.top_bar.widgetForAction(secili[0])
        check(isinstance(dugme, QToolButton)
              and dugme.toolButtonStyle()
              == _Qt.ToolButtonStyle.ToolButtonIconOnly,
              "secili arac da YALNIZCA simge (serit genislemiyor)")
        otekiler = [win.top_bar.widgetForAction(a)
                    for a in win._state_tool_actions if not a.isChecked()]
        check(all(d.toolButtonStyle()
                  == _Qt.ToolButtonStyle.ToolButtonIconOnly
                  for d in otekiler if isinstance(d, QToolButton)),
              "secili olmayan araclar yalnizca simge")
    finally:
        win.settings.clear()
        win.close()
        app.processEvents()


def test_wheel_does_not_edit() -> None:
    """Fare tekerlegi MODELI DEGISTIRMEMELI.

    Kullanicinin Problems panelinde gordugu
    "V034 - An initial transition cannot have an event." hatasinin kaynagi
    buydu: PROPERTIES paneli kaydirilirken imlec "Event" acilir kutusunun
    uzerinden gecince kutu sessizce ilk olaya atliyor, odak kaybinda da
    modele yaziliyordu. Tek centik olcum: '' -> 'BUTTON'.
    """
    print("\n== 19b. Tekerlek modeli degistirmiyor ==")

    from PyQt6.QtCore import QPoint, QPointF, Qt as _Qt
    from PyQt6.QtGui import QWheelEvent
    from PyQt6.QtWidgets import QApplication, QComboBox
    from app.core.validator import validate
    from app.ui.main_window import MainWindow
    from app.ui.theme import stylesheet

    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(stylesheet())
    win = MainWindow()
    win.resize(1500, 900)
    win.show()
    app.processEvents()
    try:
        sm = win.doc.machine
        baslangic = [t for t in sm.transitions.values()
                     if sm.states.get(t.source) is not None
                     and sm.states[t.source].kind == StateKind.INITIAL]
        check(bool(baslangic), "ornekte baslangic gecisi var")

        win.inspector.show_selection([baslangic[0].id])
        for _ in range(2):
            app.processEvents()

        kutular = win.inspector.findChildren(QComboBox)
        check(bool(kutular), "gecis formunda acilir kutu var")

        # UML: baslangic gecisi olay/koruma TASIYAMAZ -> alanlar kapali.
        check(not kutular[0].isEnabled(),
              "baslangic gecisinde Event alani KAPALI")

        tekerlek = QWheelEvent(QPointF(10, 10), QPointF(10, 10),
                               QPoint(0, 0), QPoint(0, -120),
                               _Qt.MouseButton.NoButton,
                               _Qt.KeyboardModifier.NoModifier,
                               _Qt.ScrollPhase.NoScrollPhase, False)
        for kutu in kutular:
            app.sendEvent(kutu, tekerlek)
        for _ in range(2):
            app.processEvents()

        check(all(not t.event for t in baslangic),
              "tekerlek baslangic gecisine olay YAZMADI",
              str([t.event for t in baslangic]))
        check(not [i for i in validate(sm) if i.code == "V034"],
              "V034 uretilmedi")

        # Geometri alanlari panelden cikti (kullanici: "x y genislik
        # yukseklik gibi sacma ozellikleri yazma").
        from PyQt6.QtWidgets import QFormLayout, QLabel
        win.inspector.show_selection([next(
            s.id for s in sm.states.values() if s.kind.is_real_state)])
        for _ in range(2):
            app.processEvents()
        etiketler = []
        for form in win.inspector.findChildren(QFormLayout):
            for satir in range(form.rowCount()):
                oge = form.itemAt(satir, QFormLayout.ItemRole.LabelRole)
                if oge is None:
                    continue
                w = oge.widget()
                # TUR DENETIMI SART. PyQt, geri donusturulmus bellek icin
                # yanlis turde bir sarmalayici verebiliyor (olculdu: bir
                # form etiketi yerine QMenu geldi) ve `.text()` cagrisi
                # testi AttributeError ile dusuruyordu.
                if isinstance(w, QLabel):
                    etiketler.append(w.text())
        check(not ({"X", "Y", "Width", "Height"} & set(etiketler)),
              "geometri alanlari PROPERTIES'te yok", str(etiketler))
    finally:
        win.doc.mark_clean()
        win.class_doc.mark_clean()
        win.settings.clear()
        win.close()
        app.processEvents()


def test_left_drag_bends_transition() -> None:
    """Ok SOL tusla tutulup surukleneblmeli (kullanici istegi)."""
    print("\n== 19c. Ok sol tusla bukuluyor ==")

    from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt as _Qt
    from PyQt6.QtGui import QMouseEvent
    from PyQt6.QtWidgets import QApplication
    from app.ui.canvas import Tool
    from app.ui.main_window import MainWindow
    from app.ui.theme import stylesheet

    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(stylesheet())
    win = MainWindow()
    win.resize(1600, 950)
    win.show()
    # Bukulecek bir ok GEREKIYOR; uygulama artik acilista demo yuklemiyor.
    win.load_demo()
    for _ in range(10):
        app.processEvents()
    try:
        tuval = win.canvas
        tuval.set_tool(Tool.SELECT)

        # SINIR DIKDORTGENININ MERKEZI OKUN USTUNDE OLMAYABILIR: kisa ya
        # da bukulmus bir okta merkez bosluga duser ve jest hicbir seyi
        # tutmaz. Isabet ettigi dogrulanmis bir gecis secilir.
        tid = None
        for aday, oge in tuval.tran_items.items():
            nokta = oge.mapToScene(oge.boundingRect().center())
            if tuval._transition_at(nokta) is not None:
                tid = aday
                merkez = tuval.mapFromScene(nokta)
                break
        check(tid is not None, "jest icin isabet eden bir gecis bulundu")
        if tid is None:
            return

        def fare(tur, nokta, dugme):
            return QMouseEvent(tur, QPointF(nokta), QPointF(nokta),
                               dugme, dugme, _Qt.KeyboardModifier.NoModifier)

        def jest(dx, dy, dugme=_Qt.MouseButton.LeftButton):
            tuval.mousePressEvent(
                fare(QEvent.Type.MouseButtonPress, merkez, dugme))
            for bolum in (0.5, 1.0):
                ara = QPoint(merkez.x() + int(dx * bolum),
                             merkez.y() + int(dy * bolum))
                tuval.mouseMoveEvent(fare(QEvent.Type.MouseMove, ara, dugme))
            son = QPoint(merkez.x() + dx, merkez.y() + dy)
            tuval.mouseReleaseEvent(
                fare(QEvent.Type.MouseButtonRelease, son, dugme))
            for _ in range(2):
                app.processEvents()

        gecis = win.doc.machine.transitions[tid]
        gecis.waypoints = []

        # 1) Kimildamadan sol tik SECIMDIR; oku bukmemeli.
        jest(0, 0)
        check(not gecis.waypoints,
              "hareketsiz sol tik oku bukmuyor (secim tiki)",
              str(gecis.waypoints))

        # 2) Sol tusla suruklemek oku BUKMELI.
        jest(70, 55)
        check(bool(gecis.waypoints),
              "sol tusla surukleyince ok bukuluyor",
              str(gecis.waypoints))

        # 3) Sag tikla hareketsiz birakmak oku DUZLESTIRIR (eski davranis).
        #
        # Ok bukuldugu icin SEKLI degisti: 2. adimdan kalan nokta artik
        # okun uzerinde degil. Isabet noktasi yeniden hesaplanmazsa sag
        # tik bosluga duser ve duzlestirme hic denenmemis olur.
        oge = tuval.tran_items[tid]
        # Isabet noktasi OKUN UZERINDEN alinir. Sinirlayici dikdortgenin
        # merkezi bukulmus bir okta cizginin disina duser; eskiden
        # tesaduefen isabet ediyordu, kutular icerige gore buyuyunce
        # geometri degisti ve bosluga dusmeye basladi.
        merkez = tuval.mapFromScene(
            oge.mapToScene(oge._path.pointAtPercent(0.5)))
        check(tuval._transition_at(tuval.mapToScene(merkez)) is not None,
              "bukulmus okun yeni isabet noktasi bulundu")
        jest(0, 0, _Qt.MouseButton.RightButton)
        check(not gecis.waypoints,
              "sag tik oku duzlestiriyor", str(gecis.waypoints))
    finally:
        win.doc.mark_clean()
        win.class_doc.mark_clean()
        win.settings.clear()
        win.close()
        app.processEvents()


def test_no_employer_branding() -> None:
    """Kisisel proje: kurum adi arayuzde gecmemeli."""
    print("\n== 19d. Kurum adi yok ==")

    import app.ui.main_window as MW
    kaynak = open(MW.__file__, encoding="utf-8").read()
    check("Profen" not in kaynak,
          "main_window kaynaginda kurum adi yok")
    check("Kubilay" in kaynak, "gelistirici adi duruyor")

    # TESLIM EDILEN IKILI DOSYA da denetlenir.
    #
    # YASANAN HATA: kaynaktaki kurum adi temizlenmisti ama EXE'ye gomulen
    # Windows surum bilgisi atlanmisti. Gezgin'de "Ozellikler > Ayrintilar"
    # sekmesi hala kurum adini gosteriyordu -- musteriye giden dosyanin
    # uzerinde.
    kok = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    surum_yolu = os.path.join(kok, "version_info.txt")
    check(os.path.exists(surum_yolu), "surum bilgisi dosyasi var")
    if os.path.exists(surum_yolu):
        surum = open(surum_yolu, encoding="utf-8").read()
        check("Profen" not in surum,
              "EXE surum bilgisinde kurum adi yok")
        check("Kubilay" in surum, "EXE surum bilgisi gelistirici adini yaziyor")
        # Bu metinler Gezgin'de GORUNUR; arayuz gibi ingilizce olmali.
        govde = chr(10).join(l for l in surum.splitlines()
                             if not l.lstrip().startswith("#"))
        check(not re.search(r"[çğıöşüÇĞİÖŞÜ]", govde),
              "EXE surum bilgisinde Turkce karakter yok")
        for tr in ("uretir", "lisanslanmistir", "diyagramlarindan"):
            check(tr not in govde,
                  "EXE surum bilgisi ingilizce ('%s' yok)" % tr)
        # Surum numarasi uretecin yazdigi ile AYNI olmali.
        from app.codegen.c_generator import TOOL_VERSION
        check("'%s.0'" % TOOL_VERSION in surum or
              "'%s'" % TOOL_VERSION in surum,
              "EXE surumu TOOL_VERSION ile ayni (%s)" % TOOL_VERSION, surum[:0])


# --------------------------------------------------------------------------- #
#  25. Satir sonu isareti                                                      #
# --------------------------------------------------------------------------- #

def test_line_break_marker() -> None:
    """Davranis ve etiket metinleri alt satira inebiliyor mu.

    YASANAN HATA: durum kutusu entry / exit / do metinlerinin BUTUN
    bosluklarini tek bosluga indirip her davranisi tek satira sikistiriyordu.
    Iki ifadeli bir exit eylemi kutuda
    "exit / fault_signal(false); check_temp_sensor(in..." diye kirpiliyor,
    kullanici yazdiginin tamamini goremiyordu.
    """
    print("\n== 25. Satir sonu isareti ==")

    from app.core.text_layout import (LINE_BREAK_MARKER, expand_breaks,
                                      flatten, split_lines)

    BS = chr(92)
    M = BS + "n"
    NL = chr(10)

    check(LINE_BREAK_MARKER == M, "isaret ters bolu + n")

    # -- 1) Ayirma kurallari ------------------------------------------------- #
    check(split_lines("foo();" + M + "bar();") == ["foo();", "bar();"],
          "isaret satiri boler")
    check(split_lines("a();" + NL + "b();") == ["a();", "b();"],
          "gercek Enter da boler")

    # C dizgesi icindeki isaret satir BOLMEMELI; yoksa
    # printf("Fault\n") yazan herkes etiketin ortadan bolundugunu gorurdu.
    dizge = 'printf("Fault' + M + '");'
    check(split_lines(dizge) == [dizge], "dizge icindeki isaret korunur",
          split_lines(dizge))
    karakter = "c = '" + M + "';" + M + "d();"
    check(split_lines(karakter) == ["c = '" + M + "';", "d();"],
          "karakter sabiti icindeki isaret korunur", split_lines(karakter))

    check(split_lines("a" + M + M + "b") == ["a", "b"],
          "ard arda isaret bos satir uretmez")
    check(split_lines('s = "yarim') == ['s = "yarim'],
          "kapanmamis tirnak cokmez")

    # -- 2) Kod uretimi ------------------------------------------------------ #
    # Isaret C'de dizge disinda gecerli degildir. Oldugu gibi kalirsa
    # uretilen kod derlenmez.
    check(expand_breaks("foo();" + M + "  bar();") == "foo();" + NL + "  bar();",
          "expand_breaks girintiyi korur")
    check(expand_breaks('printf("a' + M + 'b");') == 'printf("a' + M + 'b");',
          "expand_breaks dizgeye dokunmaz")

    sm = StateMachine(name="Brk", prefix="brk")
    ini = State(id="i", name="Init", kind=StateKind.INITIAL, y=0)
    a = State(id="a", name="Alpha", kind=StateKind.SIMPLE, y=100)
    a.entry = "step_one();" + M + "step_two();"
    sm.add_state(ini)
    sm.add_state(a)
    sm.add_transition(Transition(source="i", target="a"))

    files = dict(generate_c(sm))
    govde = "".join(files.values())
    check("step_one();" in govde and "step_two();" in govde,
          "iki ifade de uretilen koda giriyor")
    check(M not in govde, "uretilen kodda ham isaret KALMAZ",
          [ln for ln in govde.splitlines() if M in ln][:3])
    check(govde.count("step_one();" + NL) >= 1
          or ("step_one();" in govde and "step_two();" in govde
              and "step_one(); step_two();" not in govde),
          "isaret gercek satir sonuna cevrildi")

    # -- 3) Gecis etiketi ---------------------------------------------------- #
    t = Transition(source="i", target="a", event="EV",
                   action="one();" + M + "two();")
    # Etiket, eylemin EN SONUNDAKI noktali virgulu dusurur -- bu eski ve
    # bilincli davranis (UML etiketinde ifade sonlandirici gosterilmez).
    check(t.label_lines() == ["EV / one();", "two()"],
          "label_lines() etiketi boler", t.label_lines())
    # PlantUML ciktisi ve agac satiri satir sonu TASIYAMAZ.
    check(NL not in t.label() and M not in t.label(),
          "label() tek satir kalir", repr(t.label()))
    check(flatten("x();" + M + "y();") == "x(); y();", "flatten tek satir")


# --------------------------------------------------------------------------- #
#  26. Kod paneli suruklenerek kapanir                                          #
# --------------------------------------------------------------------------- #

def test_code_panel_collapses() -> None:
    """Sag ayirici sonuna kadar cekilince kod paneli kapanmali.

    YASANAN HATA: bozuk yerlesimden kurtarma icin konulan
    setChildrenCollapsible(False) UC bolmeye birden uygulanmisti. Oysa
    korunmasi gereken tek bolme diyagram bolmesiydi. Kod paneli de
    kapanamaz hale geldi: kullanici ayiriciyi sonuna kadar cekiyor, panel
    1 piksellik bir serit olarak yerinde kaliyordu.

    Bu bolum SETSIZES ILE DEGIL, tutamagi gercekten surukleyerek sinar --
    setSizes() kapanabilirlik yolunu hic calistirmaz ve kusuru goremezdi.
    """
    print("\n== 26. Kod paneli suruklenerek kapanir ==")

    from PyQt6.QtCore import QPoint, Qt as QtC
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication
    from app.ui.main_window import MainWindow, app_settings
    from app.ui.theme import stylesheet

    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(stylesheet())

    ayar = app_settings()
    ayar.clear()
    ayar.sync()

    def surukle(splitter, index, dx):
        """Tutamagi dx kadar tasir -- kullanicinin yaptigi seyin ta kendisi."""
        h = splitter.handle(index)
        QTest.mousePress(h, QtC.MouseButton.LeftButton, pos=QPoint(2, 20))
        QTest.mouseMove(h, QPoint(2 + dx, 20))
        QTest.mouseRelease(h, QtC.MouseButton.LeftButton, pos=QPoint(2 + dx, 20))
        app.processEvents()

    win = MainWindow()
    win.settings = ayar
    win.resize(1900, 1000)
    win.show()
    app.processEvents()

    sp = win.main_splitter
    i_code = sp.indexOf(win.code_panel)
    i_tabs = sp.indexOf(win.mode_tabs)

    check(sp.isCollapsible(i_code), "kod paneli kapatilabilir isaretli")
    check(not sp.isCollapsible(i_tabs),
          "diyagram bolmesi HALA korunuyor (asil kusur geri gelmesin)")
    check(win.code_panel_open(), "baslangicta acik")

    # -- Tutamagi sonuna kadar saga surukle ---------------------------------- #
    surukle(sp, i_code, 3000)
    check(sp.sizes()[i_code] == 0,
          "surukleyince panel tamamen kapaniyor (1 px serit kalmiyor)",
          sp.sizes())
    check(not win.code_panel_open(), "code_panel_open() kapali diyor")
    check(not win.a_code_panel.isChecked(),
          "menu isareti de kapandi (dugme bozuk gorunmesin)")

    # -- F9 geri getirmeli --------------------------------------------------- #
    win.a_code_panel.setChecked(True)
    win.toggle_code_panel(True)
    app.processEvents()
    check(win.code_panel_open(), "F9 paneli geri getiriyor", sp.sizes())

    # -- Diyagram bolmesi suruklemeyle YOK EDILEMEZ -------------------------- #
    # Asil kusur buydu; korumasi kalkmis olmamali.
    surukle(sp, i_tabs, -3000)
    check(sp.sizes()[i_tabs] >= win.mode_tabs.minimumWidth(),
          "diyagram bolmesi asgari genisligin altina inmiyor", sp.sizes())

    # -- Kapali durum acilisa TASINMALI -------------------------------------- #
    surukle(sp, i_code, 3000)
    ayar.setValue("splitter", sp.sizes())
    ayar.setValue("code_panel_open", win.code_panel_open())
    ayar.sync()

    win2 = MainWindow()
    win2.settings = ayar
    win2._restore_state()
    win2.resize(1900, 1000)
    win2.show()
    app.processEvents()
    check(not win2.code_panel_open(),
          "kapali panel acilista kapali kaliyor", win2.main_splitter.sizes())
    check(not win2.a_code_panel.isChecked(), "menu isareti de kapali geliyor")

    # -- Yerlesimi sifirla KURTARMA yolu olmayi surdurmeli -------------------- #
    win2.reset_layout()
    app.processEvents()
    check(win2.code_panel_open(),
          "Reset Layout paneli geri getiriyor", win2.main_splitter.sizes())

    # -- Bozuk kayit kurtarmasi BOZULMADI ------------------------------------ #
    ayar.clear()
    ayar.setValue("splitter", ["230", "0", "1364"])   # diyagram bolmesi 0
    ayar.sync()
    win3 = MainWindow()
    win3.settings = ayar
    win3._restore_state()
    win3.resize(1900, 1000)
    win3.show()
    app.processEvents()
    paylar3 = win3.main_splitter.sizes()
    oran = paylar3[win3.main_splitter.indexOf(win3.mode_tabs)] / float(sum(paylar3) or 1)
    check(oran >= win3.MIN_DIAGRAM_SHARE,
          "bozuk kayit kurtarmasi calismaya devam ediyor", paylar3)

    for w in (win, win2, win3):
        w.close()
    ayar.clear()
    ayar.sync()


# --------------------------------------------------------------------------- #
#  27. Uretilen kodda KISALTMA ve tek harfli ad yok                             #
# --------------------------------------------------------------------------- #

#: Uretecin YAZDIGI, artik kabul edilmeyen kisa adlar.
#:
#: `ctx` LISTEDE DEGIL: kullanicinin model icinde yazdigi eylem govdeleri
#: (`ctx->blink_count = 0U;`) bu ada dayanir, degistirmek kullanicinin
#: kodunu kirardi. `i` / `a` / `b` de listede degil -- dongu sayaci ve
#: "iki durumun ortak atasi" matematiksel parametreleri.
_KISA_ADLAR = ("src", "dst", "evt", "cur", "trans", "tran", "g_sm",
               "s", "t", "k", "n", "d", "e", "da", "db")


def test_no_abbreviations_in_code() -> None:
    """Uretilen C/C++ kisaltilmis ya da tek harfli TANIMLAYICI icermez.

    YASANAN HATA: kullanici `blinky_trans` adini gorunce "teknik terimleri
    kisaltmadan yaz" dedi. Tip adi duzeltildi ama GOVDELER kisa kaldi:
    `t->src`, `t->dst`, `t->evt`, `uint8_t s`, `uint8_t n`, `g_sm`. Bu
    bolum METNE bakar, cunku kural ancak CIKTIDA saglanmissa saglanmistir.

    Yorum METINLERI disarida tutulur; ingilizce cumlelerdeki "a", "to",
    "is" gibi sozcukler tanimlayici degildir.
    """
    print("\n== 27. Uretilen kodda kisaltma yok ==")

    from app.core.samples import demo_class_model, demo_machine

    sm = demo_machine()
    uretilen = dict(generate_c(sm))
    uretilen.update(generate_cpp(sm))
    cm = demo_class_model()
    uretilen.update(generate_class_c(cm))
    uretilen.update(generate_class_cpp(cm))

    desen = re.compile(r"(?<![\w.>])(%s)(?![\w])"
                       % "|".join(sorted(_KISA_ADLAR, key=len, reverse=True)))
    for ad in sorted(uretilen):
        bulunan = []
        for no, satir in _kod_satirlari(uretilen[ad]):
            for m in desen.finditer(satir):
                bulunan.append("satir %d: %s (%s)"
                               % (no, satir.strip()[:70], m.group(1)))
        check(not bulunan, "%s: kisaltilmis tanimlayici yok" % ad,
              "\n".join(bulunan[:6]))

    # -- Tip govdesi ile ADI arasinda BOS SATIR ------------------------------ #
    #
    # Kullanici: "struct'in son elemanina gelince bir bos satir birak,
    # oyle ismini yaz." Kural tek bir gecise (blank_before_close) bagli;
    # once `blinky_transition_t` ve `drawable_t` atlanmisti.
    kapanis = re.compile(r"^\s*\}\s*[A-Za-z_]\w*\s*;")
    sayi = 0
    for ad in sorted(uretilen):
        L = uretilen[ad].splitlines()
        for i, satir in enumerate(L):
            if not kapanis.match(satir):
                continue
            sayi += 1
            onceki = L[i - 1] if i else ""
            check(not onceki.strip(),
                  "%s:%d `%s` oncesinde bos satir var"
                  % (ad, i + 1, satir.strip()), onceki)
    check(sayi >= 4, "adli kapanis satirlari gercekten var", sayi)


# --------------------------------------------------------------------------- #
#  28. Git sekmesi panelleri kapatir, cikista geri verir                        #
# --------------------------------------------------------------------------- #

def test_git_tab_hides_panels() -> None:
    """Git sekmesine gecince kod ve simulasyon panelleri OTOMATIK kapanir.

    Kullanici: "git kismina gelince kod ve simulasyon paneli acikssa
    otomatik kapat." Fark ekrani genis olmak zorunda; iki panel birden
    acikken git govdesine 400 pikselden az yer kaliyordu.

    ONEMLI: durum yakalamasi `a_sim_panel.isChecked()` ile yapilir,
    `sim_panel.isVisible()` ile DEGIL -- `_sync_mode` sekme degisiminden
    SONRA kostugu icin gorunurluk o anda zaten False okunuyor ve panel
    git'ten cikista bir daha asla geri gelmiyordu.
    """
    print("\n== 28. Git sekmesi panelleri kapatir ==")

    from PyQt6.QtWidgets import QApplication
    from app.ui.main_window import MainWindow, app_settings

    app = QApplication.instance() or QApplication([])
    ayar = app_settings()
    ayar.clear()
    ayar.sync()

    win = MainWindow()
    win.settings = ayar
    win.resize(1900, 1000)
    win.show()
    app.processEvents()

    git_index = win.mode_tabs.indexOf(win.git_panel)
    fsm_index = win.mode_tabs.indexOf(win._state_stack)
    check(git_index >= 0 and fsm_index >= 0, "git ve fsm sekmeleri bulundu")

    for kod_acik in (False, True):
        for sim_acik in (False, True):
            win.mode_tabs.setCurrentIndex(fsm_index)
            app.processEvents()
            win.a_code_panel.setChecked(kod_acik)
            win.toggle_code_panel(kod_acik)
            win.a_sim_panel.setChecked(sim_acik)
            win.toggle_sim_panel(sim_acik)
            app.processEvents()

            win.mode_tabs.setCurrentIndex(git_index)
            app.processEvents()
            check(not win.code_panel_open() and not win.sim_panel.isVisible(),
                  "git'te iki panel de kapali (kod=%s sim=%s)"
                  % (kod_acik, sim_acik),
                  (win.code_panel_open(), win.sim_panel.isVisible()))

            win.mode_tabs.setCurrentIndex(fsm_index)
            app.processEvents()
            check(win.code_panel_open() == kod_acik,
                  "git'ten cikista kod paneli eski haline dondu (%s)" % kod_acik,
                  win.code_panel_open())
            check(win.sim_panel.isVisible() == sim_acik,
                  "git'ten cikista sim paneli eski haline dondu (%s)" % sim_acik,
                  win.sim_panel.isVisible())

    win.doc.mark_clean()
    win.class_doc.mark_clean()
    win.close()
    ayar.clear()
    ayar.sync()


# --------------------------------------------------------------------------- #
#  29. Calisma alaninda dosya secince GORSEL fark                               #
# --------------------------------------------------------------------------- #

def test_visual_workspace_diff() -> None:
    """Agacta model secilince eklenen/silinen/degisen ogeler TUVALDE isaretlenir.

    Kullanici: "workspace'de hangi degisiklikler olmussa klasoru/dosyayi
    sectigim zaman canli olarak eklenen cikarilan bloklari, transition'lari
    gosteren bir diff yapisi kur."

    Silinen ogeler artik modelde YOK; bu yuzden hayalet (GhostItem)
    olarak cizilirler -- yoksa "cikarilan blok" hic gorunmezdi.
    """
    print("\n== 29. Calisma alaninda gorsel fark ==")

    import tempfile

    from PyQt6.QtWidgets import QApplication
    from app.core.model import State, StateKind, Transition
    from app.core.samples import demo_machine
    from app.core.workspace import Workspace
    from app.ui.diagram_items import GhostItem
    from app.ui.main_window import MainWindow, app_settings
    import app.ui.workspace_tree as wt

    app = QApplication.instance() or QApplication([])
    ayar = app_settings()
    ayar.clear()
    ayar.sync()

    kok = tempfile.mkdtemp(prefix="usd_vdiff_")
    ws = Workspace.create(os.path.join(kok, "diff-demo"))
    model_yolu = os.path.join(ws.model_path, "blinky.usm")
    with open(model_yolu, "w", encoding="utf-8") as fh:
        fh.write(demo_machine().to_json())

    win = MainWindow()
    win.settings = ayar
    win.resize(1500, 900)
    win.show()
    win.canvas.auto_edit = False
    app.processEvents()
    win.apply_workspace(ws)
    win.open_model_path(model_yolu)
    for _ in range(20):
        app.processEvents()

    sm = win.doc.machine
    silinecek = next(s for s in sm.states.values() if s.name == "Fault")
    degisecek = next(s for s in sm.states.values() if s.name == "Off")

    yeni = State(name="Calibrating", kind=StateKind.SIMPLE, x=900.0, y=700.0)
    sm.add_state(yeni)
    sm.add_transition(Transition(source=degisecek.id, target=yeni.id,
                                 event="CALIBRATE"))
    degisecek.entry = "led_write(false); calibrate_reset();"
    for t in list(sm.transitions.values()):
        if t.source == silinecek.id or t.target == silinecek.id:
            sm.remove_transition(t.id)
    sm.remove_state(silinecek.id)
    win.doc.changed.emit()
    app.processEvents()

    hedef = next(it for it in win.ws_tree._all_items()
                 if it.data(0, wt.NODE_ROLE) == "model")
    win.ws_tree.setCurrentItem(hedef)
    for _ in range(20):
        app.processEvents()

    isaret = win.canvas._diff_marks
    check(len(isaret.get("added") or ()) >= 2,
          "eklenen durum + gecis isaretlendi", sorted(isaret.get("added") or ()))
    check(len(isaret.get("removed") or {}) >= 1,
          "silinen oge fark kaydinda", len(isaret.get("removed") or {}))
    check(len(isaret.get("changed") or ()) >= 1,
          "davranisi degisen durum isaretlendi", len(isaret.get("changed") or ()))

    hayalet = [i for i in win.canvas.scene().items()
               if isinstance(i, GhostItem)]
    check(hayalet, "SILINEN oge hayalet cerceve olarak ciziliyor")

    boyali = set()
    for oge in (list(win.canvas.state_items.values())
                + list(win.canvas.tran_items.values())):
        if getattr(oge, "diff_mark", ""):
            boyali.add(oge.diff_mark)
    check({"added", "changed"} <= boyali,
          "tuval ogeleri gercekten boyandi", sorted(boyali))
    check("2 added" in win.lbl_message.text()
          and "1 changed" in win.lbl_message.text(),
          "durum cubugu ozeti yaziyor", win.lbl_message.text())

    # -- Baska bir sekmeye/dosyaya gecince isaretler TEMIZLENIR -------------- #
    win.canvas.set_diff_marks({})
    app.processEvents()
    kalan = [i for i in win.canvas.scene().items() if isinstance(i, GhostItem)]
    check(not kalan, "fark kapatilinca hayaletler siliniyor", len(kalan))

    # -- SINIF diyagraminda da ayni yetenek olmali --------------------------- #
    #
    # YASANAN HATA: ClassCanvas'ta `set_diff_marks` HIC YOKTU. Ana pencere
    # onu cagirinca sinyalin icinde AttributeError olusuyordu; PyQt6
    # yakalanmamis istisnayi olumcul sayip SURECI OLDURUYOR (0xC0000409).
    # Yani calisma alaninda bir sinif modeline tiklamak, kaydedilmemis
    # calismayla birlikte uygulamayi kapatiyordu.
    from app.core.model_diff import element_status
    from app.core.samples import demo_class_model

    check(hasattr(win.class_canvas, "set_diff_marks"),
          "sinif tuvali de fark isaretlerini destekliyor")
    win.class_doc.replace(demo_class_model())
    app.processEvents()
    cm = win.class_doc.machine
    eski_metin = cm.to_json()
    silinen_sinif = next(c for c in cm.classes.values() if c.name == "Couch")
    for r in list(cm.relations.values()):
        if silinen_sinif.id in (r.source, r.target):
            del cm.relations[r.id]
    del cm.classes[silinen_sinif.id]
    from app.core.class_model import UmlClass
    yeni_sinif = UmlClass(name="Lamp", x=900.0, y=700.0)
    cm.add_class(yeni_sinif)
    win.class_doc.changed.emit()
    app.processEvents()

    sinif_isaret = element_status(eski_metin, cm.to_json())
    win.class_canvas.set_diff_marks(sinif_isaret)
    app.processEvents()
    check(yeni_sinif.id in (sinif_isaret.get("added") or set()),
          "eklenen sinif fark kaydinda")
    boyali_sinif = {i.diff_mark for i in win.class_canvas.class_items.values()
                    if i.diff_mark}
    check("added" in boyali_sinif, "eklenen sinif tuvalde boyandi",
          sorted(boyali_sinif))
    sinif_hayalet = [i for i in win.class_canvas.scene().items()
                     if isinstance(i, GhostItem)]
    check(sinif_hayalet, "silinen sinif hayalet olarak ciziliyor")
    win.class_canvas.set_diff_marks(None)
    app.processEvents()
    check(not [i for i in win.class_canvas.scene().items()
               if isinstance(i, GhostItem)],
          "sinif hayaletleri de temizleniyor")

    win.doc.mark_clean()
    win.class_doc.mark_clean()
    win.close()
    ayar.clear()
    ayar.sync()


# --------------------------------------------------------------------------- #
#  30. Kullaniciya gorunen her metin INGILIZCE                                  #
# --------------------------------------------------------------------------- #

#: Turkce'ye ozgu harfler. Kod yorumlari Turkce YAZILIR (proje kurali),
#: ama dizgi sabitleri kullaniciya gider; orada Turkce olmamali.
_TR_HARF = re.compile(r"[çğıöşü"
                      r"ÇĞİÖŞÜ]")

#: Aksansiz yazilmis Turkce sozcukler (ornegin "Calisma alani yazilamadi").
_TR_SOZCUK = re.compile(
    r"\b(icin|ile|olmali|olamaz|degil|gecis|gecisi|durumu|dosyasi"
    r"|calisma|alani|yazilamadi|okunamadi|silinemedi|desteklenir"
    r"|cozulemedi|indekslenemedi|baslangic|bilesik|zincirinde|derin"
    r"|adimdan|bilinmeyen|sayisi|mesaji|klasor|yazilamaz|disina"
    r"|kaydedilmemis|sozde|hedefi|dongu|varmiyor|aciliyor|bolgede"
    # ORNEK MODELIN metinleri bu listeye takilmiyordu: "LED sonuk;
    # dugmeyle calismaya baslar." tek bir aksanli harf icermez ve
    # yukaridaki sozcuklerin hicbiri gecmez. Notlar artik URETILEN
    # BASLIGA da geciyor, yani o metin musteriye giden C/C++ yorumu
    # oluyordu. Ornekte gecen sozcukler acikca eklendi.
    r"|sonuk|dugme|dugmeyle|calismaya|baslar|yanip|sonme|cevrim"
    r"|cevrimi|hata|hatasi|uste|beklenir|sayaci|artirir|denetleyicisi"
    r"|hiyerarsi|ornegi|olayi|yazar|kullanici|satir|uretilen)\b",
    re.IGNORECASE)

#: Yazarin kendi adi ve telif satiri -- ceviri konusu degil.
_IZINLI = ("Kubilay", "©")


def test_user_facing_text_is_english() -> None:
    """Hata iletileri ve arayuz metinleri Turkce KALMAZ.

    YASANAN HATA: arayuz ingilizceye cevrildi ama CEKIRDEKTEKI istisna
    metinleri atlandi. Kullanici 255 durumlu bir model kurdugunda
    "Durum sayisi 255; en fazla 254 desteklenir." diyen bir hata kutusu
    goruyordu -- ingilizce bir programin ortasinda.

    Ayrica URETILEN kodun yorumlari da ingilizce olmali; C++ ureteci
    ornek modelin ugramadigi uc dalda Turkce yorum yaziyordu.
    """
    print("\n== 30. Kullaniciya gorunen metin ingilizce ==")

    import io
    import tokenize

    kok = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    taranan = 0
    kotu = []
    for dizin, _alt, dosyalar in os.walk(os.path.join(kok, "app")):
        for ad in sorted(dosyalar):
            if not ad.endswith(".py"):
                continue
            yol = os.path.join(dizin, ad)
            with open(yol, encoding="utf-8") as fh:
                kaynak = fh.read()
            taranan += 1
            for tok in tokenize.generate_tokens(io.StringIO(kaynak).readline):
                if tok.type != tokenize.STRING:
                    continue
                metin = tok.string
                # Belge dizgileri (docstring) Turkce kalir.
                if metin.startswith(('"""', "'''", 'r"""', "r'''")):
                    continue
                if any(izin in metin for izin in _IZINLI):
                    continue
                if _TR_HARF.search(metin) or len(_TR_SOZCUK.findall(metin)) >= 2:
                    kotu.append("%s:%d  %s"
                                % (os.path.relpath(yol, kok), tok.start[0],
                                   metin.strip()[:70]))

    check(taranan > 20, "tarama gercekten dosyalari gezdi", taranan)
    check(not kotu, "kullaniciya gorunen dizgilerde Turkce yok",
          "\n".join(kotu[:8]))

    # -- TESLIM EDILEN ORNEK C/C++ DOSYALARI ---------------------------------- #
    #
    # `examples/` altindaki dosyalar URETILMEZ; uygulamayi yazan muhendisin
    # yerine konan ornek kodlardir ve musteriye AYNEN gider. Yorumlari ve
    # bastiklari iletiler Turkce kalmisti: ingilizce bir urunun icindeki tek
    # Turkce C dosyalari onlardi. Ayni dosyalar, urunun kendi dayattigi
    # bicime de uymalidir -- aksi halde arac "kivircik parantezi ac" diye
    # kod uretirken, yanindaki ornek K&R duruyor.
    ornek_kok = os.path.join(kok, "examples")
    ornek_kotu = []
    ornek_bicim = []
    ornek_sayi = 0
    for ad in sorted(os.listdir(ornek_kok)):
        if not ad.endswith((".c", ".cpp", ".h", ".hpp")):
            continue
        ornek_sayi += 1
        with open(os.path.join(ornek_kok, ad), encoding="utf-8") as fh:
            metin = fh.read()
        for no, satir in _kod_satirlari(metin):
            if _SONDA_SUSLU.search(satir):
                ornek_bicim.append("%s:%d  %s" % (ad, no, satir.strip()[:70]))
        for no, ham in enumerate(metin.splitlines(), start=1):
            if _TR_HARF.search(ham) or len(_TR_SOZCUK.findall(ham)) >= 2:
                ornek_kotu.append("%s:%d  %s" % (ad, no, ham.strip()[:70]))

    check(ornek_sayi >= 3, "ornek C/C++ dosyalari tarandi",
          "taranan = %d" % ornek_sayi)
    check(not ornek_kotu, "teslim edilen ornek dosyalarda Turkce yok",
          "\n".join(ornek_kotu[:8]))
    check(not ornek_bicim,
          "ornek dosyalarda da acilis parantezi kendi satirinda",
          "\n".join(ornek_bicim[:8]))

    # -- URETILEN kodun kendisi de ingilizce ---------------------------------- #
    from app.core.samples import demo_class_model, demo_machine

    sm = demo_machine()
    uretilen = dict(generate_c(sm))
    uretilen.update(generate_cpp(sm))
    cm = demo_class_model()
    uretilen.update(generate_class_c(cm))
    uretilen.update(generate_class_cpp(cm))
    for ad in sorted(uretilen):
        disi = [s.strip()[:70] for s in uretilen[ad].splitlines()
                if _TR_HARF.search(s)]
        check(not disi, "%s: uretilen kodda Turkce karakter yok" % ad,
              "\n".join(disi[:4]))


# --------------------------------------------------------------------------- #
#  31. PlantUML ciktisi TUVALDEKI cizimi izler                                  #
# --------------------------------------------------------------------------- #

#: Yon eki tasiyan PlantUML oku:  A -right-> B / A o-down- B / A .up.|> B
_PUML_OK = re.compile(
    r"^(?P<src>\[\*\]|\[H\*?\]|\"[^\"]+\"|[A-Za-z_]\w*)"
    r"(?:\s+\"[^\"]*\")?\s+"
    r"(?P<ok>[-.*o|>]*(?P<yon>right|left|up|down)[-.|>]*)"
    r"\s+(?:\"[^\"]*\"\s+)?"
    r"(?P<dst>\[\*\]|\[H\*?\]|\"[^\"]+\"|[A-Za-z_]\w*)\s*(?::.*)?$")

#: Yonsuz ok -- artik olmamali (dikey ve bozuk cizimin sebebiydi).
_PUML_YONSUZ = re.compile(
    r"^\S.*\s(-->|<--|o--|\*--|--\|>|\.\.\|>|\.\.>|--)\s\S")


def _puml_govde(metin: str):
    """Yorum ve skinparam disi ANLAMLI satirlar."""
    icinde = False
    for ham in metin.splitlines():
        satir = ham.strip()
        if satir.startswith("skinparam") and satir.endswith("{"):
            icinde = True
            continue
        if icinde:
            if satir == "}":
                icinde = False
            continue
        if not satir or satir.startswith(("'", "skinparam", "hide ",
                                          "title ", "caption ", "@")):
            continue
        yield satir


def test_plantuml_matches_drawing() -> None:
    """PlantUML metni tuvaldeki YERLESIMI ve UML anlamini korur.

    YASANAN HATA: uretec her gecis icin duz `-->` yaziyordu. PlantUML
    yonsuz oklari yukaridan asagiya dizer; kullanici yatay cizdigi
    makineyi "dikey ve bozuk" olarak geri aldi. Ayrica tarih/junction/
    terminate sozde-durumlari duz kutu olarak, IC gecisler ise ok olarak
    ciziliyordu -- ikisi de uretilen C/C++ koduyla celisiyordu.

    PlantUML burada CALISTIRILAMAZ (jar yok); bu yuzden metin YAPISAL
    olarak denetlenir: dengeli bloklar, bildirilmis uclar, her okta yon.
    """
    print("\n== 31. PlantUML ciktisi cizimi izliyor ==")

    from app.codegen.class_plantuml_generator import generate_class_plantuml
    from app.codegen.plantuml_generator import generate_plantuml
    from app.core.model import (State, StateKind, StateMachine, Transition,
                                TransitionKind)
    from app.core.samples import demo_class_model, demo_machine

    sm = demo_machine()
    ciktilar = dict(generate_plantuml(sm))
    ciktilar.update(generate_class_plantuml(demo_class_model()))

    for ad, metin in sorted(ciktilar.items()):
        satirlar = metin.splitlines()
        check(satirlar[0] == "@startuml", "%s: @startuml ilk satir" % ad,
              satirlar[0])
        check("@enduml" in satirlar, "%s: @enduml var" % ad)
        check(metin.count("@startuml") == 1 and metin.count("@enduml") == 1,
              "%s: tek bir diyagram blogu" % ad)

        acik = sum(1 for s in satirlar if s.rstrip().endswith("{"))
        kapali = sum(1 for s in satirlar if s.strip() == "}")
        check(acik == kapali, "%s: suslu parantezler dengeli" % ad,
              (acik, kapali))

        yonsuz = [s for s in _puml_govde(metin) if _PUML_YONSUZ.match(s)]
        check(not yonsuz, "%s: her okta YON var (yatay/dikey korunur)" % ad,
              "\n".join(yonsuz[:5]))

        # Bosluklu adlar tirnaksiz kalmamali: PlantUML metni ayristiramaz.
        bozuk = [s for s in _puml_govde(metin)
                 if re.match(r"^(state|class|interface|abstract class)\s+"
                             r"[A-Za-z_]\w*\s+\w", s)
                 and "<<" not in s and " as " not in s]
        check(not bozuk, "%s: bosluklu adlar tirnaklanmis" % ad,
              "\n".join(bozuk[:4]))

    # -- Durum diyagramina ozel kurallar ------------------------------------- #
    puml = ciktilar["blinky.puml"]

    # `left to right direction` YAZILMAMALI.
    #
    # YASANAN HATA: genis modellerde bu yonerge yaziliyordu. PlantUML onu
    # GraphViz'e `rankdir=LR` diye gecirir; `-right->` oku ise "ayni rank"
    # demektir. Dikey akista ayni rank YAN YANA, yatay akista ALT ALTA
    # dizilir. Sonucta yan yana cizilen LedOn/LedOff alt alta indi, sol
    # alttaki Fault sag uste cikti -- cizim 90 derece dondu.
    for ad, metin in sorted(ciktilar.items()):
        check("left to right direction" not in metin,
              "%s: kuresel yon yonergesi YOK (oklar zaten yon tasiyor)" % ad)

    # -- Rank kisitlari CELISKILI olmamali ----------------------------------- #
    #
    # `A -right-> B` ayni rank, `A -down-> B` bir alt rank demektir. Ayni
    # ranka konan iki dugum arasinda ayrica bir ust/alt kisiti olursa
    # GraphViz birini atar ve yerlesim bozulur. Ornekte tam boyle olmustu:
    # Off ile Running ayni rank, Running ile Check ayni rank, ama
    # Check'in bir ustu Off deniyordu.
    ayni_rank = {}

    def kok(dugum):
        while ayni_rank.get(dugum, dugum) != dugum:
            dugum = ayni_rank[dugum]
        return dugum

    def birlestir(a, b):
        ka, kb = kok(a), kok(b)
        if ka != kb:
            ayni_rank[ka] = kb

    dikey = []
    for s in _puml_govde(puml):
        m = _PUML_OK.match(s)
        if m is None:
            continue
        a, b, yon = m.group("src"), m.group("dst"), m.group("yon")
        if a == "[*]" or b == "[*]":
            continue          # basla/bitir dugumleri tekil degil
        ayni_rank.setdefault(a, a)
        ayni_rank.setdefault(b, b)
        if yon in ("right", "left"):
            birlestir(a, b)
        else:
            dikey.append((a, b, s))

    celiski = [satir for a, b, satir in dikey if kok(a) == kok(b)]
    check(not celiski,
          "ayni ranka konan dugumler arasinda ust/alt kisiti YOK",
          "\n".join(celiski[:4]))

    # IC gecis OK DEGIL, govde satiridir (uretilen kod da oyle davranir).
    ic = [t for t in sm.transitions.values()
          if t.kind is TransitionKind.INTERNAL]
    check(ic, "ornek modelde ic gecis var (sinamanin anlami olsun)")
    check("Running : FAULT" in puml,
          "ic gecis govde satiri olarak yaziliyor")
    check("Running -" not in puml.replace("Running -right-> [*]", "")
          .replace("Running -left-> Check", "")
          or "Running -right-> Running" not in puml,
          "ic gecis OK olarak cizilmiyor")

    # Bildirilmemis uc kalmamali.
    bildirilen = set()
    for s in _puml_govde(puml):
        m = re.match(r"^state\s+(?:\"[^\"]+\"\s+as\s+)?([A-Za-z_]\w*)", s)
        if m:
            bildirilen.add(m.group(1))
    ozel = {"[*]", "[H]", "[H*]"}
    eksik = []
    for s in _puml_govde(puml):
        m = _PUML_OK.match(s)
        if m is None:
            continue
        for uc in (m.group("src"), m.group("dst")):
            if uc not in ozel and uc not in bildirilen:
                eksik.append("%s  (%s)" % (uc, s))
    check(not eksik, "her ok ucu BILDIRILMIS bir durum", "\n".join(eksik[:5]))

    # -- Tarih / junction / terminate gosterimi ------------------------------- #
    ozel_sm = StateMachine(name="Special", prefix="sp")
    ana = State(id="s_ana", name="Ana", kind=StateKind.COMPOSITE,
                x=0, y=0, w=420, h=260)
    icil = State(id="s_ic", name="Ic", kind=StateKind.SIMPLE,
                 x=40, y=80, parent="s_ana")
    ic_baslangic = State(id="s_ib", name="IcBas", kind=StateKind.INITIAL,
                         x=20, y=30, w=24, h=24, parent="s_ana")
    tarih = State(id="s_h", name="Tarih", kind=StateKind.SHALLOW_HISTORY,
                  x=260, y=40, w=32, h=32, parent="s_ana")
    derin = State(id="s_hd", name="Derin", kind=StateKind.DEEP_HISTORY,
                  x=320, y=40, w=32, h=32, parent="s_ana")
    kavsak = State(id="s_j", name="Kavsak", kind=StateKind.JUNCTION,
                   x=700, y=120, w=22, h=22)
    son = State(id="s_x", name="Bitir", kind=StateKind.TERMINATE,
                x=900, y=120, w=30, h=30)
    disari = State(id="s_d", name="Disari", kind=StateKind.SIMPLE,
                   x=900, y=300)
    bas = State(id="s_b", name="Bas", kind=StateKind.INITIAL,
                x=-80, y=0, w=24, h=24)
    for st in (bas, ana, ic_baslangic, icil, tarih, derin, kavsak, son,
               disari):
        ozel_sm.add_state(st)
    ozel_sm.add_transition(Transition(source="s_b", target="s_ana"))
    ozel_sm.add_transition(Transition(source="s_ib", target="s_ic"))
    ozel_sm.add_transition(Transition(source="s_ana", target="s_j",
                                      event="GO"))
    ozel_sm.add_transition(Transition(source="s_j", target="s_x"))
    ozel_sm.add_transition(Transition(source="s_ic", target="s_h",
                                      event="BACK"))
    ozel_sm.add_transition(Transition(source="s_d", target="s_hd",
                                      event="DEEP"))
    ozel = generate_plantuml(ozel_sm)["sp.puml"]

    check("[H]" in ozel, "sig tarih [H] olarak ciziliyor")
    check("[H*]" in ozel, "derin tarih [H*] olarak ciziliyor")
    check("<<choice>>" in ozel, "junction elmas olarak ciziliyor")
    check("<<junction>>" in ozel, "junction / choice ayrimi yazida korunuyor")
    check("<<end>>" in ozel, "terminate sonlandirma isaretiyle ciziliyor")
    check("state Tarih" not in ozel and "state Derin" not in ozel,
          "tarih sozde-durumlari DUZ KUTU olarak cizilmiyor")
    check(ozel.count("@startuml") == 1, "ozel model tek blok")

    # -- Bosluklu ad -------------------------------------------------------- #
    bosluklu = StateMachine(name="Gap", prefix="gap")
    bosluklu.add_state(State(id="a", name="Led On", kind=StateKind.SIMPLE,
                             x=0, y=0))
    bosluklu.add_state(State(id="b", name="Led Off", kind=StateKind.SIMPLE,
                             x=400, y=0))
    bosluklu.add_transition(Transition(source="a", target="b", event="TICK"))
    bmetin = generate_plantuml(bosluklu)["gap.puml"]
    check('state "Led On" as Led_On' in bmetin,
          "bosluklu durum adi tirnaklanip takma ad aliyor", bmetin)
    check("Led_On -right-> Led_Off" in bmetin,
          "takma adlar oklarda da kullaniliyor")


# --------------------------------------------------------------------------- #
#  31b. PlantUML'in GERCEK yerlesimi tuvaldeki siralamayi koruyor               #
# --------------------------------------------------------------------------- #

def test_plantuml_layout_matches_canvas() -> None:
    """Uretilen PlantUML, GraphViz'de TUVALDEKI sira ile yerlesiyor mu?

    Yapisal denetim (bolum 31) metnin dogru oldugunu gosterir ama RESMIN
    dogru ciktigini gostermez. PlantUML cizimi GraphViz'e yaptirir; dot
    kurulu oldugunda ayni kisitlar DOT'a cevrilip yerlesim GERCEKTEN
    hesaplanir ve modeldeki konumlarla karsilastirilir.

    Yon ipuclarinin karsiligi:
        A -right-> B : ayni rank, B sagda   ->  {rank=same} A -> B
        A -left->  B : ayni rank, B solda   ->  {rank=same} B -> A
        A -down->  B : B bir alt rank       ->  A -> B
        A -up->    B : B bir ust rank       ->  B -> A

    YASANAN HATA: `left to right direction` yaziliyordu; GraphViz'e
    `rankdir=LR` olarak gecince "ayni rank" YAN YANA olmaktan cikip ALT
    ALTA oldu ve butun cizim 90 derece dondu.
    """
    print("\n== 31b. PlantUML yerlesimi tuvalle ayni ==")

    import shutil
    import subprocess

    if shutil.which("dot") is None:
        skip("graphviz (dot) yok -- yerlesim dogrulamasi atlandi")
        return

    from app.codegen.plantuml_generator import _abs_center, generate_plantuml
    from app.core.samples import demo_machine

    sm = demo_machine()
    puml = generate_plantuml(sm)["blinky.puml"]

    ayni, kenar, kume_uye = [], [], {}
    aktif = None
    ozel = [0]

    def dugum(ad: str) -> str:
        if ad.startswith("["):
            ozel[0] += 1
            return "OZEL%d" % ozel[0]      # [*] tekil degil, her biri ayri
        return ad

    for ham in puml.splitlines():
        satir = ham.strip()
        m = re.match(r"^state\s+(\w+)\s*\{$", satir)
        if m:
            aktif = m.group(1)
            kume_uye.setdefault(aktif, [])
            continue
        if satir == "}":
            aktif = None
            continue
        m = re.match(r"^state\s+(\w+)", satir)
        if m and aktif:
            kume_uye[aktif].append(m.group(1))
            continue
        m = _PUML_OK.match(satir)
        if m is None:
            continue
        a, b = dugum(m.group("src")), dugum(m.group("dst"))
        yon = m.group("yon")
        if yon == "right":
            ayni.append((a, b)); kenar.append((a, b))
        elif yon == "left":
            ayni.append((a, b)); kenar.append((b, a))
        elif yon == "down":
            kenar.append((a, b))
        else:
            kenar.append((b, a))

    L = ["digraph G {", "  node [shape=box];"]
    # `left to right direction` -> GraphViz `rankdir=LR`. Cevirinin bu
    # ayrintiyi tasimasi SART: yonergeyi geri koyan biri olursa asagidaki
    # karsilastirma hatayi yakalasin, sessizce gecmesin.
    if "left to right direction" in puml:
        L.append("  rankdir=LR;")
    for kume, uyeler in kume_uye.items():
        L.append("  subgraph cluster_%s {" % kume)
        L += ["    %s;" % u for u in uyeler]
        L.append("  }")
    L += ["  { rank=same; %s; %s; }" % (a, b) for a, b in ayni]
    L += ["  %s -> %s;" % (a, b) for a, b in kenar]
    L.append("}")

    sonuc = subprocess.run(["dot", "-Tplain"], input="\n".join(L),
                           capture_output=True, text=True)
    check(sonuc.returncode == 0, "uretilen kisitlar GraphViz'de gecerli",
          sonuc.stderr[:300])
    if sonuc.returncode != 0:
        return

    konum = {}
    for satir in sonuc.stdout.splitlines():
        p = satir.split()
        if p and p[0] == "node":
            konum[p[1]] = (float(p[2]), float(p[3]))

    tuval = {}
    for st in sm.ordered_states():
        if st.kind in (StateKind.INITIAL, StateKind.FINAL):
            continue
        tuval[st.name] = _abs_center(sm, st)

    # KARDESLER karsilastirilir: bir bilesik durumun ICINDEKI kutuyla
    # DISARIDAKI bir kutunun x'ini kiyaslamak anlamsizdir.
    kardes = {}
    for st in sm.ordered_states():
        if st.kind in (StateKind.INITIAL, StateKind.FINAL):
            continue
        ust = sm.parent_of(st.id)
        kardes.setdefault(ust.id if ust else None, []).append(st.name)

    kiyas, bozuk = 0, []
    for grup in kardes.values():
        adlar = [a for a in grup if a in konum and a in tuval]
        for i in range(len(adlar)):
            for j in range(i + 1, len(adlar)):
                a, b = adlar[i], adlar[j]
                kiyas += 1
                if abs(tuval[a][0] - tuval[b][0]) > 80:
                    if (tuval[a][0] < tuval[b][0]) != (konum[a][0] < konum[b][0]):
                        bozuk.append("yatay: %s / %s" % (a, b))
                if abs(tuval[a][1] - tuval[b][1]) > 80:
                    # dot'ta y YUKARI dogru artar, tuvalde asagi dogru.
                    if (tuval[a][1] < tuval[b][1]) != (konum[a][1] > konum[b][1]):
                        bozuk.append("dikey: %s / %s" % (a, b))

    check(kiyas >= 6, "karsilastirilacak yeterli kardes cifti var", kiyas)
    check(not bozuk, "her kardes cifti tuvaldeki sirada yerlesti",
          "\n".join(bozuk[:6]))

    # Yan yana cizilen iki alt durum, cizimde de YAN YANA olmali.
    if "LedOn" in konum and "LedOff" in konum:
        check(abs(konum["LedOn"][1] - konum["LedOff"][1]) < 0.01,
              "yan yana cizilen alt durumlar AYNI SATIRDA kaldi",
              (konum["LedOn"], konum["LedOff"]))
        check(konum["LedOn"][0] < konum["LedOff"][0],
              "soldaki alt durum cizimde de solda")


# --------------------------------------------------------------------------- #
#  32. C ve C++ AYNI yorum ve duzen kurallarini paylasir                        #
# --------------------------------------------------------------------------- #

_C_BOLUM = re.compile(r"^/\* -+ (.*?) -- \*/$")
_CPP_BOLUM = re.compile(r"^\s*// -+ (.*?) --$")

#: Dilin zorunlu kildigi, karsiligi olmayan bolumler.
#:
#: C'de ic islevler dosyanin basinda ONBILDIRILIR; C++'ta ayni islevler
#: sinifin icinde bildirildigi icin ayri bir prototip bolumu yoktur.
#: Ayni sekilde C'nin `instance` struct'i C++'ta ozel (private) bolumdur.
#: * C'de ic islevler dosyanin basinda ONBILDIRILIR; C++'ta ayni islevler
#:   sinifin govdesinde bildirilir (`internal helpers`).
#: * C'nin `instance` struct'i C++'ta ozel bolumdur (`instance state`) ve
#:   dilin kurali geregi kamu API'sinden SONRA gelir.
#: * C++'ta isimsiz uzaydaki yardimcilar, onlari kullanan uye islevlerden
#:   ONCE tanimlanmak zorundadir (`file-scope helpers`).
_YALNIZ_C = {"internal function prototypes", "instance"}
_YALNIZ_CPP = {"internal helpers", "instance state", "file-scope helpers"}


def _bolumler(metin: str, c_stili: bool):
    desen = _C_BOLUM if c_stili else _CPP_BOLUM
    return [desen.match(s).group(1) for s in metin.splitlines()
            if desen.match(s)]


def test_c_and_cpp_layout_match() -> None:
    """C ve C++ ciktilari AYNI bolumleri AYNI sirada ve ayni derinlikte belgeler.

    YASANAN HATA: C tarafi her islev icin @brief/@param/@return yaziyor ve
    dosyayi `/* --- states --- */` seritleriyle bolumlere ayiriyordu; C++
    tarafi tek satirlik `///` ile yetiniyor, hic bolum basligi
    kullanmiyordu. Ayni modelden uretilen iki dosya birbirine hic
    benzemiyordu. Kullanici "c ve c++ yorum satirlari ve kod duzeni de
    ayni olsun" dedi.
    """
    print("\n== 32. C ve C++ duzen esitligi ==")

    from app.core.samples import demo_machine

    sm = demo_machine()
    c = dict(generate_c(sm))
    cpp = dict(generate_cpp(sm))

    c_bas = _bolumler(c["blinky.h"], True)
    p_bas = _bolumler(cpp["Blinky.hpp"], False)
    check(c_bas, "C basliginda bolum seritleri var", c_bas)
    check(p_bas, "C++ basliginda bolum seritleri var", p_bas)
    check([x for x in c_bas if x not in _YALNIZ_C]
          == [x for x in p_bas if x not in _YALNIZ_CPP],
          "baslik bolumleri ayni ve ayni sirada", (c_bas, p_bas))
    # Dilin zorunlu kildigi bolumler de ATLANMIS degil, yalnizca baska
    # yerde: ikisinin de var oldugu ayrica dogrulanir.
    check("instance" in c_bas and "instance state" in p_bas,
          "ornek durumu iki tarafta da belgelenmis")
    check("internal function prototypes" in c_bas
          or "internal helpers" in p_bas,
          "ic islevler iki tarafta da bildirilmis")

    c_kay = _bolumler(c["blinky.c"], True)
    p_kay = _bolumler(cpp["Blinky.cpp"], False)
    check([x for x in c_kay if x not in _YALNIZ_C]
          == [x for x in p_kay if x not in _YALNIZ_CPP],
          "kaynak bolumleri ayni ve ayni sirada", (c_kay, p_kay))

    # Bolum seritleri AYNI GENISLIKTE olmali; biri 79 biri 60 olursa iki
    # dosya yan yana konuldugunda hizasiz gorunur.
    c_genislik = {len(s) for s in c["blinky.h"].splitlines()
                  if _C_BOLUM.match(s)}
    p_genislik = {len(s) for s in cpp["Blinky.hpp"].splitlines()
                  if _CPP_BOLUM.match(s) and not s.startswith(" ")}
    check(c_genislik == {79}, "C bolum seritleri 79 karakter", c_genislik)
    check(p_genislik == {79}, "C++ bolum seritleri de 79 karakter",
          p_genislik)

    # Belgeleme DERINLIGI: iki tarafta da Doxygen etiketleri kullanilir.
    for etiket, metin in (("C", c["blinky.h"] + c["blinky.c"]),
                          ("C++", cpp["Blinky.hpp"] + cpp["Blinky.cpp"])):
        check(len(re.findall(r"@brief", metin)) >= 25,
              "%s ciktisi @brief ile belgelenmis" % etiket,
              len(re.findall(r"@brief", metin)))
        check(len(re.findall(r"@param", metin)) >= 15,
              "%s ciktisi @param ile belgelenmis" % etiket,
              len(re.findall(r"@param", metin)))
        check(len(re.findall(r"@retval|@return", metin)) >= 8,
              "%s ciktisi donus degerlerini belgeliyor" % etiket,
              len(re.findall(r"@retval|@return", metin)))

    # Genel kamu API'sinin HER uyesi belgelenmis olmali.
    for imza in ("void start() noexcept;", "bool dispatch(Event event)",
                 "void doActivity() noexcept;", "bool isIn(State state)",
                 "bool isTerminated() const noexcept;",
                 "static const char* stateName", "static const char* eventName"):
        i = cpp["Blinky.hpp"].find(imza)
        check(i > 0, "C++ basliginda '%s' var" % imza[:28])
        if i > 0:
            onceki = cpp["Blinky.hpp"][:i].rstrip().splitlines()[-1].strip()
            check(onceki == "*/" or onceki.endswith("*/"),
                  "'%s' ustunde Doxygen blogu var" % imza[:28], onceki)

    # Tek satirlik `///` yorumu KALMAMALI: iki dosyada da blok bicim.
    tek = [s.strip() for s in (cpp["Blinky.hpp"] + cpp["Blinky.cpp"]).splitlines()
           if s.strip().startswith("///") and not s.strip().startswith("///<")]
    check(not tek, "C++'ta tek satirlik /// belgesi kalmadi",
          "\n".join(tek[:5]))


# --------------------------------------------------------------------------- #
#  33. Kullanicinin YAZMASI GEREKEN semboller bildirilir                        #
# --------------------------------------------------------------------------- #

def test_required_symbols_reported() -> None:
    """Model govdelerindeki dis cagrilar kullaniciya SOYLENIR.

    YASANAN HATA: entry/exit/do/effect/guard alanlarina yazilan cagrilar
    (led_write, fault_signal, system_halt) uretec tarafindan
    TANIMLANMAZ. Hicbir yerde soylenmiyordu; eksiklik ancak baglama
    asamasinda "undefined reference to `led_write`" olarak ortaya
    cikiyordu -- hedef donanimda, gec ve anlasilmaz bicimde.

    Uc yerde birden bildirilir: uretilen baslikta, MCU orneginde ve
    arayuzun durum cubugunda.
    """
    print(chr(10) + "== 33. Dis semboller bildiriliyor ==")

    from app.codegen.ir import build_ir
    from app.core.model import (State, StateKind, StateMachine, Transition)
    from app.core.samples import demo_machine

    sm = demo_machine()
    ir = build_ir(sm)
    gerekli = {r.name: r for r in ir.required_functions()}
    check(set(gerekli) == {"led_write", "fault_signal", "system_halt"},
          "model govdelerindeki cagrilar bulundu", sorted(gerekli))
    check(gerekli["led_write"].argc == 1, "arguman sayisi dogru",
          gerekli["led_write"].argc)
    check(not gerekli["system_halt"].argc, "argumansiz cagri taniniyor")
    check(any("LedOn / entry" in y for y in gerekli["led_write"].sites),
          "cagrinin NEREDE gectigi kaydedildi", gerekli["led_write"].sites)

    # Anahtar kelimeler islev sanilmamali.
    kw = StateMachine(name="Kw", prefix="kw")
    kw.add_state(State(id="a", name="A", kind=StateKind.SIMPLE,
                       entry="if (ctx->n > 0) { ctx->n--; } while (0) {}"))
    kw.add_state(State(id="i", name="I", kind=StateKind.INITIAL,
                       x=0, y=-60, w=24, h=24))
    kw.add_transition(Transition(source="i", target="a"))
    adlar = {r.name for r in build_ir(kw).required_functions()}
    check(not adlar, "if/while gibi anahtar kelimeler islev sayilmiyor", adlar)

    # Dizgi icindeki parantez arguman sayimini bozmamali.
    st = StateMachine(name="Str", prefix="st")
    st.add_state(State(id="a", name="A", kind=StateKind.SIMPLE,
                       entry='log("a, b(c)", 2);'))
    st.add_state(State(id="i", name="I", kind=StateKind.INITIAL,
                       x=0, y=-60, w=24, h=24))
    st.add_transition(Transition(source="i", target="a"))
    r = build_ir(st).required_functions()
    check(len(r) == 1 and r[0].name == "log" and r[0].argc == 2,
          "dizgi icindeki virgul/parantez sayimi bozmuyor",
          [(x.name, x.argc) for x in r])

    # ctx->uye() cagrisi kullanicinin baglamina aittir, dis sembol degil.
    cx = StateMachine(name="Cx", prefix="cx", context_type="cx_t")
    cx.add_state(State(id="a", name="A", kind=StateKind.SIMPLE,
                       entry="ctx->reset(); obj.run();"))
    cx.add_state(State(id="i", name="I", kind=StateKind.INITIAL,
                       x=0, y=-60, w=24, h=24))
    cx.add_transition(Transition(source="i", target="a"))
    adlar = {x.name for x in build_ir(cx).required_functions()}
    check(not adlar, "uye cagrilari (ctx->f, obj.f) dis sembol sayilmiyor",
          adlar)

    # -- URETILEN DOSYALARDA yaziyor mu -------------------------------------- #
    c = dict(generate_c(sm))
    cpp = dict(generate_cpp(sm))
    for ad, metin in (("blinky.h", c["blinky.h"]),
                      ("blinky_main.c", c["blinky_main.c"]),
                      ("Blinky.hpp", cpp["Blinky.hpp"]),
                      ("Blinky_main.cpp", cpp["Blinky_main.cpp"])):
        check("you must provide" in metin or "you provide" in metin
              or "WHAT YOU HAVE TO WRITE" in metin,
              "%s: 'yazmaniz gerekenler' bolumu var" % ad)
        for sembol in ("led_write", "fault_signal", "system_halt"):
            check(sembol in metin, "%s: %s adi geciyor" % (ad, sembol))

    # PROTOTIP YAZILMAZ: uydurma imza gercekle sessizce uyusmayabilir.
    for ad, metin in (("blinky.h", c["blinky.h"]),
                      ("blinky_main.c", c["blinky_main.c"])):
        for satir in metin.splitlines():
            duz = satir.strip()
            if duz.startswith("extern ") and "led_write" in duz:
                check(False, "%s: led_write icin UYDURMA prototip yok" % ad,
                      duz)
    check(True, "dis semboller bildirilmis ama prototiplenmemis")


# --------------------------------------------------------------------------- #
#  34. Spesifikasyon PDF'i: indirme sonucu YARISA birakilmaz                    #
# --------------------------------------------------------------------------- #

def test_spec_download_result() -> None:
    """Indirme biter bitmez sonuc GUVENLE okunur ve pencere acilir.

    YASANAN HATA: indirme ayri bir is parcaciginda yapiliyor, sonuc ise
    `finished_ok` SINYALIYLE gonderiliyordu. Bekleyen taraf
    `while isci.isRunning(): processEvents()` ile donuyor ve is parcacigi
    durur durmaz cikiyordu -- oysa sinyal ana parcaciga KUYRUKLA teslim
    edilir. Ikisi arasinda bir yaris vardi.

    Ana pencere mesgulken (zamanlayicilar, git is parcacigi) yaris
    KAYBEDILIYORDU: 18 MB'lik dosya eksiksiz iniyor, ama cagiran taraf
    sonucu goremeyip None donuyor ve PDF penceresi HIC ACILMIYORDU.
    Kullanici bunu "pdf indirdi ama acamadi" diye bildirdi.

    Bu bolum AGA CIKMAZ: indirme adresi yerel bir dosyaya cevrilir.
    """
    print(chr(10) + "== 34. Spesifikasyon indirme sonucu ==")

    import tempfile

    from PyQt6.QtWidgets import QApplication, QMessageBox
    import app.ui.spec_window as sw
    from app.ui.main_window import MainWindow, app_settings

    app = QApplication.instance() or QApplication([])
    ayar = app_settings()
    ayar.clear()
    ayar.sync()

    # -- Sonuc SINYALLE degil ALANLARDA tasinmali --------------------------- #
    check(not hasattr(sw._Downloader, "finished_ok"),
          "sonuc sinyali kaldirildi (yaris kaynagi)")
    for alan in ("result_path", "result_error", "cancelled"):
        check(alan in sw._Downloader.__init__.__code__.co_names
              or hasattr(sw._Downloader(tempfile.mktemp()), alan),
              "indirici '%s' alanini tasiyor" % alan)

    kaynak = open(sw.__file__, encoding="utf-8").read()
    check("while worker.isRunning()" not in kaynak,
          "elle processEvents() donguSU KALMADI")
    check("QEventLoop" in kaynak, "bekleme yerel olay dongusuyle yapiliyor")
    check("worker.wait()" in kaynak,
          "alanlar okunmadan once is parcaciginin bittiginden emin olunuyor")

    # -- UCTAN UCA: sahte bir 'indirme' ile sonuc gercekten doner ----------- #
    gecici = tempfile.mkdtemp(prefix="usd_spec_reg_")
    sahte_pdf = os.path.join(gecici, "kaynak.pdf")
    with open(sahte_pdf, "wb") as fh:
        fh.write(b"%PDF-1.4" + bytes([10]) + b"% sahte icerik" + bytes([10]))
    hedef = os.path.join(gecici, sw.SPEC_FILE)

    gercek_url, gercek_yol, gercek_bul, gercek_kutu = (
        sw.SPEC_URL, sw.spec_pdf_path, sw.find_spec_pdf, sw.QMessageBox)

    class _Dugme:
        def __init__(self, ad):
            self.ad = ad

    class _Kutu:
        Icon = QMessageBox.Icon
        ButtonRole = QMessageBox.ButtonRole
        son_hata = []

        def __init__(self, *_a, **_k):
            self._d = []

        def setWindowTitle(self, _t):
            pass

        def setIcon(self, _i):
            pass

        def setText(self, _t):
            pass

        def setInformativeText(self, _t):
            pass

        def addButton(self, metin="", rol=None):
            d = _Dugme(str(metin))
            self._d.append(d)
            return d

        def exec(self):
            return 0

        def clickedButton(self):
            return next(d for d in self._d if d.ad == "Download")

        @staticmethod
        def critical(*a, **_k):
            _Kutu.son_hata.append(a[2] if len(a) > 2 else "")
            return 0

    win = MainWindow()
    win.settings = ayar
    win.resize(1400, 900)
    win.show()
    app.processEvents()
    try:
        sw.SPEC_URL = "file:///" + sahte_pdf.replace(os.sep, "/")
        sw.spec_pdf_path = lambda: hedef
        sw.find_spec_pdf = lambda: (hedef if os.path.isfile(hedef) else None)
        sw.QMessageBox = _Kutu

        # NOT: yerel bir kaynakla indirme ANINDA biter, dolayisiyla bu
        # uctan uca kontrol YARISI yeniden uretemez -- yarisa karsi asil
        # koruma yukaridaki YAPISAL kontrollerdir (sinyal yok, elle
        # dongu yok, wait() var). Buradaki kontrol, duzeltmeden sonra
        # akisin BUTUN olarak calistigini gosterir.
        donen = sw.ensure_spec_pdf(win)
        check(donen == hedef,
              "indirme bitince YOL DONUYOR (akis butun olarak calisiyor)",
              donen)
        check(os.path.isfile(hedef), "dosya hedefe yazildi")
        check(not _Kutu.son_hata, "hata kutusu gosterilmedi", _Kutu.son_hata)

        # Ikinci cagri: dosya artik var, indirme YAPILMAZ.
        donen2 = sw.ensure_spec_pdf(win)
        check(donen2 == hedef, "var olan kopya yeniden indirilmiyor", donen2)

        # -- Es zamanli ikinci cagri ENGELLENIR ----------------------------- #
        win._spec_busy = True
        oncesi = getattr(win, "_spec_window", None)
        win.show_spec()
        check(getattr(win, "_spec_window", None) is oncesi,
              "indirme surerken ikinci istek yok sayiliyor")
        win._spec_busy = False
    finally:
        sw.SPEC_URL = gercek_url
        sw.spec_pdf_path = gercek_yol
        sw.find_spec_pdf = gercek_bul
        sw.QMessageBox = gercek_kutu
        win.doc.mark_clean()
        win.class_doc.mark_clean()
        try:
            win.git_panel.shutdown()
        except Exception:                      # noqa: BLE001
            pass
        win.close()
        ayar.clear()
        ayar.sync()
        shutil.rmtree(gecici, ignore_errors=True)



# --------------------------------------------------------------------------- #

def test_spec_viewer_does_not_freeze() -> None:
    """35) Spesifikasyon penceresi: ANINDA acilmali, TUM belge gezilmeli.

    IKI AYRI HATA BU BOLUMDE KILITLENIR.

    1) DONMA. Pencere QPdfView'in MultiPage kipini kullaniyordu. O kip
       belgenin tamaminin yerlesimini PESIN hesaplar; her sayfa icin
       pagePointSize() cagirir ve pdfium her sayfayi ilk erisimde acar.
       796 sayfalik spesifikasyonda olculdu:

           MultiPage, dosyadan yukleme   47 804 ms
           MultiPage, bellekten yukleme  22 848 ms
           tek sayfa render()              7-38 ms

       Yani uygulama yarim dakikadan uzun sure tamamen kilitleniyordu.

    2) TEK SAYFAYA HAPSOLMA. Donmayi gidermek icin SinglePage kipine
       gecilmisti; bu sefer kaydirma cubugu YALNIZCA gecerli sayfayi
       kapsiyor, kullanici 796 sayfa boyunca gezinemiyordu. Kullanici
       bunu "sagda sadece tek sayfada asagi iniyor" diye bildirdi.

    Cozum ikisini de disarida birakan kendi surekli gorunumumuz
    (ContinuousPdfView): yerlesim ilk sayfanin olcusunden tahmin edilir,
    kaydirma cubugu butun belgeye yayilir, yalnizca gorunen sayfalar ve
    o da AYRI BIR IS PARCACIGINDA cizilir.

    Bu bolum AGA CIKMAZ: kendi cok sayfali PDF'ini uretir.
    """
    print(chr(10) + "== 35. Spesifikasyon goruntuleyici ==")

    import inspect
    import shutil
    import tempfile
    import time

    from PyQt6.QtCore import QPoint, QPointF, Qt
    from PyQt6.QtGui import (QPageSize, QPainter, QPdfWriter, QWheelEvent)
    from PyQt6.QtWidgets import QApplication, QToolBar

    import app.ui.spec_window as sw

    kaynak = inspect.getsource(sw)

    # -- QPdfView'in HICBIR kipi kullanilmamali ----------------------------- #
    check("PageMode.MultiPage" not in kaynak,
          "MultiPage kipi kurulmuyor (46 saniyelik pesin yerlesim)")
    check("PageMode.SinglePage" not in kaynak,
          "SinglePage kipi kurulmuyor (tek sayfaya hapsediyordu)")
    check(hasattr(sw, "ContinuousPdfView"),
          "kendi surekli gorunumu var")

    # -- Arayuz ve BETIKLER ayni PDF'i gormeli ------------------------------ #
    #
    # YASANAN HATA: QStandardPaths.AppDataLocation uygulama adi ayarliysa
    # onu icerir. Arayuz adi kurar, tools/ altindaki betikler kurmaz; ayni
    # cagri iki farkli klasor donduruyor ve arayuzun sorunsuz actigi PDF
    # betiklerde "bulunamadi" sayiliyordu.
    from app.ui.main_window import APP_NAME, SETTINGS_ORG
    check((sw.GUI_APP, sw.GUI_ORG) == (APP_NAME, SETTINGS_ORG),
          "PDF arama kimlikleri main_window ile ayni",
          "%r / %r" % ((sw.GUI_APP, sw.GUI_ORG), (APP_NAME, SETTINGS_ORG)))
    adaylar = sw.spec_pdf_candidates()
    check(len(adaylar) >= 2, "PDF icin birden cok yol deneniyor",
          "%d aday" % len(adaylar))
    check(any(APP_NAME in aday for aday in adaylar),
          "arayuzun kullandigi klasor de aday listesinde")

    app = QApplication.instance() or QApplication([])
    gecici = tempfile.mkdtemp(prefix="uml_spec_")
    try:
        SAYFA = 12
        yol = os.path.join(gecici, "ornek.pdf")
        yazici = QPdfWriter(yol)
        yazici.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        boyaci = QPainter(yazici)
        for i in range(SAYFA):
            if i:
                yazici.newPage()
            boyaci.drawText(400, 1200, "Page %d" % (i + 1))
        boyaci.end()
        check(os.path.getsize(yol) > 0, "test PDF'i uretildi")

        basla = time.perf_counter()
        pencere = sw.SpecWindow(yol)
        pencere.resize(620, 500)
        pencere.show()
        for _ in range(30):
            app.processEvents()
        sure = time.perf_counter() - basla

        def bekle(sn: float = 0.9) -> None:
            son = time.perf_counter() + sn
            while time.perf_counter() < son:
                app.processEvents()

        try:
            check(sure < 5.0, "pencere hizli aciliyor", "%.2f sn" % sure)
            check(pencere.doc.pageCount() == SAYFA,
                  "%d sayfa yuklendi" % SAYFA)

            gorunum = pencere.view
            cubuk = gorunum.verticalScrollBar()

            # -- KAYDIRMA CUBUGU BUTUN BELGEYI KAPSAMALI ----------------- #
            #
            # Asil gerileme testi: cubugun kapsadigi yukseklik tek bir
            # sayfa degil, butun belge kadar olmali.
            tek = gorunum._h[0] + gorunum.GAP
            kapsam = (cubuk.maximum() + gorunum.viewport().height()) / tek
            check(kapsam > SAYFA - 1.0,
                  "kaydirma cubugu BUTUN belgeyi kapsiyor",
                  "%.1f sayfalik (beklenen ~%d)" % (kapsam, SAYFA))
            check(cubuk.maximum() > 0, "belge kaydirilabilir")

            # -- Cubugun SONU son sayfaya varmali ------------------------ #
            cubuk.setValue(cubuk.maximum())
            bekle()
            check(gorunum.current_page() == SAYFA - 1,
                  "cubugun sonu SON sayfaya variyor",
                  "sayfa %d" % (gorunum.current_page() + 1))
            check("Page %d of %d" % (SAYFA, SAYFA) in pencere.lbl_page.text(),
                  "gosterge son sayfayi yaziyor")

            # -- Sayfalar GERCEKTEN ciziliyor mu ------------------------- #
            cubuk.setValue(0)
            bekle(1.2)
            check(len(gorunum._onbellek) > 0,
                  "gorunen sayfalar ciziliyor",
                  "onbellek: %s" % sorted(gorunum._onbellek))

            # -- Sayfa kutusu gidis-donus -------------------------------- #
            pencere.spin.setValue(7)
            bekle()
            check(gorunum.current_page() == 6,
                  "sayfa kutusu istenen sayfaya goturuyor",
                  "sayfa %d" % (gorunum.current_page() + 1))
            check(pencere.spin.value() == 7, "kutu geri sicramiyor")

            # -- Kesintisiz kaydirma: sayfa siniri okumayi durdurmaz ----- #
            once = gorunum.current_page()
            for _ in range(10):
                cubuk.setValue(cubuk.value() + int(tek / 3))
                bekle(0.12)
            check(gorunum.current_page() > once,
                  "kaydirma sayfa sinirini kendiliginden geciyor",
                  "%d -> %d" % (once + 1, gorunum.current_page() + 1))

            # -- YAKINLASTIRMA ------------------------------------------- #
            pencere.a_fit_width.trigger()
            bekle(0.4)
            sigan = gorunum._w[0]
            check(gorunum.fit_mode() == "genislik", "genislige sigdirma kipi")
            check(abs(sigan + 2 * gorunum.MARGIN
                      - gorunum.viewport().width()) <= 4.0,
                  "genislige sigdirma gorunume oturuyor",
                  "%.0f + kenar / %d" % (sigan, gorunum.viewport().width()))

            yakin_once = gorunum.zoom_factor()
            pencere.a_zoom_in.trigger()
            bekle(0.5)
            check(gorunum.zoom_factor() > yakin_once,
                  "yakinlastirma orani buyuyor")
            check(gorunum._w[0] > sigan,
                  "sayfa gercekten buyuyor",
                  "%.0f -> %.0f px" % (sigan, gorunum._w[0]))
            check(gorunum.horizontalScrollBar().maximum() > 0,
                  "yakinlasinca yana kaydirilabiliyor")
            check("%d%%" % int(round(gorunum.zoom_factor() * 100.0))
                  == pencere.lbl_zoom.text(),
                  "yakinlik etiketi guncel", pencere.lbl_zoom.text())

            pencere.a_zoom_out.trigger()
            bekle(0.4)
            check(abs(gorunum.zoom_factor() - yakin_once) < 1e-6,
                  "uzaklastirma yakinlastirmayi geri aliyor")

            pencere.a_fit_page.trigger()
            bekle(0.4)
            check(gorunum._h[0] <= gorunum.viewport().height() + 1.0,
                  "sayfaya sigdirma sayfayi tam gosteriyor",
                  "%.0f / %d" % (gorunum._h[0],
                                 gorunum.viewport().height()))

            # -- Yakinlastirma BAKILAN YERI korumali ---------------------- #
            pencere.a_fit_width.trigger()
            bekle(0.4)
            pencere.spin.setValue(9)
            bekle()
            capa = gorunum.current_page()
            pencere.a_zoom_in.trigger()
            bekle(0.5)
            check(gorunum.current_page() == capa,
                  "yakinlastirma bakilan sayfayi koruyor",
                  "%d -> %d" % (capa + 1, gorunum.current_page() + 1))

            # -- UZUN pencere + COK UZAKLASMA: sayac kaymamali ------------ #
            #
            # YASANAN HATA: gecerli sayfa "gorunumun ust ucte birindeki
            # sayfa" diye bulunuyordu. Gorunume bes alti sayfa siginca o
            # nokta bakilan sayfanin birkac altina dusuyordu: 1453 piksel
            # yuksekligindeki bir pencerede %20 yakinlikta 16. sayfaya
            # gidilince sayac 18 yaziyordu.
            pencere.resize(700, 1200)
            bekle(0.5)
            for oran in (1.0, 0.4, 0.25, 0.20):
                gorunum.set_zoom(oran)
                bekle(0.25)
                gorunum.goto(8)
                bekle(0.35)
                check(gorunum.current_page() == 8,
                      "%d%% yakinlikta sayfa sayaci kaymiyor"
                      % int(round(gorunum.zoom_factor() * 100.0)),
                      "goto(8) -> %d" % gorunum.current_page())
            # -- SON sayfa gorunume cok sayfa siginca da secilebilmeli --- #
            #
            # YASANAN HATA: esitlik her zaman EN USTTEKI sayfaya
            # veriliyordu. Belgenin sonunda kaydirma cubugu daha fazla
            # inemedigi icin son sayfa hicbir zaman en uste gelemez;
            # sonuc olarak son sayfa SECILEMEZ oluyordu -- 400 sayfalik
            # belgede son sayfa tam ekrandayken sayac 399 yaziyordu.
            for oran in (0.5, 0.3):
                gorunum.set_zoom(oran)
                bekle(0.3)
                gorunum.goto(SAYFA - 1)
                bekle(0.5)
                check(gorunum.current_page() == SAYFA - 1,
                      "%d%% yakinlikta SON sayfa secilebiliyor"
                      % int(round(gorunum.zoom_factor() * 100.0)),
                      "sayfa %d / %d" % (gorunum.current_page() + 1, SAYFA))

            # -- YALNIZCA yukseklik degisince kaydirma araligi ------------ #
            #
            # YASANAN HATA: genislige sigdirma olcegi gorunum
            # GENISLIGINDEN turetir; yalnizca yukseklik degisince olcek
            # ayni kaliyor, set_zoom erken donuyor ve kaydirma araligi
            # eski gorunum yuksekligine gore kaliyordu. OLCULDU: 796
            # sayfalik belgede pencere 900'den 480 piksele kisaltilinca
            # belgenin son 408 pikseline hicbir yolla inilemiyor, ustelik
            # pageStep eski kaldigi icin her PageDown 408 piksellik
            # metni atliyordu.
            pencere.a_fit_width.trigger()
            bekle(0.4)
            pencere.resize(700, 900)
            bekle(0.5)
            pencere.resize(700, 420)           # YALNIZCA yukseklik
            bekle(0.6)
            gereken = round(gorunum._total - gorunum.viewport().height())
            check(gorunum.verticalScrollBar().maximum() == gereken,
                  "yalnizca yukseklik degisince kaydirma araligi yenileniyor",
                  "%d / %d" % (gorunum.verticalScrollBar().maximum(),
                               gereken))
            check(gorunum.verticalScrollBar().pageStep()
                  == max(1, gorunum.viewport().height() - 24),
                  "pageStep yeni gorunum yuksekligine gore",
                  "%d / %d" % (gorunum.verticalScrollBar().pageStep(),
                               gorunum.viewport().height() - 24))
            gorunum.verticalScrollBar().setValue(
                gorunum.verticalScrollBar().maximum())
            bekle(0.5)
            check(gorunum.current_page() == SAYFA - 1,
                  "kisaltilmis pencerede belgenin SONUNA inilebiliyor",
                  "sayfa %d" % (gorunum.current_page() + 1))

            pencere.resize(620, 500)
            bekle(0.4)

            # -- Ctrl+tekerlek yakinlastirir, duz tekerlek kaydirir ------- #
            pencere.a_fit_width.trigger()
            bekle(0.4)
            z0 = gorunum.zoom_factor()
            gorunum.wheelEvent(QWheelEvent(
                QPointF(200.0, 200.0), QPointF(200.0, 200.0),
                QPoint(0, 0), QPoint(0, 120), Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.ControlModifier,
                Qt.ScrollPhase.NoScrollPhase, False))
            bekle(0.4)
            check(gorunum.zoom_factor() > z0,
                  "Ctrl+tekerlek yakinlastiriyor")

            pencere.a_fit_width.trigger()
            bekle(0.4)
            cubuk = gorunum.verticalScrollBar()
            cubuk.setValue(0)
            bekle(0.2)
            gorunum.wheelEvent(QWheelEvent(
                QPointF(200.0, 200.0), QPointF(200.0, 200.0),
                QPoint(0, 0), QPoint(0, -120), Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
                Qt.ScrollPhase.NoScrollPhase, False))
            bekle(0.3)
            check(gorunum.verticalScrollBar().value() > 0,
                  "duz tekerlek belgeyi kaydiriyor",
                  "konum %d" % gorunum.verticalScrollBar().value())

            # -- Onbellek sinirli ----------------------------------------- #
            toplam = sum(g.sizeInBytes()
                         for _, _, g in gorunum._onbellek.values())
            check(toplam <= gorunum.ONBELLEK_BAYT,
                  "cizim onbellegi sinir icinde",
                  "%.1f MB" % (toplam / 1048576.0))

            # -- HIZLI KAYDIRMA cizim istememeli -------------------------- #
            #
            # YASANAN HATA: her ara konum icin sayfa isteniyordu.
            # Kullanici cubugu belge boyunca surukledigi surece hicbiri
            # goruntulenmeyecek onlarca cizim uretiliyor, tek bir
            # kaydirma adimi 225 ms'ye kadar cikiyordu. Istekler artik
            # kaydirma DURDUKTAN sonra veriliyor.
            gorunum._onbellek.clear()
            gorunum._pending.clear()
            cubuk = gorunum.verticalScrollBar()
            basla = time.perf_counter()
            for k in range(20):
                cubuk.setValue(int(cubuk.maximum() * k / 20.0))
                app.processEvents()
            gecen = (time.perf_counter() - basla) * 1000.0
            # Kosul: dongu gecikmeden HIZLI tamamlanmis olmali. Cok yuklu
            # bir makinede tek bir processEvents() 90 ms'yi asabilir; o
            # durumda zamanlayici hakli olarak atesler ve "kaydirma hala
            # suruyor" varsayimi gecersizdir. Testi yanlis yere
            # basarisiz saymak yerine ATLANIR.
            if gecen < gorunum.REQUEST_DELAY:
                check(not gorunum._pending,
                      "hizli kaydirma sirasinda cizim ISTENMIYOR",
                      "%d istek ucusta" % len(gorunum._pending))
                check(gorunum._request_timer.isActive(),
                      "istek zamanlayicisi kaydirmayla yeniden basliyor")
            else:
                skip("makine yavas: kaydirma dongusu %.0f ms surdu "
                     "(gecikme %d ms) -- erteleme sinanamadi"
                     % (gecen, gorunum.REQUEST_DELAY))
            bekle(0.8)
            check(len(gorunum._onbellek) > 0,
                  "kaydirma DURUNCA sayfalar ciziliyor",
                  "onbellek: %s" % sorted(gorunum._onbellek))

            # -- BOSTA sonsuz cizim dongusu OLMAMALI ---------------------- #
            #
            # YASANAN HATA: atma dongusu "en az iki girdi kalsin" diyordu,
            # oysa her boyamada gorunenler arti birer komsu, yani en az UC
            # sayfa isteniyordu. Uc goruntu butceyi asinca N onbellege
            # konunca N-1 atiliyor, sonraki boyama N-1'i yeniden istiyor ve
            # dongu hic bitmiyordu. OLCULDU: pencere hicbir girdi almadan
            # %200 yakinlikta 3 saniyede 118 kez yeniden boyaniyor,
            # %250'de okunan sayfa 29 kez yer tutucuya donuyordu.
            gorunum.set_zoom(2.5)
            bekle(1.2)
            boyama = [0]
            asil_paint = type(gorunum).paintEvent

            def sayan_paint(kendi, olay, _a=asil_paint, _s=boyama):
                _s[0] += 1
                return _a(kendi, olay)

            type(gorunum).paintEvent = sayan_paint
            try:
                boyama[0] = 0
                bekle(1.5)             # HICBIR girdi yok
                check(boyama[0] <= 3,
                      "pencere bosta dururken yeniden boyanmiyor",
                      "1,5 sn'de %d boyama" % boyama[0])
            finally:
                type(gorunum).paintEvent = asil_paint
            pencere.a_fit_width.trigger()
            bekle(0.5)

            # -- Ctrl+tekerlek centleri BIRIKTIRMELI ---------------------- #
            #
            # YASANAN HATA: yalnizca isaret okunuyordu; Windows hassas
            # dokunmatik yuzeyinin tek harekette gonderdigi onlarca kucuk
            # olayin HER BIRI tam bir 1,25 kati uyguluyor, yakinlik
            # %108'den %400'e firliyordu.
            z_once = gorunum.zoom_factor()
            for _ in range(6):
                gorunum.wheelEvent(QWheelEvent(
                    QPointF(200.0, 200.0), QPointF(200.0, 200.0),
                    QPoint(0, 0), QPoint(0, 8), Qt.MouseButton.NoButton,
                    Qt.KeyboardModifier.ControlModifier,
                    Qt.ScrollPhase.NoScrollPhase, False))
            bekle(0.3)
            check(abs(gorunum.zoom_factor() - z_once) < 1e-9,
                  "centin kesri yakinlastirmiyor",
                  "%d%% -> %d%%" % (round(z_once * 100),
                                    round(gorunum.zoom_factor() * 100)))
            for _ in range(9):
                gorunum.wheelEvent(QWheelEvent(
                    QPointF(200.0, 200.0), QPointF(200.0, 200.0),
                    QPoint(0, 0), QPoint(0, 8), Qt.MouseButton.NoButton,
                    Qt.KeyboardModifier.ControlModifier,
                    Qt.ScrollPhase.NoScrollPhase, False))
            bekle(0.3)
            check(abs(gorunum.zoom_factor()
                      - z_once * gorunum.ZOOM_ADIM) < 1e-6,
                  "tam cent TEK adim yakinlastirir",
                  "%d%%" % round(gorunum.zoom_factor() * 100))
            pencere.a_fit_width.trigger()
            bekle(0.5)

            # -- Sayfa dikdortgenleri TAM PIKSEL olmali ------------------- #
            #
            # Kesirli hedef dikdortgen goruntuyu Qt'nin 1:1 kopyalama
            # yolundan cikarir; olculdu, kenar karsitligi %65'e duserek
            # yazi surekli yumusak gorunuyordu.
            check(all(float(x).is_integer()
                      for x in (gorunum._w[0], gorunum._h[0], gorunum._y[0],
                                gorunum._page_x(gorunum._w[0]))),
                  "sayfa dikdortgenleri tam piksel",
                  "w=%.2f h=%.2f y=%.2f x=%.2f"
                  % (gorunum._w[0], gorunum._h[0], gorunum._y[0],
                     gorunum._page_x(gorunum._w[0])))
            girdi = gorunum._onbellek.get(gorunum.current_page())
            if girdi is not None:
                check(girdi[2].width() == int(gorunum._w[0])
                      and girdi[2].height() == int(gorunum._h[0]),
                      "cizim hedefle AYNI piksel olcusunde (1:1 kopya)",
                      "%dx%d / %dx%d" % (girdi[2].width(), girdi[2].height(),
                                         int(gorunum._w[0]),
                                         int(gorunum._h[0])))
            else:
                skip("sayfa henuz cizilmedi: 1:1 kopya denenmedi")

            # -- Butun eylemler KLAVYEDEN erisilebilir -------------------- #
            #
            # YASANAN HATA: "Open the spec page" tek elle kurulan eylemdi,
            # kisayolu yoktu. Arac cubugu dar kalip tasma menusune
            # dustugunde klavyeyle hic erisilemiyordu: arac cubugu
            # dugmeleri odak almaz ve pencerede menu cubugu yok.
            cubuk_nesnesi = pencere.findChild(QToolBar)
            eylemler = [a for a in cubuk_nesnesi.actions()
                        if not a.isSeparator() and a.text()]
            check(len(eylemler) >= 9, "arac cubugunda dokuz eylem var",
                  "%d eylem" % len(eylemler))
            for eylem in eylemler:
                check(bool(eylem.shortcut().toString()),
                      "eylemin klavye kisayolu var", eylem.text())

            # -- Arac cubugu SATIR-ICI stil sayfasi almamali -------------- #
            #
            # Alsaydi renk kurulum aninda gomulur, satir-ici sayfa
            # uygulama sayfasini ezdigi icin tema degisiminde serit eski
            # zeminde kalir ve yazi 1,05:1 karsitliga duserdi.
            check(cubuk_nesnesi.styleSheet() == "",
                  "arac cubugu temayi uygulama sayfasindan aliyor")

            # -- Kuyruktan DUSEN istek "bekliyor" kalmamali --------------- #
            #
            # Kalsaydi o sayfa sonsuza dek yer tutucu olarak gorunurdu.
            # Isci BASLATILMADAN denenir: calisan bir isci kuyrugu
            # bosalttigi icin tasma hic olusmaz ve sozlesme sinanamaz.
            sessiz = sw._PageRenderer(pencere.doc)
            sinir = sessiz.QUEUE_LIMIT
            dusenler = []
            for sira in range(sinir + 16):
                dusenler.extend(sessiz.request_page(sira, 100, 130))
            check(len(dusenler) == 16,
                  "kuyruk tasinca DUSEN istekler bildiriliyor",
                  "%d dusen (beklenen 16)" % len(dusenler))
            check(dusenler == list(range(16)),
                  "dusenler EN ESKI istekler")
            bekleyen_deneme = {s: (100, 130) for s in range(sinir + 16)}
            for dusen in dusenler:
                bekleyen_deneme.pop(dusen, None)
            check(len(bekleyen_deneme) == sinir,
                  "bildirilen dusenler bekleyen listesinden silinebiliyor",
                  "%d kaldi" % len(bekleyen_deneme))
            gorunum._pending.clear()

            # -- Gosterge KALICI parcacikta ------------------------------- #
            #
            # showMessage() ile yazilsaydi arac cubugu durum ipuclari onu
            # ezer, fare uzaklasinca bos ipucu tamamen silerdi.
            simdiki = pencere.lbl_page.text()
            pencere.statusBar().showMessage("hover ipucu")
            check(pencere.lbl_page.text() == simdiki,
                  "gecici mesaj sayfa gostergesini EZMIYOR")
            pencere.statusBar().clearMessage()

            # -- Cipilak PageUp/PageDown eyleme baglanmamali -------------- #
            for eylem in (pencere.a_first, pencere.a_prev, pencere.a_next,
                          pencere.a_last, pencere.a_zoom_in,
                          pencere.a_zoom_out, pencere.a_fit_width,
                          pencere.a_fit_page):
                metin = eylem.shortcut().toString()
                check(metin.startswith("Ctrl+"),
                      "gorunum kisayolu degistirici tus kullaniyor",
                      "%s -> %r" % (eylem.text(), metin))

            # -- __init__ YARIDA kalirsa isci DURDURULMALI ---------------- #
            #
            # Gorunum kuruldugu anda bir is parcacigi calismaya baslar.
            # Sonraki bir satir hata atar ve isci durdurulmadan cikilirsa
            # calisan bir QThread yok edilir; Qt bunu qFatal ile
            # karsilar ve surec SESSIZCE oler (0xC0000409).
            asil_kur = sw.SpecWindow._kur

            def patlayan_kur(kendi):
                raise RuntimeError("kasitli hata")

            sw.SpecWindow._kur = patlayan_kur
            try:
                sw.SpecWindow(yol)
                check(False, "yarida kalan kurulum hata veriyor",
                      "hata verilmedi")
            except RuntimeError:
                check(True, "yarida kalan kurulum hata veriyor")
            finally:
                sw.SpecWindow._kur = asil_kur
            bekle(0.4)
            check(True, "yarida kalan kurulumdan sonra surec yasiyor")

            # -- Bozuk dosya SESSIZ bos pencere degil, HATA verir --------- #
            bozuk = os.path.join(gecici, "bozuk.pdf")
            with open(bozuk, "wb") as fh:
                fh.write(b"not a pdf at all")
            try:
                sw.SpecWindow(bozuk)
                check(False, "bozuk PDF hata veriyor", "hata verilmedi")
            except Exception:                  # noqa: BLE001
                check(True, "bozuk PDF hata veriyor")
        finally:
            pencere.close()
            bekle(0.3)

        # -- Kapanista isci parcacigi DURMALI ---------------------------- #
        #
        # Durmazsa belge yok edilirken hala pdfium icinde olabilir.
        check(pencere.view._worker is None,
              "kapanista cizim is parcacigi durduruluyor")

        # -- Kapanista BELLEK birakilmali -------------------------------- #
        #
        # YASANAN HATA: kapatilan pencere cizim onbellegini ve pdfium
        # sayfa onbellegini tutmaya devam ediyordu. Pencere sekiz kez
        # acilip kapatildiginda surec 131 MB'den 463 MB'ye cikiyordu.
        check(not pencere.view._onbellek,
              "kapanista cizim onbellegi bosaltiliyor",
              "%d sayfa kaldi" % len(pencere.view._onbellek))
        check(pencere.doc.pageCount() == 0,
              "kapanista PDF belgesi kapatiliyor")
    finally:
        shutil.rmtree(gecici, ignore_errors=True)


# --------------------------------------------------------------------------- #

def test_alignment_guides() -> None:
    """36) Suruklenen eleman komsusuyla ayni satira/sutuna YAPISMALI.

    Elle "goz kararı" hizalama bir iki piksel sasar ve diyagram basildigi
    ya da bir belgeye konuldugu anda bu goze carpar. Kullanici bunu
    "otomatik satirda veya sutunda durumu hareket ettirince eger ayni
    hizada ise orada hizalasin" diye istedi.

    Karsilastirilan olcutler AYNI TURDEN olanlardir: sol-sol, orta-orta,
    sag-sag (sutun) ve ust-ust, orta-orta, alt-alt (satir).
    """
    print(chr(10) + "== 36. Hizalama kilavuzlari ==")

    from PyQt6.QtCore import QPoint, QPointF, Qt
    from PyQt6.QtGui import QMouseEvent
    from PyQt6.QtWidgets import QApplication

    from app.core.class_model import ClassModel, UmlClass
    from app.core.model import State, StateKind, StateMachine
    from app.ui.canvas import DiagramCanvas, Tool
    from app.ui.class_canvas import ClassCanvas, ClassTool
    from app.ui.document import Document

    app = QApplication.instance() or QApplication([])

    def durum_tuvali(durumlar, izgara=False):
        sm = StateMachine(name="M", prefix="m", context_type="void")
        for sid, x, y, w, h, ust in durumlar:
            sm.add_state(State(id=sid, name=sid.upper(),
                               kind=StateKind.SIMPLE,
                               x=x, y=y, w=w, h=h, parent=ust))
        tuval = DiagramCanvas(Document(sm))
        tuval.auto_edit = False
        tuval.resize(900, 700)
        tuval.show()
        tuval.rebuild()
        tuval.tool = Tool.SELECT
        tuval.snap_enabled = izgara
        for _ in range(8):
            app.processEvents()
        return tuval

    def surukle(tuval, oge, dx, dy, birak=False):
        """Ogeyi GERCEK fare olaylariyla surukler.

        Sahte bir `setPos()` ise yaramaz: hizalama YALNIZCA fareyle
        tutulan oge icin calisir (bkz. CanvasNavigation._align_uygun).
        """
        merkez = oge.mapToScene(oge.rect().center())
        bas = tuval.mapFromScene(merkez)
        son = tuval.mapFromScene(merkez + QPointF(dx, dy))
        ara = QPoint((bas.x() + son.x()) // 2, (bas.y() + son.y()) // 2)
        for sira, nokta in enumerate((bas, ara, son)):
            p = QPointF(nokta)
            tip = (QMouseEvent.Type.MouseButtonPress if sira == 0
                   else QMouseEvent.Type.MouseMove)
            app.sendEvent(tuval.viewport(), QMouseEvent(
                tip, p, tuval.viewport().mapToGlobal(p),
                Qt.MouseButton.LeftButton if sira == 0
                else Qt.MouseButton.NoButton,
                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
            app.processEvents()
        if birak:
            p = QPointF(son)
            app.sendEvent(tuval.viewport(), QMouseEvent(
                QMouseEvent.Type.MouseButtonRelease, p,
                tuval.viewport().mapToGlobal(p), Qt.MouseButton.LeftButton,
                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
            app.processEvents()

    # -- SUTUN: sol kenarlar -------------------------------------------- #
    tuval = durum_tuvali([("a", 100.0, 100.0, 120.0, 60.0, None),
                          ("b", 103.0, 400.0, 120.0, 60.0, None)])
    b = tuval.state_items["b"]
    surukle(tuval, b, -1.0, 0.0)
    check(abs(b.pos().x() - 100.0) < 0.01,
          "ayni SUTUNA yapisiyor", "b.x = %.2f" % b.pos().x())
    check(len(tuval._align_guides) == 1,
          "bir kilavuz cizgisi var", "%d" % len(tuval._align_guides))
    if tuval._align_guides:
        bas, son = tuval._align_guides[0]
        check(abs(bas.x() - son.x()) < 0.01,
              "sutun kilavuzu DIKEY")
        check(bas.y() < 100.0 and son.y() > 460.0,
              "kilavuz hizalanan iki kutuyu da kapsiyor",
              "%.0f .. %.0f" % (bas.y(), son.y()))
    surukle(tuval, b, 0.0, 0.0, birak=True)
    check(not tuval._align_guides,
          "birakinca kilavuz siliniyor",
          "%d kaldi" % len(tuval._align_guides))
    tuval.close()

    # -- SATIR: ust kenarlar --------------------------------------------- #
    tuval = durum_tuvali([("a", 100.0, 200.0, 120.0, 60.0, None),
                          ("b", 500.0, 203.0, 120.0, 60.0, None)])
    b = tuval.state_items["b"]
    surukle(tuval, b, 0.0, -1.0)
    check(abs(b.pos().y() - 200.0) < 0.01,
          "ayni SATIRA yapisiyor", "b.y = %.2f" % b.pos().y())
    if tuval._align_guides:
        bas, son = tuval._align_guides[0]
        check(abs(bas.y() - son.y()) < 0.01, "satir kilavuzu YATAY")
    else:
        check(False, "satir kilavuzu YATAY", "kilavuz yok")
    tuval.close()

    # -- ORTA hizasi (farkli genislikte kutular) ------------------------- #
    tuval = durum_tuvali([("a", 100.0, 100.0, 200.0, 60.0, None),
                          ("b", 141.0, 400.0, 120.0, 60.0, None)])
    b = tuval.state_items["b"]
    surukle(tuval, b, -1.0, 0.0)
    check(abs((b.pos().x() + 60.0) - 200.0) < 0.01,
          "farkli genislikte kutular ORTADAN hizalaniyor",
          "b ortasi %.2f / a ortasi 200.00" % (b.pos().x() + 60.0))
    tuval.close()

    # -- ESIK disinda YAPISMAZ ------------------------------------------- #
    tuval = durum_tuvali([("a", 100.0, 100.0, 120.0, 60.0, None),
                          ("b", 100.0, 400.0, 120.0, 60.0, None)])
    b = tuval.state_items["b"]
    surukle(tuval, b, 40.0, 0.0)
    check(abs(b.pos().x() - 140.0) < 2.0,
          "esik disinda serbestce hareket ediyor",
          "b.x = %.2f" % b.pos().x())
    check(not tuval._align_guides, "hiza yoksa kilavuz da yok")
    tuval.close()

    # -- PROGRAMLI setPos hizalanmamali ---------------------------------- #
    #
    # `itemChange` dosya yuklerken, geri al/yinele yaparken ve ok
    # tuslariyla kaydirirken de tetiklenir. Oralarda yapismak modeli
    # kullanicinin istemedigi bicimde SESSIZCE degistirirdi.
    tuval = durum_tuvali([("a", 100.0, 100.0, 120.0, 60.0, None),
                          ("b", 400.0, 400.0, 120.0, 60.0, None)])
    b = tuval.state_items["b"]
    b.setPos(QPointF(102.0, 400.0))
    for _ in range(4):
        app.processEvents()
    check(abs(b.pos().x() - 102.0) < 0.01,
          "programli setPos HIZALANMIYOR", "b.x = %.2f" % b.pos().x())
    check(not tuval._align_guides, "programli setPos kilavuz cizmiyor")
    tuval.close()

    # -- COK SECIMDE hizalanmaz ------------------------------------------ #
    #
    # Qt her ogeye AYRI ItemPositionChange gonderir; yalnizca tutulan oge
    # kaydirilirsa secim birbirine gore DAGILIR.
    tuval = durum_tuvali([("a", 100.0, 100.0, 120.0, 60.0, None),
                          ("b", 103.0, 400.0, 120.0, 60.0, None),
                          ("d", 600.0, 400.0, 120.0, 60.0, None)])
    for anahtar in ("b", "d"):
        tuval.state_items[anahtar].setSelected(True)
    surukle(tuval, tuval.state_items["b"], -1.0, 0.0)
    check(abs(tuval.state_items["b"].pos().x() - 102.0) < 0.01,
          "cok secimde hizalama yapilmiyor",
          "b.x = %.2f" % tuval.state_items["b"].pos().x())
    tuval.close()

    # -- BILESIK durum: kardeslerine hizalanir, kilavuz SAHNEYE cevrilir - #
    tuval = durum_tuvali([("c", 100.0, 100.0, 400.0, 300.0, None),
                          ("x", 40.0, 60.0, 120.0, 60.0, "c"),
                          ("y", 43.0, 180.0, 120.0, 60.0, "c")])
    y = tuval.state_items["y"]
    surukle(tuval, y, -1.0, 0.0)
    check(abs(y.pos().x() - 40.0) < 0.01,
          "bilesik durum icinde KARDESE hizalaniyor",
          "y.x = %.2f" % y.pos().x())
    if tuval._align_guides:
        bas, _son = tuval._align_guides[0]
        check(abs(bas.x() - 200.0) < 0.01,
              "kilavuz ebeveyn koordinatindan SAHNEYE cevriliyor",
              "sahne x = %.2f (100 + 100 bekleniyor)" % bas.x())
    else:
        check(False, "kilavuz ebeveyn koordinatindan SAHNEYE cevriliyor",
              "kilavuz yok")
    tuval.close()

    # -- IZGARADAN SONRA gelir ------------------------------------------- #
    #
    # Izgara 10 px'e yuvarlar; hiza ise komsunun GERCEK kenarina oturtur.
    # Ters sirada olsaydi izgara, bulunan hizayi hemen bozardi.
    tuval = durum_tuvali([("a", 137.0, 100.0, 120.0, 60.0, None),
                          ("b", 142.0, 400.0, 120.0, 60.0, None)],
                         izgara=True)
    b = tuval.state_items["b"]
    surukle(tuval, b, -1.0, 0.0)
    check(abs(b.pos().x() - 137.0) < 0.01,
          "izgara aciksa bile komsunun GERCEK kenarina oturuyor",
          "b.x = %.2f (izgara 140 derdi)" % b.pos().x())
    tuval.close()

    # -- KAPATILABILIR ---------------------------------------------------- #
    tuval = durum_tuvali([("a", 100.0, 100.0, 120.0, 60.0, None),
                          ("b", 103.0, 400.0, 120.0, 60.0, None)])
    tuval.align_enabled = False
    b = tuval.state_items["b"]
    surukle(tuval, b, -1.0, 0.0)
    check(abs(b.pos().x() - 102.0) < 0.01,
          "kapaliyken hizalama yapilmiyor", "b.x = %.2f" % b.pos().x())
    check(not tuval._align_guides, "kapaliyken kilavuz cizilmiyor")
    tuval.close()

    # -- ESIK EKRANDA sabit ----------------------------------------------- #
    #
    # Sahne biriminde sabitlenseydi uzaklasinca her sey birbirine yapisir,
    # yakinlasinca hicbir sey yakalanmazdi.
    tuval = durum_tuvali([("a", 100.0, 100.0, 120.0, 60.0, None),
                          ("b", 400.0, 400.0, 120.0, 60.0, None)])
    komsular = [(100.0, 100.0, 120.0, 60.0)]
    for olcek, sapma, beklenen in ((1.0, 3.0, True), (1.0, 30.0, False),
                                   (4.0, 3.0, False), (0.25, 20.0, True)):
        esik = tuval.ALIGN_PX / olcek
        kaydirma, hedef = tuval._align_eksen(100.0 + sapma, 120.0,
                                             komsular, 0, esik)
        check((hedef is not None) == beklenen,
              "olcek %.2f, %.0f px sapma -> %s"
              % (olcek, sapma, "yakalar" if beklenen else "yakalamaz"),
              "esik %.2f sahne px" % esik)
    tuval.close()

    # -- SINIF TUVALINDE de calisir --------------------------------------- #
    cm = ClassModel(name="D", prefix="d")
    for cid, x, y in (("a", 100.0, 100.0), ("b", 102.0, 400.0)):
        cm.add_class(UmlClass(id=cid, name=cid.upper(), x=x, y=y,
                              w=160.0, h=90.0))
    ctuval = ClassCanvas(Document(cm))
    ctuval.auto_edit = False
    ctuval.resize(900, 700)
    ctuval.show()
    ctuval.rebuild()
    ctuval.tool = ClassTool.SELECT
    ctuval.snap_enabled = False
    for _ in range(8):
        app.processEvents()
    cb = ctuval.class_items["b"]
    surukle(ctuval, cb, -1.0, 0.0)
    check(abs(cb.pos().x() - 100.0) < 0.01,
          "sinif tuvalinde de hizalaniyor", "b.x = %.2f" % cb.pos().x())
    check(len(ctuval._align_guides) == 1, "sinif tuvalinde kilavuz cizilyor")
    surukle(ctuval, cb, 0.0, 0.0, birak=True)
    check(not ctuval._align_guides, "sinif tuvalinde kilavuz siliniyor")
    ctuval.close()

# --------------------------------------------------------------------------- #
# 37. TUVAL BOLGEYI MODELE YAZIYOR MU
#
# Bu bolum, iz karsilastiran anlambilim testinin YAPISAL OLARAK goremedigi
# bir kusur sinifini olcer. `test_semantics.py` modeli ELIYLE kurar; tuvalin
# modele ne yazdigina hic bakmaz. Bolge numarasi yanlis yazilirsa uc
# gerceklestirme de AYNI yanlis modeli calistirir ve test yesil kalir --
# oysa uretilen `state_region[]` tablosu kullanicinin cizdigi resimle
# celisir. Olculen sey su tek degismezdir: MODELDEKI BOLGE, OGENIN CIZILDIGI
# SERITTIR.
# --------------------------------------------------------------------------- #

def test_region_authoring() -> None:
    print("\n== 37. tuval bolgeyi modele yaziyor mu ==")

    from PyQt6.QtCore import QPoint, QPointF, Qt
    from PyQt6.QtGui import QMouseEvent
    from PyQt6.QtWidgets import QApplication

    from app.core.model import State, StateKind, StateMachine
    from app.ui.canvas import DiagramCanvas, Tool
    from app.ui.document import Document

    app = QApplication.instance() or QApplication([])

    def ortogonal_tuval():
        """Iki bolgeli bir bilesik durum ve disarida bir basit durum."""
        sm = StateMachine(name="M", prefix="m", context_type="void")
        sm.add_state(State(id="par", name="Par", kind=StateKind.COMPOSITE,
                           x=260.0, y=60.0, w=420.0, h=360.0, regions=2))
        sm.add_state(State(id="out", name="Out", kind=StateKind.SIMPLE,
                           x=30.0, y=470.0, w=120.0, h=60.0))
        tuval = DiagramCanvas(Document(sm))
        tuval.auto_edit = False
        tuval.resize(900, 700)
        tuval.show()
        tuval.rebuild()
        tuval.snap_enabled = False
        for _ in range(8):
            app.processEvents()
        return tuval

    def serit_merkezi(oge, bolge):
        """`bolge` seridinin YEREL dikey orta noktasi."""
        alan = oge.content_rect()
        sayi = oge.region_count()
        yukseklik = alan.height() / float(sayi)
        return alan.top() + yukseklik * (float(bolge) + 0.5)

    def cizildigi_serit(tuval, durum):
        """Modeldeki koordinattan, ogenin GORUNDUGU serit."""
        ust = tuval.state_items.get(durum.parent)
        if ust is None or ust.region_count() <= 1:
            return 0
        return ust.region_at(durum.y + durum.h / 2.0)

    def fare(tuval, tip, konum, dugme):
        q = QPointF(konum)
        app.sendEvent(tuval.viewport(), QMouseEvent(
            tip, q, tuval.viewport().mapToGlobal(q), dugme,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        app.processEvents()

    # ---------------------------------------------------------- palet yolu
    tuval = ortogonal_tuval()
    par = tuval.state_items["par"]
    onceki = set(tuval.doc.machine.states)
    tuval.tool = Tool.STATE
    hedef = par.mapToScene(QPointF(par.content_rect().center().x(),
                                   serit_merkezi(par, 1)))
    fare(tuval, QMouseEvent.Type.MouseButtonPress,
         tuval.mapFromScene(hedef), Qt.MouseButton.LeftButton)
    eklenen = [d for i, d in tuval.doc.machine.states.items() if i not in onceki]
    check(len(eklenen) == 1, "paletten birakma bir durum ekliyor",
          "eklenen = %d" % len(eklenen))
    if eklenen:
        d = eklenen[0]
        check(d.parent == "par", "paletten birakilan oge bilesige giriyor",
              "parent = %r" % d.parent)
        check(d.region == 1,
              "paletten IKINCI serite birakilan durum region=1 aliyor",
              "region = %d" % d.region)
        check(d.region == cizildigi_serit(tuval, d),
              "palet: modeldeki bolge, cizildigi serit ile ayni")
    tuval.close()

    # ------------------------------------------------- surukleyip birakma yolu
    tuval = ortogonal_tuval()
    par = tuval.state_items["par"]
    out = tuval.state_items["out"]
    tuval.tool = Tool.SELECT
    out.setSelected(True)
    hedef = par.mapToScene(QPointF(par.content_rect().center().x(),
                                   serit_merkezi(par, 1)))
    bas = tuval.mapFromScene(out.mapToScene(out.rect().center()))
    son = tuval.mapFromScene(hedef)
    ara = QPoint((bas.x() + son.x()) // 2, (bas.y() + son.y()) // 2)
    fare(tuval, QMouseEvent.Type.MouseButtonPress, bas,
         Qt.MouseButton.LeftButton)
    fare(tuval, QMouseEvent.Type.MouseMove, ara, Qt.MouseButton.NoButton)
    fare(tuval, QMouseEvent.Type.MouseMove, son, Qt.MouseButton.NoButton)
    fare(tuval, QMouseEvent.Type.MouseButtonRelease, son,
         Qt.MouseButton.LeftButton)
    d = tuval.doc.machine.states["out"]
    check(d.parent == "par", "surukleme ogeyi bilesigin icine aliyor",
          "parent = %r" % d.parent)
    check(d.region == 1, "IKINCI serite surukleneni region=1 olarak yaziyor",
          "region = %d" % d.region)
    check(d.region == cizildigi_serit(tuval, d),
          "surukleme: modeldeki bolge, cizildigi serit ile ayni")
    tuval.close()

    # ------------------------------------------------------ yapistirma yolu
    #
    # Yapistirma, parcayi BIRAKMA NOKTASINA koyar ve ust durumun ic
    # alanina sigdirir. Eskiden parcanin KENDI eski koordinati
    # kullaniliyordu: kok bolgede asagida duran bir durumu bilesigin
    # icine yapistirmak, onu kutunun yuzlerce piksel disina koyuyordu --
    # kullanici "yapistirdim, hicbir sey olmadi" diyordu. Bolge numarasi
    # da o zaman kelepcelenmis, anlamsiz bir degerdi.
    for oran, beklenen in ((0.25, 0), (0.75, 1)):
        tuval = ortogonal_tuval()
        par = tuval.state_items["par"]
        alan = par.content_rect()
        hedef = par.mapToScene(QPointF(alan.center().x(),
                                       alan.top() + alan.height() * oran))
        tuval.state_items["out"].setSelected(True)
        check(tuval.copy_selection(), "kopyalama basarili")
        onceki = set(tuval.doc.machine.states)
        # Hedefi ACIKCA ver: gercek fare imlecinin nerede oldugu testin
        # denetiminde degildir ve yapistirma yolu olculemez kalirdi.
        tuval.paste_clipboard(hedef)
        eklenen = [d for i, d in tuval.doc.machine.states.items()
                   if i not in onceki]
        check(len(eklenen) == 1, "yapistirma bir durum ekliyor",
              "eklenen = %d" % len(eklenen))
        for d in eklenen:
            check(d.parent == "par", "yapistirilan kok bilesige giriyor",
                  "parent = %r" % d.parent)
            ust = tuval.state_items["par"]
            ic = ust.content_rect()
            icinde = (ic.left() <= d.x and d.x + d.w <= ic.right()
                      and ic.top() <= d.y and d.y + d.h <= ic.bottom())
            check(icinde, "yapistirilan kopya ust durumun ICINDE ciziliyor",
                  "yerel = (%.0f, %.0f), ic alan = (%.0f..%.0f, %.0f..%.0f)"
                  % (d.x, d.y, ic.left(), ic.right(), ic.top(), ic.bottom()))
            check(d.region == beklenen,
                  "%.2f oranina birakilan kopya region=%d aliyor"
                  % (oran, beklenen), "region = %d" % d.region)
            check(d.region == cizildigi_serit(tuval, d),
                  "yapistirma: modeldeki bolge, cizildigi serit ile ayni",
                  "region = %d, serit = %d"
                  % (d.region, cizildigi_serit(tuval, d)))
        tuval.close()

# --------------------------------------------------------------------------- #
# 38. KOD URETECI SAGLAMLIGI
#
# Buradaki iki kontrol de IZ KARSILASTIRMASIYLA OLCULEMEZ:
#
#  - Zamanlayici kancalari testte BOS birakilir ve olayi test dizisi elle
#    gonderir; kanca hic uretilmese bile izler ayni cikar. Kancanin VAR
#    OLUP OLMADIGI ancak uretilen metne bakarak anlasilir.
#  - Kod uretiminin COKMESI bir iz farki degildir; hic dosya ciktisi
#    olmaz.
# --------------------------------------------------------------------------- #

def test_codegen_robustness() -> None:
    print("\n== 38. kod ureteci saglamligi ==")

    from app.core.model import (State, StateKind, StateMachine, Transition,
                                TransitionKind)
    from app.codegen.c_generator import generate_c
    from app.codegen.cpp_generator import generate_cpp

    # ------------------------------------------- #6 ic gecisteki after(N)
    def orneklemeli(kind):
        sm = StateMachine(name="Sampler", prefix="samp", context_type="void")
        sm.add_state(State(id="i", name="I", kind=StateKind.INITIAL))
        sm.add_state(State(id="r", name="Run", kind=StateKind.SIMPLE))
        sm.add_transition(Transition(id="t0", source="i", target="r"))
        sm.add_transition(Transition(id="t1", source="r", target="r",
                                     event="after(50)", kind=kind,
                                     action="sample(ctx);"))
        return sm

    c = generate_c(orneklemeli(TransitionKind.INTERNAL))["samp.c"]
    check("samp_timers_start" in c,
          "ic gecisteki after(N) icin zamanlayici baslatma kancasi uretiliyor")
    check("samp_timer_start(me, (uint8_t)SAMP_STATE_RUN," in c,
          "kanca DOGRU durumu adlandiriyor")
    check("SAMP_EVENT_AFTER50" in c, "kanca DOGRU olayi adlandiriyor")
    check("samp_timer_cancel(me, (uint8_t)SAMP_STATE_RUN," in c,
          "cikista zamanlayici iptal ediliyor")

    cpp = generate_cpp(orneklemeli(TransitionKind.INTERNAL))["Sampler.cpp"]
    check("void Sampler::timersStart" in cpp,
          "C++ tarafinda da baslatma kancasi uretiliyor")
    check("Event::After50" in cpp, "C++ kancasi dogru olayi adlandiriyor")

    # Ic gecis ATESLENINCE zamanlayici YENIDEN BASLAMAMALIDIR: baslatma
    # yalnizca enter_one icinde olur, gecis alma yolunda degil.
    govde = c[c.index("static void samp_take("):]
    govde = govde[:govde.index("\n}\n")]
    check("samp_timers_start" not in govde,
          "ic gecis atesleyince zamanlayici YENIDEN baslatilmiyor")

    # Disa vuran (external) kendine gecis zamanlayiciyi yeniden baslatir:
    # cikis ve giris calistigi icin bu kendiliginden olur.
    c_dis = generate_c(orneklemeli(TransitionKind.EXTERNAL))["samp.c"]
    check("samp_timers_start" in c_dis,
          "dis kendine gecis icin de kanca uretiliyor")

    # ------------- gecikme IFADESI parametre tipine cevriliyor mu
    #
    # `after(50)` gibi bir sabit sorun cikarmaz; derleyici degeri bilir.
    # Ama `after(timeout_ms())` ya da `after(g_timeout)` int dondurur ve
    # uint32_t parametreye donusturulmeden gecerse -Wsign-conversion ile
    # derleme DURUR -- tam da README'nin garanti ettigi bayraklarla.
    def gecikmeli(ifade):
        sm = StateMachine(name="Delay", prefix="delay", context_type="void")
        sm.add_state(State(id="i", name="I", kind=StateKind.INITIAL))
        sm.add_state(State(id="r", name="Run", kind=StateKind.SIMPLE))
        sm.add_state(State(id="d", name="Done", kind=StateKind.SIMPLE))
        sm.add_transition(Transition(id="t0", source="i", target="r"))
        sm.add_transition(Transition(id="t1", source="r", target="d",
                                     event="after(%s)" % ifade))
        sm.add_transition(Transition(id="t2", source="d", target="r",
                                     event="AGAIN"))
        return sm

    c_gecikme = generate_c(gecikmeli("timeout_ms()"))["delay.c"]
    check("(uint32_t)(timeout_ms())" in c_gecikme,
          "C: gecikme ifadesi uint32_t'ye cevriliyor",
          [ln.strip() for ln in c_gecikme.splitlines()
           if "delay_timer_start(me" in ln][:1])
    cpp_gecikme = generate_cpp(gecikmeli("timeout_ms()"))["Delay.cpp"]
    check("static_cast<std::uint32_t>(timeout_ms())" in cpp_gecikme,
          "C++: gecikme ifadesi uint32_t'ye cevriliyor",
          [ln.strip() for ln in cpp_gecikme.splitlines()
           if "timerStart(*this" in ln][:1])

    # -------- tamamlanma butcesi BOLGE BASINA mi (makine capinda degil)
    #
    # Dongu her adimda TEK BIR BOLGEYI isler. Makine capinda sabit bir
    # butce, bolge sayisi arttikca paylasiliyor ve 8 bolgeden sonra
    # tukeniyordu: diyagramda cizili tamamlanma gecisleri HIC alinmiyor,
    # uretilen kod da bunu bildirmiyordu.
    c_ortho_h = generate_c(_stil_ortogonal())["styortho.h"]
    check("16U * STYORTHO_REGION_COUNT" in c_ortho_h,
          "C: tamamlanma butcesi bolge sayisiyla olcekleniyor",
          [ln.strip() for ln in c_ortho_h.splitlines()
           if "MAX_RUN_TO_COMPLETION_STEPS" in ln][:2])
    cpp_ortho = generate_cpp(_stil_ortogonal())["StyleOrtho.hpp"]
    check("16U * kRegionCount" in cpp_ortho,
          "C++: tamamlanma butcesi bolge sayisiyla olcekleniyor")

    # ---------------- sonlanmis makine ERTELENMIS olay islemez (C)
    c_defer = generate_c(_stil_ortogonal())["styortho.c"]
    drain = c_defer[c_defer.index("styortho_drain_deferred(styortho_t *me)\n{"):]
    drain = drain[:drain.index("\n}\n")]
    check("me->terminated" in drain,
          "C: ertelenmis olay bosaltimi terminate'te duruyor",
          "govde: %s" % drain[:160])

    # ------- C++ kurucusu active_ dizisini kNone ile dolduruyor mu
    cpp_ctor = generate_cpp(_stil_ortogonal())["StyleOrtho.cpp"]
    govde = cpp_ctor[cpp_ctor.index("StyleOrtho::StyleOrtho("):]
    govde = govde[:govde.index("\n}\n")]
    check("active_[region] = kNone;" in govde,
          "C++ kurucusu active_ dizisini kNone ile dolduruyor",
          "0 GECERLI bir durum indisidir; deger-baslatma onu etkin gosterir")

    # ---------------- URETILEN METINDE bicimlenmemis yer tutucu kalmamali
    #
    # Uretec satirlari "%s" ile kuruluyor; bir satirda `% P` unutulunca
    # yer tutucu URETILEN DOSYAYA oldugu gibi giriyor. Musteri kaynakta
    # "%s_MAX_RUN_TO_COMPLETION_STEPS" goruyor -- derleme gecer, cunku
    # satir bir yorumdur, ve hicbir test bunu yakalamiyordu.
    for _kur in (_stil_ortogonal, _stil_tarih, _stil_zamanli):
        _m = _kur()
        for _ad, _metin in list(generate_c(_m).items()) + list(
                generate_cpp(_m).items()):
            _kacak = [(_no, _l.strip())
                      for _no, _l in enumerate(_metin.splitlines(), 1)
                      if "%s" in _l or "%d" in _l or "%u" in _l]
            check(not _kacak, "%s: bicimlenmemis yer tutucu yok" % _ad,
                  "\n".join("satir %d: %s" % x for x in _kacak[:3]))

    # ------------------------------- #11 tirnaksiz ilk include satiri
    def baslikli(includes):
        sm = StateMachine(name="M", prefix="m", context_type="my_ctx_t",
                          user_includes=includes)
        sm.add_state(State(id="i", name="I", kind=StateKind.INITIAL))
        sm.add_state(State(id="a", name="A", kind=StateKind.SIMPLE,
                           entry="act(ctx);"))
        sm.add_transition(Transition(id="t", source="i", target="a"))
        return sm

    for metin, beklenen in (
            ('#include "mytypes.h"', "mytypes.h"),
            ("#include <mytypes.h>", "mytypes.h"),
            ("/* only a comment */", "your own header"),
            ("", "your own header"),
            ("/* note */\n#include <late.h>", "late.h"),
    ):
        try:
            uretilen = generate_c(baslikli(metin))
        except Exception as hata:                       # noqa: BLE001
            check(False, "include %r ile kod uretiliyor" % metin,
                  "%r" % hata)
            continue
        check(True, "include %r ile kod uretiliyor" % metin)
        notlar = [satir for dosya in uretilen.values()
                  for satir in dosya.splitlines() if "Define it in" in satir]
        check(bool(notlar) and beklenen in notlar[0],
              "include %r icin baslik adi %r olarak yaziliyor"
              % (metin, beklenen),
              notlar[0].strip() if notlar else "not yok")


# --------------------------------------------------------------------------- #
# 39. TUR DEGISTIRME PANELI
#
# Bir durumun turunu degistirmek YALNIZCA bir alan yazmak degildir: olcu,
# davranislar ve turle birlikte anlamini yitiren alanlar da temizlenmeli,
# form da yeni turun satirlariyla yeniden kurulmalidir. Hicbiri iz
# karsilastirmasiyla gorunmez -- model zaten "gecerli" gorunur, sadece
# kullanicinin cizdiginden baska bir seydir.
# --------------------------------------------------------------------------- #

def test_kind_change_panel() -> None:
    print("\n== 39. tur degistirme paneli ==")

    from PyQt6.QtWidgets import QApplication, QComboBox, QLineEdit

    from app.core.model import State, StateKind, StateMachine
    from app.ui.document import Document
    from app.ui.inspector import PSEUDO_SIZES, Inspector

    app = QApplication.instance() or QApplication([])

    def panel(kind=StateKind.SIMPLE, **ek):
        sm = StateMachine(name="M", prefix="m", context_type="void")
        sm.add_state(State(id="s", name="S", kind=kind,
                           x=0.0, y=0.0, w=170.0, h=84.0,
                           entry="on_entry(ctx);", exit="on_exit(ctx);",
                           do="tick(ctx);", deferred=["PRINT"], **ek))
        gozlemci = Inspector(Document(sm))
        gozlemci.resize(320, 700)
        gozlemci.show()
        gozlemci.show_selection(["s"])
        app.processEvents()
        return gozlemci

    def alan(gozlemci, ad):
        for w in gozlemci.findChildren(QLineEdit):
            if w.objectName() == ad:
                return w
        return None

    def odakla(gozlemci, w):
        """`w`ye odagi verir ve GERCEKTEN aldigini dogrular.

        `QApplication.focusWidget()` yalnizca ETKIN PENCEREDEKI odagi
        bildirir; pencere etkin degilse None doner. Bu bolumun tum
        onermeleri `refresh()`in odaga bakan dalini olctugu icin, odak
        kurulamadiginda testler SESSIZCE yanlis sebeple gecer ya da
        duser. On kosul burada acikca dogrulanir.
        """
        gozlemci.raise_()
        gozlemci.activateWindow()
        w.setFocus()
        for _ in range(4):
            app.processEvents()
        return QApplication.focusWidget() is w

    # ------------------------------- #10 olcu, davranis ve erteleme temizligi
    for hedef in (StateKind.FORK, StateKind.JOIN,
                  StateKind.ENTRY_POINT, StateKind.EXIT_POINT,
                  StateKind.JUNCTION):
        gozlemci = panel()
        gozlemci._change_kind("s", hedef)
        app.processEvents()
        st = gozlemci.doc.machine.states["s"]
        check(st.kind is hedef, "%s turune geciliyor" % hedef.value,
              "kind = %s" % st.kind.value)
        check((st.w, st.h) == PSEUDO_SIZES[hedef],
              "%s DOGRU olcuyu aliyor" % hedef.value,
              "olcu = (%.1f, %.1f)" % (st.w, st.h))
        check(not st.entry and not st.exit and not st.do,
              "%s davranislari temizleniyor" % hedef.value,
              "entry=%r exit=%r do=%r" % (st.entry, st.exit, st.do))
        check(not st.deferred,
              "%s ertelenen olaylari temizleniyor" % hedef.value,
              "deferred = %r" % (st.deferred,))
        gozlemci.close()

    # Alt makine adi, tur degisince birakilmamali.
    gozlemci = panel(kind=StateKind.SUBMACHINE, submachine_ref="Other")
    gozlemci._change_kind("s", StateKind.SIMPLE)
    app.processEvents()
    check(not gozlemci.doc.machine.states["s"].submachine_ref,
          "alt makine adi tur degisince temizleniyor",
          "ref = %r" % gozlemci.doc.machine.states["s"].submachine_ref)
    gozlemci.close()

    # ------------------------------------- #13 form YENI turle kuruluyor mu
    gozlemci = panel()
    check(alan(gozlemci, "state_deferred") is not None,
          "gercek durumda Defers satiri var")
    kutular = [w for w in gozlemci.findChildren(QComboBox)
               if w.accessibleName() == "State kind" or w.count() > 3]
    check(bool(kutular), "Kind acilir kutusu bulunuyor")
    if not kutular:
        pass
    elif not odakla(gozlemci, kutular[0]):
        print("  [ATLANDI] Kind kutusuna odak verilemedi (pencere etkin degil)")
    else:
        # Tur degistiren kullanicinin odagi ZATEN bu kutudadir; tazeleme
        # bu yuzden atlaniyordu.
        gozlemci._change_kind("s", StateKind.JUNCTION)
        app.processEvents()
        # Ana pencere her model degisiminden sonra `_rebuild_views` icinde
        # `inspector.refresh()` cagirir (main_window.py); panel tek basina
        # sinandigi icin o baglantiyi burada taklit ediyoruz.
        gozlemci.refresh()
        app.processEvents()
        check(alan(gozlemci, "state_deferred") is None,
              "sozde-duruma cevrilince Defers satiri KALKIYOR")
    gozlemci.close()

    # DONDURME KUTUSU da korunmali: formu degistirmez ama yeniden
    # kurulmak onu YOK EDER. Kullanici yukari okuna her bastiginda
    # bilesen silinip yeniden yaratiliyor, odak kayboluyor ve klavye
    # izleme kapali oldugu icin yarim yazilmis deger de atiliyordu.
    from PyQt6.QtWidgets import QSpinBox

    sm_bolge = StateMachine(name="B", prefix="b", context_type="void")
    sm_bolge.add_state(State(id="p", name="Par", kind=StateKind.COMPOSITE,
                             regions=2, x=0.0, y=0.0, w=400.0, h=400.0))
    gozlemci = Inspector(Document(sm_bolge))
    gozlemci.resize(320, 700)
    gozlemci.show()
    gozlemci.show_selection(["p"])
    app.processEvents()
    kutular2 = [w for w in gozlemci.findChildren(QSpinBox)
                if w.accessibleName() == "Region count"]
    check(bool(kutular2), "Regions dondurme kutusu bulundu")
    if kutular2 and odakla(gozlemci, kutular2[0]):
        kutular2[0].stepUp()
        app.processEvents()
        gozlemci.refresh()
        app.processEvents()
        sonra = [w for w in gozlemci.findChildren(QSpinBox)
                 if w.accessibleName() == "Region count"]
        check(bool(sonra) and sonra[0] is kutular2[0],
              "dondurme kutusu odaktayken form YENIDEN KURULMUYOR")
        check(QApplication.focusWidget() is kutular2[0],
              "dondurme kutusunda odak korunuyor")
        check(gozlemci.doc.machine.states["p"].regions == 3,
              "deger yine de modele yazildi",
              "regions = %d" % gozlemci.doc.machine.states["p"].regions)
    elif kutular2:
        print("  [ATLANDI] dondurme kutusuna odak verilemedi")
    gozlemci.close()

    # Metin alanina yazarken tazeleme HALA atlanmali: yazilan sey
    # tusa basildikca sifirlanmamalidir.
    gozlemci = panel()
    metin = alan(gozlemci, "state_deferred")
    check(metin is not None, "Defers alani bulunuyor")
    if metin is None:
        pass
    elif not odakla(gozlemci, metin):
        print("  [ATLANDI] Defers alanina odak verilemedi (pencere etkin degil)")
    else:
        metin.setText("YAZILIYOR")
        app.processEvents()
        gozlemci.refresh()
        app.processEvents()
        hala = alan(gozlemci, "state_deferred")
        check(hala is metin and hala.text() == "YAZILIYOR",
              "metin alanina yazarken form sifirlanmiyor",
              "metin = %r" % (hala.text() if hala is not None else None))
    gozlemci.close()


# --------------------------------------------------------------------------- #
# 40. ALTMAKINE, OLAY SAYIMI VE BOLGE DUZENLEME
#
# Bu bolumdeki kusurlarin ORTAK yani: hicbiri uc gerceklestirmenin izini
# birbirinden ayirmiyordu. `test_semantics.py` modeli eliyle kurar ve uc
# motoru karsilastirir; modelin NASIL yazildigina, dogrulayicinin ne
# soyledigine ya da panelin ne gosterdigine bakmaz. Hepsi yesil kalirken
# uretilen kod derlenmiyor ya da kullanicinin cizdiginden baska bir sey
# yapiyordu.
# --------------------------------------------------------------------------- #

def test_submachine_events_and_regions() -> None:
    print("\n== 40. altmakine, olay sayimi ve bolge duzenleme ==")

    from PyQt6.QtWidgets import QApplication, QCheckBox, QLabel, QSpinBox

    from app.codegen.ir import CodegenError, MAX_EVENTS, build_ir
    from app.core.model import (State, StateKind, StateMachine, Transition,
                                TransitionKind)
    from app.core.submachine import flatten
    from app.core.validator import validate
    from app.ui.document import Document
    from app.ui.inspector import Inspector
    from app.ui.sim_panel import SimulatorPanel

    app = QApplication.instance() or QApplication([])

    def ic_makine(ad="Inner", includes="", koruma=""):
        sm = StateMachine(name=ad, prefix="inner", context_type="void",
                          user_includes=includes)
        sm.add_state(State(id="i", name="I", kind=StateKind.INITIAL))
        sm.add_state(State(id="g", name="Go", kind=StateKind.SIMPLE,
                           entry="inner_begin();"))
        sm.add_state(State(id="s", name="Stop", kind=StateKind.SIMPLE))
        sm.add_transition(Transition(id="t", source="i", target="g"))
        sm.add_transition(Transition(id="t2", source="g", target="s",
                                     event="DONE", guard=koruma))
        return sm

    def ana_makine(ic_adi="Inner", ek_durum=None, includes=""):
        sm = StateMachine(name="Main", prefix="main", context_type="void",
                          user_includes=includes)
        sm.add_state(State(id="mi", name="MI", kind=StateKind.INITIAL))
        sm.add_state(State(id="idle", name="Idle", kind=StateKind.SIMPLE))
        sm.add_state(State(id="sub", name="Sub", kind=StateKind.SUBMACHINE,
                           submachine_ref=ic_adi))
        if ek_durum:
            sm.add_state(State(id="x", name=ek_durum, kind=StateKind.SIMPLE))
        sm.add_transition(Transition(id="a", source="mi", target="sub"))
        sm.add_transition(Transition(id="b", source="sub", target="idle",
                                     event="E"))
        sm.add_transition(Transition(id="c", source="idle", target="sub",
                                     event="F"))
        return sm

    def cozumleyici(ic):
        return lambda ref: ic if ref.strip().lower() == ic.name.lower() else None

    def cok_olayli(sayi):
        """`sayi` kadar AYRI olay turu bildiren makine."""
        sm = StateMachine(name="M", prefix="m", context_type="void")
        sm.add_state(State(id="i", name="I", kind=StateKind.INITIAL))
        sm.add_state(State(id="a", name="A", kind=StateKind.SIMPLE))
        sm.add_transition(Transition(id="t0", source="i", target="a"))
        for k in range(sayi):
            sm.add_transition(Transition(id="t%d" % (k + 1), source="a",
                                         target="a", event="EV%d" % k,
                                         kind=TransitionKind.INTERNAL))
        return sm

    # -------------------------- #9 genisletme SONRASI ad cakismasi yakalaniyor
    ic = ic_makine()
    hatalar = [i for i in validate(ana_makine(ek_durum="Sub_Go"),
                                   resolve=cozumleyici(ic))
               if i.severity == "error"]
    check(any(i.code == "V164" for i in hatalar),
          "genisletmeden DOGAN ad cakismasi V164 ile bildiriliyor",
          "kodlar = %s" % [i.code for i in hatalar])
    temiz = [i for i in validate(ana_makine(), resolve=cozumleyici(ic))
             if i.severity == "error"]
    check(not temiz, "cakismayan altmakine modeli temiz dogrulaniyor",
          "hatalar = %s" % [(i.code, i.message) for i in temiz])

    # ------------- altmakinenin BAGLAM TIPI sessizce atilmamali
    #
    # Yerine koyma, ic makinenin entry/exit/do ve koruma metinlerini aynen
    # tasir; oradaki `ctx`, genisletilmis makinenin baglam tipiyle
    # derlenir. Iki tip farkliysa ic makinenin kodu YANLIS YAPIYA karsi
    # derleniyordu ve dogrulama buna hicbir sey demiyordu.
    def baglamli(ic_ctx, dis_ctx):
        _ic = StateMachine(name="Inner", prefix="inner", context_type=ic_ctx)
        _ic.add_state(State(id="i", name="II", kind=StateKind.INITIAL))
        _ic.add_state(State(id="g", name="Go", kind=StateKind.SIMPLE))
        _ic.add_transition(Transition(id="t", source="i", target="g"))
        _dis = StateMachine(name="Main", prefix="main", context_type=dis_ctx)
        _dis.add_state(State(id="mi", name="MI", kind=StateKind.INITIAL))
        _dis.add_state(State(id="idle", name="Idle", kind=StateKind.SIMPLE))
        _dis.add_state(State(id="sub", name="Sub", kind=StateKind.SUBMACHINE,
                             submachine_ref="Inner"))
        _dis.add_transition(Transition(id="a", source="mi", target="sub"))
        _dis.add_transition(Transition(id="b", source="sub", target="idle",
                                       event="E"))
        _dis.add_transition(Transition(id="c", source="idle", target="sub",
                                       event="F"))
        return _dis, _ic

    for ic_ctx, dis_ctx, beklenir in (("inner_ctx_t", "outer_ctx_t", True),
                                      ("ayni_t", "ayni_t", False),
                                      ("void", "outer_ctx_t", False),
                                      ("inner_ctx_t", "void", True)):
        _dis, _ic = baglamli(ic_ctx, dis_ctx)
        kodlar = [i.code for i in validate(_dis, resolve=lambda r, h=_ic: h)
                  if i.severity == "error"]
        check(("V166" in kodlar) == beklenir,
              "baglam tipi ic=%s dis=%s -> %s"
              % (ic_ctx, dis_ctx, "reddedilir" if beklenir else "kabul"),
              "kodlar = %s" % kodlar)

    # ---------------- HER BOLGEYE birer tarih sozde-durumu GECERLIDIR
    #
    # UML 2.5.1, 14.2.3.7 (basili s.312): "at most one such Pseudostate can
    # be contained in a Region of a composite State". Sinir REGION
    # basinadir. Sayim bolgeyi yok sayip yalnizca ust duruma bakiyordu:
    # uc bolgeli bir ortogonal duruma her bolge icin birer derin tarih
    # koymak -- aracin kendi paletinin tesvik ettigi sey -- ikinciden
    # itibaren reddediliyor ve model HIC kod uretemiyordu.
    def tarihli(bolgeler):
        """`bolgeler` listesindeki her numaraya bir DERIN tarih koyar."""
        m = StateMachine(name="H", prefix="h", context_type="void")
        m.add_state(State(id="i", name="I", kind=StateKind.INITIAL))
        m.add_state(State(id="p", name="Par", kind=StateKind.COMPOSITE,
                          regions=2))
        m.add_transition(Transition(id="t0", source="i", target="p"))
        for bolge, on in ((0, "a"), (1, "b")):
            m.add_state(State(id=on + "i", name=on.upper() + "I",
                              kind=StateKind.INITIAL, parent="p",
                              region=bolge))
            m.add_state(State(id=on + "0", name=on.upper() + "0",
                              kind=StateKind.SIMPLE, parent="p",
                              region=bolge))
            m.add_transition(Transition(id="t" + on, source=on + "i",
                                        target=on + "0"))
        for n, bolge in enumerate(bolgeler):
            hid = "h%d" % n
            m.add_state(State(id=hid, name="H%d" % n,
                              kind=StateKind.DEEP_HISTORY, parent="p",
                              region=bolge))
            m.add_transition(Transition(
                id="th%d" % n, source=hid,
                target=("a0" if bolge == 0 else "b0")))
        return m

    kodlar = [i.code for i in validate(tarihli([0, 1]))
              if i.severity == "error"]
    check(not kodlar, "her bolgeye BIRER derin tarih GECERLI",
          "kodlar = %s" % kodlar)
    kodlar = [i.code for i in validate(tarihli([0, 0]))
              if i.severity == "error"]
    check("V068" in kodlar, "AYNI bolgede iki derin tarih reddediliyor",
          "kodlar = %s" % kodlar)

    # -------------- altmakine hatalari DOGRU ogeye mi bagli
    #
    # Tuval her hatayi bir ogeye baglayip kirmiziya boyar. Genisletme
    # TUM MAKINE icin bir kez yapildigindan, sonucu bildiren kod hatayi
    # o anda uzerinde durdugu altmakine durumuna bagliyordu: dosyadaki
    # ILK altmakine durumu. Kullanici, kusursuz olan bir ogeye
    # yonlendiriliyor ve gercek sucluyu goremiyordu.
    def basit_ic(durum="Go"):
        m = StateMachine(name="Inner", prefix="inner", context_type="void")
        m.add_state(State(id="i0", name="IInit", kind=StateKind.INITIAL))
        m.add_state(State(id="i1", name=durum, kind=StateKind.SIMPLE))
        m.add_transition(Transition(id="it0", source="i0", target="i1"))
        return m

    def coz_inner(ref):
        if "inner" in ref.strip().lower():
            return basit_ic()
        return None

    # (a) IKI altmakine durumu, YALNIZCA ikincisinin referansi bozuk
    bozuk = StateMachine(name="Main", prefix="main", context_type="void")
    bozuk.add_state(State(id="init", name="Init", kind=StateKind.INITIAL))
    bozuk.add_state(State(id="iyi", name="Good", kind=StateKind.SUBMACHINE,
                          submachine_ref="Inner"))
    bozuk.add_state(State(id="kotu", name="Bad", kind=StateKind.SUBMACHINE,
                          submachine_ref="Missing"))
    bozuk.add_transition(Transition(id="t0", source="init", target="iyi"))
    bozuk.add_transition(Transition(id="t1", source="iyi", target="kotu",
                                    event="GO"))
    v163 = [i for i in validate(bozuk, resolve=coz_inner)
            if i.code == "V163"]
    check(bool(v163), "bozuk referans V163 uretiyor")
    for i in v163:
        check(i.element_id != "iyi",
              "V163 MASUM altmakine durumuna baglanmiyor",
              "element_id = %r" % i.element_id)
        check(i.element_id in (None, "kotu"),
              "V163 referansi bozuk olan duruma bagli",
              "element_id = %r" % i.element_id)

    # (b) Cakismayi IKINCI altmakine durumu doguruyor
    carp = StateMachine(name="Main", prefix="main", context_type="void")
    carp.add_state(State(id="init", name="Init", kind=StateKind.INITIAL))
    carp.add_state(State(id="birinci", name="Alpha",
                         kind=StateKind.SUBMACHINE, submachine_ref="Inner"))
    carp.add_state(State(id="ikinci", name="Beta",
                         kind=StateKind.SUBMACHINE, submachine_ref="Inner"))
    carp.add_state(State(id="carpisan", name="Beta_Go",
                         kind=StateKind.SIMPLE))
    carp.add_transition(Transition(id="t0", source="init", target="birinci"))
    carp.add_transition(Transition(id="t1", source="birinci", target="ikinci",
                                   event="GO"))
    carp.add_transition(Transition(id="t2", source="ikinci", target="carpisan",
                                   event="DONE"))
    v164 = [i for i in validate(carp, resolve=coz_inner) if i.code == "V164"]
    check(bool(v164), "genisletme cakismasi V164 uretiyor")
    for i in v164:
        check(i.element_id == "ikinci",
              "V164 cakismayi DOGURAN altmakine durumuna bagli",
              "element_id = %r (beklenen 'ikinci' = Beta)" % i.element_id)

    # ---------------- genisletmeden gelen OLAY adlari da denetleniyor mu
    #
    # Genisletme durum adlarini niteler (`Disari_Iceri`) ama OLAY adlarini
    # OLDUGU GIBI birakir. Olay kurallari genisletilmemis modele bakinca
    # disaridaki `DO_IT` ile icerideki `DoIt` hic karsilasmiyor: dogrulama
    # tertemiz geciyor, uretilen baslikta ayni sabit iki kez tanimlaniyor
    # ve derleme "redeclaration of enumerator" ile duruyordu. Ayni acik
    # uretec sabiti (V014/V017), gecersiz ad (V012) ve zaman olayi
    # (V190/V191) kurallarini da altmakineden gelen olaylar icin devre
    # disi birakiyordu.
    def olayli_ic(ic_olay):
        m = StateMachine(name="Inner", prefix="inner", context_type="void")
        m.add_state(State(id="i0", name="IInit", kind=StateKind.INITIAL))
        m.add_state(State(id="i1", name="IA", kind=StateKind.SIMPLE))
        m.add_state(State(id="i2", name="IB", kind=StateKind.SIMPLE))
        m.add_transition(Transition(id="it0", source="i0", target="i1"))
        m.add_transition(Transition(id="it1", source="i1", target="i2",
                                    event=ic_olay))
        m.add_transition(Transition(id="it2", source="i2", target="i1",
                                    event="IBACK"))
        return m

    def olayli_dis(dis_olay):
        m = StateMachine(name="Main", prefix="main", context_type="void")
        m.add_state(State(id="init", name="Init", kind=StateKind.INITIAL))
        m.add_state(State(id="idle", name="Idle", kind=StateKind.SIMPLE))
        m.add_state(State(id="sub", name="Sub", kind=StateKind.SUBMACHINE,
                          submachine_ref="Inner"))
        m.add_transition(Transition(id="t0", source="init", target="idle"))
        m.add_transition(Transition(id="t1", source="idle", target="sub",
                                    event=dis_olay))
        m.add_transition(Transition(id="t2", source="sub", target="idle",
                                    event="F"))
        return m

    for etiket, d_olay, i_olay, beklenen in (
            ("buyuk/kucuk harf cakismasi", "DO_IT", "DoIt", ["V015", "V016"]),
            ("ayni olay iki makinede",     "SAME", "SAME", []),
            ("cakisma yok",                "OUTER", "INNER", []),
            ("ic olay uretec sabiti",      "OUTER", "COMPLETION",
             ["V014", "V017"]),
            ("ic zaman olayi gecikmesiz",  "OUTER", "after()", ["V190"]),
            ("ic zaman olayi saglam",      "OUTER", "after(250)", []),
            ("ic olay gecersiz ad",        "OUTER", "2bad", ["V012"]),
    ):
        _dis = olayli_dis(d_olay)
        _ic = olayli_ic(i_olay)
        kodlar = sorted({i.code for i in validate(_dis, resolve=lambda r, h=_ic: h)
                         if i.severity == "error"})
        check(kodlar == sorted(beklenen),
              "altmakine olayi: %s" % etiket,
              "beklenen %s, cikan %s" % (sorted(beklenen), kodlar))
        # Hata ogesi KULLANICININ modelinde olmali; aksi halde tuval onu
        # isaretleyemez ve kullanici hatanin nerede oldugunu goremez.
        for i in validate(_dis, resolve=lambda r, h=_ic: h):
            if i.severity != "error":
                continue
            check(i.element_id in _dis.states or i.element_id in _dis.transitions,
                  "%s hatasi kullanici modelindeki bir ogeye bagli" % i.code,
                  "element_id = %r" % i.element_id)

    # ------------------------------------- #20 ic makinenin include satirlari
    ic2 = ic_makine(includes='#include "inner_api.h"')
    ana = ana_makine(includes='#include "main_api.h"')
    duz = flatten(ana, cozumleyici(ic2))
    check("inner_api.h" in (duz.user_includes or ""),
          "altmakinenin include satiri genisletilmis modele tasiniyor",
          "includes = %r" % duz.user_includes)
    check("main_api.h" in (duz.user_includes or ""),
          "disaridaki makinenin include satiri korunuyor")
    check("main_api.h" in (ana.user_includes or "")
          and "inner_api.h" not in (ana.user_includes or ""),
          "KULLANICININ belgesi degistirilmiyor",
          "ana = %r" % ana.user_includes)
    # Ayni satir iki kez yazilmamali.
    ic3 = ic_makine(includes='#include "main_api.h"')
    duz3 = flatten(ana_makine(includes='#include "main_api.h"'),
                   cozumleyici(ic3))
    check((duz3.user_includes or "").count("main_api.h") == 1,
          "ayni include satiri iki kez yazilmiyor",
          "includes = %r" % duz3.user_includes)

    # ------------------------- #12 benzetim paneli altmakine ICINI de goruyor
    ic4 = ic_makine(koruma="inner_ready(ctx)")
    belge = Document(ana_makine())
    panel = SimulatorPanel(belge)
    panel.window().submachine_resolver = lambda _ic=ic4: cozumleyici(_ic)
    panel.rebuild()
    app.processEvents()
    # Korumalar artik onay kutusu degil, bir tablo satiridir; iddia ayni:
    # ALTMAKINE ICINDEKI koruma da listede gorunmelidir.
    kutular = [panel.guard_table.item(r, 0).text()
               for r in range(panel.guard_table.rowCount())]
    check(any("inner_ready" in t for t in kutular),
          "altmakine ICINDEKI koruma, koruma listesinde gorunuyor",
          "listelenen = %s" % kutular)
    panel.close()

    # Kod uretimi COKERSE hata GORUNMELI; sessizce "(no guard)" yazilmamali.
    #
    # Tehlikeli sekil, modelin DOGRULAMADAN GECIP kod uretiminde
    # cokmesidir: panel o zaman ACIK kalir, "(no guard)" yazar ve
    # `_eval_guard` her ifadeye True dondugu icin benzetim korumali her
    # gecisi alir. (Dogrulamanin zaten reddettigi bir model panelde
    # kirmizi "model is not valid" ile kilitlidir; orada "(no guard)"
    # yaniltici degildir.) Olay sayisi sinirini asan model tam olarak bu
    # sekildedir: dogrulayicinin bir kurali yoktur, `build_ir` cokertir.
    asiri = cok_olayli(MAX_EVENTS + 1)
    check(not [i for i in validate(asiri) if i.severity == "error"],
          "olcut model dogrulamadan GECIYOR (panel acik kalir)")
    panel2 = SimulatorPanel(Document(asiri))
    panel2.rebuild()
    app.processEvents()
    etiketler = [w.text() for w in panel2.findChildren(QLabel)]
    check(not any(t.strip() == "(no guard)" for t in etiketler),
          "uretilemeyen model 'koruma yok' diye gosterilmiyor",
          "etiketler = %s" % etiketler)
    check(any("guards unavailable" in t for t in etiketler),
          "yerine kod uretimi hatasi gosteriliyor",
          "etiketler = %s" % etiketler)
    panel2.close()

    # ------------------------------ #14 zaman olayi enum cakismasi yakalaniyor
    def olayli(olaylar):
        sm = StateMachine(name="M", prefix="m", context_type="void")
        sm.add_state(State(id="i", name="I", kind=StateKind.INITIAL))
        sm.add_state(State(id="a", name="A", kind=StateKind.SIMPLE))
        sm.add_transition(Transition(id="t0", source="i", target="a"))
        for k, ev in enumerate(olaylar):
            sm.add_transition(Transition(id="t%d" % (k + 1), source="a",
                                         target="a", event=ev,
                                         kind=TransitionKind.INTERNAL))
        return sm

    for olaylar, beklenir in ((["after(50)", "AFTER50"], True),
                              (["after(1.5)", "after(15)"], True),
                              (["after(50)", "after(100)"], False),
                              (["after(50)"], False)):
        kodlar = [i.code for i in validate(olayli(olaylar))
                  if i.severity == "error"]
        var = "V015" in kodlar
        check(var == beklenir,
              "%s -> %s" % (olaylar, "cakisma bildiriliyor" if beklenir
                            else "temiz"),
              "kodlar = %s" % kodlar)

    # --------------------------------------------- #15 olay sayisi SINIRLI
    try:
        build_ir(cok_olayli(MAX_EVENTS))
        check(True, "%d olay kabul ediliyor" % MAX_EVENTS)
    except CodegenError as hata:
        check(False, "%d olay kabul ediliyor" % MAX_EVENTS, str(hata))
    try:
        build_ir(cok_olayli(MAX_EVENTS + 1))
        check(False, "%d olay REDDEDILIYOR" % (MAX_EVENTS + 1),
              "sinir denetlenmedi")
    except CodegenError:
        check(True, "%d olay REDDEDILIYOR" % (MAX_EVENTS + 1))

    # ---------------- #19 bolge sayisini degistirmek KAYIPSIZ olmali
    #
    # Bolge numarasi turetilmis bir degerdir: oge hangi SERITTE ciziliyorsa
    # o. Sayiyi kucultmek ogelerin KOORDINATINI degistirmez, dolayisiyla
    # yeniden buyutunce eski dagilim kendiliginden geri gelir. Eski surum
    # numarayi modele kalici olarak yaziyor ve kucultmede kelepceliyordu:
    # asagi okuna basili tutmak -- tek bir el hareketi -- uc bolgeli bir
    # diyagrami tek bolgeye yigiyor ve geri buyutmek onu GERI GETIRMIYORDU.
    from PyQt6.QtCore import QPointF
    from app.ui.canvas import DiagramCanvas

    sm = StateMachine(name="R", prefix="r", context_type="void")
    sm.add_state(State(id="i", name="I", kind=StateKind.INITIAL,
                       x=20.0, y=20.0))
    sm.add_state(State(id="p", name="Par", kind=StateKind.COMPOSITE,
                       regions=3, x=200.0, y=60.0, w=420.0, h=420.0))
    sm.add_transition(Transition(id="t0", source="i", target="p"))
    tuval = DiagramCanvas(Document(sm))
    tuval.auto_edit = False
    tuval.resize(900, 800)
    tuval.show()
    tuval.rebuild()
    app.processEvents()

    par = tuval.state_items["p"]
    alan = par.content_rect()
    serit = alan.height() / 3.0
    for bolge, sid in ((0, "a"), (1, "b"), (2, "c")):
        sm.add_state(State(id=sid, name=sid.upper(), kind=StateKind.SIMPLE,
                           parent="p", region=bolge, x=alan.left() + 20.0,
                           y=alan.top() + serit * (bolge + 0.5) - 26.0,
                           w=120.0, h=52.0))
    tuval.rebuild()
    app.processEvents()

    def dagilim():
        d = tuval.doc.machine.states
        return [d[k].region for k in ("a", "b", "c")]

    def celiski():
        """Modeldeki bolge ile CIZILDIGI serit ayrilan ogeler."""
        ust = tuval.state_items["p"]
        return [st.name for st in tuval.doc.machine.states.values()
                if st.parent == "p"
                and int(st.region or 0) != ust.region_at(st.y + st.h / 2.0)]

    baslangic = dagilim()
    check(baslangic == [0, 1, 2], "uc bolgeye birer durum yerlesti",
          "dagilim = %s" % baslangic)
    check(not celiski(), "baslangicta model ile cizim ayni")

    # GERCEK KULLANICI YOLU: Inspector'daki dondurme kutusu. Modele
    # dogrudan yazmak, kutunun tetikledigi `_set_regions` kelepcelemesini
    # -- eski veri kaybinin tam kaynagini -- hic calistirmazdi.
    from PyQt6.QtWidgets import QSpinBox
    from app.ui.inspector import Inspector

    gozlemci = Inspector(tuval.doc)
    gozlemci.show_selection(["p"])
    app.processEvents()
    kutu = [w for w in gozlemci.findChildren(QSpinBox)
            if w.accessibleName() == "Region count"]
    check(bool(kutu), "Regions dondurme kutusu bulundu")

    for hedef in (2, 1, 2, 3):
        if kutu:
            kutu[0].setValue(hedef)
        else:
            tuval.doc.machine.states["p"].regions = hedef
        app.processEvents()
        # Ana pencere her duzenlemeden sonra tuvali yeniden cizer.
        tuval.rebuild()
        app.processEvents()
        check(tuval.doc.machine.states["p"].regions == hedef,
              "dondurme kutusu regions=%d yazdi" % hedef,
              "regions = %d" % tuval.doc.machine.states["p"].regions)
        check(not celiski(),
              "regions=%d iken model ile cizim hala ayni" % hedef,
              "ayrilan: %s" % celiski())
    check(dagilim() == baslangic,
          "3 -> 1 -> 3 gidip gelmek dagilimi GERI GETIRIYOR",
          "%s -> %s" % (baslangic, dagilim()))
    gozlemci.close()

    # Bilesik duruma DAVRANIS metni yazmak da bant sinirlarini kaydirir;
    # model yine cizimi izlemeli.
    tuval.doc.machine.states["p"].do = "one();\ntwo();\nthree();"
    tuval.rebuild()
    app.processEvents()
    check(not celiski(),
          "davranis metni bant sinirini kaydirinca model cizimi izliyor",
          "ayrilan: %s" % celiski())
    tuval.close()

# --------------------------------------------------------------------------- #
# 41. HER TUR, HER KATMANDA TANIMLI MI
#
# Yeni bir oge turu eklendiginde tur -> gorunum esleyen SOZLUKLERI
# guncellemek unutulabiliyor. Bu, testlerin cogundan KACAN bir kusur
# sinifidir: kod uretimi ve benzetim calisir, iz karsilastirmasi yesil
# kalir, ama o ogeyi iceren modeli CALISMA ALANINDA GORUNTULEMEK
# KeyError ile cokertir -- yani model hic acilamaz.
#
# Fork, join, giris/cikis noktasi ve altmakine eklendiginde tam olarak bu
# oldu: `KIND_GLYPH` ve `KIND_LABEL` guncellenmemisti.
# --------------------------------------------------------------------------- #

def test_every_kind_is_mapped() -> None:
    print("\n== 41. her tur her katmanda tanimli mi ==")

    from PyQt6.QtWidgets import QApplication

    from app.core.model import State, StateKind, StateMachine, Transition
    from app.ui.panels import KIND_GLYPH, KIND_LABEL
    from app.ui.workspace_tree import WorkspaceTree

    app = QApplication.instance() or QApplication([])

    eksik_g = sorted(k.value for k in StateKind if k not in KIND_GLYPH)
    eksik_l = sorted(k.value for k in StateKind if k not in KIND_LABEL)
    check(not eksik_g, "her StateKind icin bir agac simgesi var",
          "eksik: %s" % eksik_g)
    check(not eksik_l, "her StateKind icin bir agac etiketi var",
          "eksik: %s" % eksik_l)

    # Etiketler BIRBIRINDEN ayirt edilebilmeli; agacta iki ayri tur ayni
    # adla gorunmemelidir.
    check(len(set(KIND_LABEL.values())) == len(KIND_LABEL),
          "tur etiketleri benzersiz",
          "tekrarlayan: %s" % sorted(
              e for e in set(KIND_LABEL.values())
              if list(KIND_LABEL.values()).count(e) > 1))

    # Paletteki her araca karsilik gelen tur de tabloda olmali.
    from app.ui.canvas import _NEW_STATE_DEFAULTS
    palet = {kind for kind, _b, _w, _h in _NEW_STATE_DEFAULTS.values()}
    check(palet <= set(KIND_GLYPH),
          "paletten cizilebilen her tur agacta gosterilebiliyor",
          "eksik: %s" % sorted(k.value for k in palet if k not in KIND_GLYPH))

    # HER turu iceren bir modeli agac gercekten cizebilmeli.
    sm = StateMachine(name="M", prefix="m", context_type="void")
    sm.add_state(State(id="i", name="I", kind=StateKind.INITIAL))
    sm.add_state(State(id="p", name="Par", kind=StateKind.COMPOSITE,
                       regions=2))
    sm.add_transition(Transition(id="t0", source="i", target="p"))
    sayac = 0
    for kind in StateKind:
        if kind in (StateKind.INITIAL, StateKind.COMPOSITE):
            continue
        sayac += 1
        ust = "p" if kind.is_connection_point else None
        sm.add_state(State(id="k%d" % sayac, name="K%d" % sayac, kind=kind,
                           parent=ust))
    try:
        agac = WorkspaceTree()
        agac.resize(320, 600)
        agac.show()
        agac._fill_states(agac.invisibleRootItem(), sm, None)
        app.processEvents()
        agac.close()
        check(True, "agac, HER turu iceren modeli cizebiliyor")
    except Exception as hata:                              # noqa: BLE001
        check(False, "agac, HER turu iceren modeli cizebiliyor", repr(hata))


# --------------------------------------------------------------------------- #
# 42. KISAYOLLAR ODAGIN BULUNDUGU PANELE AITTIR
#
# SESSIZ VERI KAYBIYDI. Kod paneli ve benzetim izi SALT OKUNURDUR; Qt'de
# salt okunur bir metin alani `ShortcutOverride` olayini KABUL ETMEZ ve
# pencere kapsamli bir QAction kisayolu onu yener (duzenlenebilir bir alan
# yener, salt okunur olan yenilir -- fark tam olarak budur).
#
# Sonuc: kullanici uretilen kodu kopyalamak icin panele tiklayip Ctrl+A'ya
# basiyor, kod SECILMIYOR; onun yerine gormedigi tuvalde her sey seciliyor
# ve Del butun modeli siliyordu. Ustelik Del eylemini ETKINLESTIREN sey de
# o Ctrl+A idi: iki tusluk bir jest, dokuz durumluk bir diyagrami sifirlar.
# --------------------------------------------------------------------------- #

def test_shortcuts_follow_focus() -> None:
    print("\n== 42. kisayollar odagin bulundugu panele ait ==")

    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication, QPlainTextEdit

    from app.core.samples import demo_machine
    from app.ui.code_editor import CodeEditor
    from app.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    win.resize(1500, 950)
    win.show()
    win.canvas.auto_edit = False
    win.doc.replace(demo_machine())
    win.canvas.rebuild()
    win.a_code_panel.setChecked(True)
    for _ in range(8):
        app.processEvents()
    win.raise_()
    win.activateWindow()

    def olcu():
        return (len(win.doc.machine.states), len(win.doc.machine.transitions))

    def odakla(w):
        w.setFocus()
        for _ in range(4):
            app.processEvents()
        return QApplication.focusWidget() is w

    baslangic = olcu()
    check(baslangic[0] > 3, "ornek model yuklendi", "olcu = %s" % (baslangic,))

    # --- 1) TUVAL odakta: Del CALISMAYA DEVAM ETMELI ------------------------
    if odakla(win.canvas):
        sid = next(iter(win.doc.machine.states))
        win.canvas.set_selected_ids([sid])
        app.processEvents()
        QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Delete)
        for _ in range(4):
            app.processEvents()
        check(olcu()[0] == baslangic[0] - 1,
              "tuval odaktayken Del SILMEYE devam ediyor",
              "%s -> %s" % (baslangic, olcu()))
        win._undo()
        for _ in range(4):
            app.processEvents()
        check(olcu() == baslangic, "geri alma modeli geri getiriyor",
              "olcu = %s" % (olcu(),))
    else:
        print("  [ATLANDI] tuvale odak verilemedi")

    # --- 2) SALT OKUNUR paneller: Ctrl+A metni secer, Del dokunmaz ----------
    paneller = []
    kod = [e for e in win.findChildren(CodeEditor) if e.isVisible()]
    if kod:
        paneller.append(("kod paneli", kod[0]))
    iz = getattr(win.sim_panel, "trace", None)
    if isinstance(iz, QPlainTextEdit):
        iz.setPlainText("-" * 400)
        paneller.append(("benzetim izi", iz))
    check(bool(paneller), "salt okunur panel(ler) bulundu",
          "bulunan = %d" % len(paneller))

    for ad, w in paneller:
        if not odakla(w):
            print("  [ATLANDI] %s odagi alinamadi" % ad)
            continue
        once = olcu()
        win.canvas.set_selected_ids([])
        app.processEvents()
        QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_A,
                       Qt.KeyboardModifier.ControlModifier)
        for _ in range(4):
            app.processEvents()
        secili = len(w.textCursor().selectedText())
        check(secili > 50, "%s: Ctrl+A METNI seciyor" % ad,
              "secilen karakter = %d" % secili)
        check(not win.canvas.selected_ids(),
              "%s: Ctrl+A tuvali SECMIYOR" % ad,
              "tuval secimi = %d" % len(win.canvas.selected_ids()))
        QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Delete)
        for _ in range(4):
            app.processEvents()
        check(olcu() == once, "%s: Del modele DOKUNMUYOR" % ad,
              "%s -> %s" % (once, olcu()))

    # --- 3) CALISMA ALANI AGACI odakta: Del diyagrami silmez ----------------
    agac = getattr(win, "ws_tree", None)
    if agac is not None and odakla(agac):
        once = olcu()
        win.canvas.set_selected_ids(list(win.doc.machine.states))
        app.processEvents()
        QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Delete)
        for _ in range(4):
            app.processEvents()
        check(olcu() == once, "agac odaktayken Del diyagrami silmiyor",
              "%s -> %s" % (once, olcu()))
    else:
        print("  [ATLANDI] calisma alani agacina odak verilemedi")

    # KAPATMADAN ONCE BELGEYI TEMIZ ISARETLE.
    #
    # `MainWindow.closeEvent` -> `_confirm_discard` kaydedilmemis degisiklik
    # varsa MODAL bir kutu acar. `offscreen` platformda o kutuyu kapatacak
    # kimse yoktur: test sureci CPU harcamadan sonsuza dek bekler ve butun
    # takim kilitlenir. Bir kere yasandi; testin kendisi urunu degil,
    # kosumu kirdi.
    win.doc.mark_clean()
    win.class_doc.mark_clean()
    win.close()


# --------------------------------------------------------------------------- #
# 43. KULLANICININ YAZDIGI SEY CIKTIYA ULASIYOR MU
#
# Bir alani toplamak yetmez; onu OKUYAN bir cikti da olmali. Aksi halde
# kullanici birsey yazar, arac onu saklar, kimse gormez. Ayni sekilde bir
# cizim ogesi cikti bicimine dogru cevrilmeli: PlantUML'de ciplak `[H]`,
# ICINDE yazildigi bolgenin tarihidir; her tarihi oyle yazmak hepsini tek
# dugume cokertir ve resim modeli YANLIS anlatir.
# --------------------------------------------------------------------------- #

def test_authoring_reaches_output() -> None:
    print("\n== 43. yazilan sey ciktiya ulasiyor mu ==")

    from app.codegen.plantuml_generator import generate_plantuml
    from app.core.model import State, StateKind, StateMachine, Transition

    # -------------------------------------------- durum NOTU koda geciyor mu
    sm = StateMachine(name="N", prefix="n", context_type="void")
    sm.add_state(State(id="i", name="I", kind=StateKind.INITIAL))
    sm.add_state(State(id="a", name="Armed", kind=StateKind.SIMPLE,
                       note="Launch interlock opens here.\n"
                            "Second line */ dangerous."))
    sm.add_transition(Transition(id="t", source="i", target="a"))

    c_h = generate_c(sm)["n.h"]
    cpp_h = generate_cpp(sm)["N.hpp"]
    check("Launch interlock opens here." in c_h,
          "durum notu URETILEN C basligina geciyor",
          [ln.strip() for ln in c_h.splitlines() if "ARMED" in ln][:1])
    check("Launch interlock opens here." in cpp_h,
          "durum notu URETILEN C++ basligina geciyor")
    # Not TEK SATIRA inmeli ve yorum kapanisini bozmamali.
    notlu = [ln for ln in c_h.splitlines() if "Launch interlock" in ln]
    check(len(notlu) == 1 and "Second line" in notlu[0],
          "cok satirli not TEK SATIRLIK yoruma indiriliyor",
          notlu[:1])
    check("*/ dangerous" not in c_h,
          "nottaki yorum kapanisi etkisizlestiriliyor")

    # ------------------------- PlantUML: her tarih KENDI sahibini adlandirir
    pm = StateMachine(name="M", prefix="m", context_type="void")
    pm.add_state(State(id="i", name="I", kind=StateKind.INITIAL))
    pm.add_state(State(id="out", name="Out", kind=StateKind.SIMPLE))
    pm.add_transition(Transition(id="t0", source="i", target="out"))
    for on, tur in (("A", StateKind.SHALLOW_HISTORY),
                    ("B", StateKind.DEEP_HISTORY)):
        c = on.lower()
        pm.add_state(State(id=c, name="Grp" + on, kind=StateKind.COMPOSITE))
        pm.add_state(State(id=c + "i", name=on + "I",
                           kind=StateKind.INITIAL, parent=c))
        pm.add_state(State(id=c + "0", name=on + "0",
                           kind=StateKind.SIMPLE, parent=c))
        pm.add_state(State(id=c + "h", name=on + "H", kind=tur, parent=c))
        pm.add_transition(Transition(id="t" + c, source=c + "i",
                                     target=c + "0"))
        pm.add_transition(Transition(id="tin" + c, source="out", target=c,
                                     event="GO" + on))
        pm.add_transition(Transition(id="th" + c, source="out",
                                     target=c + "h", event="RES" + on))
    puml = generate_plantuml(pm)["m.puml"]
    check("GrpA[H]" in puml, "sig tarih SAHIBIYLE adlandiriliyor",
          [ln.strip() for ln in puml.splitlines() if "RESA" in ln][:1])
    check("GrpB[H*]" in puml, "derin tarih SAHIBIYLE adlandiriliyor",
          [ln.strip() for ln in puml.splitlines() if "RESB" in ln][:1])
    ciplak = [ln.strip() for ln in puml.splitlines()
              if ("> [H]" in ln or "> [H*]" in ln)]
    check(not ciplak,
          "hicbir ok CIPLAK [H] hedeflemiyor (hepsi tek dugume cokerdi)",
          ciplak[:2])


# --------------------------------------------------------------------------- #
# 44. OZELLIK ARAYUZDEN ERISILEBILIYOR MU, TESLIMAT EKSIKSIZ MI
#
# Bir ozelligin cekirdegi kusursuz calisabilir ve yine de KULLANILAMAZ
# olabilir. Altmakine tam boyle bir durumdaydi: duzlestirme, dogrulama
# (V160-V166), include birlestirme ve baglam denetimi calisiyordu ama
# "Machine" acilir kutusu HER ZAMAN bostu -- kullanici bir altmakine
# durumunu hicbir makineye baglayamiyordu. Sebep tek bir parantezdi
# (`ws.model_path()` bir OZELLIGI cagiriyordu) ve genis bir `except`
# hatayi "hicbir makine yok" diye gosteriyordu.
#
# Ayni bolum, TESLIM EDILEN pakette olmasi gerekeni de denetler.
# --------------------------------------------------------------------------- #

def test_feature_reachable_and_shipped() -> None:
    print("\n== 44. ozellik erisilebilir mi, teslimat eksiksiz mi ==")

    import json
    import tempfile
    from PyQt6.QtWidgets import QApplication, QComboBox

    from app.core.model import State, StateKind, StateMachine, Transition
    from app.core.workspace import Workspace
    from app.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])

    # ---------------- altmakine secicisi GERCEK bir calisma alaninda doluyor mu
    def basit(ad, on):
        m = StateMachine(name=ad, prefix=on, context_type="void")
        m.add_state(State(id="i", name="I", kind=StateKind.INITIAL))
        m.add_state(State(id="a", name="A", kind=StateKind.SIMPLE))
        m.add_transition(Transition(id="t", source="i", target="a"))
        return m

    kok = tempfile.mkdtemp(prefix="usd_reg_sub_")
    ws = Workspace.create(kok, "Test")
    os.makedirs(ws.model_path, exist_ok=True)
    for ad, on in (("Inner", "inner"), ("Helper", "helper")):
        with open(os.path.join(ws.model_path, "%s.usm" % ad.lower()),
                  "w", encoding="utf-8") as fh:
            fh.write(basit(ad, on).to_json())

    win = MainWindow()
    win.resize(1300, 860)
    win.show()
    win.canvas.auto_edit = False
    win.workspace = ws
    ana = basit("Main", "main")
    ana.add_state(State(id="sub", name="Sub", kind=StateKind.SUBMACHINE))
    ana.add_transition(Transition(id="t2", source="a", target="sub",
                                  event="GO"))
    win.doc.replace(ana)
    win.canvas.rebuild()
    app.processEvents()

    makineler = win.inspector._workspace_machines()
    check(len(makineler) == 2,
          "calisma alanindaki makineler listeleniyor",
          "listelenen = %s" % makineler)

    win.inspector.show_selection(["sub"])
    app.processEvents()
    dolu = [c for c in win.inspector.findChildren(QComboBox)
            if any("inner" in c.itemText(i).lower()
                   for i in range(c.count()))]
    check(bool(dolu),
          "'Machine' acilir kutusu calisma alanindaki makineleri gosteriyor",
          "kutu icerikleri = %s"
          % [[c.itemText(i) for i in range(c.count())]
             for c in win.inspector.findChildren(QComboBox)])
    win.doc.mark_clean()
    win.class_doc.mark_clean()
    win.close()
    remove_tree(kok)

    # ------------------- gecerli bir FORK modeli YANLIS uyari uretmemeli
    fm = StateMachine(name="F", prefix="f", context_type="void")
    fm.add_state(State(id="i", name="I", kind=StateKind.INITIAL))
    fm.add_state(State(id="out", name="Out", kind=StateKind.SIMPLE))
    fm.add_state(State(id="fk", name="FK", kind=StateKind.FORK))
    fm.add_state(State(id="p", name="Par", kind=StateKind.COMPOSITE,
                       regions=2))
    fm.add_transition(Transition(id="t0", source="i", target="out"))
    for bolge, on in ((0, "a"), (1, "b")):
        fm.add_state(State(id=on + "i", name=on.upper() + "I",
                           kind=StateKind.INITIAL, parent="p", region=bolge))
        fm.add_state(State(id=on + "0", name=on.upper() + "0",
                           kind=StateKind.SIMPLE, parent="p", region=bolge))
        fm.add_state(State(id=on + "1", name=on.upper() + "1",
                           kind=StateKind.SIMPLE, parent="p", region=bolge))
        fm.add_transition(Transition(id="ti" + on, source=on + "i",
                                     target=on + "0"))
        fm.add_transition(Transition(id="tf" + on, source="fk",
                                     target=on + "1"))
    fm.add_transition(Transition(id="tg", source="out", target="fk",
                                 event="GO"))
    fm.add_transition(Transition(id="tb", source="p", target="out",
                                 event="BACK"))
    kodlar = [i.code for i in validate(fm)]
    check("V072" not in kodlar,
          "fork'la girilen bilesik 'erisilmez' sayilmiyor",
          [i.message[:70] for i in validate(fm) if i.code == "V072"])
    check("V080" not in kodlar,
          "fork'un dallari 'belirsiz secim' sayilmiyor",
          [i.message[:70] for i in validate(fm) if i.code == "V080"])

    # ------------------- TESLIM EDILEN pakette spesifikasyon dizini var mi
    spec = open(os.path.join(ROOT, "UML-Design-Studio.spec"),
                encoding="utf-8").read()
    check("uml_spec_index.json" in spec,
          "spesifikasyon dizini EXE paketine dahil",
          "PyInstaller bir .py yanindaki veri dosyasini kendiliginden almaz; "
          "liste disinda kalinca her bulgu sayfa numarasini kaybeder")

    # ------------------- ORNEK MODEL metinleri ingilizce mi
    ornek = json.load(open(os.path.join(ROOT, "examples", "blinky.usm"),
                           encoding="utf-8"))
    metinler = [ornek.get("description", "")]
    metinler += [st.get("note", "") for st in ornek.get("states", [])]
    kotu = [m for m in metinler
            if m and (_TR_HARF.search(m) or len(_TR_SOZCUK.findall(m)) >= 1)]
    check(not kotu, "teslim edilen ornek modelin metinleri ingilizce",
          "\n".join(kotu[:3]))

    # Ayni metinler URETILEN BASLIGA da geciyor (durum notu olarak).
    from app.core.samples import demo_machine
    uretilen = generate_c(demo_machine())["blinky.h"]
    kotu2 = [ln.strip() for ln in uretilen.splitlines()
             if _TR_HARF.search(ln) or len(_TR_SOZCUK.findall(ln)) >= 1]
    check(not kotu2, "ornek modelin notlari uretilen baslikta ingilizce",
          "\n".join(kotu2[:3]))


# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
#  45) Benzetim paneli, menu cizgisi ve ornek galerisi
# --------------------------------------------------------------------------- #

def test_simulation_and_examples() -> None:
    """Kullanicinin bildirdigi dort kusur + ornek galerisinin saglamligi.

    1) Benzetim paneli kapaninca tuvalde etkin durum VURGUSU kaliyordu;
       kullanici onu secim sanip silmeye calisiyordu.
    2) Menu cubugundaki erisim harflerinin alti HEP ciziliydi (Fusion
       SH_UnderlineShortcut icin kosulsuz 1 doner).
    3) Korumalar yalnizca elle cevrilen anahtarlardi; artik degisken
       degerlerinden HESAPLANIYOR.
    4) Iz kaydi yalnizca entry/exit/effect listeliyordu; artik secilen
       gecisi, koruma sonucunu ve etkin konfigurasyonu da yaziyor -- ama
       `Simulator.trace` uretilen kodun iziyle BIREBIR kalmali.
    5) Galerideki her ornek dogrulamadan gecmeli ve kod uretmeli.
    """
    print("\n== 45. benzetim paneli, menu cizgisi ve ornek galerisi ==")

    from PyQt6.QtWidgets import QApplication, QStyle
    from app.core.examples import CLASS_EXAMPLES, STATE_EXAMPLES
    from app.core.guard_expr import evaluate, identifiers
    from app.core.simulator import Simulator
    from app.ui.main_window import MainWindow, _QuietMnemonicStyle

    # -- 2) menu erisim harfi cizgisi ---------------------------------------
    stil = _QuietMnemonicStyle("Fusion")
    check(stil.styleHint(QStyle.StyleHint.SH_UnderlineShortcut) == 0,
          "menu erisim harflerinin alti cizilmiyor",
          "SH_UnderlineShortcut = %d"
          % stil.styleHint(QStyle.StyleHint.SH_UnderlineShortcut))

    # -- 3) koruma ifadesi degiskenlerden hesaplaniyor -----------------------
    check(identifiers("ctx->t >= limit") == ["t", "limit"],
          "koruma ifadesindeki degisken adlari cikariliyor",
          str(identifiers("ctx->t >= limit")))
    check(evaluate("ctx->t >= limit", {"t": 31, "limit": 30}) is True,
          "koruma degiskenlerden TRUE hesaplaniyor")
    check(evaluate("ctx->t >= limit", {"t": 29, "limit": 30}) is False,
          "koruma degiskenlerden FALSE hesaplaniyor")
    check(evaluate("app_over_limit(ctx)", {}) is None,
          "hesaplanamayan koruma None doner (elle degere duser)")
    check(evaluate("t > 0", {}) is None,
          "tanimsiz degisken TRUE varsayilmiyor")

    # -- 4) anlatim `trace` listesini KIRLETMIYOR ---------------------------
    sm = StateMachine(name="N", prefix="n", context_type="void")
    sm.add_state(State(id="i", name="I", kind=StateKind.INITIAL, y=0))
    sm.add_state(State(id="a", name="A", kind=StateKind.SIMPLE, y=100))
    sm.add_state(State(id="b", name="B", kind=StateKind.SIMPLE, y=200))
    sm.add_transition(Transition(id="t0", source="i", target="a"))
    sm.add_transition(Transition(id="t1", source="a", target="b", event="GO",
                                 guard="flag"))
    notlar = []
    ref = Simulator(sm, guard_eval=lambda _e: True,
                    on_event=lambda k, d: notlar.append(k))
    ref.start()
    ref.dispatch("GO")
    check(not any(t.split(":")[0] in ("T", "G") for t in ref.trace),
          "gecis/koruma anlatimi referans IZE yazilmiyor", str(ref.trace))
    check("T" in notlar and "G" in notlar,
          "gecis/koruma anlatimi PANELE gonderiliyor", str(sorted(set(notlar))))

    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    win.resize(1400, 900)
    win.doc.replace(demo_machine(), None)
    win.canvas.rebuild()
    win.a_sim_panel.setChecked(True)
    win.toggle_sim_panel(True)
    app.processEvents()

    # -- 1) panel kapaninca vurgu kalmiyor ----------------------------------
    win.sim_panel.start()
    app.processEvents()
    etkin = [s for s, it in win.canvas.state_items.items() if it.is_active]
    check(bool(etkin), "benzetim etkin durumu vurguluyor", str(etkin))
    win.a_sim_panel.setChecked(False)
    win.toggle_sim_panel(False)
    app.processEvents()
    kalan = [s for s, it in win.canvas.state_items.items() if it.is_active]
    check(not kalan, "panel kapaninca tuvalde vurgu KALMIYOR", str(kalan))

    # Sinif kipine gecince de kalmamali.
    win.a_sim_panel.setChecked(True)
    win.toggle_sim_panel(True)
    win.sim_panel.start()
    app.processEvents()
    win.mode_tabs.setCurrentIndex(1)
    app.processEvents()
    kalan = [s for s, it in win.canvas.state_items.items() if it.is_active]
    check(not kalan, "sinif kipine gecince de vurgu KALMIYOR", str(kalan))
    win.mode_tabs.setCurrentIndex(0)
    app.processEvents()

    # -- 4b) iz kaydi anlatimli mi ------------------------------------------
    panel = win.sim_panel
    panel.start()
    panel.dispatch("BUTTON")
    app.processEvents()
    iz = panel.trace.toPlainText()
    for parca, etiket in (("legend", "okuma kilavuzu"),
                          ("dispatch(BUTTON)", "olay basligi"),
                          ("-->", "secilen gecis"),
                          ("entry", "giris davranisi")):
        check(parca in iz, "iz kaydinda %s var" % etiket, iz[:400])
    check(any(satir.strip().startswith("=") for satir in iz.splitlines()),
          "iz kaydi her adimdan sonra ETKIN KONFIGURASYONU yaziyor",
          iz[:400])

    # -- 3b) panel degisken tablosunu kendiliginden dolduruyor --------------
    gsm = StateMachine(name="G", prefix="g", context_type="g_t")
    gsm.add_state(State(id="i", name="I", kind=StateKind.INITIAL, y=0))
    gsm.add_state(State(id="a", name="A", kind=StateKind.SIMPLE, y=100))
    gsm.add_state(State(id="c", name="C", kind=StateKind.CHOICE, y=200))
    gsm.add_state(State(id="b", name="B", kind=StateKind.SIMPLE, y=300))
    gsm.add_transition(Transition(id="t0", source="i", target="a"))
    gsm.add_transition(Transition(id="t1", source="a", target="c", event="GO"))
    gsm.add_transition(Transition(id="t2", source="c", target="b",
                                  guard="ctx->temp >= limit"))
    gsm.add_transition(Transition(id="t3", source="c", target="a",
                                  guard="else"))
    win.doc.replace(gsm, None)
    app.processEvents()
    adlar = [panel.var_table.item(r, 0).text()
             for r in range(panel.var_table.rowCount())]
    check(adlar == ["temp", "limit"],
          "degisken tablosu koruma adlarindan dolduruluyor", str(adlar))
    panel.var_table.cellWidget(0, 1).setText("31")
    panel.var_table.cellWidget(1, 1).setText("30")
    app.processEvents()
    panel.start()
    panel.dispatch("GO")
    app.processEvents()
    check(panel.sim.state_name == "B",
          "girilen degerler gecisi SURUYOR (31 >= 30)", panel.sim.state_name)
    panel.reset(quiet=True)
    panel.var_table.cellWidget(0, 1).setText("29")
    app.processEvents()
    panel.start()
    panel.dispatch("GO")
    app.processEvents()
    check(panel.sim.state_name == "A",
          "deger dusurulunce else dali aliniyor (29 < 30)",
          panel.sim.state_name)

    # -- 5) galerideki her ornek gecerli ve uretilebilir --------------------
    from app.codegen.class_c_generator import generate_class_c
    from app.codegen.class_cpp_generator import generate_class_cpp
    from app.core.class_validator import validate_classes
    check(len(STATE_EXAMPLES) >= 10 and len(CLASS_EXAMPLES) >= 6,
          "galeri her iki kip icin de birden cok ornek tasiyor",
          "%d durum / %d sinif"
          % (len(STATE_EXAMPLES), len(CLASS_EXAMPLES)))
    anahtarlar = [o.key for o in list(STATE_EXAMPLES) + list(CLASS_EXAMPLES)]
    check(len(anahtarlar) == len(set(anahtarlar)),
          "ornek anahtarlari benzersiz", str(anahtarlar))
    for ornek in STATE_EXAMPLES:
        model = ornek.build()
        sorun = [i for i in validate(model) if i.severity == "error"]
        check(not sorun, "ornek '%s' dogrulamadan geciyor" % ornek.key,
              "; ".join(i.message for i in sorun[:3]))
        try:
            generate_c(model)
            generate_cpp(model)
            uretildi = True
            neden = ""
        except Exception as exc:                       # noqa: BLE001
            uretildi = False
            neden = str(exc)
        check(uretildi, "ornek '%s' C ve C++ uretiyor" % ornek.key, neden)
        check(bool(ornek.teaches and ornek.summary and ornek.reference),
              "ornek '%s' ne ogrettigini yaziyor" % ornek.key)
    for ornek in CLASS_EXAMPLES:
        model = ornek.build()
        sorun = [i for i in validate_classes(model) if i.severity == "error"]
        check(not sorun, "sinif ornegi '%s' dogrulamadan geciyor" % ornek.key,
              "; ".join(getattr(i, "message", str(i)) for i in sorun[:3]))
        try:
            generate_class_c(model)
            generate_class_cpp(model)
            uretildi = True
            neden = ""
        except Exception as exc:                       # noqa: BLE001
            uretildi = False
            neden = str(exc)
        check(uretildi, "sinif ornegi '%s' kod uretiyor" % ornek.key, neden)

    # -- 5b) menude gercekten gorunuyorlar mi -------------------------------
    gal = getattr(win, "_menus_examples", None)
    check(gal is not None, "File > Examples menusu kuruldu")
    if gal is not None:
        altlar = [a.menu() for a in gal.actions() if a.menu() is not None]
        toplam = sum(len(m.actions()) for m in altlar)
        check(toplam == len(STATE_EXAMPLES) + len(CLASS_EXAMPLES),
              "her ornek menude bir satir", "%d satir" % toplam)
        for m in altlar:
            for a in m.actions():
                if not a.toolTip():
                    check(False, "menu satirinda ipucu yok: %s" % a.text())
                    break

    win.doc.mark_clean()
    win.class_doc.mark_clean()
    win.settings.clear()
    win.close()


# --------------------------------------------------------------------------- #
#  46) Orneklerin YERLESIMI okunakli mi
# --------------------------------------------------------------------------- #

def test_example_layout() -> None:
    """Ornek diyagramlarda hicbir yazi kirpilmamali, hicbir sey cakismamali.

    Kullanicinin bildirdigi sikayet: "hepsinde yazilarin hepsi okunsun,
    sikistirarak yazma." Tuval kutuya sigmayan davranis satirini
    `elidedText` ile kirpiyor ve kullanici kodun yarisini goruyor; iki
    gecis etiketi ust uste binince de ikisi de okunmaz oluyor
    (ornekte 'ACK_SHALLOW' ile 'ACK_DEEP' boyleydi).

    Olcum GERCEK oge geometrisinden yapilir, tahminden degil.
    """
    print("\n== 46. ornek yerlesimleri okunakli mi ==")

    from PyQt6.QtCore import QRectF, Qt
    from PyQt6.QtGui import QFontMetricsF
    from PyQt6.QtWidgets import QApplication

    from app.core.examples import CLASS_EXAMPLES, STATE_EXAMPLES
    from app.ui.canvas import DiagramCanvas
    from app.ui.class_canvas import ClassCanvas
    from app.ui.diagram_items import TransitionItem
    from app.ui.document import Document

    app = QApplication.instance() or QApplication([])

    # Metin olcumu YALNIZCA beklenen mono yazi tipi varsa anlamlidir.
    # offscreen platformunda Qt'nin yazi tipi veritabani bostur: tuval
    # karakter basina 7.0 px yerine 11.0 px suren bir yedege duser ve
    # olmayan kirpilmalar bildirilir.
    from app.ui import theme as _theme
    _mono = _theme._first_available(_theme.MONO_STACK)
    olc_metin = _mono is not None
    if not olc_metin:
        print("  (metin olcumu atlandi: %s bulunamadi -- "
              "gecerli yazi tipi %r)"
              % (" / ".join(_theme.MONO_STACK[:2]),
                 _theme.mono_font(9).family()))

    def ortak(a: QRectF, b: QRectF) -> float:
        k = a.intersected(b)
        return 0.0 if k.isEmpty() else k.width() * k.height()

    for ornek in STATE_EXAMPLES:
        sm = ornek.build()
        tuval = DiagramCanvas(Document(sm))
        tuval.auto_edit = False
        tuval.resize(1600, 1000)
        tuval.rebuild()
        app.processEvents()
        sorunlar = []
        ogeler = tuval.state_items

        for sid, oge in ogeler.items():
            if not olc_metin:
                break
            st = sm.states[sid]
            if st.kind.is_pseudo or st.kind.name == "FINAL":
                continue
            r = oge.rect()
            fm = QFontMetricsF(oge.f_title)
            if fm.elidedText(st.name, Qt.TextElideMode.ElideRight,
                             r.width() - 16) != st.name:
                sorunlar.append("baslik kirpik: %s" % st.name)
            satirlar = [t for t in (
                ("entry / " + st.entry) if st.entry else "",
                ("exit  / " + st.exit) if st.exit else "",
                ("do    / " + st.do) if st.do else "") if t]
            if satirlar:
                fmb = QFontMetricsF(oge.f_body)
                for ln in satirlar:
                    if fmb.elidedText(ln, Qt.TextElideMode.ElideRight,
                                      r.width() - 16) != ln:
                        sorunlar.append("davranis kirpik (%s): %s"
                                        % (st.name, ln.strip()))
                if r.height() < 34.0 + len(satirlar) * (fmb.height() + 1.0) + 10.0:
                    sorunlar.append("davranis sigmiyor: %s" % st.name)

        # kardes kutular ve alt durum tasmasi
        for sid, oge in ogeler.items():
            st = sm.states[sid]
            r1 = oge.mapToScene(oge.rect()).boundingRect()
            for sid2, oge2 in ogeler.items():
                if sid >= sid2 or sm.states[sid2].parent != st.parent:
                    continue
                if ortak(r1, oge2.mapToScene(oge2.rect()).boundingRect()) > 1.0:
                    sorunlar.append("kutular cakisiyor: %s <-> %s"
                                    % (st.name, sm.states[sid2].name))
            if (st.parent and st.parent in ogeler
                    and st.kind.name not in ("ENTRY_POINT", "EXIT_POINT")):
                ust = ogeler[st.parent]
                ic = (ust.mapToScene(ust.rect()).boundingRect()
                      .adjusted(6, 34, -6, -6))
                if not ic.contains(r1):
                    sorunlar.append("alt durum ustunden tasiyor: %s" % st.name)

        # gecis etiketleri -- genislikleri yazi tipine baglidir
        etiketler = []
        for it in (tuval._scene.items() if olc_metin else []):
            if not isinstance(it, TransitionItem) or it._label_rect.isEmpty():
                continue
            etiketler.append((it, it.mapToScene(it._label_rect).boundingRect()))
            ham = it._label_lines or ([it._label] if it._label else [])
            for a, b in zip(ham, it._label_drawn):
                if a != b:
                    sorunlar.append("gecis etiketi kirpik: %s" % a)
        for i, (it1, r1) in enumerate(etiketler):
            for it2, r2 in etiketler[i + 1:]:
                if ortak(r1, r2) > 4.0:
                    sorunlar.append("etiketler cakisiyor: '%s' <-> '%s'"
                                    % (it1._label_text, it2._label_text))
            for sid, oge in ogeler.items():
                if sm.states[sid].kind.is_pseudo:
                    continue
                k = oge.mapToScene(oge.rect()).boundingRect()
                if ortak(r1, QRectF(k.left(), k.top(), k.width(), 30.0)) > 6.0:
                    sorunlar.append("etiket basligi kapatiyor: '%s' -> %s"
                                    % (it1._label_text, sm.states[sid].name))

        check(not sorunlar, "ornek '%s' yerlesimi okunakli" % ornek.key,
              "\n".join(sorunlar[:6]))
        tuval.close()

    for ornek in CLASS_EXAMPLES:
        cm = ornek.build()
        tuval = ClassCanvas(Document(cm))
        tuval.resize(1600, 1000)
        tuval.rebuild()
        app.processEvents()
        sorunlar = []
        ogeler = getattr(tuval, "class_items", {})
        kimlikler = list(ogeler)
        for i, a in enumerate(kimlikler):
            ra = ogeler[a].mapToScene(ogeler[a].rect()).boundingRect()
            for b in kimlikler[i + 1:]:
                rb = ogeler[b].mapToScene(ogeler[b].rect()).boundingRect()
                if ortak(ra, rb) > 1.0:
                    sorunlar.append("sinif kutulari cakisiyor: %s <-> %s"
                                    % (cm.classes[a].name, cm.classes[b].name))
        for cid, oge in ogeler.items():
            sinif = cm.classes[cid]
            n = len(sinif.attributes) + len(sinif.operations)
            if oge.rect().height() < 34.0 + n * 16.0 + 18.0:
                sorunlar.append("uyeler sigmiyor: %s" % sinif.name)
        check(not sorunlar, "sinif ornegi '%s' yerlesimi okunakli" % ornek.key,
              "\n".join(sorunlar[:6]))
        tuval.close()


# --------------------------------------------------------------------------- #
#  47) Kutular icerige gore buyur mu
# --------------------------------------------------------------------------- #

def test_boxes_fit_their_text() -> None:
    """Metin uzayinca kutu buyumeli; hicbir sey kirpilmamali.

    YENIDEN URETILEN KUSUR: bir duruma uzun bir entry yazinca kutu olduğu
    yerde kaliyordu ve tuval satiri kirpiyordu:

        entry / configure_peripheral(ctx, CHANNEL_THREE, MODE_CONTINUOUS);
          ->  "entry / configure_per..."

    Uretilen kod kritik yerlerde kullanildigi icin yarim gorunen bir
    davranis kabul edilemez. Ayni kusur sinif kutularinda da vardi.

    Model DEGISMEMELI: buyume yalnizca cizimdedir, yoksa dosya acar acmaz
    "kaydedilmedi" isareti yanar ve kullanicinin verdigi boyut kaybolur.
    """
    print("\n== 47. kutular icerigine gore buyuyor mu ==")

    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QFontMetricsF
    from PyQt6.QtWidgets import QApplication

    from app.core.class_model import (Attribute, ClassModel, Operation,
                                      Parameter, UmlClass)
    from app.core.model import (State, StateKind, StateMachine, Transition)
    from app.ui.canvas import DiagramCanvas
    from app.ui.class_canvas import ClassCanvas
    from app.ui.document import Document

    app = QApplication.instance() or QApplication([])

    sm = StateMachine(name="Fit", prefix="fit", context_type="fit_ctx_t")
    sm.add_state(State(id="i", name="I", kind=StateKind.INITIAL, y=0))
    sm.add_state(State(id="a", name="A", kind=StateKind.SIMPLE, x=60, y=100))
    sm.add_transition(Transition(id="t0", source="i", target="a"))
    doc = Document(sm)
    tuval = DiagramCanvas(doc)
    tuval.auto_edit = False
    tuval.resize(1200, 700)
    tuval.rebuild()
    app.processEvents()

    dar = tuval.state_items["a"].rect().width()
    uzun = "configure_peripheral(ctx, CHANNEL_THREE, MODE_CONTINUOUS);"
    doc.edit("entry", lambda m: setattr(m.states["a"], "entry", uzun))
    tuval.rebuild()
    app.processEvents()

    oge = tuval.state_items["a"]
    genis = oge.rect().width()
    check(genis > dar, "uzun metin kutuyu GENISLETTI",
          "%.0f -> %.0f" % (dar, genis))
    fmb = QFontMetricsF(oge.f_body)
    satir = "entry / " + uzun
    check(fmb.elidedText(satir, Qt.TextElideMode.ElideRight,
                         oge.rect().width() - 16) == satir,
          "davranis satiri KIRPILMIYOR")
    check(sm.states["a"].w == 170.0,
          "model bozulmadi (buyume yalnizca cizimde)",
          "w = %s" % sm.states["a"].w)

    doc.edit("uc satir",
             lambda m: (setattr(m.states["a"], "exit", "teardown(ctx);"),
                        setattr(m.states["a"], "do", "poll(ctx);")))
    tuval.rebuild()
    app.processEvents()
    # Dogru iddia "buyudu" degil, "UC SATIRI TASIYOR": kutu zaten yeterince
    # yuksekse buyumesi gerekmez. (Ilk yazimda "buyudu" denmisti; satir
    # yuksekligi yazi tipine bagli oldugu icin bu, dar bir yazi tipinde
    # olmayan bir kusuru bildiriyordu.)
    oge3s = tuval.state_items["a"]
    gerekli = 34.0 + 3 * (QFontMetricsF(oge3s.f_body).height() + 1.0) + 10.0
    check(oge3s.rect().height() >= gerekli,
          "kutu UC davranis satirini tasiyor",
          "%.0f >= %.0f" % (oge3s.rect().height(), gerekli))
    check(len(oge3s.behavior_lines()) == 3, "uc davranis satiri da cizilecek")

    # Sozde-durumlar sabit sekillerdir: MIN_W/MIN_H onlara uygulanmamali.
    ilk = tuval.state_items["i"]
    check(abs(ilk.rect().width() - sm.states["i"].w) < 0.5,
          "baslangic sozde-durumu sisirilmiyor",
          "%.0f vs %.0f" % (ilk.rect().width(), sm.states["i"].w))

    # Bilesik durum alt durumlarini KAPSAMALI.
    sm2 = StateMachine(name="Nest", prefix="nest", context_type="void")
    sm2.add_state(State(id="i", name="I", kind=StateKind.INITIAL, y=0))
    sm2.add_state(State(id="c", name="Outer", kind=StateKind.COMPOSITE,
                        x=40, y=60, w=200.0, h=160.0))
    sm2.add_state(State(id="ci", name="CI", kind=StateKind.INITIAL,
                        parent="c", x=20, y=40, w=22.0, h=22.0))
    sm2.add_state(State(id="ch", name="Inner", kind=StateKind.SIMPLE,
                        parent="c", x=20, y=80, w=170.0, h=70.0,
                        entry="a_rather_long_call_that_needs_room(ctx);"))
    sm2.add_transition(Transition(id="t0", source="i", target="c"))
    sm2.add_transition(Transition(id="t1", source="ci", target="ch"))
    doc2 = Document(sm2)
    tuval2 = DiagramCanvas(doc2)
    tuval2.auto_edit = False
    tuval2.resize(1200, 700)
    tuval2.rebuild()
    app.processEvents()
    ust = tuval2.state_items["c"]
    alt = tuval2.state_items["ch"]
    check(ust.rect().width() >= alt.pos().x() + alt.rect().width(),
          "bilesik durum alt durumunu KAPSIYOR",
          "ust %.0f, alt sag kenar %.0f"
          % (ust.rect().width(), alt.pos().x() + alt.rect().width()))

    # --- sinif kutulari
    cm = ClassModel(name="FitC", prefix="fitc")
    cm.add_class(UmlClass(id="k", name="Thing", x=40, y=40))
    doc3 = Document(cm)
    tuval3 = ClassCanvas(doc3)
    tuval3.resize(1200, 700)
    tuval3.rebuild()
    app.processEvents()
    dar3 = list(tuval3.class_items.values())[0].rect().width()

    def uyeler(m):
        k = m.classes["k"]
        k.attributes = [Attribute(name="configuration_register_value",
                                  type="volatile uint32_t")]
        k.operations = [Operation(name="applyConfigurationToPeripheral",
                                  return_type="bool",
                                  params=[Parameter(name="channel",
                                                    type="uint8_t")])]
    doc3.edit("uye", uyeler)
    tuval3.rebuild()
    app.processEvents()
    oge3 = list(tuval3.class_items.values())[0]
    check(oge3.rect().width() > dar3, "uye eklenince sinif kutusu GENISLEDI",
          "%.0f -> %.0f" % (dar3, oge3.rect().width()))
    fm3 = QFontMetricsF(oge3.f_member)
    for uye in (list(cm.classes["k"].attributes)
                + list(cm.classes["k"].operations)):
        check(fm3.elidedText(uye.label(), Qt.TextElideMode.ElideRight,
                             oge3.rect().width() - 24) == uye.label(),
              "sinif uyesi KIRPILMIYOR: %s" % uye.name)
    check(cm.classes["k"].w == 220.0,
          "sinif modeli bozulmadi", "w = %s" % cm.classes["k"].w)

    tuval.close()
    tuval2.close()
    tuval3.close()


# --------------------------------------------------------------------------- #
#  48) Kod paneli anahtari
# --------------------------------------------------------------------------- #

def test_code_panel_toggle() -> None:
    """Kod paneli seritten acilip kapanmali ve durumu TUTARLI olmali.

    YENIDEN URETILEN KUSUR: kullanici paneli kapatip cikinca, sonraki
    acilista panel `visible=True` kaliyor ama genisligi 0 oluyordu -- yani
    ekranda yok, Qt icin var. Menuden acmak `setVisible(True)` cagirir,
    panel zaten "gorunur" oldugu icin HICBIR SEY DEGISMEZ; kullaniciya
    dugme calismiyor gibi gelir. Ustelik seritte anahtari da yoktu:
    simulasyon panelinin aksine acmanin gorunur bir yolu bulunmuyordu.
    """
    print("\n== 48. kod paneli anahtari ==")

    from PyQt6.QtWidgets import QApplication, QToolBar

    from app.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])

    win = MainWindow()
    win.resize(1600, 900)
    win.show()
    for _ in range(6):
        app.processEvents()

    # -- serit uzerinde anahtar var mi --------------------------------------
    bar = win.findChild(QToolBar)
    eylemler = [a for a in bar.actions() if a is not None]
    check(win.a_code_panel in eylemler,
          "kod paneli anahtari SERITTE",
          "seritteki eylemler: %s"
          % [a.text() for a in eylemler if a.text()][:12])
    check(win.a_sim_panel in eylemler, "benzetim anahtari SERITTE")

    # Iki anahtar AYIRT EDILEBILIR olmali: ikisi de ayni goz simgesini
    # tasiyinca seritte hangisinin hangisi oldugu anlasilmiyordu.
    sim_c = win._action_icons.get(win.a_code_panel)
    sim_s = win._action_icons.get(win.a_sim_panel)
    check(sim_c is not None and sim_s is not None and sim_c is not sim_s,
          "iki panel anahtarinin simgeleri FARKLI",
          "%s vs %s" % (getattr(sim_c, "__name__", sim_c),
                        getattr(sim_s, "__name__", sim_s)))

    # -- ac / kapa tutarli mi ------------------------------------------------
    def pay():
        i = win.main_splitter.indexOf(win.code_panel)
        return win.main_splitter.sizes()[i] if i >= 0 else -1

    win.a_code_panel.setChecked(True)
    win.toggle_code_panel(True)
    for _ in range(4):
        app.processEvents()
    check(win.code_panel_open() and pay() > 0,
          "panel acikken GERCEKTEN yer kapliyor", "pay = %s" % pay())

    win.a_code_panel.setChecked(False)
    win.toggle_code_panel(False)
    for _ in range(4):
        app.processEvents()
    check(not win.code_panel.isVisible() and not win.code_panel_open(),
          "kapatinca hem gizli hem kapali", "pay = %s" % pay())

    # TEK tikla geri gelmeli.
    win.a_code_panel.trigger()
    for _ in range(4):
        app.processEvents()
    check(win.code_panel_open() and pay() > 0,
          "TEK tikla yeniden aciliyor", "pay = %s" % pay())

    # -- pay 0 iken acmak da ise yaramali -----------------------------------
    # `_restore_splitter_share` bagis yapacak komsu bulamazsa sessizce
    # donuyordu; `_force_panel_share` bu durumda devreye girer.
    boyutlar = win.main_splitter.sizes()
    i = win.main_splitter.indexOf(win.code_panel)
    toplam = sum(boyutlar)
    sifirli = [0] * len(boyutlar)
    sifirli[0] = toplam
    win.main_splitter.setSizes(sifirli)
    for _ in range(4):
        app.processEvents()
    win.a_code_panel.setChecked(True)
    win.toggle_code_panel(True)
    for _ in range(4):
        app.processEvents()
    check(pay() > 0, "pay sifira dusmusken bile acilabiliyor",
          "paylar = %s" % win.main_splitter.sizes())

    win.doc.mark_clean()
    win.class_doc.mark_clean()
    win.settings.clear()
    win.close()


# --------------------------------------------------------------------------- #
#  49) Ozellik diyaloglarinda bos alan METIN KUTULARINA gider
# --------------------------------------------------------------------------- #

def test_dialog_fields_take_the_space() -> None:
    """Pencere buyudukce buyuyen sey YAZI YAZILAN alan olmali.

    YENIDEN URETILEN KUSUR (735x888'lik bir State penceresinde olculdu):

        'State' bilgi etiketi -> 480 px
        entry / exit / do     ->  40 px  (setFixedHeight ile cakili)

    QFormLayout fazla yuksekligi ESNEYEBILEN satirlara dagitir. Kod
    alanlari sabit yukseklikte oldugu icin formdaki tek esnek satir en
    ustteki bilgi etiketiydi; pencerenin yarisi bos dururken kullanicinin
    yazacagi yer 40 pikselde kaliyordu.
    """
    print("\n== 49. diyalog alanlari bos alani aliyor mu ==")

    from PyQt6.QtWidgets import QApplication, QLabel

    from app.core.model import State, StateKind, Transition
    from app.ui.dialogs import StateDialog, TransitionDialog

    app = QApplication.instance() or QApplication([])

    st = State(id="s", name="Waiting", kind=StateKind.SIMPLE,
               entry="led_state(RED_LED_ON);\nled_state(GREEN_LED_ON);",
               do="get_alarm(curr_alarm_value);")

    olculer = {}
    for yukseklik in (320, 900):
        d = StateDialog(st)
        d.resize(735, yukseklik)
        d.show()
        app.processEvents()
        etiket = [w for w in d.findChildren(QLabel) if w.text() == "State"]
        olculer[yukseklik] = {
            "etiket": etiket[0].height() if etiket else -1,
            "entry": d.entry_edit.height(),
            "exit": d.exit_edit.height(),
            "do": d.do_edit.height(),
            "note": d.note_edit.height(),
        }
        d.close()

    kucuk, buyuk = olculer[320], olculer[900]
    check(buyuk["etiket"] <= kucuk["etiket"] + 4,
          "UML bilgi etiketi pencere buyuyunce BUYUMUYOR",
          "%d -> %d" % (kucuk["etiket"], buyuk["etiket"]))
    check(buyuk["etiket"] < 60,
          "bilgi etiketi kucuk kaliyor", "%d px" % buyuk["etiket"])
    for alan in ("entry", "exit", "do", "note"):
        check(buyuk[alan] > kucuk[alan],
              "'%s' alani pencereyle birlikte BUYUYOR" % alan,
              "%d -> %d" % (kucuk[alan], buyuk[alan]))
        check(kucuk[alan] >= 36,
              "'%s' alani dar pencerede bile en az iki satir" % alan,
              "%d px" % kucuk[alan])

    # -- gecis penceresi -----------------------------------------------------
    tr = Transition(id="t", source="a", target="b", event="BUTTON",
                    guard="ready(ctx)", action="set_alarm(v);")
    olc = {}
    for yukseklik in (320, 900):
        d = TransitionDialog(tr, "Idle", "Waiting", ["BUTTON"])
        d.resize(700, yukseklik)
        d.show()
        app.processEvents()
        yol = [w for w in d.findChildren(QLabel) if "\u2192" in w.text()]
        olc[yukseklik] = (yol[0].height() if yol else -1,
                          d.guard_edit.height(), d.action_edit.height())
        d.close()
    check(olc[900][0] <= olc[320][0] + 4,
          "gecis penceresinde de ust bilgi BUYUMUYOR",
          "%d -> %d" % (olc[320][0], olc[900][0]))
    check(olc[900][1] > olc[320][1] and olc[900][2] > olc[320][2],
          "guard ve effect alanlari pencereyle BUYUYOR",
          "guard %d->%d, effect %d->%d"
          % (olc[320][1], olc[900][1], olc[320][2], olc[900][2]))

    # -- cok satirli not kaydediliyor mu ------------------------------------
    d = StateDialog(State(id="s2", name="X", kind=StateKind.SIMPLE))
    d.note_edit.setPlainText("birinci satir\nikinci satir")
    hedef = State(id="s2", name="X", kind=StateKind.SIMPLE)
    d.apply_to(hedef)
    check(hedef.note == "birinci satir\nikinci satir",
          "cok satirli not modele KAYDEDILIYOR", repr(hedef.note))
    d.close()


def main() -> int:
    test_reserved_names()
    test_reserved_names_compile()
    test_uml_semantics()
    test_class_symbols()
    test_persistence()
    test_workspace_encoding()
    test_git()
    test_ui()
    test_layout_recovery()
    test_no_clipped_text()
    test_ui_appearance()
    test_generated_style()
    test_uml_naming()
    test_clipboard()
    test_spec_citations()
    test_theme()
    test_theme_contrast()
    test_composite_behaviors()
    test_simulation_default_and_labels()
    test_tool_menu_and_design_window()
    test_codegen_naming_and_standard()
    test_diagram_readability()
    test_no_demo_on_startup()
    test_removed_model_and_bends()
    test_model_diff()
    test_packaging()
    test_workspace_panel()
    test_single_top_bar()
    test_wheel_does_not_edit()
    test_left_drag_bends_transition()
    test_no_employer_branding()
    test_line_break_marker()
    test_code_panel_collapses()
    test_no_abbreviations_in_code()
    test_git_tab_hides_panels()
    test_visual_workspace_diff()
    test_user_facing_text_is_english()
    test_plantuml_matches_drawing()
    test_plantuml_layout_matches_canvas()
    test_c_and_cpp_layout_match()
    test_required_symbols_reported()
    test_spec_download_result()
    test_spec_viewer_does_not_freeze()
    test_alignment_guides()
    test_region_authoring()
    test_codegen_robustness()
    test_kind_change_panel()
    test_submachine_events_and_regions()
    test_every_kind_is_mapped()
    test_shortcuts_follow_focus()
    test_authoring_reaches_output()
    test_feature_reachable_and_shipped()
    test_simulation_and_examples()
    test_example_layout()
    test_boxes_fit_their_text()
    test_code_panel_toggle()
    test_dialog_fields_take_the_space()

    print("\n== Ozet ==")
    if _failures:
        print("  %d regresyon kontrolu basarisiz:" % len(_failures))
        for item in _failures:
            print("    - %s" % item)
        return 1
    print("  Tum regresyon kontrolleri gecti%s."
          % (" (%d bolum atlandi)" % _skipped if _skipped else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
