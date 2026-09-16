"""Git komut satiri sarmalayicisi -- Qt'den BAGIMSIZ.

Arayuz katmani (``app/ui/git_panel.py``) yalnizca burada tanimli veri
yapilarini tuketir; boylece git mantigi arayuzsuz olarak sinanabilir
(``tools/test_git.py``).

Tasarim kurallari
-----------------
* Her cagri **zaman asimlidir**; hicbir islem arayuzu suresiz kilitlemez.
* Windows'ta paketlenmis (konsolsuz) uygulamada konsol penceresi acilmasin
  diye ``CREATE_NO_WINDOW`` kullanilir.
* Ayristirma ``--porcelain=v2 -z`` gibi **kararli** bicimlere dayanir; insan
  icin bicimlenmis cikti hicbir yerde ayristirilmaz.
* Hicbir islev istisna sizdirmaz: hata ``GitError`` olarak yukselir, cagiran
  taraf tek yerde yakalar.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

#: ``git log`` bicim alanlari (sirasi ayristirmayla birebir eslesir)
_LOG_FIELDS = ["%H", "%h", "%P", "%an", "%ae", "%aI", "%D", "%s", "%b"]

#: Alan ve kayit ayirici: NUL. Commit mesajlari ve yazar adlari git'e C dizgesi
#: olarak gectigi icin NUL ICEREMEZ; yazdirilabilir bir ayirici secilseydi ayni
#: karakteri tasiyan bir commit gecmisten sessizce dusebilirdi.
_NUL = "\0"

DEFAULT_TIMEOUT = 20.0
NETWORK_TIMEOUT = 180.0

#: Grafikte kullanilan serit renkleri (arayuz bu listeyi kullanir)
LANE_COLORS = [
    "#4D9FFF", "#3DDC84", "#FF9F43", "#B983FF", "#35D0BA",
    "#FFD166", "#FF5C5C", "#6FB3FF", "#C7D66D", "#FF7AB6",
]


class GitError(Exception):
    """git cagrisi basarisiz oldu; ``message`` kullaniciya gosterilebilir."""

    def __init__(self, message: str, command: str = "", output: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.command = command
        self.output = output


# ============================================================== veri yapilari

@dataclass
class GitFile:
    """Calisma agacindaki tek bir dosyanin durumu."""

    path: str
    index_status: str = " "     # hazirlik alanindaki durum (X)
    work_status: str = " "      # calisma agacindaki durum (Y)
    orig_path: Optional[str] = None
    unmerged: bool = False

    @property
    def untracked(self) -> bool:
        return self.index_status == "?"

    @property
    def staged(self) -> bool:
        return (not self.untracked) and self.index_status not in (" ", "?")

    @property
    def unstaged(self) -> bool:
        return self.untracked or self.work_status not in (" ", "?")

    def label(self) -> str:
        """Kullaniciya gosterilecek tek harfli durum etiketi."""
        if self.unmerged:
            return "U"
        if self.untracked:
            return "?"
        code = self.index_status if self.index_status != " " else self.work_status
        return code if code != " " else "M"


@dataclass
class Commit:
    """Tek bir commit kaydi; ``lane`` grafik yerlesiminde atanir."""

    sha: str
    short: str
    parents: List[str] = field(default_factory=list)
    author: str = ""
    email: str = ""
    when: str = ""              # ISO-8601
    subject: str = ""
    #: Commit mesajinin GOVDESI (ilk satirdan sonrasi). Grafikte dugum
    #: ipucunda gosterilir; tek satirlik commit'lerde bostur.
    body: str = ""
    refs: List[str] = field(default_factory=list)
    lane: int = 0
    is_head: bool = False

    @property
    def is_merge(self) -> bool:
        return len(self.parents) > 1


@dataclass
class RepoStatus:
    """``git status`` sonucunun yapisal ozeti."""

    branch: str = ""
    upstream: str = ""
    ahead: int = 0
    behind: int = 0
    detached: bool = False
    initial: bool = False           # henuz commit yok
    files: List[GitFile] = field(default_factory=list)

    def staged(self) -> List[GitFile]:
        return [f for f in self.files if f.staged and not f.unmerged]

    def unstaged(self) -> List[GitFile]:
        return [f for f in self.files if f.unstaged and not f.unmerged]

    def conflicts(self) -> List[GitFile]:
        return [f for f in self.files if f.unmerged]

    def is_clean(self) -> bool:
        return not self.files


@dataclass
class DiffLine:
    """Fark goruntuleyicinin tek satiri."""

    kind: str       # "hunk" | "add" | "del" | "ctx" | "meta"
    text: str
    old_no: Optional[int] = None
    new_no: Optional[int] = None


# ================================================================== yardimcilar

def _no_window_flags() -> int:
    if sys.platform.startswith("win"):
        return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return 0


def _git_env() -> Dict[str, str]:
    """git ortami.

    ``GIT_LITERAL_PATHSPECS``: bu modulun git'e verdigi yollar HER ZAMAN
    ``status`` / ``show --name-status`` ciktisindan gelen GERCEK dosya
    adlaridir. Varsayilan olarak git bunlari *pathspec* sayar ve ``*``, ``?``,
    ``[...]`` karakterlerini joker olarak yorumlar -- Windows'ta ``[`` ve ``]``
    gecerli dosya adi karakteri oldugundan ``foo[1].c`` icin istenen islem
    ``foo1.c`` dosyasini da etkilerdi (hazirlama, hazirliktan cikarma ve
    ozellikle GERI ALMA'da veri kaybi). Bu degisken jokerleri tumuyle kapatir.
    """
    env = dict(os.environ)
    env["GIT_LITERAL_PATHSPECS"] = "1"
    return env


def _run(args: Sequence[str], cwd: Optional[str], timeout: float,
         check: bool = True) -> Tuple[int, str, str]:
    """git'i cagirir; (donus kodu, stdout, stderr) verir.

    ``check`` dogruysa sifir olmayan donus ``GitError`` yukseltir.
    """
    cmd = ["git"] + list(args)
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, timeout=timeout, check=False,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=_no_window_flags(), env=_git_env(),
        )
    except FileNotFoundError as exc:
        raise GitError("git was not found. Git must be installed and on PATH.",
                       " ".join(cmd)) from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError("git timed out after %g s: %s"
                       % (timeout, " ".join(args[:2])), " ".join(cmd)) from exc
    except OSError as exc:
        raise GitError("git could not be started: %s" % exc, " ".join(cmd)) from exc

    out = proc.stdout.decode("utf-8", "replace")
    err = proc.stderr.decode("utf-8", "replace")
    if check and proc.returncode != 0:
        detail = (err or out).strip() or "git exited with %d" % proc.returncode
        raise GitError(detail, " ".join(cmd), out + err)
    return proc.returncode, out, err


def git_version() -> str:
    """Kurulu git surumu; yoksa ``GitError``."""
    _, out, _ = _run(["--version"], None, 10.0)
    return out.strip()


def git_available() -> bool:
    try:
        git_version()
        return True
    except GitError:
        return False


def _split_z(text: str) -> List[str]:
    parts = text.split("\0")
    if parts and parts[-1] == "":
        parts.pop()
    return parts


def _split_refs(text: str) -> List[str]:
    """``%D`` ref listesini boler.

    git ayirici olarak ", " (virgul + BOSLUK) kullanir ve ref adlarinda bosluk
    YASAKTIR (git-check-ref-format); virgul ise gecerli bir ref karakteridir.
    Bu yuzden yalnizca virgulden bolmek 'a,b' adli tek bir dali iki ref gibi
    gosterirdi.
    """
    return [r.strip() for r in text.split(", ") if r.strip()]


# ====================================================================== depo

class Repo:
    """Tek bir calisma agacinin git islemleri."""

    def __init__(self, root: str) -> None:
        self.root = os.path.abspath(root)

    # ------------------------------------------------------------- kesif

    def is_repo(self) -> bool:
        """Bu klasor bir git calisma agacinin *koku* mu."""
        if not os.path.isdir(self.root):
            return False
        return os.path.isdir(os.path.join(self.root, ".git")) or \
            os.path.isfile(os.path.join(self.root, ".git"))

    def toplevel(self) -> Optional[str]:
        """Icinde bulundugumuz deponun kok klasoru (yoksa None).

        git ileri bolu isaretli ve UZUN ad bicimli yol dondurur; Windows'ta
        cagiran taraf kisa ad (``KUBILA~1``) tutuyor olabilir, bu yuzden
        ``realpath`` ile normallestirilir.
        """
        if not os.path.isdir(self.root):
            return None
        try:
            _, out, _ = _run(["rev-parse", "--show-toplevel"], self.root,
                             DEFAULT_TIMEOUT)
        except GitError:
            return None
        path = out.strip()
        return os.path.realpath(path) if path else None

    def init(self, initial_branch: str = "main") -> None:
        """Depoyu baslatir. Zaten depoysa hicbir sey yapmaz."""
        if self.is_repo():
            return
        os.makedirs(self.root, exist_ok=True)
        # -b eski git surumlerinde yok; once dener, olmazsa yedege duser.
        code, _, _ = _run(["init", "-b", initial_branch], self.root,
                          DEFAULT_TIMEOUT, check=False)
        if code != 0:
            _run(["init"], self.root, DEFAULT_TIMEOUT)

    def has_commits(self) -> bool:
        code, _, _ = _run(["rev-parse", "--verify", "-q", "HEAD"], self.root,
                          DEFAULT_TIMEOUT, check=False)
        return code == 0

    def identity(self) -> Tuple[str, str]:
        """(ad, e-posta) -- tanimsizsa bos dizge."""
        def cfg(key: str) -> str:
            code, out, _ = _run(["config", "--get", key], self.root,
                                DEFAULT_TIMEOUT, check=False)
            return out.strip() if code == 0 else ""
        return cfg("user.name"), cfg("user.email")

    # ------------------------------------------------------------- durum

    def status(self) -> RepoStatus:
        """``git status --porcelain=v2`` ciktisini yapisal hale getirir."""
        st = RepoStatus()
        _, out, _ = _run(["status", "--porcelain=v2", "--branch",
                          "--untracked-files=all", "-z"], self.root,
                         DEFAULT_TIMEOUT)
        parts = _split_z(out)
        i = 0
        while i < len(parts):
            rec = parts[i]
            i += 1
            if not rec:
                continue
            if rec.startswith("# "):
                self._parse_branch_header(rec[2:], st)
                continue
            tag = rec[0]
            if tag == "1":
                st.files.append(self._parse_ordinary(rec))
            elif tag == "2":
                # yeniden adlandirma: ozgun yol BIR SONRAKI NUL alanindadir
                orig = parts[i] if i < len(parts) else None
                i += 1
                gf = self._parse_ordinary(rec, renamed=True)
                gf.orig_path = orig
                st.files.append(gf)
            elif tag == "u":
                fields = rec.split(" ")
                path = rec.split(" ", 10)[-1] if len(fields) > 10 else fields[-1]
                st.files.append(GitFile(path=path, index_status="U",
                                        work_status="U", unmerged=True))
            elif tag == "?":
                st.files.append(GitFile(path=rec[2:], index_status="?",
                                        work_status="?"))
            # "!" (yok sayilan) gosterilmez
        st.files.sort(key=lambda f: f.path.lower())
        return st

    @staticmethod
    def _parse_branch_header(text: str, st: RepoStatus) -> None:
        if text.startswith("branch.head "):
            head = text[len("branch.head "):].strip()
            if head == "(detached)":
                st.detached = True
                st.branch = "HEAD (ayrik)"
            else:
                st.branch = head
        elif text.startswith("branch.upstream "):
            st.upstream = text[len("branch.upstream "):].strip()
        elif text.startswith("branch.oid "):
            st.initial = text[len("branch.oid "):].strip() == "(initial)"
        elif text.startswith("branch.ab "):
            for token in text[len("branch.ab "):].split():
                try:
                    if token.startswith("+"):
                        st.ahead = int(token[1:])
                    elif token.startswith("-"):
                        st.behind = int(token[1:])
                except ValueError:
                    pass

    @staticmethod
    def _parse_ordinary(rec: str, renamed: bool = False) -> GitFile:
        # 1 XY sub mH mI mW hH hI path
        # 2 XY sub mH mI mW hH hI Xscore path
        #
        # DIKKAT: porcelain=v2, "degisiklik yok"u BOSLUK degil NOKTA ile
        # gosterir (v1'in aksine). Nokta bosluga cevrilmezse her dosya hem
        # hazirlanmis hem hazirlanmamis gorunur.
        limit = 9 if renamed else 8
        fields = rec.split(" ", limit)
        xy = fields[1] if len(fields) > 1 else ".."
        path = fields[-1] if fields else ""
        index_status = xy[0] if xy else "."
        work_status = xy[1] if len(xy) > 1 else "."
        return GitFile(path=path,
                       index_status=" " if index_status == "." else index_status,
                       work_status=" " if work_status == "." else work_status)

    # ------------------------------------------------------------- gecmis

    def log(self, limit: int = 400) -> List[Commit]:
        """Butun dallardaki commit'leri topolojik sirada verir.

        Hem alanlar hem kayitlar NUL ile ayrilir (``%x00`` + ``-z``); cikti
        duz bir NUL akisidir ve her commit tam ``len(_LOG_FIELDS)`` alan
        katkilar. Boylece mesajinda ya da yazar adinda hangi karakter olursa
        olsun hicbir commit gecmisten dusmez.
        """
        if not self.has_commits():
            return []
        fmt = "%x00".join(_LOG_FIELDS)
        _, out, _ = _run(["log", "--all", "--topo-order", "-z",
                          "--max-count=%d" % max(1, limit),
                          "--pretty=format:" + fmt], self.root,
                         DEFAULT_TIMEOUT)
        tokens = out.split(_NUL)
        head_sha = self.head_sha()
        commits: List[Commit] = []
        width = len(_LOG_FIELDS)
        for start in range(0, (len(tokens) // width) * width, width):
            f = tokens[start:start + width]
            sha = f[0].strip()
            if not sha:
                continue
            commits.append(Commit(
                sha=sha, short=f[1].strip(),
                parents=[p for p in f[2].split() if p],
                author=f[3], email=f[4], when=f[5],
                refs=_split_refs(f[6]), subject=f[7].rstrip("\n"),
                # %b EN SON alandir; tek satirlik commit'lerde bostur.
                # Uzunluk denetimi savunmacidir: eski bir bicimden gelen
                # kisa kayit IndexError ile paneli cokertmemeli.
                body=f[8].strip("\n") if len(f) > 8 else "",
                is_head=(sha == head_sha),
            ))
        assign_lanes(commits)
        return commits

    def head_sha(self) -> str:
        code, out, _ = _run(["rev-parse", "HEAD"], self.root, DEFAULT_TIMEOUT,
                            check=False)
        return out.strip() if code == 0 else ""

    def commit_files(self, sha: str) -> List[GitFile]:
        """Bir commit'in degistirdigi dosyalar.

        ``-m --first-parent`` BIRLESME commit'leri icindir: git birlesmeler
        icin varsayilan olarak fark URETMEZ, ``--cc`` ise yalnizca cakisma
        cozumlerini gosterir (temiz birlesmede BOS cikar). Ilk ebeveyne gore
        fark, "bu birlesme neyi getirdi" sorusunun karsiligidir. Birlesme
        olmayan commit'lerde secenek etkisizdir.
        """
        _, out, _ = _run(["show", "--format=", "--name-status",
                          "-r", "-z", "--root", "-m", "--first-parent", sha],
                         self.root, DEFAULT_TIMEOUT)
        parts = _split_z(out)
        files: List[GitFile] = []
        i = 0
        while i < len(parts):
            code = parts[i]
            i += 1
            if not code:
                continue
            letter = code[0]
            if letter in ("R", "C"):
                src = parts[i] if i < len(parts) else ""
                dst = parts[i + 1] if i + 1 < len(parts) else ""
                i += 2
                files.append(GitFile(path=dst, index_status=letter,
                                     orig_path=src))
            else:
                path = parts[i] if i < len(parts) else ""
                i += 1
                files.append(GitFile(path=path, index_status=letter))
        files.sort(key=lambda f: f.path.lower())
        return files

    # ------------------------------------------------------------- farklar

    def diff(self, path: str, staged: bool = False, untracked: bool = False,
             context: int = 3) -> str:
        """Tek dosyanin birlesik fark metni.

        Izlenmeyen dosya icin git fark uretmez; icerik ``+`` onekleriyle
        sentezlenir.
        """
        if untracked:
            return self._synthetic_new_file_diff(path)
        args = ["diff", "--no-color", "-U%d" % max(0, context)]
        if staged:
            args.append("--cached")
        args += ["--", path]
        _, out, _ = _run(args, self.root, DEFAULT_TIMEOUT)
        return out

    def _synthetic_new_file_diff(self, path: str) -> str:
        full = os.path.join(self.root, path)
        try:
            with open(full, "rb") as fh:
                raw = fh.read()
        except OSError as exc:
            return "* okunamadi: %s" % exc
        if b"\0" in raw[:8000]:
            return "* binary file (%d bytes) - no diff shown" % len(raw)
        text = raw.decode("utf-8", "replace")
        lines = text.splitlines()
        head = ["diff --git a/%s b/%s" % (path, path), "new file",
                "--- /dev/null", "+++ b/%s" % path,
                "@@ -0,0 +1,%d @@" % len(lines)]
        return "\n".join(head + ["+" + ln for ln in lines])

    def diff_commit(self, sha: str, path: Optional[str] = None,
                    context: int = 3) -> str:
        """Bir commit'in (istege bagli tek dosyanin) farki.

        BIRLESME commit'leri ilk ebeveyne gore gosterilir; bkz.
        ::meth:`commit_files` aciklamasi.
        """
        args = ["show", "--no-color", "--format=", "-U%d" % max(0, context),
                "--root", "-m", "--first-parent", sha]
        if path:
            args += ["--", path]
        _, out, _ = _run(args, self.root, DEFAULT_TIMEOUT)
        return out

    def file_at(self, sha: str, path: str) -> str:
        """Dosyanin bir commit'teki tam icerigi."""
        _, out, _ = _run(["show", "%s:%s" % (sha, path)], self.root,
                         DEFAULT_TIMEOUT)
        return out

    def staged_text(self, path: str) -> str:
        """Dosyanin HAZIRLIK ALANINDAKI (index) icerigi.

        ``file_at("HEAD", ...)`` son commit'i verir; hazirlanmis ama henuz
        commit edilmemis surum yalnizca index'tedir ve ``:path`` sozdizimi
        ile okunur. Model karsilastirmasi hazirlanmis degisiklikleri
        gosterebilmek icin buna ihtiyac duyar.
        """
        _, out, _ = _run(["show", ":%s" % path], self.root, DEFAULT_TIMEOUT)
        return out

    # ------------------------------------------------------------- islemler

    def stage(self, paths: Sequence[str]) -> None:
        if not paths:
            return
        _run(["add", "--"] + list(paths), self.root, DEFAULT_TIMEOUT)

    def stage_all(self) -> None:
        _run(["add", "--all"], self.root, DEFAULT_TIMEOUT)

    def unstage(self, paths: Sequence[str]) -> None:
        if not paths:
            return
        if not self.has_commits():
            _run(["rm", "--cached", "-r", "--"] + list(paths), self.root,
                 DEFAULT_TIMEOUT)
            return
        _run(["restore", "--staged", "--"] + list(paths), self.root,
             DEFAULT_TIMEOUT)

    def discard(self, paths: Sequence[str]) -> None:
        """Calisma agacindaki degisiklikleri geri alir; izlenmeyeni siler.

        YIKICIDIR -- cagiran taraf kullanicidan onay almalidir.
        """
        if not paths:
            return
        tracked: List[str] = []
        untracked: List[str] = []
        known = {f.path: f for f in self.status().files}
        for p in paths:
            gf = known.get(p)
            (untracked if (gf is not None and gf.untracked) else tracked).append(p)
        if tracked:
            if self.has_commits():
                _run(["restore", "--worktree", "--"] + tracked, self.root,
                     DEFAULT_TIMEOUT)
            else:
                _run(["rm", "-f", "--"] + tracked, self.root, DEFAULT_TIMEOUT)
        for p in untracked:
            full = os.path.join(self.root, p)
            try:
                if os.path.isfile(full):
                    os.remove(full)
            except OSError as exc:
                raise GitError("could not delete %s (%s)" % (p, exc)) from exc

    def commit(self, message: str, amend: bool = False,
               author_name: str = "", author_email: str = "") -> str:
        """Hazirlik alanini commit'ler; yeni commit'in kisa sha'sini verir."""
        text = message.strip()
        if not text:
            raise GitError("The commit message cannot be empty.")
        args: List[str] = []
        if author_name and author_email:
            args += ["-c", "user.name=%s" % author_name,
                     "-c", "user.email=%s" % author_email]
        args += ["commit", "-m", text]
        if amend:
            args.append("--amend")
        _run(args, self.root, DEFAULT_TIMEOUT)
        _, out, _ = _run(["rev-parse", "--short", "HEAD"], self.root,
                         DEFAULT_TIMEOUT)
        return out.strip()

    # ------------------------------------------------------------- dallar

    def branches(self) -> List[str]:
        """Yerel dal adlari.

        Ayrik (detached) HEAD durumunda git listeye '(HEAD detached at ...)'
        yer tutucusunu da basar; bu bir dal DEGILDIR ve checkout edilemez, bu
        yuzden elenir (git ref adlarinda '(' kullanilamaz).
        """
        code, out, _ = _run(["branch", "--format=%(refname:short)"], self.root,
                            DEFAULT_TIMEOUT, check=False)
        if code != 0:
            return []
        return [b.strip() for b in out.splitlines()
                if b.strip() and not b.strip().startswith("(")]

    def create_branch(self, name: str, checkout: bool = True) -> None:
        clean = name.strip()
        if not clean:
            raise GitError("The branch name cannot be empty.")
        if checkout:
            _run(["checkout", "-b", clean], self.root, DEFAULT_TIMEOUT)
        else:
            _run(["branch", clean], self.root, DEFAULT_TIMEOUT)

    def checkout(self, name: str) -> None:
        _run(["checkout", name], self.root, DEFAULT_TIMEOUT)

    def remotes(self) -> List[str]:
        code, out, _ = _run(["remote"], self.root, DEFAULT_TIMEOUT, check=False)
        if code != 0:
            return []
        return [r.strip() for r in out.splitlines() if r.strip()]

    # ------------------------------------------------------------- ag islemleri

    def fetch(self, remote: str = "") -> str:
        args = ["fetch", "--prune"] + ([remote] if remote else ["--all"])
        _, out, err = _run(args, self.root, NETWORK_TIMEOUT)
        return (out + err).strip() or "Fetch complete."

    def pull(self, remote: str = "", branch: str = "") -> str:
        args = ["pull", "--ff-only"]
        if remote:
            args.append(remote)
            if branch:
                args.append(branch)
        _, out, err = _run(args, self.root, NETWORK_TIMEOUT)
        return (out + err).strip() or "Pull complete."

    def push(self, remote: str = "", branch: str = "",
             set_upstream: bool = False) -> str:
        args = ["push"]
        if set_upstream:
            args.append("--set-upstream")
        if remote:
            args.append(remote)
            if branch:
                args.append(branch)
        _, out, err = _run(args, self.root, NETWORK_TIMEOUT)
        return (out + err).strip() or "Push complete."


# ============================================================ grafik yerlesimi

def assign_lanes(commits: List[Commit]) -> None:
    """Commit'lere serit (lane) numarasi atar -- GitKraken benzeri yerlesim.

    Etkin seritler listesi tutulur; her slot o seritte BEKLENEN commit'in
    sha'sini saklar. Commit beklenen bir seritteyse o seride oturur, degilse
    ilk bos serite girer. Birlesme (merge) ek ebeveynleri icin de serit
    ayrilir; boylece cizimde her kenar iki serit arasinda akar.
    """
    active: List[Optional[str]] = []

    def first_free() -> int:
        for idx, slot in enumerate(active):
            if slot is None:
                return idx
        active.append(None)
        return len(active) - 1

    for commit in commits:
        lane = -1
        for idx, slot in enumerate(active):
            if slot == commit.sha:
                lane = idx
                break
        if lane < 0:
            lane = first_free()
        else:
            # ayni commit'i bekleyen diger seritler burada birlesir
            for idx in range(len(active)):
                if idx != lane and active[idx] == commit.sha:
                    active[idx] = None
        commit.lane = lane

        active[lane] = commit.parents[0] if commit.parents else None
        for extra in commit.parents[1:]:
            if extra in active:
                continue
            active[first_free()] = extra

        while active and active[-1] is None:
            active.pop()


def lane_count(commits: Sequence[Commit]) -> int:
    return (max((c.lane for c in commits), default=-1)) + 1


# ============================================================ fark ayristirma

def parse_diff(text: str) -> List[DiffLine]:
    """Birlesik fark metnini satir satir yapisal hale getirir."""
    lines: List[DiffLine] = []
    old_no = new_no = 0
    for raw in text.splitlines():
        if raw.startswith("@@"):
            old_no, new_no = _hunk_starts(raw)
            lines.append(DiffLine("hunk", raw))
        elif raw.startswith(("diff --git", "index ", "--- ", "+++ ",
                             "new file", "deleted file", "old mode",
                             "new mode", "similarity index", "rename from",
                             "rename to", "Binary files")):
            lines.append(DiffLine("meta", raw))
        elif raw.startswith("+"):
            lines.append(DiffLine("add", raw[1:], None, new_no))
            new_no += 1
        elif raw.startswith("-"):
            lines.append(DiffLine("del", raw[1:], old_no, None))
            old_no += 1
        elif raw.startswith("\\"):
            lines.append(DiffLine("meta", raw))
        else:
            body = raw[1:] if raw.startswith(" ") else raw
            lines.append(DiffLine("ctx", body, old_no, new_no))
            old_no += 1
            new_no += 1
    return lines


def _hunk_starts(header: str) -> Tuple[int, int]:
    """``@@ -a,b +c,d @@`` basligindan (a, c) cikarir."""
    try:
        core = header.split("@@")[1].strip()
        old_part, new_part = core.split(" ")[0], core.split(" ")[1]
        old = int(old_part.lstrip("-").split(",")[0])
        new = int(new_part.lstrip("+").split(",")[0])
        return old, new
    except (IndexError, ValueError):
        return 0, 0


def diff_stats(text: str) -> Tuple[int, int]:
    """(eklenen, silinen) satir sayisi."""
    add = dele = 0
    for raw in text.splitlines():
        if raw.startswith("+") and not raw.startswith("+++"):
            add += 1
        elif raw.startswith("-") and not raw.startswith("---"):
            dele += 1
    return add, dele
