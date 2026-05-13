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

# All Python packages required by the dbt package itself.
_DEPS = [
    "littlefs-python",
    "esptool",
]


def _venv_python() -> Path:
    if sys.platform == "win32":
        return _VENV_DIR / "Scripts" / "python.exe"
    return _VENV_DIR / "bin" / "python3"


def _in_dbt_venv() -> bool:
    exe = Path(sys.executable).resolve()
    venv = _VENV_DIR.resolve()
    try:
        exe.relative_to(venv)
        return True
    except ValueError:
        return False


def _bootstrap() -> None:
    python = _venv_python()
    if not python.exists():
        print("dbt: first run — creating tool venv…", flush=True)
        subprocess.check_call([sys.executable, "-m", "venv", str(_VENV_DIR)])

    # pip is idempotent: already-installed packages resolve in <1 s.
    result = subprocess.run(
        [str(python), "-m", "pip", "install", "--quiet"] + _DEPS,
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
