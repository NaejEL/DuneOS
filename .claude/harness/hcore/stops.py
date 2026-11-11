# STOP files: the explicit stops of the loop (SX-01 to SX-07).
#
# - SX-01: the closed list of stop kinds.
# - SX-02: .harness/runs/STOP-<YYYYMMDD-HHMMSS>.md, one second added on a name
#   collision (as in runs.create).
# - SX-03: the format, English, 40 lines at most: title, Where, Why (5 lines of
#   reason then 10 items at most), To resume (5 steps at most), Command (one block).
# - SX-04: the resume command of each kind, rendered here.
# - SX-05: a stop yields the file, a `stop` journal event and an exit 1 whose
#   stderr line is `STOP: <reason> (see <path>)`.
# - SX-06: once a run is stopped (state key `stopped`), every later command of
#   that run is refused with exit 1 citing the STOP (see `refuse_if_stopped`).
# - SX-07: the files live under .harness/runs/ (excluded from git); never deleted.

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import journal, runs

KINDS = ("destructive", "iterations", "agent", "blocked", "profile", "drift", "cost", "secret", "gate",
         "conflict", "child")
MAX_LINES = 40
MAX_REASON_LINES = 5
MAX_ITEMS = 10
MAX_STEPS = 5
TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "stop.md"

NONE = "(none)"


class StopError(ValueError):
    """Invalid stop request (exit 2)."""


class StopRequest(RuntimeError):
    """A loop asks for a stop of `kind`: the CLI writes the STOP file (SX-02) and exits 1."""

    def __init__(self, kind: str, reason: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(reason)
        self.kind = kind
        self.details = dict(details or {}, reason=reason)


class Stopped(RuntimeError):
    """The run was stopped earlier: every later command is refused (exit 1)."""


# SX-04: kind -> (resume steps, command pattern).
RESUME = {
    "blocked": (["Fill the `Answer:` line of every open question in the spec.", "Run the command below."],
                "/factory-run {spec}"),
    "iterations": (["Read the remaining issues above.", "Run the command below to start a new cycle."],
                   "/factory-run {spec}"),
    "agent": (["The agent died twice: check the connection and the quota.", "Run the command below."],
              "/factory-run {spec}"),
    "profile": (["Pick the profile that fits the project.", "Run the command below."],
                "/software-factory --profile {profile}"),
    "drift": (["Fix `Files scope` in the spec, or the implementation.", "Run the command below."],
              "/factory-run {spec}"),
    "destructive": (["No agent command resumes this stop.",
                     "Run the destructive operation yourself in a terminal if it is wanted.",
                     "Then run the command of the cycle again."],
                    "(run the operation yourself, then relaunch the cycle command)"),
    "cost": (["A billing variable is set: the child would not use the subscription.",
              "Unset the variable, or set `chain.allow_api_billing: true` in .claude/harness.json.",
              "Run the command again."],
             "(relaunch without the billing variable, or set chain.allow_api_billing: true)"),
    "gate": (["Provide the missing value, or switch to the gate policy.", "Run the command again."],
             "(relaunch with the missing value, or with --autonomy gate)"),
    "conflict": (["Resolve the conflict in the worktree.", "Resume the batch."],
                 "/factory-batch --resume {batch_id}"),
    "child": (["Read the STOP of the child, and fix its cause.", "Run the child command again."],
              "{child_command}"),
    "secret": (["The cycle tried to edit a secret file.", "Edit it yourself if it is wanted.",
                "Run the command of the cycle again."],
               "(edit the file yourself, then relaunch the cycle command)"),
}


def resume_command(kind: str, details: Dict[str, Any]) -> str:
    """SX-04: the exact command that resumes after a stop of this kind."""
    if details.get("command"):
        return str(details["command"])
    pattern = RESUME[kind][1]
    values = {"spec": details.get("spec") or "<spec>", "profile": details.get("profile") or "<profile>",
              "batch_id": details.get("batch_id") or "<id>",
              "child_command": details.get("child_command") or details.get("child_stop") or "<child command>"}
    return pattern.format(**values)


def _clip(text: str, limit: int) -> List[str]:
    lines = [ln.rstrip() for ln in str(text).strip().splitlines() if ln.strip()]
    return lines[:limit] if lines else [NONE]


def render(skill: str, kind: str, details: Dict[str, Any], where: Dict[str, Any]) -> str:
    """SX-03: the text of a STOP file (pure, deterministic)."""
    if kind not in KINDS:
        raise StopError("unknown stop kind: %r (known: %s)" % (kind, ", ".join(KINDS)))
    why = _clip(details.get("reason") or "", MAX_REASON_LINES)
    items = [str(i).strip().replace("\n", " ") for i in details.get("items") or [] if str(i).strip()]
    why += ["- " + i for i in items[:MAX_ITEMS]]
    steps = list(details.get("steps") or RESUME[kind][0])[:MAX_STEPS]
    values = {
        "skill": skill, "kind": kind,
        "phase": where.get("phase") or NONE, "run": where.get("run") or NONE,
        "spec": where.get("spec") or details.get("spec") or NONE,
        "branch": where.get("branch") or NONE, "worktree": where.get("worktree") or NONE,
        "why": "\n".join(why),
        "steps": "\n".join("%d. %s" % (n, s) for n, s in enumerate(steps, 1)),
        "command": resume_command(kind, details),
    }
    text = TEMPLATE.read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{{%s}}" % key, str(value))
    if len(text.splitlines()) > MAX_LINES:
        raise StopError("a STOP file is %d lines at most" % MAX_LINES)
    return text


def _git_branch(root: Path) -> Optional[str]:
    from .proc import Runner
    res = Runner(cwd=str(root), timeout=10)(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    return res.out.strip() if res.ok and res.out.strip() else None


def new_path(root: Path, now: Optional[datetime] = None) -> Path:
    folder = Path(root) / runs.RUNS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    moment = now or datetime.now()
    path = folder / ("STOP-%s.md" % moment.strftime("%Y%m%d-%H%M%S"))
    while path.exists():
        moment += timedelta(seconds=1)
        path = folder / ("STOP-%s.md" % moment.strftime("%Y%m%d-%H%M%S"))
    return path


def write(root: Path, skill: str, kind: str, details: Dict[str, Any], run: Optional[Path] = None,
          now: Optional[datetime] = None, branch: Optional[str] = None) -> Path:
    """SX-02 and SX-05: the STOP file, the `stop` journal event and the `stopped`
    mark of the run state. The caller holds the run lock when it passes a run."""
    root = Path(root)
    where = {"phase": details.get("phase"), "run": rel(root, run) if run else details.get("run"),
             "spec": details.get("spec"), "branch": branch or _git_branch(root), "worktree": str(root.resolve())}
    text = render(skill, kind, details, where)
    path = new_path(root, now)
    runs.write_atomic(path, text)
    if run is not None:
        journal.append(Path(run), {"event": "stop", "kind": kind, "path": rel(root, path)})
        state = runs.load_state(Path(run))
        state["stopped"] = {"kind": kind, "path": rel(root, path), "reason": _clip(details.get("reason") or "", 1)[0]}
        if state.get("status") == "running":
            state["status"] = "stopped"        # the activation marker of the run becomes stale (HK-05)
        runs.save_state(Path(run), state)
    return path


def rel(root: Path, path: Path) -> str:
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return Path(path).as_posix()


def message(reason: str, path: str) -> str:
    """SX-05: the stderr line of a stop."""
    first = _clip(reason, 1)[0]
    return "%s (see %s)" % (first, path)


def refuse_if_stopped(state: Dict[str, Any]) -> None:
    """SX-06: raises Stopped when the run was stopped."""
    stopped = state.get("stopped")
    if stopped:
        raise Stopped("run stopped (%s): %s (see %s)" % (stopped.get("kind"), stopped.get("reason", ""),
                                                          stopped.get("path")))
