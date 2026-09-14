"""LEG-31: an i2c: list without bus 0 must not reach the compiler.

bspgen emitted CONFIG_DUNEOS_DRV_I2C on the presence of the list and
DUNEOS_I2C<yaml id>_* from the ids in it, while the kernel I2C stack is
single-bus and names bus 0 literally. A board numbering its only bus 1 was
therefore generated cleanly and failed at compile time on three undefined
macros. validate() rejects it instead.
"""

import importlib.util
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_bspgen():
    spec = importlib.util.spec_from_file_location(
        "duneos_bspgen", REPO_ROOT / "tools" / "duneos-bspgen.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BSPGEN = _load_bspgen()

BASE = {"name": "testboard", "cpu": "esp32s3", "flash_size_mb": 8}
FAKE_YAML = Path("boards/testboard/board.yaml")


def _with_i2c(*buses):
    board = dict(BASE)
    if buses:
        board["i2c"] = [dict(b) for b in buses]
    return board


def test_a_bus_numbered_one_is_refused(capsys):
    with pytest.raises(SystemExit):
        BSPGEN.validate(_with_i2c({"id": 1, "sda_pin": 2, "scl_pin": 1}), FAKE_YAML)
    err = capsys.readouterr().err
    assert "testboard" in err
    assert "id: 0" in err


@pytest.mark.parametrize("buses", [
    ({"id": 0, "sda_pin": 2, "scl_pin": 1},),
    ({"id": 0, "sda_pin": 2, "scl_pin": 1}, {"id": 1, "sda_pin": 4, "scl_pin": 5}),
    (),
])
def test_a_board_carrying_bus_zero_or_no_bus_passes(buses):
    BSPGEN.validate(_with_i2c(*buses), FAKE_YAML)


def _tracked_boards():
    return sorted((REPO_ROOT / "boards").glob("*/board.yaml"))


@pytest.mark.parametrize("yaml_path", _tracked_boards(), ids=lambda p: p.parent.name)
def test_the_new_rule_rejects_no_existing_board(yaml_path):
    board = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))["board"]
    BSPGEN.validate(board, yaml_path)


def test_a_board_declaring_logic_passes_validate():
    board = dict(BASE)
    board["logic"] = None
    BSPGEN.validate(board, FAKE_YAML)
    assert "logic" in BSPGEN.KNOWN_BOARD_KEYS
