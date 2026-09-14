"""LEG-31, the general case: what the fragment enables, the header must define.

The per-board check is only worth its runtime if it can fail, so the negative
control lives beside it: the board LEG-31 describes — one I2C bus numbered 1 —
is built here in the test body, pushed past validate(), and must come back
naming the DUNEOS_I2C0_* macros the kernel would not have found.

Nothing here reads a generated file from the tree: every header and fragment is
produced into tmp_path from the tracked board.yaml.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from . import kernel_macros as km

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_bspgen():
    spec = importlib.util.spec_from_file_location(
        "duneos_bspgen", REPO_ROOT / "tools" / "duneos-bspgen.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BSPGEN = _load_bspgen()


def _tracked_boards():
    return sorted((REPO_ROOT / "boards").glob("*/board.yaml"))


@pytest.mark.parametrize("yaml_path", _tracked_boards(), ids=lambda p: p.parent.name)
def test_every_macro_the_enabled_drivers_reference_is_defined(yaml_path, tmp_path):
    board = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))["board"]
    out = tmp_path / yaml_path.parent.name
    out.mkdir()
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "duneos-bspgen.py"),
         str(yaml_path), "--out", str(out / "board_config.h")],
        capture_output=True)
    assert proc.returncode == 0, proc.stderr.decode(errors="replace")

    missing = km.missing_macros(board["cpu"],
                               (out / "sdkconfig.board").read_text(encoding="utf-8"),
                               out / "board_config.h")
    assert missing == set(), (
        f"{yaml_path.parent.name} enables drivers referencing undefined board "
        f"macros: {sorted(missing)}")


def test_the_check_fails_on_a_bus_numbered_one(tmp_path):
    """Negative control. Without this the test above is a green light with no
    lamp behind it — an over-broad subtraction would pass all eight boards."""
    board = {"name": "synthetic-i2c1", "cpu": "esp32s3", "flash_size_mb": 8,
             "i2c": [{"id": 1, "sda_pin": 2, "scl_pin": 1, "freq_hz": 400000}]}
    with pytest.raises(SystemExit):
        BSPGEN.validate(board, Path("boards/synthetic-i2c1/board.yaml"))

    header = tmp_path / "board_config.h"
    header.write_text(BSPGEN.generate(board), encoding="utf-8")
    fragment = BSPGEN.generate_sdkconfig_board(board)
    assert "CONFIG_DUNEOS_DRV_I2C=y" in fragment

    missing = km.missing_macros("esp32s3", fragment, header)
    assert {"DUNEOS_I2C0_SDA_PIN", "DUNEOS_I2C0_SCL_PIN",
            "DUNEOS_I2C0_FREQ_HZ"} <= missing


def test_the_source_scan_reaches_the_guarded_drivers():
    """kernel_sources() returning nothing would make every assertion above
    vacuous, and it would look exactly the same from outside."""
    gated = {p.name for p, gates in km.kernel_sources("esp32s3").items()
             if all(g for g in gates)}
    assert {"drv_i2c.c", "i2c_bus.c", "drv_logic.c", "hal_logic.c"} <= gated
