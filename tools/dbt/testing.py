"""
dbt test — run the repo's test gates and say, per gate, what happened.

An unavailable gate is never a pass. That is the whole point of this module:
a machine with no pytest, no clang or the wrong board selected must be told so
by a non-zero exit, not by a green run that tested less than it looked.

Exit codes (asserted by tools/dbt/tests/test_testing.py):
    0  every selected gate ran and passed
    1  a selected gate ran and failed
    3  a selected gate could not run   (--allow-missing downgrades this to 0)

Failure outranks unavailability: a run with both exits 1, because the failing
gate is the thing to go and look at.
"""

# Named testing.py rather than test.py so it does not read as a suite sitting
# next to tools/dbt/tests/. It would import as dbt.test and shadow nothing.

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .constants import DUNEOS_ROOT, BOARD_FILE
from .qemu import QEMU_BOARDS, EXIT_CONFIG as QEMU_EXIT_CONFIG

EXIT_OK          = 0
EXIT_FAILED      = 1
EXIT_UNAVAILABLE = 3

RAN_PASSED  = "passed"
RAN_FAILED  = "failed"
UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class Gate:
    name: str
    summary: str
    # Returns the reason the gate cannot run, or None when it can.
    probe: Callable[[], str | None]
    # Returns a process exit code; 0 is a pass. May return UNAVAILABLE_RC to
    # say, after the fact, that the gate did not get to run after all.
    run: Callable[[], int]


# `dbt qemu` exits 6 when .duneos_board does not name the board asked for. That
# is a configuration answer, not a firmware verdict, so it is reported here as
# unavailability — and the board file is never written to get past it.
#
# The value is outside the range a process can return, because gates hand their
# child's raw `returncode` to run_gates and subprocess reports a signal death as
# -N: -1 is SIGHUP, which a dropped ssh session delivers to a `make` running
# here. That would have read as unavailable — a configuration problem, said the
# message — and exited 0 under --allow-missing, which is the one outcome this
# module exists to refuse.
UNAVAILABLE_RC = 1 << 20


def _run(argv: list[str]) -> int:
    print(f"  $ {' '.join(argv)}", flush=True)
    return subprocess.run(argv, cwd=str(DUNEOS_ROOT)).returncode


def _probe_pytest() -> str | None:
    probe = subprocess.run([sys.executable, "-c", "import pytest"],
                           capture_output=True)
    if probe.returncode == 0:
        return None
    return (f"{Path(sys.executable).name} has no pytest "
            f"(pip install -c tools/constraints.txt pytest)")


def _probe_toolchain() -> str | None:
    if shutil.which("make") is None:
        return "no make on PATH"
    if shutil.which("cc") is None and shutil.which("gcc") is None:
        return "no host C compiler on PATH"
    return None


# test_manifest_depth links cJSON from the submodule. A plain `git clone` (which
# is what CONTRIBUTING's setup section describes) leaves it empty and make stops
# before running a single suite — a setup problem, not a test verdict.
SUBMODULE_WITNESS = DUNEOS_ROOT / "third_party" / "cjson" / "cJSON.c"


def _probe_make() -> str | None:
    missing = _probe_toolchain()
    if missing:
        return missing
    if not SUBMODULE_WITNESS.exists():
        return (f"submodules are not checked out ({SUBMODULE_WITNESS.name} is "
                "absent) — run `git submodule update --init --recursive`")
    return None


def _probe_fuzz() -> str | None:
    missing = _probe_toolchain()
    if missing:
        return missing
    if shutil.which("clang") is None:
        return "no clang on PATH — libFuzzer needs one (see tests/host/Makefile)"
    return None


def _active_board() -> str | None:
    try:
        return BOARD_FILE.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _probe_qemu() -> str | None:
    board = _active_board()
    if board is None:
        return ".duneos_board is not set"
    if board not in QEMU_BOARDS:
        return (f".duneos_board says '{board}'; the bench needs one of "
                f"{', '.join(QEMU_BOARDS)}, and switching it is a full clean "
                f"(see CONTRIBUTING.md)")
    return None


def _run_qemu() -> int:
    board = _active_board()
    rc = _run([sys.executable, str(DUNEOS_ROOT / "tools" / "dbt.py"), "qemu",
               "--board", str(board), "--build-dir", f"build-{board}"])
    return UNAVAILABLE_RC if rc == QEMU_EXIT_CONFIG else rc


# ---------------------------------------------------------------------------
# The pytest gate counts what RAN.
#
# `pytest -q` exits 0 for a run that skipped every test, so the return code
# alone cannot tell a pass from a suite that tested nothing — the failure mode
# three cycles were spent on, and the one this verb exists to end. The floor is
# read from the CI workflow so the two cannot drift apart.
# ---------------------------------------------------------------------------

CI_WORKFLOW = DUNEOS_ROOT / ".github" / "workflows" / "ci.yml"


def _ci_floor() -> int | None:
    try:
        match = re.search(r"^\s*FLOOR=(\d+)\s*$",
                          CI_WORKFLOW.read_text(encoding="utf-8"), re.M)
    except OSError:
        return None
    return int(match.group(1)) if match else None


def _junit_counts(report: Path) -> tuple[int, int] | None:
    """(collected, skipped) from a junit report, or None if unreadable."""
    try:
        suite = next(ET.parse(report).getroot().iter("testsuite"))
    except (OSError, ET.ParseError, StopIteration):
        return None
    try:
        return int(suite.get("tests", 0)), int(suite.get("skipped", 0))
    except (TypeError, ValueError):
        return None


def pytest_verdict(rc: int, counts: tuple[int, int] | None, floor: int | None,
                   echo: Callable[[str], None] = print) -> int:
    """Turn pytest's exit code plus its junit counts into a gate result.

    Mirrors the triage in .github/workflows/ci.yml: exit 5 is an empty
    collection, >= 2 is a collection error, and a run whose tests all skipped
    verified nothing however green it looked.
    """
    if rc == 5:
        echo("  pytest collected 0 tests — NO TEST RAN")
        return 1
    if rc >= 2:
        echo(f"  pytest exited {rc} before running the suite "
             f"(collection error) — NO TEST RAN")
        return 1
    if counts is None:
        echo("  pytest produced no readable junit report — cannot tell how "
             "many tests ran, so this is not a pass")
        return 1

    collected, skipped = counts
    ran = collected - skipped
    echo(f"  collected {collected} tests, {skipped} skipped, {ran} ran"
         + (f" (floor {floor})" if floor is not None else ""))
    if ran <= 0:
        echo("  every collected test was skipped — the suite verified nothing")
        return 1
    if floor is not None and ran < floor:
        echo(f"  only {ran} of {collected} tests ran, floor is {floor} — the "
             f"suite shrank or mass-skipped. Raise FLOOR in "
             f"{CI_WORKFLOW.relative_to(DUNEOS_ROOT)} in the same commit as "
             f"tests you remove on purpose.")
        return 1
    return rc


def _run_pytest() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "pytest-report.xml"
        rc = _run([sys.executable, "-m", "pytest", "-q",
                   f"--junitxml={report}"])
        return pytest_verdict(rc, _junit_counts(report), _ci_floor())


def build_gates(fuzz: bool = False, qemu: bool = False) -> list[Gate]:
    """Cheapest first, so a broken tree is reported in seconds."""
    gates = [
        Gate("pytest", "dbt and bspgen tooling", _probe_pytest, _run_pytest),
        Gate("host-c", "C host suites", _probe_make,
             lambda: _run(["make", "-C", "tests/host", "test"])),
    ]
    if fuzz:
        gates.append(Gate("fuzz", "libFuzzer run on the ELF entry point",
                          _probe_fuzz,
                          lambda: _run(["make", "-C", "tests/host", "fuzz-run"])))
    if qemu:
        gates.append(Gate("qemu", "hardware-free boot + loader smoke test",
                          _probe_qemu, _run_qemu))
    return gates


def run_gates(gates: list[Gate], allow_missing: bool = False,
              echo: Callable[[str], None] = print) -> int:
    results: list[tuple[str, str, str]] = []

    for gate in gates:
        reason = gate.probe()
        if reason is not None:
            echo(f"\n[{gate.name}] unavailable — {reason}")
            results.append((gate.name, UNAVAILABLE, reason))
            continue

        echo(f"\n[{gate.name}] {gate.summary}")
        rc = gate.run()
        if rc == UNAVAILABLE_RC:
            reason = "the gate reported a configuration problem, see above"
            echo(f"[{gate.name}] unavailable — {reason}")
            results.append((gate.name, UNAVAILABLE, reason))
        elif rc == 0:
            results.append((gate.name, RAN_PASSED, ""))
        else:
            results.append((gate.name, RAN_FAILED, f"exit {rc}"))

    echo("\n" + "=" * 60)
    for name, status, detail in results:
        echo(f"  [{status.upper():>11}] {name}" + (f" — {detail}" if detail else ""))
    echo("=" * 60)

    failed = [name for name, status, _ in results if status == RAN_FAILED]
    missing = [name for name, status, _ in results if status == UNAVAILABLE]

    if failed:
        echo(f"FAIL: {', '.join(failed)}")
        return EXIT_FAILED
    if missing and not allow_missing:
        echo(f"COULD NOT RUN: {', '.join(missing)} — a gate that did not run is "
             f"not a gate that passed. Re-run with --allow-missing to accept it.")
        return EXIT_UNAVAILABLE
    if missing:
        echo(f"WARNING: {', '.join(missing)} did not run (--allow-missing).")
    echo("OK")
    return EXIT_OK


def cmd_test(args) -> None:
    gates = build_gates(fuzz=getattr(args, "fuzz", False),
                        qemu=getattr(args, "qemu", False))
    sys.exit(run_gates(gates, allow_missing=getattr(args, "allow_missing", False)))
