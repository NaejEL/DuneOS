"""Every build/flash entry point must run the stale-sdkconfig guard (SPEC-leg-37).

Testing `sdkconfig_check.enforce` in isolation proves the guard works, not that
it is reached. The first review of LEG-37 found the TUI worker — the path
`python tools/dbt.py` with no argument lands on — building and flashing without
it: a guard covering two of three entry points is worse than none, because it
licenses trusting a path it does not protect.

So this file walks the AST of every module under tools/dbt, finds each function
that starts an `idf.py build`/`flash` or calls a toolchain plugin's
`build_kernel`/`flash_kernel`, and asserts the same function also reaches the
guard. A new entry point added without the check fails here, by name.
"""

import ast
from pathlib import Path

import pytest

DBT_DIR = Path(__file__).resolve().parents[1]

GUARD_NAMES = {"enforce", "enforce_sdkconfig", "check_build_sdkconfig",
               "_check_sdkconfig"}

# Functions that hand off to a `dbt` subprocess which runs its own guard, or
# that neither build nor flash the kernel image. Each is a deliberate decision,
# not an oversight; adding to this set is the thing a reviewer must argue with.
EXEMPT: dict = {}


def _iter_functions():
    for path in sorted(DBT_DIR.rglob("*.py")):
        if "tests" in path.parts or "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield path, node


def _called_names(node) -> set:
    """Every callee name mentioned in this function, attribute tail included."""
    names = set()
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        fn = sub.func
        if isinstance(fn, ast.Name):
            names.add(fn.id)
        elif isinstance(fn, ast.Attribute):
            names.add(fn.attr)
    return names


def _string_args(node) -> list:
    out = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            for arg in sub.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    out.append(arg.value)
                elif isinstance(arg, ast.JoinedStr):
                    out.extend(v.value for v in arg.values
                               if isinstance(v, ast.Constant)
                               and isinstance(v.value, str))
    return out


def _is_entry_point(node) -> bool:
    called = _called_names(node)
    if called & {"build_kernel", "flash_kernel"}:
        return True
    # The TUI builds its own idf.py argv rather than going through a plugin.
    if "_idf_cmd" in called:
        for s in _string_args(node):
            words = s.replace("-", " ").split()
            if "build" in words or "flash" in words:
                return True
    return False


def _entry_points():
    found = []
    for path, node in _iter_functions():
        if _is_entry_point(node):
            found.append((path.relative_to(DBT_DIR).as_posix(), node))
    return found


def test_the_scan_finds_the_known_entry_points():
    """A scan that silently matches nothing would make every assertion below
    vacuous — pin the sites this repo is known to have."""
    names = {f"{rel}::{node.name}" for rel, node in _entry_points()}
    assert "kernel.py::cmd_flash_kernel" in names
    assert "qemu.py::cmd_qemu" in names
    assert "tui.py::_worker_flash_kernel" in names


@pytest.mark.parametrize("rel,name", [
    (rel, node.name) for rel, node in _entry_points()
])
def test_every_build_or_flash_entry_point_calls_the_guard(rel, name):
    node = next(n for r, n in _entry_points() if r == rel and n.name == name)
    if EXEMPT.get(f"{rel}::{name}"):
        pytest.skip(EXEMPT[f"{rel}::{name}"])
    assert _called_names(node) & GUARD_NAMES, (
        f"{rel}::{name} builds or flashes the kernel without calling the "
        f"stale-sdkconfig guard ({' / '.join(sorted(GUARD_NAMES))})."
    )


def test_the_guard_runs_before_the_build_in_each_entry_point():
    """Order matters: a check after `idf.py build` reports a stale config the
    build already consumed."""
    for rel, node in _entry_points():
        guard_line = build_line = None
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            fn = sub.func
            nm = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
            if nm in GUARD_NAMES and guard_line is None:
                guard_line = sub.lineno
            if nm in {"build_kernel", "flash_kernel"} and build_line is None:
                build_line = sub.lineno
            if nm == "_idf_cmd" and build_line is None:
                build_line = sub.lineno
        if build_line is not None:
            assert guard_line is not None and guard_line < build_line, (
                f"{rel}::{node.name} runs the guard after the build")
