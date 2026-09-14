"""LEG-32 and LEG-33B: which drivers a board's fragment turns on.

Two symbols were emitted for reasons no board.yaml expressed. LOGIC sat in the
same literal list as NULL/UART/KLOG/GPIO, against a `default n` in Kconfig, so
every board carried /dev/logic0. WiFi was the one block read opt-out
(`board.get("wifi", True)`), so an ESP32-P4 — which has no radio — claimed one.

Both are now presence-tested like every other peripheral, and the pinning tests
here make the next unconditional addition a deliberate one.
"""

import importlib.util
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]

BASE = {"name": "testboard", "cpu": "esp32s3", "flash_size_mb": 8}

UNCONDITIONAL = {"CONFIG_DUNEOS_DRV_NULL", "CONFIG_DUNEOS_DRV_UART",
                 "CONFIG_DUNEOS_DRV_KLOG", "CONFIG_DUNEOS_DRV_GPIO"}

RADIO_BOARDS = {"m5stack-cardputer", "esp32s3-devkitc",
                "lilygo-t-embed-cc1101", "kincony-A16"}


def _load_bspgen():
    spec = importlib.util.spec_from_file_location(
        "duneos_bspgen", REPO_ROOT / "tools" / "duneos-bspgen.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BSPGEN = _load_bspgen()


def _emitted(board):
    return {line.split("=", 1)[0]
            for line in BSPGEN.generate_sdkconfig_board(board).splitlines()
            if line.startswith("CONFIG_DUNEOS_DRV_") and line.endswith("=y")}


def _tracked_boards():
    return sorted((REPO_ROOT / "boards").glob("*/board.yaml"))


def _board_dict(yaml_path):
    return yaml.safe_load(yaml_path.read_text(encoding="utf-8"))["board"]


# ---------------------------------------------------------------- LEG-32 ----

def test_logic_is_emitted_only_for_a_board_declaring_it():
    assert "CONFIG_DUNEOS_DRV_LOGIC" not in _emitted(dict(BASE))
    declared = dict(BASE)
    declared["logic"] = None
    assert "CONFIG_DUNEOS_DRV_LOGIC" in _emitted(declared)


def test_exactly_one_tracked_board_enables_logic():
    with_logic = {p.parent.name for p in _tracked_boards()
                  if "CONFIG_DUNEOS_DRV_LOGIC" in _emitted(_board_dict(p))}
    assert with_logic == {"m5stack-cardputer"}


def test_only_four_driver_symbols_are_emitted_for_every_board():
    per_board = [_emitted(_board_dict(p)) for p in _tracked_boards()]
    assert set.intersection(*per_board) == UNCONDITIONAL


# ---------------------------------------------------------------- Kconfig ---

def _kconfig_defaults():
    text = (REPO_ROOT / "kernel" / "duneos_kernel" / "Kconfig").read_text(encoding="utf-8")
    defaults = {}
    current = None
    for line in text.splitlines():
        m = re.match(r"^config\s+(DUNEOS_\w+)", line)
        if m:
            current = m.group(1)
            continue
        m = re.match(r"^\s+default\s+(\S+)", line)
        if m and current:
            defaults["CONFIG_" + current] = m.group(1)
            current = None
    return defaults


def test_every_emitted_driver_symbol_is_declared_in_kconfig():
    declared = _kconfig_defaults()
    for path in _tracked_boards():
        for symbol in _emitted(_board_dict(path)):
            assert symbol in declared, f"{path.parent.name} emits undeclared {symbol}"


def test_every_unconditional_symbol_defaults_to_y():
    """A `default n` symbol emitted for every board is a contradiction: one of
    the two says the driver is optional and the other says it is platform."""
    declared = _kconfig_defaults()
    for symbol in sorted(UNCONDITIONAL):
        assert declared[symbol] == "y", f"{symbol} is emitted always but is not default y"


# ---------------------------------------------------------------- LEG-33B ---

@pytest.mark.parametrize("wifi,expected", [(True, True), (False, False)])
def test_wifi_follows_the_declaration(wifi, expected):
    board = dict(BASE)
    board["wifi"] = wifi
    assert ("CONFIG_DUNEOS_DRV_WIFI" in _emitted(board)) is expected


def test_wifi_is_absent_when_the_key_is_absent():
    assert "CONFIG_DUNEOS_DRV_WIFI" not in _emitted(dict(BASE))


def test_wifi_per_tracked_board_matches_the_declaration():
    for path in _tracked_boards():
        board = _board_dict(path)
        assert ("CONFIG_DUNEOS_DRV_WIFI" in _emitted(board)) is (board.get("wifi") is True), \
            path.parent.name


def test_the_boards_that_carry_a_radio_are_the_four_expected_ones():
    """The flip is a no-op for these; it only takes the symbol away from the
    two paper RISC-V boards, one of which has no radio at all."""
    enabled = {p.parent.name for p in _tracked_boards()
               if "CONFIG_DUNEOS_DRV_WIFI" in _emitted(_board_dict(p))}
    assert enabled == RADIO_BOARDS
