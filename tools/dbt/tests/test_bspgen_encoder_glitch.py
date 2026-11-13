"""Tests for encoder.glitch_filter_ns (MAGIC-03).

A mechanical rotary chatters on each transition; without a hardware filter the
bounce crosses the PCNT watch window and forges phantom detents the intent layer
then emits. `encoder.glitch_filter_ns` declares the rejection window; bspgen
emits DUNEOS_ENCODER_GLITCH_FILTER_NS, which the PCNT HAL feeds to
pcnt_unit_set_glitch_filter. The key is optional — omitting it means no filter,
today's behaviour — and bounded to the PCNT hardware window.
"""

import importlib.util
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]

MACRO = "DUNEOS_ENCODER_GLITCH_FILTER_NS"


def _load_bspgen():
    spec = importlib.util.spec_from_file_location(
        "duneos_bspgen", REPO_ROOT / "tools" / "duneos-bspgen.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BSPGEN = _load_bspgen()

BASE = {
    "name": "testboard",
    "cpu": "esp32s3",
    "flash_size_mb": 16,
    "uart": [{"id": 0, "tx_pin": 43, "rx_pin": 44, "default_baud": 115200}],
}


def _with_encoder(**enc):
    board = dict(BASE)
    board["encoder"] = {"a_pin": 4, "b_pin": 5, **enc}
    return board


def _board_yaml(name):
    with open(REPO_ROOT / "boards" / name / "board.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)["board"]


# --- emission: present when declared, absent when omitted --------------------

def test_declared_value_is_emitted():
    header = BSPGEN.generate(_with_encoder(glitch_filter_ns=5000))
    assert f"#define {MACRO}" in header
    assert "5000" in header.split(MACRO, 1)[1].splitlines()[0]


def test_no_key_emits_no_macro():
    """Absent means no filter — the HAL guards on #ifdef, so emitting nothing
    keeps the pre-MAGIC-03 behaviour for every board that says nothing."""
    assert MACRO not in BSPGEN.generate(_with_encoder())


def test_a_board_without_an_encoder_emits_no_macro():
    assert MACRO not in BSPGEN.generate(dict(BASE))


# --- validation: bounded to the PCNT hardware window -------------------------

@pytest.mark.parametrize("value", [1, 5000, 12000])
def test_in_range_values_are_accepted(value):
    BSPGEN.validate(_with_encoder(glitch_filter_ns=value), Path("t.yaml"))


@pytest.mark.parametrize("value", [0, -1, 12001, 20000, 5000.0, True, "5000"])
def test_out_of_range_or_non_integer_is_rejected(value):
    with pytest.raises(SystemExit):
        BSPGEN.validate(_with_encoder(glitch_filter_ns=value), Path("t.yaml"))


def test_the_rejection_names_the_value(capsys):
    with pytest.raises(SystemExit):
        BSPGEN.validate(_with_encoder(glitch_filter_ns=99999), Path("t.yaml"))
    assert "99999" in capsys.readouterr().err


# --- the real board ----------------------------------------------------------

def test_the_t_embed_declares_the_known_good_window():
    enc = _board_yaml("lilygo-t-embed-cc1101")["encoder"]
    assert enc["glitch_filter_ns"] == 5000
