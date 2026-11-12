# Cycle context of the hooks: activation markers, scopes, drifts (HK-05, HK-07, HP, HD, HR).
#
# - HK-05: `activate` writes `.harness/runs/active/<session_id>.json`, a stack
#   (the top applies); `deactivate` removes the entry at the end of a cycle. A
#   marker is stale when its run is no longer `running` or it is older than 12 h:
#   ignored, and removed by the next `activate`. A literal `${` instead of a
#   session id falls back to `active/project.json`, with a warning (Q17).
# - HK-07: paths are normalised: `\` and `/`, case ignored for Windows paths, `..`
#   resolved, relative to the root of the context.
# - HP-01 to HP-03: the perimeter of the spec (`Files scope`) during the
#   build; `.harness/runs/**`, the active spec and paths outside the root pass.
# - HD-01: during ship-pr only the files of `git diff --name-only origin/<target>...HEAD`.
# - HR-01: drifts are counted per run, under the run lock; the second one stops the cycle.
# - HG-02 (5): `deletable` decides whether a deletion target is generated
#   (`.harness/tmp/`, the agents' scratch folder, always is).

from __future__ import annotations

import fnmatch
import json
import posixpath
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import journal, runs, specfile, stops
from .proc import Runner

ACTIVE_DIR = runs.RUNS_DIR / "active"
STALE_SECONDS = 12 * 3600
CONTEXT_SKILLS = ("factory-run", "lab-run", "ship-pr")
ARTEFACT_DIRS = ("factory-logs", "lab-logs", "experiments/results", ".harness/runs", "__pycache__", "node_modules",
                 "dist", "bin", "obj", ".venv", ".pytest_cache", "build", "target")
SCRATCH_DIR = ".harness/tmp"
SECRET_PATTERNS =(".env", ".env.*", "*.pem", "*.key", "id_rsa*", "*.p12", "*.pfx")
SECRET_ALLOWED = (".env.example",)
WIN_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


class ScopeError(ValueError):
    """Invalid activation request (exit 2)."""


# --------------------------------------------------------------------- paths

def _windows_style(*values: str) -> bool:
    return any(WIN_DRIVE.match(v) for v in values)


def normalize(path: str, root: str) -> Optional[str]:
    """HK-07: `path` relative to `root`, with `/` separators and `..` resolved;
    None when it lies outside the root (the caller lets it pass)."""
    raw = path.replace("\\", "/")
    base = root.replace("\\", "/").rstrip("/")
    fold = _windows_style(path, root)
    if raw.startswith("/") or WIN_DRIVE.match(path):
        full = posixpath.normpath(raw)
    else:
        full = posixpath.normpath(base + "/" + raw)
    cmp_full, cmp_base = (full.lower(), base.lower()) if fold else (full, base)
    if cmp_full == cmp_base:
        return "."
    if not cmp_full.startswith(cmp_base + "/"):
        return None
    return full[len(base) + 1:]


def _glob_regex(pattern: str) -> "re.Pattern[str]":
    out, i = [], 0
    pattern = pattern.replace("\\", "/")
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        out.append("[^/]*" if c == "*" else "[^/]" if c == "?" else re.escape(c))
        i += 1
    return re.compile("^" + "".join(out) + "$", re.I if WIN_DRIVE.match(pattern) else 0)


def matches(pattern: str, rel: str, fold: bool = False) -> bool:
    regex = _glob_regex(pattern)
    if fold:
        regex = re.compile(regex.pattern, re.I)
    return bool(regex.match(rel)) or (pattern.endswith("/**") and rel == pattern[:-3])


def in_runs_dir(rel: str) -> bool:
    return rel == ".harness/runs" or rel.startswith(".harness/runs/")


def in_scratch_dir(rel: str) -> bool:
    """Agent scratch files live under `<root>/.harness/tmp/` (git-ignored): always deletable."""
    return rel == SCRATCH_DIR or rel.startswith(SCRATCH_DIR + "/")


# ------------------------------------------------------------------- markers

def marker_file(root: Path, session: Optional[str]) -> Path:
    return Path(root) / ACTIVE_DIR / ("%s.json" % (session or "project"))


def _read_marker(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def entry_is_stale(root: Path, entry: Dict[str, Any], now: float) -> bool:
    if now - float(entry.get("started", 0)) > STALE_SECONDS:
        return True
    try:
        state = runs.load_state(Path(root) / entry["run"])
    except (OSError, ValueError, FileNotFoundError, KeyError):
        return True
    return state.get("status") != "running"


def activate(root: Path, skill: str, run: str, session: Optional[str], phase: str = "build",
             spec: Optional[str] = None, target: Optional[str] = None, now: Optional[float] = None) -> Dict[str, Any]:
    """HK-05."""
    if skill not in CONTEXT_SKILLS:
        raise ScopeError("--skill must be one of %s" % ", ".join(CONTEXT_SKILLS))
    now = time.time() if now is None else now
    warnings: List[str] = []
    if not session or "${" in session:
        warnings.append("session id not substituted (%r): the marker is shared by the project (active/project.json)"
                        % (session or ""))
        session = None
    path = marker_file(root, session)
    path.parent.mkdir(parents=True, exist_ok=True)
    stack: List[Dict[str, Any]] = []
    if path.exists():
        try:
            stack = [e for e in _read_marker(path)["stack"] if not entry_is_stale(root, e, now)]
        except (OSError, ValueError, KeyError):
            stack = []
    stack.append({"skill": skill, "run": run, "phase": phase, "spec": spec, "target": target, "started": now})
    runs.write_atomic(path, json.dumps({"stack": stack}, ensure_ascii=False, indent=1))
    return {"marker": path.relative_to(root).as_posix(), "depth": len(stack), "warnings": warnings}


def deactivate(root: Path, run: str, session: Optional[str]) -> Dict[str, Any]:
    """HK-05: removes the entries of `run` (success, failure or stop)."""
    if not session or "${" in session:
        session = None
    path = marker_file(root, session)
    if not path.exists():
        return {"removed": 0, "depth": 0}
    stack = _read_marker(path)["stack"]
    kept = [e for e in stack if e.get("run") != run]
    if kept:
        runs.write_atomic(path, json.dumps({"stack": kept}, ensure_ascii=False, indent=1))
    else:
        path.unlink()
    return {"removed": len(stack) - len(kept), "depth": len(kept)}


def current(root: Path, session: Optional[str], now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """The context that applies to a session: the top of its stack, else the project's."""
    now = time.time() if now is None else now
    for path in ([marker_file(root, session)] if session else []) + [marker_file(root, None)]:
        if not path.exists():
            continue
        live = [e for e in _read_marker(path)["stack"] if not entry_is_stale(root, e, now)]
        if live:
            return live[-1]
    return None


# --------------------------------------------------------- scope and secrets

def secret_hit(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    return name not in SECRET_ALLOWED and any(fnmatch.fnmatchcase(name, p) for p in SECRET_PATTERNS)


def perimeter_of(root: Path, ctx: Dict[str, Any]) -> List[str]:
    if not ctx.get("spec"):
        return []
    try:
        return specfile.parse((Path(root) / ctx["spec"]).read_text(encoding="utf-8-sig", errors="replace"))["perimeter"]
    except OSError:
        return []


def build_violation(root: Path, ctx: Dict[str, Any], rel: str) -> Optional[str]:
    """HP-01 to HP-03: the English blocking message, or None when the path is in scope."""
    perimeter = perimeter_of(root, ctx)
    if not perimeter:
        return None
    fold = _windows_style(str(root))
    if in_runs_dir(rel) or rel == ctx.get("spec") or any(matches(g, rel, fold) for g in perimeter):
        return None
    return ("Blocked: %s is outside the scope declared by %s (Files scope: %s). Stay within the spec; "
            "if the spec is wrong, stop and report it." % (rel, ctx.get("spec"), ", ".join(perimeter)))


def pr_files(root: Path, target: str) -> List[str]:
    res = Runner(cwd=str(root), timeout=5)(["git", "diff", "--name-only", "origin/%s...HEAD" % target])
    if not res.ok:
        raise RuntimeError("git diff failed: %s" % (res.err or res.out).strip()[:200])
    return [ln.strip() for ln in res.out.splitlines() if ln.strip()]


def diff_violation(root: Path, ctx: Dict[str, Any], rel: str) -> Optional[str]:
    """HD-01."""
    if in_runs_dir(rel):
        return None
    target = ctx.get("target") or "main"
    if rel in pr_files(root, target):
        return None
    return ("Blocked: scope limited to the PR diff — %s is not in git diff --name-only origin/%s...HEAD."
            % (rel, target))


# ------------------------------------------------------------------ deletion

def deletable_factory(root: Path, ctx: Optional[Dict[str, Any]]) -> Callable[[str], bool]:
    """HG-02 (5): is a deletion target generated? Artefact folder, created during
    the active cycle (untracked and not in the snapshot of `start`), or in the perimeter."""
    root = Path(root)
    perimeter = perimeter_of(root, ctx) if ctx else []
    snapshot: List[str] = []
    if ctx:
        try:
            snapshot = runs.load_state(root / ctx["run"]).get("untracked_at_start", [])
        except (OSError, ValueError, FileNotFoundError, KeyError):
            snapshot = []

    def check(target: str) -> bool:
        rel = normalize(target, str(root))
        if rel is None or rel == ".":
            return False
        if in_scratch_dir(rel):
            return True
        if any(("/" + d + "/") in ("/" + rel + "/") for d in ARTEFACT_DIRS):
            return True
        if ctx is None:
            return False
        if any(matches(g, rel) for g in perimeter):
            return True
        tracked = Runner(cwd=str(root), timeout=5)(["git", "ls-files", "--", rel])
        if not tracked.ok or tracked.out.strip():
            return False
        return not any(rel == s or rel.startswith(s.rstrip("/") + "/") or s.startswith(rel + "/") for s in snapshot)

    return check


def untracked_snapshot(root: Path, limit: int = 5000) -> List[str]:
    """The untracked files of the tree, taken by `factory-run start` (HG-02 5)."""
    res = Runner(cwd=str(root), timeout=30)(["git", "status", "--porcelain", "-uall"])
    if not res.ok:
        return []
    return [ln[3:].strip().strip('"') for ln in res.out.splitlines() if ln.startswith("??")][:limit]


# --------------------------------------------------------------------- drift

def record_drift(root: Path, ctx: Dict[str, Any], rel: str, kind: str = "drift") -> Dict[str, Any]:
    """HR-01: counts the drift under the run lock, journals it, stops the cycle at the second one."""
    run = Path(root) / ctx["run"]
    with runs.locked(run):
        state = runs.load_state(run)
        state["drift_count"] = int(state.get("drift_count", 0)) + 1
        count = state["drift_count"]
        runs.save_state(run, state)
        journal.append(run, {"event": "drift", "path": rel, "count": count})
        stop_path = None
        if count >= 2:
            stop_path = stops.write(root, state.get("skill", ctx["skill"]), kind,
                                    {"reason": "Second scope drift in this cycle: %s" % rel,
                                     "items": [rel], "spec": ctx.get("spec"), "phase": ctx.get("phase")}, run)
    return {"count": count, "stop": stops.rel(root, stop_path) if stop_path else None}
