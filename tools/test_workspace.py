"""Calisma alani (workspace) cekirdegini sinar -- Qt gerekmez.

    python tools/test_workspace.py

Sinananlar: klasor duzeni, kalicilik, kok disina yazma korumasi, uretilen
dosyalarin yalnizca DEGISTIGINDE yazilmasi, son kullanilanlar listesi.
"""

from __future__ import annotations



import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tmpdir import remove_tree   # noqa: E402

from app.core.workspace import (GITIGNORE, MARKER, Workspace,  # noqa: E402
                                WorkspaceError, normalise_recent, push_recent,
                                suggest_root)

_passed = 0
_failed: list = []


def check(label: str, cond: bool, detail: str = "") -> None:
    global _passed
    if cond:
        _passed += 1
        print("  [ TAMAM ] %s" % label)
    else:
        _failed.append(label)
        print("  [ HATA  ] %s%s" % (label, ("  -- " + detail) if detail else ""))


def section(title: str) -> None:
    print("\n== %s ==" % title)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="umlws_")
    try:
        run_all(tmp)
    finally:
        remove_tree(tmp)

    print("\n== Ozet ==")
    if _failed:
        print("  %d kontrol basarisiz:" % len(_failed))
        for f in _failed:
            print("    - %s" % f)
        return 1
    print("  Tum calisma alani kontrolleri gecti (%d)." % _passed)
    return 0


def run_all(tmp: str) -> None:
    # ------------------------------------------------------------------ olustur
    section("1. Olusturma ve klasor duzeni")
    root = os.path.join(tmp, "proje")
    ws = Workspace.create(root, name="Proje")
    check("kok olusturuldu", os.path.isdir(root))
    check("isaretci dosyasi yazildi", os.path.isfile(os.path.join(root, MARKER)))
    check("model/ olusturuldu", os.path.isdir(ws.model_path))
    check("generated/ olusturuldu", os.path.isdir(ws.generated_path))
    check(".gitignore olusturuldu",
          os.path.isfile(os.path.join(root, ".gitignore")))
    with open(os.path.join(root, ".gitignore"), encoding="utf-8") as fh:
        check(".gitignore icerigi dogru", fh.read() == GITIGNORE)
    check("is_workspace dogru", Workspace.is_workspace(root))
    check("is_workspace olmayan klasorde yanlis",
          not Workspace.is_workspace(tmp))

    # ------------------------------------------------------------------ kalicilik
    section("2. Kalicilik")
    ws.auto_write = False
    ws.last_state_model = "model/blinky.usm"
    ws.extra["dil"] = "c"
    ws.save()
    again = Workspace.load(root)
    check("ad korundu", again.name == "Proje", again.name)
    check("auto_write korundu", again.auto_write is False)
    check("son model korundu", again.last_state_model == "model/blinky.usm")
    check("ek alanlar korundu", again.extra.get("dil") == "c")

    plain = os.path.join(tmp, "isaretcisiz")
    os.makedirs(plain, exist_ok=True)
    loaded = Workspace.load(plain)
    check("isaretcisiz klasor varsayilanlarla acildi",
          loaded.model_dir == "model" and loaded.auto_write is True)
    try:
        Workspace.load(os.path.join(tmp, "yok-boyle-klasor"))
        check("olmayan klasor reddedildi", False)
    except WorkspaceError:
        check("olmayan klasor reddedildi", True)

    bozuk = os.path.join(tmp, "bozuk")
    os.makedirs(bozuk, exist_ok=True)
    with open(os.path.join(bozuk, MARKER), "w", encoding="utf-8") as fh:
        fh.write("{ bu json degil")
    try:
        Workspace.load(bozuk)
        check("bozuk isaretci reddedildi", False)
    except WorkspaceError:
        check("bozuk isaretci reddedildi", True)

    # ------------------------------------------------------------------ koruma
    section("3. Kok disina yazma korumasi")
    for bad in ("../kacak.c", "..\\kacak.c", "alt/../../kacak.c"):
        try:
            ws.write_generated({bad: "x"})
            check("reddedildi: %s" % bad, False)
        except WorkspaceError:
            check("reddedildi: %s" % bad, True)
    try:
        ws.resolve("..")
        check("resolve kok disini reddetti", False)
    except WorkspaceError:
        check("resolve kok disini reddetti", True)
    check("resolve kokun kendisini kabul etti",
          os.path.normcase(ws.resolve()) == os.path.normcase(root))
    check("resolve alt yolu kabul etti",
          ws.resolve("generated", "a.c").startswith(root))

    if sys.platform.startswith("win"):
        try:
            ws.write_generated({"C:/gecici/kacak.c": "x"})
            check("mutlak yol reddedildi", False)
        except WorkspaceError:
            check("mutlak yol reddedildi", True)

    # ------------------------------------------------------------------ yazma
    section("4. Uretilen dosyalarin yazilmasi")
    files = {"blinky.h": "#ifndef H\n#define H\n#endif\n",
             "blinky.c": "#include \"blinky.h\"\n"}
    written = ws.write_generated(files)
    check("iki dosya yazildi", len(written) == 2, str(written))
    check("bagil yol ileri bolu ile", all("/" in w for w in written),
          str(written))
    check("icerik diske gitti",
          os.path.isfile(os.path.join(ws.generated_path, "blinky.c")))

    again2 = ws.write_generated(files)
    check("DEGISMEYEN dosya yeniden yazilmadi", again2 == [], str(again2))

    files["blinky.c"] = "#include \"blinky.h\"\n/* degisti */\n"
    third = ws.write_generated(files)
    check("yalnizca degisen dosya yazildi",
          third == ["generated/blinky.c"], str(third))

    sub = ws.write_generated({"t.c": "int main(void){return 0;}\n"},
                             subdir="test")
    check("alt klasore yazildi", sub == ["generated/test/t.c"], str(sub))
    check("alt klasor olustu",
          os.path.isfile(os.path.join(ws.generated_path, "test", "t.c")))

    # ------------------------------------------------------------------ yollar
    section("5. Yol yardimcilari")
    mp = ws.model_file("blinky.usm")
    check("model_file kok altinda", mp.startswith(root))
    check("relative bagil verdi",
          ws.relative(os.path.join(ws.generated_path, "blinky.c"))
          == "generated/blinky.c")

    # ------------------------------------------------------------------ son kul.
    section("6. Son kullanilanlar")
    a = os.path.join(tmp, "a")
    b = os.path.join(tmp, "b")
    os.makedirs(a, exist_ok=True)
    os.makedirs(b, exist_ok=True)
    yok = os.path.join(tmp, "yok")

    lst = normalise_recent([a, b, yok, a, ""])
    check("olmayan klasor ayiklandi", yok not in lst, str(lst))
    check("tekrar ayiklandi", lst.count(a) == 1, str(lst))
    check("bos ayiklandi", "" not in lst)

    lst2 = push_recent([a, b], b)
    check("push_recent one aldi",
          os.path.normcase(lst2[0]) == os.path.normcase(b), str(lst2))
    check("push_recent tekrar uretmedi", len(lst2) == 2, str(lst2))

    many = normalise_recent([a, b] * 20, limit=3)
    check("limit uygulandi", len(many) <= 3, str(many))

    check("suggest_root cakismayani sectii",
          os.path.basename(suggest_root(tmp, "proje")) != "proje",
          suggest_root(tmp, "proje"))
    check("suggest_root gecersiz karakterleri temizledi",
          "/" not in os.path.basename(suggest_root(tmp, "a/b:c")),
          suggest_root(tmp, "a/b:c"))
    check("suggest_root bos addan da yol uretti",
          bool(os.path.basename(suggest_root(tmp, "   "))))


if __name__ == "__main__":
    sys.exit(main())
