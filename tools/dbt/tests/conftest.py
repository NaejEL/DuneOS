"""Fixtures for the bspgen tests. The read guard lives in the root conftest.py."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="session")
def regenerated_root(tmp_path_factory):
    """A stand-in repo root, bspgen re-run over every tracked board.yaml."""
    root = tmp_path_factory.mktemp("regenerated")
    shutil.copyfile(REPO_ROOT / "sdkconfig.defaults", root / "sdkconfig.defaults")
    for yaml_path in sorted((REPO_ROOT / "boards").glob("*/board.yaml")):
        dest = root / "boards" / yaml_path.parent.name
        dest.mkdir(parents=True)
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "tools" / "duneos-bspgen.py"),
             str(yaml_path), "--out", str(dest / "board_config.h")],
            capture_output=True)
        if proc.returncode != 0:
            raise RuntimeError(f"bspgen rejected {yaml_path}:\n"
                               f"{proc.stderr.decode(errors='replace')}")
    return root
