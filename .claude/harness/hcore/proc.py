# Execution of external commands (gh, git) behind an injectable interface,
# so that the tests can simulate gh and git.
#
# Test injection point: HCORE_GH / HCORE_GIT / HCORE_CLAUDE (JSON array replacing the
# executable) are honoured only if HCORE_TEST_MODE=1 is also set. Outside
# that mode they are ignored (warning on stderr); in that mode, the CLI
# reports it in every JSON output ("hcore_test_mode" key).

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional

TEST_MODE_VAR = "HCORE_TEST_MODE"
TOOLS = ("gh", "git", "claude")


@dataclass
class Result:
    code: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.code == 0


def test_mode() -> bool:
    return os.environ.get(TEST_MODE_VAR) == "1"


def overrides(warn: bool = True) -> Dict[str, List[str]]:
    """Active gh/git replacements (empty outside test mode)."""
    active: Dict[str, List[str]] = {}
    for name in TOOLS:
        raw = os.environ.get("HCORE_" + name.upper())
        if not raw:
            continue
        if not test_mode():
            if warn:
                sys.stderr.write("WARNING: HCORE_%s ignored (%s=1 not set)\n" % (name.upper(), TEST_MODE_VAR))
            continue
        try:
            value = json.loads(raw)
        except ValueError:
            continue
        if isinstance(value, list) and value and all(isinstance(x, str) for x in value):
            active[name] = value
    return active


class Runner:
    """Runs a command in a given directory and returns a Result."""

    def __init__(self, cwd: Optional[str] = None, timeout: Optional[float] = None):
        self.cwd = cwd
        self.timeout = timeout
        self.overrides = overrides()

    def __call__(self, args: List[str], input: Optional[str] = None) -> Result:
        if args and args[0] in self.overrides:
            args = self.overrides[args[0]] + list(args[1:])
        try:
            proc = subprocess.run(
                args,
                cwd=self.cwd,
                input=input,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
            )
        except FileNotFoundError as exc:
            return Result(127, "", str(exc))
        except subprocess.TimeoutExpired:
            return Result(124, "", "timed out: " + " ".join(args))
        return Result(proc.returncode, proc.stdout or "", proc.stderr or "")
