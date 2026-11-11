"""SPEC-input-portability criteria 7, 8 and 9 — bspgen's input map.

Every board here is built in the test body. Nothing reads `board_config.h` or
`sdkconfig.board`: both are gitignored, so a test asserting against them passes
only on a machine where bspgen already ran (LEG-38, shipped twice).
"""

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from dbt import boardgen
from dbt.input_map import (
    BATCH_CLAMP,
    BUTTON_INTENTS,
    INTENTS,
    INTENT_TO_KEY,
    InputTopologyError,
    VOCABULARY,
    reachable_intents,
    topology,
    unreachable_intents,
)

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
}


def board(**kw):
    return {**BASE_BOARD, **kw}


KEYBOARD_MATRIX = {
    "type": "ioMatrix",
    "row_select_pins": [8, 9, 11],
    "col_pins": [13, 15, 3, 4, 5, 6, 7],
    "matrix_rows": 4,
    "matrix_cols": 14,
}


def topo_block(header: str) -> str:
    """The input-topology section of a generated board_config.h."""
    start = header.index("Input topology")
    rest = header[start:]
    end = rest.find("/* ----------", 20)
    return rest if end < 0 else rest[:end]


def defines(text: str) -> dict[str, str]:
    out = {}
    for m in re.finditer(r"^#define\s+(\S+)\s+(.*)$", text, re.M):
        out[m.group(1)] = m.group(2).strip()
    return out


# --- Criterion 7: the expected map, and an explicitly empty one -------------


def test_a_rotary_and_two_buttons_emit_the_expected_map():
    b = board(
        buttons=[
            {"pin": 0, "intent": "VALIDATE"},
            {"pin": 6, "intent": "BACK"},
        ],
        encoder={"a_pin": 4, "b_pin": 5, "axis": "primary", "counts_per_step": 2},
    )
    d = defines(topo_block(BSPGEN.generate(b)))

    assert d["DUNEOS_INPUT_TOPOLOGY"] == "1"
    assert d["DUNEOS_INPUT_HAS_KEYBOARD"] == "0"
    assert d["DUNEOS_INPUT_N_KEYS"] == "2"
    assert d["DUNEOS_INPUT_N_AXES"] == "1"
    assert d["DUNEOS_INPUT_KEYS_INIT"] == (
        "{ { KEY_ENTER, INPUT_INTENT_VALIDATE, INPUT_INTENT_NONE, 0 }, "
        "{ KEY_ESC, INPUT_INTENT_BACK, INPUT_INTENT_NONE, 0 } }")
    assert d["DUNEOS_INPUT_AXES_INIT"] == (
        "{ { REL_WHEEL, INPUT_INTENT_PREV, INPUT_INTENT_NEXT, 2 } }")


def test_a_keyboard_board_declares_the_keyboard_and_no_keys():
    d = defines(topo_block(BSPGEN.generate(board(keyboard_matrix=KEYBOARD_MATRIX))))
    assert d["DUNEOS_INPUT_HAS_KEYBOARD"] == "1"
    assert d["DUNEOS_INPUT_N_KEYS"] == "0"
    assert d["DUNEOS_INPUT_N_AXES"] == "0"


def test_an_alt_axis_emits_the_second_pair():
    b = board(encoder={"a_pin": 4, "b_pin": 5, "axis": "alt"})
    d = defines(topo_block(BSPGEN.generate(b)))
    assert d["DUNEOS_INPUT_AXES_INIT"] == (
        "{ { REL_WHEEL, INPUT_INTENT_ALT_PREV, INPUT_INTENT_ALT_NEXT, 1 } }")


def test_a_long_press_intent_reaches_the_map():
    b = board(buttons=[{"pin": 0, "intent": "VALIDATE",
                        "intent_long": "BACK", "long_ms": 750}])
    d = defines(topo_block(BSPGEN.generate(b)))
    assert d["DUNEOS_INPUT_KEYS_INIT"] == (
        "{ { KEY_ENTER, INPUT_INTENT_VALIDATE, INPUT_INTENT_BACK, 750 } }")


def test_a_board_with_no_input_source_reports_an_explicitly_empty_map():
    block = topo_block(BSPGEN.generate(board()))
    assert defines(block)["DUNEOS_INPUT_TOPOLOGY"] == "0"
    # Explicit, not absent: a reader must be able to tell a board that declares
    # no input from a bspgen that forgot to emit the block.
    assert "declares no keyboard, button or encoder" in block
    assert "DUNEOS_INPUT_KEYS_INIT" not in block
    assert "DUNEOS_INPUT_AXES_INIT" not in block
    assert topology(board()) is None


def test_the_map_records_whether_every_intent_is_reachable():
    full = topo_block(BSPGEN.generate(board(keyboard_matrix=KEYBOARD_MATRIX)))
    assert "Every intent in the vocabulary is reachable" in full

    partial = topo_block(BSPGEN.generate(
        board(encoder={"a_pin": 4, "b_pin": 5})))
    assert "Intents NOT reachable on this board:" in partial
    assert "VALIDATE" in partial


# --- The breaking schema change must be named, never silently ignored -------


def test_the_old_code_spelling_is_rejected_by_name():
    b = board(buttons=[{"pin": 0, "code": "KEY_ENTER"}])

    with pytest.raises(InputTopologyError) as exc:
        topology(b)
    assert "'code:'" in str(exc.value)
    assert "'intent:'" in str(exc.value)

    # And bspgen dies rather than emitting a map with no keys in it — a schema
    # that accepts a stale field and generates nothing is the failure class this
    # cycle replaced.
    with pytest.raises(SystemExit):
        BSPGEN.generate(b)


def test_boardgen_also_rejects_the_old_spelling():
    b = board(buttons=[{"pin": 0, "code": "KEY_ENTER"}])
    with pytest.raises(InputTopologyError):
        boardgen.generate({"board": b}, "testboard", ["input"])


def test_boardgen_derives_the_key_code_from_the_intent():
    b = board(buttons=[{"pin": 0, "intent": "VALIDATE"},
                       {"pin": 6, "intent": "BACK"}])
    header = boardgen.generate({"board": b}, "testboard", ["input"])
    d = defines(header)
    assert d["DUNEOS_BTN_GPIO_CODES"] == "{ KEY_ENTER, KEY_ESC }"
    assert d["DUNEOS_BTN_GPIO_PINS"] == "{ 0, 6 }"
    # One field per button: the YAML never names a key code.
    assert "code" not in str(b["buttons"][0])


@pytest.mark.parametrize("bad", [
    {"pin": 0},                                        # no intent at all
    {"pin": 0, "intent": "ZOOM"},                      # not in the vocabulary
    {"pin": 0, "intent": "TEXT"},                      # no button can carry one
    {"pin": 0, "intent": "VALIDATE", "intent_long": "ZOOM"},
    {"pin": 0, "intent": "VALIDATE", "long_ms": 500},  # threshold, no intent
    {"pin": 0, "intent": "VALIDATE", "intent_long": "BACK", "long_ms": 0},
])
def test_a_malformed_button_is_refused(bad):
    with pytest.raises(InputTopologyError):
        topology(board(buttons=[bad]))


def test_two_buttons_with_the_same_intent_are_refused():
    # They would inject the same key code and be indistinguishable to every
    # reader of /dev/input/event0.
    with pytest.raises(InputTopologyError):
        topology(board(buttons=[{"pin": 0, "intent": "BACK"},
                                {"pin": 6, "intent": "BACK"}]))


@pytest.mark.parametrize("enc", [
    {"a_pin": 4, "b_pin": 5, "axis": "diagonal"},
    {"a_pin": 4, "b_pin": 5, "counts_per_step": 0},
])
def test_a_malformed_encoder_is_refused(enc):
    with pytest.raises(InputTopologyError):
        topology(board(encoder=enc))


def test_intent_case_is_normalised():
    t = topology(board(buttons=[{"pin": 0, "intent": "validate"}]))
    assert t["keys"][0]["intent"] == "VALIDATE"


# --- Criterion 8: every tracked board, from the git index -------------------


def tracked_board_yamls():
    out = subprocess.run(["git", "-C", str(REPO_ROOT), "ls-files", "boards/*/board.yaml"],
                         capture_output=True, check=True).stdout.decode()
    paths = sorted(p for p in out.split() if p)
    assert paths, "no tracked board.yaml found"
    return paths


def flatten(path):
    cfg = yaml.safe_load((REPO_ROOT / path).read_text(encoding="utf-8"))
    return boardgen._flatten(cfg)


@pytest.mark.parametrize("path", tracked_board_yamls())
def test_every_tracked_board_yields_a_complete_map_or_no_input_verdict(path):
    """Never a partial map, never an exception — criterion 8."""
    try:
        topo = topology(flatten(path))
    except InputTopologyError as exc:   # pragma: no cover - the failure report
        pytest.fail(f"{path}: {exc}")

    if topo is None:
        return   # the explicit "no input source" verdict

    assert set(topo) == {"has_keyboard", "keys", "axes"}
    assert isinstance(topo["has_keyboard"], bool)
    # A map is complete when every entry it carries is fully specified. A key
    # with an intent but no code, or an axis with no step, is the partial map
    # this asserts cannot exist.
    for k in topo["keys"]:
        assert k["code"] in INTENT_TO_KEY.values()
        assert k["intent"] in BUTTON_INTENTS
        assert k["intent_long"] in BUTTON_INTENTS | {"NONE"}
        assert (k["long_ms"] > 0) == (k["intent_long"] != "NONE")
    for a in topo["axes"]:
        assert a["counts_per_step"] >= 1
        assert a["intent_neg"] in VOCABULARY and a["intent_pos"] in VOCABULARY
    assert topo["has_keyboard"] or topo["keys"] or topo["axes"]


@pytest.mark.parametrize("path", tracked_board_yamls())
def test_every_tracked_board_generates(path):
    """bspgen must not die on any board the repo ships."""
    BSPGEN.generate(flatten(path))


def test_only_the_t_embed_declares_a_buttons_list():
    """One board, not two. The spec says "only two boards declare buttons", but
    the CardPuter declares `keyboard_matrix:` — it has no `buttons:` entry at
    all, so exactly one board's buttons carry an `intent:`.

    If this grows, the new board's buttons need `intent:` and its reachability
    needs looking at; failing here is what makes that a decision.
    """
    with_buttons = [p for p in tracked_board_yamls() if flatten(p).get("buttons")]
    assert len(with_buttons) == 1, with_buttons
    assert "lilygo-t-embed-cc1101" in with_buttons[0]


# --- Criterion 9: vocabulary reachability ----------------------------------


@pytest.mark.parametrize("path", tracked_board_yamls())
def test_reachability_is_computable_for_every_board_declaring_input(path):
    topo = topology(flatten(path))
    reach = reachable_intents(topo)
    assert reach <= VOCABULARY
    if topo is None:
        assert reach == frozenset()
    else:
        # A board that declares input reaches at least one intent; a declaration
        # that reaches nothing is a declaration that does nothing.
        assert reach, f"{path} declares input but reaches no intent"


def test_a_keyboard_board_reaches_the_whole_vocabulary():
    assert unreachable_intents(topology(board(keyboard_matrix=KEYBOARD_MATRIX))) == []


def test_the_t_embed_reaches_everything_but_the_second_axis():
    """The gap is reported, not silent — which is what criterion 9 asks for.

    A rotary on the primary axis plus VALIDATE and BACK cannot zoom. ui_wave is
    the only widget with a second axis, so on this board it pans and does not
    zoom. If somebody declares an `intent_long:` or a second axis on the
    T-Embed, this test is the record of what that changed.
    """
    path = next(p for p in tracked_board_yamls() if "lilygo-t-embed" in p)
    assert unreachable_intents(topology(flatten(path))) == ["ALT_NEXT", "ALT_PREV"]


def test_press_duration_is_what_widens_a_button_boards_vocabulary():
    """PO decision 4's real purpose, as an assertion.

    Two buttons reach two intents. The same two buttons with a long-press each
    reach four — and four is the threshold at which the on-screen keyboard
    becomes navigable, so TEXT and ERASE come with it. Without press duration a
    board short of buttons cannot state that it reaches the vocabulary; that is
    why long-press is in v1 rather than in a later API change.
    """
    short_only = topology(board(buttons=[
        {"pin": 0, "intent": "VALIDATE"},
        {"pin": 6, "intent": "BACK"},
    ]))
    assert reachable_intents(short_only) == {"VALIDATE", "BACK"}
    assert unreachable_intents(short_only) == [
        "ALT_NEXT", "ALT_PREV", "ERASE", "NEXT", "PREV", "TEXT"]

    with_long = topology(board(buttons=[
        {"pin": 0, "intent": "VALIDATE", "intent_long": "PREV", "long_ms": 600},
        {"pin": 6, "intent": "BACK", "intent_long": "NEXT", "long_ms": 600},
    ]))
    assert reachable_intents(with_long) == {
        "VALIDATE", "BACK", "PREV", "NEXT", "TEXT", "ERASE"}
    assert unreachable_intents(with_long) == ["ALT_NEXT", "ALT_PREV"]


def test_a_board_that_cannot_reach_a_needed_intent_is_reported():
    """The report is the deliverable: a caller asking for a set it cannot have
    gets the names back, not a partial success."""
    one_button = topology(board(buttons=[{"pin": 0, "intent": "VALIDATE"}]))
    needed = {"PREV", "NEXT", "VALIDATE", "BACK"}

    missing = unreachable_intents(one_button, needed)
    assert missing == ["BACK", "NEXT", "PREV"]
    assert set(missing) & needed == set(missing)

    # And a board that does reach the set reports nothing missing.
    assert unreachable_intents(
        topology(board(keyboard_matrix=KEYBOARD_MATRIX)), needed) == []


def test_the_vkbd_derivation_is_what_makes_text_reachable_without_a_keyboard():
    # Four intents navigate the on-screen keyboard, so TEXT and ERASE follow
    # from them. Three do not.
    four = topology(board(
        buttons=[{"pin": 0, "intent": "VALIDATE"}, {"pin": 6, "intent": "BACK"}],
        encoder={"a_pin": 4, "b_pin": 5}))
    assert {"TEXT", "ERASE"} <= reachable_intents(four)

    three = topology(board(
        buttons=[{"pin": 0, "intent": "VALIDATE"}],
        encoder={"a_pin": 4, "b_pin": 5}))
    assert not {"TEXT", "ERASE"} & reachable_intents(three)


def test_no_board_reaches_an_intent_outside_the_vocabulary():
    # Scope creep via the vocabulary is a design change needing sign-off; this
    # is the gate that makes adding a tenth intent a visible act.
    assert len(VOCABULARY) == 8
    assert VOCABULARY == {"PREV", "NEXT", "ALT_PREV", "ALT_NEXT",
                          "VALIDATE", "BACK", "TEXT", "ERASE"}


# --- The two halves of the vocabulary must not drift -----------------------

INPUT_IOCTL_H = REPO_ROOT / "kernel" / "duneos_kernel" / "include" / "duneos" / "input_ioctl.h"


def test_the_c_enum_matches_the_python_table_in_name_and_order():
    """bspgen writes INPUT_INTENT_<NAME> into the generated map and the kernel
    compiles it against this enum. A reorder on one side only would silently
    remap every board's buttons, so the order is asserted, not just the set."""
    text = INPUT_IOCTL_H.read_text(encoding="utf-8")
    body = text.split("INPUT_INTENT_NONE = 0", 1)[1].split("};", 1)[0]
    names = ["NONE"] + re.findall(r"INPUT_INTENT_([A-Z_]+)", body)
    assert names == list(INTENTS)


def test_the_batch_clamp_matches_the_c_header():
    text = INPUT_IOCTL_H.read_text(encoding="utf-8")
    m = re.search(r"#define\s+INPUT_INTENT_BATCH_MAX\s+(\d+)", text)
    assert m, "INPUT_INTENT_BATCH_MAX is gone from input_ioctl.h"
    assert int(m.group(1)) == BATCH_CLAMP


# --- The long-press mechanism needs a real clock on the event ---------------
#
# The host suite for the translation core fabricates time_ms, which is correct
# (LEG-38: construct what you assert against) and is exactly why it cannot see
# a producer that never sets one. Both userspace producers inject time_ms = 0,
# so unless the ring stamps it, every press/release interval is 0 and a button
# declaring intent_long can only ever yield its short intent. That is a dead
# mechanism whose reachability we would still be crediting.

DRV_INPUT_C = (REPO_ROOT / "kernel" / "duneos_kernel" / "src" / "drivers"
               / "input" / "drv_input.c")


def test_the_ring_stamps_a_missing_timestamp():
    src = DRV_INPUT_C.read_text(encoding="utf-8")
    push = src.split("void drv_input_push_event", 1)[1].split("\n}", 1)[0]
    assert "time_ms == 0" in push, \
        "drv_input_push_event no longer detects an unstamped event"
    assert "duneos_hal_monotonic_us" in push, \
        "drv_input_push_event no longer stamps time_ms from the monotonic clock"
    # It must not overwrite a timestamp the producer did set: enc_quadrature
    # stamps from the PCNT ISR and that value is the accurate one.
    assert "stamped.time_ms = " in push and push.count("stamped.time_ms") == 2


@pytest.mark.parametrize("producer", [
    "apps/system/btn_gpio/btn_gpio.c",
    "apps/system/kb_iomatrix/kb_iomatrix.c",
])
def test_a_producer_injecting_zero_relies_on_that_stamp(producer):
    """Both inject `.time_ms = 0`. This records the dependency, so removing the
    stamp above breaks a test that names the reason rather than silently
    disabling long-press on every board."""
    src = (REPO_ROOT / producer).read_text(encoding="utf-8")
    assert ".time_ms = 0" in src
