"""The gauge and the charger must agree on what "finished" means.

A gauge declares charge termination only after watching the current fall below
its own taper threshold *while charge is still flowing* (BQ27220 TRM SLUUBD4A
4.4.1: two consecutive 40 s windows under Taper Current, with capacity still
accumulating). If the charger cuts off ABOVE that threshold, the current jumps
from the cutoff straight to zero, the crossing never happens, the gauge's FC
flag never sets, RemainingCapacity is never synced to FullChargeCapacity, and
the indicator stops short of 100% forever — on a battery the charger considers
full.

That is not a hypothetical. Measured on the t-embed: charger terminating at
256 mA, gauge taper at the chip default of 100 mA, stuck at 96% with the
charger reporting "termination done" throughout. Nothing was broken in either
chip. They simply disagreed, and nothing in the tree could notice.

Two numbers, two datasheets, one relationship. So the relationship is checked.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from dbt import boardgen   # noqa: E402  (after the path insert)


BASE = {"name": "testboard", "cpu": "esp32s3", "flash_size_mb": 8,
        "i2c": [{"id": 0, "sda_pin": 8, "scl_pin": 18}]}

GAUGE = {"type": "bq27220", "i2c_id": 0, "gauge_addr": 0x55,
         "design_capacity_mah": 1300}
CHARGER = {"type": "bq25896", "i2c_id": 0, "addr": 0x6B,
           "charge_voltage_mv": 4208, "charge_current_ma": 832,
           "termination_current_ma": 128}


def _board(battery=None, charger=None):
    b = dict(BASE)
    if battery is not None:
        b["battery"] = dict(battery)
    if charger is not None:
        b["charger"] = dict(charger)
    return b


def _header(board, caps=("battery", "charger")):
    return boardgen.generate(board, "testboard", list(caps))


def _defines(text):
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "#define":
            out[parts[1]] = parts[2]
    return out


# ----- the relationship --------------------------------------------------

def test_a_taper_above_the_cutoff_is_accepted():
    bat = dict(GAUGE, taper_current_ma=200)
    d = _defines(_header(_board(bat, CHARGER)))
    assert d["DUNEOS_BATTERY_TAPER_CURRENT_MA"] == "200"
    assert d["DUNEOS_CHARGER_TERM_CURRENT_MA"] == "128"


@pytest.mark.parametrize("taper,term", [
    (100, 256),   # the real t-embed defaults, which produced the 96% ceiling
    (128, 128),   # equal is not enough: the crossing must actually happen
    (64, 1024),
])
def test_a_taper_at_or_below_the_cutoff_is_refused(taper, term):
    bat = dict(GAUGE, taper_current_ma=taper)
    chg = dict(CHARGER, termination_current_ma=term)
    with pytest.raises(SystemExit) as e:
        _header(_board(bat, chg))
    msg = str(e.value)
    assert str(taper) in msg and str(term) in msg
    assert "100%" in msg          # says what the consequence is, not just that it is wrong


def test_the_check_is_silent_when_either_number_is_absent():
    """A board that declares neither is not making a mistake. Only a board
    that states both can be inconsistent."""
    _header(_board(GAUGE, dict(CHARGER, termination_current_ma=None)))
    _header(_board(dict(GAUGE), CHARGER))          # no taper declared
    _header(_board(GAUGE, None))                   # no charger at all
    _header(_board(None, CHARGER))                 # no gauge at all


def test_the_real_t_embed_board_is_consistent():
    """The board this was found on. Reads board.yaml, which is hand-edited and
    tracked — not a generated artefact."""
    import yaml
    path = REPO_ROOT / "boards" / "lilygo-t-embed-cc1101" / "board.yaml"
    board = yaml.safe_load(path.read_text(encoding="utf-8"))
    # generate() raises if the two disagree; that is the assertion.
    d = _defines(_header(board))
    assert int(d["DUNEOS_BATTERY_TAPER_CURRENT_MA"]) > \
           int(d["DUNEOS_CHARGER_TERM_CURRENT_MA"])


def test_every_board_declaring_both_is_consistent():
    """Completeness: no board may ship with the two numbers at odds."""
    import yaml
    checked = 0
    for path in sorted((REPO_ROOT / "boards").glob("*/board.yaml")):
        board = yaml.safe_load(path.read_text(encoding="utf-8"))
        flat = boardgen._flatten(board)
        if not (flat.get("battery") or {}).get("taper_current_ma"):
            continue
        if not (flat.get("charger") or {}).get("termination_current_ma"):
            continue
        boardgen._check_taper_agrees_with_charger(flat)   # raises on conflict
        checked += 1
    assert checked >= 1, "no board declares both — this test now proves nothing"


# ----- the charger block itself ------------------------------------------

def test_the_charger_block_is_emitted_for_a_declared_charger():
    d = _defines(_header(_board(GAUGE, CHARGER)))
    assert d["DUNEOS_CHARGER_DRIVER"] == '"bq25896"'
    assert d["DUNEOS_CHARGER_I2C_DEV"] == '"/dev/i2c-0"'
    assert d["DUNEOS_CHARGER_ADDR"] == "0x6b"
    assert d["DUNEOS_CHARGER_VOLTAGE_MV"] == "4208"


def test_a_board_without_a_charger_emits_nothing_about_one():
    text = _header(_board(GAUGE, None))
    assert "DUNEOS_CHARGER" not in text


def test_an_undeclared_profile_field_is_simply_absent():
    """So the backend's #ifdef leaves that register alone rather than writing
    a default of someone's invention."""
    chg = {k: v for k, v in CHARGER.items() if k != "charge_current_ma"}
    text = _header(_board(GAUGE, chg))
    assert "DUNEOS_CHARGER_CURRENT_MA" not in text
    assert "DUNEOS_CHARGER_VOLTAGE_MV" in text


@pytest.mark.parametrize("key,value", [
    ("charge_voltage_mv", 3000),           # below the chip's range
    ("charge_voltage_mv", 5000),           # above it
    ("termination_current_ma", 32),        # below the 64 mA floor
    ("termination_current_ma", 2048),      # above the 1024 mA ceiling
])
def test_a_value_the_chip_cannot_express_is_refused(key, value):
    with pytest.raises(SystemExit):
        _header(_board(GAUGE, dict(CHARGER, **{key: value})))


# ----- the capability wiring, which was already one entry behind ---------

def test_every_mapped_capability_block_is_actually_reachable():
    """The emission loop used to be a hand-written tuple naming three of the
    four mapped blocks, so registering a capability emitted nothing — silently.
    Driving the loop from the map removed that; this keeps it removed."""
    board = _board(dict(GAUGE, taper_current_ma=200), CHARGER)
    board["display"] = {"driver": "st7789", "width": 320, "height": 170}
    board["buttons"] = [{"pin": 0, "code": "KEY_ENTER"}]
    for cap in boardgen.CAPABILITY_TO_BLOCKS:
        text = boardgen.generate(board, "testboard", [cap])
        body = [l for l in text.splitlines()
                if l.startswith("#define") and "BOARD_NAME" not in l]
        assert body, f"capability {cap!r} is mapped but emits nothing"


def test_the_charger_capability_resolves_to_its_sources():
    from dbt import capabilities
    entry = capabilities.CAPABILITY_MAP["charger"]
    assert entry["board_key"] == ["charger", "type"]
    srcs = " ".join(entry["sources"])
    assert "libcharger.c" in srcs and "lib{driver}.c" in srcs
