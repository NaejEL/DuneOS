# Computed prioritization of the audits under the `auto` policy (PZ-01 to PZ-05).
#
# - PZ-01: "make it reliable first": legacy set = risk first, vibecode set =
#   severity first with the minor ones detailed, research questions = PZ-04.
# - PZ-02: milestones computed from the severity (1 critical, 2 major, 3 minor);
#   dependency closure: a prerequisite moves up to the earliest milestone of its
#   dependents; empty milestones are removed and the rest renumbered (1 to 3).
# - PZ-03: the assignment is the milestone of each finding, nothing is excluded;
#   the cap `--max-specs` applies afterwards, in the order of the milestones.
# - PZ-04: research questions: severity decreasing, then cost increasing (TS-03).
# - PZ-05: `rank` gives milestones, ranks and the `Priority` of each finding.
# - CH-04: `chain_plan` orders the specs written by an audit for the queue.

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from . import specfile, tshirt

MILESTONE_OF = {"critical": 1, "major": 2, "minor": 3}
TITLES = {1: "Critiques", 2: "Majeurs", 3: "Mineurs"}
FRAMING = {"legacy": {"order": "risk first"},
           "vibecode": {"order": "Severity first", "minor": "Detail them"}}


def _severity(f: Dict[str, Any]) -> int:
    return MILESTONE_OF.get(f.get("severity"), 3)


def milestones(findings: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    """PZ-02: {id: milestone number}, after closure and renumbering."""
    items = {f["id"]: f for f in findings}
    level = {fid: _severity(f) for fid, f in items.items()}
    changed = True
    while changed:
        changed = False
        for fid, f in items.items():
            for dep in f.get("depends_on") or []:
                if dep in level and level[dep] > level[fid]:
                    level[dep] = level[fid]
                    changed = True
    used = sorted(set(level.values()))
    renumber = {old: new for new, old in enumerate(used, 1)}
    return {fid: renumber[n] for fid, n in level.items()}


def rank(findings: List[Dict[str, Any]]) -> Dict[str, Any]:
    """PZ-05: {milestones: [{number, title, ids}], assignment, ranks, priority}.
    Inside a milestone: prerequisites first, then severity, size, id."""
    findings = [f for f in findings if f.get("id")]
    number_of = milestones(findings)
    by_id = {f["id"]: f for f in findings}
    # Title of each renumbered milestone: the one of the severity it came from.
    source: Dict[int, int] = {}
    for f in findings:
        n = number_of[f["id"]]
        source[n] = min(source.get(n, 99), _severity(f))

    def order_key(f: Dict[str, Any]):
        return (_severity(f), tshirt.size_rank(f.get("size")), f["id"])

    ordered: List[str] = []
    for number in sorted(source):
        remaining = [f["id"] for f in sorted((f for f in findings if number_of[f["id"]] == number), key=order_key)]
        while remaining:
            ready = [fid for fid in remaining
                     if all(d not in remaining or d == fid for d in by_id[fid].get("depends_on") or [])]
            take = ready[0] if ready else remaining[0]    # a dependency cycle is broken by the order key
            ordered.append(take)
            remaining.remove(take)
    ranks = {fid: n for n, fid in enumerate(ordered, 1)}
    return {
        "milestones": [{"number": n, "title": TITLES[source[n]], "ids": [fid for fid in ordered if number_of[fid] == n]}
                       for n in sorted(source)],
        "assignment": number_of, "ranks": ranks, "priority": dict(ranks),
    }


def apply_cap(ordered_ids: List[str], cap: Optional[int]) -> Dict[str, List[str]]:
    """PZ-03: the cap keeps the first N in the order of the milestones; the excess is listed."""
    if cap is None or cap >= len(ordered_ids):
        return {"kept": list(ordered_ids), "excess": []}
    return {"kept": list(ordered_ids[:cap]), "excess": list(ordered_ids[cap:])}


def rank_questions(findings: List[Dict[str, Any]]) -> List[str]:
    """PZ-04: ids of research questions, severity decreasing then cost increasing."""
    def key(f: Dict[str, Any]):
        return (_severity(f), tshirt.size_rank(f.get("size")), f["id"])
    return [f["id"] for f in sorted((f for f in findings if f.get("id")), key=key)]


def chain_plan(root: Path, only: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """CH-04: the specs/ files with a proposed status, ordered by `Priority` (absent = last) then name."""
    folder = Path(root) / "specs"
    rows: List[Dict[str, Any]] = []
    wanted = {Path(o).name for o in only or []}
    for path in sorted(folder.glob("SPEC-*.md")) if folder.is_dir() else []:
        if wanted and path.name not in wanted:
            continue
        parsed = specfile.parse(path.read_text(encoding="utf-8-sig", errors="replace"))
        if parsed["role"] != "proposed":
            continue
        rows.append({"file": "specs/" + path.name, "priority": parsed["priority"], "blocking": parsed["blocking"]})
    rows.sort(key=lambda r: (r["priority"] is None, r["priority"] or 0, r["file"]))
    return rows
