"""A wrapper around the git command line -- INDEPENDENT of Qt.

The interface layer (``app/ui/git_panel.py``) consumes only the data
structures defined here, so the git logic can be tested without a user
interface (``tools/test_git.py``).

Design rules
------------
* Every call has a **timeout**; no operation locks the interface forever.
* On Windows ``CREATE_NO_WINDOW`` is used so that a console window does not
  pop up in the packaged (console-less) application.
* Parsing relies on **stable** formats such as ``--porcelain=v2 -z``; output
  formatted for humans is never parsed anywhere.
* No function leaks an exception: an error is raised as ``GitError`` and the
  caller catches it in one place.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

#: ``git log`` format fields (the order matches the parser exactly)
_LOG_FIELDS = ["%H", "%h", "%P", "%an", "%ae", "%aI", "%D", "%s", "%b"]

#: Field and record separator: NUL. Commit messages and author names reach git
#: as C strings and so CANNOT CONTAIN NUL; with a printable separator, a commit
#: carrying that character could silently drop out of the history.
_NUL = "\0"

DEFAULT_TIMEOUT = 20.0
NETWORK_TIMEOUT = 180.0

#: The lane colours used in the graph (the interface uses this list)
LANE_COLORS = [
    "#4D9FFF", "#3DDC84", "#FF9F43", "#B983FF", "#35D0BA",
    "#FFD166", "#FF5C5C", "#6FB3FF", "#C7D66D", "#FF7AB6",
]


class GitError(Exception):
    """A git call failed; ``message`` can be shown to the user."""

    def __init__(self, message: str, command: str = "", output: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.command = command
        self.output = output


#  data structures

@dataclass
class GitFile:
    """The state of a single file in the working tree."""

    path: str
    index_status: str = " "     # the status in the staging area (X)
    work_status: str = " "      # the status in the working tree (Y)
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
        """The single-letter status label shown to the user."""
        if self.unmerged:
            return "U"
        if self.untracked:
            return "?"
        code = self.index_status if self.index_status != " " else self.work_status
        return code if code != " " else "M"


@dataclass
class Commit:
    """A single commit record; ``lane`` is assigned during graph layout."""

    sha: str
    short: str
    parents: List[str] = field(default_factory=list)
    author: str = ""
    email: str = ""
    when: str = ""              # ISO-8601
    subject: str = ""
    #: The BODY of the commit message (everything after the first line). Shown
    #: in the node tooltip in the graph; empty for one-line commits.
    body: str = ""
    refs: List[str] = field(default_factory=list)
    lane: int = 0
    is_head: bool = False

    @property
    def is_merge(self) -> bool:
        return len(self.parents) > 1


@dataclass
class RepoStatus:
    """The structured summary of a ``git status`` result."""

    branch: str = ""
    upstream: str = ""
    ahead: int = 0
    behind: int = 0
    detached: bool = False
    initial: bool = False           # no commit yet
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
    """One line of the diff viewer."""

    kind: str       # "hunk" | "add" | "del" | "ctx" | "meta"
    text: str
    old_no: Optional[int] = None
    new_no: Optional[int] = None


#  helpers

def _no_window_flags() -> int:
    if sys.platform.startswith("win"):
        return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return 0


def _git_env() -> Dict[str, str]:
    """The git environment.

    ``GIT_LITERAL_PATHSPECS``: the paths this module hands to git are ALWAYS
    the REAL file names coming out of ``status`` / ``show --name-status``. By
    default git treats them as a *pathspec* and reads ``*``, ``?`` and
    ``[...]`` as wildcards -- and since ``[`` and ``]`` are valid file name
    characters on Windows, an operation meant for ``foo[1].c`` would also hit
    ``foo1.c`` (staging, unstaging and, above all, DISCARD -- data loss). This
    variable turns wildcards off completely.
    """
    env = dict(os.environ)
    env["GIT_LITERAL_PATHSPECS"] = "1"
    return env


def _run(args: Sequence[str], cwd: Optional[str], timeout: float,
         check: bool = True) -> Tuple[int, str, str]:
    """Calls git; returns (return code, stdout, stderr).

    With ``check`` true, a non-zero return raises ``GitError``.
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
    """The installed git version; ``GitError`` when there is none."""
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
    """Splits the ``%D`` ref list.

    git uses ", " (comma + SPACE) as the separator and spaces are FORBIDDEN in
    ref names (git-check-ref-format), while a comma is a valid ref character.
    So splitting on the comma alone would show one branch named 'a,b' as two
    refs.
    """
    return [r.strip() for r in text.split(", ") if r.strip()]


#  repository

class Repo:
    """The git operations of a single working tree."""

    def __init__(self, root: str) -> None:
        self.root = os.path.abspath(root)

    # discovery

    def is_repo(self) -> bool:
        """Is this folder the *root* of a git working tree?"""
        if not os.path.isdir(self.root):
            return False
        return os.path.isdir(os.path.join(self.root, ".git")) or \
            os.path.isfile(os.path.join(self.root, ".git"))

    def toplevel(self) -> Optional[str]:
        """The root folder of the repository we are in (None if none).

        git returns a path with forward slashes and LONG names; on Windows the
        caller may be holding a short name (``RUNNER~1``), so it is normalised
        with ``realpath``.
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
        """Initialises the repository. Does nothing if it is one already."""
        if self.is_repo():
            return
        os.makedirs(self.root, exist_ok=True)
        # -b is missing in older git versions; try it first, then fall back.
        code, _, _ = _run(["init", "-b", initial_branch], self.root,
                          DEFAULT_TIMEOUT, check=False)
        if code != 0:
            _run(["init"], self.root, DEFAULT_TIMEOUT)

    def has_commits(self) -> bool:
        code, _, _ = _run(["rev-parse", "--verify", "-q", "HEAD"], self.root,
                          DEFAULT_TIMEOUT, check=False)
        return code == 0

    def identity(self) -> Tuple[str, str]:
        """(name, e-mail) -- empty strings when undefined."""
        def cfg(key: str) -> str:
            code, out, _ = _run(["config", "--get", key], self.root,
                                DEFAULT_TIMEOUT, check=False)
            return out.strip() if code == 0 else ""
        return cfg("user.name"), cfg("user.email")

    # status

    def status(self) -> RepoStatus:
        """Turns ``git status --porcelain=v2`` output into structure."""
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
                # a rename: the original path is in the NEXT NUL field
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
            # "!" (ignored) is not shown
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
        # CAREFUL: porcelain=v2 marks "no change" with a DOT, not a SPACE (unlike
        # v1). Unless the dot is turned into a space, every file looks both staged
        # and unstaged.
        limit = 9 if renamed else 8
        fields = rec.split(" ", limit)
        xy = fields[1] if len(fields) > 1 else ".."
        path = fields[-1] if fields else ""
        index_status = xy[0] if xy else "."
        work_status = xy[1] if len(xy) > 1 else "."
        return GitFile(path=path,
                       index_status=" " if index_status == "." else index_status,
                       work_status=" " if work_status == "." else work_status)

    # history

    def log(self, limit: int = 400) -> List[Commit]:
        """The commits on every branch, in topological order.

        Both fields and records are NUL separated (``%x00`` + ``-z``); the
        output is a flat NUL stream and each commit contributes exactly
        ``len(_LOG_FIELDS)`` fields. So no commit can drop out of the history,
        whatever characters its message or author name contains.
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
                # %b is the LAST field; it is empty for one-line commits. The length
                # check is defensive: a short record from an older format must not
                # crash the panel with IndexError.
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
        """The files a commit changed.

        ``-m --first-parent`` is for MERGE commits: by default git PRODUCES no
        diff for merges, and ``--cc`` shows only conflict resolutions (EMPTY on
        a clean merge). A diff against the first parent is the answer to "what
        did this merge bring in". On non-merge commits the option has no effect.

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

    # diffs

    def diff(self, path: str, staged: bool = False, untracked: bool = False,
             context: int = 3) -> str:
        """The unified diff text of a single file.

        git produces no diff for an untracked file; the content is synthesised
        with ``+`` prefixes.
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
        """The diff of a commit (optionally of a single file).

        MERGE commits are shown against the first parent; see the
        ::meth:`commit_files` docstring.
        """
        args = ["show", "--no-color", "--format=", "-U%d" % max(0, context),
                "--root", "-m", "--first-parent", sha]
        if path:
            args += ["--", path]
        _, out, _ = _run(args, self.root, DEFAULT_TIMEOUT)
        return out

    def file_at(self, sha: str, path: str) -> str:
        """The full content of a file at a commit."""
        _, out, _ = _run(["show", "%s:%s" % (sha, path)], self.root,
                         DEFAULT_TIMEOUT)
        return out

    def staged_text(self, path: str) -> str:
        """The content of the file IN THE STAGING AREA (the index).

        ``file_at("HEAD", ...)`` gives the last commit; a version that is staged
        but not yet committed exists only in the index and is read with the
        ``:path`` syntax. The model comparison needs this to be able to show
        staged changes.
        """
        _, out, _ = _run(["show", ":%s" % path], self.root, DEFAULT_TIMEOUT)
        return out

    # operations

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
        """Discards the changes in the working tree; deletes untracked files.

        IT IS DESTRUCTIVE -- the caller must get confirmation from the user.
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
        """Commits the staging area; returns the short sha of the new commit."""
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

    # branches

    def branches(self) -> List[str]:
        """The local branch names.

        On a detached HEAD git also prints the '(HEAD detached at ...)'
        placeholder into the list; that IS NOT a branch and cannot be checked
        out, so it is filtered out (git ref names cannot contain '(').
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

    # network operations

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


#  graph layout

def assign_lanes(commits: List[Commit]) -> None:
    """Assigns a lane number to each commit -- a GitKraken-like layout.

    A list of active lanes is kept; every slot holds the sha of the commit
    EXPECTED in that lane. A commit that is expected in a lane sits in it,
    otherwise it takes the first free lane. A lane is also reserved for the
    extra parents of a merge, so every edge in the drawing flows between two.
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
            # other lanes waiting for the same commit merge here
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


#  diff parsing

def parse_diff(text: str) -> List[DiffLine]:
    """Turns unified diff text into structure, line by line."""
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
    """Extracts (a, c) from an ``@@ -a,b +c,d @@`` header."""
    try:
        core = header.split("@@")[1].strip()
        old_part, new_part = core.split(" ")[0], core.split(" ")[1]
        old = int(old_part.lstrip("-").split(",")[0])
        new = int(new_part.lstrip("+").split(",")[0])
        return old, new
    except (IndexError, ValueError):
        return 0, 0


def diff_stats(text: str) -> Tuple[int, int]:
    """The (added, removed) line counts."""
    add = dele = 0
    for raw in text.splitlines():
        if raw.startswith("+") and not raw.startswith("+++"):
            add += 1
        elif raw.startswith("-") and not raw.startswith("---"):
            dele += 1
    return add, dele
