"""The TUI's Flash Kernel path refuses a stale build directory (SPEC-leg-37).

`python tools/dbt.py` with no argument opens the TUI, so this is the path most
likely to flash the physical board. `sdkconfig_check.enforce` cannot be called
from here as-is: it prints to stdout and raises SystemExit, which from a worker
thread tears the app down instead of telling the user what to do. `DbtApp.
_check_sdkconfig` routes the same refusal through the TUI's error channel, and
these tests drive it directly — a Textual app cannot be flashed from pytest,
but the method is an ordinary function taking `self`.
"""

from pathlib import Path

import pytest

from dbt import sdkconfig_check as sc
from dbt import tui

SYMBOL = "CONFIG_ESP_MAIN_TASK_STACK_SIZE"
BOARD = "m5stack-cardputer"


class FakeApp:
    """Enough of DbtApp for _check_sdkconfig: the two output channels."""

    def __init__(self):
        self.logged: list = []
        self.errors: list = []

    def call_from_thread(self, fn, *args):
        return fn(*args)

    def _log_ansi(self, line):
        self.logged.append(line)

    def _err(self, title, body):
        self.errors.append((title, body))


@pytest.fixture
def fake_root(tmp_path, monkeypatch):
    """A repo layout the guard can read: root defaults + board fragment."""
    (tmp_path / "boards" / BOARD).mkdir(parents=True)
    (tmp_path / "sdkconfig.defaults").write_text(
        "CONFIG_FREERTOS_WATCHPOINT_END_OF_STACK=y\n", encoding="utf-8")
    (tmp_path / "boards" / BOARD / "sdkconfig.board").write_text(
        f"{SYMBOL}=4608\n", encoding="utf-8")
    monkeypatch.setattr(sc, "DUNEOS_ROOT", tmp_path)
    monkeypatch.setattr(tui, "DUNEOS_ROOT", tmp_path)
    return tmp_path


def _seed_build_dir(root: Path, stack: str, watchpoint: str) -> None:
    (root / "sdkconfig").write_text(
        f"{SYMBOL}={stack}\n{watchpoint}\n", encoding="utf-8")


def test_a_stale_build_directory_is_refused_with_the_actionable_message(fake_root):
    _seed_build_dir(fake_root, "3584",
                    "# CONFIG_FREERTOS_WATCHPOINT_END_OF_STACK is not set")
    app = FakeApp()

    assert tui.DbtApp._check_sdkconfig(app, BOARD) is False

    assert len(app.errors) == 1
    title, body = app.errors[0]
    assert "sdkconfig" in title.lower()
    for expected in (SYMBOL, "4608", "3584",
                     "CONFIG_FREERTOS_WATCHPOINT_END_OF_STACK",
                     "fullclean"):
        assert expected in body, f"missing {expected!r} from the TUI refusal"
    assert any(SYMBOL in line for line in app.logged), \
        "the refusal must also reach the TUI log, not only the modal"


def test_a_clean_build_directory_proceeds_silently(fake_root):
    _seed_build_dir(fake_root, "4608", "CONFIG_FREERTOS_WATCHPOINT_END_OF_STACK=y")
    app = FakeApp()

    assert tui.DbtApp._check_sdkconfig(app, BOARD) is True
    assert app.errors == []
    assert app.logged == []


def test_a_first_build_with_no_cached_sdkconfig_proceeds(fake_root):
    app = FakeApp()
    assert tui.DbtApp._check_sdkconfig(app, BOARD) is True
    assert app.errors == []


def test_the_refusal_does_not_raise_into_the_ui_thread(fake_root):
    """enforce() exits the process; the TUI must return a value instead."""
    _seed_build_dir(fake_root, "3584", "CONFIG_FREERTOS_WATCHPOINT_END_OF_STACK=y")
    app = FakeApp()
    try:
        result = tui.DbtApp._check_sdkconfig(app, BOARD)
    except SystemExit:  # pragma: no cover - the failure this test exists for
        pytest.fail("_check_sdkconfig raised SystemExit into the TUI")
    assert result is False
