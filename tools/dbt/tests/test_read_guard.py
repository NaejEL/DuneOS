"""Tests for the read guard in the root conftest.py (LEG-38)."""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
GENERATED = REPO_ROOT / "boards" / "m5stack-cardputer" / "sdkconfig.board"
TRACKED = REPO_ROOT / "boards" / "m5stack-cardputer" / "board.yaml"


def test_a_generated_board_artefact_is_denied(read_guard):
    rel = read_guard._GUARD.candidate(GENERATED, "r")
    assert rel is not None
    assert read_guard._GUARD.is_ignored(rel) is True


def test_the_tracked_yaml_next_to_it_is_allowed(read_guard):
    rel = read_guard._GUARD.candidate(TRACKED, "r")
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


@pytest.mark.parametrize("mode", ["r", "rb", "r+", "rb+", "r+b"])
def test_every_read_mode_is_classified(read_guard, mode):
    assert read_guard._GUARD.candidate(GENERATED, mode) is not None


@pytest.mark.parametrize("mode", ["w", "wb", "x", "a", "w+"])
def test_write_modes_are_not(read_guard, mode):
    assert read_guard._GUARD.candidate(GENERATED, mode) is None


def test_submodule_paths_are_out_of_scope(read_guard):
    """`git check-ignore` exits 128 inside a submodule instead of answering."""
    for path in (REPO_ROOT / "third_party" / "cjson" / "README.md",
                 REPO_ROOT / "third_party" / "littlefs" / "lfs.c"):
        assert read_guard._GUARD.candidate(path, "r") is None


def test_reading_inside_a_submodule_disturbs_no_other_path(read_guard):
    """The regression: a per-path git failure used to poison the whole session,
    turning every later test into a skip while the collected-count floor held."""
    readme = REPO_ROOT / "third_party" / "cjson" / "README.md"
    if readme.exists():
        with open(readme, encoding="utf-8") as f:
            f.read(1)
    assert read_guard._GUARD.is_ignored(
        read_guard._GUARD.candidate(GENERATED, "r")) is True
    assert read_guard._GUARD.is_ignored(
        read_guard._GUARD.candidate(TRACKED, "r")) is False


def test_a_failed_classification_is_not_remembered(read_guard, monkeypatch):
    guard = read_guard._GUARD
    calls = []
    real = guard._check_ignore

    def flaky(rel):
        calls.append(rel)
        if len(calls) == 1:
            raise read_guard._Unclassifiable("simulated per-path git failure")
        return real(rel)

    monkeypatch.setattr(guard, "_check_ignore", flaky)
    rel = guard.candidate(REPO_ROOT / "boards" / "probe" / "sdkconfig.board", "r")
    with pytest.raises(read_guard._Unclassifiable):
        guard.is_ignored(rel)
    assert guard.is_ignored(rel) is True


@pytest.mark.parametrize("reader", [
    lambda p: p.read_text(encoding="utf-8"),
    lambda p: p.read_bytes(),
    lambda p: p.open(encoding="utf-8").close(),
])
def test_pathlib_readers_are_intercepted_too(reader):
    """The idiom of all four recorded instances and of every rebuilt assertion.
    pathlib goes through io.open, which is a different object from builtins.open."""
    with pytest.raises(BaseException, match="LEG-38"):
        reader(GENERATED)


def test_an_empty_submodule_path_cannot_exempt_the_whole_repo(read_guard):
    """`path =` or `path = .` yields an empty prefix, which prefix-matches every
    path in the repo: the guard would classify nothing and stay green."""
    assert read_guard._parse_submodule_paths(
        '[submodule "x"]\n\tpath =\n[submodule "y"]\n\tpath = .\n') == ()
    assert read_guard._parse_submodule_paths(
        '[submodule "z"]\n\tpath = third_party/cjson\n') == (("third_party", "cjson"),)


def test_a_transient_git_failure_does_not_narrow_the_signature(read_guard, monkeypatch):
    guard = read_guard._GUARD
    monkeypatch.setattr(guard, "_rule_files", None)
    monkeypatch.setattr(guard, "_git", lambda args, ok: None)
    assert len(guard._ignore_rule_files()) == 2
    assert guard._rule_files is None

    monkeypatch.undo()
    monkeypatch.setattr(guard, "_rule_files", None)
    assert REPO_ROOT / "tests" / "host" / ".gitignore" in guard._ignore_rule_files()


def test_the_nested_gitignore_files_are_in_the_cache_signature(read_guard):
    """tests/host/.gitignore is tracked and governs real build artefacts, so a
    cache that outlives an edit to it would answer from stale rules."""
    files = read_guard._GUARD._ignore_rule_files()
    assert REPO_ROOT / "tests" / "host" / ".gitignore" in files
    assert REPO_ROOT / ".gitignore" in files


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
