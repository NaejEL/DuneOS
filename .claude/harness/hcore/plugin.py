# Plugin distribution of the repository (Phase 7, steps M1 and M2).
#
# - PL-01: `read_manifest` reads `.claude-plugin/plugin.json` at the root of the repository
#   (the folder two levels above the core); None when absent or unreadable.
# - PL-02: `effective_name` gives the name the Skill tool must use: `<plugin>:<skill>` when the
#   skill runs from a plugin, `<skill>` otherwise. The mode is read from the UNRESOLVED skill
#   folder (`${CLAUDE_SKILL_DIR}`): `<root>/skills/<skill>` holds a `.claude-plugin/plugin.json`
#   at `<root>` in plugin mode, while a link `~/.claude/skills/<skill>` has none.
#   Without a skill folder, CLAUDE_PLUGIN_ROOT (set by Claude Code for plugin processes) decides.
# - PL-03: `version_guard` fails when something under `skills/` changed since the last commit that
#   changed `version` in plugin.json and `version` was not raised (the plugin cache is indexed by
#   version: a changed content with the same version is never redistributed).

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .proc import Runner

MANIFEST_REL = ".claude-plugin/plugin.json"
GIT_TIMEOUT = 20


def read_manifest(root: Path) -> Optional[Dict[str, Any]]:
    """PL-01."""
    try:
        data = json.loads((Path(root) / MANIFEST_REL).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if isinstance(data, dict) and isinstance(data.get("name"), str) and data["name"]:
        return data
    return None


def plugin_version(root: Path) -> Optional[str]:
    manifest = read_manifest(root)
    version = manifest.get("version") if manifest else None
    return version if isinstance(version, str) and version else None


def effective_name(skill: str, skill_dir: Optional[str] = None,
                   environ: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """PL-02: {skill, name, mode, plugin} for the Skill tool."""
    env = os.environ if environ is None else environ
    plugin_root: Optional[Path] = None
    if skill_dir:
        # Deliberately not resolved: a link must keep looking like a link.
        candidate = Path(os.path.abspath(skill_dir)).parent.parent
        if read_manifest(candidate) is not None and Path(os.path.abspath(skill_dir)).parent.name == "skills":
            plugin_root = candidate
    elif env.get("CLAUDE_PLUGIN_ROOT"):
        root = Path(env["CLAUDE_PLUGIN_ROOT"])
        if read_manifest(root) is not None:
            plugin_root = root
    if plugin_root is None:
        return {"skill": skill, "name": skill, "mode": "links", "plugin": None}
    plugin = read_manifest(plugin_root)["name"]
    return {"skill": skill, "name": "%s:%s" % (plugin, skill), "mode": "plugin", "plugin": plugin}


def _git(root: Path, run: Optional[Callable[..., Any]], *args: str) -> Optional[str]:
    runner = run or Runner(cwd=str(root), timeout=GIT_TIMEOUT)
    res = runner(["git"] + list(args))
    return res.out if res.ok else None


def _version_of(text: Optional[str]) -> Optional[str]:
    try:
        data = json.loads(text or "")
    except ValueError:
        return None
    version = data.get("version") if isinstance(data, dict) else None
    return version if isinstance(version, str) else None


def version_guard(root: Path, run: Optional[Callable[..., Any]] = None) -> Dict[str, Any]:
    """PL-03: {ok, status, version, base, files}. status: no_plugin | no_git | no_baseline | bumped | clean | stale."""
    root = Path(root)
    current = plugin_version(root)
    if current is None:
        return {"ok": True, "status": "no_plugin", "version": None, "base": None, "files": []}
    if _git(root, run, "rev-parse", "--git-dir") is None:
        return {"ok": True, "status": "no_git", "version": current, "base": None, "files": []}
    base = (_git(root, run, "log", "-1", "--format=%H", "-G", '"version"', "--", MANIFEST_REL) or "").strip()
    if not base:
        # The manifest is not committed yet: the version is being introduced, nothing to compare.
        return {"ok": True, "status": "no_baseline", "version": current, "base": None, "files": []}
    if _version_of(_git(root, run, "show", "%s:%s" % (base, MANIFEST_REL))) != current:
        return {"ok": True, "status": "bumped", "version": current, "base": base, "files": []}
    changed: List[str] = (_git(root, run, "diff", "--name-only", base, "--", "skills") or "").splitlines()
    changed += (_git(root, run, "ls-files", "--others", "--exclude-standard", "--", "skills") or "").splitlines()
    files = sorted(set(changed))
    return {"ok": not files, "status": "stale" if files else "clean", "version": current, "base": base,
            "files": files}
