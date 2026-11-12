# T-shirt sizes, orderings and finding -> spec mapping of the audits.
#
# Findings are dicts of the FD-01 schema (hcore/findings.py): severity, axis,
# size, category, ... A milestone (legacy-audit) is read from the key
# "milestone", set by the skill after the assignment gate.
#
# - TS-01: ordered enumeration XS < S < M < L < XL. Development scale (XS <
#   30 min, S < 2 h, M half a day, L 1-2 days, XL to split) and GPU scale of
#   ai-lab-audit:66. The scale labels are the French texts shared with the
#   prompts, read from templates/deliverables.json.
# - TS-02: an XL is split into at least 2 specs <= L, chained
#   (legacy-audit:121, ai-lab-audit:134). XS findings are grouped by 4 at
#   most: same category and same milestone (legacy-audit:120) or same lens
#   (ai-lab-audit:134).
# - TS-03: orderings. LEG and COR: severity descending, then size ascending
#   (legacy-audit:76, ai-lab-audit:83). Q: severity descending, then cost
#   ascending. VIB: every critical first, all axes together; then security >
#   maintainability > evolvability; then difficulty ascending.
#   regression_risk never enters an ordering (vibecode-audit:105-106).
# - TS-04: quick wins = XS or S of severity major or critical, flagged
#   without moving up in the order (vibecode-audit:108).
# - TS-05: cumulative load and distribution of the sizes per milestone or
#   per selection (legacy-audit:87, :160; ai-lab-audit:95).

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

SIZES = ("XS", "S", "M", "L", "XL")
# The scale labels are French texts shared with the prompts: data, in
# templates/deliverables.json.
_TEXTS = json.loads((Path(__file__).resolve().parent.parent / "templates" / "deliverables.json")
                    .read_text(encoding="utf-8"))["tshirt"]
DEV_SCALE: Dict[str, str] = {s: _TEXTS["dev"][s] for s in SIZES}
GPU_SCALE: Dict[str, str] = {s: _TEXTS["gpu"][s] for s in SIZES}
SEVERITIES = ("critical", "major", "minor")
VIB_AXES = ("securite", "maintenabilite", "evolutivite")
MAX_SPLIT_SIZE = "L"
GROUP_MAX = 4
ORDERS = ("LEG", "COR", "Q", "VIB")
GROUPINGS = ("category-milestone", "lens")


class SizeError(ValueError):
    """Unknown size, severity, ordering or grouping."""


def size_rank(size: Optional[str]) -> int:
    """TS-01: rank of a size; a missing size sorts after XL."""
    if size is None:
        return len(SIZES)
    if size not in SIZES:
        raise SizeError("unknown size: %r (expected one of %s)" % (size, ", ".join(SIZES)))
    return SIZES.index(size)


def _severity_rank(severity: str) -> int:
    if severity not in SEVERITIES:
        raise SizeError("unknown severity: %r" % (severity,))
    return SEVERITIES.index(severity)


def _axis_rank(axis: str) -> int:
    return VIB_AXES.index(axis) if axis in VIB_AXES else len(VIB_AXES)


def sort_key(order: str) -> Callable[[Dict[str, Any]], Tuple[int, ...]]:
    """TS-03 (regression_risk is never read)."""
    if order in ("LEG", "COR", "Q"):
        # Q: the size of a question is the cost of its experiment (GPU scale).
        return lambda f: (_severity_rank(f["severity"]), size_rank(f.get("size")))
    if order == "VIB":
        # Severity first (every critical ahead of the first major, all axes
        # together), then the axis, then the difficulty.
        return lambda f: (_severity_rank(f["severity"]), _axis_rank(f["axis"]), size_rank(f.get("size")))
    raise SizeError("unknown ordering: %r (expected one of %s)" % (order, ", ".join(ORDERS)))


def ordered(findings: Iterable[Dict[str, Any]], order: str) -> List[Dict[str, Any]]:
    """TS-03: stable sort (equal findings keep their input order)."""
    return sorted(findings, key=sort_key(order))


def raised_criticals(findings: Iterable[Dict[str, Any]]) -> List[str]:
    """TS-03 (VIB): ids of the critical findings whose axis is not security,
    to be reported as raised on purpose."""
    return [f["id"] for f in findings if f["severity"] == "critical" and f.get("axis") != "securite"]


def quick_wins(findings: Iterable[Dict[str, Any]]) -> List[str]:
    """TS-04: XS or S of severity major or critical (order left unchanged)."""
    return [f["id"] for f in findings
            if f.get("size") in ("XS", "S") and f["severity"] in ("critical", "major")]


def _group_key(finding: Dict[str, Any], grouping: str) -> Tuple[Any, ...]:
    if grouping == "category-milestone":
        return (finding.get("category"), finding.get("milestone"))
    if grouping == "lens":
        return (finding.get("axis"),)
    raise SizeError("unknown grouping: %r (expected one of %s)" % (grouping, ", ".join(GROUPINGS)))


def plan_specs(findings: Sequence[Dict[str, Any]], grouping: str,
               parts: Optional[Dict[str, int]] = None) -> List[Dict[str, Any]]:
    """TS-02: finding -> spec mapping, in the order of `findings`.

    1 finding = 1 spec; XS findings with the same group key are grouped by 4
    at most (a group takes the place of its first member); an XL becomes
    parts[id] (default 2, at least 2) chained specs of size <= L, each one
    depending on the previous one. Returns the specs in order: {"findings",
    "kind" (single | group | part), "max_size", "part", "of", "depends_on"
    (index of the previous part or None)}."""
    parts = dict(parts or {})
    specs: List[Dict[str, Any]] = []
    open_groups: Dict[Tuple[Any, ...], int] = {}
    for f in findings:
        size = f.get("size")
        size_rank(size)
        if size == "XS":
            key = _group_key(f, grouping)
            index = open_groups.get(key)
            if index is not None and len(specs[index]["findings"]) < GROUP_MAX:
                specs[index]["findings"].append(f["id"])
                specs[index]["kind"] = "group"
                continue
            open_groups[key] = len(specs)
            specs.append({"findings": [f["id"]], "kind": "single", "max_size": "XS", "part": None,
                          "of": None, "depends_on": None})
        elif size == "XL":
            count = parts.get(f["id"], 2)
            if not isinstance(count, int) or count < 2:
                raise SizeError("an XL is split into at least 2 specs: %s" % f["id"])
            for n in range(1, count + 1):
                specs.append({"findings": [f["id"]], "kind": "part", "max_size": MAX_SPLIT_SIZE, "part": n,
                              "of": count, "depends_on": len(specs) - 1 if n > 1 else None})
        else:
            specs.append({"findings": [f["id"]], "kind": "single", "max_size": size, "part": None,
                          "of": None, "depends_on": None})
    return specs


def apply_cap(units: Sequence[Any], cap: int) -> Dict[str, List[Any]]:
    """The ceiling of a deliverable (--max-specs, --max-questions,
    --max-findings): the first `cap` units in the given order are kept, the
    excess goes to the 'not retained' list (LA-10, AA-02, VA-02)."""
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
        raise SizeError("the cap must be an integer >= 1, got %r" % (cap,))
    return {"kept": list(units[:cap]), "excess": list(units[cap:])}


def distribution(sizes: Iterable[Optional[str]]) -> Dict[str, int]:
    """TS-05: number of findings of each size, in the XS..XL order."""
    counts = {s: 0 for s in SIZES}
    for size in sizes:
        size_rank(size)
        if size is not None:
            counts[size] += 1
    return {s: n for s, n in counts.items() if n}


def load(findings: Iterable[Dict[str, Any]], by: str = "milestone") -> Dict[str, Any]:
    """TS-05: cumulative load (count and distribution of the sizes) per group
    (milestone, selection...) and in total; text form "2 XS, 1 M"."""
    groups: Dict[str, List[Optional[str]]] = {}
    every: List[Optional[str]] = []
    for f in findings:
        groups.setdefault(str(f.get(by)), []).append(f.get("size"))
        every.append(f.get("size"))

    def entry(sizes: List[Optional[str]]) -> Dict[str, Any]:
        dist = distribution(sizes)
        return {"count": len(sizes), "sizes": dist, "text": ", ".join("%d %s" % (n, s) for s, n in dist.items())}
    return {"groups": {k: entry(v) for k, v in groups.items()}, "total": entry(every)}
