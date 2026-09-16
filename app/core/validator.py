"""Model dogrulayici.

Kod uretmeden ONCE calisir. Amac: uretilen C/C++ kodunun her zaman derlenebilir
ve anlamsal olarak tutarli olmasi. Bir tek ERROR varsa kod uretimi durdurulur.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Set

from .model import (StateKind, StateMachine, TransitionKind,
                    time_event_delay)
from .naming import pascal, screaming_snake

IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Uretecin kendi sembolleriyle cakisan adlar. Liste tahmine degil, her adayin
# GERCEKTEN derlenip derlenmedigine bakan bir olcume dayanir; buradaki her
# giris icin tools/test_reserved_names.py bir regresyon vakasi tutar.
#
#   <PREFIX>_STATE_COUNT / <PREFIX>_STATE_NONE  makrolari, ayni prefixli durum
#   enum sabitleriyle cakisir; olay tarafinda ayrica EVENT_COUNT / EVENT_INVALID
#   / EVENT_COMPLETION vardir.
RESERVED_STATE_NAMES = {"NONE", "COUNT"}
RESERVED_EVENT_NAMES = {"COUNT", "INVALID", "COMPLETION"}

#: C++ 'enum class Event' icinde ureteci tarafindan zaten tanimli uyeler
RESERVED_EVENT_PASCAL = {"Completion", "Invalid"}

# C ve C++ anahtar kelimeleri - durum/olay adi olarak kullanilamaz.
C_KEYWORDS: Set[str] = {
    "auto", "break", "case", "char", "const", "continue", "default", "do",
    "double", "else", "enum", "extern", "float", "for", "goto", "if", "inline",
    "int", "long", "register", "restrict", "return", "short", "signed",
    "sizeof", "static", "struct", "switch", "typedef", "union", "unsigned",
    "void", "volatile", "while", "bool", "true", "false", "class", "namespace",
    "template", "typename", "public", "private", "protected", "virtual", "new",
    "delete", "this", "operator", "try", "catch", "throw", "using", "nullptr",
    "constexpr", "noexcept", "explicit", "friend", "mutable", "static_assert",
}

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


@dataclass
class Issue:
    severity: str           # "error" | "warning" | "info"
    code: str               # V001 gibi kararli bir kod
    message: str
    element_id: Optional[str] = None   # tiklaninca secilecek eleman

    @property
    def is_error(self) -> bool:
        return self.severity == "error"

    def __str__(self) -> str:
        return "[%s] %s: %s" % (self.severity.upper(), self.code, self.message)


def _valid_ident(name: str) -> bool:
    return bool(IDENT_RE.match(name)) and name not in C_KEYWORDS


def _balanced(expr: str) -> bool:
    """Parantez/kose/kume dengesini ve tirnak kapanisini kabaca dogrular."""
    stack: List[str] = []
    pairs = {")": "(", "]": "[", "}": "{"}
    i = 0
    n = len(expr)
    while i < n:
        ch = expr[i]
        if ch in ("'", '"'):
            quote = ch
            i += 1
            while i < n:
                if expr[i] == "\\":
                    i += 2
                    continue
                if expr[i] == quote:
                    break
                i += 1
            if i >= n:
                return False           # kapanmamis tirnak
        elif ch in "([{":
            stack.append(ch)
        elif ch in ")]}":
            if not stack or stack.pop() != pairs[ch]:
                return False
        i += 1
    return not stack


def _baglamli(tip: str) -> bool:
    """Makine bir KULLANICI BAGLAMI tasiyor mu (`void` tasimaz)."""
    return (tip or "").strip() not in ("", "void")


def _submachine_flatten(sm, resolve):
    """Duzlestirmeyi DENER; hata varsa yukseltir (dongu, derinlik, eksik).

    Girdi degistirilmez; `flatten` genisletilmis bir KOPYA dondurur.
    """
    from .submachine import flatten
    return flatten(sm, resolve)


def _sorumlu_altmakine(sm, genis_id: str):
    """Genisletilmis bir durumu URETEN altmakine durumunun kimligi.

    Yerine koyma, ic durumlara `<disId>__<icId>` bicimli kimlikler verir
    ve ic ice gecmede bu ONE eklenmeye devam eder. Ilk parca, daima
    KULLANICININ modelindeki altmakine durumudur.

    :return: kimlik, ya da bu durum genisletmeden gelmiyorsa None
    """
    if "__" not in genis_id:
        return None
    dis = genis_id.split("__", 1)[0]
    if dis in sm.states:
        return dis
    return None


def _hatanin_sahibi(sm, metin: str):
    """Duzlestirme hatasinin metninde ADI GECEN altmakine durumu.

    `SubmachineError` iletileri sucluyu tirnak icinde adlandirir. Tuval
    hatayi bir ogeye baglayarak kirmiziya boyadigi icin, dogru ogeyi
    bulmak kullaniciyi masum bir duruma yollamamak demektir.

    :return: kimlik, ya da ad tek basina secilemiyorsa None
    """
    adaylar = [x.id for x in sm.ordered_states()
               if x.kind is StateKind.SUBMACHINE and x.name
               and ("'%s'" % x.name) in metin]
    if len(adaylar) == 1:
        return adaylar[0]
    return None


def _genisletilmis_ad_sorunlari(sm, duz):
    """Duzlestirmeden SONRA dogan ad cakismalarini dondurur.

    Altmakine yerine kondugunda ic durumlar `Disari_Iceri` bicimine
    cevrilir. Bu ad, ana makinede ZATEN VAR OLAN bir durumun adiyla ayni
    C sabitine dusebilir. Kullanicinin iki diyagrami da tek basina
    kusursuz gorunur; cakisma yalnizca genisletilmis modelde vardir.

    Eskiden bu model HIC DOGRULANMIYORDU: dogrulayici yalnizca
    duzlestirmenin BASARILI OLDUGUNA bakiyor, sonucuna bakmiyordu.
    Cakisma, musterinin derleyicisinde "redeclaration of enumerator"
    olarak ortaya cikiyordu -- aracin hicbir uyarisi olmadan.

    SORUMLU OGE de dondurulur. Tuval her hatayi bir ogeye baglayip
    kirmiziya boyar; sorun hangi altmakine durumunun genisletilmesinden
    dogduysa o isaretlenmelidir. Once hepsi DOSYADAKI ILK altmakine
    durumuna baglaniyordu ve kullanici, kusursuz olan bir ogeye
    yonlendiriliyordu.

    :return: (kod, mesaj, sorumlu_id) uclulari; sorumlu_id None olabilir
    """
    sorunlar = []
    gorulen = {}
    for st in duz.ordered_states():
        if st.kind is StateKind.INITIAL:
            continue
        sorumlu = _sorumlu_altmakine(sm, st.id)
        if not _valid_ident(st.name):
            sorunlar.append((
                "V165",
                "Expanding the submachines produces the state name '%s', "
                "which is not a valid C identifier." % st.name,
                sorumlu))
            continue
        anahtar = screaming_snake(st.name)
        onceki = gorulen.get(anahtar)
        if onceki is not None:
            # Cakismanin iki tarafindan GENISLETMEDEN GELENI isaretle:
            # kullanicinin duzeltecegi sey odur (ya ic durumun adi, ya
            # da onu niteleyen altmakine durumunun adi).
            hedef = sorumlu if sorumlu is not None else onceki[1]
            if onceki[0] != st.name:
                sorunlar.append((
                    "V164",
                    "Expanding the submachines produces two states, '%s' and "
                    "'%s', that generate the same constant '%s'. Rename one "
                    "of them or the submachine state that qualifies it."
                    % (onceki[0], st.name, anahtar),
                    hedef))
            else:
                sorunlar.append((
                    "V164",
                    "Expanding the submachines produces two states both named "
                    "'%s', which generate the same constant '%s'. Rename the "
                    "submachine state that qualifies one of them."
                    % (st.name, anahtar),
                    hedef))
        else:
            gorulen[anahtar] = (st.name, sorumlu)
    return sorunlar


def _bolge_ayrimi(sm, dugum, idler, ne: str):
    """Verilen dugumler AYNI ortogonal durumun FARKLI bolgelerinde mi?

    UML 2.5.1, 14.5.6.7 (basili s.350-351): fork'tan cikan gecisler
    "must target states in different regions of an orthogonal state",
    join'e giren gecisler ise "must originate in different Regions of an
    orthogonal State".
    """
    idler = [x for x in idler if x in sm.states]
    if len(idler) < 2:
        return None
    ortak = idler[0]
    for baska in idler[1:]:
        ortak = sm.lca(ortak, baska)
        if ortak is None:
            break
    if ortak is None or not sm.is_orthogonal(ortak):
        return "The segments of '%s' must %s." % (dugum.name, ne)
    bolgeler = []
    for x in idler:
        b = _bolge_dizini(sm, x, ortak)
        if b is None:
            return "The segments of '%s' must %s." % (dugum.name, ne)
        bolgeler.append(b)
    if len(set(bolgeler)) != len(bolgeler):
        return ("Two segments of '%s' use the SAME region of '%s'; they must "
                "%s." % (dugum.name, sm.states[ortak].name, ne))
    return None


def _bolge_dizini(sm, sid: str, sahip: str):
    """`sid`in, `sahip` altindaki hangi bolgeye dustugu (yoksa None)."""
    cur = sid
    n = 0
    while cur is not None and n <= len(sm.states) + 1:
        st = sm.states.get(cur)
        if st is None:
            return None
        if st.parent == sahip:
            return sm.region_of(cur)
        cur = st.parent
        n += 1
    return None


def validate(sm: StateMachine, resolve=None) -> List[Issue]:
    """Modeli dogrular ve sorun listesini onem sirasina gore dondurur.

    ``resolve``: altmakine referanslarini cozen islev. Verilmezse
    referansin VARLIGI sinanmaz -- yalnizca yapisal kurallar bakilir.
    Boylece arac calisma alani disinda da (testler, hizli onizleme)
    calisabilir ama uygulama gercek cozumleyiciyi verdiginde eksik ya da
    dongusel bir referans YAKALANIR.
    """
    issues: List[Issue] = []
    #: Genisletme sorunlari YALNIZCA BIR KEZ bildirilir.
    _genisletme_bildirildi: List[bool] = []
    #: Duzlestirme SONUCU: [(genisletilmis_makine, hata)] -- en fazla bir oge.
    #:
    #: Duzlestirme, altmakine durumu BASINA bir kez yapiliyordu ve her
    #: cagri makinenin TAMAMINI deepcopy ile kopyaliyor. Maliyet karesel
    #: buyuyordu: 8 altmakine durumunda dogrulama 0.067 sn, 32'de bir
    #: saniyeyi asiyordu -- ve dogrulama HER DUZENLEMEDEN sonra kosar.
    #: Makine dogrulama boyunca degismedigi icin sonuc paylasilabilir.
    _duz_sonuc: List = []

    def err(code, msg, eid=None):
        issues.append(Issue("error", code, msg, eid))

    def warn(code, msg, eid=None):
        issues.append(Issue("warning", code, msg, eid))

    def info(code, msg, eid=None):
        issues.append(Issue("info", code, msg, eid))

    def _genisletilmis():
        """Genisletilmis modeli BIR KEZ hesaplar.

        :return: (makine, hata) -- makine None ise ya altmakine yoktur,
                 ya cozumleyici verilmemistir, ya da genisletme
                 basarisiz olmustur (o zaman `hata` doludur).
        """
        if not _duz_sonuc:
            altmakine_var = any(x.kind is StateKind.SUBMACHINE
                                for x in sm.states.values())
            if resolve is None or not altmakine_var:
                _duz_sonuc.append((None, None))
            else:
                try:
                    _duz_sonuc.append((_submachine_flatten(sm, resolve), None))
                except Exception as exc:            # noqa: BLE001
                    _duz_sonuc.append((None, exc))
        return _duz_sonuc[0]

    # ---------------------------------------------------------------- makine #
    if not _valid_ident(sm.prefix):
        err("V001", "Symbol prefix '%s' is not a valid C identifier." % sm.prefix)
    if not sm.states:
        err("V002", "Diagram is empty: at least one initial pseudostate and one state are required.")
        return issues

    # ------------------------------------------------------------- adlandirma #
    # Initial disindaki her dugum uretilen enum'a girer; adi C tanimlayicisi olmali.
    real_states = [s for s in sm.ordered_states()
                   if s.kind is not StateKind.INITIAL]
    seen_names = {}
    for s in real_states:
        if not _valid_ident(s.name):
            err("V010", "'%s' is not a valid C identifier (state name)." % s.name, s.id)
        # Anahtar, uretecin YAZACAGI sabittir (naming.screaming_snake).
        # Duz .upper() ile bakmak yanlis cevap verir: 'LedOn' ile 'Led_On'
        # ayni '..._LED_ON' sabitini uretir ama .upper() farkli gorunur.
        key = screaming_snake(s.name)
        if key in seen_names:
            err("V011", "States '%s' and '%s' generate the same "
                        "'<PREFIX>_STATE_%s' constant."
                % (seen_names[key][1], s.name, key), s.id)
        seen_names[key] = (s.id, s.name)

    # Uretecin kendi urettigi sembollerle cakismalar
    for s in real_states:
        sym = screaming_snake(s.name)
        if sym in RESERVED_STATE_NAMES:
            err("V013", "State name '%s' conflicts with the generated "
                        "'<PREFIX>_STATE_%s' constant; choose another name."
                % (s.name, sym), s.id)

    # OLAY KURALLARI GENISLETILMIS MODEL UZERINDE KOSAR.
    #
    # Altmakine yerine kondugunda ic makinenin olaylari da uretilen
    # enum'a girer -- ve ADLARI NITELENMEZ (durumlar `Disari_Iceri`
    # olur, olaylar oldugu gibi kalir). Denetim genisletilmemis modele
    # bakinca disaridaki `DO_IT` ile icerideki `DoIt` HIC KARSILASMIYOR:
    # dogrulama tertemiz geciyor, uretilen baslikta ayni sabit iki kez
    # tanimlaniyor ve musterinin derleyicisi "redeclaration of
    # enumerator" diyordu. Ayni acik V012/V014/V017 ile zaman olayi
    # kurallarini da (V190/V191) altmakineden gelen olaylar icin
    # devre disi birakiyordu.
    _genis_model, _ = _genisletilmis()
    if _genis_model is not None:
        olay_kaynagi = _genis_model
    else:
        olay_kaynagi = sm

    def tran_of(event_name: str):
        """Olayi tasiyan gecisin kimligi.

        Genisletmeden gelen olaylarin gecis kimligi KULLANICININ
        modelinde yoktur; tuval o kimligi isaretleyemez. Boyle bir
        durumda sorun, olayi getiren altmakine durumuna baglanir.
        """
        kendi = next((t.id for t in sm.transitions.values()
                      if t.event.strip() == event_name), None)
        if kendi is not None:
            return kendi
        return next((x.id for x in sm.ordered_states()
                     if x.kind is StateKind.SUBMACHINE), None)

    for ev in olay_kaynagi.events():
        if screaming_snake(ev) in RESERVED_EVENT_NAMES:
            err("V014", "Event name '%s' is reserved by the generator; choose "
                        "another name." % ev, tran_of(ev))

    seen_events = {}
    seen_pascal = {}
    for ev in olay_kaynagi.events():
        # ZAMAN OLAYI ayri bir bicimdedir: `after(<ifade>)`.
        #
        # MUAFIYET YALNIZCA TANIMLAYICI KURALINA AITTIR. Zaman olayi
        # once tumden atlaniyordu (`continue`) ve asagidaki cakisma
        # denetimlerine hic girmiyordu. Oysa uretilen sabit ayni yoldan
        # gecer: `after(50)` ile `AFTER50` da, `after(1.5)` ile
        # `after(15)` de AYNI `<PREFIX>_EVENT_...` sabitini uretir.
        # Sonuc, uretilen baslikta CIFT ENUM SABITI ve derlenmeyen bir
        # dosyaydi -- arac hicbir sey soylemeden.
        gecikme = time_event_delay(ev)
        if gecikme is not None:
            if not gecikme:
                err("V190", "The time event '%s' has no delay; write "
                            "after(100) or after(MY_TIMEOUT_MS)." % ev,
                    tran_of(ev))
            elif not _balanced(gecikme):
                err("V191", "The delay of the time event '%s' has unbalanced "
                            "brackets or quotes." % ev, tran_of(ev))
        elif not _valid_ident(ev):
            err("V012", "Event name '%s' is not a valid C identifier." % ev,
                tran_of(ev))
        esym = screaming_snake(ev)
        if esym in seen_events:
            err("V015", "Events '%s' and '%s' generate the same "
                        "'<PREFIX>_EVENT_%s' constant."
                % (seen_events[esym], ev, esym),
                tran_of(ev))
        seen_events[esym] = ev

        # C++ olay sabitleri PascalCase'e cevrilir; 'MY_EVENT' ile 'MyEvent'
        # ayni uyeye duser ve uretilen .hpp derlenmez.
        pev = pascal(ev)
        if pev in RESERVED_EVENT_PASCAL:
            err("V017", "Event name '%s' conflicts with the generator's own "
                        "'Event::%s' constant in the C++ output; choose another name."
                % (ev, pev), tran_of(ev))
        if pev in seen_pascal and seen_pascal[pev] != ev:
            err("V016", "Events '%s' and '%s' generate the same 'Event::%s' "
                        "constant in the C++ output."
                % (seen_pascal[pev], ev, pev), tran_of(ev))
        seen_pascal[pev] = ev

    # --------------------------------------------------------------- yapisal #
    for s in sm.states.values():
        if s.parent is not None and s.parent not in sm.states:
            err("V020", "The parent state of '%s' was not found." % s.name, s.id)
        if s.parent is not None and sm.is_descendant(s.parent, s.id):
            err("V021", "State '%s' has a circular hierarchy." % s.name, s.id)
        parent = sm.parent_of(s.id)
        if parent is not None and parent.kind is not StateKind.COMPOSITE:
            err("V022", "'%s' is inside non-composite state '%s'." % (s.name, parent.name), s.id)

    for s in sm.states.values():
        if s.kind is StateKind.COMPOSITE and not sm.children(s.id):
            warn("V023", "Composite state '%s' is empty; convert it to a simple state." % s.name, s.id)

    # ------------------------------------------------------------- gecisler  #
    for t in sm.transitions.values():
        src = sm.states.get(t.source)
        tgt = sm.states.get(t.target)
        if src is None:
            err("V030", "The source of a transition is undefined (not connected).", t.id)
            continue
        if tgt is None:
            err("V031", "The target of the transition leaving '%s' is undefined (not connected)." % src.name, t.id)
            continue

        if src.kind is StateKind.FINAL:
            err("V032", "A final state ('%s') cannot have an outgoing transition." % src.name, t.id)
        if src.kind is StateKind.TERMINATE:
            err("V045", "A terminate pseudostate ('%s') cannot have an outgoing transition."
                % src.name, t.id)
        if tgt.kind is StateKind.INITIAL:
            err("V033", "An initial pseudostate ('%s') cannot be the target of a transition." % tgt.name, t.id)

        if src.kind is StateKind.INITIAL:
            if t.event.strip():
                err("V034", "An initial transition cannot have an event.", t.id)
            if t.guard.strip():
                err("V035", "An initial transition cannot have a guard.", t.id)
            if tgt.parent != src.parent:
                err("V036", "An initial transition cannot leave its own region "
                            "('%s' -> '%s')." % (src.name, tgt.name), t.id)

        if src.kind.is_branch and t.event.strip():
            err("V037", "Transitions leaving a %s pseudostate cannot have an event "
                        "(guard only)."
                % ("Choice" if src.kind is StateKind.CHOICE else "Junction"), t.id)

        if src.kind.is_history:
            if t.event.strip():
                err("V046", "A history default transition cannot have an event.", t.id)
            if t.guard.strip():
                err("V047", "A history default transition cannot have a guard.", t.id)
            if tgt.parent != src.parent:
                err("V048", "The target of a history default transition must be in the same region "
                            "('%s' -> '%s')." % (src.name, tgt.name), t.id)

        if t.kind is TransitionKind.INTERNAL and t.source != t.target:
            err("V038", "An internal transition must have the same source and target "
                        "('%s' -> '%s')." % (src.name, tgt.name), t.id)
        if t.kind is TransitionKind.INTERNAL and not t.event.strip():
            # ESKI METIN YANLISTI: "never fires" deniyordu, oysa boyle bir
            # gecis durumun tamamlanma olayiyla tetiklenir ve TAM BIR KEZ
            # calisir (UML 2.5.1, 14.2.3.8.3 -- tamamlanma olayi duruma
            # GIRILDIGINDE dogar). Onceki motor onu 16 kez calistiriyordu;
            # bu duzeltildi, ama yapinin kendisi gecerlidir ve yalnizca
            # kolayca giris davranisiyla karistirildigi icin bildirilir.
            info("V039", "This internal transition is triggered by the state's "
                         "completion event, so it runs exactly once, right after "
                         "the entry behavior.", t.id)
        if t.kind is TransitionKind.LOCAL and not sm.is_descendant(t.target, t.source):
            err("V040", "The target of a local transition ('%s') must be inside the source ('%s')."
                % (tgt.name, src.name), t.id)

        # ORTOGONAL BOLGELER ARASINDA DUZ GECIS OLMAZ.
        #
        # UML 2.5.1, 14.2.3.7 (basili s.313): bolgeler arasina dallanmak
        # fork, birlestirmek join sozde-durumunun isidir. Duz bir ok iki
        # bolgeyi birbirine baglarsa kaynak bolge kapanir ama hedef bolge
        # halen etkindir; konfigurasyon tutarsiz kalir.
        # FORK / JOIN ve baglanti noktalari BU KURALDAN MUAFTIR: bolgeler
        # arasini gecmek zaten ONLARIN isidir. Muafiyet olmayinca, aracin
        # "fork ya da join kullanin" diyen mesaji tam da kullanici fork
        # cizdiginde beliriyor ve uc ozellik birden kullanilamaz hale
        # geliyordu. Ayrimi V120-V127 ile V140-V149 zaten denetler.
        _MUAF = (StateKind.FORK, StateKind.JOIN,
                 StateKind.ENTRY_POINT, StateKind.EXIT_POINT)
        ortak = sm.lca(t.source, t.target)
        if (ortak is not None and sm.is_orthogonal(ortak)
                and src.kind not in _MUAF and tgt.kind not in _MUAF
                and t.source != ortak and t.target != ortak):
            s_bolge = _bolge_dizini(sm, t.source, ortak)
            t_bolge = _bolge_dizini(sm, t.target, ortak)
            if s_bolge is not None and t_bolge is not None and s_bolge != t_bolge:
                err("V102", "This transition crosses from region %d to region %d "
                            "of orthogonal state '%s'; use a fork or a join."
                    % (s_bolge + 1, t_bolge + 1, sm.states[ortak].name), t.id)

        for label, expr in (("guard", t.guard), ("action", t.action)):
            if expr.strip() and not _balanced(expr):
                err("V041", "The transition's %s expression has unbalanced brackets/quotes." % label, t.id)

    # ---------------------------------------------------------- initial/bolge #
    #
    # KURALLAR BOLGE BASINADIR, durum basina degil. UML 2.5.1, 14.2.3.2
    # (basili s.307): bir bilesik durum bir ya da daha cok BOLGE sahibidir
    # ve HER bolgenin kendi varsayilan girisi vardir. Ortogonal bir durumda
    # iki initial bulunmasi dogrudur; onlari tek bir kaba koyup saymak
    # gecerli bir modeli reddederdi.
    sahipler: List[Optional[str]] = [None]
    sahipler += [s.id for s in sm.states.values()
                 if s.kind is StateKind.COMPOSITE]
    for sahip in sahipler:
        if sahip is not None and not sm.children(sahip):
            continue
        bolge_sayisi = sm.region_count(sahip)

        # BILDIRILEN BOLGE SAYISININ DISINDA COCUK OLMAMALI.
        #
        # `build_ir`, bir cocugun `region` alanini oldugu gibi kullanir:
        # iki bolgeli bir duruma `region=5` tasiyan bir cocuk konursa
        # uretilen tabloda DIYAGRAMDA HIC GORUNMEYEN bir bolge acilir,
        # `active[]` buyur ve o cocuk hicbir zaman etkinlesemeyecegi
        # halde koda girer. Hicbir kural bunu yakalamiyordu.
        #
        # Arayuz artik boyle bir model URETEMEZ (bolge, ogenin cizildigi
        # seritten okunur ve kucultme dolu bolgeyi silmeyi reddeder), ama
        # elle duzenlenmis ya da baska bir surumden gelen bir dosya
        # tasiyabilir. Cizim ile uretilen kod arasindaki her sessiz
        # ayrilik bildirilmelidir.
        if sahip is not None:
            for cocuk in sm.children(sahip):
                bolge_no = int(getattr(cocuk, "region", 0) or 0)
                if bolge_no < 0 or bolge_no >= bolge_sayisi:
                    err("V103",
                        "'%s' says it is in region %d of '%s', but that state "
                        "has only %d region(s). Drag it into one of the bands "
                        "shown in the diagram."
                        % (cocuk.name, bolge_no + 1,
                           sm.states[sahip].name, bolge_sayisi),
                        cocuk.id)
        for bolge in range(bolge_sayisi):
            icerik = sm.children_in(sahip, bolge)
            if sahip is None:
                rname = "root region"
            elif bolge_sayisi > 1:
                rname = "region %d of '%s'" % (bolge + 1, sm.states[sahip].name)
            else:
                rname = "'%s'" % sm.states[sahip].name
            if sahip is not None and bolge_sayisi > 1 and not icerik:
                err("V100", "%s is empty; every region of an orthogonal state "
                            "must contain at least one state." % rname, sahip)
                continue
            inits = [x for x in icerik if x.kind is StateKind.INITIAL]
            if not inits:
                err("V050", "%s has no initial pseudostate." % rname, sahip)
            elif len(inits) > 1:
                err("V051", "%s contains %d initial pseudostates; only one is "
                            "allowed." % (rname, len(inits)), inits[1].id)
            else:
                outs = sm.outgoing(inits[0].id)
                if not outs:
                    err("V052", "The initial pseudostate in %s has no outgoing "
                                "transition." % rname, inits[0].id)
                elif len(outs) > 1:
                    err("V053", "The initial pseudostate in %s has more than one "
                                "outgoing transition." % rname, inits[0].id)
                else:
                    tgt = sm.states.get(outs[0].target)
                    if tgt is not None and (tgt.kind.is_history
                                            or tgt.kind is StateKind.TERMINATE):
                        err("V054", "The initial transition in %s cannot target a "
                                    "history/terminate pseudostate ('%s'); target "
                                    "a state directly." % (rname, tgt.name),
                            outs[0].id)
                    elif tgt is not None and sm.region_of(tgt.id) != bolge:
                        err("V101", "The initial transition in %s targets '%s', "
                                    "which is in another region; a region's "
                                    "default entry must stay inside it."
                            % (rname, tgt.name), outs[0].id)

    # ------------------------------------------------- sozde-durum kisitlari #
    for s in sm.states.values():
        if s.kind is StateKind.INITIAL and sm.incoming(s.id):
            err("V060", "An initial pseudostate cannot have an incoming transition.", s.id)
        if s.kind.is_branch:
            kname = "choice" if s.kind is StateKind.CHOICE else "junction"
            outs = sm.outgoing(s.id)
            if not outs:
                err("V061", "The '%s' %s node has no outgoing transitions; the machine "
                            "cannot proceed once it reaches this node." % (s.name, kname), s.id)
            elif len(outs) < 2:
                warn("V073", "The '%s' %s node should have at least two outgoing transitions."
                     % (s.name, kname), s.id)
            has_else = any(not t.guard.strip() or t.guard.strip().lower() == "else"
                           for t in outs)
            if outs and not has_else:
                # CHOICE ile JUNCTION AYNI SEY DEGILDIR.
                #
                # UML 2.5.1, 14.2.3.7 (basili s.313) choice icin: "If none of
                # the guards evaluates to true, then the model is considered
                # ill formed." -- bu bir HATADIR.
                #
                # Ayni bolum junction icin bunu SOYLEMEZ; tam tersine:
                # "the entire compound transition is disabled even though its
                # Triggers are enabled." Yani yol bulunamazsa bilesik gecis
                # devre disi kalir, model bozuk olmaz. Junction'i hata saymak,
                # gecerli bir modelin kod uretimini engelliyordu.
                if s.kind is StateKind.CHOICE:
                    err("V062", "The '%s' choice node has no default (else / "
                                "unguarded) branch; if no guard holds the model "
                                "is ill-formed." % s.name, s.id)
                else:
                    info("V062", "The '%s' junction node has no default (else / "
                                 "unguarded) branch; if no path holds, the whole "
                                 "compound transition is simply disabled."
                                 % s.name, s.id)
            if not sm.incoming(s.id):
                warn("V063", "The '%s' %s node has no incoming transitions." % (s.name, kname), s.id)
        # ------------------------------------------------ fork / join kurallari
        #
        # Alintilarin tamami OMG UML 2.5.1'den birebir alinmistir; bkz.
        # app/core/uml_spec.py.
        if s.kind is StateKind.FORK:
            gelen = sm.incoming(s.id)
            giden = sm.outgoing(s.id)
            if len(gelen) != 1:
                err("V120", "Fork '%s' must have exactly one incoming "
                            "transition (it has %d)." % (s.name, len(gelen)),
                    s.id)
            if len(giden) < 2:
                err("V121", "Fork '%s' must have at least two outgoing "
                            "transitions (it has %d); with one target it is "
                            "an ordinary transition." % (s.name, len(giden)),
                    s.id)
            for t in giden:
                if t.guard.strip() or t.event.strip():
                    err("V122", "A transition leaving fork '%s' cannot carry a "
                                "guard or a trigger." % s.name, t.id)
            # Kod BURADA, cagri yerinde, DUZ YAZI olarak durur: atif
            # tablosunun eksiksizligini sinayan test kaynakta birebir
            # "V123" arar ve degiskenle verilen bir kodu goremez.
            sorun = _bolge_ayrimi(sm, s, [t.target for t in giden],
                                  "target states in different regions of an "
                                  "orthogonal state")
            if sorun:
                err("V123", sorun, s.id)

        if s.kind is StateKind.JOIN:
            gelen = sm.incoming(s.id)
            giden = sm.outgoing(s.id)
            if len(gelen) < 2:
                err("V124", "Join '%s' must have at least two incoming "
                            "transitions (it has %d)." % (s.name, len(gelen)),
                    s.id)
            if len(giden) != 1:
                err("V125", "Join '%s' must have exactly one outgoing "
                            "transition (it has %d)." % (s.name, len(giden)),
                    s.id)
            for t in gelen:
                if t.guard.strip() or t.event.strip():
                    err("V126", "A transition entering join '%s' cannot carry "
                                "a guard or a trigger." % s.name, t.id)
            sorun = _bolge_ayrimi(sm, s, [t.source for t in gelen],
                                  "originate in different regions of an "
                                  "orthogonal state")
            if sorun:
                err("V127", sorun, s.id)

        # --------------------------------------------- ertelenen olaylar
        if s.deferred:
            if not s.kind.is_real_state:
                err("V180", "'%s' is a pseudostate; only a state can defer "
                            "events." % s.name, s.id)
            # GECISLERDE kullanilan olaylar. `sm.events()` ertelenenleri de
            # icerdigi icin onu kullanmak, hicbir gecisin tuketmedigi bir
            # olayi "bilinen" sayar ve uyari HIC calismazdi.
            bilinen = {t.event.strip() for t in sm.transitions.values()
                       if t.event.strip()}
            gorulen = set()
            for ad in s.deferred:
                ad = str(ad).strip()
                if not ad:
                    continue
                if not IDENT_RE.match(ad):
                    err("V181", "'%s' is not a valid event name to defer in "
                                "'%s'." % (ad, s.name), s.id)
                elif ad in gorulen:
                    warn("V182", "'%s' is listed twice in the deferred events "
                                 "of '%s'." % (ad, s.name), s.id)
                gorulen.add(ad)
                # Bir durumun KENDI cikis gecisi ayni olayi tasiyorsa, UML
                # gecise oncelik verir ("a kind of override option"). Bu
                # GECERLIDIR ama kolayca yanlis anlasilir, bu yuzden
                # BILDIRILIR.
                for t in sm.outgoing(s.id):
                    if t.event.strip() == ad:
                        info("V183", "'%s' both defers '%s' and has a "
                                     "transition triggered by it; the "
                                     "transition wins." % (s.name, ad), s.id)
                        break
            bos = gorulen - bilinen
            if bos:
                warn("V184", "'%s' defers %s, which no transition uses."
                     % (s.name, ", ".join(sorted(bos))), s.id)

        # ------------------------------------------------------ altmakine
        if s.kind is StateKind.SUBMACHINE:
            ref = (s.submachine_ref or "").strip()
            if not ref:
                err("V160", "The submachine state '%s' does not reference a "
                            "machine; pick one in the properties panel."
                    % s.name, s.id)
            if sm.children(s.id):
                err("V161", "The submachine state '%s' contains states of its "
                            "own; its contents come from the referenced "
                            "machine. Move them out or make it a composite "
                            "state." % s.name, s.id)
            # BAGLANTI NOKTASI BAGLAMA (ConnectionPointReference) su an
            # DESTEKLENMEZ ve acikca reddedilir; bkz. core/submachine.py.
            if ref and resolve is not None:
                try:
                    hedef = resolve(ref)
                except Exception:               # noqa: BLE001
                    hedef = None
                if hedef is None:
                    err("V162", "The machine referenced by '%s' could not be "
                                "found: %s" % (s.name, ref), s.id)
                elif (_baglamli(hedef.context_type)
                      and hedef.context_type.strip()
                      != sm.context_type.strip()):
                    # BAGLAM TIPI SESSIZCE ATILIYORDU.
                    #
                    # Yerine koyma, ic makinenin entry/exit/do ve koruma
                    # metinlerini AYNEN tasir; o metinlerdeki `ctx`,
                    # genisletilmis makinenin baglam tipiyle derlenir.
                    # Iki tip farkliysa ic makinenin kodu YANLIS YAPIYA
                    # karsi derleniyor -- dogrulama tertemiz gecerken.
                    #
                    # Ic makinenin baglami 'void' ise sorun yoktur: o
                    # kod `ctx`e hic dokunmaz.
                    err("V166",
                        "The submachine state '%s' references '%s', whose "
                        "context type '%s' differs from this machine's "
                        "'%s'. Expanding it would compile the referenced "
                        "machine's behaviour against the wrong context. "
                        "Make the two match, or give the referenced machine "
                        "the context type 'void'."
                        % (s.name, hedef.name, hedef.context_type.strip(),
                           sm.context_type.strip()), s.id)
                elif not _genisletme_bildirildi:
                    # Genisletme ve onun denetimi TUM MAKINE icindir, tek
                    # bir altmakine durumu icin degil: bir kez yapilir ve
                    # sonuc paylasilir. Aksi halde ayni cakisma her
                    # altmakine durumu icin yeniden bildirilirdi.
                    _genisletme_bildirildi.append(True)
                    genis, hata = _genisletilmis()
                    if hata is not None:
                        # Ileti sucluyu ZATEN adlandirir; hatayi o ogeye
                        # bagla. Bulunamazsa HICBIR ogeye baglama --
                        # rastgele bir altmakine durumunu kirmiziya
                        # boyamak kullaniciyi yanlis yere gonderir.
                        err("V163", "A submachine reference cannot be "
                                    "expanded: %s" % hata,
                            _hatanin_sahibi(sm, str(hata)))
                    elif genis is not None:
                        # Kodlar DEGISKENLE degil, duz yaziyla verilir:
                        # atif testi kaynakta `err("Vxxx"` kalibini arar
                        # ve degiskenle gecilen kod "olu kayit" gorunur.
                        for kod, mesaj, hedef in _genisletilmis_ad_sorunlari(
                                sm, genis):
                            if kod == "V164":
                                err("V164", mesaj, hedef)
                            else:
                                err("V165", mesaj, hedef)

        # -------------------------------------------- baglanti noktalari
        if s.kind.is_connection_point:
            ad = ("entry point" if s.kind is StateKind.ENTRY_POINT
                  else "exit point")
            sahip = sm.states.get(s.parent) if s.parent else None
            if sahip is None or sahip.kind is not StateKind.COMPOSITE:
                err("V140", "The %s '%s' must belong to a composite state; "
                            "drag it onto the state whose boundary it sits "
                            "on." % (ad, s.name), s.id)
            giden = sm.outgoing(s.id)
            gelen = sm.incoming(s.id)

            if s.kind is StateKind.ENTRY_POINT:
                if not giden:
                    err("V141", "The entry point '%s' has no transition into "
                                "the state; it would lead nowhere." % s.name,
                        s.id)
                if not gelen:
                    warn("V142", "Nothing enters the entry point '%s'."
                         % s.name, s.id)
                if sahip is not None:
                    for t in giden:
                        if not sm.is_descendant(t.target, sahip.id):
                            err("V143", "A transition leaving entry point "
                                        "'%s' must end inside '%s'."
                                % (s.name, sahip.name), t.id)
                    # 14.2.3.7 (basili s.313): "In each Region ... there is
                    # at most a single Transition from the entry point to a
                    # Vertex within that Region."
                    kullanilan = {}
                    for t in giden:
                        b = _bolge_dizini(sm, t.target, sahip.id)
                        if b is None:
                            continue
                        if b in kullanilan:
                            err("V144", "The entry point '%s' has two "
                                        "transitions into the same region of "
                                        "'%s'; at most one is allowed."
                                % (s.name, sahip.name), t.id)
                        kullanilan[b] = t.id
                    for t in gelen:
                        if sahip is not None and sm.is_descendant(t.source,
                                                                 sahip.id):
                            err("V145", "The entry point '%s' is entered from "
                                        "inside '%s'; an entry point is the "
                                        "way IN from outside."
                                % (s.name, sahip.name), t.id)
            else:
                if len(giden) != 1:
                    err("V146", "The exit point '%s' must have exactly one "
                                "outgoing transition (it has %d)."
                        % (s.name, len(giden)), s.id)
                if not gelen:
                    warn("V147", "Nothing inside the state reaches the exit "
                                 "point '%s'." % s.name, s.id)
                if sahip is not None:
                    for t in gelen:
                        if not sm.is_descendant(t.source, sahip.id):
                            err("V148", "A transition entering exit point "
                                        "'%s' must start inside '%s'."
                                % (s.name, sahip.name), t.id)
                    for t in giden:
                        if sm.is_descendant(t.target, sahip.id):
                            err("V149", "The transition leaving exit point "
                                        "'%s' ends inside '%s'; an exit point "
                                        "is the way OUT."
                                % (s.name, sahip.name), t.id)

        if s.kind.is_history:
            if s.parent is None:
                err("V064", "History pseudostate '%s' cannot be in the root region; move "
                            "it inside a composite state." % s.name, s.id)
            outs = sm.outgoing(s.id)
            if len(outs) > 1:
                err("V065", "History pseudostate '%s' can have at most one default "
                            "transition." % s.name, s.id)
            if not sm.incoming(s.id):
                warn("V066", "History pseudostate '%s' has no incoming transitions." % s.name, s.id)
        if s.kind.is_pseudo and (s.entry.strip() or s.exit.strip() or s.do.strip()):
            err("V067", "'%s' is a pseudostate; it cannot carry entry/exit/do behavior."
                % s.name, s.id)
        # YALNIZCA EXIT YASAKTIR.
        #
        # UML 2.5.1, 14.5.2.5 FinalState Constraints (basili s.346) tam olarak
        # UC kisit sayar: no_exit_behavior, no_outgoing_transitions,
        # no_regions. ENTRY ya da doActivity hakkinda HICBIR kisit yoktur.
        # Onceki surum ucunu birden reddediyor ve gecerli bir modelin kod
        # uretimini engelliyordu.
        if s.kind is StateKind.FINAL and s.exit.strip():
            err("V069", "'%s' is a final state; it cannot carry exit behavior. "
                        "Move it into the action of the incoming transition."
                % s.name, s.id)
        if s.kind.is_branch:
            for t in sm.outgoing(s.id):
                if t.target == s.id:
                    err("V070", "Pseudostate '%s' cannot transition to itself; the "
                                "machine would hang on this node." % s.name, t.id)

    # ------------------------------------------------- junction zincirleri -- #
    # Junction STATIK bir dallanmadir: kod uretiminde zincir duzlestirilir ve
    # her yol tek bir gecise doner. Zincir bir duruma varmiyorsa (dongu) ya da
    # cok derinse yol uretilemez; kullaniciya BURADA soylenmezse gecis sessizce
    # kaybolur ve olay hicbir uyari olmadan yok sayilir.
    MAX_JUNCTION_CHAIN = 8
    junction_ids = {j.id for j in sm.states.values()
                    if j.kind is StateKind.JUNCTION}

    def junction_chain_fault(start: str):
        """Zincirdeki ilk sorunu (kod, mesaj) olarak verir; saglamsa None."""
        stack = [(start, 0, frozenset())]
        while stack:
            node, depth, seen = stack.pop()
            if node not in junction_ids:
                continue
            if node in seen:
                return ("V074", "The junction chain at '%s' has a loop; the chain "
                                "never reaches a state." % sm.states[node].name)
            if depth >= MAX_JUNCTION_CHAIN:
                return ("V075", "The junction chain at '%s' is %d steps deep; at "
                                "most %d steps are supported."
                        % (sm.states[node].name, depth + 1, MAX_JUNCTION_CHAIN))
            for branch in sm.outgoing(node):
                stack.append((branch.target, depth + 1, seen | {node}))
        return None

    for tran in sm.transitions.values():
        if tran.target in junction_ids and tran.source not in junction_ids:
            fault = junction_chain_fault(tran.target)
            if fault is not None:
                err(fault[0], fault[1], tran.id)

    # BOLGE BASINA ayni turden en fazla bir tarih sozde-durumu.
    #
    # UML 2.5.1, 14.2.3.7 (basili s.312-313): "A deepHistory Pseudostate can
    # only be defined for composite States and, at most one such Pseudostate
    # can be contained in a Region of a composite State." Sinir REGION
    # basinadir, DURUM basina degil.
    #
    # Anahtar bolgeyi yok sayiyordu: uc bolgeli bir ortogonal duruma her
    # bolge icin birer derin tarih koymak -- UML'in acikca izin verdigi ve
    # aracin kendi paletinin tesvik ettigi sey -- ikinciden itibaren
    # reddediliyor ve model HIC kod uretemiyordu.
    hist_seen: dict = {}
    for s in sm.states.values():
        if s.kind.is_history:
            key = (s.parent, int(getattr(s, "region", 0) or 0), s.kind)
            if key in hist_seen:
                err("V068", "More than one %s in the same region ('%s')."
                    % ("deep history (H*)" if s.kind is StateKind.DEEP_HISTORY
                       else "shallow history (H)", s.name), s.id)
            hist_seen[key] = s.id

    # ------------------------------------------------------------ erisilebilirlik #
    start = sm.initial_of(None)
    if start is not None:
        reachable: Set[str] = set()
        stack = [start.id]
        while stack:
            cur = stack.pop()
            if cur in reachable:
                continue
            reachable.add(cur)
            # UST DURUMLAR DA ERISILMISTIR.
            #
            # Bir alt duruma girmek, onu KAPSAYAN butun durumlara girmek
            # demektir. Yuruyus asagi (cocuklar) ve yana (gecisler)
            # gidiyor ama yukari gitmiyordu: fork'tan dogrudan bir alt
            # duruma giren model, kapsayan bilesik durumu "erisilmez"
            # gosteriyordu -- ve o bilesik erisilmez sayilinca cocuklari
            # da hic taranmadigi icin ONLAR da erisilmez cikiyordu.
            # Ders kitabi bir fork ciziminde ucu birden uyariliyordu.
            for a in sm.ancestors(cur):
                if a.id not in reachable:
                    stack.append(a.id)
            # alt durumlara inis
            for c in sm.children(cur):
                if c.id not in reachable:
                    stack.append(c.id)
            # cikis gecisleri
            for t in sm.outgoing(cur):
                if t.target and t.target not in reachable:
                    stack.append(t.target)
            # ust durumlarin gecisleri de gecerlidir
            for a in sm.ancestors(cur):
                for t in sm.outgoing(a.id):
                    if t.target and t.target not in reachable:
                        stack.append(t.target)
        for s in sm.states.values():
            if s.id not in reachable and s.kind is not StateKind.INITIAL:
                warn("V072", "State '%s' is unreachable by any path." % s.name, s.id)

    # ----------------------------------------------------------- cikmaz sokak #
    for s in sm.states.values():
        if s.kind is StateKind.SIMPLE:
            has_out = bool(sm.outgoing(s.id))
            has_ancestor_out = any(sm.outgoing(a.id) for a in sm.ancestors(s.id))
            if not has_out and not has_ancestor_out:
                warn("V071", "State '%s' has no outgoing transition (deadlock state)." % s.name, s.id)

    # ------------------------------------------------------- belirsiz gecisler #
    for s in sm.states.values():
        # FORK'UN DALLARI SECENEK DEGIL, HEPSI BIRDEN ALINIR.
        #
        # UML 2.5.1, 14.2.3.7: fork gelen bir gecisi "two or more
        # Transitions terminating on Vertices in orthogonal Regions"
        # haline boler ve o gecisler tetikleyici ya da koruma TASIYAMAZ.
        # Yani ayni (olay, koruma, oncelik) ucusu fork icin KURALIN
        # KENDISIDIR, ihlali degil -- ama denetim bunu siradan bir
        # dallanma sanip her gecerli fork icin uyari veriyordu. Cok
        # bolgeye giden bir GIRIS NOKTASI da "acts as a fork" (ayni
        # madde), o da muaftir.
        if s.kind in (StateKind.FORK, StateKind.ENTRY_POINT):
            continue
        buckets = {}
        for t in sm.outgoing(s.id):
            key = (t.event.strip(), t.guard.strip(), t.priority)
            buckets.setdefault(key, []).append(t)
        for (ev, gd, _pri), group in buckets.items():
            if len(group) > 1:
                warn("V080", "State '%s' has %d transitions with the same "
                             "event/guard/priority; the choice is ambiguous."
                     % (s.name, len(group)), group[1].id)

    if sm.max_depth() > 8:
        warn("V090", "Hierarchy depth is %d; stack usage increases in the generated code."
             % sm.max_depth())

    issues.sort(key=lambda i: (SEVERITY_ORDER.get(i.severity, 9), i.code))
    return issues


def has_errors(issues: List[Issue]) -> bool:
    return any(i.is_error for i in issues)
