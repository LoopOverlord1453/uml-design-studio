"""Dogrulama kurallari -> OMG UML 2.5.1 spesifikasyonu atiflari.

Sorunlar panelindeki her bulgunun yaninda, ihlal edilen kuralin
spesifikasyondaki BOLUMU, BASILI SAYFA numarasi ve tek cumlelik ozeti
gosterilir. Boylece kullanici "neden hata veriyor" sorusunu aracin
soyledigiyle degil, belgeyle yanitlayabilir.

KAYNAK
------
OMG Unified Modeling Language (OMG UML), Version 2.5.1,
OMG belge numarasi ``formal/2017-12-05``.

SAYFA NUMARALARI ELLE YAZILMAZ. ``uml_spec_index.json`` belgenin
KENDISINDEN uretilir (``tools/build_spec_index.py``); burada yalnizca bolum
numarasi tutulur ve sayfa calisma aninda o dizinden okunur. Belge surumu
degisince dizin yeniden uretilir, bu tablo oldugu gibi kalir.

CUMLELER ALINTI DEGILDIR. Her ``rule`` alani, ilgili normatif kisitin
kendi sozlerimizle yazilmis tek cumlelik OZETIDIR; kisitin spesifikasyondaki
ADI ayrica ``constraint`` alaninda verilir, boylece okuyucu belgede birebir
arayabilir.

ARAC KISITI ile UML KURALI AYRILIR. Ad cakismasi, gecersiz C tanimlayicisi
gibi bulgular UML'in degil, KOD URETECININ kurallaridir; bunlarin spec
atfi YOKTUR ve panelde "tool rule" olarak gorunurler. Uydurma bir sayfa
numarasi vermek, belgeye bakan kullaniciyi yaniltirdi.
"""

from __future__ import annotations

import io
import json
import os
from dataclasses import dataclass
from typing import Dict, Optional

SPEC_TITLE = "OMG Unified Modeling Language (UML) 2.5.1"
SPEC_DOCUMENT = "formal/2017-12-05"
SPEC_URL = "https://www.omg.org/spec/UML/2.5.1/"

_INDEX_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "uml_spec_index.json")

_index_cache: Optional[Dict[str, dict]] = None


def _index() -> Dict[str, dict]:
    """Bolum -> {"page", "title"} dizini; dosya yoksa BOS sozluk.

    Dizin bulunamazsa arayuz calismaya devam eder, yalnizca sayfa numarasi
    gosterilmez. Eksik bir veri dosyasi yuzunden dogrulama panelini
    coketmek kabul edilemez.
    """
    global _index_cache
    if _index_cache is None:
        try:
            with io.open(_INDEX_PATH, encoding="utf-8") as fh:
                _index_cache = json.load(fh)
        except Exception:
            _index_cache = {}
    return _index_cache


@dataclass(frozen=True)
class SpecRef:
    """Bir dogrulama kuralinin spesifikasyondaki karsiligi."""

    section: str          #: "14.5.11.8"  (bos ise arac kurali)
    rule: str             #: ihlal edilen kuralin tek cumlelik ozeti
    constraint: str = ""  #: normatif kisitin belgedeki adi ("state_is_internal")

    @property
    def is_tool_rule(self) -> bool:
        """UML'de karsiligi olmayan, koda ozgu kural mi?"""
        return not self.section

    @property
    def page(self) -> Optional[int]:
        """Belgedeki BASILI sayfa numarasi (dizin yoksa None)."""
        kayit = _index().get(self.section)
        if kayit is None:
            return None
        return kayit.get("page")

    @property
    def section_title(self) -> str:
        kayit = _index().get(self.section)
        if kayit is None:
            return ""
        return kayit.get("title", "")

    def citation(self) -> str:
        """Panelde gosterilen kisa atif: 'UML 2.5.1 §14.5.11.8, p. 361'."""
        if self.is_tool_rule:
            return "Tool rule - no UML 2.5.1 counterpart"
        sayfa = self.page
        metin = "UML 2.5.1 §%s" % self.section
        if self.section_title:
            metin += " %s" % self.section_title
        if sayfa is not None:
            metin += ", p. %d" % sayfa
        if self.constraint:
            metin += "  [%s]" % self.constraint
        return metin


#: Arac kurali (UML atfi yok) icin kisayol.
def _tool(rule: str) -> SpecRef:
    return SpecRef(section="", rule=rule)


# --------------------------------------------------------------------------- #
#  Durum makinesi kurallari  (app/core/validator.py)
# --------------------------------------------------------------------------- #

STATE_MACHINE_RULES: Dict[str, SpecRef] = {
    # -- arac kurallari: uretilen C/C++ sembolleri -------------------------- #
    "V001": _tool("The symbol prefix becomes a C identifier in every generated "
                  "name, so it must itself be a valid C identifier."),
    "V010": _tool("State names become C enum constants, so they must be valid "
                  "C identifiers."),
    "V011": _tool("Two states whose names map to the same generated constant "
                  "would produce a duplicate enumerator."),
    "V012": _tool("Event names become C enum constants, so they must be valid "
                  "C identifiers."),
    "V013": _tool("The name collides with a constant the generator emits "
                  "itself (<PREFIX>_STATE_NONE / _COUNT)."),
    "V014": _tool("The name collides with an event constant the generator "
                  "emits itself (COUNT / INVALID / COMPLETION)."),
    "V015": _tool("Two events whose names map to the same generated constant "
                  "would produce a duplicate enumerator."),
    "V016": _tool("Two events that map to the same PascalCase name would "
                  "produce a duplicate C++ enum member."),
    "V017": _tool("The name collides with a member of the generated C++ "
                  "Event enumeration."),
    "V041": _tool("Guard and action text is copied verbatim into the generated "
                  "code, so brackets and quotes must balance."),
    "V090": _tool("Deeper hierarchies enlarge the generated entry/exit path "
                  "buffers and the stack they need."),

    # -- yapisal / kapsama --------------------------------------------------- #
    "V002": SpecRef(
        "14.2.3.2",
        "Every Region of a StateMachine owns a set of Vertices and needs a "
        "default activation, so an empty diagram cannot be executed."),
    "V020": SpecRef(
        "14.5.13",
        "Every Vertex must be contained in exactly one Region, so a missing "
        "container leaves the state unreachable by the semantics."),
    "V021": SpecRef(
        "14.2.3.4.2",
        "State containment forms a strict hierarchy, so a state cannot be a "
        "direct or transitive container of itself."),
    "V022": SpecRef(
        "14.5.9.7",
        "Only a composite State owns Regions, so a simple state cannot "
        "contain substates.",
        constraint="isComposite"),
    "V023": SpecRef(
        "14.5.9.7",
        "A State with no Region is a simple State; an empty composite adds a "
        "Region that never contributes to a state configuration.",
        constraint="isSimple"),

    # -- gecisler ------------------------------------------------------------ #
    "V030": SpecRef(
        "14.5.11.6",
        "A Transition has exactly one source Vertex, so an unattached end is "
        "not a well-formed Transition."),
    "V031": SpecRef(
        "14.5.11.6",
        "A Transition has exactly one target Vertex, so an unattached end is "
        "not a well-formed Transition."),
    "V032": SpecRef(
        "14.2.3.6",
        "A FinalState cannot have outgoing Transitions: reaching it completes "
        "the enclosing Region."),
    "V038": SpecRef(
        "14.5.11.8",
        "A Transition of kind internal must have a State as its source and "
        "its source and target must be the same.",
        constraint="state_is_internal"),
    "V039": SpecRef(
        "14.2.3.8.3",
        "A Transition without a Trigger is a completion Transition; the "
        "completion event is generated upon entry into the State, so such an "
        "internal Transition runs exactly once after the entry Behavior."),
    "V040": SpecRef(
        "14.5.11.8",
        "A Transition of kind local must have a composite State as its "
        "source and stay inside it.",
        constraint="state_is_local"),
    "V080": SpecRef(
        "14.2.3.9.3",
        "Transitions enabled by the same event are in conflict; the "
        "specification resolves them by priority, and equal priority leaves "
        "the choice undefined."),

    # -- initial sozde-durumu ------------------------------------------------ #
    "V033": SpecRef(
        "14.2.3.7",
        "An initial Pseudostate marks where a Region starts and is not a "
        "Transition target."),
    "V060": SpecRef(
        "14.2.3.7",
        "An initial Pseudostate marks where a Region starts and is not a "
        "Transition target."),
    "V034": SpecRef(
        "14.5.6.7",
        "The Transition leaving an initial Pseudostate may carry a behavior "
        "but must not carry a Trigger.",
        constraint="outgoing_from_initial"),
    "V035": SpecRef(
        "14.5.6.7",
        "The Transition leaving an initial Pseudostate may carry a behavior "
        "but must not carry a Guard.",
        constraint="outgoing_from_initial"),
    "V036": SpecRef(
        "14.2.3.4.5",
        "The default Transition of a Region must reach a Vertex inside that "
        "same Region."),
    # -- Bolgeler (UML 2.5.1, 14.2.3.2) ------------------------------------ #
    "V100": SpecRef(
        "14.2.3.2",
        "A Region owns a set of Vertices and Transitions that determine the "
        "behavioral flow within it; an orthogonal Region with no Vertex has "
        "no flow to execute."),
    # Bos bolge ve initial'siz bolge icin belge TEK bir davranis DAYATMAZ:
    # "no specific approach is defined if there is no initial Pseudostate...
    # One possible approach is to deem the model ill defined." Arac, bu iki
    # secenekten ILKINI secer ve modeli reddeder; ikinci secenek (bolge
    # etkin olmadan kalir) kod uretiminde sessiz bir yanlis davranisa
    # doner. Secim burada ACIKCA yazilidir.
    "V101": SpecRef(
        "14.2.3.2",
        "Default activation starts with the Transition originating from the "
        "initial Pseudostate of the Region, so that Transition must stay "
        "inside its own Region."),
    "V103": SpecRef(
        "14.2.3.2",
        "A composite State owns a fixed set of Regions, so every one of its "
        "Vertices must belong to one of them."),
    "V102": SpecRef(
        "14.2.3.7",
        "A fork splits a Transition into Vertices in orthogonal Regions and a "
        "join synchronizes Transitions coming from different orthogonal "
        "Regions; a plain Transition cannot cross a Region boundary."),
    # -- Fork / Join (UML 2.5.1, 14.5.6.7 ve 14.2.3.7) ---------------------- #
    "V120": SpecRef(
        "14.5.6.7",
        "In a complete StateMachine, a fork Vertex must have at least two "
        "outgoing Transitions and exactly one incoming Transition.",
        constraint="fork_vertex"),
    "V121": SpecRef(
        "14.5.6.7",
        "In a complete StateMachine, a fork Vertex must have at least two "
        "outgoing Transitions and exactly one incoming Transition.",
        constraint="fork_vertex"),
    "V122": SpecRef(
        "14.2.3.7",
        "The Transitions outgoing from a fork Pseudostate cannot have a "
        "guard or a trigger."),
    "V123": SpecRef(
        "14.5.6.7",
        "All transitions outgoing a fork vertex must target states in "
        "different regions of an orthogonal state.",
        constraint="transitions_outgoing"),
    "V124": SpecRef(
        "14.5.6.7",
        "In a complete StateMachine, a join Vertex must have at least two "
        "incoming Transitions and exactly one outgoing Transition.",
        constraint="join_vertex"),
    "V125": SpecRef(
        "14.5.6.7",
        "In a complete StateMachine, a join Vertex must have at least two "
        "incoming Transitions and exactly one outgoing Transition.",
        constraint="join_vertex"),
    "V126": SpecRef(
        "14.2.3.7",
        "Transitions terminating on a join Pseudostate cannot have a guard "
        "or a trigger."),
    "V127": SpecRef(
        "14.5.6.7",
        "All Transitions incoming a join Vertex must originate in different "
        "Regions of an orthogonal State.",
        constraint="transitions_incoming"),
    # -- Baglanti noktalari (UML 2.5.1, 14.2.3.7 ve 14.5.1.6) --------------- #
    "V140": SpecRef(
        "14.2.3.7",
        "An entryPoint or exitPoint Pseudostate provides encapsulation of "
        "the insides of a State or StateMachine, so it belongs to the State "
        "whose boundary it sits on."),
    "V141": SpecRef(
        "14.2.3.7",
        "In each Region of the composite State owning the entry point there "
        "is at most a single Transition from the entry point to a Vertex "
        "within that Region."),
    "V142": SpecRef(
        "14.2.3.7",
        "An entry point is the way into an encapsulated State; nothing "
        "reaches it if no Transition terminates on it."),
    "V143": SpecRef(
        "14.2.3.7",
        "The Transition leaving an entry point terminates on a Vertex within "
        "a Region of the State that owns the entry point."),
    "V144": SpecRef(
        "14.2.3.7",
        "In each Region of the composite State owning the entry point there "
        "is at most a single Transition from the entry point to a Vertex "
        "within that Region."),
    "V145": SpecRef(
        "14.2.3.7",
        "An entryPoint Pseudostate represents an entry point for the State, "
        "so it is reached from outside it."),
    "V146": SpecRef(
        "14.2.3.7",
        "An exitPoint Pseudostate is an exit point of the composite State, "
        "so exactly one Transition continues out of it."),
    "V147": SpecRef(
        "14.2.3.7",
        "Transitions terminating on an exit point within a Region of the "
        "composite State imply exiting of this composite State."),
    "V148": SpecRef(
        "14.2.3.7",
        "Transitions terminating on an exit point within any Region of the "
        "composite State implies exiting of this composite State."),
    "V149": SpecRef(
        "14.2.3.7",
        "An exitPoint Pseudostate is an exit point of the State, so the "
        "Transition leaving it continues outside that State."),
    # -- Altmakine (UML 2.5.1, 14.2.3.4.7) ---------------------------------- #
    "V160": SpecRef(
        "14.2.3.4.7",
        "A submachine State represents a reference to a corresponding "
        "submachine StateMachine, so it must name one."),
    "V161": SpecRef(
        "14.2.3.4.7",
        "A submachine State implies a macro-like insertion of the "
        "specification of the corresponding submachine StateMachine; its "
        "contents come from the reference, not from this document."),
    "V162": SpecRef(
        "14.2.3.4.7",
        "Submachines are distinct Behavior specifications, which may be "
        "defined in a different context than the one where they are used, "
        "so the referenced specification must exist."),
    # Dongu ve derinlik sinirinin UML'de KARSILIGI YOKTUR; bu bir ARAC
    # kuralidir: makro genislemesi dongusel bir referans grafiginde durmaz.
    "V163": SpecRef(
        "",
        "A submachine reference must expand to a finite machine: the tool "
        "refuses a circular or excessively deep reference chain."),
    "V166": _tool("A submachine is inserted into the referencing machine, so "
                  "its behaviour is compiled against that machine's context "
                  "type; a different context type cannot be honoured."),
    "V164": _tool("Expanding a submachine qualifies its states as "
                  "'Outer_Inner'; two of the resulting names must not map to "
                  "the same generated constant."),
    "V165": _tool("Expanding a submachine must not produce a state name that "
                  "is invalid as a C identifier."),
    # -- Ertelenen olaylar (UML 2.5.1, 14.2.3.4.4 ve 14.5.9.6) -------------- #
    "V180": SpecRef(
        "14.5.9.6",
        "deferrableTrigger is a feature of a State: a list of Triggers that "
        "are candidates to be retained by the StateMachine if they trigger "
        "no Transitions out of the State.",
        constraint="deferrableTrigger"),
    "V181": SpecRef(
        "",
        "A deferred event name must be a valid C identifier, because it "
        "becomes an enumerator in the generated code."),
    "V182": SpecRef(
        "14.5.9.6",
        "deferrableTrigger is a set of Trigger types; listing the same type "
        "twice adds nothing."),
    "V183": SpecRef(
        "14.2.3.4.4",
        "If a deferred Event type is used explicitly in a Trigger of a "
        "Transition whose source is the deferring State, the Transition "
        "wins: it is a kind of override option."),
    "V184": SpecRef(
        "14.2.3.4.4",
        "A deferred Event type is retained until a state configuration is "
        "reached where it is no longer deferred; an event no Transition ever "
        "uses would simply be held forever."),
    # -- Zaman olaylari (UML 2.5.1, TimeEvent) ------------------------------ #
    "V190": SpecRef(
        "13.3.3",
        "A TimeEvent specifies a point in time by an expression; a relative "
        "time event must say how long to wait."),
    "V191": SpecRef(
        "",
        "The delay expression is emitted into the generated code, so it must "
        "be a balanced C expression."),
    "V050": SpecRef(
        "14.2.3.2",
        "A Region needs an initial Pseudostate to define which Vertex is "
        "entered by default."),
    "V051": SpecRef(
        "14.2.3.2",
        "A Region can own at most one initial Pseudostate, otherwise its "
        "default activation is ambiguous."),
    "V052": SpecRef(
        "14.5.6.7",
        "An initial Vertex needs its single outgoing Transition to define "
        "the default state of the Region.",
        constraint="initial_vertex"),
    "V053": SpecRef(
        "14.5.6.7",
        "An initial Vertex can have at most one outgoing Transition.",
        constraint="initial_vertex"),
    "V054": SpecRef(
        "14.2.3.4.5",
        "The default Transition of a Region must enter a State; a history or "
        "terminate Pseudostate is not a valid default target."),

    # -- choice / junction --------------------------------------------------- #
    "V061": SpecRef(
        "14.5.6.7",
        "A choice or junction Vertex must have at least one incoming and one "
        "outgoing Transition.",
        constraint="choice_vertex / junction_vertex"),
    "V073": SpecRef(
        "14.2.3.4.6",
        "A branch Vertex exists to split a compound Transition, so a single "
        "outgoing path makes it redundant."),
    # Choice icin HATA, junction icin BILGI: belge ikisini ayirir. Choice
    # "ill formed" der; junction icin yalnizca bilesik gecisin devre disi
    # kaldigini soyler (ikisi de 14.2.3.7, basili s.313).
    "V062": SpecRef(
        "14.2.3.7",
        "If none of the Guards of a choice Vertex evaluates to true the "
        "model is ill formed; for a junction the compound transition is "
        "merely disabled."),
    "V063": SpecRef(
        "14.5.6.7",
        "A choice or junction Vertex must have at least one incoming "
        "Transition.",
        constraint="choice_vertex / junction_vertex"),
    "V037": SpecRef(
        "14.5.11.8",
        "A Transition leaving a Pseudostate other than initial must not "
        "carry a Trigger.",
        constraint="outgoing_pseudostates"),

    # -- tarih (history) ----------------------------------------------------- #
    "V046": SpecRef(
        "14.5.11.8",
        "A Transition leaving a Pseudostate other than initial must not "
        "carry a Trigger.",
        constraint="outgoing_pseudostates"),
    "V047": SpecRef(
        "14.2.3.4.4",
        "The default Transition of a history Pseudostate is taken whenever "
        "no history is stored, so it must not be conditional."),
    "V048": SpecRef(
        "14.2.3.4.4",
        "A history Pseudostate restores the last active configuration of its "
        "own Region, so its default Transition must stay in that Region."),
    "V064": SpecRef(
        "14.2.3.4.4",
        "History records the last active substate of a composite State's "
        "Region, so it is meaningless in the top-level Region."),
    "V065": SpecRef(
        "14.5.6.7",
        "History Vertices can have at most one outgoing Transition.",
        constraint="history_vertices"),
    "V066": SpecRef(
        "14.2.3.4.4",
        "A history Pseudostate is only reached by an incoming Transition; "
        "without one it can never restore anything."),
    "V068": SpecRef(
        "14.2.3.7",
        "At most one history Pseudostate of a given kind can be contained "
        "in a Region of a composite State, so a second one of the same kind "
        "in the same Region is ambiguous."),

    # -- sozde-durum davranislari -------------------------------------------- #
    "V067": SpecRef(
        "14.2.3.4.3",
        "entry, exit and doActivity Behaviors belong to States; a "
        "Pseudostate is only passed through and executes none of them."),
    "V069": SpecRef(
        "14.5.2.5",
        "A FinalState has no exit Behavior.",
        constraint="no_exit_behavior"),
    "V045": SpecRef(
        "14.2.3.7",
        "Entering a terminate Pseudostate ends the StateMachine, so no "
        "Transition can leave it."),
    "V070": SpecRef(
        "14.2.3.7",
        "A Pseudostate is transient and must be left through a Transition "
        "that leads elsewhere; a self-transition never completes."),

    # -- erisilebilirlik (model kalitesi) ------------------------------------ #
    "V071": _tool("A state with no outgoing transition can never be left; "
                  "the machine stays there forever."),
    "V072": _tool("A state that no transition reaches is dead weight in the "
                  "generated tables."),
}


# --------------------------------------------------------------------------- #
#  Sinif diyagrami kurallari  (app/core/class_validator.py)
# --------------------------------------------------------------------------- #

CLASS_RULES: Dict[str, SpecRef] = {
    # -- arac kurallari ------------------------------------------------------ #
    "C001": _tool("The symbol prefix becomes part of every generated name, so "
                  "it must be a valid C identifier."),
    "C010": _tool("Class names become C type names, so they must be valid C "
                  "identifiers."),
    "C011": _tool("Two classes with the same name would produce the same "
                  "generated type."),
    "C012": _tool("Attribute names become struct members, so they must be "
                  "valid C identifiers."),
    "C014": _tool("The generator needs a concrete type to emit a struct "
                  "member."),
    "C016": _tool("Operation names become C function names, so they must be "
                  "valid C identifiers."),
    "C017": _tool("C has no overloading, so two operations with the same name "
                  "would produce the same function."),
    "C018": _tool("Parameter names and types are copied into the generated "
                  "signature, so they must be valid C."),
    "C037": _tool("Two class names that map to the same snake_case symbol "
                  "would produce the same generated type."),
    "C039": _tool("A duplicated relation emits the same member twice."),
    "C060": _tool("Two members of the class map to the same generated C "
                  "symbol."),
    "C061": _tool("The attribute collides with the embedded 'base' member the "
                  "generator emits for inheritance."),
    "C062": _tool("Two relation ends map to the same generated member; give "
                  "one of them a role name."),
    "C063": _tool("The operation collides with the 'self' pointer of the "
                  "generated interface dispatch table."),
    "C065": _tool("The override signature differs from the supertype, so the "
                  "generated code would not compile."),

    # -- UML kurallari ------------------------------------------------------- #
    "C002": SpecRef(
        "11.4.2",
        "A Class diagram describes Classifiers and their relationships; with "
        "no Class there is nothing to describe."),
    "C013": SpecRef(
        "9.4.3",
        "The Properties owned by a Classifier are distinguishable, so the "
        "same attribute name cannot appear twice."),
    "C015": SpecRef(
        "7.5.3",
        "A MultiplicityElement declares a lower and an upper bound, written "
        "as lower..upper."),
    "C019": SpecRef(
        "9.4.3",
        "A Classifier that owns an abstract operation cannot be instantiated "
        "and must itself be abstract."),
    "C020": SpecRef(
        "10.4.3",
        "An Interface declares Operations and constants but owns no instance "
        "state of its own."),
    "C030": SpecRef(
        "11.5.3",
        "An Association connects two or more ends, so an unattached end is "
        "not a well-formed relationship."),
    "C031": SpecRef(
        "9.2.3.2",
        "A Generalization relates a specific Classifier to a different "
        "general one, so a Classifier cannot generalize itself."),
    "C032": SpecRef(
        "10.4.3",
        "A Class does not inherit from an Interface; it realizes it through "
        "an InterfaceRealization."),
    "C033": SpecRef(
        "10.4.3",
        "The supplier of an InterfaceRealization must be an Interface."),
    "C034": SpecRef(
        "11.5.3",
        "A composite end owns its parts by value, which an abstract "
        "Classifier cannot supply."),
    "C035": SpecRef(
        "7.5.3",
        "A MultiplicityElement declares a lower and an upper bound, written "
        "as lower..upper."),
    "C036": SpecRef(
        "9.2.3.2",
        "A Classifier may have several Generalizations, but the generated C "
        "code embeds only single inheritance."),
    "C038": SpecRef(
        "10.4.3",
        "An Interface can only specialize another Interface."),
    "C040": SpecRef(
        "9.2.3.2",
        "Generalization is a directed, acyclic relationship, so a Classifier "
        "cannot generalize itself transitively."),
    "C041": SpecRef(
        "11.5.3",
        "Composition is a whole-part relationship with existence dependency, "
        "so parts cannot own each other in a cycle."),
    "C050": SpecRef(
        "10.4.3",
        "A Classifier that realizes an Interface must provide every Operation "
        "the Interface declares."),
    "C064": SpecRef(
        "10.4.3",
        "Realizing both an Interface and its ancestor is redundant, since "
        "the derived Interface already includes the inherited Operations."),
}


ALL_RULES: Dict[str, SpecRef] = {}
ALL_RULES.update(STATE_MACHINE_RULES)
ALL_RULES.update(CLASS_RULES)


def lookup(code: str) -> Optional[SpecRef]:
    """Kural kodu -> spesifikasyon atfi; bilinmeyen kod icin None."""
    return ALL_RULES.get(code)


def describe(code: str) -> str:
    """Panelde gosterilecek tam metin: kural cumlesi + atif."""
    ref = lookup(code)
    if ref is None:
        return ""
    return "%s\n%s" % (ref.rule, ref.citation())
