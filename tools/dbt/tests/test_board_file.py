"""Tests for the `.duneos_board` write/read race (FIX 7).

CMakeLists.txt reads `.duneos_board` on every configure and falls back to a
hardcoded board when the file is absent, then aborts with FATAL_ERROR if that
fallback differs from the board the build dir was configured for. So a
truncate-and-write leaves a window in which a concurrent configure fails for a
reason that has nothing to do with the firmware. Two defences are tested here:
dbt's own write is atomic, and `dbt qemu` names the interference when someone
else's write is not.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from dbt.constants import write_board_file  # noqa: E402
from dbt import qemu  # noqa: E402


def test_write_board_file_writes_the_name_with_a_trailing_newline(tmp_path):
    target = tmp_path / ".duneos_board"
    write_board_file("m5stack-cardputer", target)
    assert target.read_text() == "m5stack-cardputer\n"


def test_write_board_file_overwrites_an_existing_value(tmp_path):
    target = tmp_path / ".duneos_board"
    target.write_text("esp32s3-qemu\n")
    write_board_file("m5stack-cardputer", target)
    assert target.read_text().strip() == "m5stack-cardputer"


def test_write_board_file_never_truncates_in_place(tmp_path, monkeypatch):
    """The decisive property: the destination inode is replaced, not opened for
    writing. A reader holding the old path sees old-or-new, never empty."""
    target = tmp_path / ".duneos_board"
    target.write_text("esp32s3-qemu\n")

    real_open = open
    opened = []

    def spy(file, *a, **kw):
        opened.append(str(file))
        return real_open(file, *a, **kw)

    monkeypatch.setattr("builtins.open", spy)
    write_board_file("m5stack-cardputer", target)
    assert str(target) not in opened


def test_write_board_file_leaves_no_temporary_behind(tmp_path):
    target = tmp_path / ".duneos_board"
    write_board_file("m5stack-cardputer", target)
    assert [p.name for p in tmp_path.iterdir()] == [".duneos_board"]


def test_a_failed_write_leaves_the_old_value_intact(tmp_path, monkeypatch):
    target = tmp_path / ".duneos_board"
    target.write_text("esp32s3-qemu\n")

    def boom(*_a, **_kw):
        raise OSError("disk full")

    monkeypatch.setattr("dbt.constants.os.replace", boom)
    with pytest.raises(OSError):
        write_board_file("m5stack-cardputer", target)
    assert target.read_text().strip() == "esp32s3-qemu"
    assert [p.name for p in tmp_path.iterdir()] == [".duneos_board"]


# ---------------------------------------------------------------------------
# dbt qemu: the board file changing underneath a run
# ---------------------------------------------------------------------------

def _point_qemu_at(tmp_path, monkeypatch, content):
    board_file = tmp_path / ".duneos_board"
    if content is not None:
        board_file.write_text(content)
    monkeypatch.setattr(qemu, "DUNEOS_ROOT", tmp_path)
    return board_file


def test_unchanged_board_file_is_not_reported(tmp_path, monkeypatch):
    _point_qemu_at(tmp_path, monkeypatch, "esp32s3-qemu\n")
    qemu._board_file_changed_under_run("esp32s3-qemu")


def test_a_rewritten_board_file_exits_config(tmp_path, monkeypatch):
    _point_qemu_at(tmp_path, monkeypatch, "esp32s3-devkitc\n")
    with pytest.raises(SystemExit) as e:
        qemu._board_file_changed_under_run("esp32s3-qemu")
    assert e.value.code == qemu.EXIT_CONFIG


def test_a_deleted_board_file_exits_config(tmp_path, monkeypatch):
    """The exact shape of the observed race: the file is momentarily absent
    because a shell redirection truncated it before writing."""
    _point_qemu_at(tmp_path, monkeypatch, None)
    with pytest.raises(SystemExit) as e:
        qemu._board_file_changed_under_run("esp32s3-qemu")
    assert e.value.code == qemu.EXIT_CONFIG


def test_the_message_says_interference_not_firmware(tmp_path, monkeypatch,
                                                    capsys):
    """The whole point of the check is that the report does not read as a
    firmware failure."""
    _point_qemu_at(tmp_path, monkeypatch, "esp32s3-devkitc\n")
    with pytest.raises(SystemExit):
        qemu._board_file_changed_under_run("esp32s3-qemu")
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert "interference" in text
    assert "esp32s3-qemu" in text
    assert "esp32s3-devkitc" in text


def test_read_board_file_returns_none_when_absent(tmp_path, monkeypatch):
    _point_qemu_at(tmp_path, monkeypatch, None)
    assert qemu._read_board_file() is None


def test_read_board_file_ignores_trailing_lines(tmp_path, monkeypatch):
    _point_qemu_at(tmp_path, monkeypatch, "esp32s3-qemu\n# a stray comment\n")
    assert qemu._read_board_file() == "esp32s3-qemu"
