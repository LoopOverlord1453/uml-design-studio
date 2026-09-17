"""The workspace -- INDEPENDENT of Qt.

A workspace is the single folder the user picks at start-up, and it becomes
the common root of three things:

    <root>/                      the workspace root (also the git repository)
      umlstudio.workspace        the workspace definition (JSON)
      model/                     .usm / .ucd model files
      generated/                 generated C / C++ / test / PlantUML files

That keeps "where you built the model" and "where you generated the code"
in one repository; the Git panel works directly on this root.

Attempts to write outside the root are refused with ``WorkspaceError``.
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

#: Default .gitignore content created inside a workspace
GITIGNORE = """\
# UML Design Studio workspace
__pycache__/
*.pyc

# compiler output
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
    """A workspace operation failed."""


@dataclass
class Workspace:
    """An open workspace.

    Nothing is written outside ``root``; ``resolve`` enforces it.
    """

    root: str
    name: str = ""
    model_dir: str = DEFAULT_MODEL_DIR
    generated_dir: str = DEFAULT_GENERATED_DIR
    auto_write: bool = True
    last_state_model: str = ""      # relative to the root
    last_class_model: str = ""
    extra: Dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------- constructor

    def __post_init__(self) -> None:
        self.root = os.path.abspath(self.root)
        if not self.name:
            self.name = os.path.basename(self.root.rstrip(os.sep)) or self.root

    # ------------------------------------------------------------------- paths

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
        """Returns a path under the root; raises if it escapes the root."""
        target = os.path.abspath(os.path.join(self.root, *parts))
        root = os.path.abspath(self.root)
        if os.path.normcase(target) != os.path.normcase(root) and \
                not os.path.normcase(target).startswith(
                    os.path.normcase(root) + os.sep):
            raise WorkspaceError(
                "Cannot write outside the workspace: %s" % target)
        return target

    def relative(self, path: str) -> str:
        """Makes an absolute path root-relative, with forward slashes."""
        try:
            rel = os.path.relpath(os.path.abspath(path), self.root)
        except ValueError:
            return os.path.abspath(path)
        return rel.replace(os.sep, "/")

    # ------------------------------------------------------------------- create

    def ensure_layout(self) -> None:
        """Creates the folder layout and .gitignore (existing ones untouched)."""
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
                pass    # failing to write .gitignore is not fatal

    # ---------------------------------------------------------------- persistence

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
        """Reads an existing workspace; with no marker it opens with defaults."""
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
        """Creates a new workspace and writes it to disk."""
        ws = cls(root=root, name=name)
        ws.save()
        return ws

    # ------------------------------------------------------------------ write

    def write_generated(self, files: Dict[str, str],
                        subdir: str = "") -> List[str]:
        """Writes the generated files under ``generated/``.

        Only files whose content CHANGED are written, so the git status is
        never polluted with pointless "modified" entries.

        :return: the written files, as paths relative to the root
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
        """The full path of a model file under ``model/``."""
        return self.resolve(self.model_dir, base_name)


def _same_content(path: str, text: str) -> bool:
    """Is the file on disk identical to this text (line endings ignored).

    The comparison happens at BYTE level: the file on disk may have been
    saved in another encoding (e.g. Windows-1254) and may not decode. Treating
    such a file as "not identical" is the correct behaviour -- it gets
    overwritten. Trying to read it as text would raise UnicodeDecodeError
    here, and that would break write_generated's WorkspaceError contract.
    """
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return False
    return raw.replace(b"\r\n", b"\n") == text.encode("utf-8").replace(b"\r\n", b"\n")


# ============================================================ recent workspaces

MAX_RECENT = 10   # requested by the user: remember up to 10 workspaces


def normalise_recent(paths: List[str], limit: int = MAX_RECENT) -> List[str]:
    """Cleans the recent list: existing entries only, de-duplicated, in order."""
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
    """Moves ``path`` to the front of the list."""
    return normalise_recent([path] + list(paths), limit)


def suggest_root(parent_dir: str, name: str) -> str:
    """Suggests a non-colliding root path from a parent folder and a name."""
    safe = "".join(ch if (ch.isalnum() or ch in "-_ .") else "_"
                   for ch in name).strip() or "workspace"
    candidate = os.path.join(parent_dir, safe)
    if not os.path.exists(candidate):
        return candidate
    for i in range(2, 100):
        lower = "%s-%d" % (candidate, i)
        if not os.path.exists(lower):
            return lower
    return candidate


def display_path(path: str) -> str:
    """A path with the home folder written as ``~``.

    THE ACCOUNT NAME IS IN EVERY PATH under the home folder, and paths are on
    screen constantly -- the start-up dialog, the workspace tooltip, the file
    tree. That puts the account name into every screenshot, every screen share
    and every bug report, whether or not anyone meant to share it. Showing
    ``~/Documents`` says exactly as much to the person using the application
    and nothing at all to anyone else looking at the picture.

    Only the DISPLAY is shortened. Every path the application acts on is the
    real one; :func:`expand_path` is the way back.
    """
    if not path:
        return path
    try:
        full = os.path.abspath(path)
        home = os.path.abspath(os.path.expanduser("~"))
    except (OSError, ValueError):
        return path
    if os.path.normcase(full) == os.path.normcase(home):
        return "~"
    if os.path.normcase(full).startswith(os.path.normcase(home + os.sep)):
        return "~" + os.sep + full[len(home) + 1:]
    return path


def expand_path(text: str) -> str:
    """The inverse of :func:`display_path`.

    It also accepts a ``~`` the user typed themselves, which is what someone
    coming from a shell will try first.
    """
    return os.path.expanduser((text or "").strip())
