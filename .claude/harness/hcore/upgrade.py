# Upgrade engine of an equipped project (Phase 3, UP-01 to UP-13, DT-01 to DT-05).
#
# - UP-01: `build_plan` computes every modification without writing in the project;
#   `write_plan` writes only upgrade-<ts>.plan.json and upgrade-<ts>.diff under
#   .harness/runs/.
# - UP-02: software-factory plan = core copy (CO), generated files of the active
#   profiles, merges, migrations, headers, stamp. code-index plan = rgkb.py, rgkb
#   blocks, merges, contributions, local hooks, stamp.
# - UP-03: out-of-block invariant, checked before any write: strip_blocks(before) ==
#   strip_blocks(after) (JSON and .gitignore merges: nothing existing is modified or
#   removed). The end-of-file newlines are not compared.
# - UP-04: complete unified diff of every created, modified or deleted file.
# - UP-05: `apply_plan` re-checks the sha256 of every file as it was planned (stale
#   plan: stop, nothing written), backs up first, writes all or nothing, the stamp last.
# - UP-06: a plan right after an apply has no change; an empty apply writes nothing.
# - UP-07: classes header | block | merge | copy | create | convert | migrate | stamp |
#   local; blocks_only <=> all in {header, block, merge, copy, stamp, local}.
# - UP-08: no way back (installed version newer than the repository: stop).
# - UP-09: one upgrade at a time per project (lock). UP-10: one `upgrade` event per
#   file written. UP-11: no sub-agent, no block rewritten by the LLM. UP-12: a skill
#   never touches the blocks of another one. UP-13: a missing generated file is recreated.
# - DT-02, DT-03, DT-04: a project without stamp is detected by its generated files; the
#   stamp is created by the first successful upgrade (inferred_from).
# - DT-05: the stamp's interpreter beats the one detected by the caller as long as it
#   answers on this machine, so moving between machines does not rewrite the files.
# - CV: the first upgrade of a file without harness blocks goes through convert.py.

from __future__ import annotations

import difflib
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import (CORE_VERSION, blocks, convert, cx, fusion, gates, history, interp, journal, lab, migrate,
               models, profiles, render, runs, stack, stamp, version)
from .ghflow import Stop
from .proc import Runner

SF, CI = "software-factory", "code-index"
BLOCKS_ONLY = ("header", "block", "merge", "copy", "stamp", "local")
KINDS = BLOCKS_ONLY + ("create", "convert", "migrate")
LOCK = ".harness/runs/upgrade.lock"
CONVERT_GATE = "upgrade.convert"
KEEP_OPTION, CONVERT_OPTION = "Keep as is", "Convert (backup)"
CODE_INDEX_ASSETS = Path(__file__).resolve().parent.parent.parent / "code-index" / "assets"


class UpgradeError(ValueError):
    """Invalid request (exit 2)."""


def sha_text(text: Optional[str]) -> Optional[str]:
    return None if text is None else hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha_file(path: Path) -> Optional[str]:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _asset_module(assets: Path):
    spec = importlib.util.spec_from_file_location("rgkb_asset", str(Path(assets) / "rgkb.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)             # type: ignore[union-attr]
    return module


class Ctx:
    """What a plan accumulates: changes, managed files, candidates, notes."""

    def __init__(self, root: Path, skill: str, repo: Dict[str, Any], now: datetime, run: Any):
        self.root, self.skill, self.repo, self.now, self.run = Path(root), skill, repo, now, run
        self.now_s = stamp.now_iso(now)
        self.changes: Dict[str, Dict[str, Any]] = {}
        self.managed: Dict[str, Dict[str, Any]] = {}
        self.candidates: List[Dict[str, Any]] = []
        self.forced: List[str] = []
        self.notes: List[str] = []
        self.warnings: List[str] = []
        self.migrations: List[Dict[str, Any]] = []

    def read(self, rel: str) -> Optional[str]:
        return fusion._read_text(self.root / rel)          # noqa: SLF001

    def change(self, rel: str, kind: str, after: Optional[str]) -> bool:
        before = self.read(rel)
        if after == before:
            return False
        self.changes[rel] = {"file": rel, "kind": kind, "before": before, "after": after}
        return True

    def manage(self, rel: str, file_kind: str, text: Optional[str] = None,
               layout: Optional[render.Layout] = None) -> None:
        entry: Dict[str, Any] = {"kind": file_kind}
        if layout is not None and text is not None:
            hashes = render.block_hashes(text, layout)
            if hashes:
                entry["blocks"] = hashes
        self.managed[rel] = entry


def _layout_class(before: str, after: str, layout: render.Layout) -> str:
    for name, style, _c in layout.blocks:
        if blocks.get_block(before, name, style) != blocks.get_block(after, name, style):
            return "block"
    return "header"


def _without_legacy_blocks(text: str) -> str:
    for name in blocks.harness_block_names(text):
        if name not in blocks.LAYOUT_BLOCKS:
            text = blocks.remove_block(text, name, "hash" if name == "harness.model" else "md")
    return text


def layout_file(ctx: Ctx, rel: str, layout: render.Layout, convert_all: bool, base_run: Any = None) -> None:
    """RD-01, UP-07, CV: brings one generated file to its layout."""
    before = ctx.read(rel)
    if before is None:
        after = render.render_new(layout)
        ctx.change(rel, "create", after)
        ctx.manage(rel, "generated", after, layout)
        return
    if blocks.has_layout_blocks(before):
        after = render.update(before, layout)
        if after != before:
            ctx.change(rel, _layout_class(before, after, layout), after)
        ctx.manage(rel, "generated", after, layout)
        return
    base = convert.git_base(ctx.root, rel, base_run)
    # Phase 1/2 blocks (harness.model, harness.profile.*) are regenerable from models.json and the
    # profile: they are neutral for the classification, and the layout renders them again.
    cls, rest = convert.classify(_without_legacy_blocks(before),
                                 None if base is None else _without_legacy_blocks(base))
    if cls in (convert.PRISTINE, convert.SUFFIX) or convert_all:
        if cls not in (convert.PRISTINE, convert.SUFFIX):
            ctx.forced.append(rel)
        after = convert.converted(layout, before, cls, rest)
        ctx.change(rel, "convert", after)
        ctx.manage(rel, "generated", after, layout)
        return
    alt = convert.converted(layout, before, cls, rest)
    ctx.candidates.append({"file": rel, "class": cls, "alt": alt,
                           "alt_entry": dict(kind="generated", blocks=render.block_hashes(alt, layout))})
    ctx.manage(rel, "legacy")


# ---------------------------------------------------------------- arguments

def parse_arguments(text: str) -> Optional[Dict[str, Any]]:
    """EP-08, CX-17: `--upgrade [--convert]` and nothing else; None without --upgrade."""
    tokens = (text or "").split()
    if "--upgrade" not in tokens:
        if "--convert" in tokens:
            raise UpgradeError("--convert goes with --upgrade only")
        return None
    others = [t for t in tokens if t not in ("--upgrade", "--convert")]
    if others:
        raise UpgradeError("--upgrade excludes --profile, --rescan, --reindex and any other argument: %s"
                           % " ".join(others))
    if tokens.count("--upgrade") > 1 or tokens.count("--convert") > 1:
        raise UpgradeError("--upgrade and --convert are given once")
    return {"upgrade": True, "convert": "--convert" in tokens}


def ailab_translate(text: str) -> str:
    """EP-10: /ai-lab --upgrade is software-factory --upgrade, WITHOUT --profile research
    (every profile of the stamp is upgraded); otherwise AX-01: --profile research is forced."""
    parsed = parse_arguments(text)
    if parsed:
        return "--upgrade" + (" --convert" if parsed["convert"] else "")
    return ("--profile research " + (text or "")).strip()


# ------------------------------------------------------------ shared inputs

def _stamp_entry(data: Optional[Dict[str, Any]], skill: str) -> Dict[str, Any]:
    return dict(((data or {}).get("skills") or {}).get(skill) or {})


def _mcp_interpreter(root: Path) -> Optional[str]:
    try:
        entry = json.loads((root / ".mcp.json").read_text(encoding="utf-8-sig"))["mcpServers"]["rgkb"]
        args = [a for a in entry.get("args", [])]
        return " ".join([entry["command"]] + (["-3"] if args[:1] == ["-3"] else []))
    except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError, AttributeError):
        return None


def interpreter_of(root: Path, old_entry: Dict[str, Any], given: Optional[str]) -> str:
    """DT-03 order: stamp when it answers on this machine, option, stamp, .mcp.json, detection D5.

    The option is the interpreter detected by the calling skill on the current machine: letting it
    win would rewrite every generated file each time the project moves to another machine. The
    recorded one is kept as long as it works here (DT-05)."""
    saved = (old_entry.get("options") or {}).get("interpreter")
    if saved and given and saved != given:
        version = interp.probe(saved.split())
        if version is not None and version[:2] >= interp.FLOOR:
            return saved
    if given:
        return given
    if saved:
        return saved
    found = _mcp_interpreter(Path(root))
    if found:
        return found
    detected = interp.detect()
    return " ".join(detected["command"]) if detected else "python"


def contributions_of(root: Path, data: Optional[Dict[str, Any]], interpreter: str) -> List[str]:
    """RD-05: tools declared by the other skills (stamp), else inferred from the rgkb wiring."""
    entry = _stamp_entry(data, CI)
    if "contributes" in entry:
        return list(entry["contributes"])
    wired = any(blocks.get_block(fusion._read_text(root / rel) or "", "rgkb", "md") is not None   # noqa: SLF001
                for rel in cx.wire_targets(root).values())
    return rpc_tools(interpreter) if wired else []


def rpc_tools(interpreter: str) -> List[str]:
    return cx.READ_TOOLS + [cx.cli_rule(cx.interpreter(interpreter.split()))]


def _installed(data: Optional[Dict[str, Any]], skill: str, root: Path, core_dir: Path) -> Dict[str, Any]:
    entry = _stamp_entry(data, skill)
    if entry:
        return {"commit": entry.get("repo_commit"), "describe": entry.get("repo_describe"),
                "core_version": entry.get("core_version") or entry.get("tool_version")}
    if skill == SF:
        dest = root / ".claude" / "harness"
        manifest = version.read_manifest(dest) if dest.is_dir() else None
        if manifest:
            return {"commit": manifest["repo"].get("commit"), "describe": manifest["repo"].get("describe"),
                    "core_version": manifest.get("core_version")}
        if dest.is_dir():
            info = history.installed_core(dest, history.load(core_dir))
            return {"commit": None, "describe": None, "core_version": info.get("core_version")}
    return {"commit": None, "describe": None, "core_version": None}


def _guard_direction(installed: Dict[str, Any], repo: Dict[str, Any], run: Any) -> Dict[str, Any]:
    """UP-08."""
    result = version.lag(installed["commit"], repo.get("commit"), Path(repo.get("path") or "."),
                         installed["core_version"], CORE_VERSION, run)
    newer_core = installed["core_version"] and version.semver(installed["core_version"]) > version.semver(CORE_VERSION)
    if result["status"] in ("ahead", "diverged") or newer_core:
        raise Stop("project installed by a newer harness (%s) than this repository (%s); "
                   "update claude-skills first (git pull)"
                   % (installed["describe"] or installed["core_version"] or result["status"], repo["describe"]))
    return result


# ---------------------------------------------------------- software-factory

def _plan_core(ctx: Ctx, core_dir: Path) -> Dict[str, Any]:
    """CO-03 to CO-06: the copy of the core under .claude/harness/."""
    dest = ctx.root / ".claude" / "harness"
    entries = history.load(core_dir)
    states = history.copy_states(core_dir, dest, entries) if dest.is_dir() else {}
    custom, extra = history.customized(states)
    if custom:
        raise Stop("core not upgraded: customized files in .claude/harness/: %s; move your changes out of "
                   ".claude/harness/ or restore these files, then run the upgrade again" % ", ".join(sorted(custom)))
    wanted = history.tree(core_dir)
    for rel in wanted:
        target = ".claude/harness/" + rel
        ctx.change(target, "copy", (Path(core_dir) / rel).read_bytes().decode("utf-8"))
    for rel, state in sorted(states.items()):
        if state == history.PRISTINE and rel not in wanted:
            ctx.change(".claude/harness/" + rel, "copy", None)
    manifest = history.manifest_text(core_dir, ctx.repo, entries)
    ctx.change(".claude/harness/MANIFEST.json", "copy", manifest)
    ctx.manage(".claude/harness/MANIFEST.json", "copied")
    info = history.installed_core(dest, entries) if dest.is_dir() else {}
    if extra:
        ctx.notes.append("extra files kept in .claude/harness/: %s" % ", ".join(sorted(extra)))
    return {"installed": info, "extra": sorted(extra), "states": {s: sum(1 for v in states.values() if v == s)
                                                                   for s in set(states.values())}}


def _gather_params(prof_list: Sequence[Dict[str, Any]], saved: Dict[str, Any], given: Dict[str, Any],
                   detected: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any], List[str]]:
    """RD-03, RD-04: (all params, judgement params to store, missing required)."""
    judged = dict(saved)
    judged.update(given)
    missing: List[str] = []
    for prof in prof_list:
        for spec in prof.get("params", []):
            name = spec["name"]
            if spec["source"] == "judgement" and spec.get("required") and judged.get(name) in (None, ""):
                if name not in missing:
                    missing.append(name)
    allp = dict(detected)
    allp.update(judged)
    return allp, judged, missing


def sf_layouts(root: Path, prof_list: Sequence[Dict[str, Any]], params: Dict[str, Any],
               contributions: Sequence[str], describe: str,
               profiles_dir: Optional[Path] = None) -> Dict[str, render.Layout]:
    """RD-01, RD-02: the layout of every generated file of the active profiles."""
    hdr = render.header(describe, CORE_VERSION)
    roles = tuple(dict.fromkeys(tuple(models.ROLES) + tuple(profiles.model_roles(profiles_dir))))
    assignment = models.resolve_readonly(root, roles)
    by_role = {r: v["model"] for r, v in assignment["roles"].items()}
    out: Dict[str, render.Layout] = {}
    for prof in prof_list:
        out.update(render.profile_layouts(prof, params, contributions, by_role, hdr))
    return out


def sf_inputs(root: Path, data: Optional[Dict[str, Any]], resolved: Dict[str, Any],
              profiles_dir: Optional[Path] = None, given: Optional[Dict[str, Any]] = None,
              interpreter_arg: Optional[str] = None, requested: Sequence[str] = ()) -> Dict[str, Any]:
    """Profiles, parameters and interpreter of the software-factory plan (shared with the doctor)."""
    old = _stamp_entry(data, SF)
    names = list(resolved["profiles"]) or list(requested)
    known = profiles.discover(profiles_dir)
    unknown = [n for n in names if n not in known]
    if unknown:
        raise UpgradeError("unknown profile: %s (known: %s)" % (", ".join(unknown), ", ".join(known)))
    prof_list = [known[n] for n in names]
    interpreter = interpreter_of(Path(root), old, interpreter_arg)
    stacks = stack.detect(Path(root))["stacks"]
    allp, judged, missing = _gather_params(prof_list, old.get("params") or {}, given or {},
                                           {"stacks": stacks, "interpreter": interpreter})
    return {"names": names, "profiles": prof_list, "params": allp, "judged": judged, "missing": missing,
            "interpreter": interpreter, "stacks": stacks, "old": old}


def _plan_sf(ctx: Ctx, data: Optional[Dict[str, Any]], resolved: Dict[str, Any], core_dir: Path,
             profiles_dir: Optional[Path], requested: Sequence[str], by: str, given_params: Dict[str, Any],
             interpreter_arg: Optional[str], convert_all: bool) -> Dict[str, Any]:
    root = ctx.root
    old = _stamp_entry(data, SF)
    inputs = sf_inputs(root, data, resolved, profiles_dir, given_params, interpreter_arg, requested)
    names, prof_list, allp = inputs["names"], inputs["profiles"], inputs["params"]
    judged, interpreter, stacks = inputs["judged"], inputs["interpreter"], inputs["stacks"]
    if inputs["missing"]:
        return {"action": "fill_params", "missing": inputs["missing"]}
    core_info = _plan_core(ctx, core_dir)
    contributions = contributions_of(root, data, interpreter)
    ci_files: List[str] = []
    for rel, layout in sf_layouts(root, prof_list, allp, contributions, ctx.repo["describe"],
                                  profiles_dir).items():
        layout_file(ctx, rel, layout, convert_all, ctx.run)
        if rel.startswith("ci/"):
            ci_files.append(rel)
    merged = profiles.merged(prof_list, stacks, root, allp.get("test_command"), allp.get("test_windows"))
    for rel, plan in ((".claude/settings.json", fusion.plan_json(root / ".claude" / "settings.json",
                                                                  [fusion.op_array_union(("permissions", "deny"), merged["deny"])])),
                      (".vscode/tasks.json", fusion.plan_json(root / ".vscode" / "tasks.json",
                                                              [fusion.op_tasks(merged["inputs"], merged["tasks"])])),
                      (".gitignore", fusion.plan_lines(root / ".gitignore", merged["gitignore"]))):
        if plan.text is not None:
            ctx.change(rel, "merge", plan.text)
        ctx.manage(rel, "merged")
    new_models = migrate.models_text(root)
    if new_models is not None:
        ctx.change(models.MODELS_FILE.as_posix(), "migrate", new_models)
    done = migrate.migrated_sources(data)
    findings, mig = migrate.registries_text(root, done, ctx.now_s)
    if findings is not None:
        ctx.change(".claude/findings.jsonl", "migrate", findings)
    ctx.migrations += mig
    for name in migrate.legacy_deliverables(root):
        ctx.notes.append("%s: repris par le prochain /audit (MG-01)" % name)
    options = {"stacks": stacks, "interpreter": interpreter, "ci": sorted(set(ci_files)),
               "code_index_offered": bool((old.get("options") or {}).get("code_index_offered", False))}
    return {"action": "plan", "profiles": names, "options": options, "params": judged, "core": core_info,
            "core_version": CORE_VERSION}


# ---------------------------------------------------------------- code-index

def _rgkb_kind(name: str) -> str:
    if name in cx.READONLY_AGENTS:
        return "read-only"
    return "orchestrator" if name in cx.ORCHESTRATORS else "builder"


def rgkb_contents(root: Path, command: Sequence[str], describe: str, assets: Optional[Path] = None) -> Dict[str, str]:
    """RD-06: the content of the `rgkb` block of every wire target, from the templates."""
    assets = Path(assets) if assets else CODE_INDEX_ASSETS
    tool_version = cx.tool_version((assets / "rgkb.py").read_text(encoding="utf-8")) or ()
    tv = ".".join(str(x) for x in tool_version)
    out = {}
    for name in cx.wire_targets(Path(root)):
        template = (assets / "blocks" / render.RGKB_KINDS[_rgkb_kind(name)]).read_text(encoding="utf-8")
        out[name] = render.rgkb_block(template, name, " ".join(command), describe, tv)
    return out


def _plan_ci(ctx: Ctx, data: Optional[Dict[str, Any]], assets: Path, interpreter_arg: Optional[str]) -> Dict[str, Any]:
    root = ctx.root
    old = _stamp_entry(data, CI)
    interpreter = interpreter_of(root, old, interpreter_arg)
    command = cx.interpreter(interpreter.split())
    asset = _asset_module(assets)
    wanted = (Path(assets) / "rgkb.py").read_bytes()
    dest = root / cx.RGKB_PATH
    notes: List[str] = []
    if not dest.exists() or dest.read_bytes() != wanted:
        mine = cx.tool_version(wanted.decode("utf-8", "replace"))
        theirs = cx.tool_version(dest.read_bytes().decode("utf-8", "replace")) if dest.exists() else None
        if not dest.exists() or (mine is not None and (theirs is None or mine >= theirs)):
            ctx.change(cx.RGKB_PATH, "copy", wanted.decode("utf-8"))
        else:
            notes.append("rgkb.py kept: project version %s is newer than the asset %s" % (theirs, mine))
    ctx.manage(cx.RGKB_PATH, "copied")
    for rel, ops in ((".mcp.json", cx.mcp_ops(command)), (".claude/settings.json", cx.settings_ops(command)),
                     (".vscode/tasks.json", cx.tasks_ops(command))):
        plan = fusion.plan_json(root / rel, ops)
        if plan.text is not None:
            ctx.change(rel, "merge", plan.text)
        ctx.manage(rel, "merged")
    config = root / ".knowledge" / "config.json"
    hooks_mode = None
    if config.is_file():
        heavy = cx.heavy_dirs(stack.detect(root)["artifact_dirs"])
        plan = fusion.plan_json(config, cx.config_ops(heavy))
        if plan.text is not None:
            ctx.change(".knowledge/config.json", "merge", plan.text)
        ctx.manage(".knowledge/config.json", "merged")
        try:
            hooks_mode = str(json.loads(config.read_text(encoding="utf-8-sig")).get("hooks") or "off").lower()
        except (OSError, ValueError):
            hooks_mode = None
    gi = root / ".gitignore"
    text = cx.read_text(gi)
    if text is not None and cx.knowledge_excluded(text):
        plan = fusion.plan_lines(gi, [cx.GITIGNORE_NOTE, cx.COUNTER_RULE])
        if plan.text is not None:
            ctx.change(".gitignore", "merge", plan.text)
    targets = cx.wire_targets(root)
    present = {n: blocks.get_block(ctx.read(r) or "", "rgkb", "md") is not None for n, r in targets.items()}
    no_wire = (old.get("options") or {}).get("no_wire")
    if no_wire is None:
        no_wire = not any(present.values())
    contributes: List[str] = []
    if not no_wire:
        contributes = rpc_tools(interpreter)
        describe = ctx.repo["describe"]
        contents = rgkb_contents(root, command, describe, assets)
        for name, rel in targets.items():
            content = contents[name]
            before = ctx.read(rel) or ""
            after = blocks.upsert_rgkb(before, content)
            if not blocks.has_layout_blocks(before):          # legacy file: FU-05
                if name in cx.READONLY_AGENTS:
                    after = fusion.union_tools(after, cx.READ_TOOLS)
            if after != before:
                ctx.change(rel, "block", after)
            ctx.manage(rel, "legacy" if not blocks.has_layout_blocks(before) else "generated")
            ctx.managed[rel]["blocks"] = {"rgkb": hashlib.sha256(
                (blocks.get_block(after, "rgkb", "md") or "").encode("utf-8")).hexdigest()}
        for rel in cx.CI_SCRIPTS:
            current = ctx.read(rel)
            if current is None or blocks.has_layout_blocks(current):
                continue                                         # layout files get it by contribution
            new, warn = fusion.union_allowed_tools(current, contributes)
            ctx.warnings += ["%s: %s" % (rel, w) for w in warn]
            if new != current:
                ctx.change(rel, "merge", new)
    if hooks_mode in ("auto", "commit"):
        _plan_hooks(ctx, asset, commit=(hooks_mode == "commit"))
    options = {"no_wire": bool(no_wire), "hooks": hooks_mode or "off", "interpreter": interpreter}
    return {"action": "plan", "options": options, "contributes": contributes, "notes": notes,
            "tool_version": asset.TOOL_VERSION}


def _plan_hooks(ctx: Ctx, asset: Any, commit: bool) -> None:
    """CX-21: declared hooks that are absent are installed by the rgkb block."""
    res = Runner(cwd=str(ctx.root), timeout=10)(["git", "rev-parse", "--git-path", "hooks"])
    if not res.ok or not res.out.strip():
        return
    hooks = Path(res.out.strip())
    hooks = hooks if hooks.is_absolute() else ctx.root / hooks
    try:
        base = hooks.resolve().relative_to(ctx.root.resolve()).as_posix()
    except ValueError:
        ctx.warnings.append("git hooks folder lies outside the project: hooks not planned")
        return
    for name in asset.HOOK_NAMES + ((asset.COMMIT_HOOK,) if commit else ()):
        rel = "%s/%s" % (base, name)
        existing = ctx.read(rel)
        block = asset.hook_block(name)
        if existing is None:
            after = "#!/bin/sh\n\n" + block
        elif asset.HOOK_BEGIN in existing:
            after = asset.insert_hook_block(existing, block)
        else:
            after = existing.rstrip("\n") + "\n\n" + block
        if after != existing:
            ctx.change(rel, "local", after)


# ------------------------------------------------------------------- the plan

def _invariants(ctx: Ctx) -> None:
    """UP-03."""
    for rel, ch in ctx.changes.items():
        before, after, kind = ch["before"], ch["after"], ch["kind"]
        if before is None or after is None:
            continue
        if kind in ("header", "block", "local"):
            if blocks.strip_blocks(before).rstrip("\n") != blocks.strip_blocks(after).rstrip("\n"):
                raise Stop("out-of-block invariant violated for %s: nothing written" % rel)
        elif kind == "merge":
            if rel.endswith(".json"):
                ok = _superset(fusion.jsonc.loads(before).value, fusion.jsonc.loads(after).value)
            else:
                lines = set(l.strip() for l in after.splitlines())
                ok = all(l.strip() in lines for l in before.splitlines())
            if not ok:
                raise Stop("merge of %s modified or removed existing content: nothing written" % rel)
        elif kind == "stamp":
            continue


def _superset(before: Any, after: Any) -> bool:
    if isinstance(before, dict):
        return isinstance(after, dict) and all(k in after and _superset(v, after[k]) for k, v in before.items())
    if isinstance(before, list):
        return isinstance(after, list) and all(item in after for item in before)
    return before == after


def build_plan(root: Path, skill: str, repo: Dict[str, Any], core_dir: Optional[Path] = None,
               profiles_dir: Optional[Path] = None, params: Optional[Dict[str, Any]] = None,
               convert_all: bool = False, interpreter: Optional[str] = None,
               requested: Sequence[str] = (), by: str = "scout", now: Optional[datetime] = None,
               run: Any = None, assets: Optional[Path] = None) -> Dict[str, Any]:
    """UP-01, UP-02. Reads the project, never writes in it."""
    root = Path(root)
    core_dir = Path(core_dir) if core_dir else history.CORE_DIR
    if skill not in (SF, CI):
        raise UpgradeError("--skill must be %s or %s" % (SF, CI))
    data = stamp._read(root)                                          # noqa: SLF001
    resolved = stamp.resolve_full(root)
    equipped = bool(resolved["profiles"]) if skill == SF else CI in resolved["skills"]
    if not equipped and not (skill == SF and requested):
        raise Stop("project not equipped: run /%s" % ("software-factory" if skill == SF else "code-index"))
    installed = _installed(data, skill, root, core_dir)
    lag = _guard_direction(installed, repo, run) if equipped else {"status": "unknown", "commits": None}
    ctx = Ctx(root, skill, repo, now or datetime.now(), run)
    if skill == SF:
        out = _plan_sf(ctx, data, resolved, core_dir, profiles_dir, requested, by, params or {}, interpreter,
                       convert_all)
    else:
        out = _plan_ci(ctx, data, Path(assets) if assets else CODE_INDEX_ASSETS, interpreter)
    if out["action"] == "fill_params":
        return dict(out, skill=skill, changes=[], blocks_only=True, candidates=[])
    _invariants(ctx)
    stamp_change = _plan_stamp(ctx, data, resolved, skill, out, requested, by)
    if stamp_change is not None:
        ctx.change(stamp.STAMP_FILE.as_posix(), "stamp", stamp_change)
    changes = [{"file": c["file"], "kind": c["kind"], "op": "create" if c["before"] is None else
                "delete" if c["after"] is None else "modify", "before_sha": sha_text(c["before"]),
                "after_sha": sha_text(c["after"]), "after": c["after"]} for c in _ordered(ctx.changes)]
    gate = None
    if ctx.candidates:
        gate = {"id": CONVERT_GATE, "files": [c["file"] for c in ctx.candidates],
                "classes": {c["file"]: c["class"] for c in ctx.candidates}, "options": [KEEP_OPTION, CONVERT_OPTION],
                "default": KEEP_OPTION}
    plan = {"plan_version": 1, "action": "plan", "skill": skill, "repo": {k: repo.get(k) for k in
                                                                         ("commit", "describe", "dirty")},
            "profiles": out.get("profiles", resolved["profiles"]), "generation": resolved.get("generation"),
            "installed": installed, "lag": lag, "changes": changes,
            "blocks_only": all(c["kind"] in BLOCKS_ONLY for c in changes), "gate": gate,
            "legacy": sorted(rel for rel, m in ctx.managed.items() if m["kind"] == "legacy"),
            "notes": ctx.notes + out.get("notes", []), "warnings": ctx.warnings, "core": out.get("core"),
            "candidates": ctx.candidates, "forced": ctx.forced, "stamp_skill": skill}
    return plan


def _ordered(changes: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    stamp_rel = stamp.STAMP_FILE.as_posix()
    return [changes[r] for r in sorted(changes) if r != stamp_rel] + ([changes[stamp_rel]] if stamp_rel in changes else [])


def stamp_text_for(plan_ctx: Dict[str, Any]) -> str:
    return stamp.dumps(plan_ctx)


def _plan_stamp(ctx: Ctx, data: Optional[Dict[str, Any]], resolved: Dict[str, Any], skill: str,
                out: Dict[str, Any], requested: Sequence[str], by: str) -> Optional[str]:
    """HJ-07 to HJ-14: the stamp, written last; unchanged byte for byte when nothing changes."""
    old_entry = _stamp_entry(data, skill)
    repo = ctx.repo
    files = {rel: dict(m) for rel, m in sorted(ctx.managed.items())}
    for cand in ctx.candidates:
        files[cand["file"]] = {"kind": "legacy"}
    entry: Dict[str, Any] = {"repo_commit": repo.get("commit"), "repo_describe": repo.get("describe"),
                             "repo_dirty": repo.get("dirty")}
    entry["core_version" if skill == SF else "tool_version"] = out.get("core_version") or out.get("tool_version")
    entry["installed_at"] = old_entry.get("installed_at", ctx.now_s)
    entry["updated_at"] = old_entry.get("updated_at", ctx.now_s)
    entry["options"] = out["options"]
    if skill == SF:
        entry["params"] = out.get("params", {})
    else:
        entry["contributes"] = out.get("contributes", [])
    entry["files"] = _merge_files(old_entry.get("files") or {}, files, ctx)
    inferred = resolved.get("generation") if data is None else None
    migrations = ctx.migrations
    names = out.get("profiles", []) if skill == SF else []
    new = stamp.compose(data, skill, entry, names, by, ctx.now.date().isoformat(), migrations, inferred)
    if data is None or new != data:
        entry["updated_at"] = ctx.now_s
        new = stamp.compose(data, skill, entry, names, by, ctx.now.date().isoformat(), migrations, inferred)
    elif ctx.changes:
        entry["updated_at"] = ctx.now_s
        new = stamp.compose(data, skill, entry, names, by, ctx.now.date().isoformat(), migrations, inferred)
    errors = stamp.validate_data(new)
    if errors:
        raise UpgradeError("the stamp would be invalid: %s" % "; ".join(errors))
    if data is not None and new == data:
        return None
    return stamp.dumps(new)


def _merge_files(old: Dict[str, Any], files: Dict[str, Any], ctx: Ctx) -> Dict[str, Any]:
    """The stamp keeps the entries of files this run does not manage (other profiles)."""
    merged = {rel: v for rel, v in old.items() if rel not in files and rel not in ctx.managed}
    merged.update(files)
    return {rel: merged[rel] for rel in sorted(merged)}


# ----------------------------------------------------------------- diff, plan files

def make_diff(changes: Sequence[Dict[str, Any]], before_of: Callable[[str], Optional[str]]) -> str:
    """UP-04: unified diff, relative paths with /, applicable by `git apply`."""
    out: List[str] = []
    for ch in changes:
        before, after = before_of(ch["file"]), ch.get("after")
        a = before.splitlines(keepends=True) if before is not None else []
        b = after.splitlines(keepends=True) if after is not None else []
        old = "a/" + ch["file"] if before is not None else "/dev/null"
        new = "b/" + ch["file"] if after is not None else "/dev/null"
        out.append("diff --git a/%s b/%s\n" % (ch["file"], ch["file"]))
        if before is None:
            out.append("new file mode 100644\n")
        elif after is None:
            out.append("deleted file mode 100644\n")
        for line in difflib.unified_diff(a, b, old, new, lineterm="\n"):
            out.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return "".join(out)


def write_plan(root: Path, plan: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, str]:
    """UP-01: the only writes of a plan, under .harness/runs/."""
    from datetime import timedelta
    root = Path(root)
    moment = (now or datetime.now()).replace(microsecond=0)
    runs_dir = root / runs.RUNS_DIR
    runs_dir.mkdir(parents=True, exist_ok=True)
    while (runs_dir / ("upgrade-%s.plan.json" % moment.strftime("%Y%m%d-%H%M%S"))).exists():
        moment += timedelta(seconds=1)
    name = "upgrade-%s" % moment.strftime("%Y%m%d-%H%M%S")
    diff = make_diff(plan["changes"], lambda rel: fusion._read_text(root / rel))      # noqa: SLF001
    runs.write_atomic(runs_dir / (name + ".diff"), diff)
    runs.write_atomic(runs_dir / (name + ".plan.json"), json.dumps(plan, ensure_ascii=False, indent=1) + "\n")
    return {"plan": "%s/%s.plan.json" % (runs.RUNS_DIR.as_posix(), name), "diff": "%s/%s.diff" % (runs.RUNS_DIR.as_posix(), name)}


def public(plan: Dict[str, Any]) -> Dict[str, Any]:
    """The plan without the file contents."""
    out = {k: v for k, v in plan.items() if k not in ("candidates",)}
    out["changes"] = [{k: v for k, v in c.items() if k != "after"} for c in plan["changes"]]
    return out


# ------------------------------------------------------------------- apply

def _write_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".upgrade-tmp")
    with open(str(tmp), "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    fusion.keep_mode(path, tmp, text)
    os.replace(str(tmp), str(path))
    if ".git" in path.parts and "hooks" in path.parts:
        try:
            os.chmod(str(path), 0o755)
        except OSError:
            pass


def _gate_events(root: Path, answer: str, source: str) -> List[Dict[str, Any]]:
    decision = gates.decide(CONVERT_GATE, gates.read_policy(root))
    return [decision, dict(decision, phase="answer", answer=answer, source=source)]


def _patch_stamp(root: Path, plan: Dict[str, Any], changes: List[Dict[str, Any]],
                 entries: Dict[str, Any]) -> None:
    """A file converted on the user's answer is no longer `legacy` in the stamp."""
    rel = stamp.STAMP_FILE.as_posix()
    if changes and changes[-1]["file"] == rel:
        doc = json.loads(changes[-1]["after"])
        files = doc["skills"][plan["skill"]]["files"]
        files.update(entries)
        text = stamp.dumps(doc)
        changes[-1] = dict(changes[-1], after=text, after_sha=sha_text(text))
        return
    current = fusion._read_text(root / rel)                      # noqa: SLF001
    doc = json.loads(current)
    doc["skills"][plan["skill"]]["files"].update(entries)
    text = stamp.dumps(doc)
    changes.append({"file": rel, "kind": "stamp", "op": "modify", "before_sha": sha_text(current),
                    "after_sha": sha_text(text), "after": text})


def apply_plan(root: Path, plan_path: Path, decisions: Optional[Dict[str, Any]] = None,
               now: Optional[datetime] = None) -> Dict[str, Any]:
    """UP-05, UP-06, UP-10, CV-04."""
    root = Path(root)
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    changes = list(plan["changes"])
    answers = dict((decisions or {}).get("files", {}))
    wants = [c for c in plan.get("candidates", []) if answers.get(c["file"]) == "convert"]
    if not changes and not wants:
        return {"applied": 0, "files": [], "backup": None, "journal": None}
    (root / runs.RUNS_DIR).mkdir(parents=True, exist_ok=True)
    with runs.locked_file(root / LOCK):
        for ch in changes:
            if sha_file(root / ch["file"]) != ch["before_sha"]:
                raise Stop("upgrade plan is stale, the project changed since it was shown; plan again")
        gate_events: List[Dict[str, Any]] = []
        if plan.get("forced"):
            gate_events = _gate_events(root, CONVERT_OPTION, "user")
        elif plan.get("candidates"):
            gate_events = _gate_events(root, CONVERT_OPTION if wants else KEEP_OPTION,
                                       (decisions or {}).get("source", "headless"))
        if wants:
            tail = [changes.pop()] if changes and changes[-1]["kind"] == "stamp" else []
            for cand in wants:
                changes.append({"file": cand["file"], "kind": "convert", "op": "modify",
                                "before_sha": sha_file(root / cand["file"]), "after_sha": sha_text(cand["alt"]),
                                "after": cand["alt"]})
            changes += tail
            _patch_stamp(root, plan, changes, {c["file"]: c["alt_entry"] for c in wants})
        moment = (now or datetime.now()).replace(microsecond=0)
        backup = "%s/%s/backup" % (runs.RUNS_DIR.as_posix(), Path(plan_path).name.replace(".plan.json", ""))
        done: List[Tuple[Path, Optional[bytes]]] = []
        try:
            for ch in changes:
                path = root / ch["file"]
                original = path.read_bytes() if path.is_file() else None
                if original is not None:
                    saved = root / backup / ch["file"]
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    saved.write_bytes(original)
                if ch["after"] is None:
                    os.remove(str(path))
                else:
                    _write_file(path, ch["after"])
                done.append((path, original))
        except BaseException:
            for path, original in reversed(done):
                if original is None:
                    if path.exists():
                        os.remove(str(path))
                else:
                    path.write_bytes(original)
            raise
        jpath = root / runs.RUNS_DIR / journal.journal_name(plan["skill"], moment)
        for event in gate_events:
            journal.append(jpath, event)
        for ch in changes:
            journal.append(jpath, {"event": "upgrade", "file": ch["file"], "kind": ch["kind"],
                                   "from": ch["before_sha"], "to": ch["after_sha"]})
    kept = [c["file"] for c in plan.get("candidates", []) if c not in wants]
    return {"applied": len(changes), "files": [{"file": c["file"], "kind": c["kind"]} for c in changes],
            "backup": backup, "journal": jpath.relative_to(root).as_posix(), "legacy": kept}
