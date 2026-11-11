# Model per role: .claude/models.json, --models "role=model;..." and the
# model block of generated agent files.
#
# - MD-01: closed role set (ROLES). An unknown role is an explicit error.
# - MD-02: missing file -> "inherit" for every role (SC-04: harness.scout is haiku).
# - MD-03 / X8: an unknown value (or role) in the file is an explicit error,
#   like in --models (decision X8 of 2026-10-17 replaces the "inherit with a
#   warning" of ai-lab:158).
# - MD-04: --models applies to the current cycle only and never rewrites the
#   file; an unknown role (for the orchestrator) or value -> exit 2.
# - MD-05: migration read once, never destructive. models.json missing and
#   lab-models.json present -> models.json written with the keys prefixed
#   "lab.", lab-models.json left byte for byte; both present ->
#   lab-models.json ignored with a warning.
# - MD-06: short aliases (math, neuro, director.cadrage, ..., validator ->
#   lab.*) accepted in --models of lab-run and ai-lab-audit (aliases="lab").
# - MD-07: resolution returns {model, pass_param}; inherit -> pass_param
#   false, the Agent tool parameter is not passed.
# - MD-08: presets as data (PRESETS: the four ai-lab profiles of
#   ai-lab:139-142, plus economy / quality, decision X10). Writing merges:
#   missing keys completed, existing keys preserved (explicit choices with
#   --set excepted).
# - X1: generated agent files carry "model: <value>" in a delimited,
#   regenerable block of their YAML frontmatter (MODEL_BLOCK).

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import blocks, profiles

ALIASES = ("inherit", "opus", "fable", "sonnet", "haiku")
FULL_ID = re.compile(r"^claude-[a-z0-9][a-z0-9.\-\[\]]*$")

FACTORY_ROLES = ("factory.planner", "factory.builder", "factory.verifier")
SHIPPR_ROLES = ("shippr.reviewer", "shippr.validator")
AUDIT_ROLES = ("audit.auditor", "audit.validator")
LAB_ROLES = ("lab.director.cadrage", "lab.director.interpretation", "lab.neuro", "lab.math",
             "lab.builder", "lab.verifier", "lab.validator")
# SC-04: the scout is cheap by default (the one exception to MD-02).
HARNESS_ROLES = ("harness.scout",)
DEFAULTS = {"harness.scout": "haiku"}
_BASE_ROLES = FACTORY_ROLES + SHIPPR_ROLES + AUDIT_ROLES + LAB_ROLES + HARNESS_ROLES
# PF-05: the closed role set is the core roles plus the roles the profiles declare.
ROLES = _BASE_ROLES + tuple(r for r in profiles.model_roles() if r not in _BASE_ROLES)
ROLE_GROUPS = {"factory": FACTORY_ROLES, "shippr": SHIPPR_ROLES, "audit": AUDIT_ROLES, "lab": LAB_ROLES}
ALIAS_TABLES = {"lab": {r[len("lab."):]: r for r in LAB_ROLES}}

MODELS_FILE = Path(".claude") / "models.json"
LEGACY_LAB_FILE = Path(".claude") / "lab-models.json"

_LAB_ALL_INHERIT = {r: "inherit" for r in LAB_ROLES}
PRESETS: Dict[str, Optional[Dict[str, str]]] = {
    # ai-lab:139-142 ("Sur-mesure" = one question per role, no fixed data).
    "lab-inherit": dict(_LAB_ALL_INHERIT),
    "lab-experts": dict(_LAB_ALL_INHERIT, **{"lab.neuro": "fable", "lab.math": "fable"}),
    "lab-interpretation": dict(_LAB_ALL_INHERIT, **{"lab.math": "fable", "lab.director.interpretation": "fable"}),
    "lab-custom": None,
    # X10: generic presets for the factory and the audits.
    "economy": {"factory.planner": "sonnet", "factory.builder": "sonnet", "factory.verifier": "sonnet",
                "audit.auditor": "sonnet", "audit.validator": "haiku"},
    "quality": {"factory.planner": "opus", "factory.builder": "opus", "factory.verifier": "opus",
                "audit.auditor": "opus", "audit.validator": "sonnet"},
}

MODEL_BLOCK = "harness.model"
_FRONTMATTER = re.compile(r"\A(---[ \t]*\r?\n)(.*?)(^---[ \t]*(?:\r?\n|\Z))", re.S | re.M)
_MODEL_LINE = re.compile(r"^model[ \t]*:", re.M)


class ModelsError(ValueError):
    """Invalid model assignment."""


def check_model(value: str) -> str:
    if value in ALIASES or FULL_ID.match(value):
        return value
    raise ModelsError("unknown model: %r (expected: %s or a claude-* identifier)" % (value, ", ".join(ALIASES)))


def roles_of(spec: Optional[str]) -> Tuple[str, ...]:
    """Roles given as 'factory', 'shippr', ... or as a comma-separated list."""
    if not spec:
        return ROLES
    result: List[str] = []
    for item in (s.strip() for s in spec.split(",")):
        if not item:
            continue
        for role in ROLE_GROUPS.get(item, (item,)):
            if role not in ROLES:
                raise ModelsError("unknown role: %r (known roles: %s)" % (role, ", ".join(ROLES)))
            if role not in result:
                result.append(role)
    return tuple(result)


def _check_role(role: str, allowed: Iterable[str]) -> str:
    if role not in set(allowed):
        raise ModelsError("unknown role: %r (expected one of: %s)" % (role, ", ".join(allowed)))
    return role


def _read_mapping(path: Path, prefix: str = "") -> Dict[str, str]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ModelsError("%s is not valid JSON: %s" % (path, exc))
    if not isinstance(data, dict):
        raise ModelsError("%s must contain a role -> model object" % path)
    result: Dict[str, str] = {}
    for role, model in data.items():
        if not isinstance(model, str):
            raise ModelsError("%s: non-text model for role %r" % (path, role))
        try:
            result[_check_role(prefix + role, ROLES)] = check_model(model)
        except ModelsError as exc:
            raise ModelsError("%s: %s" % (path, exc))
    return result


def load(path: Path, roles: Iterable[str] = ROLES) -> Dict[str, str]:
    """MD-01 to MD-03: file assignment for `roles` (missing -> inherit)."""
    roles = tuple(roles)
    result = {r: DEFAULTS.get(r, "inherit") for r in roles}
    path = Path(path)
    if path.exists():
        data = _read_mapping(path)
        result.update({r: m for r, m in data.items() if r in roles})
    return result


def parse_override(value: str, roles: Iterable[str] = ROLES, aliases: Optional[str] = None) -> Dict[str, str]:
    """MD-04, MD-06: --models "role=model;..." for this cycle only."""
    roles = tuple(roles)
    table = ALIAS_TABLES.get(aliases or "", {})
    if aliases and aliases not in ALIAS_TABLES:
        raise ModelsError("unknown alias table: %r" % (aliases,))
    result: Dict[str, str] = {}
    for pair in value.split(";"):
        if not pair.strip():
            continue
        if "=" not in pair:
            raise ModelsError("--models pair without '=': %r" % pair)
        role, model = (p.strip() for p in pair.split("=", 1))
        role = _check_role(table.get(role, role), roles)
        if role in result:
            raise ModelsError("--models gives role %r twice" % role)
        result[role] = check_model(model)
    if not result:
        raise ModelsError("--models is empty")
    return result


def split_arguments(arguments: str) -> Tuple[str, Optional[str]]:
    """Removes --models "..." from $ARGUMENTS; returns (rest, value or None)."""
    tokens = shlex.split(arguments or "", posix=True)
    rest, value, i = [], None, 0
    while i < len(tokens):
        if tokens[i] == "--models":
            if i + 1 >= len(tokens):
                raise ModelsError("--models expects a value")
            value, i = tokens[i + 1], i + 2
            continue
        rest.append(tokens[i])
        i += 1
    return " ".join(rest), value


# X6: an option is "--models" followed by a value of the form role=model[;...]
# (quoted or bare), at the head or at the tail of $ARGUMENTS. Anywhere else, or
# with a value of another form, "--models" is part of the need, word for word.
_SP = r"[ \t]*"
_BARE_PAIR = r"[A-Za-z][A-Za-z0-9_.\-]*=[^\s;=\"']+"
_QUOTED_PAIR = r"[A-Za-z][A-Za-z0-9_.\-]*" + _SP + "=" + _SP + r"[^\s;=\"']+"


def _pairs(pair: str, sep: str) -> str:
    return pair + "(?:" + sep + ";" + sep + pair + ")*" + sep + ";?"


_VALUE = ("(?P<value>"
          + '"' + _SP + _pairs(_QUOTED_PAIR, _SP) + _SP + '"'
          + "|'" + _SP + _pairs(_QUOTED_PAIR, _SP) + _SP + "'"
          + "|" + _pairs(_BARE_PAIR, "") + ")")
_OPTION = r"--models(?:" + _SP + "=" + _SP + r"|[ \t]+)" + _VALUE
_HEAD = re.compile(r"\A\s*" + _OPTION + r"(?=\s|\Z)")
_TAIL = re.compile(r"(?:(?<=\s)|\A)" + _OPTION + r"\s*\Z")


def extract_option(arguments: str) -> Tuple[str, Optional[str]]:
    """X6: removes '--models <role=model;...>' from the head or the tail of free
    text, leaving the rest verbatim (a need may hold apostrophes or quotes that
    a shell split would refuse, or merely mention --models)."""
    text = arguments or ""
    head, tail = _HEAD.match(text), _TAIL.search(text)
    if head and tail and tail.start() >= head.end():
        raise ModelsError("--models given twice")
    match = head or tail
    if match is None:
        return text.strip(), None
    value = match.group("value")
    if value[:1] in "\"'":
        value = value[1:-1]
    rest = (text[: match.start()] + text[match.end():]).strip()
    return rest, value.strip()


def migrate(root: Path) -> Dict[str, Any]:
    """MD-05: one-time, non-destructive migration of lab-models.json."""
    root = Path(root)
    target, legacy = root / MODELS_FILE, root / LEGACY_LAB_FILE
    if not legacy.exists():
        return {"migrated": False, "warnings": []}
    if target.exists():
        return {"migrated": False, "warnings": [
            "%s ignored: %s exists and takes precedence" % (LEGACY_LAB_FILE.as_posix(), MODELS_FILE.as_posix())]}
    data = _read_mapping(legacy, prefix="lab.")
    _write_json(target, data)
    return {"migrated": True, "warnings": [
        "%s migrated to %s (lab.* keys); the old file is left unchanged"
        % (LEGACY_LAB_FILE.as_posix(), MODELS_FILE.as_posix())]}


def _write_json(path: Path, data: Dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def entry(model: str) -> Dict[str, Any]:
    """MD-07: what the skill passes to the Agent tool."""
    return {"model": model, "pass_param": model != "inherit"}


def resolve_root(root: Path, roles: Iterable[str], override: Optional[str] = None,
                 aliases: Optional[str] = None) -> Dict[str, Any]:
    """Effective assignment of a cycle: migration, file, then --models."""
    roles = tuple(roles)
    migration = migrate(root)
    assignment = load(Path(root) / MODELS_FILE, roles)
    if override:
        assignment.update(parse_override(override, roles, aliases))
    return {"roles": {r: entry(m) for r, m in assignment.items()},
            "migrated": migration["migrated"], "warnings": migration["warnings"]}


def resolve_readonly(root: Path, roles: Iterable[str], override: Optional[str] = None,
                     aliases: Optional[str] = None) -> Dict[str, Any]:
    """Effective assignment without writing anything (X5: an audit writes only
    its allowed paths): models.json if present, otherwise lab-models.json read
    in memory with the lab. prefix (MD-05 is not run), then --models."""
    roles = tuple(roles)
    root = Path(root)
    target, legacy = root / MODELS_FILE, root / LEGACY_LAB_FILE
    warnings: List[str] = []
    assignment = {r: DEFAULTS.get(r, "inherit") for r in roles}
    if target.exists():
        assignment = load(target, roles)
        if legacy.exists():
            warnings.append("%s ignored: %s exists and takes precedence"
                            % (LEGACY_LAB_FILE.as_posix(), MODELS_FILE.as_posix()))
    elif legacy.exists():
        data = _read_mapping(legacy, prefix="lab.")
        assignment.update({r: m for r, m in data.items() if r in roles})
        warnings.append("%s read in memory (not migrated: this command writes no models file)"
                        % LEGACY_LAB_FILE.as_posix())
    if override:
        assignment.update(parse_override(override, roles, aliases))
    return {"roles": {r: entry(m) for r, m in assignment.items()}, "migrated": False, "warnings": warnings}


def resolve(path: Path, override: Optional[str], roles: Iterable[str] = ROLES) -> Dict[str, str]:
    """Effective assignment (role -> model): file, then --models for this cycle."""
    roles = tuple(roles)
    result = load(path, roles)
    if override:
        result.update(parse_override(override, roles))
    return result


def write_preset(root: Path, name: Optional[str], explicit: Optional[str] = None) -> Dict[str, Any]:
    """MD-08: merges a preset into models.json (missing keys completed,
    existing keys kept) then applies the explicit choices of --set."""
    if name is not None and name not in PRESETS:
        raise ModelsError("unknown preset: %r (expected one of: %s)" % (name, ", ".join(PRESETS)))
    migrate(root)
    path = Path(root) / MODELS_FILE
    current = _read_mapping(path) if path.exists() else {}
    merged = dict(current)
    added: List[str] = []
    for role, model in (PRESETS.get(name or "") or {}).items():
        if role not in merged:
            merged[role] = model
            added.append(role)
    chosen = parse_override(explicit) if explicit else {}
    merged.update(chosen)
    ordered = {r: merged[r] for r in ROLES if r in merged}
    changed = ordered != current or not path.exists()
    if changed:
        _write_json(path, ordered)
    return {"file": MODELS_FILE.as_posix(), "changed": changed, "added": added,
            "set": sorted(chosen), "models": ordered}


# ------------------------------------------------------------ model block (X1)

def _frontmatter(text: str) -> Optional[re.Match]:
    return _FRONTMATTER.match(text)


frontmatter = _frontmatter


def model_in_block(text: str) -> Optional[str]:
    """Model value of the harness block of a frontmatter, or None."""
    match = _frontmatter(text)
    if not match:
        return None
    inner = blocks.get_block(match.group(2), MODEL_BLOCK, "hash")
    if inner is None:
        return None
    found = re.search(r"^model[ \t]*:[ \t]*(\S+)", inner, re.M)
    return found.group(1) if found else None


def has_block(text: str) -> bool:
    match = _frontmatter(text)
    return bool(match) and blocks.get_block(match.group(2), MODEL_BLOCK, "hash") is not None


def with_model_block(text: str, model: str) -> str:
    """Inserts or regenerates the model block at the end of the frontmatter."""
    check_model(model)
    match = _frontmatter(text)
    if not match:
        raise ModelsError("no YAML frontmatter (--- ... ---) to hold the model block")
    head, inner, close = match.group(1), match.group(2), match.group(3)
    eol = "\r\n" if head.endswith("\r\n") else "\n"
    outside = blocks.remove_block(inner, MODEL_BLOCK, "hash")
    if _MODEL_LINE.search(outside):
        raise ModelsError("the frontmatter already sets model: outside the %s block" % MODEL_BLOCK)
    start_m, end_m = blocks.markers(MODEL_BLOCK, "hash")
    block = eol.join((start_m, "model: %s" % model, end_m))
    if blocks.get_block(inner, MODEL_BLOCK, "hash") is not None:
        start, end = inner.find(start_m), inner.find(end_m) + len(end_m)
        new_inner = inner[:start] + block + inner[end:]
    else:
        if inner and not inner.endswith("\n"):
            inner += eol
        new_inner = inner + block + eol
    return head + new_inner + close + text[match.end():]


def without_model_block(text: str) -> str:
    match = _frontmatter(text)
    if not match or blocks.get_block(match.group(2), MODEL_BLOCK, "hash") is None:
        return text
    inner = blocks.remove_block(match.group(2), MODEL_BLOCK, "hash")
    return match.group(1) + inner + match.group(3) + text[match.end():]
