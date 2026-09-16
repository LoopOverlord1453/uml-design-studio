"""Gecici klasorleri GERCEKTEN silen yardimci.

NEDEN AYRI BIR MODUL
--------------------
Testler `shutil.rmtree(tmp, ignore_errors=True)` kullaniyordu. Windows'ta
git, nesne deposundaki dosyalari (``.git/objects/...``) SALT OKUNUR
yazar; `os.unlink` bunlarda ``PermissionError`` verir ve `ignore_errors`
hatayi sessizce yutar. Sonuc: her kosumda bir kalinti. Kullanicinin
makinesinde 70 adet ``umlui_*`` klasoru birikmisti -- ve bunlardan biri
(``.../calisma``) hala bir calisma alani gibi durdugu icin uygulamanin
"son kullanilanlar" listesinde gecerli gorunuyordu.

Buradaki `remove_tree` salt okunur bayragini temizleyip yeniden dener.
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile


def _force_writable(func, path, _exc):
    """rmtree hata isleyicisi: salt okunur bayragini kaldirip yeniden dener."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass                    # gercekten silinemiyorsa sessizce birak


def remove_tree(path: str) -> None:
    """Klasoru siler; salt okunur dosyalar buna engel olamaz."""
    if not path or not os.path.exists(path):
        return
    # onexc 3.12+, onerror eski surumler. Ikisini de destekle.
    try:
        shutil.rmtree(path, onexc=_force_writable)
    except TypeError:
        shutil.rmtree(path, onerror=_force_writable)


def make_tree(prefix: str = "umlui_") -> str:
    """Gecici klasor acar (silme sorumlulugu cagirana aittir)."""
    return tempfile.mkdtemp(prefix=prefix)


def sweep_leftovers(prefix: str = "umlui_") -> int:
    """Onceki kosumlardan kalan gecici klasorleri toplar; sayisini doner."""
    kok = tempfile.gettempdir()
    silinen = 0
    try:
        girdiler = os.listdir(kok)
    except OSError:
        return 0
    for ad in girdiler:
        if not ad.startswith(prefix):
            continue
        tam = os.path.join(kok, ad)
        if not os.path.isdir(tam):
            continue
        remove_tree(tam)
        if not os.path.exists(tam):
            silinen += 1
    return silinen
