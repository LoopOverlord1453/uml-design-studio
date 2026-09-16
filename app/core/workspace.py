"""Calisma alani (workspace) -- Qt'den BAGIMSIZ.

Calisma alani, kullanicinin acilista sectigi tek bir klasordur ve uc seyin
ortak koku olur:

    <kok>/                       calisma alani koku (ayni zamanda git deposu)
      umlstudio.workspace        calisma alani tanimi (JSON)
      model/                     .usm / .ucd model dosyalari
      generated/                 uretilen C / C++ / test / PlantUML dosyalari

Boylece "modeli kurdugun yer" ile "kodu urettigin yer" ayni depo icinde
kalir; Git paneli de dogrudan bu kok uzerinde calisir.

Kok disina yazma girisimleri ``WorkspaceError`` ile reddedilir.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List

MARKER = "umlstudio.workspace"
SCHEMA = 1

DEFAULT_MODEL_DIR = "model"
DEFAULT_GENERATED_DIR = "generated"

#: Calisma alaninda olusturulan varsayilan .gitignore icerigi
GITIGNORE = """\
# UML Design Studio calisma alani
__pycache__/
*.pyc

# derleyici ciktilari
*.o
*.obj
*.elf
*.bin
*.hex
*.map
*.exe
*.out
build/
"""


class WorkspaceError(Exception):
    """Calisma alani islemi basarisiz."""


@dataclass
class Workspace:
    """Acik calisma alani.

    ``root`` disindaki hicbir yola yazilmaz; ``resolve`` bunu zorlar.
    """

    root: str
    name: str = ""
    model_dir: str = DEFAULT_MODEL_DIR
    generated_dir: str = DEFAULT_GENERATED_DIR
    auto_write: bool = True
    last_state_model: str = ""      # koke gore bagil
    last_class_model: str = ""
    extra: Dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------ kurucu

    def __post_init__(self) -> None:
        self.root = os.path.abspath(self.root)
        if not self.name:
            self.name = os.path.basename(self.root.rstrip(os.sep)) or self.root

    # ------------------------------------------------------------------ yollar

    @property
    def marker_path(self) -> str:
        return os.path.join(self.root, MARKER)

    @property
    def model_path(self) -> str:
        return os.path.join(self.root, self.model_dir)

    @property
    def generated_path(self) -> str:
        return os.path.join(self.root, self.generated_dir)

    def resolve(self, *parts: str) -> str:
        """Kok altindaki bir yolu verir; kok disina cikilirsa hata yukselir."""
        target = os.path.abspath(os.path.join(self.root, *parts))
        root = os.path.abspath(self.root)
        if os.path.normcase(target) != os.path.normcase(root) and \
                not os.path.normcase(target).startswith(
                    os.path.normcase(root) + os.sep):
            raise WorkspaceError(
                "Cannot write outside the workspace: %s" % target)
        return target

    def relative(self, path: str) -> str:
        """Mutlak yolu koke gore bagil, ileri bolu isaretli hale getirir."""
        try:
            rel = os.path.relpath(os.path.abspath(path), self.root)
        except ValueError:
            return os.path.abspath(path)
        return rel.replace(os.sep, "/")

    # ------------------------------------------------------------------ olustur

    def ensure_layout(self) -> None:
        """Klasor duzenini ve .gitignore'u olusturur (varsa dokunmaz)."""
        try:
            os.makedirs(self.root, exist_ok=True)
            os.makedirs(self.model_path, exist_ok=True)
            os.makedirs(self.generated_path, exist_ok=True)
        except OSError as exc:
            raise WorkspaceError("Could not create the folder: %s" % exc) from exc
        ignore = os.path.join(self.root, ".gitignore")
        if not os.path.exists(ignore):
            try:
                with open(ignore, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(GITIGNORE)
            except OSError:
                pass    # .gitignore yazilamamasi olumcul degil

    # ------------------------------------------------------------------ kalicilik

    def to_json(self) -> str:
        data = {
            "schema": SCHEMA,
            "name": self.name,
            "model_dir": self.model_dir,
            "generated_dir": self.generated_dir,
            "auto_write": self.auto_write,
            "last_state_model": self.last_state_model,
            "last_class_model": self.last_class_model,
            "extra": self.extra,
        }
        return json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    def save(self) -> None:
        self.ensure_layout()
        try:
            with open(self.marker_path, "w", encoding="utf-8",
                      newline="\n") as fh:
                fh.write(self.to_json())
        except OSError as exc:
            raise WorkspaceError("Could not save the workspace: %s" % exc) from exc

    @staticmethod
    def is_workspace(root: str) -> bool:
        return os.path.isfile(os.path.join(root, MARKER))

    @classmethod
    def load(cls, root: str) -> "Workspace":
        """Var olan calisma alanini okur; isaretci yoksa varsayilanlarla acar."""
        root = os.path.abspath(root)
        if not os.path.isdir(root):
            raise WorkspaceError("Folder does not exist: %s" % root)
        marker = os.path.join(root, MARKER)
        if not os.path.isfile(marker):
            return cls(root=root)
        try:
            with open(marker, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            raise WorkspaceError(
                "Could not read the workspace file: %s" % exc) from exc
        if not isinstance(data, dict):
            raise WorkspaceError("The workspace file is corrupt.")
        ws = cls(
            root=root,
            name=str(data.get("name") or ""),
            model_dir=str(data.get("model_dir") or DEFAULT_MODEL_DIR),
            generated_dir=str(data.get("generated_dir")
                              or DEFAULT_GENERATED_DIR),
            auto_write=bool(data.get("auto_write", True)),
            last_state_model=str(data.get("last_state_model") or ""),
            last_class_model=str(data.get("last_class_model") or ""),
        )
        extra = data.get("extra")
        if isinstance(extra, dict):
            ws.extra = {str(k): str(v) for k, v in extra.items()}
        return ws

    @classmethod
    def create(cls, root: str, name: str = "") -> "Workspace":
        """Yeni calisma alani olusturur ve diske yazar."""
        ws = cls(root=root, name=name)
        ws.save()
        return ws

    # ------------------------------------------------------------------ yazma

    def write_generated(self, files: Dict[str, str],
                        subdir: str = "") -> List[str]:
        """Uretilen dosyalari ``generated/`` altina yazar.

        Yalnizca icerigi DEGISEN dosyalar yazilir; boylece git durumu
        gereksiz "degisti" kayitlariyla kirlenmez.

        :return: yazilan dosyalarin koke gore bagil yollari
        """
        base = self.resolve(self.generated_dir, subdir) if subdir \
            else self.resolve(self.generated_dir)
        try:
            os.makedirs(base, exist_ok=True)
        except OSError as exc:
            raise WorkspaceError("Could not create the folder: %s" % exc) from exc

        written: List[str] = []
        for name, text in sorted(files.items()):
            if os.path.isabs(name) or ".." in name.replace("\\", "/").split("/"):
                raise WorkspaceError("Invalid file name: %s" % name)
            target = self.resolve(self.generated_dir, subdir, name) if subdir \
                else self.resolve(self.generated_dir, name)
            if _same_content(target, text):
                continue
            try:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with open(target, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
            except (OSError, ValueError) as exc:
                raise WorkspaceError("Could not write %s (%s)" % (name, exc)) from exc
            written.append(self.relative(target))
        return written

    def model_file(self, base_name: str) -> str:
        """``model/`` altindaki bir model dosyasinin tam yolu."""
        return self.resolve(self.model_dir, base_name)


def _same_content(path: str, text: str) -> bool:
    """Diskteki dosya verilen metinle ayni mi (satir sonu farki yok sayilir).

    Karsilastirma BAYT duzeyindedir: diskteki dosya baska bir kodlamayla
    (or. Windows-1254) kaydedilmis olabilir ve cozulemeyebilir. Boyle bir
    dosyayi "ayni degil" saymak dogru davranistir -- uzerine yazilir. Metin
    olarak okumaya calismak burada UnicodeDecodeError yukseltir, o da
    write_generated'in WorkspaceError sozlesmesini delerdi.
    """
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return False
    return raw.replace(b"\r\n", b"\n") == text.encode("utf-8").replace(b"\r\n", b"\n")


# ============================================================ son kullanilanlar

MAX_RECENT = 10   # kullanici istegi: 10 calisma alanina kadar hatirla


def normalise_recent(paths: List[str], limit: int = MAX_RECENT) -> List[str]:
    """Son kullanilan listesini temizler: var olanlar, tekrarsiz, sirali."""
    seen = set()
    out: List[str] = []
    for raw in paths:
        if not raw:
            continue
        path = os.path.abspath(raw)
        key = os.path.normcase(path)
        if key in seen:
            continue
        seen.add(key)
        if os.path.isdir(path):
            out.append(path)
        if len(out) >= limit:
            break
    return out


def push_recent(paths: List[str], path: str,
                limit: int = MAX_RECENT) -> List[str]:
    """``path``i listenin basina tasir."""
    return normalise_recent([path] + list(paths), limit)


def suggest_root(parent_dir: str, name: str) -> str:
    """Ust klasor + ad'dan cakismayan bir kok yolu onerir."""
    safe = "".join(ch if (ch.isalnum() or ch in "-_ .") else "_"
                   for ch in name).strip() or "workspace"
    candidate = os.path.join(parent_dir, safe)
    if not os.path.exists(candidate):
        return candidate
    for i in range(2, 100):
        alt = "%s-%d" % (candidate, i)
        if not os.path.exists(alt):
            return alt
    return candidate
