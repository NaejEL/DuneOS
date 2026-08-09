"""Tests for the bspgen paths exercised by SPEC-radar-ld2450:
multi-UART emission, I2C removal, and UTF-8 board.yaml reading."""

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_bspgen():
    spec = importlib.util.spec_from_file_location(
        "duneos_bspgen", REPO_ROOT / "tools" / "duneos-bspgen.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BSPGEN = _load_bspgen()

BASE_BOARD = {
    "name": "testboard",
    "cpu": "esp32s3",
    "flash_size_mb": 8,
    "uart": [
        {"id": 0, "tx_pin": 43, "rx_pin": 44, "default_baud": 115200},
        {"id": 1, "tx_pin": 2, "rx_pin": 1, "default_baud": 256000},
    ],
}


def test_generate_emits_all_uart_entries():
    hdr = BSPGEN.generate(dict(BASE_BOARD))
    assert "#define DUNEOS_UART0_TX_PIN" in hdr
    assert "#define DUNEOS_UART1_TX_PIN" in hdr
    lines = {l.split()[1]: l.split()[2] for l in hdr.splitlines()
             if l.startswith("#define DUNEOS_UART")}
    assert lines["DUNEOS_UART1_TX_PIN"] == "2"
    assert lines["DUNEOS_UART1_RX_PIN"] == "1"
    assert lines["DUNEOS_UART1_BAUD"] == "256000"


def test_generate_without_i2c_emits_no_i2c_defines():
    hdr = BSPGEN.generate(dict(BASE_BOARD))
    assert "DUNEOS_I2C" not in hdr
    assert "DUNEOS_HAVE_I2C" not in hdr


def test_sdkconfig_without_i2c_omits_drv_i2c():
    frag = BSPGEN.generate_sdkconfig_board(dict(BASE_BOARD))
    assert "CONFIG_DUNEOS_DRV_UART=y" in frag
    assert "CONFIG_DUNEOS_DRV_I2C=y" not in frag


def test_sdkconfig_with_i2c_keeps_drv_i2c():
    board = dict(BASE_BOARD)
    board["i2c"] = [{"id": 0, "sda_pin": 2, "scl_pin": 1, "freq_hz": 400000}]
    frag = BSPGEN.generate_sdkconfig_board(board)
    assert "CONFIG_DUNEOS_DRV_I2C=y" in frag


def test_utf8_board_yaml_reads_regardless_of_locale(tmp_path, monkeypatch, capsys):
    """Board YAMLs carry UTF-8 comments (arrows, dashes). Reading them must
    not depend on the platform locale codepage — cp1252 rejects bytes like
    0x90 (present in a UTF-8 left-arrow) and used to crash bspgen."""
    yml = tmp_path / "board.yaml"
    yml.write_bytes(
        "# G1 = RX ← sensor TX — utf-8 comment\n"
        "board:\n  name: t\n  cpu: esp32s3\n".encode("utf-8"))

    monkeypatch.setattr(sys, "argv", ["duneos-bspgen.py", str(yml), "--dry-run"])
    BSPGEN.main()   # success = no UnicodeDecodeError
    assert "board_config.h" in capsys.readouterr().out


def test_cardputer_board_yaml_declares_radar_uart():
    import yaml
    with open(REPO_ROOT / "boards" / "m5stack-cardputer" / "board.yaml",
              encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    board = doc["board"]
    uarts = {u["id"]: u for u in board["uart"]}
    assert uarts[1]["tx_pin"] == 2
    assert uarts[1]["rx_pin"] == 1
    assert uarts[1]["default_baud"] == 256000
    assert "i2c" not in board
