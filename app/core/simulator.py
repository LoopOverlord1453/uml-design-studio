"""Referans HSM yorumlayicisi (Python).

Uretilen C/C++ kodu ile *ayni* ara temsili (IR) ve *ayni* algoritmayi kullanir,
ama bagimsiz bir dilde yazilmistir. Bu sayede:

  * arayuzde diyagrami kod uretmeden calistirip izleyebiliriz,
  * `tools/test_semantics.py` iki gerceklestirmeyi karsilastirarak
    ureteclerdeki anlamsal kaymalari yakalayabilir.

Algoritma C ureteciyle satir satir eslesir; birini degistirirken digerini de
degistirin.

ETKIN KONFIGURASYON BIR VEKTORDUR
---------------------------------
Tek bir "etkin durum" yeterli degildir: ortogonal bir durumun bolgeleri ES
ZAMANLI etkindir (UML 2.5.1, 14.2.3.2, basili s.307). Bu yuzden konfigurasyon
BOLGE BASINA bir yaprak tutan `active[]` dizisidir. Tarih kaydi da bolgeye
gore anahtarlanir; ust duruma gore anahtarlansaydi bir ortogonal durumun iki
bolgesi ayni kaydi paylasir ve derin tarihle geri donuste biri otekinin
durumunu geri yuklerdi.

OZYINELEME YOKTUR. Giris ve cikis, uretilen C'de de birebir kurulabilsin diye
acik yiginlarla yuruur: gomulu hedefte olay isleme derinligi MODELE bagli
olamaz, yoksa yigin tuketimi statik olarak siniranamaz.
"""

from __future__ import annotations

from typing import Callable, List, Optional

from ..codegen.ir import (Ir, KIND_COMPOSITE, KIND_FINAL, KIND_HIST_DEEP,
                          KIND_HIST_SHALLOW, KIND_SIMPLE, KIND_TERMINATE, NONE,
                          REGION_NONE, TKIND_EXTERNAL, TKIND_INTERNAL,
                          build_ir)
from .model import StateMachine

MAX_RTC_STEPS = 16
COMPLETION = 0

#: Giris/cikis yuruyuslerinde en fazla adim. Model hiyerarsisi ve bolge
#: sayisiyla sinirlidir; buradaki deger yalnizca bozuk bir IR'de sonsuz
#: donguyu keser.
MAX_WALK_STEPS = 4096

#: Ertelenmis olaylarin tutuldugu havuzun boyu. UML havuzu sinirsiz sayar;
#: gomulu hedefte sinirsiz kuyruk yoktur, bu yuzden sinir ACIKCA konur ve
#: tasma SESSIZ KALMAZ.
MAX_DEFERRED = 16


class Simulator:
    """Bir durum makinesini bellek icinde calistirir."""

    def __init__(self, sm: StateMachine,
                 guard_eval: Optional[Callable[[str], bool]] = None,
                 on_event: Optional[Callable[[str, str], None]] = None,
                 resolve=None) -> None:
        # `resolve`: altmakine referanslarini cozer; modelde altmakine
        # yoksa hic kullanilmaz (bkz. codegen/ir.build_ir).
        self.ir: Ir = build_ir(sm, resolve)
        #: Bolge basina etkin yaprak; NONE = bolge etkin degil.
        self.active: List[int] = [NONE] * self.ir.region_count
        #: Gosterim ve geriye donuk uyumluluk icin TEMSILI yaprak.
        self.state: int = NONE
        self.started = False
        self.terminated = False
        #: Tarih kaydi BOLGEYE gore (ust duruma gore DEGIL).
        self.history: List[int] = [NONE] * self.ir.region_count
        #: Tamamlanma olayi BOLGE BASINA beklenir.
        #:
        #: UML 2.5.1, 14.2.3.8.3 (basili s.314): "If no such Behaviors are
        #: defined, the completion event is generated upon entry into the
        #: State." Olay DURUMA aittir; ortogonal bir makinede her bolgenin
        #: kendi yapragi vardir, dolayisiyla kendi bekleyen olayi da.
        #:
        #: YASANAN HATA: tek bir makine capinda bayrak vardi. Yuksek
        #: numarali bir bolgedeki IC GECIS, dusuk numarali bir bolgenin
        #: girisinden dogan bayragi siliyordu; o bolgenin diyagramda
        #: cizili tamamlanma gecisi HIC alinmiyor ve hicbir uyari
        #: verilmiyordu. Sonuc, kullanicinin bolgeyi hangi sirada
        #: cizdigine bagliydi.
        self.completion_pending = [False] * self.ir.region_count
        #: Tamamlanma dongusu sinira dayandiysa True (model kararsiz).
        self.rtc_overflow = False
        #: ERTELENMIS olay havuzu (olay indeksleri, gelis sirasinda).
        self.deferred_pool: List[int] = []
        #: Havuz tastiysa True.
        self.defer_overflow = False
        self.trace: List[str] = []
        self._guard_eval = guard_eval or (lambda expr: True)
        self._on_event = on_event
        self._event_index = {name: i for i, name in enumerate(self.ir.events)}

    # -------------------------------------------------------------- yardimci #

    def _emit(self, kind: str, detail: str) -> None:
        self.trace.append("%s:%s" % (kind, detail))
        if self._on_event is not None:
            self._on_event(kind, detail)

    def _note(self, kind: str, detail: str) -> None:
        """Yalnizca ARAYUZE anlatim gonderir; `trace` listesine yazmaz.

        `trace`, uretilen C/C++ kodunun izine birebir esit olmak
        zorundadir (tools/test_semantics.py bunu karsilastirir; regresyon
        paketi de "sonlanmis makinede iz bos kalmali" ve "ilk kayit X:S
        olmali" gibi iddialar tutar). Koruma degerlendirmeleri ve secilen
        gecisler uretilen kodun izinde YOKTUR, bu yuzden buraya degil,
        yalnizca panele akarlar.
        """
        if self._on_event is not None:
            self._on_event(kind, detail)

    def transition_label(self, tran) -> str:
        """Bir gecisi okunakli tek satira cevirir: Src --EV [g] / eylem--> Dst."""
        kaynak = self._name(tran.source)
        hedef = self._name(tran.target)
        olay = "" if tran.event == COMPLETION else self.ir.events[tran.event]
        koruma = "" if tran.guard < 0 else self.ir.guards[tran.guard]
        eylem = "" if tran.action < 0 else " ".join(
            self.ir.actions[tran.action].split())

        orta = olay if olay else "completion"
        if koruma:
            orta += " [%s]" % koruma
        if eylem:
            orta += " / %s" % eylem
        if tran.kind == TKIND_INTERNAL:
            return "%s --%s-- (internal, stays in %s)" % (kaynak, orta, kaynak)
        return "%s --%s--> %s" % (kaynak, orta, hedef)

    def _name(self, index: int) -> str:
        return self.ir.states[index].name if index != NONE else "<none>"

    def _parent(self, index: int) -> int:
        return self.ir.states[index].parent if index != NONE else NONE

    def _region_of(self, index: int) -> int:
        return self.ir.states[index].region if index != NONE else REGION_NONE

    def _depth(self, index: int) -> int:
        d = 0
        cur = index
        while cur != NONE and d <= self.ir.max_depth:
            cur = self._parent(cur)
            d += 1
        return d

    def _lca(self, a: int, b: int) -> int:
        da, db = self._depth(a), self._depth(b)
        while da > db:
            a = self._parent(a)
            da -= 1
        while db > da:
            b = self._parent(b)
            db -= 1
        while a != b:
            if a == NONE or b == NONE:
                return NONE
            a = self._parent(a)
            b = self._parent(b)
        return a

    def _is_ancestor(self, maybe: int, node: int) -> bool:
        """`maybe`, `node`un OZ atasi mi (kendisi sayilmaz)?"""
        cur = self._parent(node)
        n = 0
        while cur != NONE and n <= self.ir.max_depth:
            if cur == maybe:
                return True
            cur = self._parent(cur)
            n += 1
        return False

    # --------------------------------------------------------------- davranis #

    def _exec_entry(self, index: int) -> None:
        st = self.ir.states[index]
        self._emit("E", st.name)
        if st.entry:
            self._emit("code", st.entry)

    def _exec_exit(self, index: int) -> None:
        st = self.ir.states[index]
        self._emit("X", st.name)
        if st.exit:
            self._emit("code", st.exit)
        # Sig tarih kaydi (UML 14.2.3.4.5): yalnizca gercek durumlar saklanir;
        # bolge final ile tamamlandiysa tarih SILINIR (varsayilan gecis
        # kullanilir); sozde-durum cikislari kaydi degistirmez.
        if st.region != REGION_NONE and st.parent != NONE:
            if st.kind in (KIND_SIMPLE, KIND_COMPOSITE):
                self.history[st.region] = index
            elif st.kind == KIND_FINAL:
                self.history[st.region] = NONE

    def _exec_action(self, action_id: int) -> None:
        if action_id < 0:
            return
        self._emit("A", self.ir.actions[action_id])

    def _guard(self, guard_id: int) -> bool:
        if guard_id < 0:
            return True
        expr = self.ir.guards[guard_id]
        sonuc = bool(self._guard_eval(expr))
        self._note("G", "%s\t%s" % (expr, "true" if sonuc else "false"))
        return sonuc

    # ------------------------------------------------------- giris / cikis #

    def _enter_one(self, index: int) -> None:
        """Tek bir durumu etkinlestirir (alt bolgelerine DOKUNMAZ)."""
        self._exec_entry(index)
        bolge = self._region_of(index)
        if bolge != REGION_NONE:
            self.active[bolge] = index
            # Duruma GIRILDI: bu bolgenin tamamlanma olayi dogdu.
            self.completion_pending[bolge] = True

    def _exit_one(self, index: int) -> None:
        """Tek bir durumu kapatir (alt bolgeleri ONCEDEN bosaltilmis olmali)."""
        self._exec_exit(index)
        bolge = self._region_of(index)
        if bolge != REGION_NONE and self.active[bolge] == index:
            self.active[bolge] = NONE
            self.completion_pending[bolge] = False

    def _activate_below(self, index: int) -> None:
        """Girilmis bir durumun ALTINI varsayilanlarla etkinlestirir.

        Bolgeler ARTAN sirada islenir. UML bu sirayi tanimlamaz
        (14.2.3.8.3); arac sabitler ve uretilen baslikta yazar.
        """
        # Yigin ogeleri: [durum, islenecek bir sonraki bolge sirasi]
        yigin: List[List[int]] = [[index, 0]]
        adim = 0
        while yigin and adim < MAX_WALK_STEPS:
            adim += 1
            cur, k = yigin[-1]
            st = self.ir.states[cur]
            if k >= st.region_count:
                yigin.pop()
                continue
            yigin[-1][1] = k + 1
            reg = self.ir.regions[st.first_region + k]
            hedef = reg.initial_state
            if hedef == NONE:
                continue
            self._exec_action(reg.initial_action)
            self._enter_one(hedef)
            yigin.append([hedef, 0])

    def _deepest_active_below(self, index: int) -> int:
        """`index` altindaki EN DERIN etkin durum (yoksa NONE).

        Bolgeler AZALAN sirada taranir: cikis, girisin tersi sirada olur.
        """
        bulunan = NONE
        dugum = index
        adim = 0
        while adim < MAX_WALK_STEPS:
            adim += 1
            st = self.ir.states[dugum]
            sonraki = NONE
            for k in range(st.region_count - 1, -1, -1):
                aday = self.active[st.first_region + k]
                if aday != NONE:
                    sonraki = aday
                    break
            if sonraki == NONE:
                return bulunan
            bulunan = sonraki
            dugum = sonraki
        return bulunan

    def _exit_below(self, index: int) -> None:
        """`index` altindaki her seyi kapatir; `index`in kendisi kalir."""
        adim = 0
        while adim < MAX_WALK_STEPS:
            adim += 1
            hedef = self._deepest_active_below(index)
            if hedef == NONE:
                return
            self._exit_one(hedef)

    def _enter_path(self, target: int, top: int) -> None:
        """`top` ile `target` arasindaki durumlara distan ice girer.

        Yol uzerindeki bir durum ORTOGONAL ise, yolun GECMEDIGI bolgeleri
        varsayilanlariyla etkinlestirilir: bir ortogonal duruma girmek
        butun bolgelerini baslatir (14.2.3.2, basili s.307). `target`in
        kendi bolgeleri burada acilmaz; onu `_descend` yapar.
        """
        zincir: List[int] = []
        s = target
        adim = 0
        while s != top and s != NONE and adim <= MAX_WALK_STEPS:
            zincir.append(s)
            s = self._parent(s)
            adim += 1
        zincir.reverse()
        for i, cur in enumerate(zincir):
            self._enter_one(cur)
            st = self.ir.states[cur]
            if st.region_count <= 0:
                continue
            if i + 1 >= len(zincir):
                # SON oge `target`tir; onun bolgelerini `_descend` acar.
                # Burada da acilsaydi hedefin alt durumlarina IKI KEZ
                # girilirdi (izde ayni entry iki kez gorunur).
                continue
            gecilen = self._region_of(zincir[i + 1])
            for r in self.ir.regions_of(cur):
                if r == gecilen:
                    continue
                reg = self.ir.regions[r]
                if reg.initial_state == NONE:
                    continue
                self._exec_action(reg.initial_action)
                self._enter_one(reg.initial_state)
                self._activate_below(reg.initial_state)

    def _leaf_of(self, index: int) -> int:
        """Gosterim icin TEMSILI yaprak: her zaman ILK bolgeden inilir."""
        cur = index
        adim = 0
        while cur != NONE and adim <= MAX_WALK_STEPS:
            adim += 1
            st = self.ir.states[cur]
            if st.region_count <= 0:
                return cur
            sonraki = self.active[st.first_region]
            if sonraki == NONE or sonraki == cur:
                return cur
            cur = sonraki
        return cur

    def _descend(self, s: int) -> int:
        self._activate_below(s)
        return self._leaf_of(s)

    def _resolve_history(self, h: int) -> int:
        """Tarih sozde-durumunu gercek hedefe cevirir ve entry zincirini calistirir."""
        st = self.ir.states[h]
        bolge = st.region
        sahip = st.parent
        stored = self.history[bolge] if bolge != REGION_NONE else NONE
        if stored == NONE:
            stored = st.history_default
        if stored == NONE and bolge != REGION_NONE:
            stored = self.ir.regions[bolge].initial_state
        if stored == NONE:
            return sahip                       # emniyet: bolgenin sahibi
        self._enter_path(stored, sahip)
        if st.kind == KIND_HIST_DEEP:
            self._restore_deep(stored)
            return self._leaf_of(stored)
        return self._descend(stored)

    def _restore_deep(self, index: int) -> None:
        """Derin tarih: `index` ALTINDAKI HER BOLGEYI kayittan geri yukler.

        ATIF: 14.2.3.6 FinalState bolumudur, tarih DEGIL. Dogru yer
        14.2.3.4.5 (basili s.310), "Deep history entry" maddesidir:
        kural sig tarihle aynidir, su farkla -- "the rule is applied
        recursively to all levels in the active state configuration
        below this". Yani kayit HER DERINLIKTE ve her bolgede izlenir.

        YASANAN HATA: yalnizca ILK bolgenin kaydi izleniyor, otekiler
        icin `_activate_below` cagriliyordu; o da kayit yerine
        VARSAYILANI acar. Sonuc: ikinci ve sonraki bolgelerin hatirlanan
        alt agaci kayboluyor ve o bolgenin initial eylemi -- gomulu
        kodda gercek bir yan etki -- bir daha calisiyordu.

        Yuruyus GENISLIK ONCELIKLIDIR: giris disdan ice, bolgeler artan
        sirada olur ve iki kosum ayni sirayi verir.
        """
        kuyruk: List[int] = [index]
        adim = 0
        while kuyruk and adim < MAX_WALK_STEPS:
            adim += 1
            cur = kuyruk.pop(0)
            for r in self.ir.regions_of(cur):
                kayit = self.history[r]
                reg = self.ir.regions[r]
                if kayit == NONE:
                    if reg.initial_state == NONE:
                        continue
                    self._exec_action(reg.initial_action)
                    self._enter_one(reg.initial_state)
                    self._activate_below(reg.initial_state)
                    continue
                self._enter_one(kayit)
                kuyruk.append(kayit)

    def _land(self, target: int) -> int:
        """Gecis hedefine varildiginda gercek yaprak durumu belirler."""
        kind = self.ir.states[target].kind
        if kind in (KIND_HIST_SHALLOW, KIND_HIST_DEEP):
            return self._resolve_history(target)
        if kind == KIND_TERMINATE:
            self.terminated = True             # UML terminate: makine sonlanir
            return target
        return self._descend(target)

    # ------------------------------------------------------------- gecisler #

    def _take(self, tran) -> None:
        self._note("T", self.transition_label(tran))
        if tran.kind == TKIND_INTERNAL:
            # IC GECIS DURUM DEGISTIRMEZ, dolayisiyla YENI BIR TAMAMLANMA
            # OLAYI DOGURMAZ.
            #
            # YASANAN HATA: tamamlanma dongusu her olay gonderiminden
            # sonra kosulsuz calisiyordu. Bir durumun tamamlanma gecisi
            # guard'i yuzunden atlandiktan SONRA, ilgisiz bir ic gecis
            # gelince ESKI tamamlanma olayi yeniden ateslenip makineyi
            # baska bir duruma tasiyordu.
            #
            # YALNIZCA KENDI BOLGESININ bayragini soner. Makine capinda
            # bir bayrak silmek, baska bir bolgenin bekleyen tamamlanma
            # olayini da yok ederdi.
            bolge = self._region_of(tran.source)
            if bolge != REGION_NONE:
                self.completion_pending[bolge] = False
            self._exec_action(tran.action)
            return

        # UML 2.5.1, 14.2.3.7: terminate sozde-durumuna girildiginde makine
        # hicbir durumdan CIKMAZ; exit davranislari calistirilmaz.
        if self.ir.states[tran.target].kind == KIND_TERMINATE:
            self._exec_action(tran.action)
            self.terminated = True
            self.completion_pending = [False] * self.ir.region_count
            self.state = tran.target
            return

        top = self._lca(tran.source, tran.target)
        if tran.kind == TKIND_EXTERNAL and (top == tran.source or top == tran.target):
            top = NONE if top == NONE else self._parent(top)

        # CIKIS, `top`un ALTINDAKI ETKILENEN BOLGEDEN baslar.
        #
        # Kaynagin kendi bolgesinden baslamak yanlistir: YEREL bir gecis
        # (ornegin Work --JUMP--> W2) icin kaynak `top`un ta kendisidir ve
        # dongu hic donmez; oysa Work'un o anki alt durumu (W1) KAPANMALIDIR.
        # Hangi bolgenin etkilendigini HEDEFIN yolu soyler: `top`un hemen
        # altindaki dugumun bolgesi.
        alt = tran.target
        adim = 0
        while (self._parent(alt) != top and self._parent(alt) != NONE
               and adim <= MAX_WALK_STEPS):
            alt = self._parent(alt)
            adim += 1
        bolge = self._region_of(alt)
        s = self.active[bolge] if bolge != REGION_NONE else NONE
        adim = 0
        while s != top and s != NONE and adim <= MAX_WALK_STEPS:
            adim += 1
            self._exit_below(s)
            self._exit_one(s)
            s = self._parent(s)

        self._exec_action(tran.action)
        self._enter_path(tran.target, top)
        if tran.fork_targets:
            self._enter_forked(tran.target, tran.fork_targets)
            self.state = self._leaf_of(tran.target)
        else:
            self.state = self._land(tran.target)
        # Bayraklar `_enter_one` icinde, GIRILEN BOLGE BASINA konur.
        if self.terminated:
            self.completion_pending = [False] * self.ir.region_count

    def _completed(self, index: int) -> bool:
        """Bilesik durumun TAMAMLANMA olayi dogmus mu?

        UML 2.5.1, 14.2.3.8.3 (basili s.315): "if the State is a composite
        State, all its orthogonal Regions have reached a FinalState".
        Ortogonal durumda kosul VE'dir: tek bir bolgenin final'e varmasi
        yetmez.
        """
        st = self.ir.states[index]
        if st.region_count <= 0:
            return True
        for r in self.ir.regions_of(index):
            etkin = self.active[r]
            if etkin == NONE or self.ir.states[etkin].kind != KIND_FINAL:
                return False
        return True

    def _join_ready(self, tran) -> bool:
        """Join'in butun kaynaklari su anda etkin mi?"""
        for kaynak in tran.join_sources:
            bolge = self._region_of(kaynak)
            if bolge == REGION_NONE or self.active[bolge] != kaynak:
                return False
        return True

    def _enter_forked(self, sahip: int, hedefler: List[int]) -> None:
        """FORK: adi gecen bolgelere ACIKCA girer, otekiler varsayilanla.

        UML 2.5.1, 14.2.3.7 (basili s.313): fork "an incoming Transition
        into two or more Transitions terminating on Vertices in orthogonal
        Regions of a composite State" boler. Adi gecmeyen bolgeler yine de
        baslar; ortogonal bir duruma girmek BUTUN bolgelerini baslatir.
        """
        kapsanan = set()
        for hedef in hedefler:
            alt = hedef
            adim = 0
            while (self._parent(alt) != sahip and self._parent(alt) != NONE
                   and adim <= MAX_WALK_STEPS):
                alt = self._parent(alt)
                adim += 1
            kapsanan.add(self._region_of(alt))
            self._enter_path(hedef, sahip)
            self._activate_below(hedef)
        for r in self.ir.regions_of(sahip):
            if r in kapsanan:
                continue
            reg = self.ir.regions[r]
            if reg.initial_state == NONE:
                continue
            self._exec_action(reg.initial_action)
            self._enter_one(reg.initial_state)
            self._activate_below(reg.initial_state)

    def _select(self, region: int, event_index: int):
        """Bir bolgenin yapragindan yukari yuruyup ILK etkin gecisi bulur."""
        s = self.active[region]
        adim = 0
        while s != NONE and adim <= MAX_WALK_STEPS:
            adim += 1
            first, count = self.ir.tran_slice.get(s, (0, 0))
            for i in range(count):
                tran = self.ir.transitions[first + i]
                if tran.event != event_index:
                    continue
                if (event_index == COMPLETION
                        and self.ir.states[s].kind == KIND_COMPOSITE
                        and not self._completed(s)
                        and not tran.join_sources):
                    continue
                # JOIN: butun gelen segmentler AYNI ANDA etkin olmali.
                # UML 2.5.1, 14.2.3.7 (basili s.313): "all incoming
                # Transitions have to complete before execution can
                # continue through an outgoing Transition."
                if tran.join_sources and not self._join_ready(tran):
                    continue
                if not self._guard(tran.guard):
                    continue
                return s, tran
            s = self._parent(s)
        return NONE, None

    def _try_event(self, event_index: int) -> bool:
        """Olayi ETKIN HER BOLGEYE sunar ve cakisan gecisleri ayiklar.

        UML 2.5.1, 14.2.3.9.4: daha derin bir durumdan cikan gecis, onu
        kapsayan bir durumdan cikanla CAKISIR ve oncelik DERIN olandadir.
        Ortogonal bolgelerde iki bolge ayni dis gecisi secebilir ya da biri
        dis, oteki derin bir gecis secebilir; ikisini birden islemek durumu
        iki kez kapatirdi.

        Cakismayanlar ARTAN bolge sirasinda islenir. UML bu sirayi
        tanimlamaz; arac sabitler ve uretilen baslikta yazar.
        """
        secimler = []                      # (bolge, kaynak, gecis)
        for r in range(self.ir.region_count):
            if self.active[r] == NONE:
                continue
            kaynak, tran = self._select(r, event_index)
            if tran is None:
                continue
            secimler.append((r, kaynak, tran))

        if not secimler:
            return False

        # Ayni gecisi iki bolge sectiyse BIR KEZ islenir.
        benzersiz = []
        gorulen = set()
        for r, kaynak, tran in secimler:
            if tran.index in gorulen:
                continue
            gorulen.add(tran.index)
            benzersiz.append((r, kaynak, tran))

        # Bir secimin kaynagi, baska bir secimin kaynaginin OZ ATASI ise
        # disaridaki dusurulur: oncelik derin olandadir.
        kalan = []
        for r, kaynak, tran in benzersiz:
            if any(self._is_ancestor(kaynak, digeri)
                   for _r2, digeri, _t2 in benzersiz if digeri != kaynak):
                continue
            kalan.append((r, kaynak, tran))

        islendi = False
        for r, _kaynak, tran in kalan:
            # TERMINATE HER SEYI DURDURUR.
            #
            # UML 2.5.1, 14.2.3.7: terminate sozde-durumuna girilince
            # makine yurutmeyi BIRAKIR; hicbir durumdan cikilmaz. Bir
            # bolge makineyi oldurdukten sonra otekilerin secilmis
            # gecislerini islemek, olmus bir makinede exit/effect/entry
            # calistirmak ve bittikten SONRA girilen bir durumu
            # bildirmek demekti.
            if self.terminated:
                break
            # Daha once islenen bir gecis bu bolgeyi kapatmis olabilir.
            if self.active[r] == NONE and tran.kind != TKIND_INTERNAL:
                continue
            self._take(tran)
            islendi = True
        return islendi

    def _run_to_completion(self) -> None:
        """Bekleyen tamamlanma olaylarini kararli konfigurasyona dek isler.

        Dongu KOSULSUZ degil, `completion_pending` bayragina baglidir:
        tamamlanma olayi bir duruma GIRILDIGINDE dogar (UML 2.5.1,
        14.2.3.8.3) ve islendiginde -- gecis alinsa da alinmasa da --
        tukenir. Kosulsuz donmek, tuketilmis bir olayi sonraki her olay
        gonderiminde yeniden atesliyordu.
        """
        # SINIR BOLGE BASINADIR.
        #
        # Dongu her adimda TEK BIR BOLGENIN bekleyen tamamlanma olayini
        # isler. Makine capinda sabit bir sinir, bolge sayisi arttikca
        # bolgeler arasinda paylasiliyor ve 8 bolgeden sonra tukeniyordu:
        # diyagramda cizili tamamlanma gecisleri HIC alinmiyordu. Uretilen
        # C/C++ bunu bildirmiyor bile. Bolge sayisiyla olcekleyince her
        # bolge kendi butcesine sahip olur, dongusel modele karsi koruma
        # da yerinde kalir.
        sinir = MAX_RTC_STEPS * max(1, self.ir.region_count)
        steps = 0
        while not self.terminated:
            bolge = REGION_NONE
            for r in range(self.ir.region_count):
                if self.completion_pending[r]:
                    bolge = r
                    break
            if bolge == REGION_NONE:
                return
            if steps >= sinir:
                # SESSIZCE KESMEK YERINE BILDIR. Onceki surum donguden
                # cikip devam ediyordu; model kararsiz kaliyor ama
                # kullaniciya hicbir sey soylenmiyordu.
                self.rtc_overflow = True
                self._emit("error",
                           "run-to-completion limit (%d steps) reached: the "
                           "model has a cycle of completion transitions"
                           % sinir)
                return
            self.completion_pending[bolge] = False
            if self.active[bolge] != NONE:
                _kaynak, tran = self._select(bolge, COMPLETION)
                if tran is not None:
                    self._take(tran)
            steps += 1

    # ------------------------------------------------------------------- API #

    def start(self) -> None:
        if self.started:
            return
        self.started = True
        self.terminated = False
        self.state = NONE
        self.active = [NONE] * self.ir.region_count
        self.history = [NONE] * self.ir.region_count
        self.completion_pending = [False] * self.ir.region_count
        self.rtc_overflow = False
        self.deferred_pool = []
        self.defer_overflow = False
        for r in self.ir.root_regions:
            reg = self.ir.regions[r]
            self._exec_action(reg.initial_action)
            self._enter_path(reg.initial_state, NONE)
            yaprak = self._land(reg.initial_state)
            if self.state == NONE:
                self.state = yaprak
        if self.terminated:
            self.completion_pending = [False] * self.ir.region_count
        self._run_to_completion()
        self._drain_deferred()

    def _is_deferred(self, event_index: int) -> bool:
        """Etkin konfigurasyonda BIR durum bile bu olayi erteliyor mu?

        UML 2.5.1, 14.2.3.4.4 (basili s.309): "An Event may be deferred by
        a composite State or submachine States, in which case it remains
        deferred as long as the composite State remains in the active
        configuration." Yani yalnizca yaprak degil, ZINCIRIN TAMAMI
        bakilir.
        """
        for index in self.active_indices():
            if event_index in self.ir.states[index].deferred:
                return True
        return False

    def _defer(self, event_index: int) -> None:
        if len(self.deferred_pool) >= MAX_DEFERRED:
            # SESSIZCE ATMA. Kaybolan bir olay, modelin neden beklendigi
            # gibi davranmadigini aciklanamaz kilardi.
            self.defer_overflow = True
            self._emit("error",
                       "the deferred-event pool is full (%d); the "
                       "occurrence of '%s' was dropped"
                       % (MAX_DEFERRED, self.ir.events[event_index]))
            return
        self.deferred_pool.append(event_index)
        self._emit("defer", self.ir.events[event_index])

    def _drain_deferred(self) -> None:
        """Artik ertelenmeyen olaylari havuzdan alip isler."""
        adim = 0
        while adim < MAX_DEFERRED * 2:
            if self.terminated:
                return                         # sonlanmis makine olay islemez
            adim += 1
            siradaki = None
            for index in self.deferred_pool:
                if not self._is_deferred(index):
                    siradaki = index
                    break
            if siradaki is None:
                return
            self.deferred_pool.remove(siradaki)
            self._emit("recall", self.ir.events[siradaki])
            if self._try_event(siradaki):
                self._run_to_completion()

    def dispatch(self, event: str) -> bool:
        if not self.started:
            self.start()
        if self.terminated:
            return False
        index = self._event_index.get(event)
        if index is None or index == COMPLETION:
            return False
        # ONCE TETIKLEME DENENIR. Belge bunu acikca soyler: ertelenen bir
        # olay turu, KAYNAGI erteleyen durum olan bir gecisin
        # tetikleyicisiyse gecis KAZANIR ("a kind of override option").
        # Once erteleme bakilsaydi o gecis hic ateslenmezdi.
        handled = self._try_event(index)
        if handled:
            self._run_to_completion()
            self._drain_deferred()
            return True
        if self._is_deferred(index):
            self._defer(index)
            return True                        # olay TUKETILDI: havuzda duruyor
        return False

    def do_activity(self) -> None:
        if self.terminated:
            return              # sonlanmis makine davranis yurutmez
        for index in self.active_indices():
            st = self.ir.states[index]
            if st.do:
                self._emit("D", st.name)

    # ----------------------------------------------------------------- durum #

    def active_indices(self) -> List[int]:
        """Etkin BUTUN durumlar; icten disa, bolge sirasinda.

        Ortogonal olmayan bir modelde bu, yapraktan koke giden eski
        zincirin ta kendisidir.
        """
        out: List[int] = []
        for r in range(self.ir.region_count - 1, -1, -1):
            index = self.active[r]
            if index != NONE and index not in out:
                out.append(index)
        return out

    @property
    def state_name(self) -> str:
        return self._name(self.state)

    def active_chain(self) -> List[str]:
        """Etkin durumlarin model id'leri (tuvalde vurgulamak icin)."""
        return [self.ir.states[i].model_id for i in self.active_indices()]

    def is_terminated(self) -> bool:
        if self.terminated:
            return True
        for r in self.ir.root_regions:
            index = self.active[r]
            if index == NONE:
                return False
            st = self.ir.states[index]
            if st.kind != KIND_FINAL:
                return False
        return bool(self.ir.root_regions)
