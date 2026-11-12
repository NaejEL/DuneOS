# Automatic merge of ship-pr under the `auto` policy (SM-01 to SM-08).
#
# - SM-01: five conditions, all evaluated and all reported (no short circuit):
#   C1 zero validated finding left (CONFIRMED not FIXED, UNVALIDATED), C2 every
#   review thread resolved (paginated), C3 CI green when the repository has one
#   (pending is polled every 30 s for `ship_pr.ci_wait_min` minutes, default 20),
#   C4 target not protected (API `protected` and `ship_pr.protected` of the
#   stamp; an API error is false), C5 no conflict (UNKNOWN reread 5 times, 3 s).
# - SM-02: the SP-05 refusal (uncovered theme, loop not finished) is evaluated
#   first and counts as a false condition: the PR stays open, no question.
# - SM-03: comment on the PR, posted BEFORE the merge, then the merge or not.
# - SM-04: the comment body is English, posted through `gh pr comment --body-file`,
#   and posted once: a relaunch finds `merge_comment` in the state.
# - SM-06: in a registered worktree the merge is `gh pr merge --merge` without
#   `--delete-branch`, then the remote branch is deleted by the core, and no
#   checkout of the target nor pull happens (WT-07 cleans up locally).

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import ghflow, runs, shippr
from .proc import Result

CONDITIONS = ("C1", "C2", "C3", "C4", "C5")
TITLES = {"C1": "no validated finding left", "C2": "all review threads resolved",
          "C3": "CI green (if any)", "C4": "target not protected", "C5": "no conflict"}
CI_POLL_S = 30
UNKNOWN_RETRIES = 5
UNKNOWN_DELAY_S = 3
DEFAULT_CI_WAIT_MIN = 20

THREADS_STATE_QUERY = (
    "query($owner:String!,$repo:String!,$number:Int!,$after:String){"
    " repository(owner:$owner,name:$repo){ pullRequest(number:$number){"
    " reviewThreads(first:100,after:$after){ pageInfo{ hasNextPage endCursor }"
    " nodes{ id isResolved } } } } }"
)


def _cond(ok: bool, detail: str) -> Dict[str, Any]:
    return {"ok": bool(ok), "detail": detail}


def c1_findings(state: Dict[str, Any]) -> Dict[str, Any]:
    """C1: no CONFIRMED finding left unfixed, no UNVALIDATED one (Q3); a `cap` exit is signalled (Q4)."""
    cands = state.get("candidates", {}).values()
    open_ = [c["id"] for c in cands if c["status"] == "CONFIRMED" and c.get("outcome") != "FIXED"]
    unvalidated = [c["id"] for c in cands if c["status"] == "UNVALIDATED"]
    if open_ or unvalidated:
        parts = []
        if open_:
            parts.append("confirmed and not fixed: " + ", ".join(open_))
        if unvalidated:
            parts.append("unvalidated: " + ", ".join(unvalidated))
        return _cond(False, "; ".join(parts))
    note = "; the round cap was reached" if state.get("exit") == "cap" else ""
    return _cond(True, "zero validated finding left%s" % note)


def c2_threads(run: ghflow.Run, state: Dict[str, Any]) -> Dict[str, Any]:
    """C2: every reviewThread of the PR, humans included, is resolved (pages of 100)."""
    owner, repo = state["owner_repo"].split("/", 1)
    open_ = 0
    total = 0
    after: Optional[str] = None
    for _ in range(1000):
        cmd = ["gh", "api", "graphql", "-f", "query=" + THREADS_STATE_QUERY, "-f", "owner=" + owner,
               "-f", "repo=" + repo, "-F", "number=%d" % state["pr"]]
        if after:
            cmd += ["-f", "after=" + after]
        res = run(cmd)
        if not res.ok:
            return _cond(False, "threads unreadable: %s" % (res.err or res.out).strip()[:200])
        try:
            threads = json.loads(res.out)["data"]["repository"]["pullRequest"]["reviewThreads"]
            nodes = threads["nodes"]
        except (ValueError, KeyError, TypeError):
            return _cond(False, "threads unreadable: unexpected response")
        total += len(nodes)
        open_ += sum(1 for n in nodes if not n.get("isResolved"))
        info = threads.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            break
        after = info.get("endCursor")
    return _cond(open_ == 0, "%d of %d threads unresolved" % (open_, total) if open_ else "%d threads, all resolved" % total)


def c3_ci(run: ghflow.Run, state: Dict[str, Any], wait_min: float, sleep: Callable[[float], None],
          clock: Callable[[], float]) -> Dict[str, Any]:
    """C3: `gh pr checks <n> --json name,bucket`; no check -> true ("no CI")."""
    deadline = clock() + wait_min * 60
    while True:
        res = run(["gh", "pr", "checks", str(state["pr"]), "--json", "name,bucket"])
        try:
            checks = json.loads(res.out) if res.out.strip() else []
        except ValueError:
            return _cond(False, "checks unreadable")
        if not isinstance(checks, list):
            return _cond(False, "checks unreadable")
        if not checks:
            if res.ok or "no checks" in (res.err + res.out).lower():
                return _cond(True, "no CI")
            return _cond(False, "checks unreadable: %s" % (res.err or "").strip()[:200])
        buckets = [str(c.get("bucket", "")).lower() for c in checks]
        bad = [c.get("name", "?") for c, b in zip(checks, buckets) if b in ("fail", "cancel")]
        if bad:
            return _cond(False, "failing checks: " + ", ".join(bad))
        pending = [c.get("name", "?") for c, b in zip(checks, buckets) if b not in ("pass", "skipping")]
        if not pending:
            return _cond(True, "%d checks pass or are skipped" % len(checks))
        if clock() >= deadline:
            return _cond(False, "checks still pending after %g min: %s" % (wait_min, ", ".join(pending)))
        sleep(CI_POLL_S)


def c4_protection(run: ghflow.Run, state: Dict[str, Any], protected: List[str]) -> Dict[str, Any]:
    """C4: `protected` of the API branches endpoint, and `ship_pr.protected` of the stamp."""
    target = state["target"]
    if target in protected:
        return _cond(False, "%s is listed in ship_pr.protected" % target)
    res = run(["gh", "api", "repos/%s/branches/%s" % (state["owner_repo"], target)])
    try:
        flag = json.loads(res.out)["protected"] if res.ok else None
    except (ValueError, KeyError, TypeError):
        flag = None
    if not isinstance(flag, bool):
        return _cond(False, "protection unknown")
    return _cond(not flag, "%s is protected" % target if flag else "%s is not protected" % target)


def c5_conflict(run: ghflow.Run, state: Dict[str, Any], sleep: Callable[[float], None]) -> Dict[str, Any]:
    """C5: `mergeable` is MERGEABLE; UNKNOWN is reread 5 times, 3 s apart."""
    value = "UNKNOWN"
    for attempt in range(UNKNOWN_RETRIES + 1):
        res = run(["gh", "pr", "view", str(state["pr"]), "--json", "mergeable"])
        try:
            value = json.loads(res.out).get("mergeable", "UNKNOWN") if res.ok else "UNKNOWN"
        except ValueError:
            value = "UNKNOWN"
        if value != "UNKNOWN" or attempt == UNKNOWN_RETRIES:
            break
        sleep(UNKNOWN_DELAY_S)
    return _cond(value == "MERGEABLE", "mergeable: %s" % value)


def evaluate(run: ghflow.Run, state: Dict[str, Any], protected: List[str], ci_wait_min: float = DEFAULT_CI_WAIT_MIN,
             sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> Dict[str, Any]:
    """SM-01 and SM-02: {merge, conditions}; every condition is evaluated."""
    conditions = {"C1": c1_findings(state), "C2": c2_threads(run, state),
                  "C3": c3_ci(run, state, ci_wait_min, sleep, clock),
                  "C4": c4_protection(run, state, protected), "C5": c5_conflict(run, state, sleep)}
    return {"merge": all(c["ok"] for c in conditions.values()), "conditions": conditions}


def comment_body(decision: Dict[str, Any]) -> str:
    """SM-03 and SM-04: the PR comment (English)."""
    conditions = decision["conditions"]
    refusal = decision.get("refusal")
    if decision["merge"]:
        lines = ["Automatic merge: all conditions met", ""]
        lines += ["- %s (%s): %s" % (k, TITLES[k], conditions[k]["detail"]) for k in CONDITIONS]
    else:
        lines = ["Automatic merge not performed:", ""]
        if refusal:
            lines.append("- review loop: %s" % refusal)
        lines += ["- %s (%s): %s" % (k, TITLES[k], conditions[k]["detail"])
                  for k in CONDITIONS if k in conditions and not conditions[k]["ok"]]
    return "\n".join(lines) + "\n"


def decide(run: ghflow.Run, state: Dict[str, Any], protected: List[str], ci_wait_min: float = DEFAULT_CI_WAIT_MIN,
           sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> Dict[str, Any]:
    refusal = shippr.merge_refusal(state)
    if refusal:
        return {"merge": False, "conditions": {}, "refusal": refusal}
    return evaluate(run, state, protected, ci_wait_min, sleep, clock)


def post_comment_once(run: ghflow.Run, root: Path, state: Dict[str, Any], decision: Dict[str, Any]) -> Dict[str, Any]:
    """SM-04: written to a file, posted by `gh pr comment --body-file`, recorded in the state."""
    done = state.get("merge_comment")
    if done:
        return dict(done, reposted=False)
    path = Path(root) / runs.RUNS_DIR / ("merge-comment-pr%s.md" % state["pr"])
    path.parent.mkdir(parents=True, exist_ok=True)
    runs.write_atomic(path, comment_body(decision))
    res = ghflow._must(run(["gh", "pr", "comment", str(state["pr"]), "--body-file", str(path)]), "gh pr comment")
    url = res.out.strip().splitlines()[-1] if res.out.strip() else None
    comment_id = url.rsplit("issuecomment-", 1)[1] if url and "issuecomment-" in url else None
    state["merge_comment"] = {"posted": True, "url": url, "id": comment_id, "merged": decision["merge"]}
    return dict(state["merge_comment"], reposted=True)


def merge_worktree(run: ghflow.Run, state: Dict[str, Any]) -> Dict[str, Any]:
    """SM-06: merge from a registered worktree, no checkout of the target, no pull."""
    ghflow._must(run(["gh", "pr", "merge", str(state["pr"]), "--merge"]), "gh pr merge")
    ghflow._must(run(["git", "push", "origin", "--delete", state["branch"]]), "git push origin --delete")
    state["merged"] = True
    return {"merged": True, "pr": state["pr"], "target": state["target"], "mode": "worktree",
            "remote_branch_deleted": state["branch"]}


def run_auto(run: ghflow.Run, root: Path, state: Dict[str, Any], protected: List[str], worktree: bool,
             ci_wait_min: float = DEFAULT_CI_WAIT_MIN, sleep: Callable[[float], None] = time.sleep,
             clock: Callable[[], float] = time.monotonic) -> Dict[str, Any]:
    """SM-02 and SM-03 as one step: decision, comment, then the merge when every condition holds."""
    if state.get("merged"):
        return {"merge": True, "merged": True, "conditions": {}, "already": True}
    decision = decide(run, state, protected, ci_wait_min, sleep, clock)
    comment = post_comment_once(run, root, state, decision)
    result = dict(decision, comment=comment, merged=False)
    if decision["merge"]:
        done = merge_worktree(run, state) if worktree else ghflow.merge(run, state, False)
        result.update(merged=True, mode=done.get("mode", "standard"))
    return result
