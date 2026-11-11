# Deterministic rules of the brainstorm skill (BS-01 to BS-12).
#
# brainstorm writes nothing before its final gate (X5): these functions are
# pure (the session keeps the state and passes it in), and the journal is
# written together with the synthesis, at the final gate.
#
# - BS-01: SUBJECT = every token that does not start with "--", word for word.
# - BS-02: --rounds: min(N, 3), default 3; not an integer or < 1 -> error.
# - BS-03: registry of discarded angles, exact key; rewording stays with the
#   skill's judgment.
# - BS-04: lens schema, 4 angles at most, origine in enonce | hors-enonce.
# - BS-05: dead lens -> one relaunch -> named, and the early exit is
#   DISABLED for that round.
# - BS-06: early exit when no "hors-enonce" angle survives.
# - BS-07: round ceiling -> forced convergence.
# - BS-08: hard cap of 5 retained angles; the surplus goes to "not retained"
#   with its rank.
# - BS-09: slug: lowercase, accents removed, non-alphanumeric -> "-", dashes
#   merged, <= 40 characters, no trailing dash; path BRAINSTORM-<slug>.md.
# - BS-10: resume: the "Discarded angles" section is loaded; a rewrite keeps all
#   its lines.
# - BS-11: the synthesis is rendered from templates/brainstorm.json.
# - BS-12: the dialogue gates subject, round and write are in hcore/gates.py.

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from string import Template
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import audit, findings, gates, journal, runs, verdicts

MAX_ROUNDS = 3
MAX_RETAINED = 5
MAX_ANGLES_PER_LENS = 4
SLUG_MAX = 40
LENSES = ("hypotheses", "alternatives", "contre-exemples", "cout", "second-ordre")
ORIGINS = ("enonce", "hors-enonce")
DISCARDED_HEADING = "## Discarded angles"
LEGACY_DISCARDED_HEADING = "## Angles écartés"     # syntheses written before the English templates
TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "brainstorm.json"


class BsError(ValueError):
    """Invalid argument or input (exit 2)."""


def texts() -> Dict[str, Any]:
    return json.loads(TEMPLATE.read_text(encoding="utf-8"))


# ------------------------------------------------- BS-01, BS-02, BS-09

def slug(subject: str) -> str:
    """BS-09."""
    plain = unicodedata.normalize("NFKD", subject.lower())
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    plain = re.sub(r"[^a-z0-9]", "-", plain)
    plain = re.sub(r"-+", "-", plain).strip("-")
    return plain[:SLUG_MAX].rstrip("-")


def synthesis_path(subject: str) -> str:
    return "BRAINSTORM-%s.md" % slug(subject)


def parse_arguments(arguments: str) -> Dict[str, Any]:
    """BS-01, BS-02 and BS-09 on $ARGUMENTS."""
    tokens = (arguments or "").split()
    subject: List[str] = []
    rounds: Optional[str] = None
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--rounds":
            if i + 1 >= len(tokens):
                raise BsError("--rounds expects a value")
            rounds = tokens[i + 1]
            i += 2
        elif token.startswith("--rounds="):
            rounds = token[len("--rounds="):]
            i += 1
        elif token.startswith("--"):
            raise BsError("unknown option %s (expected --rounds N)" % token)
        else:
            subject.append(token)
            i += 1
    try:
        count = audit.parse_cap("--rounds", rounds, MAX_ROUNDS, MAX_ROUNDS)
    except audit.AuditUsage as exc:
        raise BsError(str(exc))
    text = " ".join(subject)
    return {"subject": text, "rounds": count, "slug": slug(text) if text else "",
            "path": synthesis_path(text) if text else None}


# ------------------------------------------------- BS-03 registry

def key(title: str) -> str:
    return " ".join(str(title).split())


def filter_registry(pistes: Sequence[Dict[str, Any]], discarded: Sequence[Dict[str, Any]]
                    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """BS-03: exact key; the angles already discarded are removed (and listed)."""
    banned = {key(d.get("titre") or d.get("title") or "") for d in discarded}
    kept: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []
    for p in pistes:
        (removed if key(p["titre"]) in banned else kept).append(p)
    return kept, removed


# ------------------------------------------------- BS-04 lens schema

def parse_lens(lens: str, block: Any) -> Dict[str, Any]:
    """BS-04: a lens block {"pistes": [...], "objection_principale": "..."}.
    A block that does not conform is an unusable output (dead lens)."""
    if lens not in LENSES:
        raise BsError("unknown lens: %r" % (lens,))
    if isinstance(block, str):
        try:
            block = verdicts.extract_terminal_json(block)
        except verdicts.UnusableOutput as exc:
            raise verdicts.UnusableOutput("%s: %s" % (lens, exc))
    if not isinstance(block, dict) or not isinstance(block.get("pistes"), list):
        raise verdicts.UnusableOutput("%s: a 'pistes' list is required" % lens)
    if len(block["pistes"]) > MAX_ANGLES_PER_LENS:
        raise verdicts.UnusableOutput("%s: %d angles, %d at most" % (lens, len(block["pistes"]), MAX_ANGLES_PER_LENS))
    clean = []
    for i, p in enumerate(block["pistes"]):
        where = "%s angle %d" % (lens, i)
        if not isinstance(p, dict):
            raise verdicts.UnusableOutput(where + " is not an object")
        if p.get("origine") not in ORIGINS:
            raise verdicts.UnusableOutput(where + ": origine must be enonce or hors-enonce")
        for field in ("id", "titre", "these"):
            if not isinstance(p.get(field), str) or not p[field].strip():
                raise verdicts.UnusableOutput("%s: field '%s' missing or empty" % (where, field))
        clean.append(dict(p, lens=lens))
    objection = block.get("objection_principale")
    if not isinstance(objection, str) or not objection.strip():
        raise verdicts.UnusableOutput("%s: objection_principale missing (every round contests the subject)" % lens)
    return {"pistes": clean, "objection_principale": objection}


# ------------------------------------------------- BS-05, BS-06, BS-07 round

def round_step(round_no: int, rounds: int, blocks: Dict[str, Any], discarded: Sequence[Dict[str, Any]],
               relaunched: Sequence[str] = ()) -> Dict[str, Any]:
    """One round after the lens outputs: relaunch decision, then the synthesis
    inputs. `blocks`: lens -> block (dict or text) or None for a dead lens."""
    for lens in blocks:
        if lens not in LENSES:
            raise BsError("unknown lens: %r" % (lens,))
    valid: Dict[str, Dict[str, Any]] = {}
    bad: List[str] = []
    for lens in LENSES:
        if lens not in blocks:
            raise BsError("lens %r is missing from the round: the five lenses always run" % lens)
        try:
            if blocks[lens] is None:
                raise verdicts.UnusableOutput("dead lens")
            valid[lens] = parse_lens(lens, blocks[lens])
        except verdicts.UnusableOutput:
            bad.append(lens)
    again = [lens for lens in bad if lens not in relaunched]
    if again:
        return {"action": "relaunch", "lenses": again}
    survivors, removed = filter_registry([p for lens in LENSES if lens in valid for p in valid[lens]["pistes"]],
                                         discarded)
    outside = [p for p in survivors if p["origine"] == "hors-enonce"]
    early = not outside and not bad          # BS-05: a missing lens disables the early exit
    forced = round_no >= rounds              # BS-07
    return {"action": "synthesize", "round": round_no, "pistes": survivors, "removed": removed,
            "objections": [valid[lens]["objection_principale"] for lens in LENSES if lens in valid],
            "missing_lenses": bad, "early_exit": early, "early_exit_disabled": bool(bad) and not outside,
            "converge": early or forced, "ceiling": forced and not early}


# ------------------------------------------------- BS-08 cap

def cap_retained(ranked: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """BS-08: the first five of the ranked list are retained; the rest keeps its rank."""
    retained = list(ranked[:MAX_RETAINED])
    surplus = [dict(p, rank=MAX_RETAINED + i + 1) for i, p in enumerate(ranked[MAX_RETAINED:])]
    return {"retained": retained, "not_retained": surplus}


# ------------------------------------------------- BS-10 resume

def discarded_rows(text: str) -> List[str]:
    """BS-10: the table lines (header and separator excluded) of the section
    'Discarded angles' (or the legacy 'Angles écartés') of an earlier synthesis, verbatim."""
    lines = text.splitlines()
    rows: List[str] = []
    inside = False
    for line in lines:
        if line.strip() in (DISCARDED_HEADING, LEGACY_DISCARDED_HEADING):
            inside = True
            continue
        if inside and line.startswith("## "):
            break
        if inside and line.lstrip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if cells and cells[0] != "Angle" and not set("".join(cells)) <= set("-: "):
                rows.append(line.rstrip())
    return rows


def resume(root: Path, path: str) -> Dict[str, Any]:
    full = Path(root) / path
    if not full.is_file():
        return {"exists": False, "path": path, "discarded": [], "rows": []}
    rows = discarded_rows(full.read_text(encoding="utf-8", errors="replace"))
    discarded = []
    for row in rows:
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        discarded.append({"titre": cells[0], "marking": cells[1] if len(cells) > 1 else "",
                          "reason": cells[3] if len(cells) > 3 else "", "by": cells[4] if len(cells) > 4 else ""})
    return {"exists": True, "path": path, "discarded": discarded, "rows": rows}


# ------------------------------------------------- BS-11 render

def render(data: Dict[str, Any], previous_rows: Sequence[str] = ()) -> str:
    """BS-11: the synthesis; BS-10: the earlier discarded rows are all kept."""
    t = texts()
    ends = t["ends"]
    if data.get("end") not in ends:
        raise BsError("end must be one of: %s" % ", ".join(ends))
    capped = cap_retained(data.get("ranked") or [])
    mark = lambda m: t["markings"].get(m, m)  # noqa: E731
    out: List[str] = [Template(t["title"]).substitute(subject=data["subject"]), "",
                      t["idea_heading"], data.get("idea") or t["none"], "", t["objections_heading"]]
    out += [Template(t["objection_line"]).substitute(text=o) for o in data.get("objections") or [t["none"]]]
    out += ["", t["retained_heading"]]
    out += [Template(t["retained_line"]).substitute(n=i + 1, title=p["title"], marking=mark(p["marking"]),
                                                    body=p["body"])
            for i, p in enumerate(capped["retained"])] or [t["none"]]
    out += ["", t["discarded_heading"]] + t["discarded_table"]
    new_rows = [Template(t["discarded_row"]).substitute(title=d["title"], marking=mark(d["marking"]),
                                                        round=d["round"], reason=d["reason"], by=d["by"])
                for d in data.get("discarded") or []]
    seen = set()
    for row in list(previous_rows) + new_rows:
        if row not in seen:
            seen.add(row)
            out.append(row)
    if capped["not_retained"]:
        out += ["", t["not_retained_heading"]] + t["not_retained_table"]
        out += [Template(t["not_retained_row"]).substitute(title=p["title"], rank=p["rank"],
                                                           reason=t["not_retained_reason"])
                for p in capped["not_retained"]]
    out += ["", t["end_heading"],
            Template(t["end_line"]).substitute(played=data["played"], rounds=data["rounds"], end=ends[data["end"]])]
    if data.get("missing_lenses"):
        out.append(Template(t["missing_line"]).substitute(lenses=", ".join(data["missing_lenses"])))
    return "\n".join(out) + "\n"


# ------------------------------------------------- X5: journal and write at the final gate

WRITE_GATE = "brainstorm.write"
WRITE_ANSWER = "Write the synthesis"
NO_WRITE_ANSWER = "Write nothing"


def finish(root: Path, data: Dict[str, Any]) -> Dict[str, Any]:
    """Final gate (BS-12, X5): nothing was written before this call. Writes
    the journal (the gates asked, in order, then brainstorm.write) and, when
    the user chose to write, the synthesis at BRAINSTORM-<slug>.md (FD-11
    checked, the earlier 'Angles écartés' lines kept: BS-10)."""
    root = Path(root)
    path = synthesis_path(data["subject"])
    answer = gates.canonical(str(data.get("write_answer") or "").strip())
    if answer not in (WRITE_ANSWER, NO_WRITE_ANSWER):
        raise BsError("write_answer must be %r or %r" % (WRITE_ANSWER, NO_WRITE_ANSWER))
    text = None
    if answer == WRITE_ANSWER:
        text = render(data, resume(root, path)["rows"])
        hits = findings.check_forbidden(text)
        if hits:
            raise BsError("forbidden string in the synthesis, line %d: %s" % (hits[0]["line"], hits[0]["text"]))
    policy = gates.read_policy(root)
    run = runs.create(root, "brainstorm", {"gate_only": True})
    for item in list(data.get("gates") or []) + [{"id": WRITE_GATE, "answer": answer}]:
        decided = gates.decide(item["id"], policy)
        journal.append(run, decided)
        journal.append(run, dict(decided, phase="answer", answer=str(item["answer"]), source="user"))
    if text is not None:
        with open(str(root / path), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    return {"run": runs.display(run), "written": text is not None, "path": path if text is not None else None}
