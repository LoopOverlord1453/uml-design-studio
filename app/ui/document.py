"""Dokuman denetleyicisi: model + geri al/yinele + degisiklik bildirimi.

Geri alma stratejisi *anlik goruntu* (snapshot) tabanlidir: her kullanici
islemi oncesi ve sonrasi model JSON'u saklanir. Bu diyagram olceginde
(yuzlerce dugum) maliyeti onemsizdir ve "yarim uygulanmis komut" sinifindaki
hatalari tamamen ortadan kaldirir.

Ayni sinif hem durum makinesi (StateMachine) hem sinif diyagrami (ClassModel)
dokumanlarini yonetir; model nesnesinin `to_json` / `assign_from` arayuzune
ve sinifin `from_json` statik metoduna dayanir.
"""

from __future__ import annotations

import json
from typing import Callable, List, Optional

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QUndoCommand, QUndoStack

#: Dosya icerigine gore belge turu adlari
KIND_STATE = "state"
KIND_CLASS = "class"


def detect_kind(text: str) -> Optional[str]:
    """Bir model dosyasinin turunu ICERIGINDEN belirler.

    Uzantiya guvenmek yeterli degildir: ".json" her iki turu de tasiyabilir ve
    yanlis kipe yuklenen bir dosya sessizce BOS bir modele donusup ilk
    kaydetmede ozgun icerigi silerdi.

    :return: ``"state"``, ``"class"`` ya da taninmadiysa ``None``
    """
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    declared = data.get("type")
    if declared == "class_diagram":
        return KIND_CLASS
    if declared == "state_machine":
        return KIND_STATE
    # Tur alani olmayan eski dosyalar: ayirt edici anahtarlara bak.
    if isinstance(data.get("classes"), list):
        return KIND_CLASS
    if isinstance(data.get("states"), list):
        return KIND_STATE
    return None


class _SnapshotCommand(QUndoCommand):
    def __init__(self, doc: "Document", before: str, after: str, text: str) -> None:
        super().__init__(text)
        self._doc = doc
        self._before = before
        self._after = after
        self._first = True

    def redo(self) -> None:
        if self._first:
            self._first = False        # mutasyon zaten uygulandi
            self._doc.changed.emit()
            return
        self._doc._restore(self._after)

    def undo(self) -> None:
        self._doc._restore(self._before)


class Document(QObject):
    """Acik olan diyagram (durum makinesi ya da sinif modeli)."""

    changed = pyqtSignal()             # model degisti -> gorunum + kod yenilensin
    path_changed = pyqtSignal()
    selection_request = pyqtSignal(list)   # id listesi

    def __init__(self, machine, parent=None, default_name: str = "untitled.usm") -> None:
        super().__init__(parent)
        self.machine = machine
        self._model_cls = type(machine)
        self._default_name = default_name
        # Bu dokumanin tasidigi belge turu (yukleme dogrulamasi icin)
        self.kind = KIND_CLASS if hasattr(machine, "classes") else KIND_STATE
        self.undo_stack = QUndoStack(self)
        self.path: Optional[str] = None
        self._clean_snapshot = machine.to_json()
        self._in_restore = False

    # ------------------------------------------------------------- degisiklik

    def edit(self, label: str, mutator: Callable) -> bool:
        """Modeli degistirir ve islemi geri alinabilir yapar.

        `mutator` hicbir sey degistirmezse komut yigina eklenmez.
        """
        before = self.machine.to_json()
        mutator(self.machine)
        after = self.machine.to_json()
        if before == after:
            return False
        self.undo_stack.push(_SnapshotCommand(self, before, after, label))
        return True

    def edit_from(self, label: str, before: str) -> bool:
        """Model ZATEN degistirildiginde kullanilir (ornegin surukleme sirasinda).

        `before`, degisiklik oncesi alinmis anlik goruntudur.
        """
        after = self.machine.to_json()
        if before == after:
            return False
        self.undo_stack.push(_SnapshotCommand(self, before, after, label))
        return True

    def _restore(self, snapshot: str) -> None:
        self._in_restore = True
        try:
            restored = self._model_cls.from_json(snapshot)
            # Ayni nesneyi koruyoruz ki disaridaki referanslar bozulmasin.
            self.machine.assign_from(restored)
        finally:
            self._in_restore = False
        self.changed.emit()

    # ------------------------------------------------------------------ dosya

    def load(self, path: str) -> None:
        """Dosyayi okur ve modeli degistirir.

        Icerik bu dokumanin turunde degilse hicbir sey degistirilmez;
        ``ValueError`` yukselir ve cagiran taraf kullaniciya bildirir.
        """
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        kind = detect_kind(text)
        if kind is None:
            raise ValueError(
                "Unrecognised file: not a valid state machine (.usm) or "
                "class diagram (.ucd).")
        if kind != self.kind:
            other = "class diagram" if kind == KIND_CLASS else "state machine"
            mine = "class diagram" if self.kind == KIND_CLASS else "state machine"
            raise ValueError(
                "This file is a %s; it cannot be opened as a %s." % (other, mine))
        restored = self._model_cls.from_json(text)
        self.machine.assign_from(restored)
        self.path = path
        self.undo_stack.clear()
        self.mark_clean()
        self.path_changed.emit()
        self.changed.emit()

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(self.machine.to_json())
        self.path = path
        self.mark_clean()
        self.path_changed.emit()

    def replace(self, machine, path: Optional[str] = None) -> None:
        self.machine.assign_from(machine)
        self.path = path
        self.undo_stack.clear()
        self.mark_clean()
        self.path_changed.emit()
        self.changed.emit()

    # ------------------------------------------------------------------- durum

    def mark_clean(self) -> None:
        self._clean_snapshot = self.machine.to_json()
        self.undo_stack.setClean()

    def is_dirty(self) -> bool:
        return self.machine.to_json() != self._clean_snapshot

    def title(self) -> str:
        import os
        base = os.path.basename(self.path) if self.path else self._default_name
        return "%s%s" % (base, "*" if self.is_dirty() else "")

    def select(self, ids: List[str]) -> None:
        self.selection_request.emit(list(ids))
