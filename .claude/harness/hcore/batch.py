# /factory-batch: several specs in parallel worktrees, one ship at a time (FB-01 to FB-08).
#
# - FB-02: the globs are expanded by the core and sorted; eligibility is the
#   one of the queue; the specs that are not eligible are reported.
# - FB-03: `--parallel` default 2, at most 4; a non-integer or < 1 is a usage error.
# - FB-04: N slots, each: worktree (WT-02), spec transfer (WT-05), then a child
#   `factory-run <spec> --deliver-only`. A spec whose dependency is in the batch
#   becomes eligible when that dependency is merged.
# - FB-05: a single ship at a time, in the FQ-05 order of the delivered specs. Before
#   each ship the local branch (never pushed) is rebased on origin/<target>. A
#   trivial conflict (regenerable artefacts of .knowledge/, Q11) is resolved; any
#   other one aborts the rebase, stops the launches, waits for the running
#   children, then stops with kind `conflict`. The ship is a child
#   `ship-pr <target> --spec <spec>` with cwd = the worktree.
# - FB-06: state `.harness/runs/factory-batch-<ts>.state.json`; `--resume <id>`.
# - FB-07: the queue rules FQ-07 (one coordinator) and FQ-09 (policy auto) apply.
# - FB-08: journal `factory-batch-<ts>.jsonl`.

from __future__ import annotations

import glob as globlib
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from . import chain, journal, queue as queue_mod, runs, stops, worktrees
from .queue import Run

DEFAULT_PARALLEL = 2
MAX_PARALLEL = 4
POLL_S = 0.1
FETCH_EVERY_S = 30.0
TRIVIAL = re.compile(r"^\.knowledge/(?!notes\.jsonl$|config\.json$)")


class BatchUsage(ValueError):
    """Invalid batch option (exit 2)."""


def parse_parallel(value: Optional[str]) -> int:
    """FB-03."""
    if value is None:
        return DEFAULT_PARALLEL
    try:
        n = int(str(value).strip())
    except ValueError:
        raise BatchUsage("--parallel expects an integer >= 1, got %r" % (value,))
    if n < 1:
        raise BatchUsage("--parallel expects an integer >= 1, got %r" % (value,))
    return min(n, MAX_PARALLEL)


def expand(root: Path, patterns: List[str]) -> List[str]:
    """FB-02: spec files named by the globs, relative to the root, sorted, without duplicates."""
    found: Set[str] = set()
    for pattern in patterns:
        for hit in globlib.glob(str(Path(root) / pattern), recursive=True):
            path = Path(hit)
            if path.is_file() and path.suffix == ".md":
                found.add(path.resolve().relative_to(Path(root).resolve()).as_posix())
    return sorted(found)


def rebase(run: Run, wt: str, target: str) -> Dict[str, Any]:
    """FB-05: fetch, then rebase the local branch on origin/<target>. Returns
    {result: clean|resolved|conflict, files}."""
    fetched = run(["git", "-C", wt, "fetch", "origin", target])
    if not fetched.ok:
        return {"result": "conflict", "files": [], "error": (fetched.err or fetched.out).strip()}
    res = run(["git", "-C", wt, "rebase", "origin/%s" % target])
    if res.ok:
        return {"result": "clean", "files": []}
    resolved: List[str] = []
    for _ in range(50):
        conflicted = run(["git", "-C", wt, "diff", "--name-only", "--diff-filter=U"]).out.split()
        if not conflicted or not all(TRIVIAL.match(f) for f in conflicted):
            run(["git", "-C", wt, "rebase", "--abort"])
            return {"result": "conflict", "files": conflicted}
        for name in conflicted:
            run(["git", "-C", wt, "checkout", "--ours", "--", name])     # during a rebase, ours = the target
            run(["git", "-C", wt, "add", "--", name])
            resolved.append(name)
        cont = run(["git", "-C", wt, "-c", "core.editor=true", "rebase", "--continue"])
        if cont.ok:
            return {"result": "resolved", "files": resolved}
    run(["git", "-C", wt, "rebase", "--abort"])
    return {"result": "conflict", "files": resolved}


class Batch:
    """The coordinator of one batch; threads are the slots and the single shipper."""

    def __init__(self, root: Path, target: str, parallel: int, run: Run, specs: List[str], policy: str):
        self.root, self.target, self.parallel, self.run, self.policy = Path(root), target, parallel, run, policy
        self.specs = specs
        self.lock = threading.Lock()
        self.git_lock = threading.Lock()
        self.status: Dict[str, str] = {s: "queued" for s in specs}
        self.info: Dict[str, Dict[str, Any]] = {s: {} for s in specs}
        self.error: Optional[stops.StopRequest] = None
        self.journal_path: Optional[Path] = None
        self.state_path: Optional[Path] = None
        self.threads: List[threading.Thread] = []
        self.clock = time.monotonic
        self.last_fetch: Optional[float] = None

    def fetch_due(self) -> bool:
        """At most one `git fetch` every FETCH_EVERY_S seconds for the scheduling scans."""
        now = self.clock()
        if self.last_fetch is None or now - self.last_fetch >= FETCH_EVERY_S:
            self.last_fetch = now
            return True
        return False

    # -- state

    def save(self) -> None:
        state = runs.load_state(self.journal_path)
        state.update(specs=self.status, info=self.info, target=self.target, parallel=self.parallel)
        runs.save_state(self.journal_path, state)

    def set(self, spec: str, status: str, **info: Any) -> None:
        with self.lock:
            self.status[spec] = status
            self.info[spec].update(info)
            self.save()

    def fail(self, exc: stops.StopRequest) -> None:
        with self.lock:
            if self.error is None:
                self.error = exc

    def log(self, event: Dict[str, Any]) -> None:
        with self.lock:
            journal.append(self.journal_path, event)

    # -- children

    def child(self, skill: str, args: str, cwd: Path, spec: str) -> Dict[str, Any]:
        try:
            card = chain.spawn(self.root, skill, args, runs.display(self.journal_path), self.policy, cwd=cwd)
        except chain.ChainStop as exc:
            raise stops.StopRequest(exc.kind, str(exc))
        result = queue_mod.wait_child(self.root, card["id"])
        problem = chain.failure(result)
        self.log({"event": "chain", "id": card["id"], "skill": skill, "args": args,
                  "parent_run": runs.display(self.journal_path), "result": "failed" if problem else "done"})
        if problem:
            raise stops.StopRequest("child", "%s (%s)" % (problem, spec),
                                    {"child_stop": (result["stops"] or [None])[-1],
                                     "child_command": "/%s %s" % (skill, args)})
        return result

    def build(self, spec: str) -> None:
        try:
            slug = chain.slug_of(spec)
            with self.git_lock:
                prior = self.info[spec].get("worktree")
                if prior and Path(prior).is_dir():           # resume: the worktree of the previous attempt is reused
                    entry = {"path": prior, "branch": self.info[spec].get("branch")}
                else:
                    entry = worktrees.create(self.run, self.root, slug, self.target, spec, "factory-batch",
                                             runs.display(self.journal_path))
                moved = worktrees.transfer_spec(self.run, self.root, Path(entry["path"]), spec, self.target)
            self.set(spec, "building", worktree=entry["path"], branch=entry["branch"], transfer=moved)
            self.child("factory-run", "%s --deliver-only --autonomy %s" % (spec, self.policy),
                       Path(entry["path"]), spec)
            self.set(spec, "delivered")
        except worktrees.BaseMissing as exc:
            self.set(spec, "failed")
            self.fail(stops.StopRequest("gate", str(exc)))
        except (stops.StopRequest, worktrees.WorktreeError) as exc:
            self.set(spec, "failed")
            self.fail(exc if isinstance(exc, stops.StopRequest) else stops.StopRequest("child", str(exc)))
        except Exception as exc:   # noqa: BLE001 - a dying thread would leave the spec `building` and the batch green
            self.set(spec, "failed")
            self.fail(stops.StopRequest("child", "unexpected error while building %s: %s: %s"
                                        % (spec, type(exc).__name__, exc)))

    def ship(self, spec: str) -> None:
        wt = self.info[spec]["worktree"]
        try:
            outcome = rebase(self.run, wt, self.target)
            if outcome["result"] == "conflict":
                self.set(spec, "conflict")
                self.fail(stops.StopRequest("conflict", "rebase of %s on origin/%s conflicts" % (spec, self.target),
                                            {"items": outcome["files"], "worktree": wt}))
                return
            self.child("ship-pr", "%s --spec %s" % (self.target, spec), Path(wt), spec)
            self.run(["git", "fetch", "origin", self.target])
            merged = spec_name(spec) in queue_mod.merged_names(self.run, self.target, [spec_name(spec)])
            if merged:
                info = worktrees.remove(self.run, self.root, wt, merged=True)
                self.set(spec, "merged", cleanup=info)
            else:
                self.set(spec, "open")
        except stops.StopRequest as exc:
            self.set(spec, "failed")
            self.fail(exc)
        except Exception as exc:   # noqa: BLE001 - see build()
            self.set(spec, "failed")
            self.fail(stops.StopRequest("child", "unexpected error while shipping %s: %s: %s"
                                        % (spec, type(exc).__name__, exc)))

    # -- scheduler

    def alive(self) -> bool:
        self.threads = [t for t in self.threads if t.is_alive()]
        return bool(self.threads)

    def spawn_thread(self, target, *args) -> None:
        t = threading.Thread(target=target, args=args, daemon=True)
        self.threads.append(t)
        t.start()

    def schedule(self) -> None:
        shipping = False
        while True:
            building = [s for s, v in self.status.items() if v == "building"]
            shipping = any(v == "shipping" for v in self.status.values())
            if self.error is None:
                listing = queue_mod.scan(self.run, self.root, self.target, fetch=self.fetch_due())
                rows = {r["file"]: r for r in listing["specs"]}
                inside = {Path(s).name for s in self.specs}
                # delivered specs go first: the ship runs alone, in queue order
                if not shipping:
                    delivered = [Path(s).name for s in [r for r in self.specs if self.status[r] == "delivered"]]
                    for row in listing["specs"]:
                        if row["file"] in delivered:
                            spec = next(s for s in self.specs if Path(s).name == row["file"])
                            self.set(spec, "shipping")       # claimed before the thread starts
                            self.spawn_thread(self.ship, spec)
                            shipping = True
                            break
                free = self.parallel - len(building)
                for row in listing["specs"]:
                    if free <= 0:
                        break
                    spec = next((s for s in self.specs if Path(s).name == row["file"]), None)
                    if spec is None or self.status[spec] != "queued":
                        continue
                    waiting = [d for d in row["unsatisfied"] if d in inside and self.status.get(
                        next(s for s in self.specs if Path(s).name == d)) not in ("merged",)]
                    blocked_by_outside = [d for d in row["unsatisfied"] if d not in inside]
                    if row["status"] not in (queue_mod.PROPOSED, queue_mod.READY) or row["errors"] or waiting \
                            or blocked_by_outside:
                        continue
                    self.status[spec] = "building"       # claimed before the thread starts
                    self.spawn_thread(self.build, spec)
                    free -= 1
            if not self.alive():
                pending = [s for s, v in self.status.items() if v in ("queued", "delivered")]
                if self.error is not None or not pending or not self._progress_possible():
                    return
            time.sleep(POLL_S)

    def _progress_possible(self) -> bool:
        """False when what is left can never start (unsatisfied dependencies, errors)."""
        return any(v == "delivered" for v in self.status.values()) or any(
            v == "queued" and self._startable(s) for s, v in self.status.items())

    def _startable(self, spec: str) -> bool:
        listing = queue_mod.scan(self.run, self.root, self.target, fetch=False)
        row = next((r for r in listing["specs"] if r["file"] == Path(spec).name), None)
        if row is None or row["errors"] or row["status"] not in (queue_mod.PROPOSED, queue_mod.READY):
            return False
        inside = {Path(s).name: s for s in self.specs}
        return all(d in inside and self.status[inside[d]] != "failed" for d in row["unsatisfied"])


def spec_name(spec: str) -> str:
    return Path(spec).name


def run_batch(root: Path, patterns: List[str], parallel: int, target: str, policy: str, run: Run,
              resume: Optional[str] = None, on_run=None) -> Dict[str, Any]:
    """FB-04 to FB-08. Raises StopRequest (kind gate, child or conflict) after the children in progress ended."""
    root = Path(root)
    lock = queue_mod.coordinator_lock(run, root)
    with runs.locked_file(lock, 0, "the coordinator of this repository"):
        queue_mod.preflight(run, root, target, policy)
        if resume:
            journal_path = root / runs.RUNS_DIR / ("factory-batch-%s.jsonl" % resume)
            if not runs.state_path(journal_path).exists():
                raise BatchUsage("unknown batch: %s" % resume)
            saved = runs.load_state(journal_path)
            specs = [s for s, v in saved["specs"].items() if v not in ("merged", "open")]
            for s in specs:                                   # WT-05: the spec parked in handoff/ comes back
                worktrees.restore_handoff(root, s)
        else:
            specs = expand(root, patterns)
            journal_path = Path(runs.create(root, "factory-batch", {"target": target, "policy": policy}))
        if on_run:
            on_run(journal_path)
        batch = Batch(root, target, parallel, run, specs, policy)
        batch.journal_path = journal_path
        if resume:
            for s, v in saved["specs"].items():
                if s in batch.status:
                    batch.status[s] = {"building": "queued", "shipping": "delivered", "failed": "queued",
                                       "conflict": "delivered"}.get(v, v)
                    batch.info[s] = saved.get("info", {}).get(s, {})
        journal.append(journal_path, {"event": "autonomy", "value": policy, "source": "flag"})
        listing = queue_mod.scan(run, root, target)
        batch.last_fetch = batch.clock()
        eligible_now = {r["file"] for r in listing["specs"] if queue_mod.eligible(r)}
        inside = {Path(s).name for s in specs}
        not_eligible = []
        for s in specs:
            row = next((r for r in listing["specs"] if r["file"] == Path(s).name), None)
            deps_ok = row is not None and not row["errors"] and row["status"] in (queue_mod.PROPOSED, queue_mod.READY) \
                and all(d in inside for d in row["unsatisfied"])
            if not deps_ok and Path(s).name not in eligible_now:
                batch.status[s] = "ineligible"
                not_eligible.append({"file": s, "status": row["status"] if row else "unknown",
                                     "reason": "; ".join(row["errors"]) if row and row["errors"] else
                                     "waits for " + ", ".join(row["unsatisfied"]) if row and row["unsatisfied"]
                                     else "status %s" % (row["status"] if row else "unknown")})
        batch.save()
        batch.schedule()
        while batch.alive():                  # FB-05: running children are awaited before the stop
            time.sleep(POLL_S)
        batch.save()
        if batch.error is not None:
            batch.error.run = journal_path    # type: ignore[attr-defined]
            batch.error.details.setdefault("batch_id", journal_path.name[len("factory-batch-"):-len(".jsonl")])
            raise batch.error
        return {"run": runs.display(journal_path), "batch_id": journal_path.name[len("factory-batch-"):-len(".jsonl")],
                "parallel": parallel, "status": dict(batch.status), "not_eligible": not_eligible}
