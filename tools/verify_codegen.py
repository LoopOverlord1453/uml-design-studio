"""Uretilen kodu GERCEKTEN derleyip kosturarak dogrular.

    python tools/verify_codegen.py

Adimlar:
  1. Ornek modeli dogrular (hata varsa durur),
  2. C ve C++ kodunu gecici bir dizine uretir,
  3. examples/ altindaki destek dosyalariyla birlikte
     -Wall -Wextra -pedantic -Werror ile derler,
  4. cikan programi calistirir; beklenen davranis izini karsilastirir.

gcc/g++ bulunamazsa ilgili adim ATLANDI olarak isaretlenir (hata degil).
"""

from __future__ import annotations



import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tmpdir import remove_tree   # noqa: E402

from app.codegen.c_generator import generate_c            # noqa: E402
from app.codegen.class_c_generator import generate_class_c        # noqa: E402
from app.codegen.class_cpp_generator import (generate_class_cpp,  # noqa: E402
                                             pascal)
from app.codegen.cpp_generator import generate_cpp        # noqa: E402
from app.core.class_validator import validate_classes     # noqa: E402
from app.core.samples import demo_class_model, demo_machine       # noqa: E402
from app.core.validator import has_errors, validate       # noqa: E402

EXAMPLES = os.path.join(ROOT, "examples")

C_FLAGS = ["-std=gnu11", "-Wall", "-Wextra", "-Werror",
           "-Wshadow", "-Wconversion", "-Wsign-conversion", "-O2"]
CXX_FLAGS = ["-std=c++11", "-Wall", "-Wextra", "-pedantic", "-Werror",
             "-Wshadow", "-Wold-style-cast", "-O2",
             "-fno-exceptions", "-fno-rtti"]


class Reporter:
    def __init__(self) -> None:
        self.failed = 0
        self.skipped = 0

    def ok(self, msg: str) -> None:
        print("  [ TAMAM ] %s" % msg)

    def fail(self, msg: str, detail: str = "") -> None:
        self.failed += 1
        print("  [ HATA  ] %s" % msg)
        if detail:
            for line in detail.rstrip().splitlines():
                print("           | %s" % line)

    def skip(self, msg: str) -> None:
        self.skipped += 1
        print("  [ATLANDI] %s" % msg)


def have(tool: str) -> bool:
    return shutil.which(tool) is not None


def run(cmd, cwd):
    proc = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True,
                          encoding="utf-8", errors="replace")
    return proc.returncode, proc.stdout


def build_and_run(rep: Reporter, work: str, label: str, compiler: str,
                  flags, sources, exe_name: str) -> None:
    if not have(compiler):
        rep.skip("%s: %s bulunamadi" % (label, compiler))
        return

    exe = os.path.join(work, exe_name)
    cmd = [compiler] + list(flags) + ["-I", work] + list(sources) + ["-o", exe]
    code, out = run(cmd, work)
    if code != 0:
        rep.fail("%s derleme basarisiz" % label, out)
        return
    if out.strip():
        rep.fail("%s derlemede uyari uretti (-Werror'a takilmayan)" % label, out)
        return
    rep.ok("%s derlendi (uyari yok)" % label)

    code, out = run([exe], work)
    print("           > %s" % out.strip().replace("\n", "\n           > "))
    if code != 0:
        rep.fail("%s calisma zamani dogrulamasi basarisiz" % label)
        return
    rep.ok("%s davranis izi beklenenle ayni" % label)


def compile_only(rep: Reporter, work: str, label: str, compiler: str,
                 flags, source: str) -> None:
    """Bir kaynagi YALNIZCA derler (baglamaz).

    MCU ornekleri (`<prefix>_main.c` / `<Class>_main.cpp`) tahtaya ozgu
    board_* kancalarini `extern` bildirir, dolayisiyla baglanamaz. Ama
    kullaniciya AYNEN teslim edildikleri icin en azindan uyarisiz
    derlenmeleri gerekir; bu adim olmadan sablon hic denenmemis olurdu.
    """
    if not have(compiler):
        rep.skip("%s: %s bulunamadi" % (label, compiler))
        return
    cmd = [compiler] + list(flags) + ["-I", work, "-c", source,
                                      "-o", os.path.join(work, source + ".o")]
    code, out = run(cmd, work)
    if code != 0:
        rep.fail("%s derlenemedi" % label, out)
        return
    if out.strip():
        rep.fail("%s derlemede uyari uretti" % label, out)
        return
    rep.ok("%s uyarisiz derlendi" % label)


def main() -> int:
    rep = Reporter()
    sm = demo_machine()

    print("== 1. Model dogrulama ==")
    issues = validate(sm)
    for i in issues:
        print("  %s" % i)
    if has_errors(issues):
        rep.fail("Ornek modelde dogrulama hatasi var")
        return 1
    rep.ok("Model dogrulandi (%d uyari/bilgi)" % len(issues))

    work = tempfile.mkdtemp(prefix="usd_verify_")
    print("\n== 2. Kod uretimi -> %s ==" % work)
    files = {}
    files.update(generate_c(sm))
    files.update(generate_cpp(sm))
    for name, text in files.items():
        with open(os.path.join(work, name), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print("  uretildi: %-14s %6d bayt" % (name, len(text)))

    for extra in ("blinky_ctx.h", "blinky_demo.c", "blinky_demo.cpp"):
        shutil.copy(os.path.join(EXAMPLES, extra), os.path.join(work, extra))

    print("\n== 3. C derleme + kosum ==")
    build_and_run(rep, work, "C", "gcc", C_FLAGS,
                  ["blinky.c", "blinky_demo.c"], "blinky_c.exe")

    print("\n== 4. C++ derleme + kosum ==")
    build_and_run(rep, work, "C++", "g++", CXX_FLAGS,
                  ["Blinky.cpp", "blinky_demo.cpp"], "blinky_cpp.exe")

    print("\n== 5. MCU ornekleri (super loop sablonlari) ==")
    compile_only(rep, work, "C MCU ornegi", "gcc", C_FLAGS, "blinky_main.c")
    compile_only(rep, work, "C++ MCU ornegi", "g++", CXX_FLAGS,
                 "Blinky_main.cpp")

    print("\n== 6. Sinif diyagrami kodu ==")
    class_work = tempfile.mkdtemp(prefix="usd_verify_cls_")
    cm = demo_class_model()
    issues = validate_classes(cm)
    if has_errors(issues):
        rep.fail("Ornek sinif modelinde dogrulama hatasi var",
                 "\n".join(str(i) for i in issues if i.is_error))
    else:
        class_files = {}
        class_files.update(generate_class_c(cm))
        class_files.update(generate_class_cpp(cm))
        for name, text in class_files.items():
            with open(os.path.join(class_work, name), "w", encoding="utf-8",
                      newline="\n") as fh:
                fh.write(text)
        cls = pascal(cm.name)
        compile_only(rep, class_work, "sinif C kitapligi", "gcc", C_FLAGS,
                     "%s.c" % cm.prefix)
        compile_only(rep, class_work, "sinif C MCU ornegi", "gcc", C_FLAGS,
                     "%s_main.c" % cm.prefix)
        compile_only(rep, class_work, "sinif C++ kitapligi", "g++", CXX_FLAGS,
                     "%s.cpp" % cls)
        compile_only(rep, class_work, "sinif C++ MCU ornegi", "g++", CXX_FLAGS,
                     "%s_main.cpp" % cls)

    print("\n== Ozet ==")
    if rep.failed:
        print("  %d adim basarisiz. Calisma dizini korundu: %s" % (rep.failed, work))
        return 1
    print("  Tum adimlar gecti%s." %
          (" (%d adim atlandi)" % rep.skipped if rep.skipped else ""))
    remove_tree(work)
    return 0


if __name__ == "__main__":
    sys.exit(main())
