"""Verifies the class diagram generators by compiling them (dev tool).

    python tools/dev_check_classes.py

Writes the generated files into a temporary directory, lists their names
and sizes, and compiles each one with -Werror. The directory is printed,
so a developer who wants to read the output can go there.

NOTHING IS LINKED: the generated `<prefix>_main.c` / `<Class>_main.cpp`
MCU templates declare board-specific board_* hooks as `extern` and cannot
link without a board support package. The meaningful check is whether
they COMPILE without warnings.

The same checks also run as steps of tools/verify_codegen.py; this tool
exists only for inspecting the generated files by hand.
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.codegen.class_c_generator import generate_class_c
from app.codegen.class_cpp_generator import generate_class_cpp, pascal
from app.codegen.class_plantuml_generator import generate_class_plantuml
from app.core.class_validator import validate_classes
from app.core.samples import demo_class_model
from app.core.validator import has_errors

C_FLAGS = ["-std=gnu11", "-Wall", "-Wextra", "-Werror",
           "-Wshadow", "-Wconversion", "-Wsign-conversion", "-O2"]
CXX_FLAGS = ["-std=c++11", "-Wall", "-Wextra", "-pedantic", "-Werror",
             "-Wshadow", "-Wold-style-cast", "-O2",
             "-fno-exceptions", "-fno-rtti"]


def run(cmd, cwd):
    p = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, p.stdout


def compile_only(work, label, compiler, flags, source):
    """Compiles only. Returns True when it succeeds WITHOUT warnings."""
    code, out = run([compiler] + flags + ["-I", work, "-c", source,
                                          "-o", source + ".o"], work)
    if code != 0:
        print("  [HATA   ] %s derlenemedi\n%s" % (label, out))
        return False
    if out.strip():
        print("  [HATA   ] %s uyari uretti\n%s" % (label, out))
        return False
    print("  [ TAMAM ] %s uyarisiz derlendi" % label)
    return True


def main():
    cm = demo_class_model()
    issues = validate_classes(cm)
    for i in issues:
        print(" ", i)
    if has_errors(issues):
        print("HATA: ornek sinif modeli dogrulanamadi")
        return 1

    work = tempfile.mkdtemp(prefix="usd_cls_")
    print("Calisma dizini:", work)
    files = {}
    files.update(generate_class_c(cm))
    files.update(generate_class_cpp(cm))
    files.update(generate_class_plantuml(cm))
    for name, text in sorted(files.items()):
        with open(os.path.join(work, name), "w", encoding="utf-8",
                  newline="\n") as fh:
            fh.write(text)
        print("  uretildi: %-24s %6d bayt" % (name, len(text)))

    cls = pascal(cm.name)
    print()
    ok = True
    ok &= compile_only(work, "C kitapligi", "gcc", C_FLAGS,
                       "%s.c" % cm.prefix)
    ok &= compile_only(work, "C MCU ornegi", "gcc", C_FLAGS,
                       "%s_main.c" % cm.prefix)
    ok &= compile_only(work, "C++ kitapligi", "g++", CXX_FLAGS,
                       "%s.cpp" % cls)
    ok &= compile_only(work, "C++ MCU ornegi", "g++", CXX_FLAGS,
                       "%s_main.cpp" % cls)

    print("\n%s" % ("Tum derlemeler gecti." if ok
                    else "EN AZ BIR DERLEME BASARISIZ."))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
