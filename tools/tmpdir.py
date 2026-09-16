"""Helper that REALLY removes temporary folders.

WHY THIS IS A SEPARATE MODULE
-----------------------------
The tests used `shutil.rmtree(tmp, ignore_errors=True)`. On Windows git
writes the files of its object store (``.git/objects/...``) READ-ONLY;
`os.unlink` raises ``PermissionError`` on those and `ignore_errors`
swallows it silently. The result: one leftover per run. Seventy
``umlui_*`` folders had piled up on the development machine -- and one
of them still looked like a workspace, so it kept showing up as valid
in the application's "recent workspaces" list.

The `remove_tree` here clears the read-only flag and tries again.
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile


def _force_writable(func, path, _exc):
    """rmtree error handler: clears the read-only flag and retries."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass                    # if it truly cannot be removed, let it go


def remove_tree(path: str) -> None:
    """Removes a folder; read-only files cannot prevent it."""
    if not path or not os.path.exists(path):
        return
    # onexc is 3.12+, onerror is older. Support both.
    try:
        shutil.rmtree(path, onexc=_force_writable)
    except TypeError:
        shutil.rmtree(path, onerror=_force_writable)


def make_tree(prefix: str = "umlui_") -> str:
    """Opens a temporary folder (the caller must remove it)."""
    return tempfile.mkdtemp(prefix=prefix)


def sweep_leftovers(prefix: str = "umlui_") -> int:
    """Collects temp folders left by earlier runs; returns the count."""
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
