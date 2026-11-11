# The spec queue: derived statuses, dependency order, sequential run (FQ-01 to FQ-10).
#
# - FQ-02: `scan` gives one row per specs/*.md of the current tree and of the
#   registered worktrees: id, title, status, approved, dependencies (and the
#   unsatisfied ones), priority, worktree, last run, interrupted.
# - FQ-03: derived statuses: merged (delivered on origin/<target>), delivered
#   (delivered here or in a worktree), in progress (run `running` updated less than
#   2 h ago, older = `interrupted`), blocked (blocked with open questions),
#   proposed (proposed, a blocked spec whose questions are all answered, or an inactive ready one).
# - FQ-04: `Depends on` is resolved by file name or id prefix; an unknown
#   dependency or a cycle makes the specs concerned ineligible, with an error.
# - FQ-05: order = topological, then `Priority` (absent = last), then file name.
# - FQ-06: `next_spec` = first eligible spec: proposed, every dependency merged,
#   not attempted in this queue run.
# - FQ-07: one coordinator per repository (OS lock shared with the batch).
# - FQ-10: journal `.harness/runs/factory-queue-<ts>.jsonl`.

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from . import chain, journal, runs, specfile, stops, worktrees
from .proc import Result

Run = Callable[..., Result]
MERGED, DELIVERED, IN_PROGRESS, BLOCKED, PROPOSED, READY = (
    "mergée", "livrée", "en cours", "bloquée", "proposée", "proposée (réponses prêtes)")
STALE_SECONDS = 2 * 3600


def _read(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return specfile.parse(path.read_text(encoding="utf-8-sig", errors="replace"))
    except OSError:
        return None


def _title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def merged_names(run: Run, target: str, names: List[str]) -> Set[str]:
    """Specs whose copy on origin/<target> is delivered (`git show`, no checkout)."""
    out: Set[str] = set()
    for name in names:
        shown = run(["git", "show", "origin/%s:specs/%s" % (target, name)])
        if shown.ok and specfile.parse(shown.out)["role"] == "delivered":
            out.add(name)
    return out


def last_run(root: Path, rel: str, now: float) -> Optional[Dict[str, Any]]:
    """Latest run of the current tree that worked on `rel` (FQ-02), with the 2 h rule (FQ-03)."""
    folder = Path(root) / runs.RUNS_DIR
    best: Optional[Dict[str, Any]] = None
    for path in sorted(folder.glob("*.state.json")) if folder.is_dir() else []:
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if state.get("spec") != rel:
            continue
        age = now - path.stat().st_mtime
        best = {"run": path.name[: -len(".state.json")] + ".jsonl", "status": state.get("status"),
                "interrupted": state.get("status") == "running" and age > STALE_SECONDS,
                "active": state.get("status") == "running" and age <= STALE_SECONDS}
    return best


def scan(run: Run, root: Path, target: str, fetch: bool = True, now: Optional[float] = None) -> Dict[str, Any]:
    """FQ-02 to FQ-05: rows in queue order, plus warnings and errors."""
    root = Path(root)
    now = time.time() if now is None else now
    warnings: List[str] = []
    if fetch:
        res = run(["git", "fetch", "origin", target])
        if not res.ok:
            warnings.append("git fetch origin %s failed: the local reference is used" % target)
    rows: Dict[str, Dict[str, Any]] = {}

    def add(path: Path, worktree: Optional[str]) -> None:
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except FileNotFoundError:
            return          # removed meanwhile (a merged worktree cleaned up by a batch slot)
        parsed = specfile.parse(text)
        name = path.name
        if name in rows and rows[name]["worktree"] is None:
            return          # the current tree wins over a worktree copy
        rows[name] = {"file": name, "id": path.stem, "title": _title(text, path.stem), "word": parsed["word"], "role": parsed["role"],
                      "approved": parsed["role"] == "ready", "depends_on": parsed["depends_on"],
                      "priority": parsed["priority"], "unanswered": parsed["unanswered"],
                      "worktree": worktree, "errors": list(parsed["errors"])}

    folder = root / "specs"
    for path in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        add(path, None)
    try:
        for entry in worktrees.listing(run, root):
            wt_specs = Path(entry["path"]) / "specs"
            if entry.get("listed") and entry.get("state") == worktrees.STATE_ACTIVE and wt_specs.is_dir():
                for path in sorted(wt_specs.glob("*.md")):
                    add(path, entry["path"])
    except worktrees.WorktreeError:
        pass
    merged = merged_names(run, target, list(rows))
    for name, row in rows.items():
        run_info = last_run(root, "specs/" + name, now) if row["worktree"] is None else None
        row["last_run"] = run_info["run"] if run_info else None
        row["interrupted"] = bool(run_info and run_info["interrupted"])
        if name in merged:
            row["status"] = MERGED
        elif row["role"] == "delivered":
            row["status"] = DELIVERED
        elif run_info and run_info["active"]:
            row["status"] = IN_PROGRESS
        elif row["interrupted"]:
            row["status"] = IN_PROGRESS
        elif row["role"] == "blocked":
            row["status"] = BLOCKED if row["unanswered"] else READY
        else:
            row["status"] = PROPOSED
    errors = resolve_dependencies(rows)
    ordered = order(rows)
    for row in rows.values():
        row["unsatisfied"] = [d for d in row["resolved"] if rows[d]["status"] != MERGED]
    return {"specs": [rows[n] for n in ordered], "warnings": warnings, "errors": errors}


def resolve_dependencies(rows: Dict[str, Dict[str, Any]]) -> List[str]:
    """FQ-04: `resolved` file names per spec; unknown dependencies and cycles are errors."""
    errors: List[str] = []
    for name, row in rows.items():
        row["resolved"] = []
        for token in row["depends_on"]:
            hits = [n for n in rows if n == token or n == token + ".md" or n.startswith(token)]
            if not hits:
                errors.append("%s: unknown dependency %s" % (name, token))
                row["errors"].append("unknown dependency %s" % token)
            else:
                row["resolved"].append(sorted(hits)[0])
    state: Dict[str, int] = {}

    def visit(n: str, stack: List[str]) -> None:
        state[n] = 1
        for d in rows[n]["resolved"]:
            if state.get(d) == 1:
                members = stack[stack.index(d):] + [d] if d in stack else [n, d]
                for m in set(members):
                    if "dependency cycle" not in " ".join(rows[m]["errors"]):
                        rows[m]["errors"].append("dependency cycle: " + " -> ".join(members))
                errors.append("dependency cycle: " + " -> ".join(members))
            elif d not in state:
                visit(d, stack + [d])
        state[n] = 2

    for name in sorted(rows):
        if name not in state:
            visit(name, [name])
    return errors


def order(rows: Dict[str, Dict[str, Any]]) -> List[str]:
    """FQ-05: topological (dependencies first), ties by priority then name; a cycle's members come last."""
    pending = dict(rows)
    done: List[str] = []
    key = lambda n: (rows[n]["priority"] is None, rows[n]["priority"] or 0, n)   # noqa: E731
    while pending:
        ready = [n for n in pending if all(d in done or d not in pending for d in pending[n]["resolved"])]
        if not ready:
            done += sorted(pending, key=key)
            break
        pick = min(ready, key=key)
        done.append(pick)
        del pending[pick]
    return done


def eligible(row: Dict[str, Any]) -> bool:
    """FQ-06: proposed (or answers ready), every dependency merged, no error."""
    return row["status"] in (PROPOSED, READY) and not row["unsatisfied"] and not row["errors"]


def next_spec(listing: Dict[str, Any], attempted: Set[str]) -> Optional[Dict[str, Any]]:
    return next((r for r in listing["specs"] if r["file"] not in attempted and eligible(r)), None)


def leftovers(listing: Dict[str, Any], attempted: Set[str]) -> List[Dict[str, str]]:
    """FQ-06: what is left at the end of the queue, and why."""
    out = []
    for r in listing["specs"]:
        if r["status"] == MERGED:
            continue
        if r["errors"]:
            why = "; ".join(r["errors"])
        elif r["file"] in attempted:
            why = "attempted in this queue: not merged (PR left open or child stopped)"
        elif r["unsatisfied"]:
            why = "waits for " + ", ".join(r["unsatisfied"])
        else:
            why = "status %s" % r["status"]
        out.append({"file": r["file"], "status": r["status"], "reason": why})
    return out


# ------------------------------------------------------------------ queue run

def coordinator_lock(run: Run, cwd: Path) -> Path:
    """FQ-07: the OS lock file shared by the queue and the batch of one repository."""
    path = worktrees.common_dir(run, cwd) / "harness" / "coordinator.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def preflight(run: Run, root: Path, target: str, policy: str) -> None:
    """FQ-08 and FQ-09: auto policy, factory installed, clean tree, target checked out."""
    if policy != "auto":
        raise stops.StopRequest("gate", "requires --autonomy auto: the children cannot ask questions")
    if not (Path(root) / ".claude" / "harness" / "harness.py").is_file():
        raise stops.StopRequest("gate", "the software factory is not installed here: run /software-factory first")
    status = run(["git", "status", "--porcelain"])
    if not status.ok or status.out.strip():
        raise stops.StopRequest("gate", "the working tree is not clean", {"items": status.out.splitlines()[:10]})
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).out.strip()
    if branch != target:
        res = run(["git", "checkout", target])
        if not res.ok:
            raise stops.StopRequest("gate", "cannot switch cleanly to %s: %s" % (target, (res.err or res.out).strip()))


def wait_child(root: Path, chain_id: str, slice_s: float = 540) -> Dict[str, Any]:
    """The child is awaited slice by slice (the Bash tool limit), until it ends."""
    while True:
        result = chain.wait(root, chain_id, slice_s)
        if result["done"]:
            return result


def run_queue(root: Path, target: str, only: Optional[List[str]], policy: str, run: Run,
              on_run: Optional[Callable[[Path], None]] = None) -> Dict[str, Any]:
    """FQ-06: the sequential loop. A StopRequest is raised for the stops of the
    loop; `on_run` receives the journal path of the queue run as soon as it exists
    (the CLI writes the STOP file against it)."""
    root = Path(root)
    lock = coordinator_lock(run, root)
    with runs.locked_file(lock, 0, "the coordinator of this repository"):
        preflight(run, root, target, policy)
        journal_path = Path(runs.create(root, "factory-queue", {"target": target, "policy": policy}))
        if on_run:
            on_run(journal_path)
        journal.append(journal_path, {"event": "autonomy", "value": policy, "source": "flag"})
        attempted: Set[str] = set()
        outcomes: List[Dict[str, Any]] = []
        wanted = {Path(o).name for o in only or []}
        while True:
            listing = scan(run, root, target)
            skipped = {r["file"] for r in listing["specs"] if wanted and r["file"] not in wanted}
            row = next_spec(listing, attempted | skipped)
            if row is None:
                break
            attempted.add(row["file"])
            args = "specs/%s --ship %s" % (row["file"], target)
            try:
                card = chain.spawn(root, "factory-run", args, runs.display(journal_path), policy)
            except chain.ChainStop as exc:
                raise stops.StopRequest(exc.kind, str(exc))
            result = wait_child(root, card["id"])
            problem = chain.failure(result)
            journal.append(journal_path, {"event": "chain", "id": card["id"], "skill": "factory-run", "args": args,
                                          "parent_run": runs.display(journal_path),
                                          "result": "failed" if problem else "done"})
            if problem:
                raise stops.StopRequest("child", "%s (%s)" % (problem, row["file"]),
                                        {"child_stop": (result["stops"] or [None])[-1],
                                         "child_command": "/factory-run specs/%s" % row["file"]})
            if run(["git", "status", "--porcelain"]).out.strip():
                raise stops.StopRequest("gate", "the tree is dirty after the cycle of %s" % row["file"])
            for cmd in (["git", "checkout", target], ["git", "pull", "--ff-only"]):
                res = run(cmd)
                if not res.ok:
                    raise stops.StopRequest("gate", "%s failed: %s" % (" ".join(cmd), (res.err or res.out).strip()))
            outcomes.append({"file": row["file"], "chain": card["id"]})
        final = scan(run, root, target)
        return {"run": runs.display(journal_path), "attempted": sorted(attempted), "outcomes": outcomes,
                "left": leftovers(final, attempted)}
