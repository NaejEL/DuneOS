# Chaining of cycles: headless child processes and delivery commits (CH-01 to CH-06).
#
# - CH-01: `spawn` launches `claude -p "/<skill> <arguments>"` as a detached
#   child (argv list, never a shell, cwd = root, new process group). The child is
#   supervised by `exec_card`, a small wrapper that enforces `chain.timeout_min`
#   (kills the process tree) and records the exit code in the chain card
#   `.harness/runs/chain-<id>.json`; outputs go to `chain-<id>.out.json` and
#   `chain-<id>.err.txt`. The environment of the child is the parent's, plus
#   HARNESS_CHAIN_ID and HARNESS_PARENT_RUN, minus CLAUDECODE.
# - Cost guard: a non-empty billing variable stops the chain (kind `cost`)
#   unless `chain.allow_api_billing` is true in the stamp.
# - CH-02: `deliver` commits the build of a clean tree (branch `factory/<slug>`
#   when on the target or detached), never pushes.
# - CH-03: `extract_options` takes --ship, --no-ship, --deliver-only, --worktree,
#   --chained-from, --chain-id and --autonomy out of $ARGUMENTS.
# - CH-06: the `chain` journal event is written by the callers.

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import gates, proc, runs

BILLING_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX")
DEFAULT_TOOLS = ("Read", "Edit", "Write", "Glob", "Grep", "Bash", "Agent")
SHIP_TOOLS = ("Bash(gh *)", "Skill")
SKILLS = ("factory-run", "ship-pr")
MAX_TURNS = {"factory-run": 100, "ship-pr": 200}
DEFAULT_TIMEOUT_MIN = 120
CARD_PREFIX = "chain-"
WAIT_POLL = 0.2


class ChainError(ValueError):
    """Invalid chain request (exit 2)."""


class ChainStop(RuntimeError):
    """A chain cannot start or ended badly: the caller writes a STOP (kind in `kind`)."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


# --------------------------------------------------------------- arguments

_FLAG_VALUE = re.compile(r"(?:(?<=\s)|^)(--(?:ship|chained-from|chain-id|autonomy))(?:[ \t]+|=)(\"[^\"]*\"|'[^']*'|\S+)")
_FLAG_BOOL = re.compile(r"(?:(?<=\s)|^)(--(?:no-ship|deliver-only|worktree))(?=\s|$)")


def extract_options(arguments: str) -> Tuple[str, Dict[str, Any]]:
    """CH-03 and AP-03: (rest, options) with the run options taken out of $ARGUMENTS."""
    options: Dict[str, Any] = {"ship": None, "no_ship": False, "deliver_only": False, "worktree": False,
                               "chained_from": None, "chain_id": None, "autonomy": None}
    text = arguments or ""

    def value(m):
        options[m.group(1)[2:].replace("-", "_")] = m.group(2).strip("'\"")
        return ""

    def flag(m):
        options[m.group(1)[2:].replace("-", "_")] = True
        return ""

    text = _FLAG_VALUE.sub(value, text)
    text = _FLAG_BOOL.sub(flag, text)
    if options["autonomy"] is not None and options["autonomy"] not in gates.POLICIES:
        raise ChainError("--autonomy must be one of %s, got %r" % ("|".join(gates.POLICIES), options["autonomy"]))
    if options["ship"] and options["no_ship"]:
        raise ChainError("--ship and --no-ship cannot be used together")
    return " ".join(text.split()), options


# ------------------------------------------------------------------ config

def stamp_chain(root: Path) -> Dict[str, Any]:
    """The `chain` object of .claude/harness.json (empty when absent)."""
    try:
        data = json.loads((Path(root) / gates.POLICY_FILE).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    chain = data.get("chain") if isinstance(data, dict) else None
    return chain if isinstance(chain, dict) else {}


def allowed_tools(root: Path, skill: str, chain: Optional[Dict[str, Any]] = None) -> List[str]:
    """CH-01: `chain.allowed_tools`, else the allow list of ci/factory.sh, else the default list;
    plus Bash(gh *) and Skill when ship-pr is chained."""
    chain = stamp_chain(root) if chain is None else chain
    tools = [t for t in chain.get("allowed_tools") or [] if isinstance(t, str)]
    if not tools:
        try:
            ci = (Path(root) / "ci" / "factory.sh").read_text(encoding="utf-8", errors="replace")
            m = re.search(r'--allowedTools\s+"([^"]+)"', ci)
            tools = [t.strip() for t in m.group(1).split(",") if t.strip()] if m else []
        except OSError:
            tools = []
    tools = tools or list(DEFAULT_TOOLS)
    if skill == "ship-pr" or chain.get("with_ship"):
        tools += [t for t in SHIP_TOOLS if t not in tools]
    return tools


def build_argv(skill: str, arguments: str, tools: Sequence[str], max_turns: int,
               autonomy: Optional[str] = None) -> List[str]:
    """CH-01: the argv of the child (a list, never a shell string)."""
    if skill not in SKILLS:
        raise ChainError("chain skill must be one of %s, got %r" % ("|".join(SKILLS), skill))
    prompt = "/%s %s" % (skill, arguments.strip())
    if autonomy:
        prompt += " --autonomy %s" % autonomy
    return ["claude", "-p", prompt.strip(), "--output-format", "json", "--max-turns", str(max_turns),
            "--permission-mode", "acceptEdits", "--allowedTools", ",".join(tools),
            "--permission-prompts", "none"]


def child_env(base: Dict[str, str], chain_id: str, parent_run: Optional[str]) -> Dict[str, str]:
    env = {k: v for k, v in base.items() if k != "CLAUDECODE"}
    env["HARNESS_CHAIN_ID"] = chain_id
    env["HARNESS_PARENT_RUN"] = parent_run or ""
    return env


def cost_guard(env: Dict[str, str], chain: Dict[str, Any]) -> Optional[str]:
    """Cost guard: the name of the billing variable that is set, unless allowed."""
    if chain.get("allow_api_billing") is True:
        return None
    return next((v for v in BILLING_VARS if env.get(v, "").strip()), None)


def resolve_claude() -> Optional[List[str]]:
    """The executable of the child: HCORE_CLAUDE in test mode, else `claude` on the PATH."""
    override = proc.overrides().get("claude")
    if override:
        return list(override)
    found = shutil.which("claude")
    return [found] if found else None


# -------------------------------------------------------------------- cards

def card_path(root: Path, chain_id: str) -> Path:
    return Path(root) / runs.RUNS_DIR / ("%s%s.json" % (CARD_PREFIX, chain_id))


def new_id(root: Path, now: Optional[datetime] = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    n, chain_id = 0, stamp
    while card_path(root, chain_id).exists():
        n += 1
        chain_id = "%s-%d" % (stamp, n)
    return chain_id


def read_card(root: Path, chain_id: str) -> Dict[str, Any]:
    path = card_path(root, chain_id)
    if not path.exists():
        raise ChainError("unknown chain: %s" % chain_id)
    for attempt in range(20):             # Windows: the atomic replace of a writer can deny a concurrent read
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.05)
    raise ChainError("unreadable chain card: %s" % chain_id)


def write_card(root: Path, card: Dict[str, Any]) -> None:
    runs.write_atomic(card_path(root, card["id"]), json.dumps(card, ensure_ascii=False, indent=1))


def spawn(root: Path, skill: str, arguments: str, parent_run: Optional[str], autonomy: Optional[str],
          env: Optional[Dict[str, str]] = None, cwd: Optional[Path] = None,
          timeout_s: Optional[float] = None) -> Dict[str, Any]:
    """CH-01: starts the supervised child and returns its card. Raises ChainStop
    (kinds `cost` and `child`) when nothing was launched."""
    root = Path(root)
    base = dict(os.environ if env is None else env)
    chain = stamp_chain(root)
    billing = cost_guard(base, chain)
    if billing:
        raise ChainStop("cost", "%s is set: the child would not use the subscription; nothing was launched" % billing)
    exe = resolve_claude()
    if exe is None:
        raise ChainStop("child", "claude is not on the PATH: nothing was launched")
    turns = int(chain.get("max_turns") or MAX_TURNS[skill if skill in MAX_TURNS else "factory-run"])
    if chain.get("max_turns") is None and skill == "factory-run" and "--ship" in arguments:
        turns = MAX_TURNS["ship-pr"]
    tools = allowed_tools(root, skill, dict(chain, with_ship="--ship" in arguments))
    argv = exe + build_argv(skill, arguments, tools, turns, autonomy)[1:]
    chain_id = new_id(root)
    work = Path(cwd) if cwd else root
    (root / runs.RUNS_DIR).mkdir(parents=True, exist_ok=True)
    if timeout_s is None:
        timeout_s = float(chain.get("timeout_min") or DEFAULT_TIMEOUT_MIN) * 60
    card = {"id": chain_id, "skill": skill, "args": arguments, "pid": None, "root": str(root.resolve()),
            "cwd": str(work.resolve()), "parent_run": parent_run, "started": time.time(), "status": "running",
            "argv": argv, "timeout_s": timeout_s, "exit_code": None,
            "stops_before": sorted(p.name for p in (work / runs.RUNS_DIR).glob("STOP-*.md")),
            "out": str(card_path(root, chain_id).with_name("%s%s.out.json" % (CARD_PREFIX, chain_id))),
            "err": str(card_path(root, chain_id).with_name("%s%s.err.txt" % (CARD_PREFIX, chain_id)))}
    write_card(root, card)
    wrapper = [sys.executable, "-B", str(Path(__file__).resolve().parent.parent / "harness.py"), "chain", "exec",
               "--root", str(root), "--id", chain_id]
    kwargs: Dict[str, Any] = {"cwd": str(work), "env": child_env(base, chain_id, parent_run),
                              "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                              "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000200 | 0x00000008   # CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    wrap = subprocess.Popen(wrapper, **kwargs)
    card["pid"] = wrap.pid
    write_card(root, card)
    return card


def exec_card(root: Path, chain_id: str) -> int:
    """The wrapper: runs the child of a card, enforces its timeout, records the outcome."""
    card = read_card(root, chain_id)
    with open(card["out"], "wb") as out, open(card["err"], "wb") as err:
        kwargs: Dict[str, Any] = {"cwd": card["cwd"], "stdin": subprocess.DEVNULL, "stdout": out, "stderr": err}
        if os.name != "nt":
            kwargs["start_new_session"] = True
        try:
            child = subprocess.Popen(card["argv"], **kwargs)
        except OSError as exc:
            card.update(status="failed", exit_code=127, error=str(exc))
            write_card(root, card)
            return 127
        try:
            code = child.wait(timeout=card["timeout_s"])
            status = "done" if code == 0 else "failed"
        except subprocess.TimeoutExpired:
            kill_tree(child)
            code, status = None, "timeout"
    card = read_card(root, chain_id)
    card.update(status=status, exit_code=code, finished=time.time())
    write_card(root, card)
    return 0


def kill_tree(child: "subprocess.Popen[bytes]") -> None:
    """The whole process tree of a child is killed (taskkill /T on Windows, the group elsewhere)."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"], capture_output=True)
    else:
        import signal
        try:
            os.killpg(os.getpgid(child.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            child.kill()
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def find_child_run(root: Path, chain_id: str) -> Optional[Dict[str, Any]]:
    """The run of the child (state key `chain_id`), with its STOP if any."""
    folder = Path(root) / runs.RUNS_DIR
    for path in sorted(folder.glob("*.state.json")) if folder.is_dir() else []:
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if state.get("chain_id") == chain_id:
            return {"run": path.name[: -len(".state.json")] + ".jsonl", "status": state.get("status"),
                    "stopped": state.get("stopped"), "spec": state.get("spec")}
    return None


def stops_since(cwd: Path, before: Sequence[str]) -> List[str]:
    """The STOP files that appeared in the working directory of the child since the launch."""
    folder = Path(cwd) / runs.RUNS_DIR
    return sorted(p.name for p in folder.glob("STOP-*.md") if p.name not in before) if folder.is_dir() else []


def pid_alive(pid: Optional[int]) -> bool:
    """True when the process exists (an unknown pid counts as alive: the card is not complete yet)."""
    if not pid:
        return True
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259                                   # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def output_error(card: Dict[str, Any]) -> Optional[str]:
    """The reason given by `claude -p --output-format json` when it ended in error (is_error, max turns...)."""
    try:
        data = json.loads(Path(card["out"]).read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError, KeyError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("is_error") is True:
        return str(data.get("result") or data.get("subtype") or "is_error")[:200]
    subtype = data.get("subtype")
    if isinstance(subtype, str) and subtype.startswith("error"):
        return subtype
    return None


def status(root: Path, chain_id: str) -> Dict[str, Any]:
    """CH-01: card, result read from the run of the child, its STOP files and its exit code."""
    card = read_card(root, chain_id)
    if card["status"] == "running" and not pid_alive(card.get("pid")):
        card = read_card(root, chain_id)            # the wrapper may have written its record just before exiting
        if card["status"] == "running":
            card.update(status="failed", exit_code=None, finished=time.time(),
                        error="the supervising wrapper died before recording the outcome")
            write_card(root, card)
    result = {k: card.get(k) for k in ("id", "skill", "args", "pid", "parent_run", "status", "exit_code")}
    result["done"] = card["status"] != "running"
    result["error"] = card.get("error")
    result["output_error"] = output_error(card) if result["done"] else None
    result["child_run"] = find_child_run(root, chain_id)
    result["stops"] = stops_since(card["cwd"], card.get("stops_before", [])) if result["done"] else []
    return result


def wait(root: Path, chain_id: str, max_seconds: float) -> Dict[str, Any]:
    deadline = time.monotonic() + max_seconds
    while True:
        result = status(root, chain_id)
        if result["done"] or time.monotonic() >= deadline:
            return result
        time.sleep(WAIT_POLL)


def failure(result: Dict[str, Any]) -> Optional[str]:
    """Why a finished chain is a stop (kind `child`), or None when it went well."""
    if not result["done"]:
        return None
    if result["status"] == "timeout":
        return "the child exceeded chain.timeout_min and was killed"
    if result["status"] == "failed" or result.get("exit_code") not in (0, None):
        return "the child exited with code %s%s" % (result.get("exit_code"),
                                                    " (%s)" % result["error"] if result.get("error") else "")
    if result["stops"]:
        return "the child left a STOP file: %s" % result["stops"][-1]
    run = result.get("child_run") or {}
    if run.get("status") == "failed":
        return "the cycle of the child failed"
    if result.get("output_error"):
        return "the child ended in error: %s" % result["output_error"]
    return None


# ----------------------------------------------------------------- delivery

def slug_of(spec: str) -> str:
    name = Path(spec).name
    name = re.sub(r"\.md$", "", name)
    name = re.sub(r"^SPEC-", "", name, flags=re.I)
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "spec"


def title_of(root: Path, spec: str) -> str:
    try:
        for line in (Path(root) / spec).read_text(encoding="utf-8-sig", errors="replace").splitlines():
            if line.startswith("# "):
                title = re.sub(r"^(?:SPEC)\s*[—–:-]*\s*", "", line[2:].strip(), flags=re.I)
                return " ".join(title.split()) or slug_of(spec)
    except OSError:
        pass
    return slug_of(spec)


def resolve_target(run_cmd, root: Path, flag: Optional[str]) -> Optional[str]:
    """Q21: --ship, then ship_pr.target of the stamp, then refs/remotes/origin/HEAD."""
    if flag:
        return flag
    stamped = gates.stamp_ship_target(root)
    if stamped:
        return stamped
    head = run_cmd(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"])
    if head.ok and head.out.strip().startswith("origin/"):
        return head.out.strip()[len("origin/"):]
    return None


def deliver_conditions(state: Dict[str, Any], target: Optional[str]) -> List[str]:
    """CH-02: the reasons why the delivery does not take place (empty = go)."""
    reasons = []
    if state.get("dirty_at_start"):
        reasons.append("the tree was not clean when the cycle started")
    if not target:
        reasons.append("no target: pass --ship, set ship_pr.target, or set origin/HEAD")
    if state.get("profile") not in (None, "software"):
        reasons.append("only the software profile ships (Q30)")
    if not state.get("spec"):
        reasons.append("the cycle has no spec to name the delivery")
    return reasons


def deliver(run_cmd, root: Path, state: Dict[str, Any], target: str, trailers: Sequence[str] = (),
            now: Optional[datetime] = None) -> Dict[str, Any]:
    """CH-02: branch `factory/<slug>` when on the target or detached (suffix on collision),
    `git add -A`, commit `feat(factory): <title>` with trailers, no push."""
    spec = state["spec"]
    current = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"]).out.strip()
    branch = current
    if current in (target, "HEAD", ""):
        branch = "factory/" + slug_of(spec)
        if run_cmd(["git", "rev-parse", "--verify", "--quiet", "refs/heads/" + branch]).ok:
            branch += "-" + (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
        res = run_cmd(["git", "checkout", "-b", branch])
        if not res.ok:
            raise ChainStop("child", "git checkout -b %s failed: %s" % (branch, (res.err or res.out).strip()))
    for cmd in (["git", "add", "-A"],):
        res = run_cmd(cmd)
        if not res.ok:
            raise ChainStop("child", "%s failed: %s" % (" ".join(cmd), (res.err or res.out).strip()))
    message = "feat(factory): %s" % title_of(root, spec)
    if trailers:
        message += "\n\n" + "\n".join(trailers)
    res = run_cmd(["git", "commit", "-m", message])
    if not res.ok:
        raise ChainStop("child", "git commit failed: %s" % (res.err or res.out).strip())
    commit = run_cmd(["git", "rev-parse", "HEAD"]).out.strip()
    return {"delivered": True, "branch": branch, "commit": commit, "target": target, "message": message}
