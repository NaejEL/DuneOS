# Permanent Bash/PowerShell guard: destructive commands (HG-01 to HG-05).
#
# A net, not a proof (risk I3): `eval`, variables, aliases and the content of
# called scripts are not analysed; a target that cannot be resolved is blocked.
#
# - HG-02: five blocked categories:
#   1 force-push, 2 branch deletion (remote or local), 3 history rewrite,
#   4 destruction of work, 5 file deletion (allowed only when EVERY target is
#   generated: artefact folder, created during the active cycle, or inside the
#   `Files scope` of the active spec - decided by the `deletable` callback).
# - HG-03: the command is split on ; && || | & and newlines, recursion into
#   $( ), backticks, `bash -c`, `sh -c`, `env`, `sudo`, `command`, `xargs`;
#   tokens by shlex; `git -C` and `-c` are skipped; PowerShell syntax for the
#   PowerShell tool. A command that cannot be analysed raises Unanalyzable (HK-04).
# - HG-04: the message of a blocking.

from __future__ import annotations

import re
import shlex
from typing import Callable, List, NamedTuple, Optional, Sequence

CATEGORIES = {1: "force-push", 2: "branch-delete", 3: "history-rewrite", 4: "work-destruction", 5: "file-delete"}
MESSAGE = ("Blocked by harness guard: %s (%s). Destructive operations outside the accepted scope are never run "
           "by an agent; run it yourself in a terminal if intended.")
MAX_DEPTH = 6
WRAPPERS = ("sudo", "env", "command", "nohup", "time", "exec", "builtin", "stdbuf", "nice")
SHELLS = ("bash", "sh", "zsh", "dash", "ksh")
POWERSHELLS = ("pwsh", "powershell")
DELETERS = ("rm", "rmdir", "unlink", "del", "erase", "rd", "remove-item", "ri")
SHELL_META = re.compile(r"[$`*?\[\]{}~]")


class Violation(NamedTuple):
    category: int
    fragment: str

    @property
    def name(self) -> str:
        return CATEGORIES[self.category]

    def message(self) -> str:
        return MESSAGE % (self.name, self.fragment)


class Unanalyzable(ValueError):
    """The command cannot be analysed (unbalanced quote, nesting too deep): HK-04, let it pass."""


Deletable = Callable[[str], bool]


# ---------------------------------------------------------------- splitting

def split_commands(text: str, powershell: bool = False) -> List[str]:
    """HG-03: simple commands, split outside quotes on ; && || | & and newlines.
    Substitutions ($( ) and backticks) are kept inside the piece that holds them."""
    parts: List[str] = []
    cur: List[str] = []
    quote: Optional[str] = None
    depth = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            cur.append(ch)
            if ch == "\\" and quote == '"' and i + 1 < len(text) and not powershell:
                cur.append(text[i + 1])
                i += 1
            elif ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
            cur.append(ch)
        elif ch == "\\" and not powershell and i + 1 < len(text):
            cur.append(ch + text[i + 1])
            i += 1
        elif text.startswith("$(", i):
            depth += 1
            cur.append("$(")
            i += 1
        elif ch == ")" and depth:
            depth -= 1
            cur.append(ch)
        elif depth == 0 and ch in ";|&\n":
            if ch in "|&" and i + 1 < len(text) and text[i + 1] == ch:
                i += 1
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    if quote:
        raise Unanalyzable("unbalanced quote")
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def substitutions(piece: str, powershell: bool = False) -> List[str]:
    """The commands inside $( ) and backticks (not inside single quotes)."""
    found: List[str] = []
    i, single = 0, False
    while i < len(piece):
        ch = piece[i]
        if ch == "'":
            single = not single
        elif not single and piece.startswith("$(", i):
            depth, j = 1, i + 2
            while j < len(piece) and depth:
                depth += {"(": 1, ")": -1}.get(piece[j], 0)
                j += 1
            found.append(piece[i + 2:j - 1])
            i = j - 1
        elif not single and ch == "`" and not powershell:
            j = piece.find("`", i + 1)
            if j < 0:
                raise Unanalyzable("unbalanced backtick")
            found.append(piece[i + 1:j])
            i = j
        i += 1
    return found


def tokens(piece: str, powershell: bool = False) -> List[str]:
    try:
        lexer = shlex.shlex(piece, posix=not powershell)
        lexer.whitespace_split = True
        lexer.commenters = ""
        toks = list(lexer)
    except ValueError as exc:
        raise Unanalyzable(str(exc))
    if powershell:
        toks = [t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in "'\"" else t for t in toks]
    return toks


# ------------------------------------------------------------------ analysis

def _base(token: str) -> str:
    name = token.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def analyze(command: str, powershell: bool = False, deletable: Optional[Deletable] = None,
            depth: int = 0) -> Optional[Violation]:
    """The first violation of a (possibly compound) command, or None."""
    if depth > MAX_DEPTH:
        raise Unanalyzable("nesting too deep")
    check = deletable or (lambda _target: False)
    for piece in split_commands(command, powershell):
        for inner in substitutions(piece, powershell):
            hit = analyze(inner, powershell, check, depth + 1)
            if hit:
                return hit
        hit = _simple(piece, powershell, check, depth)
        if hit:
            return hit
    return None


def _strip_wrappers(toks: List[str]) -> List[str]:
    while toks:
        head = _base(toks[0])
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[0]):
            toks = toks[1:]
        elif head in WRAPPERS:
            toks = toks[1:]
            while toks and (toks[0].startswith("-") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[0])):
                toks = toks[1:]
        else:
            break
    return toks


def _simple(piece: str, powershell: bool, deletable: Deletable, depth: int) -> Optional[Violation]:
    while piece.startswith("(") and piece.endswith(")") and not piece.startswith("$("):
        piece = piece[1:-1].strip()      # a subshell group: its content is analysed
    toks = _strip_wrappers(tokens(piece, powershell))
    if not toks:
        return None
    name = _base(toks[0])
    fragment = " ".join(toks)[:100]
    if name in SHELLS + POWERSHELLS:
        for flag in ("-c", "-Command", "-command", "-lc", "-ic"):
            if flag in toks[1:]:
                at = toks.index(flag, 1)
                if at + 1 < len(toks):
                    return analyze(" ".join(toks[at + 1:]) if name in POWERSHELLS else toks[at + 1],
                                   name in POWERSHELLS, deletable, depth + 1)
        return None
    if name == "cmd":
        for at, tok in enumerate(toks[1:], 1):
            if tok.lower() in ("/c", "/k", "/r") and at + 1 < len(toks):
                return analyze(" ".join(toks[at + 1:]), False, deletable, depth + 1)
        return None
    if name in ("iex", "invoke-expression"):
        rest = [t for t in toks[1:] if t.lower() not in ("-command",)]
        return analyze(" ".join(rest), True, deletable, depth + 1) if rest else None
    if name == "xargs":
        rest = [t for t in toks[1:]]
        while rest and rest[0].startswith("-"):
            rest = rest[1:] if rest[0] not in ("-I", "-n", "-P", "-d", "-L", "-s") else rest[2:]
        if rest and _base(rest[0]) in DELETERS + ("find",):
            return Violation(5, fragment)
        return analyze(" ".join(rest), powershell, deletable, depth + 1) if rest else None
    if name == "git":
        return _git(toks[1:], fragment, deletable)
    if name in DELETERS:
        return _delete(name, toks[1:], fragment, powershell, deletable)
    if name == "find":
        return _find(toks[1:], fragment, deletable)
    return None


def _git_args(args: Sequence[str]) -> List[str]:
    """Skips the global options of git (`-C <dir>`, `-c <k=v>`, `--git-dir`, `--work-tree`, `--no-pager`...)."""
    out = list(args)
    while out and out[0].startswith("-"):
        if out[0] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"):
            out = out[2:]
        else:
            out = out[1:]
    return out


def _short(args: Sequence[str], letters: str) -> bool:
    return any(a.startswith("-") and not a.startswith("--") and any(c in letters for c in a[1:]) for a in args)


def _git(raw: Sequence[str], fragment: str, deletable: Deletable) -> Optional[Violation]:
    args = _git_args(raw)
    if not args:
        return None
    sub, rest = args[0], args[1:]
    opts = [a for a in rest if a.startswith("-")]
    pos = [a for a in rest if not a.startswith("-")]
    if sub == "push":
        if (any(o in ("--force", "--mirror", "--force-if-includes") or o.startswith("--force-with-lease")
                for o in opts) or _short(rest, "f") or any(p.startswith("+") for p in pos)):
            return Violation(1, fragment)
        if "--delete" in opts or _short(rest, "d") or any(p.startswith(":") and len(p) > 1 for p in pos):
            return Violation(2, fragment)
    elif sub == "branch":
        if "--delete" in opts or _short(rest, "dD"):
            return Violation(2, fragment)
    elif sub == "rebase":
        return Violation(3, fragment)
    elif sub == "commit":
        if "--amend" in opts:
            return Violation(3, fragment)
    elif sub == "reset":
        if any(o in ("--hard", "--keep", "--merge") for o in opts):
            return Violation(3, fragment)
        before = rest[:rest.index("--")] if "--" in rest else rest
        if any(not a.startswith("-") and a != "HEAD" for a in before):
            return Violation(3, fragment)
    elif sub in ("filter-branch", "filter-repo", "replace"):
        return Violation(3, fragment)
    elif sub == "update-ref":
        if "-d" in opts or "--delete" in opts:
            return Violation(3, fragment)
    elif sub == "reflog":
        if pos and pos[0] in ("expire", "delete"):
            return Violation(3, fragment)
    elif sub == "gc":
        if any(o.startswith("--prune") for o in opts):
            return Violation(3, fragment)
    elif sub == "clean":
        if "--force" in opts or _short(rest, "f"):
            return Violation(4, fragment)
    elif sub == "checkout":
        if "--" in rest or pos[:1] == ["."] or "-f" in opts or "--force" in opts:
            return Violation(4, fragment)
    elif sub == "restore":
        staged_only = ("--staged" in opts or "-S" in opts) and "--worktree" not in opts and "-W" not in opts
        if not staged_only:
            return Violation(4, fragment)
    elif sub == "stash":
        if pos[:1] and pos[0] in ("drop", "clear"):
            return Violation(4, fragment)
    elif sub == "worktree":
        if pos[:1] and pos[0] in ("remove", "prune"):
            return Violation(4, fragment)
    elif sub == "rm":
        if "--cached" in opts:
            return None
        return _targets_ok(pos, fragment, deletable)
    return None


def _targets_ok(targets: Sequence[str], fragment: str, deletable: Deletable) -> Optional[Violation]:
    """Category 5: every target must be resolvable and generated."""
    if not targets:
        return Violation(5, fragment)
    for target in targets:
        if SHELL_META.search(target) or not deletable(target):
            return Violation(5, fragment)
    return None


def _delete(name: str, args: Sequence[str], fragment: str, powershell: bool, deletable: Deletable) -> Optional[Violation]:
    targets: List[str] = []
    skip = False
    value_options = ("-path", "-literalpath", "-filter", "-include", "-exclude")
    for i, arg in enumerate(args):
        if skip:
            skip = False
            continue
        low = arg.lower()
        if powershell or name in ("remove-item", "ri"):
            if low in ("-path", "-literalpath") and i + 1 < len(args):
                targets += [t for t in args[i + 1].split(",") if t]
                skip = True
            elif low in value_options:
                skip = True
            elif arg.startswith("-"):
                continue
            else:
                targets += [t for t in arg.split(",") if t]
        elif arg.startswith("-") or (name in ("del", "erase", "rd") and arg.startswith("/") and len(arg) <= 3):
            continue
        elif arg == "--":
            continue
        else:
            targets.append(arg)
    return _targets_ok(targets, fragment, deletable)


def _find(args: Sequence[str], fragment: str, deletable: Deletable) -> Optional[Violation]:
    deleting = "-delete" in args or any(
        a in ("-exec", "-execdir", "-ok") and i + 1 < len(args) and _base(args[i + 1]) in DELETERS
        for i, a in enumerate(args))
    if not deleting:
        return None
    roots = []
    for a in args:
        if a.startswith("-") or a in ("(", "!", ")"):
            break
        roots.append(a)
    return _targets_ok(roots or ["."], fragment, deletable)
