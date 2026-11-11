# Iteration and round counters, and their bounds.
#
# - FR-03: factory-run, at most 3 Build/Verify iterations.
# - SP-01: ship-pr --rounds N, hard cap min(N, 3), default 3; N <= 0 or not an
#   integer -> explicit error (D8).
# - SP-02: ship-pr --themes "a;b;c", separator ';', at most 4, 3 by default;
#   empty list, more than 4 entries or a theme given twice -> explicit error
#   naming the culprit (D8: an invalid bound is an explicit error, never a
#   silent correction).

from __future__ import annotations

from typing import List, Optional

MAX_ITERATIONS = 3
MAX_ROUNDS = 3
DEFAULT_ROUNDS = 3
MAX_THEMES = 4
DEFAULT_THEMES = [
    "logic and edge cases",
    "regressions and call-site contracts",
    "internal consistency of the diff",
]


class BoundsError(ValueError):
    """Invalid bound passed as an argument (D8)."""


def parse_rounds(value: Optional[str]) -> int:
    if value is None:
        return DEFAULT_ROUNDS
    text = str(value).strip()
    try:
        n = int(text)
    except ValueError:
        raise BoundsError("--rounds expects a strictly positive integer, got: %r" % (value,))
    if n <= 0:
        raise BoundsError("--rounds expects a strictly positive integer, got: %d" % n)
    return min(n, MAX_ROUNDS)


def parse_themes(value: Optional[str]) -> List[str]:
    if value is None:
        return list(DEFAULT_THEMES)
    themes = [t.strip() for t in str(value).split(";") if t.strip()]
    if not themes:
        raise BoundsError("--themes is empty: at least one theme is required")
    if len(themes) > MAX_THEMES:
        raise BoundsError("--themes accepts at most %d themes, got: %d" % (MAX_THEMES, len(themes)))
    twice = sorted({t for t in themes if themes.count(t) > 1})
    if twice:
        raise BoundsError("--themes contains a duplicate theme: %s (one theme = one reviewer: "
                          "give each theme once)" % ", ".join(repr(t) for t in twice))
    return themes


def cap_reached(current: int, cap: int) -> bool:
    """True when the current counter has reached the cap."""
    return current >= cap
