"""Tests for the read guard in conftest.py (LEG-38).

The guard exists because the artefacts it denies are present on the machine
where the test is written, so the decisive properties are that it classifies by
ignore status rather than existence, and that it stays away from the caches a
test run legitimately touches.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
GENERATED = REPO_ROOT / "boards" / "m5stack-cardputer" / "sdkconfig.board"


def test_a_generated_board_artefact_is_denied(read_guard):
    rel = read_guard._GUARD.candidate(GENERATED, "r")
    assert rel is not None
    assert read_guard._GUARD.is_ignored(rel) is True


def test_the_tracked_yaml_next_to_it_is_allowed(read_guard):
    rel = read_guard._GUARD.candidate(
        REPO_ROOT / "boards" / "m5stack-cardputer" / "board.yaml", "r")
    assert read_guard._GUARD.is_ignored(rel) is False


def test_denial_does_not_depend_on_the_file_existing(read_guard):
    """Existence is what made all four recorded instances invisible locally."""
    absent = REPO_ROOT / "boards" / "no-such-board" / "sdkconfig.board"
    assert not absent.exists()
    assert read_guard._GUARD.is_ignored(read_guard._GUARD.candidate(absent, "r")) is True


@pytest.mark.parametrize("path", [
    Path(__file__).parent / "__pycache__" / "x.pyc",
    REPO_ROOT / ".pytest_cache" / "v" / "cache" / "lastfailed",
    REPO_ROOT / "tools" / ".dbt-venv" / "pyvenv.cfg",
    Path("/etc/hostname"),
])
def test_caches_and_paths_outside_the_repo_are_never_classified(read_guard, path):
    assert read_guard._GUARD.candidate(path, "r") is None


def test_writes_are_not_classified(read_guard):
    assert read_guard._GUARD.candidate(GENERATED, "w") is None


def test_the_guard_is_installed_during_the_run(read_guard):
    import builtins
    import io
    assert builtins.open is read_guard._guarded_open
    assert io.open is read_guard._guarded_open


def test_opening_a_denied_path_fails_the_test_that_did_it():
    with pytest.raises(BaseException) as excinfo:
        open(GENERATED, encoding="utf-8")
    message = str(excinfo.value)
    assert "LEG-38" in message
    assert "boards/m5stack-cardputer/sdkconfig.board" in message.replace("\\", "/")
    assert "test_opening_a_denied_path_fails_the_test_that_did_it" in message
