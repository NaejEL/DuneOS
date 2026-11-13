"""The gauge registers must name the quantity the code thinks it reads.

The BQ27220 backend read 0x04, 0x06 and 0x1C for Voltage, Flags and
StateOfCharge. Those are real commands — AtRateTimeToEmpty, Temperature and
StandbyTimeToEmpty — so every read succeeded and the gauge looked alive. It
reported minutes as millivolts and minutes as a percentage, and because
Temperature at room temperature is ~2982 = 0x0BA6 with bit 9 set, the FC bit
test said "fully charged" on every poll, forever. Nothing errored. Nothing
logged. The battery indicator was simply wrong, which is the one failure mode
a human notices last.

The defect was not a typo: it was three consecutive rows read off the right
table at the wrong offset. A test that merely pinned the three numbers would
have been written from the same mistaken reading and agreed with it. So this
file carries its own transcription of SLUUBD4A table 2-1 and checks that each
constant lands on the command whose NAME matches what the code calls it.

Two independent transcriptions of a datasheet agreeing is evidence. One
transcription checked against itself is not.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

BACKEND = REPO_ROOT / "sdk" / "sensor" / "libbq27220.c"
SCENARIO = REPO_ROOT / "apps" / "user" / "i2cscope" / "scenarios" / "bq27220.scn"

# TI BQ27220 Technical Reference Manual SLUUBD4A (April 2016, revised November
# 2022), table 2-1 "Standard Commands". Transcribed from the PDF, address of
# the low byte of each 2-byte command.
TRM_TABLE_2_1 = {
    0x00: "Control",
    0x02: "AtRate",
    0x04: "AtRateTimeToEmpty",
    0x06: "Temperature",
    0x08: "Voltage",
    0x0A: "BatteryStatus",
    0x0C: "Current",
    0x10: "RemainingCapacity",
    0x12: "FullChargeCapacity",
    0x14: "AverageCurrent",
    0x16: "TimeToEmpty",
    0x18: "TimeToFull",
    0x1A: "StandbyCurrent",
    0x1C: "StandbyTimeToEmpty",
    0x1E: "MaxLoadCurrent",
    0x20: "MaxLoadTimeToEmpty",
    0x22: "RawCoulombCount",
    0x24: "AveragePower",
    0x28: "InternalTemperature",
    0x2A: "CycleCount",
    0x2C: "RelativeStateOfCharge",
    0x2E: "StateOfHealth",
    0x30: "ChargeVoltage",
    0x32: "ChargeCurrent",
    0x3C: "DesignCapacity",
}

# What each backend constant is used as, and therefore which command it must
# be. The backend's own variable names and uses, read from bq27220_op_read().
EXPECTED_COMMAND = {
    "BQ27220_REG_VOLTAGE": "Voltage",
    "BQ27220_REG_FLAGS":   "BatteryStatus",
    "BQ27220_REG_SOC":     "RelativeStateOfCharge",
}


_SHIFT = re.compile(r"^\(?\s*1[uU]?\s*<<\s*(\d+)\s*\)?$")


def _c_defines(path):
    """{name: int} for the `#define NAME <value>` forms this file uses.

    Two forms matter here: a plain integer literal (the register addresses) and
    `(1u << N)` (the flag bits). A value this cannot read is skipped — which is
    why every assertion below looks the macro up by name and fails loudly if it
    is absent, rather than silently testing nothing.
    """
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("/*")[0].split("//")[0].strip()
        parts = line.split(None, 2)
        if len(parts) < 3 or parts[0] != "#define":
            continue
        name, value = parts[1], parts[2].strip()
        m = _SHIFT.match(value)
        if m:
            out[name] = 1 << int(m.group(1))
            continue
        try:
            out[name] = int(value, 0)
        except ValueError:
            continue
    return out


@pytest.mark.parametrize("macro,command", sorted(EXPECTED_COMMAND.items()))
def test_each_register_is_the_command_the_code_calls_it(macro, command):
    defines = _c_defines(BACKEND)
    assert macro in defines, f"{macro} is gone from {BACKEND.name}"
    addr = defines[macro]
    assert addr in TRM_TABLE_2_1, (
        f"{macro} = {addr:#04x} is not a standard command in SLUUBD4A table 2-1")
    assert TRM_TABLE_2_1[addr] == command, (
        f"{macro} = {addr:#04x} is {TRM_TABLE_2_1[addr]}(), not {command}(). "
        f"{command}() is at {next(a for a, n in TRM_TABLE_2_1.items() if n == command):#04x}.")


def test_the_three_previously_wrong_addresses_are_the_ones_the_table_says():
    """Pins the specific trap: 0x04/0x06/0x1C are real commands, just not these.

    Without this, a future reader who finds 0x04 in some other BQ27xxx driver
    has no way to know it was already tried here and was wrong.
    """
    assert TRM_TABLE_2_1[0x04] == "AtRateTimeToEmpty"
    assert TRM_TABLE_2_1[0x06] == "Temperature"
    assert TRM_TABLE_2_1[0x1C] == "StandbyTimeToEmpty"
    defines = _c_defines(BACKEND)
    assert defines["BQ27220_REG_VOLTAGE"] != 0x04
    assert defines["BQ27220_REG_FLAGS"] != 0x06
    assert defines["BQ27220_REG_SOC"] != 0x1C


def test_the_flags_bits_match_table_2_6():
    """DSG is low-byte bit 0, FC is high-byte bit 1 — bit 9 of the word."""
    defines = _c_defines(BACKEND)
    assert defines["BQ27220_FLAG_DSG"] == 1 << 0
    assert defines["BQ27220_FLAG_FC"] == 1 << 9


# ----- the second copy ----------------------------------------------------

def _scenario_regs(path):
    """{label: address} from an i2cscope .scn file's register lines."""
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0] in ("word", "sword", "byte", "hex"):
            out[parts[3]] = int(parts[1], 0)
    return out


def test_the_i2cscope_scenario_agrees_with_the_backend():
    """The scenario used to be copied from the C, wrong values included.

    It said so in its own header: "Registers follow DuneOS
    sdk/sensor/libbq27220.c". So it confirmed the bug to anyone who checked the
    code against it. Both are now checked against the table instead, and this
    asserts they cannot drift apart again.
    """
    scn = _scenario_regs(SCENARIO)
    defines = _c_defines(BACKEND)
    pairs = {
        "Voltage": "BQ27220_REG_VOLTAGE",
        "Charge":  "BQ27220_REG_SOC",
        "Flags":   "BQ27220_REG_FLAGS",
    }
    for label, macro in pairs.items():
        assert label in scn, f"{SCENARIO.name} no longer defines {label}"
        assert scn[label] == defines[macro], (
            f"{SCENARIO.name} reads {label} at {scn[label]:#04x} but the "
            f"backend reads {macro} at {defines[macro]:#04x}")


def test_every_scenario_address_is_a_real_command():
    """Including Current(), which the scenario carries and the backend does not."""
    for label, addr in _scenario_regs(SCENARIO).items():
        assert addr in TRM_TABLE_2_1, (
            f"{SCENARIO.name}: {label} at {addr:#04x} is not in table 2-1")
    assert _scenario_regs(SCENARIO)["Current"] == 0x0C
