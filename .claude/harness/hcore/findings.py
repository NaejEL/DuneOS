# Single finding schema of the skills: validation, adapters from the agent
# formats, validator verdicts and conversions.
#
# - FD-01: schema. Required: id, source (skill), severity in {critical, major,
#   minor}, axis (lens, family, axis or theme), title, status. evidence:
#   {kind: code|journal|structural, file, lines: {start, end}, excerpt,
#   journal_date}. occurrences[]: {file, line, excerpt}, with count and
#   sampled. fix_commit. Optional: nature, size, depends_on, impact,
#   remediation, regression_risk, risk_rationale, reason, raw (original
#   output); plus category (legacy and vibecode families), settled_by,
#   deliverable_status, history, previous, split_from, milestone.
# - FD-02: statuses, a superset of D2, with their allowed transitions
#   (TRANSITIONS; "history" lists the previous statuses in order).
#   Deliverable statuses (A_FAIRE, EN_COURS, FAIT, REPORTE, TRANCHEE (journal
#   <date>)) are a separate field, deliverable_status, kept in French: shared
#   format.
# - FD-03: validator (validate, CLI 'finding validate <file>'): exit 0 when
#   conforming, otherwise exit 1 and the list of the errors.
# - FD-04: an excerpt longer than 3 lines is a warning, never a rejection.
# - FD-05: lossless adapters from the 6 agent formats (FORMATS); "raw" holds
#   the input unchanged. The Verifier formats (factory, lab) carry neither a
#   line nor an excerpt: their conversion is partial by nature (decision X7).
# - FD-06: single parser of the validator verdicts, superset of the four
#   formats (ship-pr, legacy-audit, ai-lab-audit, vibecode-audit). Each
#   correction field is read only for the source that had it (VERDICT_FIELDS):
#   a field foreign to the source is ignored, as it was before this lot. The
#   SPLIT value exists only for vibecode-audit: elsewhere the output is
#   unusable. An unusable output = dead agent.
# - FD-07: application of a verdict: severity, axis, size, nature and
#   difficulty corrections; false_occurrences removed from the sample and
#   subtracted from count (also those outside the sample), unless the
#   validator gives corrected_count (0, the literal of the prompt template,
#   means "not given"); split (parts CONFIRMED, decision Q-F1).
# - FD-08: finding -> factory spec (template templates/spec-factory.md); the
#   judgment sections are given by the skill in a file; imposed clauses:
#   history rewriting out of scope for secrets, two acceptance criteria for
#   the "fuites" lens.
# - FD-09: finding -> rgkb note {subject, text, tags, kind, source}, accepted
#   as is by 'rgkb.py note-add'.
# - FD-10: finding -> PR comment, byte for byte the body ghflow.comment_body
#   posted at BASE1 (ghflow now calls it).
# - FD-11: forbidden strings in any rendered deliverable: "Statut :" followed
#   by the ready status of an artefact of a profile (PF-05).

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from string import Template
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import journal, profiles, verdicts

SEVERITIES = ("critical", "major", "minor")
SIZES = ("XS", "S", "M", "L", "XL")
EVIDENCE_KINDS = ("code", "journal", "structural")
NATURES = ("correction", "question")
RISKS = ("faible", "moyen", "eleve")
VIB_AXES = ("securite", "maintenabilite", "evolutivite")
_VERIFIERS = profiles.verifier_formats()
# FD-05: the Verifier formats are the verify agents of the profiles; the kind taxonomy comes with them.
LAB_VERIFIER_KINDS = tuple(k for v in _VERIFIERS.values() for k in v["kinds"])
MAX_LISTED_OCCURRENCES = 5

PROPOSED, CONFIRMED, REJECTED, DUPLICATE = "PROPOSED", "CONFIRMED", "REJECTED", "DUPLICATE"
FIXED, SETTLED, UNVALIDATED, NOT_FIXED = "FIXED", "SETTLED", "UNVALIDATED", "NOT_FIXED"
STATUSES = (PROPOSED, CONFIRMED, REJECTED, DUPLICATE, FIXED, SETTLED, UNVALIDATED, NOT_FIXED)
TRANSITIONS: Dict[str, Tuple[str, ...]] = {
    PROPOSED: (CONFIRMED, REJECTED, DUPLICATE, UNVALIDATED, SETTLED),
    UNVALIDATED: (CONFIRMED, REJECTED, SETTLED),
    CONFIRMED: (FIXED, NOT_FIXED, SETTLED),
    NOT_FIXED: (FIXED, SETTLED),
    REJECTED: (),
    DUPLICATE: (),
    FIXED: (),
    SETTLED: (),
}
DELIVERABLE = re.compile(r"^(A_FAIRE|EN_COURS|FAIT|REPORTE|TRANCHEE \(journal \d{4}-\d{2}-\d{2}\))$")
SETTLED_BY = re.compile(r"^(journal \d{4}-\d{2}-\d{2}|user)$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
EXCERPT_MAX_LINES = 3

REQUIRED = ("id", "source", "severity", "axis", "title", "status")
TEXT_FIELDS = ("impact", "remediation", "risk_rationale", "reason", "category", "fix_commit",
               "previous", "split_from")
OPTIONAL = TEXT_FIELDS + ("milestone", "evidence", "occurrences", "count", "sampled", "nature", "size", "depends_on",
                          "regression_risk", "raw", "settled_by", "deliverable_status", "history")

FORMATS = ("shippr-reviewer", "legacy-auditor", "lab-audit-auditor", "vibecode-auditor") + tuple(_VERIFIERS)
FORMAT_SOURCE = {"shippr-reviewer": "ship-pr", "legacy-auditor": "legacy-audit",
                 "lab-audit-auditor": "ai-lab-audit", "vibecode-auditor": "vibecode-audit",
                 **{agent: v["source"] for agent, v in _VERIFIERS.items()}}
def _families(debt: str) -> Dict[str, str]:
    return {l["prefix"]: l["name"] for p in profiles.discover().values() for l in p["lenses"] if l["debt"] == debt}


LEGACY_FAMILIES = _families("accumulation")
VIB_FAMILIES = _families("generation")

# FD-11, PF-05: built from the ready statuses of the profiles ("Status: <X>" or "Statut : <X>" -> any accent of E).
FORBIDDEN = re.compile(r"Stat(?:us|ut)[ \t]*:[ \t]*(%s)" % "|".join(
    re.escape(s.split(":", 1)[1].strip()).replace("E", "[EÉ]") for s in profiles.ready_statuses()))
TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
SPEC_TEMPLATE = TEMPLATES / "spec-factory.md"
# Deliverable texts (shared format) are data, not code strings.
DELIVERABLES = TEMPLATES / "deliverables.json"


def deliverable_texts() -> Dict[str, Any]:
    """Texts imposed in the rendered specs (templates/deliverables.json)."""
    return json.loads(DELIVERABLES.read_text(encoding="utf-8"))["spec"]


class FindingError(ValueError):
    """Invalid finding, refused conversion or out-of-sequence transition (exit 2)."""


# ------------------------------------------------------- validation (FD-03)

def _excerpt_warning(where: str, excerpt: Any, warnings: List[str]) -> None:
    if isinstance(excerpt, str) and len(excerpt.splitlines()) > EXCERPT_MAX_LINES:
        warnings.append("%s: excerpt of %d lines (more than %d)" % (where, len(excerpt.splitlines()),
                                                                    EXCERPT_MAX_LINES))


def _is_int(value: Any, minimum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _check_evidence(ev: Any, where: str, errors: List[str], warnings: List[str]) -> None:
    if not isinstance(ev, dict):
        errors.append("%s: evidence must be an object" % where)
        return
    unknown = set(ev) - {"kind", "file", "lines", "excerpt", "journal_date"}
    if unknown:
        errors.append("%s: evidence: unknown field(s): %s" % (where, ", ".join(sorted(unknown))))
    kind = ev.get("kind")
    if kind not in EVIDENCE_KINDS:
        errors.append("%s: evidence.kind must be one of %s" % (where, "|".join(EVIDENCE_KINDS)))
    file = ev.get("file")
    if file is not None and not isinstance(file, str):
        errors.append("%s: evidence.file must be a string or null" % where)
    if kind == "code" and not (isinstance(file, str) and file.strip()):
        errors.append("%s: evidence.kind=code requires evidence.file" % where)
    date = ev.get("journal_date")
    if date is not None and not (isinstance(date, str) and DATE.match(date)):
        errors.append("%s: evidence.journal_date must be YYYY-MM-DD or null" % where)
    if kind == "journal" and date is None:
        errors.append("%s: evidence.kind=journal requires evidence.journal_date" % where)
    lines = ev.get("lines")
    if lines is not None:
        if not (isinstance(lines, dict) and _is_int(lines.get("start"), 1) and _is_int(lines.get("end"), 1)
                and lines["end"] >= lines["start"] and set(lines) == {"start", "end"}):
            errors.append("%s: evidence.lines must be {start, end}, integers >= 1, end >= start" % where)
    excerpt = ev.get("excerpt")
    if excerpt is not None and not isinstance(excerpt, str):
        errors.append("%s: evidence.excerpt must be a string" % where)
    _excerpt_warning(where + ": evidence", excerpt, warnings)


def _check_occurrences(f: Dict[str, Any], where: str, errors: List[str], warnings: List[str]) -> None:
    occ = f.get("occurrences")
    if occ is None:
        if "count" in f or "sampled" in f:
            errors.append("%s: count and sampled go with occurrences" % where)
        return
    if not isinstance(occ, list):
        errors.append("%s: occurrences must be a list" % where)
        return
    for i, o in enumerate(occ):
        at = "%s: occurrence %d" % (where, i)
        if not isinstance(o, dict) or set(o) - {"file", "line", "excerpt"}:
            errors.append("%s must be an object {file, line, excerpt}" % at)
            continue
        if not (isinstance(o.get("file"), str) and o["file"].strip()):
            errors.append("%s: file missing" % at)
        if o.get("line") is not None and not _is_int(o.get("line"), 1):
            errors.append("%s: line must be an integer >= 1 or null" % at)
        if o.get("excerpt") is not None and not isinstance(o.get("excerpt"), str):
            errors.append("%s: excerpt must be a string" % at)
        _excerpt_warning(at, o.get("excerpt"), warnings)
    count = f.get("count", len(occ))
    if not _is_int(count, 0) or count < len(occ):
        errors.append("%s: count must be an integer >= the number of occurrences" % where)
    if "sampled" in f and not isinstance(f["sampled"], bool):
        errors.append("%s: sampled must be a boolean" % where)


def _check_status(f: Dict[str, Any], where: str, errors: List[str]) -> None:
    status = f.get("status")
    if status not in STATUSES:
        errors.append("%s: status must be one of %s" % (where, "|".join(STATUSES)))
        return
    history = f.get("history", [])
    if not isinstance(history, list) or any(h not in STATUSES for h in history):
        errors.append("%s: history must be a list of statuses" % where)
        return
    chain = list(history) + [status]
    for before, after in zip(chain, chain[1:]):
        if after not in TRANSITIONS[before]:
            errors.append("%s: transition %s -> %s not allowed" % (where, before, after))
    if status == FIXED and not (isinstance(f.get("fix_commit"), str) and f["fix_commit"].strip()):
        errors.append("%s: status FIXED requires fix_commit" % where)
    if status == NOT_FIXED and not (isinstance(f.get("reason"), str) and f["reason"].strip()):
        errors.append("%s: status NOT_FIXED requires its reason" % where)
    if status == SETTLED and not (isinstance(f.get("settled_by"), str) and SETTLED_BY.match(f["settled_by"])):
        errors.append("%s: status SETTLED requires settled_by ('journal <YYYY-MM-DD>' or 'user')" % where)
    if "settled_by" in f and status != SETTLED:
        errors.append("%s: settled_by goes with status SETTLED" % where)


def validate(f: Any) -> Tuple[List[str], List[str]]:
    """FD-01 to FD-04: (errors, warnings) of one finding."""
    errors: List[str] = []
    warnings: List[str] = []
    if not isinstance(f, dict):
        return ["the finding must be a JSON object"], []
    where = "finding %s" % (f.get("id") if isinstance(f.get("id"), str) else "?")
    for key in REQUIRED:
        if not (isinstance(f.get(key), str) and f[key].strip()):
            errors.append("%s: required field %s missing or empty" % (where, key))
    unknown = set(f) - set(REQUIRED) - set(OPTIONAL)
    if unknown:
        errors.append("%s: unknown field(s): %s" % (where, ", ".join(sorted(unknown))))
    if f.get("source") not in journal.SKILLS:
        errors.append("%s: source must be a skill (%s)" % (where, ", ".join(journal.SKILLS)))
    if f.get("severity") not in SEVERITIES:
        errors.append("%s: severity must be one of %s" % (where, "|".join(SEVERITIES)))
    _check_status(f, where, errors)
    if "evidence" in f:
        _check_evidence(f["evidence"], where, errors, warnings)
    _check_occurrences(f, where, errors, warnings)
    for key in TEXT_FIELDS:
        if key in f and f[key] is not None and not isinstance(f[key], str):
            errors.append("%s: %s must be a string" % (where, key))
    checks = (("size", SIZES), ("nature", NATURES), ("regression_risk", RISKS))
    for key, allowed in checks:
        if f.get(key) is not None and f[key] not in allowed:
            errors.append("%s: %s must be one of %s" % (where, key, "|".join(allowed)))
    milestone = f.get("milestone")
    if milestone is not None and (isinstance(milestone, bool) or not isinstance(milestone, (str, int))):
        errors.append("%s: milestone must be a string or an integer" % where)
    deps = f.get("depends_on", [])
    if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
        errors.append("%s: depends_on must be a list of ids" % where)
    if f.get("deliverable_status") is not None and not DELIVERABLE.match(str(f["deliverable_status"])):
        errors.append("%s: deliverable_status must be A_FAIRE, EN_COURS, FAIT, REPORTE or "
                      "TRANCHEE (journal <YYYY-MM-DD>)" % where)
    return errors, warnings


def load_findings(text: str) -> List[Any]:
    """A finding, a list of findings, or {"findings": [...]}."""
    data = json.loads(text)
    if isinstance(data, dict) and isinstance(data.get("findings"), list) and "id" not in data:
        return data["findings"]
    return data if isinstance(data, list) else [data]


def validate_text(text: str) -> Dict[str, Any]:
    """FD-03: report of a whole file."""
    try:
        items = load_findings(text)
    except ValueError as exc:
        return {"valid": False, "count": 0, "errors": ["not valid JSON: %s" % exc], "warnings": []}
    errors: List[str] = []
    warnings: List[str] = []
    for f in items:
        e, w = validate(f)
        errors += e
        warnings += w
    ids = [f.get("id") for f in items if isinstance(f, dict)]
    twice = sorted({i for i in ids if isinstance(i, str) and ids.count(i) > 1})
    if twice:
        errors.append("id given twice: %s" % ", ".join(twice))
    return {"valid": not errors, "count": len(items), "errors": errors, "warnings": warnings}


def transition(f: Dict[str, Any], status: str, **fields: Any) -> Dict[str, Any]:
    """FD-02: new state of a finding (history completed); a forbidden
    transition is refused."""
    if status not in STATUSES:
        raise FindingError("unknown status: %r" % (status,))
    current = f["status"]
    if status not in TRANSITIONS[current]:
        raise FindingError("finding %s: transition %s -> %s not allowed" % (f.get("id"), current, status))
    new = dict(f, status=status, history=list(f.get("history", [])) + [current])
    new.update({k: v for k, v in fields.items() if v is not None})
    return new


# --------------------------------------------------------- adapters (FD-05)

def _str(obj: Dict[str, Any], key: str, where: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise FindingError("%s: field '%s' missing or empty" % (where, key))
    return value


def _line_of(value: Any) -> Optional[int]:
    return value if _is_int(value, 1) else None


def _code_or_structural(file: Any, line: Any, excerpt: Optional[str] = None,
                        journal_date: Any = None) -> Dict[str, Any]:
    ev: Dict[str, Any]
    if isinstance(file, str) and file.strip():
        ev = {"kind": "code", "file": file}
        line = _line_of(line)
        if line is not None:
            ev["lines"] = {"start": line, "end": line}
    elif journal_date:
        ev = {"kind": "journal", "file": None}
    else:
        ev = {"kind": "structural", "file": None}
    if excerpt is not None:
        ev["excerpt"] = excerpt
    if journal_date:
        ev["journal_date"] = journal_date
    return ev


def _family(fid: str, table: Dict[str, str]) -> Optional[str]:
    prefix = fid.split("-", 1)[0]
    return table.get(prefix)


def _base(fid: Any, source: str, severity: Any, axis: Any, title: Any, raw: Any) -> Dict[str, Any]:
    return {"id": fid, "source": source, "severity": severity, "axis": axis, "title": title,
            "status": PROPOSED, "raw": copy.deepcopy(raw)}


def adapt(fmt: str, obj: Dict[str, Any], fid: Optional[str] = None, axis: Optional[str] = None,
          source: Optional[str] = None) -> Dict[str, Any]:
    """FD-05: one agent finding (or issue) -> FD-01 finding, status PROPOSED,
    the input kept as is in "raw". fid: id when the format has none (reviewer
    finding, Verifier issue); axis: theme of a ship-pr reviewer."""
    if fmt not in FORMATS:
        raise FindingError("unknown format: %r (expected one of %s)" % (fmt, ", ".join(FORMATS)))
    if not isinstance(obj, dict):
        raise FindingError("%s: the finding must be an object" % fmt)
    source = source or FORMAT_SOURCE[fmt]
    fid = fid or obj.get("id")
    if not (isinstance(fid, str) and fid.strip()):
        raise FindingError("%s: an id is required (--id)" % fmt)
    where = "%s %s" % (fmt, fid)
    if fmt == "shippr-reviewer":
        f = _base(fid, source, obj.get("severity"), axis or obj.get("theme") or "review",
                  _str(obj, "title", where), obj)
        f["evidence"] = _code_or_structural(obj.get("file"), obj.get("line"))
        f["impact"] = str(obj.get("description", ""))
        f["remediation"] = str(obj.get("fix", ""))
    elif fmt == "legacy-auditor":
        loc = obj.get("location") or {}
        f = _base(fid, source, obj.get("severity"), axis or _family(fid, LEGACY_FAMILIES) or "",
                  _str(obj, "title", where), obj)
        f["evidence"] = _code_or_structural(loc.get("file"), loc.get("line"), obj.get("evidence"))
        for key in ("category", "impact", "remediation", "size"):
            if obj.get(key) is not None:
                f[key] = obj[key]
        f["depends_on"] = list(obj.get("depends_on") or [])
    elif fmt == "lab-audit-auditor":
        loc = obj.get("location") or {}
        f = _base(fid, source, obj.get("severity"), axis or obj.get("lens"), _str(obj, "title", where), obj)
        f["evidence"] = _code_or_structural(loc.get("file"), loc.get("line"), obj.get("evidence"),
                                            loc.get("journal_date"))
        for key in ("nature", "impact", "size"):
            if obj.get(key) is not None:
                f[key] = obj[key]
        if obj.get("proposal") is not None:
            f["remediation"] = obj["proposal"]
        f["depends_on"] = list(obj.get("depends_on") or [])
    elif fmt == "vibecode-auditor":
        f = _base(fid, source, obj.get("severity"), axis or obj.get("axis"), _str(obj, "pattern", where), obj)
        occ = obj.get("occurrences")
        if not isinstance(occ, list):
            raise FindingError("%s: occurrences must be a list" % where)
        listed = [{"file": o.get("file"), "line": _line_of(o.get("line")),
                   "excerpt": o.get("evidence")} for o in occ if isinstance(o, dict)]
        # VA-04: 5 representative occurrences at most, `count` the real total
        # (never below what was listed), `sampled` as soon as some are left out.
        f["occurrences"] = listed[:MAX_LISTED_OCCURRENCES]
        count = obj.get("count", len(occ))
        f["count"] = max(count, len(listed)) if isinstance(count, int) and not isinstance(count, bool) else count
        f["sampled"] = bool(obj.get("sampled", False)) or (
            isinstance(f["count"], int) and f["count"] > len(f["occurrences"]))
        first = f["occurrences"][0] if f["occurrences"] else {}
        f["evidence"] = _code_or_structural(first.get("file"), first.get("line"), first.get("excerpt"))
        family = _family(fid, VIB_FAMILIES)
        if family:
            f["category"] = family
        for key in ("impact", "remediation", "regression_risk", "risk_rationale"):
            if obj.get(key) is not None:
                f[key] = obj[key]
        if obj.get("difficulty") is not None:
            f["size"] = obj["difficulty"]
        f["depends_on"] = list(obj.get("depends_on") or [])
    else:
        # verifier issue: {file, severity, [kind,] description}.
        # X7: partial conversion, the Verifier validates an implementation and
        # gives neither a line nor an excerpt.
        description = _str(obj, "description", where)
        if _VERIFIERS.get(fmt, {}).get("kinds") and obj.get("kind") not in LAB_VERIFIER_KINDS:
            raise FindingError("%s: kind must be one of %s" % (where, "|".join(LAB_VERIFIER_KINDS)))
        default_axis = obj.get("kind") if _VERIFIERS.get(fmt, {}).get("kinds") else "verification"
        title = description.strip().splitlines()[0][:120]
        f = _base(fid, source, obj.get("severity"), axis or default_axis, title, obj)
        f["evidence"] = _code_or_structural(obj.get("file"), None)
        f["impact"] = description
    errors, _ = validate(f)
    if errors:
        raise FindingError("%s: conversion refused: %s" % (where, "; ".join(errors)))
    return f


# --------------------------------------------------- validator verdict (FD-06)

VERDICT_FIELDS: Dict[str, Tuple[str, ...]] = {
    "ship-pr": (),
    "legacy-audit": ("size_correction",),
    "ai-lab-audit": ("nature_correction", "size_correction"),
    "vibecode-audit": ("severity_correction", "axis_correction", "difficulty_correction", "corrected_count",
                       "false_occurrences", "split_into"),
}
_PLACE = re.compile(r"^(?P<file>.+):(?P<line>[1-9][0-9]*)$")


def _choice(obj: Dict[str, Any], key: str, allowed: Iterable[str]) -> Optional[str]:
    value = obj.get(key)
    if value is None or value == "null":
        return None
    if value not in tuple(allowed):
        raise verdicts.UnusableOutput("%s must be one of %s or null" % (key, "|".join(allowed)))
    return value


def _places(value: Any, key: str) -> List[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) and _PLACE.match(v) for v in value):
        raise verdicts.UnusableOutput("%s must be a list of 'path:line'" % key)
    return list(value)


def parse_verdict(text: Optional[str], source: str) -> Dict[str, Any]:
    """FD-06: validator verdict of `source`. Raises verdicts.UnusableOutput
    (dead agent) on an output without a usable final block or with a verdict
    the source does not have (SPLIT outside vibecode-audit). Correction fields
    foreign to the source are ignored."""
    if source not in VERDICT_FIELDS:
        raise FindingError("no validator format for source %r (expected one of %s)"
                           % (source, ", ".join(VERDICT_FIELDS)))
    obj = verdicts.extract_terminal_json(text)
    allowed = VERDICT_FIELDS[source]
    accepted = (CONFIRMED, REJECTED, "SPLIT") if source == "vibecode-audit" else (CONFIRMED, REJECTED)
    verdict = obj.get("verdict")
    if verdict not in accepted:
        raise verdicts.UnusableOutput("invalid validator verdict for %s: %r" % (source, verdict))
    reason = obj.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise verdicts.UnusableOutput("validator: field 'reason' missing or empty")
    out: Dict[str, Any] = {"verdict": verdict, "reason": reason}
    if "size_correction" in allowed:
        out["size_correction"] = _choice(obj, "size_correction", SIZES)
    if "nature_correction" in allowed:
        out["nature_correction"] = _choice(obj, "nature_correction", NATURES)
    if source == "vibecode-audit":
        out["severity_correction"] = _choice(obj, "severity_correction", SEVERITIES)
        out["axis_correction"] = _choice(obj, "axis_correction", VIB_AXES)
        out["difficulty_correction"] = _choice(obj, "difficulty_correction", SIZES)
        count = obj.get("corrected_count")
        if count is not None and not _is_int(count, 0):
            raise verdicts.UnusableOutput("corrected_count must be an integer >= 0 or null")
        # 0 is the literal of the prompt template: a verified count is >= 1.
        out["corrected_count"] = count or None
        out["false_occurrences"] = _places(obj.get("false_occurrences", []), "false_occurrences")
        parts = obj.get("split_into", [])
        if verdict == "SPLIT":
            if not isinstance(parts, list) or len(parts) < 2:
                raise verdicts.UnusableOutput("SPLIT requires split_into with at least 2 parts")
            clean = []
            for i, p in enumerate(parts):
                if not isinstance(p, dict) or not isinstance(p.get("pattern"), str) or not p["pattern"].strip():
                    raise verdicts.UnusableOutput("split_into %d: pattern missing" % i)
                if p.get("severity") not in SEVERITIES or p.get("difficulty") not in SIZES:
                    raise verdicts.UnusableOutput("split_into %d: invalid severity or difficulty" % i)
                clean.append({"pattern": p["pattern"], "occurrences": _places(p.get("occurrences", []),
                                                                               "split_into occurrences"),
                              "severity": p["severity"], "difficulty": p["difficulty"]})
            out["split_into"] = clean
        elif parts:
            raise verdicts.UnusableOutput("split_into goes with the SPLIT verdict only")
    return out


# --------------------------------------------------- verdict application (FD-07)

def _place_of(o: Dict[str, Any]) -> str:
    return "%s:%s" % (o.get("file"), o.get("line"))


def apply_verdict(f: Dict[str, Any], v: Dict[str, Any]) -> List[Dict[str, Any]]:
    """FD-07: findings resulting from a parsed verdict (one, or the parts of a split)."""
    if v["verdict"] == REJECTED:
        return [transition(f, REJECTED, reason=v["reason"])]
    if v["verdict"] == "SPLIT":
        known = {_place_of(o): o for o in f.get("occurrences", [])}
        parts = []
        for n, part in enumerate(v["split_into"], 1):
            occ = []
            for place in part["occurrences"]:
                match = _PLACE.match(place)
                occ.append(known.get(place) or {"file": match.group("file"), "line": int(match.group("line")),
                                                "excerpt": None})
            new = dict(f, id="%s.%d" % (f["id"], n), title=part["pattern"], severity=part["severity"],
                       size=part["difficulty"], occurrences=occ, count=len(occ), sampled=False,
                       split_from=f["id"])
            parts.append(transition(new, CONFIRMED, reason=v["reason"]))
        return parts
    new = transition(f, CONFIRMED, reason=v["reason"])
    for key, field in (("severity_correction", "severity"), ("axis_correction", "axis"),
                       ("size_correction", "size"), ("difficulty_correction", "size"),
                       ("nature_correction", "nature")):
        if v.get(key) is not None:
            new[field] = v[key]
    false = set(v.get("false_occurrences") or [])
    if "occurrences" in new:
        kept = [o for o in new["occurrences"] if _place_of(o) not in false]
        count = v.get("corrected_count")
        if count is None:
            # Every false occurrence counts, in the sample or not (count is the real total).
            count = new.get("count", len(new["occurrences"])) - len(false)
        new["occurrences"] = kept
        new["count"] = max(count, len(kept))
        new["sampled"] = new["count"] > len(kept)
    return [new]


# ------------------------------------------------------------ conversions

def check_forbidden(text: str) -> List[Dict[str, Any]]:
    """FD-11: forbidden strings of a rendered deliverable (line, text)."""
    hits = []
    for number, line in enumerate(text.splitlines(), 1):
        for match in FORBIDDEN.finditer(line):
            hits.append({"line": number, "text": match.group(0)})
    return hits


def to_pr_comment(f: Dict[str, Any]) -> str:
    """FD-10: body of the PR comment of a finding (identical to BASE1)."""
    return "**[%s] %s**\n\n%s\n\nSuggested fix: %s" % (
        f["severity"], f["title"], f.get("impact", ""), f.get("remediation", ""))


def to_note(f: Dict[str, Any]) -> Dict[str, Any]:
    """FD-09: rgkb note of a finding (arguments of 'rgkb.py note-add')."""
    ev = f.get("evidence") or {}
    file = ev.get("file")
    lines = ev.get("lines")
    where = ""
    if file and lines:
        where = " (%s:%d)" % (file, lines["start"])
    text = "%s [%s, %s]%s" % (f["title"], f["severity"], f["status"], where)
    for label, key in (("Impact", "impact"), ("Remediation", "remediation"), ("Reason", "reason")):
        if f.get(key):
            text += "\n%s: %s" % (label, f[key])
    tags = [f["source"], f["axis"], f["severity"]]
    return {"subject": file or f["axis"], "text": text, "tags": tags, "kind": "file" if file else "topic",
            "source": "%s:%s" % (f["source"], f["id"])}


def _criteria(items: Iterable[str]) -> str:
    return "\n".join("%d. %s" % (n, c.strip()) for n, c in enumerate(items, 1))


def to_spec(f: Dict[str, Any], sections: Dict[str, Any], prefix: str, number: int) -> str:
    """FD-08: factory spec of a finding. `sections` (written by the skill):
    context, scope, criteria (list), out_of_scope, risks, questions
    (default: the no_questions text of templates/deliverables.json), title (default: the finding title), secrets (bool,
    default: category or title mentions a secret)."""
    if not re.match(r"^[a-z][a-z0-9]*$", prefix):
        raise FindingError("invalid spec prefix: %r" % (prefix,))
    for key in ("context", "scope", "out_of_scope", "risks"):
        if not isinstance(sections.get(key), str) or not sections[key].strip():
            raise FindingError("spec section %s missing or empty" % key)
    criteria = sections.get("criteria")
    if not isinstance(criteria, list) or not criteria or not all(isinstance(c, str) and c.strip() for c in criteria):
        raise FindingError("spec section criteria must be a non-empty list of texts")
    criteria = list(criteria)
    texts = deliverable_texts()
    if f.get("axis") == "fuites":
        criteria += [c for c in texts["leak_criteria"] if c not in criteria]
    out_of_scope = sections["out_of_scope"].strip()
    secrets = sections.get("secrets")
    if secrets is None:
        secrets = "secret" in ("%s %s" % (f.get("category") or "", f.get("title") or "")).lower()
    clause = texts["secrets_out_of_scope"]
    if secrets and clause.lower() not in out_of_scope.lower():
        out_of_scope = "%s ; %s" % (clause, out_of_scope)
    # BQ-11: `Depends on` (spec names) and `Priority` (rank in the PZ order) follow `Blocking questions`.
    extra = ""
    depends = sections.get("depends_on")
    if depends:
        if not isinstance(depends, list) or not all(isinstance(d, str) and d.strip() for d in depends):
            raise FindingError("spec section depends_on must be a list of spec names")
        extra += "\nDepends on: " + ", ".join(d.strip() for d in depends)
    priority = sections.get("priority")
    if priority is not None:
        if not isinstance(priority, int) or isinstance(priority, bool) or priority < 1:
            raise FindingError("spec section priority must be an integer >= 1")
        extra += "\nPriority: %d" % priority
    text = Template(SPEC_TEMPLATE.read_text(encoding="utf-8")).substitute(
        extra_header=extra,
        prefix=prefix, number="%02d" % number, title=(sections.get("title") or f["title"]).strip(),
        finding_id=f["id"], severity=f["severity"], size=f.get("size") or "?",
        context=sections["context"].strip(), scope=sections["scope"].strip(), criteria=_criteria(criteria),
        out_of_scope=out_of_scope, risks=sections["risks"].strip(),
        questions=(sections.get("questions") or texts["no_questions"]).strip())
    hits = check_forbidden(text)
    if hits:
        raise FindingError("forbidden string in the rendered spec, line %d: %s" % (hits[0]["line"], hits[0]["text"]))
    return text
