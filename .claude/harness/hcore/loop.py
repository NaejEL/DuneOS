# Loop engine entry: routing of the orchestrator input and machine selection.
#
# - LP-01 (partial): the loop is parameterized by profile.loop. The linear
#   machine (plan -> build -> verify) stays in factory.py and the machine with
#   consultation and interpretation phases in labrun.py; both stay thin facades
#   so the FR-* and LR-* assertions are unchanged. `machine_of` picks one from
#   the phase kinds the profile declares.
# - LP-02: phase kinds known to the engine (profiles.PHASE_KINDS); a profile
#   declaring another kind is refused by `profile validate`.
# - LP-05: routing of the input of the shared orchestrator.
#     artefact of an active profile whose status line is its `status_ready`
#       -> owner profile, mode build;
#     an entry keyword of a profile (e.g. "suivant")     -> that profile, mode plan;
#     --profile p                                         -> p, mode plan
#       (inactive: stop; unknown: usage error);
#     free text with one active profile                   -> that profile;
#     free text with several                              -> gate factory-run.profile,
#       default = the first profile of the stamp.
# - LP-06: the second instance of the orchestrator template (alias_of) forces
#   its profile.
# - LP-07: the journal is named after the invoked orchestrator and every line
#   carries the profile.

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import profiles


class LoopStop(RuntimeError):
    """Explicit stop (exit 1)."""


class LoopUsage(ValueError):
    """Usage error (exit 2)."""


machine_of = profiles.machine_of


_PROFILE_OPTION = re.compile(r"(?:(?<=\s)|^)--profile(?:[ 	]+(\"[^\"]*\"|'[^']*'|\S+))?(?=\s|$)")


def extract_profile_option(arguments: str) -> Tuple[str, Optional[str]]:
    """Takes --profile <value> out of $ARGUMENTS; the rest is handed back unchanged."""
    text = arguments or ""
    match = _PROFILE_OPTION.search(text)
    if match is None:
        return text, None
    if not match.group(1):
        raise LoopUsage("--profile expects a profile name")
    rest = (text[:match.start()] + text[match.end():]).strip()
    return rest, match.group(1).strip("'\"")


def _status_line(path: Path) -> Optional[str]:
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip().lower().startswith(("status", "statut")):
            return line.strip()
    return None


def _artefact(candidate: str, root: Path, active: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    candidate = candidate.strip().strip("'\"")
    if not candidate:
        return None
    try:
        path = Path(candidate)
        full = path if path.is_absolute() else Path(root) / path
        rel = full.resolve().relative_to(Path(root).resolve())
        if not full.is_file():
            return None
    except (OSError, ValueError):
        return None
    for prof in active:
        for art in prof["artefacts"]:
            if not art.get("status_ready") or rel.parts[:1] != (art["dir"],):
                continue
            if not Path(rel.name).match(art["glob"]):
                continue
            line = _status_line(full) or ""
            # BQ-07: a proposed, blocked or delivered spec is routed to its profile as well.
            lines = [art[k] for k in ("status_ready", "status_proposed", "status_blocked", "status_delivered")
                     if art.get(k)]
            lines += [v for v in (art.get("status_legacy") or {}).values() if v]
            if any(line == s or line.startswith(s + " ") for s in lines):
                return {"profile": prof["name"], "mode": "build", "artefact": rel.as_posix()}
    return None


def route(arguments: str, root: Path, active_names: Sequence[str], orchestrator: Optional[str] = None,
          profiles_dir: Optional[Path] = None, forced_profile: Optional[str] = None) -> Dict[str, Any]:
    """LP-05, LP-06. `arguments` is $ARGUMENTS without --models."""
    known = profiles.discover(profiles_dir)
    active = [known[n] for n in active_names if n in known]
    if not active:
        fallback = profiles.fallback_name(profiles_dir)
        active = [known[fallback]] if fallback else []
    rest, forced = extract_profile_option(arguments)
    forced = forced_profile or forced
    if orchestrator:
        owner = next((p for p in known.values() if p["orchestrator"]["name"] == orchestrator
                      and p["orchestrator"].get("alias_of")), None)
        if owner is not None:
            forced = owner["name"]
    if forced is not None:
        if forced not in known:
            raise LoopUsage("unknown profile: %r (known: %s)" % (forced, ", ".join(known)))
        if forced not in [p["name"] for p in active]:
            raise LoopStop("profile %s is not active" % forced)
        active = [known[forced]]
    hit = _artefact(rest, Path(root), active)
    if hit:
        return dict(hit, need=None, gate=None)
    word = rest.strip().strip("'\"").lower()
    for prof in active:
        if word and word in [k.lower() for k in prof.get("entry_keywords", [])]:
            return {"profile": prof["name"], "mode": "plan", "artefact": None, "need": rest, "gate": None}
    if len(active) == 1:
        return {"profile": active[0]["name"], "mode": "plan", "artefact": None, "need": rest, "gate": None}
    return {"profile": None, "mode": "plan", "artefact": None, "need": rest, "gate": "factory-run.profile",
            "candidates": [p["name"] for p in active], "default": active[0]["name"]}
