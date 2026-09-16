"""Git arka ucunu GERCEK bir gecici depo uzerinde sinar -- Qt gerekmez.

    python tools/test_git.py

git kurulu degilse butun adimlar ATLANDI sayilir ve cikis kodu 0 olur.

Sinananlar: init, durum ayristirma (izlenmeyen / hazirlanmis / degismis /
silinmis / yeniden adlandirilmis), hazirla-geri al, commit, gecmis + serit
yerlesimi (birlesme dahil), fark uretimi, commit dosya listesi, dallar,
degisiklikleri geri alma, fark ayristirici.
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

from app.core.git_backend import (Commit, GitError, Repo,  # noqa: E402
                                  assign_lanes, diff_stats, git_available,
                                  git_version, lane_count, parse_diff)

AUTHOR = ("UML Test", "test@example.invalid")

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


def write(root: str, rel: str, text: str) -> None:
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def commit_all(repo: Repo, message: str) -> str:
    repo.stage_all()
    return repo.commit(message, author_name=AUTHOR[0], author_email=AUTHOR[1])


def main() -> int:
    if not git_available():
        print("git bulunamadi -- butun git kontrolleri ATLANDI.")
        return 0
    print("git: %s" % git_version())

    tmp = tempfile.mkdtemp(prefix="umlgit_")
    try:
        run_all(tmp)
    finally:
        # Windows'ta .git icindeki salt-okunur nesneler silinmeyi engelleyebilir
        remove_tree(tmp)

    print("\n== Ozet ==")
    if _failed:
        print("  %d kontrol basarisiz:" % len(_failed))
        for f in _failed:
            print("    - %s" % f)
        return 1
    print("  Tum git kontrolleri gecti (%d)." % _passed)
    return 0


def run_all(tmp: str) -> None:
    root = os.path.join(tmp, "depo")
    os.makedirs(root, exist_ok=True)
    repo = Repo(root)

    # ------------------------------------------------------------------ init
    section("1. Depo baslatma")
    check("bos klasor depo degil", not repo.is_repo())
    repo.init()
    check("init sonrasi depo", repo.is_repo())
    repo.init()     # yeniden cagirmak zararsiz olmali
    check("init yeniden cagrilabilir", repo.is_repo())
    check("commit yok", not repo.has_commits())
    top = repo.toplevel()
    check("toplevel kokla ayni",
          top is not None and os.path.normcase(top)
          == os.path.normcase(os.path.realpath(root)),
          "%s vs %s" % (top, os.path.realpath(root)))

    st = repo.status()
    check("bos depo temiz", st.is_clean(), str(st.files))
    check("ilk durum bayragi", st.initial)

    # ------------------------------------------------------------------ durum
    section("2. Durum ayristirma")
    write(root, "generated/blinky.c", "int a = 1;\n")
    write(root, "generated/blinky.h", "#define H 1\n")
    st = repo.status()
    paths = sorted(f.path for f in st.files)
    check("iki izlenmeyen dosya gorundu",
          paths == ["generated/blinky.c", "generated/blinky.h"], str(paths))
    check("izlenmeyen isaretlendi", all(f.untracked for f in st.files))
    check("izlenmeyen 'unstaged' sayilir", all(f.unstaged for f in st.files))
    check("izlenmeyen 'staged' sayilmaz", not any(f.staged for f in st.files))
    check("etiket ?", st.files[0].label() == "?")

    repo.stage(["generated/blinky.c"])
    st = repo.status()
    staged = [f.path for f in st.staged()]
    unstaged = [f.path for f in st.unstaged()]
    check("hazirlanan dogru", staged == ["generated/blinky.c"], str(staged))
    check("hazirlanmayan dogru", unstaged == ["generated/blinky.h"],
          str(unstaged))

    repo.unstage(["generated/blinky.c"])
    st = repo.status()
    check("hazirliktan cikarildi", not st.staged(),
          str([f.path for f in st.staged()]))

    # ------------------------------------------------------------------ commit
    section("3. Commit")
    try:
        repo.commit("   ")
        check("bos mesaj reddedildi", False)
    except GitError:
        check("bos mesaj reddedildi", True)

    sha1 = commit_all(repo, "ilk: uretilen kod")
    check("ilk commit olustu", bool(sha1), sha1)
    check("has_commits artik dogru", repo.has_commits())
    st = repo.status()
    check("commit sonrasi temiz", st.is_clean(), str(st.files))
    check("dal adi var", bool(st.branch), st.branch)
    check("initial bayragi dustu", not st.initial)

    # degisiklik + silme + yeniden adlandirma
    write(root, "generated/blinky.c", "int a = 2;\n")
    st = repo.status()
    mod = [f for f in st.files if f.path == "generated/blinky.c"]
    check("degisiklik gorundu", len(mod) == 1 and mod[0].unstaged, str(st.files))
    check("degisiklik etiketi M", mod and mod[0].label() == "M",
          mod[0].label() if mod else "")

    os.remove(os.path.join(root, "generated/blinky.h"))
    st = repo.status()
    dele = [f for f in st.files if f.path == "generated/blinky.h"]
    check("silme gorundu", len(dele) == 1, str(st.files))
    check("silme etiketi D", dele and dele[0].label() == "D",
          dele[0].label() if dele else "")

    # geri al: hem degisiklik hem silme
    repo.discard(["generated/blinky.c", "generated/blinky.h"])
    st = repo.status()
    check("geri alma calisti", st.is_clean(), str(st.files))
    with open(os.path.join(root, "generated/blinky.c"), encoding="utf-8") as fh:
        check("icerik commit'e dondu", fh.read() == "int a = 1;\n")

    # izlenmeyen dosyayi geri alma = silme
    write(root, "gecici.txt", "sil beni\n")
    repo.discard(["gecici.txt"])
    check("izlenmeyen dosya silindi",
          not os.path.exists(os.path.join(root, "gecici.txt")))

    # yeniden adlandirma
    os.rename(os.path.join(root, "generated/blinky.c"),
              os.path.join(root, "generated/blinky_new.c"))
    repo.stage_all()
    st = repo.status()
    ren = [f for f in st.files if f.index_status == "R"]
    if ren:
        check("yeniden adlandirma algilandi",
              ren[0].path == "generated/blinky_new.c", str(ren[0]))
        check("ozgun yol tasindi", ren[0].orig_path == "generated/blinky.c",
              str(ren[0].orig_path))
    else:
        # git benzerlik esigini tutmazsa sil+ekle olarak gorur; ikisi de gecerli
        names = sorted(f.path for f in st.files)
        check("yeniden adlandirma sil+ekle olarak gorundu",
              names == ["generated/blinky.c", "generated/blinky_new.c"],
              str(names))
        check("ozgun yol tasindi (atlandi)", True)
    commit_all(repo, "ikinci: yeniden adlandirma")

    # ------------------------------------------------------------------ farklar
    section("4. Farklar")
    write(root, "generated/blinky_new.c", "int a = 1;\nint b = 3;\n")
    d_unstaged = repo.diff("generated/blinky_new.c")
    check("hazirlanmamis fark uretildi", "+int b = 3;" in d_unstaged,
          d_unstaged[:120])
    add, dele_n = diff_stats(d_unstaged)
    check("fark istatistigi", add == 1 and dele_n == 0,
          "add=%d del=%d" % (add, dele_n))

    repo.stage(["generated/blinky_new.c"])
    d_staged = repo.diff("generated/blinky_new.c", staged=True)
    check("hazirlanmis fark uretildi", "+int b = 3;" in d_staged, d_staged[:120])
    check("hazirlanmamis fark artik bos",
          repo.diff("generated/blinky_new.c").strip() == "")

    write(root, "yeni_dosya.c", "satir1\nsatir2\n")
    d_new = repo.diff("yeni_dosya.c", untracked=True)
    check("izlenmeyen dosya farki sentezlendi",
          "+satir1" in d_new and "+satir2" in d_new, d_new[:160])
    check("izlenmeyen farkta yeni dosya basligi", "new file" in d_new)

    # ikili dosya
    with open(os.path.join(root, "ikili.bin"), "wb") as fh:
        fh.write(b"\x00\x01\x02\x03" * 10)
    d_bin = repo.diff("ikili.bin", untracked=True)
    check("ikili dosya fark yerine not verdi", "binary file" in d_bin, d_bin)

    sha_diff = commit_all(repo, "ucuncu: fark testi")
    d_commit = repo.diff_commit(sha_diff)
    check("commit farki uretildi", "blinky_new.c" in d_commit, d_commit[:160])
    cf = repo.commit_files(sha_diff)
    names = sorted(f.path for f in cf)
    check("commit dosya listesi", "yeni_dosya.c" in names, str(names))

    content = repo.file_at(sha_diff, "generated/blinky_new.c")
    check("file_at icerigi verdi", "int b = 3;" in content, content[:80])

    # ilk (kok) commit'in farki da uretilebilmeli
    first = repo.log(limit=100)[-1]
    d_root = repo.diff_commit(first.sha)
    check("kok commit farki uretildi", bool(d_root.strip()), d_root[:80])
    check("kok commit dosya listesi", bool(repo.commit_files(first.sha)))

    # ------------------------------------------------------------------ dallar
    section("5. Dallar")
    base_branch = repo.status().branch
    repo.create_branch("ozellik/test", checkout=True)
    check("dal olusturuldu ve gecildi",
          repo.status().branch == "ozellik/test", repo.status().branch)
    check("dal listesinde var", "ozellik/test" in repo.branches(),
          str(repo.branches()))
    write(root, "generated/dal.c", "int dal = 1;\n")
    commit_all(repo, "dalda calisma")
    repo.checkout(base_branch)
    check("ana dala donuldu", repo.status().branch == base_branch,
          repo.status().branch)
    check("dal dosyasi calisma agacinda yok",
          not os.path.exists(os.path.join(root, "generated/dal.c")))
    try:
        repo.create_branch("   ")
        check("bos dal adi reddedildi", False)
    except GitError:
        check("bos dal adi reddedildi", True)

    # ------------------------------------------------------------------ birlesme
    section("6. Gecmis ve serit yerlesimi")
    write(root, "generated/ana.c", "int ana = 1;\n")
    commit_all(repo, "ana dalda calisma")
    code = subprocess.run(["git", "merge", "--no-ff", "-m", "birlesme",
                           "ozellik/test"], cwd=root,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    check("birlesme yapildi", code.returncode == 0,
          code.stderr.decode("utf-8", "replace")[:200])

    commits = repo.log(limit=200)
    check("gecmis okundu", len(commits) >= 5, "n=%d" % len(commits))
    check("her commit'te sha ve konu var",
          all(c.sha and c.subject for c in commits))
    merges = [c for c in commits if c.is_merge]
    check("birlesme commit'i bulundu", len(merges) == 1, "n=%d" % len(merges))
    check("birlesmenin iki ebeveyni var",
          merges and len(merges[0].parents) == 2)
    check("HEAD isaretlendi", sum(1 for c in commits if c.is_head) == 1)
    check("dal etiketi tasindi",
          any("ozellik/test" in " ".join(c.refs) for c in commits),
          str([c.refs for c in commits[:4]]))
    check("seritler negatif degil", all(c.lane >= 0 for c in commits))
    check("birlesmede en az iki serit var", lane_count(commits) >= 2,
          "lanes=%d" % lane_count(commits))
    check("seritler bitisik numaralandi",
          set(c.lane for c in commits) <= set(range(lane_count(commits))))

    # BIRLESME commit'i BOS GORUNMEMELI. git, birlesmeler icin varsayilan
    # olarak yama uretmez; --cc ise temiz birlesmede bos cikar. Panel ilk
    # ebeveyne gore fark gosterir -- bu kontrol o davranisi sabitler.
    merge = merges[0] if merges else None
    if merge is not None:
        m_files = repo.commit_files(merge.sha)
        m_diff = repo.diff_commit(merge.sha)
        check("birlesme commit'inin dosya listesi BOS DEGIL",
              bool(m_files), str([f.path for f in m_files]))
        check("birlesme commit'inin farki BOS DEGIL",
              bool(m_diff.strip()), m_diff[:120])
        check("birlesme farki dalin getirdigi dosyayi iceriyor",
              "dal.c" in m_diff, m_diff[:200])
        m_add, _m_del = diff_stats(m_diff)
        check("birlesme farkinda eklenen satir var", m_add > 0,
              "add=%d" % m_add)

    # ------------------------------------------------------------ serit birimi
    section("7. Serit yerlesimi (birim)")
    # duz zincir: hepsi ayni seritte
    chain = [Commit(sha="c3", short="c3", parents=["c2"]),
             Commit(sha="c2", short="c2", parents=["c1"]),
             Commit(sha="c1", short="c1", parents=[])]
    assign_lanes(chain)
    check("duz zincir tek seritte", [c.lane for c in chain] == [0, 0, 0],
          str([c.lane for c in chain]))

    # catallanma + birlesme
    graph = [Commit(sha="m", short="m", parents=["a", "b"]),
             Commit(sha="a", short="a", parents=["r"]),
             Commit(sha="b", short="b", parents=["r"]),
             Commit(sha="r", short="r", parents=[])]
    assign_lanes(graph)
    lanes = {c.sha: c.lane for c in graph}
    check("birlesme kok seritte", lanes["m"] == 0, str(lanes))
    check("iki dal ayri seritlerde", lanes["a"] != lanes["b"], str(lanes))
    check("ortak ata tekrar 0. seritte", lanes["r"] == 0, str(lanes))
    check("serit sayisi 2", lane_count(graph) == 2, str(lane_count(graph)))

    check("bos gecmis coksmedi", (assign_lanes([]) or True))

    # ------------------------------------------------------------ fark ayristir
    section("8. Fark ayristirici")
    sample = ("diff --git a/x.c b/x.c\n"
              "index 111..222 100644\n"
              "--- a/x.c\n"
              "+++ b/x.c\n"
              "@@ -1,3 +1,4 @@\n"
              " ayni\n"
              "-eski\n"
              "+yeni\n"
              "+ek\n"
              " son\n")
    lines = parse_diff(sample)
    kinds = [ln.kind for ln in lines]
    check("meta satirlari isaretlendi", kinds[:4] == ["meta"] * 4, str(kinds))
    check("hunk satiri isaretlendi", "hunk" in kinds)
    check("ekleme sayisi", kinds.count("add") == 2, str(kinds))
    check("silme sayisi", kinds.count("del") == 1, str(kinds))
    check("baglam sayisi", kinds.count("ctx") == 2, str(kinds))

    ctx = [ln for ln in lines if ln.kind == "ctx"]
    check("ilk baglam satir numaralari",
          ctx[0].old_no == 1 and ctx[0].new_no == 1,
          "%s/%s" % (ctx[0].old_no, ctx[0].new_no))
    adds = [ln for ln in lines if ln.kind == "add"]
    check("ekleme yeni satir numarasi aldi", adds[0].new_no == 2,
          str(adds[0].new_no))
    check("ekleme eski satir numarasi almadi", adds[0].old_no is None)
    dels = [ln for ln in lines if ln.kind == "del"]
    check("silme eski satir numarasi aldi", dels[0].old_no == 2,
          str(dels[0].old_no))
    check("bos fark bos liste verdi", parse_diff("") == [])
    check("bozuk hunk basligi coksmedi",
          isinstance(parse_diff("@@ bozuk @@\n"), list))

    a2, d2 = diff_stats(sample)
    check("diff_stats +++/--- saymadi", a2 == 2 and d2 == 1,
          "add=%d del=%d" % (a2, d2))

    # ------------------------------------------------------------ hata yollari
    section("9. Hata yollari")
    bad = Repo(os.path.join(tmp, "boyle-bir-yer-yok"))
    check("olmayan klasor depo degil", not bad.is_repo())
    check("olmayan klasorde toplevel None", bad.toplevel() is None)
    try:
        repo.checkout("boyle-bir-dal-yok")
        check("olmayan dal reddedildi", False)
    except GitError as exc:
        check("olmayan dal reddedildi", bool(exc.message))
    check("bos yol listesi zararsiz",
          (repo.stage([]) or True) and (repo.unstage([]) or True)
          and (repo.discard([]) or True))
    check("remotes bos liste verdi", repo.remotes() == [], str(repo.remotes()))


if __name__ == "__main__":
    sys.exit(main())
