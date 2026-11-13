"""Tests for psram_kernel_exec (ROADMAP 4bis / PSRAM-01).

On an OPI PSRAM board, bspgen copies the kernel's code and rodata into PSRAM by
default (CONFIG_SPIRAM_FETCH_INSTRUCTIONS/RODATA=y). That copy is only required
when an OPI flash and an OPI PSRAM contend for the one MSPI; a DIO/QIO-flash
board runs markedly faster from the flash instruction cache. `psram_kernel_exec:
flash` turns the copy off. Measured 4.0s -> 1.16s per app load on the T-Embed.

The decisive properties: a board that says nothing keeps today's behaviour (so
no board changes silently), 'flash' flips exactly the two SPIRAM exec symbols
and nothing else, the key is meaningful only on OPI PSRAM, and a bad value or a
'flash' on a non-OPI board is refused by name.
"""

import importlib.util
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]

FETCH = "CONFIG_SPIRAM_FETCH_INSTRUCTIONS"
RODATA = "CONFIG_SPIRAM_RODATA"


def _load_bspgen():
    spec = importlib.util.spec_from_file_location(
        "duneos_bspgen", REPO_ROOT / "tools" / "duneos-bspgen.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BSPGEN = _load_bspgen()

# An OPI PSRAM board — the only topology on which the two exec symbols exist.
OPI_BOARD = {
    "name": "testboard",
    "cpu": "esp32s3",
    "flash_size_mb": 16,
    "psram_size_mb": 8,
    "psram_type": "opi",
    "uart": [{"id": 0, "tx_pin": 43, "rx_pin": 44, "default_baud": 115200}],
}


def _board_yaml(name):
    with open(REPO_ROOT / "boards" / name / "board.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)["board"]


# --- default: no key keeps today's behaviour (copy into PSRAM) ---------------

def test_opi_default_copies_into_psram():
    frag = BSPGEN.generate_sdkconfig_board(dict(OPI_BOARD))
    assert f"{FETCH}=y" in frag
    assert f"{RODATA}=y" in frag


def test_explicit_psram_is_the_same_as_the_default():
    board = dict(OPI_BOARD)
    board["psram_kernel_exec"] = "psram"
    assert BSPGEN.generate_sdkconfig_board(board) == \
        BSPGEN.generate_sdkconfig_board(dict(OPI_BOARD))


# --- flash: runs from the flash cache ----------------------------------------

def test_flash_turns_the_copy_off():
    board = dict(OPI_BOARD)
    board["psram_kernel_exec"] = "flash"
    frag = BSPGEN.generate_sdkconfig_board(board)
    assert f"{FETCH}=n" in frag
    assert f"{RODATA}=n" in frag
    assert f"{FETCH}=y" not in frag
    assert f"{RODATA}=y" not in frag


def test_flash_changes_only_the_two_exec_symbols():
    default = BSPGEN.generate_sdkconfig_board(dict(OPI_BOARD)).splitlines()
    board = dict(OPI_BOARD)
    board["psram_kernel_exec"] = "flash"
    flash = BSPGEN.generate_sdkconfig_board(board).splitlines()
    changed = [l for l in default if l not in flash]
    assert changed == [f"{FETCH}=y", f"{RODATA}=y"]
    added = [l for l in flash if l not in default]
    assert added == [f"{FETCH}=n", f"{RODATA}=n"]


def test_case_insensitive():
    board = dict(OPI_BOARD)
    board["psram_kernel_exec"] = "FLASH"
    assert f"{FETCH}=n" in BSPGEN.generate_sdkconfig_board(board)


# --- the key is meaningful only on OPI PSRAM ---------------------------------

def test_no_psram_has_no_exec_symbols_regardless():
    board = dict(OPI_BOARD)
    board["psram_size_mb"] = 0
    frag = BSPGEN.generate_sdkconfig_board(board)
    assert "CONFIG_SPIRAM=n" in frag
    assert FETCH not in frag


# --- validation: bad value and misplaced key are refused by name -------------

@pytest.mark.parametrize("value", ["on", "true", "cache", "", 1])
def test_invalid_value_is_rejected(value):
    board = dict(OPI_BOARD)
    board["psram_kernel_exec"] = value
    with pytest.raises(SystemExit):
        BSPGEN.validate(board, Path("test.yaml"))


def test_flash_on_a_non_opi_board_is_rejected():
    board = dict(OPI_BOARD)
    board["psram_type"] = "qspi"
    board["psram_kernel_exec"] = "flash"
    with pytest.raises(SystemExit):
        BSPGEN.validate(board, Path("test.yaml"))


def test_psram_is_accepted_on_any_board():
    board = dict(OPI_BOARD)
    board["psram_type"] = "qspi"
    board["psram_kernel_exec"] = "psram"
    BSPGEN.validate(board, Path("test.yaml"))   # no raise


def test_the_rejection_names_the_value(capsys):
    board = dict(OPI_BOARD)
    board["psram_kernel_exec"] = "nonsense"
    with pytest.raises(SystemExit):
        BSPGEN.validate(board, Path("test.yaml"))
    assert "nonsense" in capsys.readouterr().err


# --- the real boards: T-Embed opts into flash, devkitc keeps the default -----

def test_the_t_embed_declares_flash():
    assert _board_yaml("lilygo-t-embed-cc1101").get("psram_kernel_exec") == "flash"


def test_the_t_embed_fragment_runs_from_flash():
    frag = BSPGEN.generate_sdkconfig_board(_board_yaml("lilygo-t-embed-cc1101"))
    assert f"{FETCH}=n" in frag and f"{RODATA}=n" in frag


def test_the_devkitc_still_copies_into_psram():
    """esp32s3-devkitc is also OPI PSRAM but was not re-measured here; the
    default must leave it exactly as it was."""
    board = _board_yaml("esp32s3-devkitc")
    assert "psram_kernel_exec" not in board
    frag = BSPGEN.generate_sdkconfig_board(board)
    assert f"{FETCH}=y" in frag and f"{RODATA}=y" in frag
