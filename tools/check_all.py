"""Runs every verification step in order.

    python tools/check_all.py

  1. tools/verify_codegen.py   - compiles and runs the sample model C/C++
  2. tools/test_semantics.py   - matches the Python reference against C/C++
  3. tools/test_regressions.py - checks that fixed bugs have not come back
  4. tools/test_workspace.py   - the workspace core (without Qt)
  5. tools/test_git.py         - the git backend, on a real temporary repo
  6. tools/smoke_test.py       - drives the interface end to end, headless
  7. tools/test_git_ui.py      - workspace + Git panel end to end via the UI
  8. tools/test_functional.py  - exercises EVERY feature of the tool
  9. tools/test_standards.py   - interface standards (UML notation, menus,
                                 shortcuts, accessibility)
 10. tools/test_uml_conformance.py
                               - runtime semantics, compared against the
                                 NORMATIVE sentences of UML 2.5.1

If any of them fails the exit code is non-zero (suitable for CI).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STEPS = [
    ("Kod uretimi: derle + kostur", "verify_codegen.py"),
    ("Anlamsal ayrimsal test", "test_semantics.py"),
    ("Regresyonlar (duzeltilmis hatalar)", "test_regressions.py"),
    ("Calisma alani cekirdegi", "test_workspace.py"),
    ("Git arka ucu", "test_git.py"),
    ("Arayuz duman testi", "smoke_test.py"),
    ("Calisma alani + Git paneli (arayuz)", "test_git_ui.py"),
    ("Fonksiyonellik denetimi (tum ozellikler)", "test_functional.py"),
    ("Arayuz standartlari", "test_standards.py"),
    ("UML 2.5.1 anlambilim uyumu", "test_uml_conformance.py"),
]


def main() -> int:
    # Keep subprocesses out of the user's REAL settings: the interface
    # tests build a MainWindow and write the workspace into 'recent'
    # and 'last_workspace'. Without isolation a run pushed the test's
    # temporary folder into the user's start-up list.
    os.environ.setdefault("UMLSTUDIO_SETTINGS_SCOPE", "check")

    failed = []
    for title, script in STEPS:
        print("\n" + "=" * 74)
        print("  %s   (%s)" % (title, script))
        print("=" * 74)
        started = time.time()
        proc = subprocess.run([sys.executable, "-u",
                               os.path.join(ROOT, "tools", script)],
                              cwd=ROOT)
        elapsed = time.time() - started
        if proc.returncode != 0:
            failed.append(title)
            print("  -> BASARISIZ (%.1f sn)" % elapsed)
        else:
            print("  -> gecti (%.1f sn)" % elapsed)

    print("\n" + "=" * 74)
    if failed:
        print("  %d/%d adim basarisiz:" % (len(failed), len(STEPS)))
        for f in failed:
            print("    - %s" % f)
        return 1
    print("  TUM ADIMLAR GECTI (%d/%d)" % (len(STEPS), len(STEPS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
