import os
import sys

# Kok, DOSYANIN KENDISINDEN bulunur. Sabit bir mutlak yol yalnizca onu yazan
# makinede calisir ve o makinenin kullanici adini depoya sokar.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.core.samples import demo_machine
from app.core.validator import validate, has_errors
from app.codegen.c_generator import generate_c
from app.codegen.cpp_generator import generate_cpp
from app.core.model import State, StateKind, StateMachine, Transition
from app.core.simulator import Simulator

sm = demo_machine()
issues = validate(sm)
print("demo hatasiz:", not has_errors(issues))
fc = generate_c(sm)
print("C dosyalari:", sorted(fc))
fcpp = generate_cpp(sm)
print("C++ dosyalari:", sorted(fcpp))

# History + terminate makinesi
m = StateMachine(name="Hist", prefix="hist")
def st(sid, name, kind, parent=None, x=0, y=0):
    m.add_state(State(id=sid, name=name, kind=kind, parent=parent, x=x, y=y))
def tr(tid, s, d, **kw):
    m.add_transition(Transition(id=tid, source=s, target=d, **kw))
st("i", "I", StateKind.INITIAL, y=10)
st("off", "Off", StateKind.SIMPLE, y=100)
st("run", "Run", StateKind.COMPOSITE, y=200)
st("ri", "RI", StateKind.INITIAL, "run")
st("h", "H", StateKind.SHALLOW_HISTORY, "run", y=50)
st("a", "A", StateKind.SIMPLE, "run", y=100)
st("b", "B", StateKind.SIMPLE, "run", y=200)
st("kill", "Kill", StateKind.TERMINATE, y=400)
tr("t1", "i", "off")
tr("t2", "ri", "a")
tr("t3", "off", "h", event="RESUME")
tr("t4", "run", "off", event="PAUSE")
tr("t5", "a", "b", event="STEP")
tr("t6", "b", "a", event="STEP")
tr("t7", "off", "kill", event="KILL")
iss = validate(m)
print("hist model hatalari:", [str(i) for i in iss if i.severity == "error"])
sim = Simulator(m)
sim.start()
print("start ->", sim.state_name)
sim.dispatch("RESUME"); print("RESUME ->", sim.state_name)
sim.dispatch("STEP");   print("STEP   ->", sim.state_name)
sim.dispatch("PAUSE");  print("PAUSE  ->", sim.state_name)
sim.dispatch("RESUME"); print("RESUME ->", sim.state_name)
assert sim.state_name == "B", "sig tarih B'yi geri yuklemeliydi"
sim.dispatch("PAUSE")
sim.dispatch("KILL");   print("KILL   -> terminated:", sim.is_terminated())
assert sim.is_terminated()
assert not sim.dispatch("RESUME")
fh = generate_c(m)
print("hist C uretildi:", sorted(fh))
assert "hist_resolve_history" in fh["hist.c"]
assert "terminated" in fh["hist.c"]
assert "hist_hist_default" in fh["hist.c"]
fhp = generate_cpp(m)
assert "resolveHistory" in fhp["Hist.cpp"]
assert "history_[kParent[cur]] = cur" in fhp["Hist.cpp"]
print("TAMAM - cekirdek degisiklikler calisiyor")
