# Profiles: data folders that carry everything specific to a kind of project.
#
# - PF-01: a profile is a folder profiles/<name>/ with a strict JSON manifest
#   (profile.json) and Markdown fragments referenced by relative path.
#   No .py file in a profile.
# - PF-02: manifest keys, documented in profiles/SCHEMA.md.
# - PF-03: `validate` lists every error (missing key, missing fragment, unknown
#   loop phase kind, gate id outside the profile namespaces, lens or model role
#   already declared by another profile, two fallback profiles).
# - PF-04: `list` renders the profiles sorted by name, with lenses and roles.
# - PF-05: the former closed sets are "core U profiles": model roles, gates,
#   journal names, forbidden status strings, deny rules, tasks, .gitignore
#   lines are read from the profiles found next to the core.
# - HJ-06: cumulated profiles merge by union (roles, lenses, deny, tasks).
# - RD-03: `params[]` of a manifest ({name, source, required}) is validated by PF-03.
# - AD-03, AD-04, AD-09: lens selection, waves of auditors and caps.

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

CORE_DIR = Path(__file__).resolve().parent.parent
PROFILES_DIR = CORE_DIR / "profiles"
MANIFEST = "profile.json"

# LP-02: phase kinds known to the loop engine (hcore/loop.py).
PHASE_KINDS = ("plan", "consult", "consolidate", "gate", "build", "verify", "interpret", "record")
REQUIRED_KEYS = ("name", "schema_version", "fallback", "fragments", "roles", "model_roles", "model_presets",
                 "loop", "artefacts", "verifier", "deterministic_checks", "settings", "gitignore", "tasks",
                 "ci", "orchestrator", "gates", "journal_names", "lens_sets", "lenses", "detection",
                 "audit_sections")
LENS_KEYS = ("name", "prefix", "set", "fragment", "debt", "shape", "ordering", "axis", "axis_rank",
             "unauditable", "no_run", "no_protocol", "allowed_exec", "journal_check", "nature", "size_scale",
             "critical_definition", "subagent_type", "model_role", "stack_agnostic", "dedup_key",
             "evidence_kinds", "receives", "validator_fields", "deliverable")
CRITERION_KINDS = ("glob", "dir", "dependency", "content", "judgement")
MAX_WAVE = 5


class ProfileError(ValueError):
    """Invalid profile or profile selection; `errors` lists every problem."""

    def __init__(self, errors: Sequence[str]):
        self.errors = list(errors)
        ValueError.__init__(self, "; ".join(self.errors))


def _read_manifest(folder: Path) -> Dict[str, Any]:
    path = Path(folder) / MANIFEST
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ProfileError(["%s: not readable strict JSON: %s" % (path.as_posix(), exc)])
    if not isinstance(data, dict):
        raise ProfileError(["%s: must contain a JSON object" % path.as_posix()])
    return data


def load(folder: Path) -> Dict[str, Any]:
    """Manifest of a profile folder, without validation beyond JSON."""
    data = _read_manifest(Path(folder))
    data["_dir"] = Path(folder)
    return data


def _fragment_paths(data: Dict[str, Any]) -> List[str]:
    paths: List[str] = []
    if isinstance(data.get("fragments"), dict):
        paths += [v for v in data["fragments"].values() if isinstance(v, str)]
    for role in data.get("roles") or []:
        if isinstance(role, dict) and isinstance(role.get("mission"), str):
            paths.append(role["mission"])
        if isinstance(role, dict) and isinstance(role.get("body"), str):
            paths.append(role["body"])
    for lens in data.get("lenses") or []:
        if isinstance(lens, dict):
            for key in ("fragment", "critical_definition"):
                if isinstance(lens.get(key), str):
                    paths.append(lens[key])
    for lset in data.get("lens_sets") or []:
        if isinstance(lset, dict):
            paths += [lset[k] for k in ("fragment", "map_fragment", "intro_fragment", "validator_fragment")
                      if isinstance(lset.get(k), str)]
    return paths


def validate_manifest(data: Dict[str, Any], folder: Path) -> List[str]:
    """PF-03 errors of one profile taken alone."""
    folder = Path(folder)
    errors: List[str] = []
    for key in REQUIRED_KEYS:
        if key not in data:
            errors.append("missing required key: %s" % key)
    if errors:
        return errors
    if data["name"] != folder.name:
        errors.append("name %r differs from the folder name %r" % (data["name"], folder.name))
    for py in folder.rglob("*.py"):
        errors.append("a profile holds no code: %s" % py.relative_to(folder).as_posix())
    for rel in _fragment_paths(data):
        if not (folder / rel).is_file():
            errors.append("fragment not found: %s" % rel)
    for index, phase in enumerate(data["loop"].get("phases", []) if isinstance(data["loop"], dict) else []):
        kind = phase.get("kind") if isinstance(phase, dict) else None
        if kind not in PHASE_KINDS:
            errors.append("loop phase %d: unknown phase kind %r (known: %s)" % (index, kind, ", ".join(PHASE_KINDS)))
    orch = data["orchestrator"].get("name") if isinstance(data["orchestrator"], dict) else None
    allowed = ["%s." % orch] if orch else []
    allowed += ["%s." % ns for ns in data.get("gate_namespaces", [])]
    allowed.append("audit.%s." % data["name"])
    for gate in data["gates"]:
        gid = gate.get("id") if isinstance(gate, dict) else None
        if not isinstance(gid, str) or not any(gid.startswith(p) for p in allowed):
            errors.append("gate id %r is outside the namespaces of the profile (%s)"
                          % (gid, ", ".join(allowed)))
    lens_names = [l.get("name") for l in data["lenses"] if isinstance(l, dict)]
    for lens in data["lenses"]:
        missing = [k for k in LENS_KEYS if k not in lens]
        if missing:
            errors.append("lens %r: missing keys: %s" % (lens.get("name"), ", ".join(missing)))
    for lset in data["lens_sets"]:
        for name in lset.get("lenses", []):
            if name not in lens_names:
                errors.append("lens set %r: unknown lens %r" % (lset.get("name"), name))
    criteria = data["detection"].get("criteria") if isinstance(data["detection"], dict) else None
    for crit in criteria or []:
        if crit.get("kind") not in CRITERION_KINDS:
            errors.append("detection criterion %r: unknown kind %r" % (crit.get("id"), crit.get("kind")))
    for spec in data.get("params", []):       # RD-03
        if not (isinstance(spec, dict) and isinstance(spec.get("name"), str)
                and spec.get("source") in ("detected", "judgement") and isinstance(spec.get("required"), bool)):
            errors.append("params: each entry is {name, source: detected|judgement, required: bool}: %r" % (spec,))
    agent_files = [r.get("agent_file") for r in data["roles"]]
    if len(set(agent_files)) != len(agent_files):
        errors.append("two roles share an agent file")
    return errors


def _siblings(folder: Path) -> List[Path]:
    parent = Path(folder).parent
    if not parent.is_dir():
        return []
    return sorted(p for p in parent.iterdir() if p.is_dir() and (p / MANIFEST).is_file() and p != Path(folder))


def cross_errors(profiles: Sequence[Dict[str, Any]]) -> List[str]:
    """PF-03 errors between profiles: duplicate names, fallback, agent files."""
    errors: List[str] = []
    seen: Dict[str, Dict[str, str]] = {"lens": {}, "model role": {}, "agent file": {}}
    fallbacks = [p["name"] for p in profiles if p.get("fallback") is True]
    if len(fallbacks) > 1:
        errors.append("two profiles are fallback: %s" % ", ".join(fallbacks))
    for prof in profiles:
        items = (("lens", [l.get("name") for l in prof.get("lenses", [])]),
                 ("model role", list(prof.get("model_roles", []))),
                 ("agent file", [r.get("agent_file") for r in prof.get("roles", [])]))
        for what, names in items:
            for name in names:
                owner = seen[what].get(name)
                if owner and owner != prof["name"]:
                    errors.append("%s %r is declared by %s and %s" % (what, name, owner, prof["name"]))
                seen[what].setdefault(name, prof["name"])
    return errors


def validate(path: Path) -> List[str]:
    """PF-03 for a profile folder (compared with its sibling profiles), or for a
    folder that contains profile folders (every one, with cross checks)."""
    path = Path(path)
    if not path.is_dir():
        return ["not a folder: %s" % path.as_posix()]
    if (path / MANIFEST).is_file():
        folders = [path] + _siblings(path)
        target = path
    else:
        folders = sorted(p for p in path.iterdir() if p.is_dir() and (p / MANIFEST).is_file())
        target = None
        if not folders:
            return ["no profile found in %s" % path.as_posix()]
    errors: List[str] = []
    loaded: List[Dict[str, Any]] = []
    for folder in folders:
        try:
            data = load(folder)
        except ProfileError as exc:
            if folder == target or target is None:
                errors += exc.errors
            continue
        found = validate_manifest(data, folder)
        if folder == target or target is None:
            errors += ["%s: %s" % (folder.name, e) for e in found]
        if not found:
            loaded.append(data)
    errors += cross_errors(loaded)
    return errors


def discover(profiles_dir: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """Every valid-JSON profile of a folder, sorted by name."""
    base = Path(profiles_dir) if profiles_dir else PROFILES_DIR
    result: Dict[str, Dict[str, Any]] = {}
    if base.is_dir():
        for folder in sorted(p for p in base.iterdir() if p.is_dir() and (p / MANIFEST).is_file()):
            data = load(folder)
            result[data.get("name", folder.name)] = data
    return result


def summary(profiles_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """PF-04."""
    return [{"name": name, "fallback": p.get("fallback", False),
             "lenses": [l["name"] for l in p.get("lenses", [])],
             "roles": [r["name"] for r in p.get("roles", [])],
             "lens_sets": [s["name"] for s in p.get("lens_sets", [])]}
            for name, p in discover(profiles_dir).items()]


def get(name: str, profiles_dir: Optional[Path] = None) -> Dict[str, Any]:
    found = discover(profiles_dir)
    if name not in found:
        raise ProfileError(["unknown profile: %r (known: %s)" % (name, ", ".join(found))])
    return found[name]


def fragment(prof: Dict[str, Any], rel: str) -> str:
    return (Path(prof["_dir"]) / rel).read_text(encoding="utf-8")


def fallback_name(profiles_dir: Optional[Path] = None) -> Optional[str]:
    for name, prof in discover(profiles_dir).items():
        if prof.get("fallback") is True:
            return name
    return None


# ----------------------------------------------------- PF-05: core U profiles

def journal_names(profiles_dir: Optional[Path] = None) -> List[str]:
    names: List[str] = []
    for prof in discover(profiles_dir).values():
        names += [n for n in prof.get("journal_names", []) if n not in names]
        orch = prof.get("orchestrator", {}).get("name")
        if orch and orch not in names:
            names.append(orch)
    return names


def model_roles(profiles_dir: Optional[Path] = None) -> List[str]:
    roles: List[str] = []
    for prof in discover(profiles_dir).values():
        roles += [r for r in prof.get("model_roles", []) if r not in roles]
    return roles


def model_presets(profiles_dir: Optional[Path] = None) -> Dict[str, Any]:
    presets: Dict[str, Any] = {}
    for prof in discover(profiles_dir).values():
        presets.update(prof.get("model_presets", {}))
    return presets


def ready_statuses(profiles_dir: Optional[Path] = None) -> List[str]:
    """Status lines that gate a build (FD-11 forbidden strings are built from them)."""
    out: List[str] = []
    for prof in discover(profiles_dir).values():
        for art in prof.get("artefacts", []):
            for line in ready_lines(art):
                if line not in out:
                    out.append(line)
    return out


def ready_lines(art: Dict[str, Any]) -> List[str]:
    """The ready status line of an artefact, then its legacy (French) one when declared."""
    lines = [art["status_ready"]] if art.get("status_ready") else []
    legacy = (art.get("status_legacy") or {}).get("ready")
    if legacy:
        lines.append(legacy)
    return lines


def role_names(profiles_dir: Optional[Path] = None, read_only: Optional[bool] = None) -> List[str]:
    names: List[str] = []
    for prof in discover(profiles_dir).values():
        for role in prof.get("roles", []):
            if read_only is None or role.get("read_only") is read_only:
                names.append(role["name"])
    return names


def deny_rules(prof: Dict[str, Any], stacks: Iterable[str], root: Optional[Path] = None) -> List[str]:
    """SF-01 / AL-01 as data: stack rules, generic rules, then what is present."""
    settings = prof["settings"]
    rules: List[str] = []
    for stack in stacks:
        for r in settings.get("deny_by_stack", {}).get(stack, []):
            if r not in rules:
                rules.append(r)
    for r in settings.get("deny_generic", []):
        if r not in rules:
            rules.append(r)
    present = settings.get("deny_if_present") or {}
    if root is not None and (present.get("dirs") or present.get("suffixes") or present.get("cache")):
        for r in _present_rules(Path(root), present):
            if r not in rules:
                rules.append(r)
    return rules


_SKIP_WALK = {".git", ".venv", "venv", "node_modules", "__pycache__", "lab-logs", "factory-logs"}


def _present_rules(root: Path, present: Dict[str, Any]) -> List[str]:
    rules = ["Read(./%s/**)" % d for d in present.get("dirs", []) if (root / d).is_dir()]
    suffixes, cache, seen = set(), False, 0
    for _current, dirs, files in os.walk(str(root)):
        dirs[:] = [d for d in dirs if d not in _SKIP_WALK]
        cache = cache or ".cache" in dirs
        suffixes.update(os.path.splitext(f)[1] for f in files)
        seen += len(files)
        if seen > 20000:
            break
    rules += ["Read(./**/*%s)" % s for s in present.get("suffixes", []) if s in suffixes]
    if present.get("cache") and cache:
        rules.append("Read(./**/.cache/**)")
    return rules


def tasks_of(prof: Dict[str, Any], test_command: Optional[str] = None,
             test_windows: Optional[str] = None) -> List[Dict[str, Any]]:
    tasks = [dict(t) for t in prof["tasks"].get("tasks", [])]
    spec = prof["tasks"].get("test_task")
    if spec and test_command:
        test: Dict[str, Any] = {"label": spec["label"], "type": "shell", "command": test_command,
                                "problemMatcher": []}
        if test_windows and test_windows != test_command:
            test["windows"] = {"command": test_windows}
        index = next((i for i, t in enumerate(tasks) if t["label"] == spec.get("before")), len(tasks))
        tasks.insert(index, test)
    return tasks


def merged(selected: Sequence[Dict[str, Any]], stacks: Iterable[str], root: Optional[Path] = None,
           test_command: Optional[str] = None, test_windows: Optional[str] = None) -> Dict[str, Any]:
    """HJ-06: union of deny rules, tasks, inputs and .gitignore lines of the profiles."""
    deny: List[str] = []
    gitignore: List[str] = []
    inputs: List[Dict[str, Any]] = []
    tasks: List[Dict[str, Any]] = []
    for prof in selected:
        deny += [r for r in deny_rules(prof, stacks, root) if r not in deny]
        gitignore += [g for g in prof.get("gitignore", []) if g not in gitignore]
        inputs += [i for i in prof["tasks"].get("inputs", []) if i["id"] not in [x["id"] for x in inputs]]
        tasks += [t for t in tasks_of(prof, test_command, test_windows) if t["label"] not in [x["label"] for x in tasks]]
    return {"deny": deny, "gitignore": gitignore, "inputs": inputs, "tasks": tasks}


# ------------------------------------------- loop machines and derived data (PF-05)

def machine_of(prof: Dict[str, Any]) -> str:
    """LP-01: 'lab' for a loop with consultation or interpretation phases, else 'linear'."""
    kinds = {p.get("kind") for p in prof["loop"].get("phases", [])}
    return "lab" if kinds & {"consult", "interpret"} else "linear"


def by_machine(machine: str, profiles_dir: Optional[Path] = None) -> Dict[str, Any]:
    # The engines of factory.py and labrun.py are driven by a complete loop (plan, build, verify): a
    # profile with a shorter loop (a toy, a read-only profile) never takes their place.
    candidates = [p for p in discover(profiles_dir).values()
                  if machine_of(p) == machine and {"plan", "build", "verify"} <= set(loop_agents(p))]
    if candidates:
        return sorted(candidates, key=lambda p: (not p.get("fallback"), p["name"]))[0]
    raise ProfileError(["no profile with a %s loop" % machine])


def loop_agents(prof: Dict[str, Any]) -> Dict[str, Any]:
    """Agents named by the phases of a loop: plan, build, verify, consult[], interpret[]."""
    out: Dict[str, Any] = {"consult": [], "interpret": []}
    for phase in prof["loop"]["phases"]:
        kind = phase["kind"]
        if kind in ("plan", "build", "verify"):
            out[kind] = phase["agent"]
        elif kind in ("consult", "interpret"):
            out[kind] = list(phase["agents"])
    return out


def ready_status(prof: Dict[str, Any]) -> Optional[str]:
    return next((a["status_ready"] for a in prof["artefacts"] if a.get("status_ready")), None)


def ready_status_lines(prof: Dict[str, Any]) -> List[str]:
    """Every accepted spelling (English, then legacy) of the ready status line of a profile."""
    return next((ready_lines(a) for a in prof["artefacts"] if a.get("status_ready")), [])


def files_of(prof: Dict[str, Any]) -> List[str]:
    """Files generated for a profile: its agents, then its orchestrator skill."""
    return [r["agent_file"] for r in prof["roles"]] + [".claude/skills/%s/SKILL.md" % prof["orchestrator"]["name"]]


def lens_set_of(prof: Dict[str, Any], set_name: str) -> Tuple[List[str], List[Dict[str, Any]]]:
    """(units, lenses) of a lens set: a lens belongs to the unit of its axis, else to its own."""
    lenses = [l for l in prof["lenses"] if l["set"] == set_name]
    units: List[str] = []
    for l in lenses:
        unit = l.get("axis") or l["name"]
        if unit not in units:
            units.append(unit)
    return units, lenses


def find_set(set_name: str, profiles_dir: Optional[Path] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    for prof in discover(profiles_dir).values():
        for lset in prof["lens_sets"]:
            if lset["name"] == set_name:
                return prof, lset
    raise ProfileError(["no profile declares the lens set %r" % set_name])


def verifier_formats(profiles_dir: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """Verifier agents by name: {agent: {source, kinds}} (FD-05, LR-09)."""
    out: Dict[str, Dict[str, Any]] = {}
    for prof in discover(profiles_dir).values():
        agent = loop_agents(prof).get("verify")
        if agent:
            out[agent] = {"source": prof["orchestrator"]["name"], "kinds": list(prof["verifier"]["issue_kinds"])}
    return out


# --------------------------------------------------- AD-03, AD-04, AD-09

class SelectionError(ValueError):
    """Invalid lens, profile or cap selection (exit 2)."""


def split_list(value: Optional[str]) -> List[str]:
    if value is None:
        return []
    items = [v.strip() for v in value.split(";")]
    if any(not v for v in items) or len(set(items)) != len(items):
        raise SelectionError("invalid list %r: empty or repeated value" % (value,))
    return items


def active_lenses(selected: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Lenses in the order profile -> set -> lens (AD-04)."""
    out: List[Dict[str, Any]] = []
    for prof in selected:
        by_name = {l["name"]: l for l in prof["lenses"]}
        for lset in prof["lens_sets"]:
            for name in lset["lenses"]:
                out.append(dict(by_name[name], profile=prof["name"]))
    return out


def select_lenses(selected: Sequence[Dict[str, Any]], lenses_arg: Optional[str],
                  all_profiles: Optional[Dict[str, Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """AD-03: default = union of the active lenses; --lenses takes lens or axis
    names (an axis expands to its lenses); a lens of an inactive profile or an
    unknown one is an error."""
    active = active_lenses(selected)
    if lenses_arg is None:
        return active
    known = {l["name"]: l for l in active}
    axes: Dict[str, List[str]] = {}
    for l in active:
        if l.get("axis"):
            axes.setdefault(l["axis"], []).append(l["name"])
    inactive = set()
    for prof in (all_profiles or discover()).values():
        for l in prof["lenses"]:
            inactive.add(l["name"])
            if l.get("axis"):
                inactive.add(l["axis"])
    wanted: List[str] = []
    for item in split_list(lenses_arg):
        if item in known:
            names = [item]
        elif item in axes:
            names = axes[item]
        elif item in inactive:
            raise SelectionError("lens or axis %r belongs to a profile that is not active" % item)
        else:
            raise SelectionError("unknown lens or axis: %r" % item)
        wanted += [n for n in names if n not in wanted]
    return [l for l in active if l["name"] in wanted]


def waves(lenses: Sequence[Dict[str, Any]], size: int = MAX_WAVE) -> List[List[str]]:
    """AD-04: one auditor per lens, successive waves of `size` at most."""
    names = [l["name"] for l in lenses]
    return [names[i:i + size] for i in range(0, len(names), size)]


def caps(selected: Sequence[Dict[str, Any]], options: Dict[str, Optional[str]]) -> Dict[str, Any]:
    """AD-09: caps min(N, cap) per lens set; an option whose set is inactive is an error."""
    sets = {}
    for prof in selected:
        for lset in prof["lens_sets"]:
            sets[lset["cap_option"]] = lset
    result: Dict[str, Any] = {}
    for option, raw in options.items():
        if raw is None:
            continue
        if option not in sets:
            raise SelectionError("%s: the lens set it applies to is not active" % option)
    for option, lset in sets.items():
        raw = options.get(option)
        if raw is None:
            value = lset["cap_default"]
        else:
            if not re.fullmatch(r"\d+", str(raw)) or int(raw) < 1:
                raise SelectionError("%s expects an integer >= 1, got %r" % (option, raw))
            value = min(int(raw), lset["cap"])
        result[option] = {"set": lset["name"], "cap": value, "max": lset["cap"]}
    return result
