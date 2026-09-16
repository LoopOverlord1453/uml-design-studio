"""Tum dogrulama adimlarini sirayla kosturur.

    python tools/check_all.py

  1. tools/verify_codegen.py   - ornek modelin C/C++ kodu derlenip kosturulur
  2. tools/test_semantics.py   - Python referansi ile C/C++ birebir karsilastirilir
  3. tools/test_regressions.py - duzeltilmis hatalarin geri gelmedigi dogrulanir
  4. tools/test_workspace.py   - calisma alani cekirdegi (Qt'siz)
  5. tools/test_git.py         - git arka ucu, gercek gecici depo uzerinde
  6. tools/smoke_test.py       - arayuz ekransiz olarak uctan uca kullanilir
  7. tools/test_git_ui.py      - calisma alani + Git paneli arayuzden uctan uca
  8. tools/test_functional.py  - aracin HER ozelligi uctan uca denetlenir
  9. tools/test_standards.py   - arayuz standartlari (UML gosterimi, menu,
                                 kisayol, erisilebilirlik) denetlenir
 10. tools/test_uml_conformance.py
                               - calisma zamani anlambilimi, UML 2.5.1'in
                                 NORMATIF cumleleriyle karsilastirilir

Herhangi biri basarisiz olursa cikis kodu sifirdan farklidir (CI icin uygun).
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
    # Alt surecler kullanicinin GERCEK ayarlarina yazmasin: arayuz
    # testleri MainWindow kurar ve calisma alanini 'son kullanilanlar'
    # ile 'last_workspace'e yazar. Yalitimsiz kosum, testin gecici
    # klasorunu kullanicinin acilis listesine sokuyordu.
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
