# Deterministic rules of the code-index skill (CX-01 to CX-16).
#
# - CX-01: arguments: --reindex, --no-wire; any other content is handed back
#   untouched, never interpreted.
# - CX-02: the ripgrep install command is picked from the package managers
#   found on the machine; without any, the local fallback (rg_path in
#   config.local.json) is returned.
# - CX-03: the interpreter is the one of hcore/interp.py (a command resolved
#   through the PATH, never a venv, floor 3.9 = X9); here only its use.
# - CX-04: survey of the repository: git, factory, lab, earlier install,
#   stack markers and bulky folders.
# - CX-05: copy of rgkb.py: absent -> created; identical -> nothing; asset
#   TOOL_VERSION >= project -> overwritten and announced; project > -> kept.
# - CX-06: .mcp.json: mcpServers.rgkb added if absent, "-3" first for py,
#   never an absolute path.
# - CX-07: settings.json: enabledMcpjsonServers, 7 read tools and the CLI in
#   allow (never note_add nor reindex), two deny rules.
# - CX-08: config.json: exclude completed with "!<folder>/**", nothing removed.
# - CX-10: a rule excluding .knowledge/ gets the counter-rule "!.knowledge/**"
#   at the end, after a one-line comment, and nothing else.
# - CX-12: rgkb blocks in agents and skills (BK-01), reentrant.
# - CX-13: tools of the agents with a restrictive list completed (FU-05).
# - CX-14: --allowedTools of ci/factory.* and ci/lab.* completed (FU-06).
# - CX-15: tasks.json: input rgkbQuery and five "Index: ..." tasks.
# - CX-16: the state of every file is returned (created, merged, identical,
#   customized-kept).
# CX-09 (init never rewrites config.json) lives in assets/rgkb.py; CX-11 is the
# gate code-index.hooks of hcore/gates.py.

from __future__ import annotations

import fnmatch
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import blocks, fusion, profiles, stack

CREATED, MERGED, IDENTICAL, KEPT = fusion.CREATED, fusion.MERGED, fusion.IDENTICAL, fusion.KEPT
UPDATED = "mis-à-jour"

READ_TOOLS = ["mcp__rgkb__search", "mcp__rgkb__definition", "mcp__rgkb__symbols", "mcp__rgkb__neighbors",
              "mcp__rgkb__file_card", "mcp__rgkb__notes", "mcp__rgkb__status"]
DENY_RULES = ["Read(./.knowledge/index.sqlite)", "Read(./.knowledge/graph.json)"]
RGKB_PATH = ".claude/rgkb/rgkb.py"

# PF-05: the agents and the files of each installation come from the profiles.
_LINEAR, _LAB = profiles.by_machine("linear"), profiles.by_machine("lab")
READONLY_AGENTS = tuple(r["name"] for p in (_LINEAR, _LAB) for r in p["roles"] if r["read_only"])
BUILDER_AGENTS = tuple(r["name"] for p in (_LINEAR, _LAB) for r in p["roles"] if not r["read_only"])
ORCHESTRATORS = ("factory-run", "lab-run")
CI_SCRIPTS = ("ci/factory.sh", "ci/factory.ps1", "ci/lab.sh", "ci/lab.ps1")
FACTORY_FILES = tuple(profiles.files_of(_LINEAR))
LAB_FILES = tuple(profiles.files_of(_LAB))
GITIGNORE_NOTE = "# The knowledge base is shared on purpose (code-index): do not ignore it."
COUNTER_RULE = "!.knowledge/**"

QUERY_INPUT = {"id": "rgkbQuery", "type": "promptString", "description": "Motif ou nom de symbole à chercher"}
TASKS = (("Index: réindexer", "index"), ("Index: état", "status"), ("Index: chercher", 'search "${input:rgkbQuery}"'),
         ("Index: diagnostic", "doctor"), ("Index: hooks git", "hooks status"))


class CxError(ValueError):
    """Invalid call or unusable state (exit 2)."""


# ---------------------------------------------------------------- CX-01

def parse_arguments(text: str) -> Dict[str, Any]:
    tokens = (text or "").split()
    reindex = "--reindex" in tokens
    no_wire = "--no-wire" in tokens
    extra = [t for t in tokens if t not in ("--reindex", "--no-wire")]
    return {"reindex": reindex, "no_wire": no_wire, "extra": " ".join(extra)}


# ---------------------------------------------------------------- CX-02

WINGET = ("winget install --id BurntSushi.ripgrep.MSVC -e --source winget --silent "
          "--accept-package-agreements --accept-source-agreements")
# (system, manager, command), in order of preference; system None = any.
INSTALLERS: Tuple[Tuple[Optional[str], str, str], ...] = (
    ("windows", "winget", WINGET),
    ("windows", "scoop", "scoop install ripgrep"),
    ("windows", "choco", "choco install ripgrep -y"),
    ("macos", "brew", "brew install ripgrep"),
    ("linux", "apt-get", "apt-get install -y ripgrep"),
    ("linux", "dnf", "dnf install -y ripgrep"),
    ("linux", "paru", "paru -S --needed ripgrep"),
    ("linux", "yay", "yay -S --needed ripgrep"),
    ("linux", "pacman", "pacman -S --needed ripgrep"),
    ("linux", "zypper", "zypper install -y ripgrep"),
    ("linux", "apk", "apk add ripgrep"),
    ("linux", "xbps-install", "xbps-install -y ripgrep"),
    ("linux", "nix-env", "nix-env -iA nixpkgs.ripgrep"),
    (None, "cargo", "cargo install ripgrep"),
)


def system_name(platform: Optional[str] = None) -> str:
    p = platform if platform is not None else sys.platform
    return "windows" if p.startswith("win") or p in ("cygwin", "msys") else "macos" if p == "darwin" else "linux"


def rg_install(system: str, available: Iterable[str]) -> Optional[Dict[str, str]]:
    """First install command whose package manager is available, else None
    (then: the local fallback, rg_path in config.local.json)."""
    have = set(available)
    for sysname, manager, command in INSTALLERS:
        if sysname in (None, system) and manager in have:
            return {"manager": manager, "command": command}
    return None


def probe_rg() -> Dict[str, Any]:
    """`rg --version` and, when it fails, the install command for this machine."""
    exe = shutil.which("rg")
    version = None
    if exe:
        try:
            out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10,
                                 stdin=subprocess.DEVNULL)
            if out.returncode == 0 and out.stdout.strip():
                version = out.stdout.strip().splitlines()[0]
        except (OSError, subprocess.TimeoutExpired):
            version = None
    system = system_name()
    managers = sorted({m for _, m, _ in INSTALLERS if shutil.which(m)})
    result: Dict[str, Any] = {"installed": version is not None, "version": version, "system": system}
    if version is None:
        result["install"] = rg_install(system, managers)
        result["fallback"] = {"file": ".knowledge/config.local.json", "key": "rg_path"}
    return result


# ---------------------------------------------------------------- CX-04

def read_text(path: Path) -> Optional[str]:
    return fusion._read_text(path)  # noqa: SLF001 - raw text, line endings intact


def survey(root: Path) -> Dict[str, Any]:
    root = Path(root)
    found = stack.detect(root)
    gitignore = read_text(root / ".gitignore")
    mcp = read_text(root / ".mcp.json")
    return {
        "git": (root / ".git").exists(),
        "gitignore": gitignore is not None,
        "knowledge_excluded": knowledge_excluded(gitignore or ""),
        "factory": [f for f in FACTORY_FILES if (root / f).is_file()],
        "lab": [f for f in LAB_FILES if (root / f).is_file()],
        "ci_scripts": [f for f in CI_SCRIPTS if (root / f).is_file()],
        "previous": {"server": (root / RGKB_PATH).is_file(),
                     "manifest": (root / ".knowledge" / "manifest.json").is_file(),
                     "mcp": bool(mcp and '"rgkb"' in mcp)},
        "markers": found["markers"], "manifests": found["manifests"], "stacks": found["stacks"],
        "heavy_dirs": heavy_dirs(found["artifact_dirs"]),
    }


def heavy_dirs(artifact_dirs: Iterable[str]) -> List[str]:
    """Bulky folders detected (ST-03), minus .git (already out of every index)."""
    return [d for d in artifact_dirs if d.rstrip("/") not in (".git",)]


# ---------------------------------------------------------------- CX-05

_VERSION = re.compile(r'^TOOL_VERSION = "([^"]+)"', re.M)


def tool_version(text: str) -> Optional[Tuple[int, ...]]:
    m = _VERSION.search(text)
    if not m:
        return None
    try:
        return tuple(int(x) for x in m.group(1).split("."))
    except ValueError:
        return None


def install_server(asset: Path, root: Path) -> Dict[str, Any]:
    asset, dest = Path(asset), Path(root) / RGKB_PATH
    wanted = asset.read_bytes()
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(wanted)
        return {"state": CREATED}
    current = dest.read_bytes()
    if current == wanted:
        return {"state": IDENTICAL}
    mine = tool_version(wanted.decode("utf-8", "replace"))
    theirs = tool_version(current.decode("utf-8", "replace"))
    if mine is not None and (theirs is None or mine >= theirs):
        dest.write_bytes(wanted)
        return {"state": UPDATED, "announce": "rgkb.py replaced by the asset version", "from": theirs, "to": mine}
    return {"state": KEPT, "reason": "project version %s is newer than the asset %s" % (theirs, mine)}


# ---------------------------------------------------------- CX-06, CX-07, CX-15

def interpreter(command: Sequence[str]) -> List[str]:
    cmd = [c for c in command if c]
    if not cmd:
        raise CxError("an interpreter command is required (e.g. python, python3, py -3)")
    if "/" in cmd[0] or "\\" in cmd[0] or ":" in cmd[0]:
        raise CxError("the interpreter must be resolved through the PATH, never an absolute path: %r" % cmd[0])
    if cmd[0] == "py" and cmd[1:2] != ["-3"]:
        cmd = ["py", "-3"] + cmd[1:]
    return cmd


def cli_rule(command: Sequence[str]) -> str:
    return "Bash(%s %s *)" % (" ".join(command), RGKB_PATH)


def mcp_ops(command: Sequence[str]) -> List[Any]:
    entry = {"command": command[0], "args": list(command[1:]) + [RGKB_PATH, "mcp"]}
    return [fusion.op_add_key(("mcpServers",), "rgkb", entry)]


def settings_ops(command: Sequence[str]) -> List[Any]:
    return [fusion.op_array_union(("enabledMcpjsonServers",), ["rgkb"]),
            fusion.op_array_union(("permissions", "allow"), READ_TOOLS + [cli_rule(command)]),
            fusion.op_array_union(("permissions", "deny"), DENY_RULES)]


def tasks_ops(command: Sequence[str]) -> List[Any]:
    posix = command[0] if command[0] in ("python", "python3") else "python"
    tasks = []
    for label, sub in TASKS:
        tasks.append({"label": label, "type": "shell",
                      "command": "%s %s %s" % (posix, RGKB_PATH, sub),
                      "windows": {"command": "%s %s %s" % (" ".join(command), RGKB_PATH, sub)},
                      "problemMatcher": []})
    return [fusion.op_tasks([QUERY_INPUT], tasks)]


def register(root: Path, command: Sequence[str]) -> Dict[str, str]:
    """CX-06, CX-07, CX-15: the three files planned, then written (all or nothing)."""
    root, command = Path(root), interpreter(command)
    plans = {
        ".mcp.json": fusion.plan_json(root / ".mcp.json", mcp_ops(command)),
        ".claude/settings.json": fusion.plan_json(root / ".claude" / "settings.json", settings_ops(command)),
        ".vscode/tasks.json": fusion.plan_json(root / ".vscode" / "tasks.json", tasks_ops(command)),
    }
    return fusion.write_plans(root, plans)


# ---------------------------------------------------------------- CX-08, CX-10

def _covered(excludes: Sequence[Any], folder: str) -> bool:
    name = folder.rstrip("/")
    leaf = name.split("/")[-1]
    return any(e in ("!%s/**" % name, "!**/%s/**" % name, "!**/%s/**" % leaf) for e in excludes)


def config_ops(folders: Iterable[str]) -> List[Any]:
    folders = list(folders)
    wanted = ["!%s/**" % f.rstrip("/") for f in folders]

    def run(data: Dict[str, Any]) -> List[str]:
        current = data.get("exclude")
        if current is None:
            return ["exclude"]      # the defaults apply: completing would replace them
        if not isinstance(current, list):
            raise fusion.FusionError("exclude is not an array")
        for rule, folder in zip(wanted, folders):
            if not _covered(current, folder) and rule not in current:
                current.append(rule)
        return []
    return [run]


# Files of .knowledge/ and the generic names a data/database rule may hit.
_IGNORE_FILES = ("index.sqlite", "index.db", "notes.jsonl", "manifest.json", "graph.json", "files.jsonl",
                 "config.json")


def knowledge_excluded(text: str) -> bool:
    """CX-10: a .gitignore rule hits .knowledge/ (directly or through a glob),
    and no later counter-rule gives it back."""
    state = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        rule = line[1:] if negated else line
        rule = rule.lstrip("/")
        hits = rule.rstrip("/") in (".knowledge", "**/.knowledge") or rule in (".knowledge/*", ".knowledge/**")
        if not hits and "/" not in rule.rstrip("/"):
            hits = any(fnmatch.fnmatchcase(f, rule) for f in _IGNORE_FILES) and not rule.endswith("/")
        if hits:
            state = not negated
    return state


def scope(root: Path, folders: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """CX-08 and CX-10: config.json exclusions and the .gitignore counter-rule."""
    root = Path(root)
    config = root / ".knowledge" / "config.json"
    if not config.is_file():
        raise CxError("%s is missing: run 'rgkb.py init' first" % config.as_posix())
    heavy = list(folders) if folders is not None else heavy_dirs(stack.detect(root)["artifact_dirs"])
    plans = {".knowledge/config.json": fusion.plan_json(config, config_ops(heavy))}
    gi = root / ".gitignore"
    text = read_text(gi)
    if text is not None and knowledge_excluded(text):
        plans[".gitignore"] = fusion.plan_lines(gi, [GITIGNORE_NOTE, COUNTER_RULE])
    return {"states": fusion.write_plans(root, plans), "folders": heavy,
            "gitignore_counter_rule": ".gitignore" in plans}


# ---------------------------------------------------------------- CX-12, CX-13, CX-14

def wire_targets(root: Path) -> Dict[str, str]:
    """name -> relative path of every agent and skill present, in 6a, 6b, 6c order."""
    root = Path(root)
    found: Dict[str, str] = {}
    for name in READONLY_AGENTS + BUILDER_AGENTS:
        rel = ".claude/agents/%s.md" % name
        if (root / rel).is_file():
            found[name] = rel
    for name in ORCHESTRATORS:
        rel = ".claude/skills/%s/SKILL.md" % name
        if (root / rel).is_file():
            found[name] = rel
    return found


def wire(root: Path, contents: Dict[str, str], command: Sequence[str]) -> Dict[str, Any]:
    """Blocks (BK-01), tools (FU-05) and CI allow lists (FU-06), planned then
    written. `contents`: block text per target name; a missing one is refused."""
    root, command = Path(root), interpreter(command)
    targets = wire_targets(root)
    missing = [n for n in targets if not (contents.get(n) or "").strip()]
    if missing:
        raise CxError("no block content for: %s" % ", ".join(missing))
    plans: Dict[str, fusion.Plan] = {}
    for name, rel in targets.items():
        def edit(text: str, name: str = name) -> str:
            if name in READONLY_AGENTS:
                text = fusion.union_tools(text, READ_TOOLS)
            return blocks.upsert_rgkb(text, contents[name])
        plans[rel] = fusion.plan_text(root / rel, edit)
    warnings: List[str] = []
    for rel in CI_SCRIPTS:
        def allow(text: str, rel: str = rel) -> str:
            new, warn = fusion.union_allowed_tools(text, READ_TOOLS + [cli_rule(command)])
            warnings.extend("%s: %s" % (rel, w) for w in warn)
            return new
        if (root / rel).is_file():
            plans[rel] = fusion.plan_text(root / rel, allow)
    result: Dict[str, Any] = {"states": fusion.write_plans(root, plans)}
    if warnings:
        result["warnings"] = warnings
    return result
