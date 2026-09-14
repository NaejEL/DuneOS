"""LEG-40 — the TUI can run the gates, and it reports what `dbt test` reported.

Headless limits are real: Textual's worker cancellation, key routing and
rendering are not testable from pytest. What is testable is everything that
decides what runs and what the user is told — the argv builders, the three
action tables, the rc→status mapping and the child-termination helper. Those
are plain module-level functions here, which is the point of criterion 16.
"""

import inspect
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from dbt import tui  # noqa: E402

DBT = str(REPO_ROOT / "tools" / "dbt.py")
WORKERS = ("_worker_tests", "_worker_tests_full", "_worker_flash_sysbin")


def _source(name: str) -> str:
    return inspect.getsource(getattr(tui.DbtApp, name))


# --- criterion 12 ----------------------------------------------------------

def test_tui_no_longer_imports_the_staging_internals():
    assert "flashimg" not in Path(tui.__file__).read_text()
    for symbol in ("_stage", "_create_image", "_find_esptool"):
        assert not hasattr(tui, symbol)


def test_sysbin_worker_shells_out_to_system_flash():
    assert tui.system_flash_argv() == [sys.executable, DBT, "system", "flash"]
    assert "system_flash_argv()" in _source("_worker_flash_sysbin")


# --- criterion 13 ----------------------------------------------------------

def _menu_ids() -> set[str]:
    return {item[0] for item in tui._MENU if item is not None}


def _binding_ids() -> set[str]:
    ids = set()
    for binding in tui.DbtApp.BINDINGS:
        match = re.fullmatch(r"do\('([\w-]+)'\)", binding.action)
        ids.add(match.group(1) if match else binding.action)
    return ids


def _dispatch_ids() -> set[str]:
    return set(re.findall(r'^\s+"([\w-]+)":', _source("action_do"), re.M))


def test_binding_keys_are_unique():
    keys = [b.key for b in tui.DbtApp.BINDINGS]
    assert len(keys) == len(set(keys))


def test_menu_bindings_and_dispatch_agree():
    assert _menu_ids() == _binding_ids() == _dispatch_ids()
    assert {"test-run", "test-full", "cancel"} <= _menu_ids()


# --- criterion 14 ----------------------------------------------------------

def test_gate_argv_is_exactly_dbt_test():
    assert tui.gate_argv() == [sys.executable, DBT, "test"]


def test_full_gate_argv_adds_only_fuzz_and_qemu():
    assert tui.gate_argv("--fuzz", "--qemu") == [
        sys.executable, DBT, "test", "--fuzz", "--qemu"]


def test_test_workers_are_thin_shells():
    for name in ("_worker_tests", "_worker_tests_full"):
        body = _source(name)
        assert body.count("self._run_gates(") == 1
        assert "gate_argv(" in body
        # Gate selection, probing and verdicts belong to testing.py.
        for leak in ("pytest", "make", "clang", "probe", "--allow-missing"):
            assert leak not in body


# --- criterion 15 ----------------------------------------------------------

@pytest.mark.parametrize("rc, expected", [
    (0, "passed"),
    (1, "failed"),
    (2, "failed"),
    (3, "could not run"),
])
def test_gate_status_labels(rc, expected):
    assert expected in tui.gate_status(rc)[0]


def test_exit_3_is_not_rendered_as_a_failure():
    unavailable, colour = tui.gate_status(3)
    failed, failed_colour = tui.gate_status(1)
    assert unavailable != failed
    assert "fail" not in unavailable.lower()
    assert colour != failed_colour


# --- criterion 16 ----------------------------------------------------------

class _FakeProc:
    def __init__(self, alive=True, ignores_terminate=False):
        self._alive = alive
        self._ignores = ignores_terminate
        self.terminated = False
        self.killed = False

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminated = True
        if not self._ignores:
            self._alive = False

    def wait(self, timeout=None):
        if self._alive:
            import subprocess
            raise subprocess.TimeoutExpired("cmd", timeout)
        return 0

    def kill(self):
        self.killed = True
        self._alive = False


def test_terminate_child_stops_a_running_gate():
    proc = _FakeProc()
    assert tui.terminate_child(proc) is True
    assert proc.terminated and not proc.killed


def test_terminate_child_kills_a_child_that_ignores_terminate():
    proc = _FakeProc(ignores_terminate=True)
    assert tui.terminate_child(proc) is True
    assert proc.killed


def test_terminate_child_reports_nothing_to_stop():
    assert tui.terminate_child(None) is False
    assert tui.terminate_child(_FakeProc(alive=False)) is False


class _FakeApp:
    """Enough of DbtApp for _run_gates: the log channel and the child."""

    def __init__(self, rc, cancel=False):
        self._rc = rc
        self._cancel = cancel
        self._proc = None
        self._cancelled = False
        self.logged: list[str] = []

    def call_from_thread(self, fn, *args):
        fn(*args)

    def _log(self, msg):
        self.logged.append(msg)

    def _set_busy(self, _busy):
        pass

    def _stream(self, _argv):
        if self._cancel:
            self._cancelled = True
        return self._rc


def test_a_cancelled_run_is_neither_pass_nor_fail():
    app = _FakeApp(rc=-15, cancel=True)
    tui.DbtApp._run_gates(app, tui.gate_argv())
    log = "\n".join(app.logged)
    assert "cancelled" in log
    assert tui.gate_status(0)[0] not in log
    assert tui.gate_status(-15)[0] not in log


# --- criterion 17 ----------------------------------------------------------

def test_a_tui_gate_run_never_touches_the_board_file():
    for argv in (tui.gate_argv(), tui.gate_argv("--fuzz", "--qemu")):
        assert "--board" not in argv
    for name in WORKERS + ("_run_gates", "_run_cancel"):
        body = _source(name)
        for leak in ("write_board_file", "BOARD_FILE", ".duneos_board"):
            assert leak not in body
