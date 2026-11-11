"""Input intent vocabulary, shared by bspgen (kernel map) and boardgen (app header).

One table, two generators. The vocabulary is closed: it is derived from what the
sdk/ui widgets dispatch on, and anything beyond it is a design change.
"""

from __future__ import annotations

# Order fixes the C enum values in <duneos/input_ioctl.h>. Append only.
INTENTS = (
    "NONE",
    "PREV",
    "NEXT",
    "ALT_PREV",
    "ALT_NEXT",
    "VALIDATE",
    "BACK",
    "TEXT",
    "ERASE",
)

# Everything a widget can ask for. NONE is the absence of an intent.
VOCABULARY = frozenset(INTENTS) - {"NONE"}

# What a physical button injects so the unmigrated raw readers keep working.
# A button declares one `intent:`; this derives the legacy key code from it, so
# the board still has exactly one field describing the button.
#
# TEXT is absent on purpose: a single button cannot carry a character. A board
# reaches TEXT through the on-screen keyboard (see reachable_intents).
INTENT_TO_KEY = {
    "PREV":     "KEY_UP",
    "NEXT":     "KEY_DOWN",
    "ALT_PREV": "KEY_LEFT",
    "ALT_NEXT": "KEY_RIGHT",
    "VALIDATE": "KEY_ENTER",
    "BACK":     "KEY_ESC",
    "ERASE":    "KEY_BACKSPACE",
}

BUTTON_INTENTS = frozenset(INTENT_TO_KEY)

# A rotary axis drives one pair. `alt` is the second axis ui_wave zooms with.
#
# counts_per_step is in the unit the axis EMITS, which for a rotary is detents:
# the arch HAL watches +/-DETENT_COUNTS and clears, so one notch arrives as a
# single REL_WHEEL of +/-1. Items-per-detent is therefore 1 / counts_per_step —
# raising it slows navigation down. See the ROADMAP item on making DETENT_COUNTS
# board-declared; until then the raw-count half of the ratio is not expressible.
AXIS_INTENTS = {
    "primary": ("PREV", "NEXT"),
    "alt":     ("ALT_PREV", "ALT_NEXT"),
}

# Upper bound on intents the translation core emits from one read() batch, so a
# fast spin cannot flood a redraw loop. Mirrored by INPUT_INTENT_BATCH_MAX.
BATCH_CLAMP = 8

DEFAULT_LONG_MS = 600


class InputTopologyError(Exception):
    """A board declares input in a way no generator can honour."""


def _buttons(board: dict) -> list[dict]:
    btns = board.get("buttons")
    return btns if isinstance(btns, list) else []


def intent_of(button: dict, index: int) -> str:
    """The declared `intent:` of one button, validated.

    Rejects the pre-cycle `code:` spelling by name: a board schema that accepts
    a stale field and silently emits nothing is the failure this replaces.
    """
    if "code" in button:
        raise InputTopologyError(
            f"buttons[{index}] declares 'code:' — that spelling was replaced by "
            f"'intent:' this cycle. One button, one field: write "
            f"'intent: VALIDATE' (not 'code: KEY_ENTER'); the key code a button "
            f"injects is derived from its intent. Valid intents: "
            f"{', '.join(sorted(BUTTON_INTENTS))}")
    intent = button.get("intent")
    if intent is None:
        raise InputTopologyError(
            f"buttons[{index}] declares no 'intent:'. Valid intents: "
            f"{', '.join(sorted(BUTTON_INTENTS))}")
    intent = str(intent).upper()
    if intent not in BUTTON_INTENTS:
        raise InputTopologyError(
            f"buttons[{index}] declares intent '{button['intent']}', which no "
            f"button can express. Valid intents: "
            f"{', '.join(sorted(BUTTON_INTENTS))}")
    return intent


def _long_intent(button: dict, index: int) -> tuple[str, int]:
    long_intent = button.get("intent_long")
    if long_intent is None:
        if "long_ms" in button:
            raise InputTopologyError(
                f"buttons[{index}] declares 'long_ms:' with no 'intent_long:' — "
                f"a threshold with nothing to resolve to")
        return "NONE", 0
    long_intent = str(long_intent).upper()
    if long_intent not in BUTTON_INTENTS:
        raise InputTopologyError(
            f"buttons[{index}] declares intent_long '{button['intent_long']}', "
            f"which no button can express. Valid intents: "
            f"{', '.join(sorted(BUTTON_INTENTS))}")
    long_ms = int(button.get("long_ms", DEFAULT_LONG_MS))
    if long_ms <= 0:
        raise InputTopologyError(
            f"buttons[{index}] declares long_ms={long_ms}; a long-press "
            f"threshold must be positive")
    return long_intent, long_ms


def button_codes(board: dict) -> list[str]:
    """The key code each declared button injects, in declaration order."""
    return [INTENT_TO_KEY[intent_of(b, i)] for i, b in enumerate(_buttons(board))]


def topology(board: dict) -> dict | None:
    """The board's input topology, or None when it declares no input source.

    None is an answer, not a failure: it is the explicit "no input source"
    verdict a board without a keyboard, a button or an encoder must get.
    """
    btns = _buttons(board)
    enc = board.get("encoder")
    has_keyboard = bool(board.get("keyboard_matrix") or board.get("keyboard"))

    if not (btns or enc or has_keyboard):
        return None

    keys = []
    seen = {}
    for i, b in enumerate(btns):
        intent = intent_of(b, i)
        long_intent, long_ms = _long_intent(b, i)
        code = INTENT_TO_KEY[intent]
        if code in seen:
            raise InputTopologyError(
                f"buttons[{i}] and buttons[{seen[code]}] both declare intent "
                f"'{intent}'. Two buttons injecting one key code are "
                f"indistinguishable to every reader of /dev/input/event0")
        seen[code] = i
        keys.append({
            "code": code,
            "intent": intent,
            "intent_long": long_intent,
            "long_ms": long_ms,
        })

    axes = []
    if isinstance(enc, dict):
        axis = str(enc.get("axis", "primary")).lower()
        if axis not in AXIS_INTENTS:
            raise InputTopologyError(
                f"encoder declares axis '{enc.get('axis')}'; valid axes: "
                f"{', '.join(sorted(AXIS_INTENTS))}")
        cps = int(enc.get("counts_per_step", 1))
        if cps < 1:
            raise InputTopologyError(
                f"encoder declares counts_per_step={cps}; one step cannot take "
                f"fewer than one count")
        neg, pos = AXIS_INTENTS[axis]
        axes.append({
            "code": "REL_WHEEL",
            "intent_neg": neg,
            "intent_pos": pos,
            "counts_per_step": cps,
        })

    return {"has_keyboard": has_keyboard, "keys": keys, "axes": axes}


def reachable_intents(topo: dict | None) -> frozenset[str]:
    """Every intent a board's declared topology can produce.

    A full keyboard reaches the whole vocabulary. Buttons reach their short and
    long intents — which is why press duration is in v1: it is the only way a
    board short of buttons widens what it can say. A board that can navigate the
    on-screen keyboard reaches TEXT and ERASE through it, with no extra hardware.
    """
    if topo is None:
        return frozenset()
    if topo.get("has_keyboard"):
        return frozenset(VOCABULARY)

    reach = set()
    for k in topo["keys"]:
        reach.add(k["intent"])
        if k["intent_long"] != "NONE":
            reach.add(k["intent_long"])
    for a in topo["axes"]:
        reach.add(a["intent_neg"])
        reach.add(a["intent_pos"])

    if {"PREV", "NEXT", "VALIDATE", "BACK"} <= reach:
        reach |= {"TEXT", "ERASE"}
    return frozenset(reach)


def unreachable_intents(topo: dict | None,
                        required: frozenset[str] | set[str] = VOCABULARY
                        ) -> list[str]:
    """Which of `required` the board cannot reach — empty when it reaches all."""
    return sorted(set(required) - reachable_intents(topo))
