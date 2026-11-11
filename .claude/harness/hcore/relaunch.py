# Relaunch decision for a dead agent (FR-04, SP-05, SP-06).
#
# An agent that died (API error, dropped connection) or whose output is
# unusable is relaunched once. On the second failure: explicit failure. The
# relaunch consumes neither an iteration nor a round: the counter is not
# touched here.

from __future__ import annotations

RELAUNCH = "relaunch"
FAIL = "fail"
MAX_RELAUNCHES = 1


def on_dead(previous_failures: int) -> str:
    """Decision after a failure, given the number of failures already suffered on this slot."""
    if previous_failures < 0:
        raise ValueError("negative failure count")
    return RELAUNCH if previous_failures < MAX_RELAUNCHES else FAIL
