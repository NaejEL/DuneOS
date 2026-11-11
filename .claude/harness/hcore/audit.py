# Shared audit engine of legacy-audit, ai-lab-audit and vibecode-audit: the
# functions common to the three audits (no profile, no /audit: Phase 2).
#
# - AU-01: the first token that does not start with "--" is REPO, resolved to
#   an absolute path; default: the current directory. A missing path is an
#   explicit stop (exit 1).
# - AU-02: lists separated by ";" (--categories, --lenses, --axes), default =
#   all. An unknown, empty or repeated value -> exit 2 (Q-J).
# - AU-03: caps min(N, cap) with a default. N not an integer or < 1 -> exit 2.
# - AU-04: dead lens or family (dead agent or unusable output) -> one
#   relaunch, then the skill's own outcome: "not audited" with a failed raw
#   count (legacy-audit, ai-lab-audit), or an "unauditable surface" of
#   severity major (vibecode-audit).
# - AU-05: validator dead twice -> UNVALIDATED, never CONFIRMED.
# - AU-06: deduplication before any validator, against the run registry, the
#   resumed deliverable and the project registry (RG-04), with each skill's
#   own key (DEDUP). vibecode-audit: a duplicate between two auditors of the
#   same run is not dismissed but reported for a merge (VA-07, lot L12).
# - AU-07: zero CONFIRMED -> straight to the report phase (vibecode-audit:
#   to the deliverable, which keeps the unauditable surfaces).
# - AU-08: renumbering per prefix after the TS-03 ordering; on a resume, the
#   numbering goes on from the highest number of the deliverable.
# - AU-09: resume: the tables (and sections) of ROADMAP-legacy.md,
#   AUDIT-lab.md and AUDIT-vibecode.md are read back into the run registry;
#   their lines are kept as they are in the new deliverable, statuses FAIT,
#   EN_COURS and TRANCHEE included.
# - AU-10: deliverable status sets of each skill (TRANCHEE (journal <date>)
#   for ai-lab-audit only).
# - AU-11: FD-11 (forbidden strings) applied to every written file; a write
#   outside the allowed paths is refused (X5: the deliverable, the specs of
#   the skill, .harness/runs/ and the project registry).
# - AU-12: GIT_ABSENT -> no git command afterwards, no git exclusion.
# - AU-13: report counters per lens or family: raw, confirmed, rejected or
#   deduplicated, and per skill split, confirmed occurrences, corrections and
#   questions.
# - AU-14: an existing spec is never overwritten (SF-04), whatever its slug.
# - AU-15: "Réajuster" once.
# - AU-16: gate call bounds: 4 themes at most per call, a maximal number of
#   calls per skill.
#
# The deliverable texts are data (templates/audits.json), not code.

from __future__ import annotations

import copy
import json
from datetime import datetime
import re
import shlex
from pathlib import Path
from string import Template
from typing import Any, Callable, Dict, Iterable, List, NamedTuple, Optional, Sequence, Tuple

from . import findings, fusion, gates, models, profiles, registry, relaunch, tshirt, verdicts

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
AUDIT_TEXTS = TEMPLATES / "audits.json"
LEGACY_AUDIT_TEXTS = TEMPLATES / "audits-legacy-fr.json"

NOT_AUDITED = "not-audited"
SURFACE = "surface"
MAX_THEMES_PER_CALL = 4
# Position of the readjustment option in the options of the three final
# gates (write / readjust / abandon): its label is read from the GT-08 catalog.
READJUST_INDEX = 1
SETTLED_STATUS = re.compile(r"^TRANCHEE \(journal (\d{4}-\d{2}-\d{2})\)$")
ID_RE = r"[A-Z]+-\d+(?:\.\d+)*"


class AuditUsage(ValueError):
    """Invalid argument, bound or call (exit 2)."""


class AuditStop(RuntimeError):
    """Explicit stop (exit 1)."""


class Auditor(NamedTuple):
    name: str      # lens or family of the auditor
    unit: str      # value of the units option it belongs to
    prefix: str    # id prefix of its findings


class Profile(NamedTuple):
    skill: str
    units_option: str
    units: Tuple[str, ...]
    auditors: Tuple[Auditor, ...]
    cap_option: str
    cap_max: int
    cap_default: int
    dead: str
    deliverable: str
    spec_prefix: Optional[str]
    statuses: Tuple[str, ...]
    settled: bool
    auditor_format: str
    dedup: str
    numbering: Tuple[Tuple[str, str], ...]   # (final prefix, TS-03 ordering)
    theme_gate: str
    max_calls: int
    final_gate: str
    counters: Tuple[str, ...]
    roles: Tuple[str, ...]
    aliases: Optional[str]
    auditor_roles: Dict[str, str]
    validator_role: str
    on_zero: str
    deliver_unvalidated: bool


BASE_STATUSES = ("A_FAIRE", "EN_COURS", "FAIT", "REPORTE")
_LAB_ROLES = ("lab.neuro", "lab.math", "lab.director.cadrage", "lab.validator")

PROFILES: Dict[str, Profile] = {p.skill: p for p in (
    Profile(
        skill="legacy-audit", units_option="--categories", units=(), auditors=(),
        cap_option="--max-specs", cap_max=25, cap_default=15, dead=NOT_AUDITED,
        deliverable="ROADMAP-legacy.md", spec_prefix="leg", statuses=BASE_STATUSES, settled=False,
        auditor_format="legacy-auditor", dedup="file-line-category", numbering=(("LEG", "LEG"),),
        theme_gate="legacy-audit.assignment", max_calls=3, final_gate="legacy-audit.final",
        counters=("raw", "confirmed", "rejected"), roles=models.AUDIT_ROLES, aliases=None,
        auditor_roles={}, validator_role="audit.validator", on_zero="report", deliver_unvalidated=False),
    Profile(
        skill="ai-lab-audit", units_option="--lenses", units=(), auditors=(),
        cap_option="--max-questions", cap_max=12, cap_default=6, dead=NOT_AUDITED,
        deliverable="AUDIT-lab.md", spec_prefix="lab", statuses=BASE_STATUSES, settled=True,
        auditor_format="lab-audit-auditor", dedup="place-lens", numbering=(("COR", "COR"), ("Q", "Q")),
        theme_gate="ai-lab-audit.selection", max_calls=2, final_gate="ai-lab-audit.final",
        counters=("raw", "confirmed", "rejected", "corrections", "questions"), roles=_LAB_ROLES, aliases="lab",
        auditor_roles={},
        validator_role="lab.validator", on_zero="report", deliver_unvalidated=False),
    Profile(
        skill="vibecode-audit", units_option="--axes", units=(), auditors=(),
        cap_option="--max-findings", cap_max=30, cap_default=15, dead=SURFACE,
        deliverable="AUDIT-vibecode.md", spec_prefix=None, statuses=BASE_STATUSES, settled=False,
        auditor_format="vibecode-auditor", dedup="pattern", numbering=(("VIB", "VIB"),),
        theme_gate="vibecode-audit.order", max_calls=1, final_gate="vibecode-audit.final",
        counters=("raw", "confirmed", "split", "rejected", "occurrences"), roles=models.AUDIT_ROLES,
        aliases=None, auditor_roles={}, validator_role="audit.validator", on_zero="deliverable",
        deliver_unvalidated=True),
)}


# PX-04, PF-05: units, auditors (lens, unit, prefix) and the model role of each lens come from the profiles.
_LENS_SET_OF = {"legacy-audit": "legacy", "vibecode-audit": "vibecode", "ai-lab-audit": "research"}


def _fill(p: Profile) -> Profile:
    prof, lens_set = profiles.find_set(_LENS_SET_OF[p.skill])
    units, lenses = profiles.lens_set_of(prof, lens_set["name"])
    return p._replace(
        units=tuple(units),
        auditors=tuple(Auditor(l["name"], l.get("axis") or l["name"], l["prefix"]) for l in lenses),
        auditor_roles={l["name"]: l["model_role"] for l in lenses if l["model_role"] != "audit.auditor"})


PROFILES = {name: _fill(p) for name, p in PROFILES.items()}


def lens_flag(skill: str, lens: str, flag: str) -> Any:
    prof, _ = profiles.find_set(_LENS_SET_OF[skill])
    return next((l.get(flag) for l in prof["lenses"] if l["name"] == lens), None)


def profile(skill: str) -> Profile:
    p = PROFILES.get(skill)
    if p is None:
        raise AuditUsage("unknown audit skill: %r (expected one of %s)" % (skill, ", ".join(PROFILES)))
    return p


def texts() -> Dict[str, Any]:
    """Texts of the deliverables (templates/audits.json)."""
    return json.loads(AUDIT_TEXTS.read_text(encoding="utf-8"))


def legacy_texts() -> Dict[str, Any]:
    """Texts of the deliverables written before the English templates (templates/audits-legacy-fr.json);
    only used to read back an existing French deliverable."""
    return json.loads(LEGACY_AUDIT_TEXTS.read_text(encoding="utf-8"))


# --------------------------------------------------------- arguments (AU-01 to AU-03)

def _tokens(text: str) -> List[str]:
    lexer = shlex.shlex(text, posix=False)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        raw = list(lexer)
    except ValueError as exc:
        raise AuditUsage("unbalanced quotes in the arguments: %s" % exc)
    return [t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'" else t for t in raw]


def parse_list(option: str, value: Optional[str], allowed: Sequence[str]) -> List[str]:
    """AU-02: 'a;b' -> [a, b]; default (None) = every value."""
    if value is None:
        return list(allowed)
    items = [v.strip() for v in value.split(";") if v.strip()]
    if not items:
        raise AuditUsage("%s is empty: give at least one of %s" % (option, ", ".join(allowed)))
    unknown = [v for v in items if v not in allowed]
    if unknown:
        raise AuditUsage("%s: unknown value(s) %s (expected among %s)"
                         % (option, ", ".join(repr(v) for v in unknown), ", ".join(allowed)))
    twice = sorted({v for v in items if items.count(v) > 1})
    if twice:
        raise AuditUsage("%s: value given twice: %s" % (option, ", ".join(twice)))
    return [v for v in allowed if v in items]


def parse_cap(option: str, value: Optional[str], cap: int, default: int) -> int:
    """AU-03: min(N, cap), default when absent; N not an integer or < 1 -> exit 2."""
    if value is None:
        return default
    try:
        n = int(str(value).strip())
    except ValueError:
        raise AuditUsage("%s expects an integer >= 1, got: %r" % (option, value))
    if n < 1:
        raise AuditUsage("%s expects an integer >= 1, got: %d" % (option, n))
    return min(n, cap)


def resolve_repo(token: Optional[str], cwd: Path) -> Path:
    """AU-01: REPO, absolute; a missing path is an explicit stop."""
    path = Path(token) if token else Path(cwd)
    if not path.is_absolute():
        path = Path(cwd) / path
    if not path.exists():
        raise AuditStop("the path %s does not exist" % (token if token else cwd))
    if not path.is_dir():
        raise AuditStop("the path %s is not a directory" % token)
    return path.resolve()


def parse_arguments(skill: str, arguments: str, cwd: Path) -> Dict[str, Any]:
    """AU-01 to AU-03 (and X6: --models taken out, for this audit only)."""
    p = profile(skill)
    try:
        rest, override = models.extract_option(arguments or "")
    except models.ModelsError as exc:
        raise AuditUsage(str(exc))
    tokens = _tokens(rest)
    repo_token = None
    if tokens and not tokens[0].startswith("--"):
        repo_token, tokens = tokens[0], tokens[1:]
    values: Dict[str, str] = {}
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in (p.units_option, p.cap_option):
            if i + 1 >= len(tokens) or tokens[i + 1].startswith("--"):
                raise AuditUsage("%s expects a value" % token)
            if token in values:
                raise AuditUsage("%s given twice" % token)
            values[token] = tokens[i + 1]
            i += 2
        elif token.startswith("--"):
            raise AuditUsage("unknown option %s (expected %s, %s or --models)"
                             % (token, p.units_option, p.cap_option))
        else:
            raise AuditUsage("unexpected argument %r: the repository path comes first" % token)
    units = parse_list(p.units_option, values.get(p.units_option), p.units)
    cap = parse_cap(p.cap_option, values.get(p.cap_option), p.cap_max, p.cap_default)
    repo = resolve_repo(repo_token, cwd)
    return {"repo": str(repo), "units": units, "auditors": [a.name for a in p.auditors if a.unit in units],
            "cap": cap, "models": override}


def auditor_role(p: Profile, auditor: str) -> str:
    return p.auditor_roles.get(auditor, "audit.auditor")


# ------------------------------------------------------------------ git (AU-12)

def probe_git(run_cmd: Callable[..., Any], repo: Path) -> bool:
    """AU-12: True when REPO is not a git repository (GIT_ABSENT)."""
    res = run_cmd(["git", "-C", str(repo), "rev-parse", "--git-dir"])
    return not res.ok


RUN_EXCLUDE = (".harness/runs/",)


# ---------------------------------------------------------------- run state

def new_state(skill: str, parsed: Dict[str, Any], git_absent: bool, factory: bool,
              project: Dict[str, Dict[str, Any]], resumed: Optional[Dict[str, Any]],
              assignment: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    p = profile(skill)
    return {
        "models": dict(assignment or {}),
        "repo": parsed["repo"],
        "units": list(parsed["units"]),
        "cap": parsed["cap"],
        "git_absent": bool(git_absent),
        "factory": bool(factory),
        "auditors": {a: {"status": "pending", "failures": 0, "raw": 0} for a in parsed["auditors"]},
        "findings": {},
        "origin": {},
        "vfail": {},
        "split": [],
        "surfaces": [],
        "merges": [],
        "project": dict(project),
        "resumed": resumed or {"entries": [], "numbering": {}, "headings": {}},
        "numbering": dict((resumed or {}).get("numbering", {})),
        "consolidated": None,
        "readjusted": 0,
        "audit_skill": p.skill,
    }


def _norm_file(path: str) -> str:
    path = path.replace("\\", "/").strip()
    while path.startswith("./"):
        path = path[2:]
    return path


def dedup_keys(p: Profile, f: Dict[str, Any]) -> List[str]:
    """AU-06: deterministic dedup keys of a finding, per skill."""
    ev = f.get("evidence") or {}
    file = ev.get("file")
    line = (ev.get("lines") or {}).get("start")
    title = registry.normalize_title(f["title"])
    if p.dedup == "file-line-category":
        # legacy-audit:63: same file, line and category.
        if file:
            return ["%s:%s::%s" % (_norm_file(file), line, f.get("category") or "")]
        return ["::%s::%s" % (f.get("category") or "", title)]
    if p.dedup == "place-lens":
        # ai-lab-audit:70: same file and line, or same document and date, and same lens.
        if ev.get("journal_date"):
            place = "%s@%s" % (_norm_file(file or ""), ev["journal_date"])
        elif file:
            place = "%s:%s" % (_norm_file(file), line)
        else:
            place = "::" + title
        return [place + "::" + f["axis"]]
    # vibecode-audit:82: same statement, or same set of occurrences.
    keys = ["pattern::" + title]
    places = places_of(f)
    if places:
        keys.append("occurrences::" + "|".join(sorted(places)))
    return keys


def places_of(f: Dict[str, Any]) -> List[str]:
    """Normalized 'file:line' (or 'file') of the occurrences of a pattern."""
    return [_norm_file(o["file"]) + (":%d" % o["line"] if o.get("line") else "") for o in f.get("occurrences") or []]


def covered_by_parts(state: Dict[str, Any], f: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """vibecode-audit:82 upstream of a split: a pattern whose occurrences are
    exactly the union of the occurrences of at least two parts, either parts of
    one split_from of this run or resumed detail blocks of one axis, is the
    coarse pattern already split. Returns {group, parts} or None."""
    target = set(places_of(f))
    if not target:
        return None
    groups: Dict[str, List[Tuple[str, set]]] = {}
    for fid, g in state["findings"].items():
        if g.get("split_from") and g["status"] != findings.DUPLICATE:
            groups.setdefault("split of %s" % g["split_from"], []).append((fid, set(places_of(g))))
    labels = {v: k for k, v in texts()["vibecode-audit"]["axes"].items()}
    axes = {e["id"]: labels.get(e["fields"].get("axis"), e["fields"].get("axis"))
            for e in state["resumed"]["entries"] if e["kind"] == "row" and e["section"] == "attack"}
    for e in state["resumed"]["entries"]:
        if e["kind"] == "block" and e.get("places"):
            groups.setdefault("resumed %s parts" % axes.get(e["id"], "?"), []).append(
                (e["id"], {_norm_file(x) for x in e["places"]}))
    for name, members in sorted(groups.items()):
        inside = [(i, ps) for i, ps in members if ps and ps <= target]
        if len(inside) >= 2 and set().union(*[ps for _, ps in inside]) == target:
            return {"group": name, "parts": [i for i, _ in inside]}
    return None


def resumed_keys(p: Profile, resumed: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """AU-06: dedup keys of the resumed deliverable: the title of every entry
    (rows and blocks); vibecode-audit:82 also: the statement, and the set of
    occurrences of its detail block."""
    keys: Dict[str, Dict[str, Any]] = {}
    for e in resumed["entries"]:
        title = registry.normalize_title(e["title"])
        own = ["title::" + title]
        if p.dedup == "pattern":
            own.append("pattern::" + title)
            if e.get("places"):
                own.append("occurrences::" + "|".join(sorted(_norm_file(x) for x in e["places"])))
        for key in own:
            keys.setdefault(key, e)
    return keys


def _registry_item(f: Dict[str, Any]) -> Dict[str, Any]:
    """{file, line, title} of a finding for the run registry, whose key equals
    the project key (RG-02): a pattern with occurrences or a finding without
    a file is keyed '::<title>'."""
    ev = f.get("evidence") or {}
    file = "" if f.get("occurrences") else (ev.get("file") or "")
    return {"file": file, "line": (ev.get("lines") or {}).get("start"), "title": f["title"]}


def _taken_ids(state: Dict[str, Any]) -> set:
    taken = set(state["findings"]) | {s["id"] for s in state["split"]}
    taken |= {e["id"] for e in state["resumed"]["entries"]}
    return taken


def _assign_id(state: Dict[str, Any], prefix: str, wanted: Any, reserved: Iterable[str] = ()) -> str:
    taken = _taken_ids(state) | set(reserved)
    if isinstance(wanted, str) and re.match(r"^%s-\d+$" % re.escape(prefix), wanted) and wanted not in taken:
        return wanted
    n = 1
    while "%s-%02d" % (prefix, n) in taken:
        n += 1
    return "%s-%02d" % (prefix, n)


def git_pre_finding(p: Profile, state: Dict[str, Any], a: Auditor, items: List[Dict[str, Any]]
                    ) -> Optional[Dict[str, Any]]:
    """LA-03: GIT_ABSENT -> a pre-finding of the lens flagged git_prefinding (versioning
    absent), added to what its auditor returned; it is validated and
    deduplicated like any other finding."""
    if not lens_flag(p.skill, a.name, "git_prefinding") or not state.get("git_absent"):
        return None
    fid = _assign_id(state, a.prefix, None, [i["id"] for i in items])
    return findings.adapt(p.auditor_format, dict(texts()[p.skill]["git_absent"], id=fid), fid, a.unit, p.skill)


_LAB_PROFILE = profiles.by_machine("lab")
ROLE_AGENTS = {l["model_role"]: l["subagent_type"] for l in _LAB_PROFILE["lenses"] if l.get("subagent_type")}
LAB_AGENTS = tuple(dict.fromkeys(ROLE_AGENTS.values()))
LAB_SKILL = ".claude/skills/%s/SKILL.md" % _LAB_PROFILE["orchestrator"]["name"]


def lab_present(repo: Path) -> bool:
    """AA-05: LAB is true when the lab agents and the lab-run skill are installed."""
    repo = Path(repo)
    return all((repo / ".claude" / "agents" / ("%s.md" % n)).is_file() for n in LAB_AGENTS) \
        and (repo / LAB_SKILL).is_file()


def subagent_type(p: Profile, auditor: str, lab: bool) -> str:
    """AA-05: the lab agent of the lens' role when LAB, otherwise Explore (every
    audit launches Explore agents when it has no specialist)."""
    role = p.auditor_roles.get(auditor)
    return ROLE_AGENTS[role] if lab and role in ROLE_AGENTS else "Explore"


def settle_status(audit_date: str, entry_date: str) -> Optional[str]:
    """AA-10: 'TRANCHEE (journal <date>)' when the journal entry is later than
    the audit date (a deterministic date comparison), otherwise None."""
    for value in (audit_date, entry_date):
        if not _valid_date(value):
            raise AuditUsage("not a calendar date (YYYY-MM-DD): %r" % (value,))
    return "TRANCHEE (journal %s)" % entry_date if entry_date > audit_date else None


def _auditor(p: Profile, name: str) -> Auditor:
    for a in p.auditors:
        if a.name == name:
            return a
    raise AuditUsage("unknown auditor for %s: %r" % (p.skill, name))


def _parse_auditor_output(p: Profile, state: Dict[str, Any], a: Auditor, output: Optional[str]) -> List[Dict[str, Any]]:
    """The auditor's final block -> FD-01 findings (PROPOSED). Any defect makes
    the output unusable, hence a dead agent (AU-04)."""
    obj = verdicts.extract_terminal_json(output)
    items = obj.get("findings")
    if not isinstance(items, list):
        raise verdicts.UnusableOutput("auditor: 'findings' must be a list")
    out: List[Dict[str, Any]] = []
    reserved: List[str] = []
    for item in items:
        if not isinstance(item, dict):
            raise verdicts.UnusableOutput("auditor: every finding must be an object")
        fid = _assign_id(state, a.prefix, item.get("id"), reserved)
        reserved.append(fid)
        axis = None if p.dedup == "pattern" else a.unit
        if p.dedup == "pattern" and item.get("axis") not in findings.VIB_AXES:
            raise verdicts.UnusableOutput("auditor %s: axis must be one of %s" % (fid, "|".join(findings.VIB_AXES)))
        if p.dedup == "place-lens" and item.get("nature") not in findings.NATURES:
            raise verdicts.UnusableOutput("auditor %s: nature must be one of %s" % (fid, "|".join(findings.NATURES)))
        try:
            out.append(findings.adapt(p.auditor_format, item, fid, axis, p.skill))
        except findings.FindingError as exc:
            raise verdicts.UnusableOutput(str(exc))
    return out


def record_auditor(state: Dict[str, Any], reg: registry.Registry, name: str, output: Optional[str],
                   dead: bool, peers: Optional[Dict[str, str]] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """AU-04, AU-06: an auditor's return. Returns (decision, journal fields).
    `peers` (AD-07): RG-02 key -> "<id> (<set>)" of the findings the other lens
    sets of the same /audit cycle already reported; the first set keeps the
    finding, a later set drops the twin as a DUPLICATE."""
    peers = peers or {}
    p = profile(state["audit_skill"])
    a = _auditor(p, name)
    slot = state["auditors"].get(name)
    if slot is None:
        raise AuditUsage("auditor %s was not launched in this run (units: %s)" % (name, ", ".join(state["units"])))
    if slot["status"] != "pending":
        raise AuditUsage("auditor %s already returned its result: %s" % (name, slot["status"]))
    log = {"iteration": 1, "verdict": "DEAD", "produced": 0, "rejected": 0}
    reason = "dead agent"
    items: List[Dict[str, Any]] = []
    if not dead:
        try:
            items = _parse_auditor_output(p, state, a, output)
        except verdicts.UnusableOutput as exc:
            dead, reason = True, "unusable output: %s" % exc
    if dead:
        failures = slot["failures"]
        slot["failures"] = failures + 1
        if relaunch.on_dead(failures) == relaunch.RELAUNCH:
            return {"action": "relaunch", "auditor": name, "reason": reason}, log
        slot["status"] = "failed"
        if p.dead == SURFACE:
            # VA-05 / vibecode-audit:76: an unauditable surface of severity major.
            state["surfaces"].append({"auditor": name, "unit": a.unit, "severity": "major", "reason": reason})
            return {"action": "surface", "auditor": name, "severity": "major",
                    "reason": "family not audited after one relaunch (%s): unauditable surface" % reason}, log
        return {"action": "not-audited", "auditor": name,
                "reason": "%s not audited after one relaunch (%s)" % (name, reason)}, log
    slot["status"] = "ok"
    pre = git_pre_finding(p, state, a, items)
    if pre is not None:
        items.insert(0, pre)
    slot["raw"] = len(items)
    seen: Dict[str, str] = {}
    for fid, f in state["findings"].items():
        for key in dedup_keys(p, f):
            seen.setdefault(key, fid)
    resumed = resumed_keys(p, state["resumed"])
    to_validate, duplicates, merges = [], [], []
    for f in items:
        keys = dedup_keys(p, f)
        twin = next((seen[k] for k in keys if k in seen), None)
        earlier = next((resumed[k] for k in keys + ["title::" + registry.normalize_title(f["title"])]
                        if k in resumed), None)
        project = state["project"].get(registry.project_key(f))
        previous = None
        if project is not None:
            previous = "%s@%s" % (project["status"], project["source"])
        blocking = project is not None and project["status"] in registry.BLOCKING
        sibling = peers.get(registry.project_key(f))
        covered = covered_by_parts(state, f) if p.dedup == "pattern" and twin is None and earlier is None \
            and not blocking and sibling is None else None
        if covered is not None:
            why = "union of the occurrences of %s (%s): already split" % (", ".join(covered["parts"]),
                                                                         covered["group"])
            f = findings.transition(f, findings.DUPLICATE, reason=why, previous=previous)
            state["findings"][f["id"]] = f
            state["origin"][f["id"]] = name
            item = _registry_item(f)
            if reg.current(registry.finding_key(item["file"], item["title"])) in (None, findings.DUPLICATE):
                reg.add(item, findings.DUPLICATE, id=f["id"], reason=why)
            duplicates.append({"id": f["id"], "title": f["title"], "reason": why})
            continue
        if earlier is None and not blocking and sibling is None and twin is not None and p.dedup == "pattern" \
                and state["origin"].get(twin) != name:
            # VA-07: the same pattern from two auditors is merged, never dropped
            # at random, whatever the call order and the twin's status (lot L12).
            merges.append({"finding": f["id"], "with": twin})
        elif twin is not None or earlier is not None or blocking or sibling is not None:
            why = ("already in %s as %s (%s)" % (p.deliverable, earlier["id"], earlier["status"] or earlier["section"])
                   if earlier is not None else "previous=%s" % previous if blocking
                   else "already reported as %s in this audit cycle" % sibling if sibling is not None
                   else "duplicate of %s (this run)" % twin)
            f = findings.transition(f, findings.DUPLICATE, reason=why, previous=previous)
            state["findings"][f["id"]] = f
            state["origin"][f["id"]] = name
            item = _registry_item(f)
            current = reg.current(registry.finding_key(item["file"], item["title"]))
            if current in (None, findings.DUPLICATE):
                # A DUPLICATE event never overwrites the final state of a key
                # (resumed or validated) in the run registry.
                reg.add(item, findings.DUPLICATE, id=f["id"], reason=why)
            duplicates.append({"id": f["id"], "title": f["title"], "reason": why})
            for key in keys:
                seen.setdefault(key, f["id"])
            continue
        if previous:
            # RG-04: FIXED or NOT_FIXED earlier: passed on as history, never dismissed.
            f = dict(f, previous=previous)
        state["findings"][f["id"]] = f
        state["origin"][f["id"]] = name
        for key in keys:
            seen.setdefault(key, f["id"])
        to_validate.append(f)
    state["merges"].extend(merges)
    log.update(verdict="FINDINGS" if items else "NO_FINDING", produced=len(items), rejected=len(duplicates))
    decision = {"action": "validate" if to_validate else "none", "auditor": name,
                "to_validate": to_validate, "duplicates": duplicates}
    if merges:
        decision["merge"] = merges
    return decision, log


def record_validator(state: Dict[str, Any], reg: registry.Registry, fid: str, output: Optional[str],
                     dead: bool) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """AU-05 (and FD-06, FD-07): a validator's return."""
    p = profile(state["audit_skill"])
    f = state["findings"].get(fid)
    if f is None:
        raise AuditUsage("unknown finding for this run: %r" % (fid,))
    if f["status"] != findings.PROPOSED:
        raise AuditUsage("finding %s already decided: %s" % (fid, f["status"]))
    log = {"iteration": 1, "verdict": "DEAD", "validated": 0, "rejected": 0}
    parsed = None
    reason = "dead agent"
    if not dead:
        try:
            parsed = findings.parse_verdict(output, p.skill)
        except verdicts.UnusableOutput as exc:
            dead, reason = True, "unusable output: %s" % exc
    if dead:
        failures = state["vfail"].get(fid, 0)
        state["vfail"][fid] = failures + 1
        if relaunch.on_dead(failures) == relaunch.RELAUNCH:
            return {"action": "relaunch", "finding": fid, "reason": reason}, log
        new = findings.transition(f, findings.UNVALIDATED, reason="validator died twice (%s)" % reason)
        state["findings"][fid] = new
        reg.add(_registry_item(new), findings.UNVALIDATED, id=fid)
        return {"action": "unvalidated", "finding": fid,
                "reason": "validator died twice: finding not validated, never CONFIRMED"}, log
    assert parsed is not None
    results = findings.apply_verdict(f, parsed)
    origin = state["origin"][fid]
    if parsed["verdict"] == "SPLIT":
        # vibecode-audit:82: every part is deduplicated against the resumed
        # report and the run before numbering; a part already there is set
        # aside (DUPLICATE), never renumbered.
        resumed = resumed_keys(p, state["resumed"])
        seen: Dict[str, str] = {}
        for other, g in state["findings"].items():
            if other != fid:
                for key in dedup_keys(p, g):
                    seen.setdefault(key, other)
        dropped = []
        for n, part in enumerate(results):
            keys = dedup_keys(p, part) + ["title::" + registry.normalize_title(part["title"])]
            earlier = next((resumed[k] for k in keys if k in resumed), None)
            twin = next((seen[k] for k in keys if k in seen), None)
            if earlier is None and twin is None:
                for key in keys:
                    seen.setdefault(key, part["id"])
                continue
            why = ("already in %s as %s" % (p.deliverable, earlier["id"]) if earlier is not None
                   else "duplicate of %s (this run)" % twin)
            results[n] = dict(part, status=findings.DUPLICATE, history=list(f.get("history", [])) + [f["status"]],
                              reason=why)
            dropped.append({"id": part["id"], "title": part["title"], "reason": why})
        state["split"].append({"id": fid, "title": f["title"], "auditor": origin, "parts": [r["id"] for r in results]})
        # The parts take the place of the original (stable order of equal findings).
        rebuilt: Dict[str, Dict[str, Any]] = {}
        for key, value in state["findings"].items():
            if key != fid:
                rebuilt[key] = value
                continue
            for part in results:
                rebuilt[part["id"]] = part
                state["origin"][part["id"]] = origin
                item = _registry_item(part)
                if part["status"] == findings.CONFIRMED:
                    reg.add(item, findings.CONFIRMED, id=part["id"], reason=parsed["reason"])
                elif reg.current(registry.finding_key(item["file"], item["title"])) in (None, findings.DUPLICATE):
                    reg.add(item, findings.DUPLICATE, id=part["id"], reason=part["reason"])
        state["findings"] = rebuilt
        log.update(verdict="SPLIT", validated=len(results) - len(dropped), rejected=len(dropped))
        decision = {"action": "split", "finding": fid, "parts": [r["id"] for r in results]}
        if dropped:
            decision["duplicates"] = dropped
        return decision, log
    new = results[0]
    state["findings"][fid] = new
    reg.add(_registry_item(new), new["status"], id=fid, reason=parsed["reason"])
    if new["status"] == findings.CONFIRMED:
        log.update(verdict="CONFIRMED", validated=1)
        return {"action": "confirmed", "finding": fid}, log
    log.update(verdict="REJECTED", rejected=1)
    return {"action": "rejected", "finding": fid, "reason": parsed["reason"]}, log


# --------------------------------------------------------- next step (AU-07, AU-13)

def counters(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """AU-13: report counters per lens or family (raw = 'échec' text of the
    templates when the auditor failed twice)."""
    p = profile(state["audit_skill"])
    failed = texts()["common"]["failed"]
    rows = []
    for name, slot in state["auditors"].items():
        mine = [f for fid, f in state["findings"].items() if state["origin"].get(fid) == name]
        row: Dict[str, Any] = {"unit": name}
        row["raw"] = failed if slot["status"] == "failed" else slot["raw"]
        confirmed = [f for f in mine if f["status"] == findings.CONFIRMED and not f.get("split_from")]
        parts = [f for f in mine if f["status"] == findings.CONFIRMED and f.get("split_from")]
        row["confirmed"] = len(confirmed)
        row["rejected"] = len([f for f in mine if f["status"] in (findings.REJECTED, findings.DUPLICATE)])
        if "split" in p.counters:
            row["split"] = len([s for s in state["split"] if s["auditor"] == name])
        if "occurrences" in p.counters:
            row["occurrences"] = sum(f.get("count", len(f.get("occurrences") or [])) for f in confirmed + parts)
        if "corrections" in p.counters:
            row["corrections"] = len([f for f in confirmed if f.get("nature") == "correction"])
            row["questions"] = len([f for f in confirmed if f.get("nature") == "question"])
        row["unvalidated"] = len([f for f in mine if f["status"] == findings.UNVALIDATED])
        rows.append(row)
    return rows


def next_step(state: Dict[str, Any]) -> Dict[str, Any]:
    """AU-07: audit -> validate -> consolidate, or straight to the report
    (vibecode-audit: to the deliverable) when nothing is CONFIRMED."""
    p = profile(state["audit_skill"])
    pending_auditors = [a for a, s in state["auditors"].items() if s["status"] == "pending"]
    pending = [fid for fid, f in state["findings"].items() if f["status"] == findings.PROPOSED]
    if p.dedup == "pattern":
        # VA-09: a partial validation starts with the critical patterns, then
        # the ones with the most occurrences (stable for equal ones).
        pending.sort(key=lambda fid: (state["findings"][fid]["severity"] != "critical",
                                      -int(state["findings"][fid].get("count") or 0)))
    confirmed = [fid for fid, f in state["findings"].items() if f["status"] == findings.CONFIRMED]
    if state["consolidated"] is not None:
        step = "render"
    elif pending_auditors:
        step = "audit"
    elif pending:
        step = "validate"
    elif not confirmed:
        step = p.on_zero
    else:
        step = "consolidate"
    return {"step": step, "pending_auditors": pending_auditors, "pending_findings": pending,
            "confirmed": len(confirmed),
            "not_audited": [a for a, s in state["auditors"].items() if s["status"] == "failed"],
            "counters": counters(state)}


# ------------------------------------------------------- consolidation (AU-08)

def _final_prefix(p: Profile, f: Dict[str, Any]) -> str:
    if len(p.numbering) == 1:
        return p.numbering[0][0]
    return "COR" if f.get("nature") == "correction" else "Q"


def delivered(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    p = profile(state["audit_skill"])
    keep = (findings.CONFIRMED, findings.UNVALIDATED) if p.deliver_unvalidated else (findings.CONFIRMED,)
    return [f for f in state["findings"].values() if f["status"] in keep]


def consolidate(state: Dict[str, Any]) -> Dict[str, Any]:
    """AU-08: TS-03 ordering, then renumbering per prefix (going on from the
    resumed deliverable); depends_on follows the new ids. Idempotent."""
    if state["consolidated"] is not None:
        return state["consolidated"]
    p = profile(state["audit_skill"])
    items = delivered(state)
    mapping: Dict[str, str] = {}
    order: Dict[str, List[str]] = {}
    for prefix, ordering in p.numbering:
        n = state["numbering"].get(prefix, 0)
        order[prefix] = []
        for f in tshirt.ordered([f for f in items if _final_prefix(p, f) == prefix], ordering):
            n += 1
            mapping[f["id"]] = "%s-%02d" % (prefix, n)
            order[prefix].append(mapping[f["id"]])
        state["numbering"][prefix] = n
    renamed: Dict[str, Dict[str, Any]] = {}
    origin: Dict[str, str] = {}
    for fid, f in state["findings"].items():
        new_id = mapping.get(fid, fid)
        g = copy.deepcopy(f)
        g["id"] = new_id
        if g.get("depends_on"):
            g["depends_on"] = [mapping.get(d, d) for d in g["depends_on"]]
        renamed[new_id] = g
        origin[new_id] = state["origin"].get(fid, "")
    state["findings"], state["origin"] = renamed, origin
    state["consolidated"] = {"renamed": mapping, "order": order}
    return state["consolidated"]


# ------------------------------------------------------ plan (LA-10, AA-02, VA-02, TS-05)

def plan(state: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
    """What the ceiling and the arbitration leave of the delivered findings.
    `data` is the render data of the skill (findings -> milestone / not_retained /
    omit / detail; optional `order`: the ids in the arbitrated order).
    legacy-audit: finding -> spec mapping (TS-02) in the order of the
    milestones, the --max-specs ceiling applied to the specs, load per
    milestone (TS-05). ai-lab-audit: specs of the corrections by lens, the
    --max-questions ceiling applied to the questions in the arbitrated order.
    vibecode-audit: the --max-findings ceiling applied to the detailed
    patterns, quick wins and raised criticals."""
    p = profile(state["audit_skill"])
    done = consolidate(state)
    entries = data.get("findings") or {}
    ids = [fid for prefix, _ in p.numbering for fid in done["order"].get(prefix, [])]
    if data.get("order"):
        if sorted(data["order"]) != sorted(ids):
            raise AuditUsage("data: order must list every delivered finding once")
        ids = list(data["order"])
    items = [state["findings"][fid] for fid in ids
             if not (entries.get(fid) or {}).get("omit") and not (entries.get(fid) or {}).get("not_retained")]
    cap = state["cap"]
    if p.skill == "legacy-audit":
        work = []
        for f in items:
            number = (entries.get(f["id"]) or {}).get("milestone")
            if number is None:
                raise AuditUsage("data: finding %s needs a milestone, or not_retained" % f["id"])
            work.append(dict(f, milestone=int(number)))
        work.sort(key=lambda f: f["milestone"])
        capped = tshirt.apply_cap(tshirt.plan_specs(work, "category-milestone"), cap)
        kept_ids = {i for s in capped["kept"] for i in s["findings"]}
        return {"specs": capped["kept"], "excess_specs": capped["excess"],
                "not_retained_cap": [f["id"] for f in work if f["id"] not in kept_ids],
                "load": tshirt.load([f for f in work if f["id"] in kept_ids], "milestone")}
    if p.skill == "ai-lab-audit":
        questions = [f for f in items if f.get("nature") != "correction"]
        capped = tshirt.apply_cap(questions, cap) if questions else {"kept": [], "excess": []}
        return {"specs": tshirt.plan_specs([f for f in items if f.get("nature") == "correction"], "lens"),
                "questions": [f["id"] for f in capped["kept"]], "not_retained_cap": [f["id"] for f in capped["excess"]],
                "load": tshirt.load(capped["kept"], "axis")}
    pool = [f for f in items if (entries.get(f["id"]) or {}).get("detail", True)]
    capped = tshirt.apply_cap(pool, cap) if pool else {"kept": [], "excess": []}
    return {"detailed": [f["id"] for f in capped["kept"]], "short_cap": [f["id"] for f in capped["excess"]],
            "quick_wins": tshirt.quick_wins(items), "raised_criticals": tshirt.raised_criticals(items),
            "load": tshirt.load(items, "axis")}


# ---------------------------------------------------- gates (AU-15, AU-16)

def gate_calls(skill: str, themes: int) -> Dict[str, Any]:
    """AU-16: themes split into calls of 4 at most; beyond the skill's maximal
    number of calls -> exit 2 (regroup the themes)."""
    p = profile(skill)
    if themes < 1:
        raise AuditUsage("at least one theme is required")
    calls = [list(range(i, min(i + MAX_THEMES_PER_CALL, themes))) for i in range(0, themes, MAX_THEMES_PER_CALL)]
    if len(calls) > p.max_calls:
        raise AuditUsage("%d themes need %d calls of %s, at most %d: regroup the themes"
                         % (themes, len(calls), p.theme_gate, p.max_calls))
    return {"gate": p.theme_gate, "calls": calls, "max_calls": p.max_calls}


def readjust_option(skill: str) -> str:
    """AU-15: label of the readjustment option of the skill's final gate (catalog)."""
    return gates.get(profile(skill).final_gate).options[READJUST_INDEX]


def final_options(state: Dict[str, Any]) -> List[str]:
    """AU-15: options of the final gate; the readjustment is gone once used."""
    options = list(gates.get(profile(state["audit_skill"]).final_gate).options)
    readjust_label = readjust_option(state["audit_skill"])
    return [o for o in options if o != readjust_label] if state["readjusted"] else options


def readjust(state: Dict[str, Any]) -> Dict[str, Any]:
    """AU-15: one readjustment, which replays the theme gate."""
    p = profile(state["audit_skill"])
    if state["readjusted"] >= 1:
        raise AuditUsage("'%s' was already used once in this run: write or abandon" % readjust_option(p.skill))
    state["readjusted"] += 1
    return {"action": "replay", "gate": p.theme_gate, "final_options": final_options(state)}


# ------------------------------------------------------- deliverable statuses (AU-10)

def _valid_date(text: str) -> bool:
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def check_status(p: Profile, status: str) -> str:
    """AU-10: TRANCHEE (journal <date>) needs a real calendar date."""
    settled = SETTLED_STATUS.match(status)
    if status in p.statuses or (p.settled and settled and _valid_date(settled.group(1))):
        return status
    allowed = list(p.statuses) + (["TRANCHEE (journal <YYYY-MM-DD>)"] if p.settled else [])
    raise AuditUsage("%s: deliverable status %r not allowed (expected one of %s)"
                     % (p.skill, status, ", ".join(allowed)))


# ----------------------------------------------------- templates and parsing (AU-09)

def _t(text: str, **values: Any) -> str:
    return Template(text).substitute({k: ("" if v is None else str(v)) for k, v in values.items()})


def _cell(value: Any) -> str:
    text = " ".join(str(value if value is not None else "").split())
    return text.replace("|", "\\|")


def _row(template: str, **values: Any) -> str:
    return _t(template, **{k: _cell(v) for k, v in values.items()})


def _split_cells(line: str) -> List[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", body)]


def _placeholders(row_template: str) -> List[str]:
    return [re.sub(r"^\$\{?(\w+)\}?$", r"\1", c) for c in _split_cells(row_template)]


def _heading_regex(template: str) -> "re.Pattern[str]":
    pattern = re.escape(template)
    pattern = pattern.replace(re.escape("$number"), r"(?P<number>\d+)").replace(re.escape("$title"), r"(?P<title>.*)")
    pattern = pattern.replace(re.escape("$id"), r"(?P<id>%s)" % ID_RE)
    return re.compile("^" + pattern + "$")


class Section(NamedTuple):
    key: str
    heading: str
    row: Optional[str]           # row template (table sections)
    header: Optional[str]        # header line template (table sections)
    block: Optional[List[str]]   # block template (sections of blocks)


# Free-text column of a table: an unescaped '|' in it gives extra cells.
FREE_COLUMNS = ("title", "question")


def _sections(skill: str, legacy: bool = False) -> List[Section]:
    """Known sections of a deliverable, from the templates (`legacy`: those of the French templates)."""
    t = (legacy_texts() if legacy else texts())[skill]

    def table(key: str) -> Section:
        return Section(key, t[key + "_heading"], t[key + "_row"], t[key + "_table"][0], None)
    if skill == "legacy-audit":
        return [table("milestone"), table("not_retained"), table("rejected")]
    if skill == "ai-lab-audit":
        return [table("corrections"), Section("questions", t["questions_heading"], None, None, t["question"]),
                table("not_retained"), table("rejected")]
    return [table("attack"), Section("detail", t["detail_heading"], None, None, t["detail"]),
            table("short"), table("rejected")]


def _line_prefix(block: List[str], placeholder: str) -> Optional[str]:
    lines = [b for b in block if "$" + placeholder in b]
    return lines[0].split("$" + placeholder)[0] if lines else None


def _status_prefix(block: List[str]) -> str:
    return _line_prefix(block, "status") or ""


def _layout(sec: Section, header: List[str]) -> Optional[Dict[str, Any]]:
    """Column of each known field in the actual header of a table (extra
    columns tolerated); None when a known column is missing."""
    labels = dict(zip(_split_cells(sec.header or ""), _placeholders(sec.row or "")))
    names: Dict[str, int] = {}
    for i, label in enumerate(header):
        name = labels.get(label)
        if name is not None and name not in names:
            names[name] = i
    if set(names) != set(labels.values()):
        return None
    return {"names": names, "width": len(header)}


def _row_fields(cells: List[str], layout: Dict[str, Any]) -> Optional[Tuple[Dict[str, str], Dict[str, int]]]:
    """Known fields of a row and the cell index of each; extra cells (an
    unescaped '|') belong to the free-text column. None when unreadable."""
    extra = len(cells) - layout["width"]
    if extra < 0:
        return None
    names = layout["names"]
    free = next((names[c] for c in FREE_COLUMNS if c in names), None)
    if extra and free is None:
        return None
    fields: Dict[str, str] = {}
    index: Dict[str, int] = {}
    for name, i in names.items():
        if extra and i == free:
            fields[name], index[name] = " | ".join(cells[i:i + extra + 1]), i
        elif extra and i > free:
            fields[name], index[name] = cells[i + extra], i + extra
        else:
            fields[name], index[name] = cells[i], i
    return fields, index


def parse_deliverable(skill: str, text: str) -> Dict[str, Any]:
    """AU-09: the lines of an existing deliverable, kept for the new one.
    Returns {"skill", "entries": [{id, title, status, section, milestone,
    kind, line or lines, fields, cells, index, places}], "numbering":
    {prefix: highest number found anywhere in the text}, "headings":
    {milestone number: heading line}}. Columns are found by their header;
    extra columns and an unescaped '|' in a title are tolerated; any other
    unreadable finding line is an explicit stop naming it (exit 1), never a
    silent loss. A status outside the skill's set -> exit 2 (AU-10)."""
    p = profile(skill)
    sections = _sections(skill) + _sections(skill, legacy=True)   # a French deliverable is still read back
    entries: List[Dict[str, Any]] = []
    headings: Dict[str, str] = {}
    tables: Dict[str, Dict[str, Any]] = {}
    current: Optional[Section] = None
    milestone: Optional[int] = None
    block: Optional[Dict[str, Any]] = None
    layout: Optional[Dict[str, Any]] = None

    def stop(number: int, why: str, line: str) -> None:
        raise AuditStop("%s, line %d: %s; fix or remove it, nothing was read: %s"
                        % (p.deliverable, number, why, line))
    for number, line in enumerate(text.splitlines(), 1):
        if line.startswith("## "):
            block, current, milestone, layout = None, None, None, None
            for sec in sections:
                m = _heading_regex(sec.heading).match(line)
                if m:
                    current = sec
                    if "number" in m.groupdict():
                        milestone = int(m.group("number"))
                        headings[str(milestone)] = line
                    break
            continue
        if current is None:
            if line.startswith("|") and any(re.fullmatch(ID_RE, c) for c in _split_cells(line)):
                stop(number, "finding line outside the known sections", line)
            continue
        if current.block is not None:
            if line.startswith("### "):
                m = _heading_regex(current.block[0]).match(line)
                if not m:
                    stop(number, "unreadable heading", line)
                block = {"id": m.group("id"), "title": m.group("title"), "section": current.key, "kind": "block",
                         "lines": [line], "milestone": None, "status": None, "places": []}
                entries.append(block)
                continue
            if block is not None and line.startswith("- "):
                block["lines"].append(line)
                prefix = _status_prefix(current.block)
                if line.startswith(prefix):
                    block["status"] = check_status(p, line[len(prefix):].strip())
                places = _line_prefix(current.block, "occurrences")
                if places is not None and line.startswith(places):
                    block["places"] = re.findall(r"`([^`]+)`", line[len(places):])
                continue
            if line.startswith("|") and any(re.fullmatch(ID_RE, c) for c in _split_cells(line)):
                stop(number, "finding line outside the known sections", line)
            block = None
            continue
        if not line.startswith("|"):
            continue
        cells = _split_cells(line)
        table_key = _table_key(current.key, milestone)
        if layout is None:
            layout = _layout(current, cells)
            if layout is None:
                stop(number, "table header without the expected columns", line)
            tables[table_key] = {"header": line, "separator": None, "layout": layout}
            continue
        if all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
            if tables[table_key]["separator"] is None:
                tables[table_key]["separator"] = line
            continue
        parsed = _row_fields(cells, layout)
        if parsed is None or not re.fullmatch(ID_RE, parsed[0]["id"]):
            stop(number, "unreadable table line", line)
        fields, index = parsed
        status = fields.get("status")
        entries.append({"id": fields["id"], "title": fields.get("title") or fields.get("question") or "",
                        "section": current.key, "kind": "row", "line": line, "fields": fields, "cells": cells,
                        "index": index, "milestone": milestone,
                        "status": check_status(p, status) if status is not None else None})
    numbering: Dict[str, int] = {}
    for prefix, _ in p.numbering:
        found = [int(x) for x in re.findall(r"(?<![A-Za-z0-9-])%s-(\d+)\b" % re.escape(prefix), text)]
        if found:
            numbering[prefix] = max(found)
    return {"skill": skill, "entries": entries, "numbering": numbering, "headings": headings, "tables": tables}


def _table_key(section: str, milestone: Optional[int]) -> str:
    return "%s:%s" % (section, "" if milestone is None else milestone)


def _relayouter(doc: Dict[str, Any], section: str, milestone: Optional[int], header: List[str],
                row_template: str) -> Tuple[List[str], Callable[[str], str]]:
    """Header of a table and the function placing a new row in it. A resumed
    table whose columns differ from the template (a column added by the
    user) keeps its own header; new rows get an empty cell in the added
    columns, never a misaligned table."""
    record = ((doc.get("resumed") or {}).get("tables") or {}).get(_table_key(section, milestone))
    names = _placeholders(row_template)
    if record is None or record["layout"]["names"] == {n: i for i, n in enumerate(names)} \
            and record["layout"]["width"] == len(names):
        return list(header), lambda row: row
    width, where = record["layout"]["width"], record["layout"]["names"]

    def place(row: str) -> str:
        out = [""] * width
        for name, value in zip(names, _split_cells(row)):
            out[where[name]] = value
        return _emit_row(out)
    return [record["header"], record["separator"] or _emit_row(["---"] * width)], place


def resumed_ids(parsed: Optional[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Ids of a resumed deliverable per section, in file order (vibecode-audit:
    'attack' lists the resumed patterns an explicit order must include)."""
    out: Dict[str, List[str]] = {}
    for e in (parsed or {"entries": []})["entries"]:
        ids = out.setdefault(e["section"], [])
        if e["id"] not in ids:
            ids.append(e["id"])
    return out


def _emit_row(cells: List[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def set_status(skill: str, entry: Dict[str, Any], status: str) -> Dict[str, Any]:
    """AU-10: new deliverable status of a resumed entry (row cell or block line)."""
    p = profile(skill)
    check_status(p, status)
    new = copy.deepcopy(entry)
    new["status"] = status
    if new["kind"] == "row":
        if "status" not in new["fields"]:
            raise AuditUsage("%s: this line has no status" % entry["id"])
        new["fields"]["status"] = status
        new["cells"][new["index"]["status"]] = status
        new["line"] = _emit_row(new["cells"])
    else:
        prefixes = [_status_prefix({s.key: s.block for s in _sections(skill, legacy)}[new["section"]])
                    for legacy in (False, True)]

        def rewrite(line: str) -> str:
            prefix = next((x for x in prefixes if line.startswith(x)), None)
            return prefix + status if prefix is not None else line
        new["lines"] = [rewrite(line) for line in new["lines"]]
    return new


def resumed_registry_status(skill: str, group: List[Dict[str, Any]]) -> Tuple[str, Dict[str, Any]]:
    """AU-09: run registry status of a resumed finding (its row and block):
    REJECTED, UNVALIDATED (vibecode 'non validé'), SETTLED for TRANCHEE,
    CONFIRMED otherwise."""
    if any(e["section"] == "rejected" for e in group):
        return findings.REJECTED, {}
    if skill == "vibecode-audit":
        no = (texts()[skill]["validated"]["no"], legacy_texts()[skill]["validated"]["no"])
        if any(e["kind"] == "row" and e["fields"].get("validated") in no for e in group):
            return findings.UNVALIDATED, {}
    for e in group:
        m = SETTLED_STATUS.match(e.get("status") or "")
        if m:
            return findings.SETTLED, {"settled_by": "journal %s" % m.group(1)}
    return findings.CONFIRMED, {}


def register_resumed(parsed: Dict[str, Any], reg: registry.Registry) -> int:
    """AU-09: the findings of an existing deliverable read back into the run
    registry, one event per id (a row and a block of the same id are one
    finding); returns the number of findings."""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for e in parsed["entries"]:
        groups.setdefault(e["id"], []).append(e)
    for fid, group in groups.items():
        status, extra = resumed_registry_status(parsed["skill"], group)
        reg.add({"file": "", "line": None, "title": group[0]["title"]}, status, id=fid, resumed=True, **extra)
    return len(groups)


# ------------------------------------------------------------------ rendering

def _depends(f: Dict[str, Any], none: str) -> str:
    return ", ".join(f.get("depends_on") or []) or none


def _status(p: Profile, f: Dict[str, Any], entry: Dict[str, Any]) -> str:
    return check_status(p, entry.get("status") or f.get("deliverable_status") or "A_FAIRE")


def _table(lines: List[str], heading: str, header: List[str], rows: Iterable[str]) -> None:
    lines.extend(["", heading])
    lines.extend(header)
    lines.extend(rows)


def _resumed(doc: Dict[str, Any], section: str, milestone: Optional[int] = None) -> List[Dict[str, Any]]:
    resumed = doc.get("resumed") or {"entries": []}
    return [e for e in resumed["entries"] if e["section"] == section
            and (milestone is None or e.get("milestone") == milestone)]


def _entry_of(data: Dict[str, Any], fid: str) -> Dict[str, Any]:
    entry = (data.get("findings") or {}).get(fid, {})
    if not isinstance(entry, dict):
        raise AuditUsage("data: findings.%s must be an object" % fid)
    return entry


def _not_audited(lines: List[str], t: Dict[str, Any], doc: Dict[str, Any]) -> None:
    if doc.get("not_audited"):
        lines.extend(["", t["not_audited_heading"]])
        lines.extend(_t(t["not_audited_line"], unit=u, reason=t["not_audited_reason"]) for u in doc["not_audited"])


def _spec_cell(c: Dict[str, Any], entry: Dict[str, Any]) -> str:
    # Relative links with "/" (LA-09), whatever the separator the skill passed.
    return _t(c["spec_link"], path=entry["spec"].replace("\\", "/")) if entry.get("spec") else c["none"]


def _render_legacy(doc: Dict[str, Any]) -> List[str]:
    p, all_t = profile("legacy-audit"), texts()
    t, c = all_t["legacy-audit"], all_t["common"]
    data = doc["data"]
    lines = [_t(x, name=data["name"], date=data["date"],
                factory=t["factory"]["present" if data["factory"] else "absent"]) for x in t["head"]]
    _not_audited(lines, t, doc)
    milestones = {int(m["number"]): m["title"] for m in data.get("milestones", [])}
    resumed_headings = (doc.get("resumed") or {}).get("headings", {})
    numbers = sorted(set(milestones) | {int(n) for n in resumed_headings})
    rows: Dict[int, List[str]] = {n: [] for n in numbers}
    not_retained: List[str] = []
    for f in doc["findings"]:
        entry = _entry_of(data, f["id"])
        if entry.get("omit"):
            continue
        if entry.get("not_retained"):
            reason = t["not_retained_reasons"].get(entry["not_retained"])
            if reason is None:
                raise AuditUsage("data: findings.%s.not_retained must be one of %s"
                                 % (f["id"], ", ".join(t["not_retained_reasons"])))
            not_retained.append(_row(t["not_retained_row"], id=f["id"], title=f["title"], reason=reason))
            continue
        number = entry.get("milestone")
        if number not in milestones:
            raise AuditUsage("data: finding %s needs a milestone among %s, or not_retained"
                             % (f["id"], ", ".join(str(n) for n in sorted(milestones)) or "none"))
        rows[number].append(_row(t["milestone_row"], id=f["id"], title=f["title"], severity=f["severity"],
                                 size=f.get("size") or c["none"], depends=_depends(f, c["none"]),
                                 spec=_spec_cell(c, entry), status=_status(p, f, entry)))
    for n in numbers:
        heading = (_t(t["milestone_heading"], number=n, title=milestones[n]) if n in milestones
                   else resumed_headings[str(n)])
        header, place = _relayouter(doc, "milestone", n, t["milestone_table"], t["milestone_row"])
        _table(lines, heading, header, [e["line"] for e in _resumed(doc, "milestone", n)] + [place(r) for r in rows[n]])
    header, place = _relayouter(doc, "not_retained", None, t["not_retained_table"], t["not_retained_row"])
    _table(lines, t["not_retained_heading"], header,
           [e["line"] for e in _resumed(doc, "not_retained")] + [place(r) for r in not_retained])
    header, place = _relayouter(doc, "rejected", None, t["rejected_table"], t["rejected_row"])
    _table(lines, t["rejected_heading"], header,
           [e["line"] for e in _resumed(doc, "rejected")]
           + [place(_row(t["rejected_row"], id=f["id"], title=f["title"], reason=f.get("reason", "")))
              for f in doc["rejected"]])
    return lines


def _render_lab(doc: Dict[str, Any]) -> List[str]:
    p, all_t = profile("ai-lab-audit"), texts()
    t, c = all_t["ai-lab-audit"], all_t["common"]
    data = doc["data"]
    lines = [_t(x, name=data["name"], date=data["date"], lab=t["lab"]["present" if data["lab"] else "absent"],
                factory=t["factory"]["present" if data["factory"] else "absent"],
                lenses=", ".join(data["lenses"]), coverage=c["coverage"]["sampled" if doc.get("sampled") else "complete"],
                journal_date=data["journal_date"]) for x in t["head"]]
    _not_audited(lines, t, doc)
    corrections, questions, not_retained = [], [], []
    for f in doc["findings"]:
        entry = _entry_of(data, f["id"])
        if entry.get("omit"):
            continue
        if f.get("nature") == "correction":
            corrections.append(_row(t["corrections_row"], id=f["id"], title=f["title"], axis=f["axis"],
                                    severity=f["severity"], size=f.get("size") or c["none"],
                                    depends=_depends(f, c["none"]), spec=_spec_cell(c, entry),
                                    status=_status(p, f, entry)))
            continue
        question = entry.get("question") or f["title"]
        if entry.get("not_retained"):
            reason = t["not_retained_reasons"].get(entry["not_retained"])
            if reason is None:
                raise AuditUsage("data: findings.%s.not_retained must be one of %s"
                                 % (f["id"], ", ".join(t["not_retained_reasons"])))
            not_retained.append(_row(t["not_retained_row"], id=f["id"], question=question, reason=reason))
            continue
        for key in ("question", "motivation", "decides"):
            if not isinstance(entry.get(key), str) or not entry[key].strip():
                raise AuditUsage("data: question %s needs %s" % (f["id"], key))
        questions.append([_t(x, id=f["id"], title=" ".join(f["title"].split()), question=entry["question"].strip(),
                             motivation=entry["motivation"].strip(), decides=entry["decides"].strip(),
                             size=f.get("size") or c["none"], depends=_depends(f, c["none"]),
                             status=_status(p, f, entry)) for x in t["question"]])
    header, place = _relayouter(doc, "corrections", None, t["corrections_table"], t["corrections_row"])
    _table(lines, t["corrections_heading"], header,
           [e["line"] for e in _resumed(doc, "corrections")] + [place(r) for r in corrections])
    lines.extend(["", t["questions_heading"]])
    blocks = [e["lines"] for e in _resumed(doc, "questions")] + questions
    for i, block in enumerate(blocks):
        if i:
            lines.append("")
        lines.extend(block)
    header, place = _relayouter(doc, "not_retained", None, t["not_retained_table"], t["not_retained_row"])
    _table(lines, t["not_retained_heading"], header,
           [e["line"] for e in _resumed(doc, "not_retained")] + [place(r) for r in not_retained])
    header, place = _relayouter(doc, "rejected", None, t["rejected_table"], t["rejected_row"])
    _table(lines, t["rejected_heading"], header,
           [e["line"] for e in _resumed(doc, "rejected")]
           + [place(_row(t["rejected_row"], id=f["id"], title=f["title"], reason=f.get("reason", "")))
              for f in doc["rejected"]])
    return lines


def _place(o: Dict[str, Any]) -> str:
    return "%s:%s" % (o["file"], o["line"]) if o.get("line") else o["file"]


def _resumed_attack(doc: Dict[str, Any], t: Dict[str, Any], none: str) -> List[Dict[str, Any]]:
    """Resumed rows of the attack table as sortable items (severity, axis,
    difficulty, count, validated)."""
    inverse = {v: k for k, v in t["axes"].items()}
    items = []
    for e in _resumed(doc, "attack"):
        f = e["fields"]
        axis = inverse.get(f.get("axis"), f.get("axis"))
        size = None if f.get("size") == none else f.get("size")
        count = f.get("count") or ""
        if f.get("severity") not in findings.SEVERITIES or axis not in findings.VIB_AXES \
                or (size is not None and size not in findings.SIZES) or not count.isdigit():
            raise AuditStop("%s: resumed line %s has an unreadable severity, axis, difficulty or count: %s"
                            % (profile("vibecode-audit").deliverable, e["id"], e["line"]))
        items.append({"id": e["id"], "severity": f["severity"], "axis": axis, "size": size, "count": int(count),
                      "validated": f.get("validated") != t["validated"]["no"], "entry": e})
    return items


def _render_vibecode(doc: Dict[str, Any]) -> List[str]:
    p, all_t = profile("vibecode-audit"), texts()
    t, c = all_t["vibecode-audit"], all_t["common"]
    data = doc["data"]
    axis = lambda a: t["axes"].get(a, a)  # noqa: E731
    order_gate = gates.get(p.theme_gate)
    # Headless: the minor patterns follow the default of the order gate.
    minors_detailed = order_gate.default.split(" / ")[1] == order_gate.questions[1][0]
    detailed, short = [], []
    for f in doc["findings"]:
        entry = _entry_of(data, f["id"])
        if entry.get("omit"):
            continue
        if entry.get("detail", True):
            detailed.append(f)
            continue
        if f["status"] != findings.CONFIRMED:
            raise AuditUsage("data: %s is not validated: it is detailed, marked as such (VA-09)" % f["id"])
        reason = t["short_reasons"].get(entry.get("reason"))
        if reason is None:
            raise AuditUsage("data: findings.%s.reason must be one of %s" % (f["id"], ", ".join(t["short_reasons"])))
        if data.get("headless") and minors_detailed and entry.get("reason") == "minor":
            raise AuditUsage("data: headless run: the default of %s details the minor patterns (%s)"
                             % (p.theme_gate, f["id"]))
        short.append(_row(t["short_row"], id=f["id"], title=f["title"], axis=axis(f["axis"]),
                          severity=f["severity"], count=f.get("count", 0), reason=reason))
    resumed_items = _resumed_attack(doc, t, c["none"])
    if data.get("order"):
        index = {x["id"]: x for x in resumed_items + detailed}
        if sorted(data["order"]) != sorted(index):
            raise AuditUsage("data: order must list every pattern of the attack table once, resumed ones included")
        seq = [index[i] for i in data["order"]]
    else:
        # The new patterns take their place in the resumed order (TS-03): a new
        # critical comes before the first resumed pattern that sorts after it.
        key = tshirt.sort_key("VIB")
        seq = list(resumed_items)
        for f in detailed:
            k = key(f)
            seq.insert(next((i for i, x in enumerate(seq) if key(x) > k), len(seq)), f)
    attack_header, attack_place = _relayouter(doc, "attack", None, t["attack_table"], t["attack_row"])
    short_header, short_place = _relayouter(doc, "short", None, t["short_table"], t["short_row"])
    rejected_header, rejected_place = _relayouter(doc, "rejected", None, t["rejected_table"], t["rejected_row"])
    attack = []
    for rank, x in enumerate(seq, 1):
        if "entry" in x:
            e = x["entry"]
            cells = list(e["cells"])
            cells[e["index"]["rank"]] = str(rank)
            attack.append(e["line"] if cells == e["cells"] else _emit_row(cells))
            continue
        attack.append(attack_place(_row(t["attack_row"], rank=rank, id=x["id"], title=x["title"], axis=axis(x["axis"]),
                           severity=x["severity"], count=x.get("count", 0), size=x.get("size") or c["none"],
                           risk=t["risks"].get(x.get("regression_risk"), c["none"]),
                           validated=t["validated"]["yes" if x["status"] == findings.CONFIRMED else "no"],
                           depends=_depends(x, c["none"]), status=_status(p, x, _entry_of(data, x["id"])))))
    occurrences = sum(x.get("count", 0) for x in seq)
    short_rows = [e["line"] for e in _resumed(doc, "short")] + [short_place(r) for r in short]
    rejected_rows = [e["line"] for e in _resumed(doc, "rejected")] + \
        [rejected_place(_row(t["rejected_row"], id=f["id"], title=f["title"], reason=f.get("reason", "")))
         for f in doc["rejected"]]
    # Validation box: every pattern of this report (resumed ones included);
    # only the attack table can hold a pattern 'non validé'.
    total = len(seq) + len(short_rows) + len(rejected_rows)
    unvalidated = len([x for x in seq if not (x["validated"] if "entry" in x else x["status"] == findings.CONFIRMED)])
    lines = [_t(x, name=data["name"], date=data["date"], axes=", ".join(axis(a) for a in data["axes"]),
                coverage=c["coverage"]["sampled" if doc.get("sampled") else "complete"]) for x in t["head"]]
    if data.get("headless"):
        lines.extend(t["headless_notice"])
    for s in doc.get("surfaces_dead", []):
        lines.extend(_t(x, unit=s) for x in t["dead_notice"])
    lines.extend(_t(x, validated=total - unvalidated, total=total) for x in t["limits"])
    surfaces = [dict(surface=_t(t["dead_family"]["surface"], unit=s), why=t["dead_family"]["why"],
                     needs=t["dead_family"]["needs"]) for s in doc.get("surfaces_dead", [])]
    for s in data.get("surfaces", []):
        # VA-06: an unauditable surface is never below major.
        if s.get("severity", "major") not in ("major", "critical"):
            raise AuditUsage("data: an unauditable surface is of severity major or critical, not %r (%s)"
                             % (s.get("severity"), s.get("surface")))
    surfaces += list(data.get("surfaces", []))
    _table(lines, t["surfaces_heading"], t["surfaces_table"],
           [_row(t["surfaces_row"], surface=s["surface"], why=s["why"], needs=s["needs"]) for s in surfaces])
    lines.extend(["", t["attack_heading"], _t(t["attack_intro"], order=data.get("order_label") or t["default_order"],
                                              count=len(seq), occurrences=occurrences)])
    lines.append("")
    lines.extend(attack_header)
    lines.extend(attack)
    raised_ids = tshirt.raised_criticals(seq)
    raised = [_t(t["raised_item"], id=x["id"], axis=axis(x["axis"])) for x in seq if x["id"] in raised_ids]
    lines.extend(["", _t(t["quick_wins"], ids=", ".join(tshirt.quick_wins(seq)) or t["nothing"]),
                  "", _t(t["raised"], items=", ".join(raised) or t["nothing"])])
    lines.extend(["", t["detail_heading"]])
    resumed_blocks = {e["id"]: e["lines"] for e in _resumed(doc, "detail")}
    blocks = []
    for x in seq:
        if "entry" in x:
            if x["id"] in resumed_blocks:
                blocks.append(resumed_blocks.pop(x["id"]))
            continue
        occ = x.get("occurrences") or []
        count = x.get("count", len(occ))
        shown = ", ".join(_t(t["occurrence"], place=_place(o)) for o in occ) or c["none"]
        if count > len(occ):
            shown += _t(t["more"], n=count - len(occ))
        blocks.append([_t(y, id=x["id"], title=" ".join(x["title"].split()), axis=axis(x["axis"]),
                          severity=x["severity"], size=x.get("size") or c["none"], count=count,
                          sample=t["sample"] if count > len(occ) else "",
                          risk=t["risks"].get(x.get("regression_risk"), c["none"]),
                          risk_rationale=" ".join(str(x.get("risk_rationale") or c["none"]).split()),
                          impact=" ".join(str(x.get("impact") or c["none"]).split()), occurrences=shown,
                          remediation=" ".join(str(x.get("remediation") or c["none"]).split()),
                          status=_status(p, x, _entry_of(data, x["id"]))) for y in t["detail"]])
    blocks.extend(resumed_blocks.values())   # a resumed block without its row is never lost
    for i, block in enumerate(blocks):
        if i:
            lines.append("")
        lines.extend(block)
    _table(lines, t["short_heading"], short_header, short_rows)
    _table(lines, t["rejected_heading"], rejected_header, rejected_rows)
    lines.append("")
    lines.extend(_t(x, path=data["path"]) for x in t["next"])
    return lines


RENDERERS = {"legacy-audit": _render_legacy, "ai-lab-audit": _render_lab, "vibecode-audit": _render_vibecode}


def render(skill: str, doc: Dict[str, Any]) -> str:
    """The deliverable of `skill` from its document (findings in their final
    order, rejected findings, resumed entries, the skill's data); FD-11 is
    checked before anything is returned (AU-11)."""
    profile(skill)
    try:
        lines = RENDERERS[skill](doc)
    except KeyError as exc:
        raise AuditUsage("data: missing field %s" % exc)
    text = "\n".join(lines) + "\n"
    hits = findings.check_forbidden(text)
    if hits:
        raise AuditStop("forbidden string in %s, line %d: %s" % (profile(skill).deliverable, hits[0]["line"],
                                                                   hits[0]["text"]))
    return text


def document(state: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
    """Render document of a run (consolidated first if needed, AU-08 being
    idempotent); a run still waiting for an auditor or a validator is refused."""
    p = profile(state["audit_skill"])
    step = next_step(state)
    if step["step"] in ("audit", "validate"):
        raise AuditUsage("run not finished: pending auditors %s, pending findings %s"
                         % (step["pending_auditors"], step["pending_findings"]))
    order = [fid for prefix, _ in p.numbering for fid in consolidate(state)["order"].get(prefix, [])]
    return {
        "findings": [state["findings"][fid] for fid in order],
        "rejected": [f for f in state["findings"].values() if f["status"] == findings.REJECTED],
        "not_audited": [a for a, s in state["auditors"].items() if s["status"] == "failed" and p.dead == NOT_AUDITED],
        "surfaces_dead": [s["auditor"] for s in state["surfaces"]],
        "sampled": bool(data.get("sampled")),
        "resumed": state["resumed"],
        "data": data,
    }


# ---------------------------------------------------------- writes (AU-11, AU-14)

def allowed_writes(skill: str) -> List[str]:
    """X5: what an audit may write in the audited repository."""
    p = profile(skill)
    paths = [p.deliverable]
    if p.spec_prefix:
        paths.append("specs/SPEC-%s-<nn>-<slug>.md" % p.spec_prefix)
    return paths + [".harness/runs/"] + list(registry.PROJECT_EXCLUDE[:2])


def check_writable(skill: str, repo: Path, dest: Path) -> str:
    """AU-11 / X5: the only paths an audit writes."""
    p = profile(skill)
    try:
        rel = Path(dest).resolve().relative_to(Path(repo).resolve()).as_posix()
    except ValueError:
        raise AuditUsage("%s is outside the audited repository" % dest)
    if rel == p.deliverable or rel.startswith(".harness/runs/") or rel in registry.PROJECT_EXCLUDE[:2]:
        return rel
    if p.spec_prefix and re.fullmatch(r"specs/SPEC-%s-\d{2}(-[a-z0-9]+(-[a-z0-9]+)*)?\.md" % p.spec_prefix, rel):
        return rel
    raise AuditUsage("%s may only write %s%s, .harness/runs/ and the project registry: %s refused"
                     % (skill, p.deliverable, " and specs/SPEC-%s-<nn>-<slug>.md" % p.spec_prefix
                        if p.spec_prefix else "", rel))


def write_text(skill: str, repo: Path, dest: Path, text: str) -> Dict[str, Any]:
    """AU-11: every written file is checked for the forbidden strings first."""
    rel = check_writable(skill, repo, dest)
    hits = findings.check_forbidden(text)
    if hits:
        raise AuditStop("forbidden string in %s, line %d: %s: nothing written" % (rel, hits[0]["line"], hits[0]["text"]))
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    before = dest.read_text(encoding="utf-8") if dest.exists() else None
    if before != text:
        with open(str(dest), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    return {"file": rel, "state": fusion.CREATED if before is None else
            fusion.IDENTICAL if before == text else fusion.MERGED}


def write_spec(skill: str, repo: Path, f: Dict[str, Any], sections: Dict[str, Any], number: int,
               slug: str) -> Dict[str, Any]:
    """AU-14: specs/SPEC-<prefix>-<nn>-<slug>.md; any existing spec of that
    number (whatever its slug) is kept as it is and reported."""
    p = profile(skill)
    if not p.spec_prefix:
        raise AuditUsage("%s writes no spec" % skill)
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", slug or ""):
        raise AuditUsage("invalid spec slug: %r (lowercase words separated by dashes)" % (slug,))
    specs = Path(repo) / "specs"
    existing = sorted(specs.glob("SPEC-%s-%02d-*.md" % (p.spec_prefix, number))) + \
        sorted(specs.glob("SPEC-%s-%02d.md" % (p.spec_prefix, number)))
    if existing:
        return {"file": "specs/" + existing[0].name, "state": fusion.KEPT}
    dest = specs / ("SPEC-%s-%02d-%s.md" % (p.spec_prefix, number, slug))
    rel = check_writable(skill, repo, dest)
    try:
        text = findings.to_spec(f, sections, p.spec_prefix, number)
    except findings.FindingError as exc:
        raise AuditUsage(str(exc))
    return {"file": rel, "state": fusion.install_file(dest, text)}


def publish(state: Dict[str, Any], reg: registry.Registry, run_name: str, deliverable_written: bool) -> Dict[str, Any]:
    """RG-03 for an audit; AU-12: no git exclusion (no git command) when GIT_ABSENT."""
    return registry.ProjectRegistry(Path(state["repo"])).publish(
        reg.last.values(), state["audit_skill"], run_name, deliverable_written, exclude=not state["git_absent"])
