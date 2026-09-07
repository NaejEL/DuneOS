"""Read guard (LEG-38): a test that opens a gitignored build artefact fails.

Existence is not the signal — the four recorded instances were all green on the
machine that wrote the artefact and vacuous everywhere else. Ignore status is,
and it comes from git rather than from a pattern list here.

Only builtins.open / io.open are intercepted; os.open, mmap and subprocesses are
not, and no current test uses them to reach an artefact.
"""

import builtins
import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent

# A hook on open() that reddens the suite for something no test did is worse
# than no hook: people learn to bypass it. The deny set is build artefacts under
# the repo — never bytecode, never a tool's own cache, never anything outside.
_EXEMPT_SUFFIXES = frozenset({".pyc", ".pyo"})
_EXEMPT_DIRS = frozenset({"__pycache__", "managed_components", "node_modules"})

_ADVICE = ("generated files are absent on a clean checkout and in CI; build what "
           "you assert against under tmp_path")


class _Unclassifiable(Exception):
    """This one path could not be classified. Never cached, never sticky:
    `git check-ignore` fatals per path (exit 128 inside a submodule), and a
    sticky error turns the guard into a session-wide skip machine."""


def _submodule_prefixes():
    modules = REPO_ROOT / ".gitmodules"
    prefixes = []
    try:
        text = modules.read_text(encoding="utf-8")
    except OSError:
        return ()
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "path":
            prefixes.append(tuple(Path(value.strip()).parts))
    return tuple(prefixes)


# Read at import time, before the guard is installed: reading it lazily from
# inside candidate() would recurse through the wrapped open().
_SUBMODULES = _submodule_prefixes()


class _Guard:
    def __init__(self):
        self._ignored = {}
        self._signature = None
        self._rule_files = None
        self._busy = False
        self.node = None

    def candidate(self, file, mode):
        if not str(mode).startswith("r"):
            return None
        try:
            # resolve(), not abspath(): reached through a symlink the path would
            # miss REPO_ROOT and the guard would silently classify nothing.
            path = Path(os.fsdecode(file)).resolve()
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
        # A submodule has its own ignore rules; `git check-ignore` fatals on
        # such a path rather than answering, so it is out of scope, not unknown.
        for prefix in _SUBMODULES:
            if rel.parts[:len(prefix)] == prefix:
                return None
        return rel

    def is_ignored(self, rel):
        signature = self._ignore_rules_signature()
        if signature != self._signature:
            self._ignored.clear()
            self._signature = signature
        key = str(rel)
        if key not in self._ignored:
            self._ignored[key] = self._check_ignore(key)
        return self._ignored[key]

    def _ignore_rule_files(self):
        if self._rule_files is None:
            files = [REPO_ROOT / ".gitignore", REPO_ROOT / ".git" / "info" / "exclude"]
            for line in self._git(["ls-files", "--", ".gitignore", "*/.gitignore"]):
                files.append(REPO_ROOT / line)
            for line in self._git(["config", "--get", "core.excludesFile"]):
                files.append(Path(os.path.expanduser(line)))
            self._rule_files = tuple(dict.fromkeys(files))
        return self._rule_files

    @staticmethod
    def _git(args):
        try:
            proc = subprocess.run(["git", "-C", str(REPO_ROOT)] + args,
                                  capture_output=True)
        except OSError:
            return []
        return [l.strip() for l in proc.stdout.decode(errors="replace").splitlines()
                if l.strip()]

    def _ignore_rules_signature(self):
        # Recomputed on every lookup, which is what keeps the cache from
        # outliving the rules it was built from. A .gitignore created after the
        # first lookup is not in the list, so it is not seen.
        out = []
        for path in self._ignore_rule_files():
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
            raise _Unclassifiable(f"git unavailable ({exc})") from None
        if proc.returncode in (0, 1):
            return proc.returncode == 0
        raise _Unclassifiable(proc.stderr.decode(errors="replace").strip()
                              or f"git check-ignore exited {proc.returncode}")

    def check(self, file, mode):
        if self._busy:
            return
        rel = self.candidate(file, mode)
        if rel is None:
            return
        self._busy = True
        try:
            ignored = self.is_ignored(rel)
        except _Unclassifiable as exc:
            pytest.skip(f"read guard cannot classify '{rel}': {exc}")
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


def pytest_runtest_teardown(item):
    _GUARD.node = None


@pytest.fixture(scope="session")
def read_guard():
    """This module, so its own tests need not guess how pytest imported it."""
    return sys.modules[__name__]
