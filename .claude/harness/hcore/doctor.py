# /harness-doctor in a project (DR-01 to DR-10) and the proposal of an upgrade (PR-01 to PR-05).
#
# - DR-02: JSON report: equipped, version, core, blocks, legacy_files, missing, hooks,
#   models, gh, signals, upgrade, and a status ok|warn|error for each check.
# - DR-03: a block is `edited` when its hash differs from the stamp, `outdated` when
#   the current repository renders it differently.
# - DR-04: expected files = stamp files U generated files of the active profiles U
#   MANIFEST files U (code-index) rgkb.py, the rgkb entry of .mcp.json, config.json.
# - DR-05: hooks declared (config.json `hooks` auto|commit) and present in the folder of
#   `git rev-parse --git-path hooks` with the rgkb block; no git: n/a.
# - DR-06: models.json absent -> warn; lab-models.json present -> migration_available.
# - DR-07: gh absent or not authenticated -> warn, 10 s, never blocking.
# - DR-08: a profile absent from the stamp whose deterministic status is not rejected
#   -> propose_rescan (never launched).
# - DR-09: the doctor writes only its journal and the plan files; nothing on an
#   unequipped project. DR-10: exit 2 when the root does not exist.
# - PR-01 to PR-03: the proposal of an upgrade goes through a gate (blocks_only or not).

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import (CORE_VERSION, blocks, cx, fusion, gates, ghflow, history, journal, proc, render, runs, scout,
               stamp, upgrade, version)
from .proc import Runner

GH_TIMEOUT = 10
STATUS_RANK = {"ok": 0, "warn": 1, "error": 2}


class DoctorError(ValueError):
    """Unusable root (exit 2)."""


def _worst(*states: str) -> str:
    return max(states, key=lambda s: STATUS_RANK[s]) if states else "ok"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def gh_state(run: Optional[Callable[..., Any]] = None) -> Dict[str, Any]:
    """DR-07."""
    overridden = "gh" in proc.overrides(warn=False)
    if not overridden and shutil.which("gh") is None:
        return {"installed": False, "authenticated": False, "message": "gh: absent", "status": "warn"}
    runner = run or Runner(timeout=GH_TIMEOUT)
    res = runner(["gh", "auth", "status"])
    if res.ok:
        return {"installed": True, "authenticated": True, "message": "ok", "status": "ok"}
    return {"installed": True, "authenticated": False, "message": ghflow.MSG_GH_AUTH, "status": "warn"}


def hooks_state(root: Path, run: Optional[Callable[..., Any]] = None) -> Dict[str, Any]:
    """DR-05."""
    declared = None
    try:
        declared = str(json.loads((root / ".knowledge" / "config.json").read_text(encoding="utf-8-sig"))
                       .get("hooks") or "off").lower()
    except (OSError, ValueError, AttributeError):
        pass
    if declared not in ("auto", "commit"):
        return {"declared": declared or "off", "expected": [], "missing": [], "status": "ok"}
    expected = ["post-merge", "post-rewrite"] + (["pre-commit"] if declared == "commit" else [])
    runner = run or Runner(cwd=str(root), timeout=10)
    res = runner(["git", "rev-parse", "--git-path", "hooks"])
    if not res.ok or not res.out.strip():
        return {"declared": declared, "expected": expected, "missing": [], "status": "ok", "git": "n/a"}
    folder = Path(res.out.strip())
    folder = folder if folder.is_absolute() else root / folder
    missing = []
    for name in expected:
        path = folder / name
        if not path.is_file() or "# >>> rgkb >>>" not in path.read_text(encoding="utf-8", errors="replace"):
            missing.append(name)
    return {"declared": declared, "expected": expected, "missing": missing, "status": "warn" if missing else "ok"}


def models_state(root: Path) -> Dict[str, Any]:
    """DR-06."""
    has = (root / ".claude" / "models.json").is_file()
    legacy = (root / ".claude" / "lab-models.json").is_file()
    return {"models_json": has, "lab_models_json": legacy, "migration_available": legacy and not has,
            "status": "ok" if has else "warn",
            "hint": "run /software-factory --upgrade (MI-01)" if legacy and not has else None}


def signals_state(root: Path, stamped: List[str], profiles_dir: Optional[Path] = None) -> Dict[str, Any]:
    """DR-08."""
    rows = scout.verdict(root, None, False, profiles_dir)["profiles"]
    new = [r["name"] for r in rows if r["name"] not in stamped and r["status"] != "rejected"]
    state: Dict[str, Any] = {"propose_rescan": bool(new), "profiles": new}
    if new:
        state["command"] = "/software-factory --rescan"
    state["status"] = "warn" if new else "ok"
    return state


def _block_rows(root: Path, layouts: Dict[str, render.Layout], recorded: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for rel, layout in layouts.items():
        text = fusion._read_text(root / rel)                       # noqa: SLF001
        if text is None or not blocks.has_layout_blocks(text):
            continue                                   # absent (see `missing`) or legacy (see `legacy_files`)
        saved = (recorded.get(rel) or {}).get("blocks") or {}
        for name, style, content in layout.blocks:
            inner = blocks.get_block(text, name, style)
            if inner is None:
                rows.append({"file": rel, "block": name, "state": "missing_block"})
                continue
            edited = name in saved and saved[name] != _sha(inner)
            # outdated: the repository renders the block differently from what was installed (the stamp
            # hash), so that a block edited by hand is `edited` alone until the repository changes too.
            outdated = _sha(content) != saved[name] if name in saved else inner != content
            state = "edited+outdated" if edited and outdated else "edited" if edited else "outdated" if outdated else "ok"
            rows.append({"file": rel, "block": name, "state": state})
    return rows


def report(root: Path, repo: Dict[str, Any], core_dir: Optional[Path] = None, profiles_dir: Optional[Path] = None,
           assets: Optional[Path] = None, run: Optional[Callable[..., Any]] = None,
           gh_run: Optional[Callable[..., Any]] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """DR-02. Writes nothing."""
    root = Path(root)
    if not root.is_dir():
        raise DoctorError("root not found: %s" % root)
    core_dir = Path(core_dir) if core_dir else history.CORE_DIR
    resolved = stamp.resolve_full(root)
    data = stamp._read(root)                                         # noqa: SLF001
    skills = resolved["skills"]
    if resolved["source"] == "none" and not skills:
        return {"equipped": "none", "status": "ok", "checks": {}}
    equipped = "stamp" if data and data.get("skills") else "inferred"
    out: Dict[str, Any] = {"equipped": equipped, "generation": resolved.get("generation"),
                           "profiles": resolved["profiles"], "skills": skills}
    # version (VS-03)
    versions: Dict[str, Any] = {}
    for skill in skills:
        installed = upgrade._installed(data, skill, root, core_dir)               # noqa: SLF001
        lag = version.lag(installed["commit"], repo.get("commit"), Path(repo.get("path") or "."),
                          installed["core_version"], CORE_VERSION, run)
        entry = upgrade._stamp_entry(data, skill)                                  # noqa: SLF001
        versions[skill] = {"installed": installed, "repo": {"commit": repo.get("commit"),
                                                            "describe": repo.get("describe"),
                                                            "core_version": CORE_VERSION},
                           "lag": lag, "dirty": bool(entry.get("repo_dirty"))}
    out["version"] = versions
    v_status = _worst(*[("error" if v["lag"]["status"] in ("ahead", "diverged") else
                         "ok" if v["lag"]["status"] == "up_to_date" and not v["dirty"] else "warn")
                        for v in versions.values()])
    # core copy (CO-03, CO-05)
    dest = root / ".claude" / "harness"
    entries = history.load(core_dir)
    core: Dict[str, Any] = {"present": dest.is_dir()}
    c_status = "ok"
    if dest.is_dir():
        info = history.installed_core(dest, entries)
        states = history.copy_states(core_dir, dest, entries)
        custom, extra = history.customized(states)
        core.update(info, repo_core_version=CORE_VERSION, customized=sorted(custom), extra=sorted(extra))
        if custom:
            c_status = "error"
        elif info.get("mixed") or version.semver(info.get("core_version")) != version.semver(CORE_VERSION):
            c_status = "warn"
    elif "software-factory" in skills:
        c_status = "warn"
    core["status"] = c_status
    out["core"] = core
    # blocks (DR-03)
    rows: List[Dict[str, Any]] = []
    expected: List[str] = []
    sf_ok = "software-factory" in skills and resolved["profiles"]
    if sf_ok:
        try:
            inputs = upgrade.sf_inputs(root, data, resolved, profiles_dir)
            if not inputs["missing"]:
                contributions = upgrade.contributions_of(root, data, inputs["interpreter"])
                layouts = upgrade.sf_layouts(root, inputs["profiles"], inputs["params"], contributions,
                                             repo["describe"], profiles_dir)
                recorded = upgrade._stamp_entry(data, "software-factory").get("files") or {}   # noqa: SLF001
                rows += _block_rows(root, layouts, recorded)
                expected += list(layouts)
        except (upgrade.UpgradeError, ValueError) as exc:
            out["blocks_error"] = str(exc)
    if "code-index" in skills:
        expected += [cx.RGKB_PATH, ".knowledge/config.json"]
    for entry_name in skills:
        expected += list((upgrade._stamp_entry(data, entry_name).get("files") or {}))     # noqa: SLF001
    manifest = version.read_manifest(dest) if dest.is_dir() else None
    expected += [".claude/harness/" + r for r in ((manifest or {}).get("files") or {})]
    expected = sorted(dict.fromkeys(expected))
    out["blocks"] = rows
    b_status = _worst(*[("error" if r["state"] == "missing_block" else "ok" if r["state"] == "ok" else "warn")
                        for r in rows])
    missing = [rel for rel in expected if not (root / rel).exists()]
    if "code-index" in skills:
        try:
            if "rgkb" not in json.loads((root / ".mcp.json").read_text(encoding="utf-8-sig")).get("mcpServers", {}):
                missing.append(".mcp.json#mcpServers.rgkb")
        except (OSError, ValueError):
            missing.append(".mcp.json#mcpServers.rgkb")
    out["missing"] = sorted(missing)
    legacy = sorted({rel for s in skills for rel, m in (upgrade._stamp_entry(data, s).get("files") or {}).items()  # noqa: SLF001
                     if isinstance(m, dict) and m.get("kind") == "legacy"})
    out["legacy_files"] = legacy
    out["hooks"] = hooks_state(root, run)
    out["models"] = models_state(root)
    out["gh"] = gh_state(gh_run)
    out["signals"] = signals_state(root, resolved["profiles"], profiles_dir)
    # upgrade plans (UP-01), nothing written
    by_skill: Dict[str, int] = {}
    classes: List[str] = []
    all_blocks_only = True
    notes: List[str] = []
    for skill in skills:
        try:
            plan = upgrade.build_plan(root, skill, repo, core_dir, profiles_dir, now=now, run=run, assets=assets)
        except ghflow.Stop as exc:
            notes.append(str(exc))
            by_skill[skill] = 0
            continue
        if plan["action"] == "fill_params":
            notes.append("parameters to fill: %s" % ", ".join(plan["missing"]))
            by_skill[skill] = 0
            continue
        by_skill[skill] = len(plan["changes"])
        classes += [c["kind"] for c in plan["changes"]]
        all_blocks_only = all_blocks_only and plan["blocks_only"]
    total = sum(by_skill.values())
    out["upgrade"] = {"changes": total, "blocks_only": all_blocks_only, "by_skill": by_skill,
                      "classes": sorted(set(classes)), "notes": notes, "status": "ok" if total == 0 and not notes
                      else "warn"}
    out["checks"] = {"version": v_status, "core": c_status, "blocks": b_status,
                     "files": "error" if out["missing"] else "ok",
                     "legacy": "warn" if legacy else "ok", "hooks": out["hooks"]["status"],
                     "models": out["models"]["status"], "gh": out["gh"]["status"],
                     "signals": out["signals"]["status"], "upgrade": out["upgrade"]["status"]}
    out["status"] = _worst(*out["checks"].values())
    return out


def proposal(rep: Dict[str, Any], policy: str) -> Optional[Dict[str, Any]]:
    """PR-01 to PR-03: the gate that proposes `--upgrade`, and what follows from its decision."""
    up = rep.get("upgrade") or {}
    if not up.get("changes"):
        return None
    gate_id = "harness-doctor.upgrade-blocks" if up["blocks_only"] else "harness-doctor.upgrade-other"
    event = gates.decide(gate_id, policy)
    commands = [c for s, c in (("software-factory", "/software-factory --upgrade"),
                               ("code-index", "/code-index --upgrade")) if up["by_skill"].get(s)]
    blockers = [k for k in up.get("classes", []) if k not in upgrade.BLOCKS_ONLY]
    launch = up["blocks_only"] and event["decision"] == gates.DEFAULT
    return {"gate": gate_id, "decision": event["decision"], "default": event["default"], "launch": launch,
            "commands": commands, "blockers": blockers, "event": event}


def write_journal(root: Path, rep: Dict[str, Any], now: Optional[datetime] = None) -> Optional[str]:
    """DR-09, JR-04: one `doctor` event in harness-doctor-<ts>.jsonl; nothing on an unequipped project."""
    if rep.get("equipped") == "none":
        return None
    path = Path(root) / runs.RUNS_DIR / journal.journal_name("harness-doctor", (now or datetime.now()))
    journal.append(path, {"event": "doctor", "checks": rep["checks"]})
    return path.relative_to(root).as_posix()
