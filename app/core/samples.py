"""Uygulama acilisinda yuklenen ornek model.

Bilerek "gomulu bir LED / hata yonetimi" senaryosu secildi; hiyerarsi, choice,
guard, internal gecis, completion gecisi ve final durumun tamamini kullanir.
Bu model `tools/verify_codegen.py` tarafindan gercekten derlenip kosturulur.
"""

from __future__ import annotations

from .model import State, StateKind, StateMachine, Transition, TransitionKind


def demo_machine() -> StateMachine:
    sm = StateMachine(
        name="Blinky",
        prefix="blinky",
        description="LED blink controller: an example of hierarchy, choice and an error counter.",
        context_type="blinky_ctx_t",
        user_includes='#include "blinky_ctx.h"',
    )

    def st(sid, name, kind, parent, x, y, w=170.0, h=84.0, **kw):
        return sm.add_state(State(id=sid, name=name, kind=kind, parent=parent,
                                  x=x, y=y, w=w, h=h, **kw))

    def tr(tid, src, dst, **kw):
        return sm.add_transition(Transition(id=tid, source=src, target=dst, **kw))

    # ------------------------------------------------------------- kok bolge #
    st("s_init", "Start", StateKind.INITIAL, None, 106, 30, 24, 24)

    st("s_off", "Off", StateKind.SIMPLE, None, 40, 90,
       entry="led_write(false);",
       note="LED off; the button starts the blink cycle.")

    st("s_run", "Running", StateKind.COMPOSITE, None, 400, 40, 560, 290,
       entry="ctx->blink_count = 0U;",
       exit="led_write(false);",
       do="ctx->uptime_ticks++;",
       note="Blink cycle. A FAULT event bumps the counter (internal transition).")

    st("s_check", "Check", StateKind.CHOICE, None, 250, 300, 38, 38)

    st("s_fault", "Fault", StateKind.SIMPLE, None, 40, 410,
       entry="fault_signal(true);",
       exit="fault_signal(false);",
       note="Repeated faults: waiting for RESET.")

    # UML 2.5.1: final durum entry/exit/do tasiyamaz; kapatma islemi buraya
    # goturen gecisin eylemindedir (t_shutdown).
    st("s_done", "Done", StateKind.FINAL, None, 1020, 150, 30, 30)

    # ------------------------------------------------- Running'in ic bolgesi #
    # Alt durumlarin koordinatlari ust duruma GORELIDIR.
    # LedOn / LedOff genis araliklarla yan yana durur; iki TICK gecisi
    # birbirinden yay ile ayrilir, etiketleri aradaki bosluga oturur.
    #
    # Y KONUMLARI, ust durumun DAVRANIS SERIDININ ALTINDAN baslar: bilesik
    # durum artik entry/exit/do satirlarinin ucunu de yaziyor (uc satir,
    # ~40 px) ve alt durumlar o seridin uzerine binmemeli.
    st("s_run_init", "RunStart", StateKind.INITIAL, "s_run", 46, 92, 24, 24)
    st("s_on", "LedOn", StateKind.SIMPLE, "s_run", 30, 140, 170, 78,
       entry="led_write(true);")
    st("s_offled", "LedOff", StateKind.SIMPLE, "s_run", 360, 140, 170, 78,
       entry="led_write(false);")

    # ---------------------------------------------------------------- gecisler #
    tr("t_init", "s_init", "s_off")
    tr("t_run_init", "s_run_init", "s_on")

    tr("t_start", "s_off", "s_run", event="BUTTON")
    tr("t_stop", "s_run", "s_check", event="BUTTON")
    tr("t_shutdown", "s_run", "s_done", event="SHUTDOWN",
       action="system_halt();")

    tr("t_fault", "s_run", "s_run", event="FAULT",
       kind=TransitionKind.INTERNAL, action="ctx->error_count++;")

    tr("t_chk_fault", "s_check", "s_fault", guard="ctx->error_count > 3U", priority=0)
    tr("t_chk_off", "s_check", "s_off", guard="else", priority=1)

    tr("t_reset", "s_fault", "s_off", event="RESET", action="ctx->error_count = 0U;")

    tr("t_blink_off", "s_on", "s_offled", event="TICK")
    tr("t_blink_on", "s_offled", "s_on", event="TICK", action="ctx->blink_count++;")

    return sm


def empty_machine() -> StateMachine:
    """Yeni bos dokuman: yalnizca baslangic + tek durum."""
    sm = StateMachine(name="NewMachine", prefix="sm",
                      description="", context_type="void", user_includes="")
    sm.add_state(State(id="s_init", name="Start", kind=StateKind.INITIAL,
                       x=96, y=40, w=24, h=24))
    sm.add_state(State(id="s_idle", name="Idle", kind=StateKind.SIMPLE,
                       x=40, y=110, w=170, h=84))
    sm.add_transition(Transition(id="t_init", source="s_init", target="s_idle"))
    return sm


# --------------------------------------------------------------------------- #
#  Sinif diyagrami ornekleri
# --------------------------------------------------------------------------- #

def demo_class_model():
    """Klasik 'Room' sinif diyagrami: arayuz, soyut sinif, kalitim,
    gerceklestirme ve iki composition iliskisini birlikte gosterir."""
    from .class_model import (Attribute, ClassModel, Operation, Parameter,
                              Relation, RelationKind, Stereotype, UmlClass)

    cm = ClassModel(
        name="RoomPlan", prefix="roomplan",
        description="Room layout model: an example of interfaces, inheritance and composition.")

    def attr(name, type_, vis="-", default="", mult=""):
        return Attribute(name=name, type=type_, visibility=vis,
                         default=default, multiplicity=mult)

    def op(name, ret="void", vis="+", params=(), abstract=False, const=False):
        return Operation(name=name, return_type=ret, visibility=vis,
                         params=[Parameter(name=n, type=t) for n, t in params],
                         abstract=abstract, const=const)

    # Iliskiler asagida SABIT KIMLIKLERLE kuruluyor; donen nesneleri
    # degiskene almak gereksiz.
    cm.add_class(UmlClass(
        id="c_drawable", name="Drawable", stereotype=Stereotype.INTERFACE,
        x=40, y=60, w=220, h=110,
        operations=[op("redraw"), op("hide")]))

    cm.add_class(UmlClass(
        id="c_room", name="Room", x=560, y=30, w=240, h=190,
        attributes=[attr("x", "double"), attr("y", "double"),
                    attr("height", "double"), attr("width", "double")],
        operations=[op("remove"), op("clone", ret="Room")]))

    cm.add_class(UmlClass(
        id="c_furniture", name="Furniture", x=60, y=330, w=230, h=170,
        stereotype=Stereotype.ABSTRACT,
        attributes=[attr("height", "double"), attr("width", "double"),
                    attr("color", "uint32_t")],
        operations=[op("getType", ret="uint8_t", const=True, abstract=True)]))

    cm.add_class(UmlClass(
        id="c_structure", name="Structure", x=600, y=330, w=240, h=110,
        stereotype=Stereotype.ABSTRACT,
        operations=[op("loadBearing", ret="bool", const=True, abstract=True)]))

    cm.add_class(UmlClass(
        id="c_couch", name="Couch", x=60, y=590, w=230, h=160,
        attributes=[attr("type", "uint8_t"), attr("material", "uint8_t")],
        operations=[op("numSeats", ret="uint8_t", const=True),
                    op("getType", ret="uint8_t", const=True)]))

    cm.add_class(UmlClass(
        id="c_window", name="Window", x=450, y=560, w=240, h=190,
        attributes=[attr("opacity", "double"), attr("isOpen", "bool")],
        operations=[op("close"), op("open"),
                    op("isOpened", ret="bool", const=True),
                    op("loadBearing", ret="bool", const=True)]))

    cm.add_class(UmlClass(
        id="c_wall", name="Wall", x=760, y=560, w=240, h=150,
        attributes=[attr("insideColor", "uint32_t"),
                    attr("outsideColor", "uint32_t")],
        operations=[op("loadBearing", ret="bool", const=True)]))

    def rel(rid, src, dst, kind, **kw):
        cm.add_relation(Relation(id=rid, source=src, target=dst, kind=kind, **kw))

    rel("r_realize", "c_room", "c_drawable", RelationKind.REALIZATION)
    rel("r_furn", "c_room", "c_furniture", RelationKind.COMPOSITION,
        source_mult="1", target_mult="0..*", target_role="furniture")
    rel("r_struct", "c_room", "c_structure", RelationKind.COMPOSITION,
        source_mult="1", target_mult="0..*", target_role="structures")
    rel("r_couch", "c_couch", "c_furniture", RelationKind.GENERALIZATION)
    rel("r_window", "c_window", "c_structure", RelationKind.GENERALIZATION)
    rel("r_wall", "c_wall", "c_structure", RelationKind.GENERALIZATION)
    return cm


def empty_class_model():
    """Yeni bos sinif diyagrami."""
    from .class_model import ClassModel, UmlClass
    cm = ClassModel(name="NewDesign", prefix="design")
    cm.add_class(UmlClass(id="c_first", name="NewClass", x=60, y=60))
    return cm
