"""Durum makinesi parcalarinin kopyala / yapistir cekirdegi -- Qt'siz.

Tuval yalnizca "neyi kopyala" ve "nereye yapistir" bilgisini verir; kimlik
uretimi, ad tekillestirme ve gecis yeniden baglama BURADA yapilir. Boylece
davranis arayuz olmadan da test edilebilir.

KOPYALAMA KURALLARI
-------------------
1. **Alt agac birlikte gelir.** Bir bilesik durum kopyalaninca icindeki her
   sey de kopyalanir; aksi halde yapistirilan kopya bos bir kabuk olurdu.
2. **Yalnizca IC gecisler kopyalanir.** Iki ucu da secimde olan gecis
   kopyalanir; disariya giden bir gecisin hedefi kopyada YOKTUR ve
   kopyalanirsa modelde kirik bir gecis olusurdu.
3. **Kimlikler YENIDEN URETILIR.** Ayni kimlik iki kez bulunursa model
   sozlugunde biri otekini ezer -- sessiz veri kaybi.
4. **Adlar tekillestirilir.** Uretilen kodda durum adlari enum sabitine
   donusur; ayni ad iki kez kullanilirsa kod DERLENMEZ (bkz. V011).
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional, Set, Tuple

from .model import SCHEMA_VERSION, State, Transition, new_id

#: Pano yukunun tur imzasi. Baska bir uygulamadan gelen JSON'un sessizce
#: modele girmesini engeller.
CLIP_TYPE = "uml_state_fragment"


def collect_subtree(machine, ids) -> List[str]:
    """Verilen kimlikleri ve BUTUN alt agaclarini dondurur (tekrarsiz)."""
    out: List[str] = []
    seen: Set[str] = set()

    def walk(sid: str) -> None:
        if sid in seen or sid not in machine.states:
            return
        seen.add(sid)
        out.append(sid)
        for child in machine.children(sid):
            walk(child.id)

    for sid in ids:
        walk(sid)
    return out


def copy_fragment(machine, ids) -> Optional[str]:
    """Secimi JSON parcasina cevirir; kopyalanacak durum yoksa ``None``.

    :param ids: secili durum ve gecis kimlikleri (karisik olabilir)
    """
    state_ids = collect_subtree(
        machine, [i for i in ids if i in machine.states])
    if not state_ids:
        return None

    icinde = set(state_ids)

    states = []
    for sid in state_ids:
        d = machine.states[sid].to_dict()
        # Secimin DISINDA kalan bir ust duruma baglilik tasinmaz: yapistirma
        # hedefi bambaska bir yer olabilir, o zaman parent kimligi modelde
        # bulunmaz ve durum agactan dusen bir yetim olurdu (V020).
        if d.get("parent") not in icinde:
            d["parent"] = None
        states.append(d)

    # Yalnizca iki ucu da secimde olan gecisler (bkz. modul aciklamasi).
    trans = [t.to_dict() for t in machine.transitions.values()
             if t.source in icinde and t.target in icinde]

    return json.dumps({
        "type": CLIP_TYPE,
        "version": SCHEMA_VERSION,
        "states": states,
        "transitions": trans,
    }, ensure_ascii=False)


def _unique_name(base: str, used: Set[str]) -> str:
    """`base`ten cakismayan bir ad turetir: Alpha -> Alpha_copy -> Alpha_copy2."""
    aday = "%s_copy" % base
    if aday not in used:
        return aday
    i = 2
    while "%s_copy%d" % (base, i) in used:
        i += 1
    return "%s_copy%d" % (base, i)


def paste_fragment(machine, payload: str,
                   parent: Optional[str] = None,
                   dx: float = 0.0, dy: float = 0.0) -> List[str]:
    """Parcayi modele ekler ve YENI durum kimliklerini dondurur.

    :param parent: koklerin yerlestirilecegi bilesik durum (None = kok bolge)
    :param dx, dy: konum kaymasi -- kopya orijinalin uzerine binmesin
    :raises ValueError: yuk bu uygulamanin parcasi degilse
    """
    try:
        data = json.loads(payload)
    except ValueError as exc:
        raise ValueError("Clipboard does not hold a diagram fragment.") from exc

    if not isinstance(data, dict) or data.get("type") != CLIP_TYPE:
        raise ValueError("Clipboard does not hold a diagram fragment.")

    used_names = {s.name for s in machine.states.values()}
    eski_yeni: Dict[str, str] = {}
    yeni_kokler: List[str] = []

    for d in data.get("states", []):
        st = State.from_dict(d)
        eski_id = st.id
        st.id = new_id("s")
        eski_yeni[eski_id] = st.id

        st.name = _unique_name(st.name, used_names)
        used_names.add(st.name)

        if st.parent is None:
            # Parcanin kokleri yapistirma hedefine baglanir ve KAYDIRILIR;
            # ic dugumler ust duruma gore konumlandigi icin dokunulmaz.
            st.parent = parent
            st.x = round(st.x + dx, 2)
            st.y = round(st.y + dy, 2)
            yeni_kokler.append(st.id)

        machine.add_state(st)

    # Ust kimlikleri ikinci turda esle: ust durum listede SONRA gelmis olabilir.
    for eski_id, yeni_id in eski_yeni.items():
        st = machine.states[yeni_id]
        if st.parent in eski_yeni:
            st.parent = eski_yeni[st.parent]

    for d in data.get("transitions", []):
        tr = Transition.from_dict(d)
        if tr.source not in eski_yeni or tr.target not in eski_yeni:
            continue          # olmamali; savunmaci
        tr.id = new_id("t")
        tr.source = eski_yeni[tr.source]
        tr.target = eski_yeni[tr.target]
        machine.add_transition(tr)

    return [eski_yeni[k] for k in eski_yeni] if not yeni_kokler else list(
        eski_yeni.values())


def fragment_summary(payload: str) -> Tuple[int, int]:
    """Yuk icindeki (durum, gecis) sayisi; gecersiz yukte (0, 0)."""
    try:
        data = json.loads(payload)
        if data.get("type") != CLIP_TYPE:
            return (0, 0)
        return (len(data.get("states", [])), len(data.get("transitions", [])))
    except Exception:
        return (0, 0)
