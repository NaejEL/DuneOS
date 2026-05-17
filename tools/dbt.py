#!/usr/bin/env python3
"""
Entry point for the DuneBuild Tool.

On first run (or when not inside the dbt venv), bootstraps a dedicated
virtualenv at tools/.dbt-venv/, installs tool dependencies, then
re-execs with the venv Python.  Subsequent runs are instant.
"""
import os
import sys
import subprocess
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent
_VENV_DIR  = _TOOLS_DIR / ".dbt-venv"

# pip package name → import module name (when they differ).
_DEP_MODULE = {
    "littlefs-python": "littlefs",
    "pyyaml": "yaml",
}

# All Python packages required by the dbt package itself.
_DEPS = [
    "littlefs-python",
    "esptool",
    "rich",
    "pyyaml",
    "textual",
]


def _venv_python() -> Path:
    if sys.platform == "win32":
        return _VENV_DIR / "Scripts" / "python.exe"
    return _VENV_DIR / "bin" / "python3"


def _in_dbt_venv() -> bool:
    # sys.executable.resolve() follows symlinks to the real binary, not the venv
    # copy. Use sys.prefix instead — it always equals the venv root when the venv
    # python is invoked directly, even without sourcing activate.sh.
    try:
        Path(sys.prefix).resolve().relative_to(_VENV_DIR.resolve())
        return True
    except ValueError:
        return False


def _deps_satisfied(python: Path) -> bool:
    """True if all _DEPS are importable in the venv — no network needed."""
    for dep in _DEPS:
        mod = _DEP_MODULE.get(dep, dep.replace("-", "_").split("[")[0])
        r = subprocess.run([str(python), "-c", f"import {mod}"],
                           capture_output=True)
        if r.returncode != 0:
            return False
    return True


def _bootstrap() -> None:
    python = _venv_python()
    if not python.exists():
        print("dbt: first run — creating tool venv…", flush=True)
        subprocess.check_call([sys.executable, "-m", "venv", str(_VENV_DIR)])

    if _deps_satisfied(python):
        return

    print("dbt: installing dependencies…", flush=True)
    result = subprocess.run(
        [str(python), "-m", "pip", "install"] + _DEPS,
    )
    if result.returncode != 0:
        sys.exit("dbt: dependency installation failed.")


if not _in_dbt_venv():
    _bootstrap()
    sys.exit(subprocess.run([str(_venv_python())] + sys.argv).returncode)

sys.path.insert(0, str(_TOOLS_DIR))
from dbt.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
