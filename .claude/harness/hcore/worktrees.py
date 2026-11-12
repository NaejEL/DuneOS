# Git worktrees of the harness (WT-01 to WT-09).
#
# - WT-01: registry `<git-common-dir>/harness/worktrees.json`, shared by every
#   worktree, never versioned: [{path, branch, base, spec, created_by, batch_id,
#   created_at, state}], written atomically under a lock.
# - WT-02: creation at `<parent of the main worktree>/<repo>.harness-wt/<slug>`,
#   branch `factory/<slug>` from `origin/<target>` after a fetch; an existing
#   path or branch is never reused (timestamp suffix); `git worktree add -b`.
# - WT-03: every operation requires the path to be in the registry AND listed
#   by `git worktree list --porcelain` with the registered branch, else nothing.
# - WT-04: never `prune`, `remove --force`, `move`, `lock`, `unlock`, and never a
#   command in a worktree that is not registered. The main worktree and those of
#   the user are never touched.
# - WT-05: a spec that differs from `origin/<target>` (or is not there) is copied
#   into the worktree; the original goes to `.harness/runs/handoff/<ts>-<name>`.
# - WT-06: before removal the runs of the worktree are copied under
#   `<main>/.harness/runs/wt-<slug>/` and its findings registry is merged.
# - WT-07: cleanup after a merge: collect, `git worktree remove` (not forced; a
#   dirty worktree is kept and reported), delete the local branch, unregister.
# - WT-09: `check_base` requires .claude/harness/ and .claude/settings.json on the base.

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import registry as registry_mod, runs
from .proc import Result

Run = Callable[..., Result]
STATE_ACTIVE, STATE_MERGED = "active", "merged"
BASE_FILES = (".claude/harness/harness.py", ".claude/settings.json")


class WorktreeError(RuntimeError):
    """A refused worktree operation (exit 1): nothing was done."""


class BaseMissing(WorktreeError):
    """WT-09: the harness files are not on the base (stop kind gate)."""


def _norm(path: Any) -> str:
    return os.path.normcase(os.path.normpath(str(Path(path).resolve())))


def common_dir(run: Run, cwd: Path) -> Path:
    res = run(["git", "rev-parse", "--git-common-dir"])
    if not res.ok:
        raise WorktreeError("not a git repository: %s" % (res.err or res.out).strip())
    path = Path(res.out.strip())
    return (path if path.is_absolute() else Path(cwd) / path).resolve()


def main_root(run: Run, cwd: Path) -> Path:
    """The main worktree: the parent of the common git directory."""
    return common_dir(run, cwd).parent


def registry_file(common: Path) -> Path:
    return Path(common) / "harness" / "worktrees.json"


def load(common: Path) -> List[Dict[str, Any]]:
    path = registry_file(common)
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _update(common: Path, edit: Callable[[List[Dict[str, Any]]], Any]) -> Any:
    """WT-01: read -> edit -> atomic write, under the registry lock."""
    path = registry_file(common)
    path.parent.mkdir(parents=True, exist_ok=True)
    with runs.locked_file(path.with_name("worktrees.lock")):
        entries = load(common)
        result = edit(entries)
        runs.write_atomic(path, json.dumps(entries, ensure_ascii=False, indent=1))
    return result


def listed(run: Run) -> List[Dict[str, Optional[str]]]:
    """`git worktree list --porcelain` -> [{path, branch}]."""
    res = run(["git", "worktree", "list", "--porcelain"])
    if not res.ok:
        raise WorktreeError("git worktree list failed: %s" % (res.err or res.out).strip())
    out: List[Dict[str, Optional[str]]] = []
    for line in res.out.splitlines():
        if line.startswith("worktree "):
            out.append({"path": line[len("worktree "):].strip(), "branch": None})
        elif line.startswith("branch ") and out:
            ref = line[len("branch "):].strip()
            out[-1]["branch"] = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
    return out


def entry_of(run: Run, cwd: Path, path: Any) -> Optional[Dict[str, Any]]:
    """WT-03: the registry entry of `path`, only if git lists it with the registered branch."""
    common = common_dir(run, cwd)
    wanted = _norm(path)
    entry = next((e for e in load(common) if _norm(e["path"]) == wanted), None)
    if entry is None:
        return None
    for item in listed(run):
        if _norm(item["path"]) == wanted and item["branch"] == entry["branch"]:
            return entry
    return None


def require(run: Run, cwd: Path, path: Any) -> Dict[str, Any]:
    entry = entry_of(run, cwd, path)
    if entry is None:
        raise WorktreeError("%s is not a worktree created by the harness (registry and `git worktree list` "
                            "must both know it with the registered branch): nothing was done" % path)
    return entry


def check_base(run: Run, target: str) -> None:
    """WT-09: the harness files must exist on origin/<target>."""
    missing = [f for f in BASE_FILES if not run(["git", "cat-file", "-e", "origin/%s:%s" % (target, f)]).ok]
    if missing:
        raise BaseMissing("commit the harness files first: %s missing on origin/%s" % (", ".join(missing), target))


def create(run: Run, cwd: Path, slug: str, target: str, spec: Optional[str], created_by: str,
           batch_id: Optional[str] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """WT-02."""
    common = common_dir(run, cwd)
    main = common.parent
    fetched = run(["git", "fetch", "origin", target])
    if not fetched.ok:
        raise WorktreeError("git fetch origin %s failed: %s" % (target, (fetched.err or fetched.out).strip()))
    check_base(run, target)
    base_dir = main.parent / ("%s.harness-wt" % main.name)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    name = slug
    branch = "factory/%s" % slug
    if (base_dir / name).exists() or run(["git", "rev-parse", "--verify", "--quiet", "refs/heads/" + branch]).ok:
        name = "%s-%s" % (slug, stamp)
        branch = "factory/%s" % name
    path = base_dir / name
    base_dir.mkdir(parents=True, exist_ok=True)
    res = run(["git", "worktree", "add", "-b", branch, str(path), "origin/%s" % target])
    if not res.ok:
        raise WorktreeError("git worktree add failed: %s" % (res.err or res.out).strip())
    entry = {"path": str(path), "branch": branch, "base": "origin/%s" % target, "spec": spec,
             "created_by": created_by, "batch_id": batch_id,
             "created_at": (now or datetime.now()).isoformat(timespec="seconds"), "state": STATE_ACTIVE}
    _update(common, lambda entries: entries.append(entry))
    return entry


def transfer_spec(run: Run, main: Path, wt: Path, spec: str, target: str,
                  now: Optional[datetime] = None) -> Dict[str, Any]:
    """WT-05: copy the spec into the worktree when it differs from the base (or is absent there)."""
    source = Path(main) / spec
    if not source.is_file():
        raise WorktreeError("spec not found: %s" % spec)
    shown = run(["git", "show", "origin/%s:%s" % (target, Path(spec).as_posix())])
    local = source.read_bytes()
    if shown.ok and shown.out.encode("utf-8") == local.replace(b"\r\n", b"\n"):
        return {"transferred": False}
    if shown.ok and shown.out.replace("\r\n", "\n") == local.decode("utf-8", "replace").replace("\r\n", "\n"):
        return {"transferred": False}
    dest = Path(wt) / spec
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(local)
    handoff = Path(main) / runs.RUNS_DIR / "handoff"
    handoff.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    moved = handoff / ("%s-%s" % (stamp, source.name))
    shutil.move(str(source), str(moved))
    return {"transferred": True, "copied_to": str(dest), "original_moved_to": str(moved)}


def restore_handoff(main: Path, spec: str) -> bool:
    """WT-05 on resume: when the spec is gone from the main tree, copy back the latest one parked in
    .harness/runs/handoff/ (the original is kept there). True when a copy was restored."""
    dest = Path(main) / spec
    if dest.exists():
        return False
    parked = sorted((Path(main) / runs.RUNS_DIR / "handoff").glob("*-%s" % Path(spec).name))
    if not parked:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(str(parked[-1]), str(dest))
    return True


def collect(main: Path, wt: Path, slug: str) -> Dict[str, Any]:
    """WT-06: runs copied under wt-<slug>/, findings registry merged (idempotent)."""
    dest = Path(main) / runs.RUNS_DIR / ("wt-%s" % slug)
    copied = 0
    source = Path(wt) / runs.RUNS_DIR
    if source.is_dir():
        shutil.copytree(str(source), str(dest), dirs_exist_ok=True)
        copied = sum(1 for p in dest.rglob("*") if p.is_file())
    merged = 0
    theirs = registry_mod.ProjectRegistry(Path(wt))
    if theirs.path.exists():
        ours = registry_mod.ProjectRegistry(Path(main))
        ours.path.parent.mkdir(parents=True, exist_ok=True)
        with runs.locked(ours.path):
            entries = ours.read()
            incoming = theirs.read()
            merged = sum(1 for k, v in incoming.items() if entries.get(k) != v)
            entries.update(incoming)
            runs.write_atomic(ours.path, ours.dumps(entries))
    return {"files": copied, "findings_merged": merged, "into": str(dest)}


def remove(run: Run, cwd: Path, path: Any, merged: bool) -> Dict[str, Any]:
    """WT-07 (and the `worktree remove` command): refused outside the registry (WT-03)."""
    entry = require(run, cwd, path)
    common = common_dir(run, cwd)
    main = common.parent
    slug = Path(entry["path"]).name
    info = collect(main, Path(entry["path"]), slug)
    if not merged:
        return {"removed": False, "kept": entry["path"], "reason": "not merged: worktree kept", "collected": info,
                "resume": "cd %s" % entry["path"]}
    if dirty_files(run, entry["path"]):
        return {"removed": False, "kept": entry["path"], "reason": "the worktree is dirty: kept", "collected": info}
    res = run(["git", "worktree", "remove", entry["path"]])
    if not res.ok:
        return {"removed": False, "kept": entry["path"], "reason": (res.err or res.out).strip(), "collected": info}
    run(["git", "branch", "-D", entry["branch"]])
    _update(common, lambda entries: _drop(entries, entry["path"]))
    return {"removed": True, "branch_deleted": entry["branch"], "collected": info}


def _drop(entries: List[Dict[str, Any]], path: Any) -> None:
    entries[:] = [e for e in entries if _norm(e["path"]) != _norm(path)]


def dirty_files(run: Run, path: str) -> str:
    """`git status --porcelain` of a worktree (`-C`, so the same runner serves every worktree)."""
    res = run(["git", "-C", str(path), "status", "--porcelain"])
    return res.out.strip() if res.ok else "unknown"


def listing(run: Run, cwd: Path) -> List[Dict[str, Any]]:
    """The registered worktrees, each marked `listed` when git agrees (WT-03)."""
    common = common_dir(run, cwd)
    known = {_norm(i["path"]): i["branch"] for i in listed(run)}
    return [dict(e, listed=known.get(_norm(e["path"])) == e["branch"]) for e in load(common)]
