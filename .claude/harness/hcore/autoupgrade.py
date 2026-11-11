# Automatic upgrade proposed by /harness-doctor (UA-01 to UA-04).
#
# Contract of the upgrade plan (Phase 3, Q25): {changes: [{path, kind, block,
# unmodified?}], divergent_blocks: [...]}, kind in block|outside|migration|new|
# removed|core|stamp.
# - UA-01: under `auto` the upgrade is launched if and only if the plan is not
#   empty, every change is a `block`, a `stamp` or an untouched `core` file
#   (`unmodified: true`), and no block diverges.
# - UA-02: otherwise the command is proposed, never launched, with the changes
#   in cause; under `gate` the gate `harness-doctor.upgrade` is asked.
# - UA-03: a scan only proposes, one line per project (done by the caller).
# - UA-04: a plan that is empty after an upgrade means: nothing to do.

from __future__ import annotations

from typing import Any, Dict, List

# Contract kinds `block` and `stamp`, plus the equivalent kinds of the real plan of `upgrade plan`
# (BLOCKS_ONLY of upgrade.py: header, merge by union, copy of the core, local).
SAFE_KINDS = ("block", "stamp", "header", "merge", "copy", "local")
CODE_INDEX_MARKS = ("rgkb", ".knowledge", "code-index")
OWNERS = {"software-factory": "/software-factory --upgrade", "code-index": "/code-index --upgrade"}


def path_of(change: Dict[str, Any]) -> str:
    return str(change.get("path") or change.get("file") or "")


def divergent_of(plan: Dict[str, Any]) -> List[Any]:
    """Blocks edited by hand: `divergent_blocks`, or the files of the convert gate of a real plan."""
    found = list(plan.get("divergent_blocks", []))
    found += list(((plan.get("gate") or {}).get("files")) or [])
    return found


def owner_of(change: Dict[str, Any]) -> str:
    """The skill that owns a change: an explicit `skill`, else code-index for its files."""
    if change.get("skill") in OWNERS:
        return change["skill"]
    text = "%s %s" % (path_of(change), change.get("block") or "")
    return "code-index" if any(m in text for m in CODE_INDEX_MARKS) else "software-factory"


def blocking_changes(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The changes that forbid the automatic launch (UA-01)."""
    out = []
    for change in plan.get("changes", []):
        kind = change.get("kind")
        if kind in SAFE_KINDS or (kind == "core" and change.get("unmodified") is True):
            continue
        out.append(change)
    return out


def decide(plan: Dict[str, Any], policy: str) -> Dict[str, Any]:
    """UA-01 and UA-02: {action: none|apply|propose|ask, causes, skills, commands}."""
    changes = plan.get("changes", [])
    divergent = divergent_of(plan)
    if not changes and not divergent:
        return {"action": "none", "causes": [], "skills": [], "commands": []}
    skills = sorted({owner_of(c) for c in changes}) or ["software-factory"]
    commands = [OWNERS[s] for s in skills]
    causes = [{"path": path_of(c), "kind": c.get("kind")} for c in blocking_changes(plan)]
    causes += [{"path": d if isinstance(d, str) else path_of(d), "kind": "divergent-block"} for d in divergent]
    if policy != "auto":
        return {"action": "ask", "causes": causes, "skills": skills, "commands": commands}
    action = "propose" if causes else "apply"
    return {"action": action, "causes": causes, "skills": skills, "commands": commands}
