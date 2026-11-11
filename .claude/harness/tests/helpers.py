# Shared tools of the core tests (not a test module).

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List, Optional, Tuple

CORE = Path(__file__).resolve().parent.parent
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from hcore.proc import Result  # noqa: E402

HARNESS = CORE / "harness.py"
TESTS = Path(__file__).resolve().parent
# Repository fixtures (absent when the core is copied into a project).
FIXTURES = CORE.parent.parent / "tests" / "fixtures"
HAS_FIXTURES = (FIXTURES / "merge-input").is_dir()
SKIP_FIXTURES = "claude-skills repository fixtures absent (core copied into a project)"


def _make_writable_and_retry(func, path, exc_info) -> None:
    """rmtree onerror: on Windows, git creates its objects read-only."""
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    func(path)


def remove_tree(path: Path) -> None:
    """Removes a temporary test directory; a failure is never silent."""
    if os.path.exists(str(path)):
        shutil.rmtree(str(path), onerror=_make_writable_and_retry)


class TempDirCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="hcore-"))
        self.addCleanup(remove_tree, self.tmp)


def harness(args: List[str], cwd: Path, stdin: Optional[str] = None,
            env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
    full_env = dict(os.environ)
    full_env["PYTHONDONTWRITEBYTECODE"] = "1"
    full_env["PYTHONIOENCODING"] = "utf-8"
    if env:
        full_env.update(env)
    return subprocess.run([sys.executable, "-B", str(HARNESS)] + args, cwd=str(cwd),
                          input=stdin, capture_output=True, text=True, encoding="utf-8",
                          env=full_env, timeout=120)


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git"] + list(args), cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", timeout=60)
    if proc.returncode != 0:
        raise AssertionError("git %s: %s" % (" ".join(args), proc.stderr))
    return proc.stdout


def init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.invalid")
    git(path, "config", "commit.gpgsign", "false")
    git(path, "config", "core.autocrlf", "false")


class FakeRunner:
    """Simulated runner: table (command prefix -> Result), calls recorded."""

    def __init__(self, table: Optional[List[Tuple[Tuple[str, ...], Result]]] = None):
        self.table = list(table or [])
        self.calls: List[List[str]] = []

    def on(self, prefix: Tuple[str, ...], code: int = 0, out: str = "", err: str = "") -> "FakeRunner":
        self.table.insert(0, (prefix, Result(code, out, err)))
        return self

    def __call__(self, args: List[str], input: Optional[str] = None) -> Result:
        self.calls.append(list(args))
        for prefix, result in self.table:
            if tuple(args[: len(prefix)]) == prefix:
                return result
        return Result(0, "", "")

    def called(self, *prefix: str) -> List[List[str]]:
        return [c for c in self.calls if tuple(c[: len(prefix)]) == prefix]


# Trap content: what a misused shell (unquoted heredoc, echo '...') would
# execute, empty or truncate. It must travel unchanged through a file. The
# non-ASCII "ï" checks that UTF-8 survives the trip.
TRAP = ("Naïve trap: `date` then $(whoami), $HOME, ${PATH}, $ARGUMENTS and $1; "
        "O'Brien's apostrophe, \"double quotes\", a backslash \\ and !! end\nline 2\n")


def write_output(root: Path, name: str, text: str) -> str:
    """What the Write tool prescribed by the SKILL.md files does: the content, as
    is, in a file under .harness/runs/; returns the path to pass to the core."""
    path = Path(root) / ".harness" / "runs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return ".harness/runs/" + name


def verifier_block(verdict: str, tests_passed: bool, issues=None) -> str:
    return "Report.\n\n```json\n%s\n```\n" % json.dumps(
        {"verdict": verdict, "tests_passed": tests_passed, "issues": issues or []})


def reviewer_block(findings) -> str:
    return "Analysis.\n%s" % json.dumps({"findings": findings})


def validator_block(verdict: str, reason: str = "reason") -> str:
    return json.dumps({"verdict": verdict, "reason": reason})


def finding(file: str = "src/a.py", line: int = 3, title: str = "Bug", severity: str = "major") -> dict:
    return {"file": file, "line": line, "side": "RIGHT", "severity": severity, "title": title,
            "description": "scenario", "fix": "fix it"}


def bash_check(script: str):
    """`bash -n` on a script text: True/False for a verdict, None when no working bash exists
    (a missing or broken WSL bash is an environment problem, not a defect of the script)."""
    candidates = ["C:/Program Files/Git/bin/bash.exe", "C:/Program Files (x86)/Git/bin/bash.exe", "bash"]
    for exe in candidates:
        try:
            res = subprocess.run([exe, "-n"], input=script.encode("utf-8"), capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            continue
        err = res.stderr.decode("utf-8", "replace")
        if res.returncode == 0:
            return True
        if "syntax error" in err or "unexpected" in err:
            return False
    return None
