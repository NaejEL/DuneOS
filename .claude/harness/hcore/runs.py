# Files of a run under .harness/runs/ of the target repository:
#   <skill>-<timestamp>.jsonl            cycle journal
#   <skill>-<timestamp>.state.json       counter state between two calls
#   ship-pr-<timestamp>.registry.jsonl   findings registry (ship-pr)
#   <skill>-<timestamp>.lock             inter-process lock of the run
# The whole directory is excluded from git (D3).
#
# Lock: every CLI command working on an existing run holds an exclusive OS
# lock on <run>.lock for its whole read -> modify -> write cycle (fcntl.flock
# on Linux/macOS, msvcrt.locking on Windows). The OS releases the lock when
# its holder dies, so an orphan lock never blocks the next command; the empty
# .lock file itself stays (harmless, excluded from git). Waiting longer than
# LOCK_TIMEOUT is an explicit stop (LockTimeout).

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from . import journal

if os.name == "nt":
    import msvcrt
else:
    import fcntl

RUNS_DIR = Path(".harness") / "runs"
LOCK_TIMEOUT = 120.0   # seconds
LOCK_POLL = 0.02
# Windows: os.replace fails with PermissionError while another process holds
# the destination open (antivirus, indexer, reader): retried a few times.
RETRY_REPLACE = os.name == "nt"
REPLACE_ATTEMPTS = 20
REPLACE_DELAY = 0.05


class LockTimeout(RuntimeError):
    """The run stayed locked by another command longer than the timeout."""


def create(root: Path, skill: str, state: Dict[str, Any], now: Optional[datetime] = None) -> Path:
    runs = Path(root) / RUNS_DIR
    runs.mkdir(parents=True, exist_ok=True)
    moment = now or datetime.now()
    path = runs / journal.journal_name(skill, moment)
    while path.exists():
        moment += timedelta(seconds=1)
        path = runs / journal.journal_name(skill, moment)
    path.touch()
    state = dict(state)
    state["skill"] = skill
    state["root"] = str(Path(root).resolve())
    state.setdefault("begun", {})
    save_state(path, state)
    return path


def _base(run: Path) -> str:
    name = Path(run).name
    if not name.endswith(".jsonl"):
        raise ValueError("unexpected run path: %s" % run)
    return str(Path(run).parent / name[: -len(".jsonl")])


def state_path(run: Path) -> Path:
    return Path(_base(run) + ".state.json")


def registry_path(run: Path) -> Path:
    return Path(_base(run) + ".registry.jsonl")


def lock_path(run: Path) -> Path:
    return Path(_base(run) + ".lock")


def _try_lock(fd: int) -> None:
    """Non-blocking exclusive lock; OSError if already held."""
    if os.name == "nt":
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fd: int) -> None:
    if os.name == "nt":
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def locked(run: Path, timeout: Optional[float] = None) -> Iterator[None]:
    """Exclusive inter-process lock of a run, held for the whole block
    (timeout: LOCK_TIMEOUT if None)."""
    with locked_file(lock_path(run), timeout, run):
        yield


@contextmanager
def locked_file(lock: Path, timeout: Optional[float] = None, what: Any = None) -> Iterator[None]:
    """Exclusive inter-process lock held on the file `lock` (created if
    missing), for any read -> modify -> write cycle of a shared file."""
    run = what if what is not None else lock
    timeout = LOCK_TIMEOUT if timeout is None else timeout
    fd = os.open(str(lock), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                _try_lock(fd)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise LockTimeout("%s locked by another harness command for more than %gs"
                                      % (run, timeout))
                time.sleep(LOCK_POLL)
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


def write_atomic(path: Path, text: str) -> None:
    """Writes a temporary file then os.replace, retried on PermissionError under Windows."""
    path = Path(path)
    tmp = path.with_name("%s.%d.tmp" % (path.name, os.getpid()))
    with open(str(tmp), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    attempts = REPLACE_ATTEMPTS if RETRY_REPLACE else 1
    for attempt in range(1, attempts + 1):
        try:
            os.replace(str(tmp), str(path))
            return
        except PermissionError:
            if attempt == attempts:
                try:
                    os.unlink(str(tmp))
                except OSError:
                    pass
                raise
            time.sleep(REPLACE_DELAY)


def load_state(run: Path) -> Dict[str, Any]:
    path = state_path(run)
    if not path.exists():
        raise FileNotFoundError("unknown run (state missing): %s" % run)
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(run: Path, state: Dict[str, Any]) -> None:
    write_atomic(state_path(run), json.dumps(state, ensure_ascii=False, indent=1))


def mark_begin(state: Dict[str, Any], slot: str, clock=time.time) -> None:
    """Timestamp taken by the core before a call, if the usage block is missing."""
    state.setdefault("begun", {})[slot] = clock()


def duration_ms(state: Dict[str, Any], slot: str, given: Optional[int], clock=time.time) -> int:
    if given is not None:
        if given < 0:
            raise ValueError("--duration-ms must be >= 0")
        state.get("begun", {}).pop(slot, None)
        return int(given)
    begun = state.get("begun", {}).pop(slot, None)
    if begun is None:
        raise ValueError("unknown duration for %s: pass --duration-ms, call 'begin' before the agent, "
                         "or complete the entry later with 'journal amend'" % slot)
    return max(0, int(round((clock() - begun) * 1000)))


def display(path: Path) -> str:
    """Path relative to the current directory if possible, with '/' separators."""
    try:
        return Path(os.path.relpath(str(path))).as_posix()
    except ValueError:
        return Path(path).as_posix()
