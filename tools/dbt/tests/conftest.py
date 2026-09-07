"""Read guard (LEG-38) plus the regeneration fixture the bspgen tests assert against.

A test that opens a gitignored build artefact fails, on every machine, whether or
not the artefact happens to exist there. The four recorded instances of this
defect were all invisible locally: the file was present, the assertion ran, and
the same test verified nothing in CI. Existence is therefore not the signal —
ignore status is, and it comes from git rather than from a pattern list here.
"""

import builtins
import io
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

# A hook on open() that reddens the suite for something no test did is worse than
# no hook: people learn to bypass it. The deny set is build artefacts under the
# repo only — never bytecode, never a tool's own cache, never anything outside.
_EXEMPT_SUFFIXES = frozenset({".pyc", ".pyo"})
_EXEMPT_DIRS = frozenset({"__pycache__", "managed_components", "node_modules"})

_ADVICE = ("generated files are absent on a clean checkout and in CI; build what "
           "you assert against under tmp_path")


class _GitUnavailable(Exception):
    pass


class _Guard:
    def __init__(self):
        self._ignored = {}
        self._signature = None
        self._git_error = None
        self._busy = False
        self.node = None

    def candidate(self, file, mode):
        if any(c in mode for c in "wxa+"):
            return None
        try:
            path = Path(os.path.abspath(os.fsdecode(file)))
        except (TypeError, ValueError, OSError):
            return None
        if not path.is_relative_to(REPO_ROOT):
            return None
        rel = path.relative_to(REPO_ROOT)
        if path.suffix in _EXEMPT_SUFFIXES:
            return None
        for part in rel.parts[:-1]:
            if part.startswith(".") or part in _EXEMPT_DIRS:
                return None
        return rel

    def is_ignored(self, rel):
        signature = self._ignore_rules_signature()
        if signature != self._signature:
            self._ignored.clear()
            self._git_error = None
            self._signature = signature
        if self._git_error:
            raise _GitUnavailable(self._git_error)
        key = str(rel)
        if key not in self._ignored:
            self._ignored[key] = self._check_ignore(key)
        return self._ignored[key]

    def _ignore_rules_signature(self):
        # Cheap enough to recompute on every lookup, which is what keeps a cache
        # from outliving the rules it was built from. Nested .gitignore files
        # live under vendored trees the deny set already excludes.
        out = []
        for path in (REPO_ROOT / ".gitignore", REPO_ROOT / ".git" / "info" / "exclude"):
            try:
                st = path.stat()
            except OSError:
                out.append(None)
            else:
                out.append((st.st_mtime_ns, st.st_size))
        return tuple(out)

    def _check_ignore(self, rel):
        try:
            proc = subprocess.run(
                ["git", "-C", str(REPO_ROOT), "check-ignore", "-q", "--", rel],
                capture_output=True)
        except OSError as exc:
            self._git_error = str(exc)
            raise _GitUnavailable(self._git_error) from None
        if proc.returncode in (0, 1):
            return proc.returncode == 0
        self._git_error = (proc.stderr.decode(errors="replace").strip()
                           or f"git check-ignore exited {proc.returncode}")
        raise _GitUnavailable(self._git_error)

    def check(self, file, mode):
        if self._busy:
            return
        rel = self.candidate(file, mode)
        if rel is None:
            return
        self._busy = True
        try:
            ignored = self.is_ignored(rel)
        except _GitUnavailable as exc:
            pytest.skip(f"read guard cannot classify '{rel}': git unavailable ({exc})")
        finally:
            self._busy = False
        if ignored:
            who = self.node or "collection"
            pytest.fail(f"LEG-38: {who} opened the gitignored build artefact "
                        f"'{rel}' — {_ADVICE}", pytrace=False)


_GUARD = _Guard()
_REAL_OPEN = builtins.open


def _guarded_open(file, mode="r", *args, **kwargs):
    _GUARD.check(file, mode)
    return _REAL_OPEN(file, mode, *args, **kwargs)


def pytest_configure(config):
    builtins.open = _guarded_open
    io.open = _guarded_open


def pytest_unconfigure(config):
    builtins.open = _REAL_OPEN
    io.open = _REAL_OPEN


def pytest_runtest_setup(item):
    _GUARD.node = item.nodeid


@pytest.fixture(scope="session")
def read_guard():
    """This module, so its own tests need not guess how pytest imported it."""
    return sys.modules[__name__]


@pytest.fixture(scope="session")
def regenerated_root(tmp_path_factory):
    """A stand-in repo root whose bspgen artefacts were regenerated from the
    tracked board.yaml files.

    The committed artefacts are gitignored, so an assertion about generated
    content has to build its own subject; running the tool end to end is also
    what distinguishes this from the pure generate_*() tests next to it.
    """
    root = tmp_path_factory.mktemp("regenerated")
    shutil.copyfile(REPO_ROOT / "sdkconfig.defaults", root / "sdkconfig.defaults")
    for yaml_path in sorted((REPO_ROOT / "boards").glob("*/board.yaml")):
        dest = root / "boards" / yaml_path.parent.name
        dest.mkdir(parents=True)
        subprocess.run(
            [sys.executable, str(REPO_ROOT / "tools" / "duneos-bspgen.py"),
             str(yaml_path), "--out", str(dest / "board_config.h")],
            check=True, capture_output=True)
    return root
