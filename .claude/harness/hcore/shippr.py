# ship-pr review loop: rounds, reviewers, validators, registry.
#
# - SP-01 / SP-02: --rounds and --themes bounds (see counters).
# - SP-03: reviewer block; unusable output = dead agent.
# - SP-04: validator block.
# - SP-05: dead theme -> one relaunch -> uncovered theme -> merge refused;
#   no early exit on its silence.
# - SP-06: validator dead (twice) -> finding UNVALIDATED, neither posted nor fixed.
# - SP-07: deduplication against the registry and the pending candidates,
#   REJECTED included, before any validator; semantic duplicates (same
#   subject, other title) dropped by the prompt with drop_duplicate.
# - SP-08: loop exit by exhaustion (zero CONFIRMED, all themes covered) or by
#   the round cap. A round without CONFIRMED in which a theme is not covered
#   does not exit: next round (or cap). The diff reference of the next round
#   (SP-15) is HEAD before the round's fix commit, so that this commit is
#   reviewed.
# - SP-09: a confirmed finding not fixed is recorded NOT_FIXED with its reason.
# - SP-10: raw / confirmed / rejected / deduplicated counters per round.
# - RG-04 (wiring): the project registry read by 'start' (state "project")
#   sets aside a finding REJECTED or SETTLED in an earlier run, noted
#   previous=<status>@<source>; FIXED / NOT_FIXED are passed on as history.
# - MD-07 (wiring): reviewers and validators have a model role
#   (AGENT_ROLES); the assignment resolved at start is kept in the state.

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from . import counters, registry as registry_mod, relaunch, verdicts

REVIEWER = "reviewer"
VALIDATOR = "validator"
AGENT_ROLES = {REVIEWER: "shippr.reviewer", VALIDATOR: "shippr.validator"}


class LoopError(RuntimeError):
    """Refused transition: out-of-sequence call or inconsistent state."""


def new_state(target: str, rounds: int, themes: List[str],
              models: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    return {
        "models": dict(models or {}),
        "target": target,
        "rounds": rounds,
        "themes": list(themes),
        "round": 1,
        "exit": None,
        "uncovered": [],
        "candidates": {},
        "rounds_data": {"1": _new_round()},
        "prev_round_sha": None,
        "commits": [],
        "pr": None,
    }


def _new_round() -> Dict[str, Any]:
    return {"themes": {}, "failures": {}, "raw": 0, "confirmed": 0, "rejected": 0,
            "duplicates": 0, "unvalidated": 0}


def _round(state: Dict[str, Any]) -> Dict[str, Any]:
    if state["exit"] is not None:
        raise LoopError("review loop already exited: %s" % state["exit"])
    return state["rounds_data"][str(state["round"])]


def record_reviewer(state: Dict[str, Any], reg: registry_mod.Registry, theme: str,
                    output: Optional[str], dead: bool) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    rd = _round(state)
    if theme not in state["themes"]:
        raise LoopError("unknown theme: %r" % (theme,))
    if rd["themes"].get(theme) in ("ok", "uncovered"):
        raise LoopError("theme %r already returned its result for this round" % theme)
    log = {"iteration": state["round"], "verdict": "DEAD", "produced": 0, "rejected": 0}
    findings: List[Dict[str, Any]] = []
    reason = "dead agent"
    if not dead:
        try:
            findings = verdicts.parse_reviewer(output)
        except verdicts.UnusableOutput as exc:
            dead, reason = True, "unusable output: %s" % exc
    if dead:
        failures = rd["failures"].get(theme, 0)
        rd["failures"][theme] = failures + 1
        if relaunch.on_dead(failures) == relaunch.RELAUNCH:
            return {"action": "relaunch", "theme": theme, "reason": reason}, log
        rd["themes"][theme] = "uncovered"
        state["uncovered"].append({"round": state["round"], "theme": theme})
        return {"action": "uncovered", "theme": theme,
                "reason": "theme not covered after one relaunch (%s): merge refused" % reason}, log
    rd["themes"][theme] = "ok"
    pending = {c["key"] for c in state["candidates"].values()}
    project = state.get("project") or {}
    to_validate, duplicates = [], []
    for f in findings:
        key = registry_mod.finding_key(f["file"], f["title"])
        if reg.known(key) or key in pending:
            duplicates.append(dict(f, key=key, previous=reg.current(key) or "PENDING"))
            continue
        earlier = project.get(key)
        seen = "%s@%s" % (earlier["status"], earlier["source"]) if earlier else None
        if earlier and earlier["status"] in registry_mod.BLOCKING:
            # RG-04: rejected or settled in an earlier run (any skill): set aside.
            duplicates.append(dict(f, key=key, previous=seen))
            continue
        pending.add(key)
        fid = "R%d-F%d" % (state["round"], len(state["candidates"]) + 1)
        cand = dict(f, id=fid, key=key, round=state["round"], theme=theme, status="PENDING",
                    vfail=0, comment_id=None, outcome=None, reason=None, commit=None, thread_done=False)
        state["candidates"][fid] = cand
        item = {k: cand[k] for k in ("id", "file", "line", "side", "severity", "title", "description", "fix")}
        if seen:
            # RG-04: FIXED or NOT_FIXED earlier: history only (a regression comes back).
            item["history"] = cand["history"] = seen
        to_validate.append(item)
    rd["raw"] += len(findings)
    rd["duplicates"] += len(duplicates)
    log.update(verdict="FINDINGS" if findings else "NO_FINDING", produced=len(findings), rejected=len(duplicates))
    return {"action": "validate" if to_validate else "none", "theme": theme,
            "to_validate": to_validate, "duplicates": duplicates}, log


def record_validator(state: Dict[str, Any], reg: registry_mod.Registry, fid: str,
                     output: Optional[str], dead: bool) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    rd = _round(state)
    cand = state["candidates"].get(fid)
    if cand is None or cand["round"] != state["round"]:
        raise LoopError("unknown finding for this round: %r" % (fid,))
    if cand["status"] != "PENDING":
        raise LoopError("finding %s already decided: %s" % (fid, cand["status"]))
    log = {"iteration": state["round"], "verdict": "DEAD", "validated": 0, "rejected": 0}
    parsed = None
    reason = "dead agent"
    if not dead:
        try:
            parsed = verdicts.parse_validator(output)
        except verdicts.UnusableOutput as exc:
            dead, reason = True, "unusable output: %s" % exc
    if dead:
        failures = cand["vfail"]
        cand["vfail"] = failures + 1
        if relaunch.on_dead(failures) == relaunch.RELAUNCH:
            return {"action": "relaunch", "finding": fid, "reason": reason}, log
        cand["status"] = "UNVALIDATED"
        rd["unvalidated"] += 1
        reg.add(cand, "UNVALIDATED", round=state["round"], id=fid)
        return {"action": "unvalidated", "finding": fid,
                "reason": "validator died twice: finding not validated, neither posted nor fixed"}, log
    assert parsed is not None
    cand["status"] = parsed["verdict"]
    cand["reason"] = parsed["reason"]
    reg.add(cand, parsed["verdict"], round=state["round"], id=fid, reason=parsed["reason"])
    if parsed["verdict"] == "CONFIRMED":
        rd["confirmed"] += 1
        log.update(verdict="CONFIRMED", validated=1)
        return {"action": "post", "finding": fid}, log
    rd["rejected"] += 1
    log.update(verdict="REJECTED", rejected=1)
    return {"action": "drop", "finding": fid, "reason": parsed["reason"]}, log


def drop_duplicate(state: Dict[str, Any], reg: registry_mod.Registry, fid: str, of: str,
                   reason: Optional[str] = None) -> Dict[str, Any]:
    """SP-07, semantic dedup decided by the prompt: a PENDING candidate of the
    current round, same subject as `of` under another title, is set aside as a
    duplicate (state and registry), counted in the round's duplicates, and no
    longer awaits a validator."""
    rd = _round(state)
    cand = state["candidates"].get(fid)
    if cand is None or cand["round"] != state["round"]:
        raise LoopError("unknown finding for this round: %r" % (fid,))
    if cand["status"] != "PENDING":
        raise LoopError("finding %s already decided: %s" % (fid, cand["status"]))
    if of == fid:
        raise LoopError("a finding cannot be a duplicate of itself: %s" % fid)
    if of not in state["candidates"]:
        raise LoopError("--of %r: unknown finding, duplicate refused" % (of,))
    if state["candidates"][of]["status"] == "DUPLICATE":
        # The target must stay a finding that will be (or was) validated: a
        # chain or a cycle of duplicates would make the real finding vanish.
        raise LoopError("--of %s is itself a duplicate (of %s): point to the finding it duplicates"
                        % (of, state["candidates"][of].get("duplicate_of")))
    cand["status"] = "DUPLICATE"
    cand["duplicate_of"] = of
    cand["reason"] = reason or "duplicate of %s" % of
    rd["duplicates"] += 1
    reg.add(cand, "DUPLICATE", round=state["round"], id=fid, of=of, reason=cand["reason"])
    return {"action": "dropped", "finding": fid, "of": of, "reason": cand["reason"]}


def confirmed_of_round(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [c for c in state["candidates"].values()
            if c["round"] == state["round"] and c["status"] == "CONFIRMED"]


def set_outcome(state: Dict[str, Any], reg: registry_mod.Registry, fid: str, fixed: bool,
                reason: Optional[str] = None, commit: Optional[str] = None) -> None:
    """SP-09: records FIXED (with the commit) or NOT_FIXED (with the reason)."""
    cand = state["candidates"].get(fid)
    if cand is None or cand["round"] != state["round"] or cand["status"] != "CONFIRMED":
        raise LoopError("%s is not a CONFIRMED finding of the current round" % fid)
    if cand["outcome"] is not None:
        raise LoopError("%s is already recorded: %s" % (fid, cand["outcome"]))
    if not fixed and not (reason or "").strip():
        raise LoopError("a finding not fixed requires its reason: %s" % fid)
    cand["outcome"] = "FIXED" if fixed else "NOT_FIXED"
    cand["commit"] = commit
    cand["reason"] = reason if not fixed else cand["reason"]
    reg.add(cand, cand["outcome"], round=state["round"], id=fid, commit=commit,
            reason=None if fixed else reason)


def mark_pre_fix(state: Dict[str, Any], sha: str) -> None:
    """SP-15: HEAD just before the round's first fix commit."""
    _round(state).setdefault("pre_fix_sha", sha)


def end_round(state: Dict[str, Any], head_sha: Optional[str]) -> Dict[str, Any]:
    """SP-08: decides the loop exit at the end of the current round.

    head_sha (current HEAD) is used as the reference only if the round
    committed no fix: otherwise the reference is the HEAD before that fix."""
    rd = _round(state)
    missing = [t for t in state["themes"] if rd["themes"].get(t) not in ("ok", "uncovered")]
    if missing:
        raise LoopError("theme(s) with neither a usable block nor an exhausted relaunch: " + ", ".join(missing))
    pending = [c["id"] for c in state["candidates"].values()
               if c["round"] == state["round"] and c["status"] == "PENDING"]
    if pending:
        raise LoopError("finding(s) without validation: " + ", ".join(pending))
    open_ = [c["id"] for c in confirmed_of_round(state) if c["outcome"] is None]
    if open_:
        raise LoopError("CONFIRMED finding(s) with neither a fix nor a recorded reason: " + ", ".join(open_))
    unpushed = state.get("unpushed")
    if unpushed and unpushed["round"] == state["round"]:
        raise LoopError("fix commit %s is not pushed: run 'commit' again" % unpushed["hash"])
    uncovered_now = [u["theme"] for u in state["uncovered"] if u["round"] == state["round"]]
    counts = round_counts(rd, state["round"])
    if rd["confirmed"] == 0 and not uncovered_now:
        state["exit"] = "exhaustion"
    elif counters.cap_reached(state["round"], state["rounds"]):
        state["exit"] = "cap"
    if state["exit"] is not None:
        return {"exit": state["exit"], "round": state["round"], "counts": counts,
                "uncovered": state["uncovered"]}
    state["prev_round_sha"] = rd.get("pre_fix_sha") or head_sha
    state["round"] += 1
    state["rounds_data"][str(state["round"])] = _new_round()
    return {"exit": None, "next_round": state["round"], "counts": counts,
            "uncovered": state["uncovered"]}


def round_counts(rd: Dict[str, Any], number: int) -> Dict[str, int]:
    """SP-10: counters of a round."""
    return {"round": number, "raw": rd["raw"], "confirmed": rd["confirmed"],
            "rejected": rd["rejected"], "duplicates": rd["duplicates"],
            "unvalidated": rd["unvalidated"]}


def summary(state: Dict[str, Any]) -> Dict[str, Any]:
    rows = [round_counts(state["rounds_data"][k], int(k))
            for k in sorted(state["rounds_data"], key=int)]
    cands = list(state["candidates"].values())
    return {
        "rounds": rows,
        "exit": state["exit"],
        "uncovered": state["uncovered"],
        "commits": state["commits"],
        "resolved": [c["id"] for c in cands if c["outcome"] == "FIXED" and c["thread_done"]],
        "left_open": [{"id": c["id"], "reason": c["reason"]} for c in cands if c["outcome"] == "NOT_FIXED"],
        "unvalidated": [c["id"] for c in cands if c["status"] == "UNVALIDATED"],
    }


def merge_refusal(state: Dict[str, Any]) -> Optional[str]:
    """SP-05: reason for refusing the merge, or None if the gate can be offered."""
    if state["exit"] is None:
        return "merge refused: the review loop is not finished"
    if state["uncovered"]:
        angles = ", ".join("%s (round %d)" % (u["theme"], u["round"]) for u in state["uncovered"])
        return ("merge refused: theme(s) not covered, angle not reviewed: %s. "
                "Run /ship-pr again to get a complete review." % angles)
    return None
