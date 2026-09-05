"""Tests for the per-board main-task stack declaration (SPEC-leg-37).

`main_task_stack: N` in a board.yaml is the sole source of
CONFIG_ESP_MAIN_TASK_STACK_SIZE for that board. The stack peak is a property of
(architecture x that board's boot workload), so the decisive properties are that
a declaring board gets exactly its value, that a board which says nothing keeps
the SDK default, and that a bad value is refused by name rather than coerced.
"""

import importlib.util
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]

SYMBOL = "CONFIG_ESP_MAIN_TASK_STACK_SIZE"


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
    "uart": [{"id": 0, "tx_pin": 43, "rx_pin": 44, "default_baud": 115200}],
}

DECLARING_BOARDS = ("m5stack-cardputer",)


def _board_yaml(name):
    with open(REPO_ROOT / "boards" / name / "board.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)["board"]


def _all_board_names():
    return sorted(p.parent.name for p in (REPO_ROOT / "boards").glob("*/board.yaml"))


# --- Criterion 1: emitted when declared, absent when not ---------------------

def test_declared_value_is_emitted_verbatim():
    board = dict(BASE_BOARD)
    board["main_task_stack"] = 4608
    assert f"{SYMBOL}=4608" in BSPGEN.generate_sdkconfig_board(board)


def test_no_key_emits_nothing():
    """Silence must leave ESP-IDF's own default in force — emitting the default
    ourselves would freeze it against an IDF upgrade."""
    assert SYMBOL not in BSPGEN.generate_sdkconfig_board(dict(BASE_BOARD))


def test_the_key_changes_nothing_else_in_the_fragment():
    plain = BSPGEN.generate_sdkconfig_board(dict(BASE_BOARD))
    board = dict(BASE_BOARD)
    board["main_task_stack"] = 4608
    declared = BSPGEN.generate_sdkconfig_board(board)

    added = [l for l in declared.splitlines() if l not in plain.splitlines()]
    assert added == [
        "# Main task stack (measured on this board — see board.yaml)",
        f"{SYMBOL}=4608",
    ]
    for line in plain.splitlines():
        assert line in declared.splitlines()


def test_the_key_is_architecture_agnostic():
    """The value is per board, but the mechanism is not Xtensa-only: a RISC-V
    board that measures its own peak must be able to declare it."""
    board = dict(BASE_BOARD)
    board["cpu"] = "esp32c3"
    board["main_task_stack"] = 4096
    assert f"{SYMBOL}=4096" in BSPGEN.generate_sdkconfig_board(board)


# --- Criterion 2: bad values are rejected, by board and by value -------------

@pytest.mark.parametrize("value", ["4608", 4608.0, "big", [4608]])
def test_non_integer_is_rejected(value):
    board = dict(BASE_BOARD)
    board["main_task_stack"] = value
    with pytest.raises(SystemExit):
        BSPGEN.generate_sdkconfig_board(board)


def test_boolean_is_rejected():
    """`main_task_stack: true` is an int in Python (1) and would otherwise pass
    every numeric check while meaning nothing."""
    board = dict(BASE_BOARD)
    board["main_task_stack"] = True
    with pytest.raises(SystemExit):
        BSPGEN.generate_sdkconfig_board(board)


@pytest.mark.parametrize("value", [0, -4, -4608])
def test_non_positive_is_rejected(value):
    board = dict(BASE_BOARD)
    board["main_task_stack"] = value
    with pytest.raises(SystemExit):
        BSPGEN.generate_sdkconfig_board(board)


@pytest.mark.parametrize("value", [4609, 4610, 3583])
def test_unaligned_is_rejected(value):
    board = dict(BASE_BOARD)
    board["main_task_stack"] = value
    with pytest.raises(SystemExit):
        BSPGEN.generate_sdkconfig_board(board)


@pytest.mark.parametrize("value", ["4608", 0, 4609])
def test_the_rejection_names_the_board_and_the_value(value, capsys):
    board = dict(BASE_BOARD)
    board["name"] = "some-board"
    board["main_task_stack"] = value
    with pytest.raises(SystemExit):
        BSPGEN.generate_sdkconfig_board(board)
    err = capsys.readouterr().err
    assert "some-board" in err
    assert repr(value) in err or str(value) in err


@pytest.mark.parametrize("entry", [
    f"{SYMBOL}=8192",
    f" {SYMBOL} = 8192",          # leading space defeated a prefix match
    f"\t{SYMBOL}=8192",
    f"{SYMBOL} = 8192   ",
    f"# {SYMBOL} is not set",
])
def test_the_typed_key_and_a_raw_kconfig_entry_are_refused_together(entry):
    """The raw `kconfig:` list is emitted last, so it would silently outrank the
    measured value — the exact silent coercion criterion 2 exists to prevent.
    Kconfig ignores surrounding whitespace, so the guard must too: matching the
    raw string let ` CONFIG_ESP_MAIN_TASK_STACK_SIZE = 8192` through."""
    board = dict(BASE_BOARD)
    board["main_task_stack"] = 4608
    board["kconfig"] = [entry]
    with pytest.raises(SystemExit):
        BSPGEN.generate_sdkconfig_board(board)


def test_an_unrelated_raw_kconfig_entry_is_not_refused():
    board = dict(BASE_BOARD)
    board["main_task_stack"] = 4608
    board["kconfig"] = ["CONFIG_ESP_MAIN_TASK_STACK_SIZE_SOMETHING_ELSE=8192",
                        "CONFIG_SOMETHING=y"]
    assert f"{SYMBOL}=4608" in BSPGEN.generate_sdkconfig_board(board)


@pytest.mark.parametrize("entry,symbol", [
    ("CONFIG_A=y", "CONFIG_A"),
    ("  CONFIG_A = y  ", "CONFIG_A"),
    ("# CONFIG_A is not set", "CONFIG_A"),
    ("  #  CONFIG_A is not set ", "CONFIG_A"),
    ("# a plain comment", ""),
])
def test_kconfig_symbol_normalises_every_accepted_spelling(entry, symbol):
    assert BSPGEN.kconfig_symbol(entry) == symbol


# --- Criterion 3: only the measured board declares it ------------------------

def test_only_the_measured_boards_declare_the_key():
    declaring = [n for n in _all_board_names()
                 if _board_yaml(n).get("main_task_stack") is not None]
    assert declaring == sorted(DECLARING_BOARDS)


def _sets_the_symbol_by_hand(name):
    """kincony-A16 predates this key and sets the symbol through the generic
    `kconfig:` passthrough. That is the untyped escape hatch — unvalidated and
    undocumented — which is exactly what this key replaces, but converting that
    board is a measurement nobody has taken (SPEC-leg-37, out of scope)."""
    return any(str(entry).startswith(SYMBOL)
               for entry in _board_yaml(name).get("kconfig", []))


def test_no_other_board_generates_the_symbol():
    for name in _all_board_names():
        frag = BSPGEN.generate_sdkconfig_board(_board_yaml(name))
        if name in DECLARING_BOARDS or _sets_the_symbol_by_hand(name):
            assert SYMBOL in frag, name
        else:
            assert SYMBOL not in frag, name


def test_generated_sdkconfig_board_files_match_the_yaml():
    """Guards against a stale checked-out artefact."""
    for name in _all_board_names():
        generated = REPO_ROOT / "boards" / name / "sdkconfig.board"
        if not generated.exists():
            continue
        text = generated.read_text(encoding="utf-8")
        expected = name in DECLARING_BOARDS or _sets_the_symbol_by_hand(name)
        assert (SYMBOL in text) == expected, name


# --- Criterion 4/5: the derivation and the policy line are recorded ----------

def test_the_cardputer_value_is_the_measured_one():
    assert _board_yaml("m5stack-cardputer")["main_task_stack"] == 4608


def test_the_cardputer_value_carries_its_derivation():
    """A bare number fails criterion 4: the comment must carry the measurement,
    its date and the margin, or the next reader cannot tell 4608 from a guess."""
    text = (REPO_ROOT / "boards" / "m5stack-cardputer" / "board.yaml").read_text(
        encoding="utf-8")
    block = text.split("main_task_stack:", 1)[0]
    for token in ("3764", "12288", "8524", "2026-09-05",
                  "uxTaskGetStackHighWaterMark",
                  # The watchpoint enabled by the same change costs up to 60 B of
                  # usable stack, so the delivered margin is 4608-60-3764 = 784 B,
                  # not 4608-3764. The derivation must show that subtraction or
                  # the next reader's arithmetic silently disagrees with reality.
                  "60", "4548", "784"):
        assert token in block, token


def test_the_derivation_does_not_claim_the_watchpoint_is_free():
    """The measurement was taken with CONFIG_FREERTOS_WATCHPOINT_END_OF_STACK
    off. A derivation that ignores its cost overstates the margin."""
    text = (REPO_ROOT / "boards" / "m5stack-cardputer" / "board.yaml").read_text(
        encoding="utf-8")
    block = text.split("main_task_stack:", 1)[0]
    assert "WATCHPOINT_END_OF_STACK" in block
    assert "844" not in block


def test_the_stack_watchpoint_is_project_wide():
    """Criterion 5: it is a policy, not a measurement, so it belongs to the root
    Kconfig — and never to a board fragment."""
    defaults = (REPO_ROOT / "sdkconfig.defaults").read_text(encoding="utf-8")
    assert "CONFIG_FREERTOS_WATCHPOINT_END_OF_STACK=y" in defaults
    # Criterion 5: the comment must state ESP-IDF's documented cost and limit,
    # not present the symbol as free. It shrinks usable stack on every board,
    # including the six still on the unmeasured default.
    block = defaults.split("CONFIG_FREERTOS_WATCHPOINT_END_OF_STACK=y", 1)[0]
    assert "60 bytes" in block
    assert "32 bytes" in block
    assert "zero RAM" not in defaults
    for name in _all_board_names():
        frag = REPO_ROOT / "boards" / name / "sdkconfig.board"
        if frag.exists():
            assert "WATCHPOINT_END_OF_STACK" not in frag.read_text(
                encoding="utf-8"), name
