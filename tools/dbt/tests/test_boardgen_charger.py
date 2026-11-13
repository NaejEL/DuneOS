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
         "design_capacity_mah": 1300, "taper_current_ma": 200}
# The same gauge with no taper declared — only valid on a board with no charger.
GAUGE_NO_TAPER = {k: v for k, v in GAUGE.items() if k != "taper_current_ma"}
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
    d = _defines(_header(_board(GAUGE, CHARGER)))
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


def test_the_check_is_silent_only_where_silence_is_harmless():
    """A board making no claim about termination is not making a mistake."""
    _header(_board(GAUGE_NO_TAPER, dict(CHARGER, termination_current_ma=None)))
    _header(_board(GAUGE_NO_TAPER, None))          # no charger at all
    _header(_board(None, CHARGER))                 # no gauge at all


def test_a_kernel_served_adc_battery_with_a_charger_is_accepted():
    """The over-reach the guard's first version committed.

    An adc_simple battery is served by the kernel and has no taper register at
    all, so a charger beside it has nothing to be consistent with. Demanding a
    taper here would force a number the generator then drops. `charger` alone,
    because `battery` would be kernel-served and skipped by resolve()."""
    adc = {"type": "adc_simple"}
    _header(_board(adc, CHARGER), caps=["charger"])


def test_a_declared_cutoff_without_a_declared_taper_is_refused():
    """The asymmetry that silently reproduces the ceiling.

    The first version of this file asserted the opposite — it pinned the
    silence as intended — which would have let a board ship with a charger
    cutting off above the gauge's 100 mA ROM default, the exact configuration
    the whole change exists to prevent. boardgen cannot know every gauge's
    default, so it refuses rather than guesses."""
    with pytest.raises(SystemExit) as e:
        _header(_board(GAUGE_NO_TAPER, CHARGER))
    msg = str(e.value)
    assert "taper_current_ma" in msg and "100%" in msg


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

def test_every_mapped_capability_block_is_actually_reachable(monkeypatch):
    """The emission loop used to be a hand-written tuple naming three of the
    four mapped blocks, so registering a capability emitted nothing — silently.
    Driving the loop from the map removed that; this keeps it removed.

    The assertion is on each capability's OWN contribution, measured against a
    baseline. The first version of this test asserted that the header held at
    least one `#define` other than BOARD_NAME, which the always-on
    storage/i2c blocks satisfy on their own: replacing a capability's block
    with a no-op left five defines and the test green. It could not fail, and
    the one case it could not detect was exactly the regression it claimed to
    pin. Measuring the difference is what makes it a gate.
    """
    board = _board(GAUGE, CHARGER)
    board["display"] = {"driver": "st7789", "width": 320, "height": 170}
    board["buttons"] = [{"pin": 0, "intent": "VALIDATE"}]

    # What generate() emits for no capability at all: the always-on blocks.
    baseline = set(_defines(boardgen.generate(board, "testboard", ["__none__"])))

    for cap in boardgen.CAPABILITY_TO_BLOCKS:
        added = set(_defines(boardgen.generate(board, "testboard", [cap]))) - baseline
        assert added, (
            f"capability {cap!r} is mapped but adds nothing beyond the "
            f"always-on blocks — is it emitted by the loop in generate()?")


def test_the_reachability_test_fails_when_a_block_stops_emitting(monkeypatch):
    """The mutation that the previous version of that test survived.

    Without this, nothing proves the assertion above is load-bearing — and
    "the test passes" was the exact evidence that was wrong before.
    """
    board = _board(GAUGE, CHARGER)
    monkeypatch.setitem(boardgen.CAPABILITY_TO_BLOCKS, "charger",
                        (lambda b, lines: None,))
    baseline = set(_defines(boardgen.generate(board, "testboard", ["__none__"])))
    added = set(_defines(boardgen.generate(board, "testboard", ["charger"]))) - baseline
    assert not added, ("a no-op block still added defines; the baseline is "
                       "not isolating the capability's own output")


def test_the_charger_capability_resolves_to_its_sources():
    from dbt import capabilities
    entry = capabilities.CAPABILITY_MAP["charger"]
    assert entry["board_key"] == ["charger", "type"]
    srcs = " ".join(entry["sources"])
    assert "libcharger.c" in srcs and "lib{driver}.c" in srcs
