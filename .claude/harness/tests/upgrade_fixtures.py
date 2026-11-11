# Builders of the Phase 3 fixtures (not a test module). The projects are generated in
# temporary folders instead of stored: they need a git history, and tests/fixtures/README.md
# forbids dotted names in the repository.

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import helpers
from helpers import CORE, git, init_repo

from hcore import upgrade

ASSETS = CORE.parent / "code-index" / "assets"
NOW = datetime(2026, 11, 1, 10, 0, 0)
PROFILES = CORE / "profiles"
INTERPRETER = "python"


def write(root: Path, rel: str, text: str) -> Path:
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return path


def read(root: Path, rel: str) -> str:
    with open(str(Path(root) / rel), encoding="utf-8", newline="") as fh:
        return fh.read()


def tree(root: Path, skip: Tuple[str, ...] = (".git", ".harness")) -> Dict[str, bytes]:
    out = {}
    for path in sorted(Path(root).rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_file() and not any(rel == s or rel.startswith(s + "/") for s in skip):
            out[rel] = path.read_bytes()
    return out


def make_profiles(tmp: Path, variant: str = "v1") -> Path:
    """profile-v1 / profile-v2: the software profile alone; v2 has other fragments."""
    dest = Path(tmp) / ("profiles-" + variant)
    if not dest.exists():
        shutil.copytree(str(PROFILES / "software"), str(dest / "software"))
        if variant == "v2":
            for name in ("factory-planner", "factory-builder"):
                body = dest / "software" / "bodies" / (name + ".md")
                body.write_text(body.read_text(encoding="utf-8") + "\nRègle ajoutée en v2.\n", encoding="utf-8")
    return dest


def make_repo(tmp: Path, commits: int = 3) -> Tuple[Path, List[str]]:
    """A throwaway claude-skills repository (core + code-index assets) with `commits` commits."""
    repo = Path(tmp) / "claude-skills"
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "tests")
    shutil.copytree(str(CORE), str(repo / "skills" / "_core"), ignore=ignore)
    shutil.copytree(str(ASSETS), str(repo / "skills" / "code-index" / "assets"), ignore=ignore)
    init_repo(repo)
    shas = []
    for i in range(commits):
        if i == 0:
            git(repo, "add", "-A")
        else:
            (repo / "CHANGELOG.txt").write_text("change %d\n" % i, encoding="utf-8")
            git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "commit %d" % i)
        shas.append(git(repo, "rev-parse", "HEAD").strip())
    return repo, shas


def repo_info(repo: Path, commit: Optional[str] = None, describe: str = "v-test") -> Dict[str, Any]:
    return {"commit": commit or git(repo, "rev-parse", "HEAD").strip(), "describe": describe, "dirty": False,
            "path": str(repo)}


def plan_and_apply(root: Path, skill: str, repo: Dict[str, Any], profiles_dir: Optional[Path] = None,
                   now: datetime = NOW, requested=(), convert: bool = False, decisions=None,
                   params: Optional[Dict[str, Any]] = None, interpreter: str = INTERPRETER) -> Dict[str, Any]:
    plan = upgrade.build_plan(root, skill, repo, CORE, profiles_dir, params, convert, interpreter, requested,
                              "scout", now, None, ASSETS)
    if plan["action"] != "plan":
        return plan
    files = upgrade.write_plan(root, plan, now)
    result = upgrade.apply_plan(root, Path(root) / files["plan"], decisions, now)
    return dict(plan, applied=result, files=files)


def fresh_install(root: Path, profiles_dir: Path, repo: Dict[str, Any], names=("software",)) -> Dict[str, Any]:
    """EP-11: a new installation = an upgrade plan of a project without files."""
    Path(root).mkdir(parents=True, exist_ok=True)
    return plan_and_apply(root, upgrade.SF, repo, profiles_dir, requested=list(names))


# ------------------------------------------------------------ upgrade-legacy

LEGACY_RGKB = "<!-- rgkb:debut -->\nBloc rédigé à la main par le LLM.\n<!-- rgkb:fin -->\n"
RGKB_TOOLS = "mcp__rgkb__search, mcp__rgkb__definition"


def legacy_agent(name: str, tools: Optional[str], rgkb: bool = False, extra: str = "") -> str:
    head = "---\nname: %s\ndescription: Agent %s ancien.\n" % (name, name)
    if tools:
        head += "tools: %s\n" % tools
    head += "---\n\nTu es %s, agent généré avant le noyau.\nLigne 3 du milieu.\nLigne 4.\n" % name
    if rgkb:
        head += "\n" + LEGACY_RGKB
    return head + extra


def legacy_ci(script: str, extra: str = "") -> str:
    return ("#!/usr/bin/env bash\n# ancien script %s\nset -euo pipefail\n"
            "cmd+=(--allowedTools \"Read,Glob,Grep,Write,Edit,Bash(git *),Bash(python *),%s\")\n%s"
            % (script, "mcp__rgkb__search", extra))


def make_legacy(root: Path) -> Path:
    """upgrade-legacy: installed 'the old way' (no stamp, no harness block), two commits, one untracked file."""
    root = Path(root)
    init_repo(root)
    write(root, "src/app.py", "print('toy')\n")
    write(root, "pyproject.toml", "[project]\nname = 'toy'\n")
    for name, tools, rgkb in (("factory-planner", "Read, Glob, Grep, " + RGKB_TOOLS, True),
                              ("factory-builder", None, True),
                              ("factory-verifier", "Read, Glob, Grep, Bash", True),
                              ("lab-director", "Read, Glob, Grep", False), ("lab-neuro", "Read, Glob, Grep", False),
                              ("lab-math", "Read, Glob, Grep", False), ("lab-builder", None, False),
                              ("lab-verifier", "Read, Glob, Grep, Bash", False)):
        write(root, ".claude/agents/%s.md" % name, legacy_agent(name, tools, rgkb))
    for orch in ("factory-run", "lab-run"):
        write(root, ".claude/skills/%s/SKILL.md" % orch,
              "---\nname: %s\ndescription: Orchestrateur ancien.\ndisable-model-invocation: true\n---\n\n"
              "Corps ancien de %s.\n" % (orch, orch) + (LEGACY_RGKB if orch == "factory-run" else ""))
    write(root, "ci/factory.sh", legacy_ci("factory"))
    write(root, "ci/factory.ps1", "# ancien factory.ps1\n$ErrorActionPreference = 'Stop'\n")
    write(root, "ci/lab.sh", legacy_ci("lab"))
    write(root, ".claude/settings.json", json.dumps({"permissions": {"deny": ["Read(./.git/objects/**)"]}}, indent=2) + "\n")
    write(root, ".vscode/tasks.json", json.dumps({"version": "2.0.0", "inputs": [], "tasks": []}, indent=2) + "\n")
    write(root, ".gitignore", "factory-logs/\n")
    write(root, ".mcp.json", json.dumps({"mcpServers": {
        "rgkb": {"command": "python", "args": [".claude/rgkb/rgkb.py", "mcp"]},
        "other": {"command": "node", "args": ["x.js"]}}}, indent=2) + "\n")
    old = (ASSETS / "rgkb.py").read_text(encoding="utf-8")
    write(root, ".claude/rgkb/rgkb.py", re.sub(r'TOOL_VERSION = "[^"]+"', 'TOOL_VERSION = "0.9.0"', old, count=1))
    write(root, ".knowledge/config.json", json.dumps({"schema": 1, "exclude": ["!.knowledge/**"]}, indent=2) + "\n")
    write(root, ".knowledge/notes.jsonl", '{"id": "n-1", "subject": "a", "text": "note"}\n')
    write(root, ".claude/lab-models.json", json.dumps({"director.cadrage": "opus", "neuro": "sonnet"}) + "\n")
    runs = root / ".harness" / "runs"
    runs.mkdir(parents=True)
    lines = [{"key": "a.py::bug one", "file": "a.py", "line": 1, "title": "Bug one", "verdict": "REJECTED"},
             {"key": "b.py::bug two", "file": "b.py", "line": 2, "title": "Bug two", "verdict": "FIXED"},
             {"key": "c.py::bug three", "file": "c.py", "line": 3, "title": "Bug three", "verdict": "CONFIRMED"}]
    write(root, ".harness/runs/ship-pr-20261001-101010.registry.jsonl",
          "".join(json.dumps(l) + "\n" for l in lines))
    git(root, "add", "-A", "-f")
    git(root, "commit", "-q", "-m", "installation")
    # commit 2: customizations outside any block
    write(root, ".claude/agents/factory-planner.md",
          read(root, ".claude/agents/factory-planner.md") + "\n## Règles maison\n\nToujours citer la spec.\n")
    builder = read(root, ".claude/agents/factory-builder.md").replace("Ligne 3 du milieu.", "Ligne 3 MODIFIEE.")
    write(root, ".claude/agents/factory-builder.md", builder)
    write(root, "ci/factory.sh", read(root, "ci/factory.sh") + "export FACTORY_EXTRA=1\n")
    write(root, ".claude/settings.json", json.dumps({"env": {"MINE": "1"}, "permissions": {
        "deny": ["Read(./.git/objects/**)", "Read(./secret/**)"]}}, indent=2) + "\n")
    write(root, ".vscode/tasks.json", json.dumps({"version": "2.0.0", "inputs": [], "tasks": [
        {"label": "Mine", "type": "shell", "command": "echo mine"}]}, indent=2) + "\n")
    write(root, ".gitignore", "factory-logs/\nmine-1/\nmine-2/\n")
    write(root, ".knowledge/config.json", json.dumps({"schema": 1, "exclude": ["!.knowledge/**", "!mine/**"],
                                                      "hooks": "auto"}, indent=2) + "\n")
    git(root, "add", "-A", "-f")
    git(root, "commit", "-q", "-m", "customizations")
    write(root, "ci/lab.ps1", "# untracked lab.ps1\n$ErrorActionPreference = 'Stop'\n")
    return root


# ------------------------------------------------------------ upgrade-blocks

REPO_OK = (ASSETS / "rgkb.py").is_file()
SKIP_REPO = "claude-skills repository assets absent (core copied into a project)"


def customize_blocks(root: Path) -> None:
    """Customizations outside any block, of every type of generated or merged file."""
    root = Path(root)
    for rel, extra in ((".claude/agents/factory-planner.md", "\n## Règles maison\n\nToujours citer la spec.\n"),
                       (".claude/skills/factory-run/SKILL.md", "\nNote locale de l'équipe.\n"),
                       ("ci/factory.sh", "export FACTORY_EXTRA=1\n"), ("ci/factory.ps1", "# local ps1\n")):
        write(root, rel, read(root, rel) + extra)
    settings = json.loads(read(root, ".claude/settings.json"))
    settings["env"] = {"MINE": "1"}
    settings["permissions"]["deny"].append("Read(./secret/**)")
    write(root, ".claude/settings.json", json.dumps(settings, indent=2) + "\n")
    tasks = json.loads(read(root, ".vscode/tasks.json"))
    tasks["tasks"].append({"label": "Mine", "type": "shell", "command": "echo mine"})
    write(root, ".vscode/tasks.json", json.dumps(tasks, indent=2) + "\n")
    write(root, ".gitignore", read(root, ".gitignore") + "mine-1/\nmine-2/\n")


def blocks_project(tmp: Path, install_repo: Optional[Dict[str, Any]] = None):
    """upgrade-blocks: a project in the RD-01 layout rendered by profile-v1, with customizations.
    Returns (root, repo, profiles-v1, profiles-v2)."""
    tmp = Path(tmp)
    repo_dir, _ = make_repo(tmp, 3)
    info = install_repo or repo_info(repo_dir)
    pv1, pv2 = make_profiles(tmp, "v1"), make_profiles(tmp, "v2")
    root = tmp / "proj"
    fresh_install(root, pv1, info)
    customize_blocks(root)
    return root, info, pv1, pv2
