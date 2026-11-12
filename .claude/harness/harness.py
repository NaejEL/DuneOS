#!/usr/bin/env python3
# harness - single CLI of the deterministic core of the claude-skills skills.
#
# Usage: <PY> harness.py <subcommand> ...   (see --help of each group)
# Output: one JSON object on stdout; stop message on stderr.
# Codes: 0 success or decision returned, 1 explicit stop, 2 usage error or invalid bound.
#
# Standard library only, Python >= 3.9.

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# D1: no bytecode, even when called without -B (versioned copy under .claude/harness/).
sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from hcore import CORE_VERSION  # noqa: E402
from hcore import (aliases, audit, autoupgrade, automerge, batch, blocks, bs, chain, counters, cx,  # noqa: E402
                   doctor, factory, findings, fusion, gates, ghflow, history, hooks_install, interp, journal, lab,
                   labrun, loop, models, plugin, prioritize, profiles, registry, runs, scan, scope, scout, shippr, specfile,
                   stack, stamp, stops, tshirt, unified, upgrade, verdicts, version, worktrees)
from hcore import proc  # noqa: E402
from hcore import queue as queue_mod  # noqa: E402
from hcore.proc import Runner  # noqa: E402


class Usage(Exception):
    """Usage or bound error: exit 2."""


def emit(obj: Any) -> None:
    active = proc.overrides(warn=False)
    if active and isinstance(obj, dict):
        obj = dict(obj, hcore_test_mode={"overrides": active})
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=1) + "\n")


TRANSIT_MISSING = ("transit file %s was already consumed or never written: "
                   "rewrite it with Write before this call")


def _input_path(value: str) -> Path:
    """Path of an input file. A missing file under .harness/runs/ is a transit
    file already consumed (or never written): explicit usage error."""
    path = Path(value)
    if not path.is_file():
        parts = path.resolve().parts
        if any(parts[i:i + 2] == (".harness", "runs") for i in range(len(parts) - 1)):
            raise Usage(TRANSIT_MISSING % value)
    return path


def read_output(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if value == "-":
        return sys.stdin.buffer.read().decode("utf-8", "replace")
    return _input_path(value).read_text(encoding="utf-8", errors="replace")


def read_file(value: str) -> str:
    """Text written by the Write tool (never stdin, never the shell)."""
    return _input_path(value).read_text(encoding="utf-8-sig", errors="replace")


def _one_line(option: str, text: str) -> str:
    text = text.strip()
    if not text or "\n" in text or "\r" in text:
        raise Usage("%s expects a single non-empty line" % option)
    return text


def _count_arg(name: str, value: Optional[str], warnings: List[str]) -> Optional[int]:
    """Integer >= 0, or None (absent or invalid: reported, never blocking)."""
    if value is None or value.strip().lower() in ("", "null", "none"):
        return None
    try:
        n = int(value.strip())
    except ValueError:
        n = -1
    if n < 0:
        warnings.append("%s invalid (%r): logged as null" % (name, value))
        return None
    return n


def _run_path(value: str) -> Path:
    path = Path(value)
    if not runs.state_path(path).exists():
        raise Usage("unknown run: %s" % value)
    return path


def _measure(state: Dict[str, Any], args: argparse.Namespace, slot: str) -> Dict[str, Any]:
    """Journal measurements, taken BEFORE any write. A measurement defect
    (duration missing without 'begin', invalid value) never blocks the skill:
    the value is logged as null and reported in 'journal_warnings'."""
    warnings: List[str] = []
    tokens = _count_arg("--tokens", args.tokens, warnings)
    given = _count_arg("--duration-ms", args.duration_ms, warnings)
    try:
        duration: Optional[int] = runs.duration_ms(state, slot, given)
    except ValueError as exc:
        warnings.append("%s: logged as null" % exc)
        duration = None
    return {"duration_ms": duration, "tokens": tokens, "warnings": warnings}


def _model(args: argparse.Namespace, state: Dict[str, Any], role: str) -> str:
    """Model logged for an agent: --model if given, otherwise the model
    resolved for its role at start (MD-07), inherit for an older run."""
    if args.model is not None:
        return args.model
    return state.get("models", {}).get(role, "inherit")


def _log(run: Path, args: argparse.Namespace, agent: str, measure: Dict[str, Any],
         fields: Dict[str, Any], decision: Dict[str, Any], model: str, **extra: Any) -> None:
    record = journal.make_record(
        agent=agent, model=model, duration_ms=measure["duration_ms"],
        iteration=fields["iteration"], verdict=fields["verdict"],
        produced=fields.get("produced", 0), validated=fields.get("validated", 0),
        rejected=fields.get("rejected", 0), tokens=measure["tokens"], **extra)
    record, fallbacks = journal.sanitize(record)
    journal.append(run, record)
    warnings = measure["warnings"] + fallbacks
    if warnings:
        decision["journal_warnings"] = warnings


def _existing_run(args: argparse.Namespace) -> Optional[Path]:
    """Run of the command, if it exists: the command then holds its lock for
    the whole read -> modify -> write cycle (concurrent calls serialized)."""
    value = getattr(args, "run", None)
    if not value:
        return None
    try:
        return Path(value) if runs.state_path(Path(value)).exists() else None
    except ValueError:
        return None


# ------------------------------------------------------------------- generic

def cmd_version(args: argparse.Namespace) -> int:
    emit(version.report(HERE))                                  # VS-01, VS-02
    return 0


def cmd_core_history(args: argparse.Namespace) -> int:
    """CO-01: --rebuild regenerates history.json from git; --check is exit 0 when the tree is an entry."""
    if args.rebuild == args.check:
        raise Usage("core history expects --rebuild or --check")
    if args.rebuild:
        entries = history.rebuild(HERE, HERE.parent.parent)
        with open(str(HERE / history.HISTORY), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(history.dumps(entries))
        emit({"entries": len(entries), "file": history.HISTORY})
        return 0
    result = history.check(HERE)
    emit(result)
    if not result["ok"]:
        raise ghflow.Stop("skills/_core differs from history.json (rebuild it): %s" % ", ".join(result["files"]))
    return 0


def cmd_interp(args: argparse.Namespace) -> int:
    found = interp.detect(timeout=args.timeout)
    emit(found or {"command": None})
    return 0 if found else 1


def cmd_journal_validate(args: argparse.Namespace) -> int:
    errors = journal.validate_file(Path(args.path))
    name_ok = bool(journal.NAME_RE.match(Path(args.path).name))
    emit({"path": args.path, "valid": not errors, "errors": errors, "name_conforms": name_ok})
    return 0 if not errors else 1


def cmd_journal_amend(args: argparse.Namespace) -> int:
    """Deferred completion of the last entry of a slot (measurements known
    after the call). A measurement defect is reported, never blocking."""
    run = _run_path(args.run)
    if args.duration_ms is None and args.tokens is None:
        raise Usage("journal amend expects --duration-ms and/or --tokens")
    warnings: List[str] = []
    duration = _count_arg("--duration-ms", args.duration_ms, warnings)
    tokens = _count_arg("--tokens", args.tokens, warnings)
    records = journal.read(run)
    amended, more = journal.amend(records, args.slot, duration, tokens)
    if amended:
        runs.write_atomic(run, journal.dumps(records))
    result: Dict[str, Any] = {"slot": args.slot, "amended": amended}
    if warnings + more:
        result["journal_warnings"] = warnings + more
    emit(result)
    return 0


def cmd_begin(args: argparse.Namespace) -> int:
    run = _run_path(args.run)
    state = runs.load_state(run)
    runs.mark_begin(state, args.slot)
    runs.save_state(run, state)
    emit({"slot": args.slot})
    return 0


def cmd_models_resolve(args: argparse.Namespace) -> int:
    try:
        emit(models.resolve_root(Path(args.root), models.roles_of(args.roles), args.models, args.aliases))
    except models.ModelsError as exc:
        raise Usage(str(exc))
    return 0


def cmd_models_write(args: argparse.Namespace) -> int:
    if args.preset is None and args.set is None:
        raise Usage("models write expects --preset and/or --set")
    try:
        emit(models.write_preset(Path(args.root), args.preset, args.set))
    except models.ModelsError as exc:
        raise Usage(str(exc))
    return 0



# ------------------------------------------------------------------ profiles

def _profiles_dir(value: Optional[str]) -> Optional[Path]:
    return Path(value) if value else None


def cmd_profile_validate(args: argparse.Namespace) -> int:
    errors = profiles.validate(Path(args.path))
    if errors:
        for e in errors:
            sys.stderr.write("ERROR: %s\n" % e)
        emit({"valid": False, "errors": errors})
        return 2
    emit({"valid": True, "errors": []})
    return 0


def cmd_profile_list(args: argparse.Namespace) -> int:
    emit({"profiles": profiles.summary(_profiles_dir(args.profiles_dir))})
    return 0


def cmd_profile_show(args: argparse.Namespace) -> int:
    """The data a skill needs to generate the files of a profile (EP-04)."""
    prof = profiles.get(args.name, _profiles_dir(args.profiles_dir))
    roles = []
    for role in prof["roles"]:
        roles.append(dict(role, mission_text=profiles.fragment(prof, role["mission"])))
    lenses = [dict(l, fragment_text=profiles.fragment(prof, l["fragment"]),
                   critical_text=profiles.fragment(prof, l["critical_definition"])) for l in prof["lenses"]]
    sets = [dict(s_, **{k.replace("fragment", "text"): profiles.fragment(prof, s_[k])
                        for k in ("fragment", "map_fragment", "intro_fragment", "validator_fragment") if k in s_})
            for s_ in prof["lens_sets"]]
    out = {"name": prof["name"], "orchestrator": prof["orchestrator"], "ci": prof["ci"], "roles": roles,
           "artefacts": prof["artefacts"], "loop": prof["loop"], "model_presets": prof["model_presets"],
           "verifier": prof["verifier"], "lenses": lenses, "lens_sets": sets}
    for key, rel in prof["fragments"].items():
        out[key + "_text"] = profiles.fragment(prof, rel)
    emit(out)
    return 0


# --------------------------------------------------------------------- stamp

def cmd_stamp_add(args: argparse.Namespace) -> int:
    names = [n for n in (args.profiles or "").split(";") if n.strip()]
    if not names:
        raise Usage("stamp add expects --profiles p[;p]")
    emit(stamp.add(Path(args.root), [n.strip() for n in names], args.by, args.date))
    return 0


def cmd_stamp_resolve(args: argparse.Namespace) -> int:
    emit(stamp.resolve_full(Path(args.root)))
    return 0


def cmd_stamp_validate(args: argparse.Namespace) -> int:
    errors = stamp.validate_file(Path(args.file))
    emit({"valid": not errors, "errors": errors})
    if errors:
        raise Usage("; ".join(errors))
    return 0


def cmd_stamp_show(args: argparse.Namespace) -> int:
    emit(stamp.show(Path(args.root)))
    return 0


def cmd_stamp_compare(args: argparse.Namespace) -> int:
    emit(stamp.compare(Path(args.root), json.loads(read_file(args.verdict))))
    return 0


# --------------------------------------------------------------------- scout

def cmd_scout_signals(args: argparse.Namespace) -> int:
    result = scout.signals(Path(args.root))
    result["judgement"] = scout.judgement_criteria()
    emit(result)
    return 0


def cmd_scout_verdict(args: argparse.Namespace) -> int:
    root = Path(args.root)
    output = None
    try:
        if args.signals:
            try:
                output = json.loads(read_file(args.signals))
            except ValueError as exc:
                raise Usage("--signals is not valid JSON: %s" % exc)
            if not isinstance(output, dict):
                raise Usage("--signals expects a JSON object")
    finally:
        _remove_transit(root, args.signals)
    result = scout.verdict(root, output, args.scout_failed)
    moment = datetime.now()
    out_dir = root / runs.RUNS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / ("scout-%s.json" % moment.strftime("%Y%m%d-%H%M%S"))
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        resolved = models.resolve_readonly(root, ("harness.scout",), args.models)
    except models.ModelsError as exc:
        raise Usage(str(exc))
    if args.run:
        run = _run_path(args.run)
    else:
        run = runs.create(root, args.skill, {"scout": True})
    record = journal.make_record(agent="harness-scout", model=resolved["roles"]["harness.scout"]["model"],
                                 duration_ms=_count_arg("--duration-ms", args.duration_ms, []),
                                 iteration=0, verdict="DEAD" if args.scout_failed else "DONE",
                                 produced=len(output.get("signals", [])) if output else 0,
                                 tokens=_count_arg("--tokens", args.tokens, []))
    journal.append(run, journal.sanitize(record)[0])
    emit(dict(result, verdict_file=path.relative_to(root).as_posix(), run=runs.display(run),
              model=resolved["roles"]["harness.scout"]))
    return 0


# --------------------------------------------------------------------- audit (Phase 2)

def _active_for_audit(args: argparse.Namespace) -> List[str]:
    if args.profile:
        names = [n.strip() for n in args.profile.split(";") if n.strip()]
        known = profiles.discover()
        unknown = [n for n in names if n not in known]
        if unknown:
            raise Usage("unknown profile: %s (known: %s)" % (", ".join(unknown), ", ".join(known)))
        return names
    resolved = stamp.resolve(Path(args.root))
    if not resolved["profiles"]:
        raise Usage("no active profile: pass --profile or run the scout (gate audit.profiles)")
    return resolved["profiles"]


def cmd_audit_select(args: argparse.Namespace) -> int:
    """AD-03, AD-04, AD-09: lens selection, waves and caps."""
    names = _active_for_audit(args)
    known = profiles.discover()
    selected = [known[n] for n in names]
    try:
        lenses = profiles.select_lenses(selected, args.lenses, known)
        options = {"--max-specs": args.max_specs, "--max-findings": args.max_findings,
                   "--max-questions": args.max_questions}
        caps = profiles.caps(selected, options)
    except profiles.SelectionError as exc:
        raise Usage(str(exc))
    active_sets = {s["name"]: s for p in selected for s in p["lens_sets"]}
    lenses = [l for l in lenses if l["set"] in active_sets]
    emit({"profiles": names, "lenses": [l["name"] for l in lenses],
          "details": [{"name": l["name"], "profile": l["profile"], "set": l["set"], "prefix": l["prefix"],
                       "model_role": l["model_role"], "debt": l["debt"], "shape": l["shape"]} for l in lenses],
          "waves": profiles.waves(lenses), "caps": caps, "via": args.via})
    return 0


def cmd_audit_alias_args(args: argparse.Namespace) -> int:
    try:
        text = read_file(args.arguments_file) if args.arguments_file else ""
    finally:
        _remove_transit(Path(args.root), args.arguments_file)
    result = aliases.translate(args.skill, text)
    result["invoke"] = plugin.effective_name("audit", args.skill_dir)["name"]      # PL-02
    emit(result)
    return 0


def cmd_plugin_skill_name(args: argparse.Namespace) -> int:
    """PL-02: the name the Skill tool must use for a skill of this repository."""
    emit(plugin.effective_name(args.skill, args.skill_dir))
    return 0


def cmd_plugin_version_guard(args: argparse.Namespace) -> int:
    """PL-03: exit 1 when skills/ changed since the last version bump and version was not raised."""
    result = plugin.version_guard(Path(args.root))
    emit(result)
    if not result["ok"]:
        raise ghflow.Stop("skills/ changed since version %s was set in .claude-plugin/plugin.json: raise "
                          "`version` (the plugin cache serves a stale content otherwise): %s"
                          % (result["version"], ", ".join(result["files"][:10])))
    return 0


def cmd_gate_list(args: argparse.Namespace) -> int:
    emit({"gates": [{"id": g.id, "kind": g.kind, "default": g.default, "justification": g.justification,
                     "rationale": g.justification, "headless": g.headless, "stop_when": g.stop_when,
                     "dialogue": g.dialogue}
                    for g in gates.all_gates().values()]})
    return 0


def _stop(root: Path, skill: str, kind: str, reason: str, details: Optional[Dict[str, Any]] = None,
          run: Optional[Path] = None) -> None:
    """SX-02 and SX-05: the STOP file, the `stop` event, then the exit 1 with `STOP: <reason> (see <path>)`."""
    path = stops.write(root, skill, kind, dict(details or {}, reason=reason), run)
    raise ghflow.Stop(stops.message(reason, stops.rel(root, path)))


def _stop_request(exc: "stops.StopRequest", root: Path, skill: str, run: Optional[Path]) -> None:
    _stop(root, skill, exc.kind, str(exc), exc.details, run if run is not None else getattr(exc, "run", None))


def _policy(root: Path, flag: Optional[str]) -> Tuple[str, str]:
    """AP-03: (value, source); an invalid value is a usage error (exit 2)."""
    try:
        return gates.effective_policy(root, flag)
    except gates.GateError as exc:
        raise Usage(str(exc))


def _note_autonomy(run: Path, root: Path, flag: Optional[str]) -> str:
    """AP-03 and AP-04: the effective policy of a run, journaled once (`autonomy` event) and kept in its state."""
    value, source = _policy(root, flag)
    state = runs.load_state(run)
    if state.get("autonomy") != value:
        journal.append(run, {"event": "autonomy", "value": value, "source": source})
        state["autonomy"] = value
        runs.save_state(run, state)
    return value


def _inputs(pairs: Optional[List[str]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for item in pairs or []:
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            raise Usage("--input expects name=value, got %r" % item)
        out[key.strip()] = value.strip().lower() in ("1", "true", "yes")
    return out

# --------------------------------------------------------------------- gates

def _gate_run(args: argparse.Namespace, gate: gates.Gate) -> Optional[Path]:
    """Existing run of a gate (--run), or None: 'decide' then opens a new run
    of the gate's skill (JR-01), once the policy is known to be valid."""
    if args.run:
        run = _run_path(args.run)
        skill = runs.load_state(run).get("skill")
        if skill != gate.skill:
            raise Usage("gate %s belongs to %s, the run is a %s run" % (gate.id, gate.skill, skill))
        return run
    if args.gcmd == "answer":
        raise Usage("gate answer expects --run (the run returned by 'gate decide')")
    return None


def cmd_gate_decide(args: argparse.Namespace) -> int:
    gate = gates.get(args.id)
    run = _gate_run(args, gate)
    root = Path(runs.load_state(run)["root"]) if run is not None else Path(args.root)
    # GT-01: an invalid .claude/harness.json is refused BEFORE any run is opened
    # (no orphan run left behind by an exit 2).
    policy, _source = _policy(root, args.autonomy)
    dynamic = gates.stamp_ship_target(root) if gate.id == "ship-pr.target" and policy == "auto" else None
    event = gates.decide(gate.id, policy, ambiguous=args.ambiguous, inputs=_inputs(args.input), default=dynamic)
    if run is None:
        run = runs.create(root, gate.skill, {"gate_only": True})
    _note_autonomy(run, root, args.autonomy)
    journal.append(run, event)
    if event["decision"] == gates.STOP:
        if event.get("ambiguous"):
            _stop(root, gate.skill, "profile",
                  "gate %s: ambiguous proposal under autonomy auto: see %s; resume with /%s --profile <p>[;<p>]"
                  % (gate.id, args.verdict or "the scout-*.json verdict", gate.skill),
                  {"items": [], "profile": "<p>"}, run)
        if event.get("stop_result"):
            _stop(root, gate.skill, "gate", "gate %s: stop condition %s holds" % (gate.id, gate.stop_when), None, run)
        _stop(root, gate.skill, "gate", gates.stop_message(gate, "decision default without a default to apply"),
              None, run)
    emit(dict(event, options=list(gate.options), dialogue=gate.dialogue, run=runs.display(run)))
    return 0


def cmd_gate_answer(args: argparse.Namespace) -> int:
    gate = gates.get(args.id)
    run = _gate_run(args, gate)
    assert run is not None
    root = Path(runs.load_state(run)["root"])
    try:
        given = [x for x in (args.answer, args.answer_file) if x is not None]
        if int(args.headless) + int(args.default) + len(given) != 1:
            raise Usage("gate answer expects exactly one of --answer, --answer-file, --headless, --default")
        text = read_file(args.answer_file) if args.answer_file is not None else args.answer
    finally:
        _remove_transit(root, args.answer_file)
    source = "headless" if args.headless else "default" if args.default else "user"
    try:
        event = gates.answer(gate.id, gates.last_decision(journal.read(run), gate.id), source, text)
    except gates.OffOptions as exc:
        # A free "Other" answer to a closed gate never kills the run: nothing is
        # logged, the decision stays pending, the skill asks the question again.
        emit({"id": gate.id, "action": "reask", "reason": str(exc), "options": list(gate.options)})
        return 0
    journal.append(run, event)
    apply = gates.applied(gate.id, event)
    if apply == gates.STOP:
        _stop(root, gate.skill, "gate", gates.stop_message(gate, "no interaction possible (headless fallback: stop)"),
              None, run)
    emit({"id": gate.id, "action": "apply", "answer": event["answer"], "source": source, "apply": apply})
    return 0


def cmd_block(args: argparse.Namespace) -> int:
    path = Path(args.file)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if args.action == "get":
        emit({"content": blocks.get_block(text, args.name, args.style)})
        return 0
    if args.action == "upsert":
        new = blocks.upsert_block(text, args.name, read_output(args.content) or "", args.style)
    else:
        new = blocks.remove_block(text, args.name, args.style)
    changed = new != text
    if changed:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(new)
    emit({"file": args.file, "changed": changed})
    return 0


# ---------------------------------------------------------- software-factory

def cmd_sf_apply(args: argparse.Namespace) -> int:
    stacks = [s.strip() for s in (args.stacks or "").split(",") if s.strip()]
    if args.profiles:
        known = profiles.discover()
        names = [n.strip() for n in args.profiles.replace(",", ";").split(";") if n.strip()]
        unknown = [n for n in names if n not in known]
        if unknown:
            raise Usage("unknown profile: %s (known: %s)" % (", ".join(unknown), ", ".join(known)))
        merged = profiles.merged([known[n] for n in names], stacks, Path(args.root), args.test_command,
                                 args.test_windows)
        emit(fusion.apply_merged(Path(args.root), merged))
        return 0
    emit(fusion.apply(Path(args.root), stacks))
    return 0


def _remove_transit(root: Path, source: Optional[str]) -> None:
    """The transit file written by Write under <root>/.harness/runs/ is removed
    once consumed, the command succeeded or not: nothing is left if the skill
    stops, and a stale file is never reused by a later call."""
    if not source or source == "-":
        return
    path = Path(source).resolve()
    if (Path(root) / runs.RUNS_DIR).resolve() in path.parents and path.is_file():
        path.unlink()


def cmd_sf_install(args: argparse.Namespace) -> int:
    try:
        content = read_output(args.source)
        dest = Path(args.root) / args.dest
        if args.model_role is None:
            emit({args.dest: fusion.install_file(dest, content or "")})
            return 0
        try:
            # X1: the frontmatter reflects models.json, never the --models of a cycle.
            resolved = models.resolve_root(Path(args.root), (args.model_role,))
            value = resolved["roles"][args.model_role]["model"]
            result = fusion.install_agent(dest, content or "", value)
        except models.ModelsError as exc:
            raise Usage("%s: %s" % (args.dest, exc))
        emit({args.dest: result["state"], "model": value, "model_block": result["model_block"]})
    finally:
        _remove_transit(Path(args.root), args.source)
    return 0


def cmd_sf_args(args: argparse.Namespace) -> int:
    """EP-01: [--profile p[;p] | --rescan] [first need]; both options -> exit 2, unknown profile -> exit 2."""
    root = Path(args.root)
    try:
        text = read_file(args.arguments_file) if args.arguments_file else ""
    finally:
        _remove_transit(root, args.arguments_file)
    if args.via == "ai-lab":
        emit({"translated": upgrade.ailab_translate(text)})                     # EP-10
        return 0
    parsed = upgrade.parse_arguments(text)                                      # EP-08
    if parsed:
        emit(dict(parsed, profiles=[], rescan=False, need=""))
        return 0
    rest, forced = loop.extract_profile_option(text)
    rescan = re.search(r"(?:(?<=\s)|^)--rescan(?=\s|$)", rest) is not None
    if rescan:
        rest = re.sub(r"(?:(?<=\s)|^)--rescan(?=\s|$)", "", rest, count=1).strip()
    if forced is not None and rescan:
        raise Usage("--profile and --rescan cannot be used together")
    names = [n.strip() for n in (forced or "").split(";") if n.strip()]
    unknown = [n for n in names if n not in profiles.discover()]
    if unknown:
        raise Usage("unknown profile: %s (known: %s)" % (", ".join(unknown), ", ".join(profiles.discover())))
    emit({"profiles": names, "rescan": rescan, "need": rest})
    return 0


def cmd_sf_copy_core(args: argparse.Namespace) -> int:
    if args.force and not args.refresh:
        raise Usage("--force goes with --refresh only")
    if args.check:
        # Strict identity of the copy with this core, on demand only.
        result = fusion.check_core_copy(HERE, Path(args.root))
        emit(dict(result, core_version=CORE_VERSION))
        if not result["identical"]:
            raise ghflow.Stop(".claude/harness/ differs from the core: %d different, %d missing, %d extra; "
                              "refresh it with 'sf copy-core --refresh'"
                              % (len(result["differs"]), len(result["missing"]), len(result["extra"])))
        return 0
    if args.refresh:
        result = fusion.refresh_core(HERE, Path(args.root), force=args.force)
        emit(dict(result, core_version=CORE_VERSION))
        if not result["refreshed"]:
            raise ghflow.Stop("%s: nothing refreshed, nothing touched" % result["reason"])
        return 0
    result = fusion.copy_core(HERE, Path(args.root))
    emit(dict(result, core_version=CORE_VERSION))
    if not result["copied"]:
        raise ghflow.Stop("core not copied: .claude/harness/ holds a different core (customized or another "
                          "version): %s; nothing was copied, the generated factory-run would not work. "
                          "Restore or remove .claude/harness/, then run the installation again, "
                          "or update the copy with /software-factory --upgrade."
                          % ", ".join(result["blocked_by"]))
    return 0


# ------------------------------------------------------------ upgrade, doctor

def _repo_path(args: argparse.Namespace) -> Dict[str, Any]:
    """The version of the repository that holds this core (VS-01), or the manifest of a copy (VS-02)."""
    repo = dict(version.report(HERE)["repo"])
    repo.setdefault("path", str(HERE.parent.parent))
    return repo


def cmd_upgrade_plan(args: argparse.Namespace) -> int:
    root = Path(args.root)
    params: Dict[str, Any] = {}
    if args.params_file:
        try:
            params = json.loads(read_file(args.params_file))
        finally:
            _remove_transit(root, args.params_file)
        if not isinstance(params, dict):
            raise Usage("--params-file must hold a JSON object")
    requested = [n.strip() for n in (args.profiles or "").split(";") if n.strip()]
    plan = upgrade.build_plan(root, args.skill, _repo_path(args), HERE, None, params, args.convert,
                              args.interpreter, requested, args.by)
    if plan["action"] == "fill_params":
        emit(plan)
        return 0
    files = upgrade.write_plan(root, plan)
    emit(dict(upgrade.public(plan), **files))
    return 0


def cmd_upgrade_apply(args: argparse.Namespace) -> int:
    root = Path(args.root)
    decisions = None
    if args.decisions_file:
        try:
            decisions = json.loads(read_file(args.decisions_file))
        finally:
            _remove_transit(root, args.decisions_file)
    emit(upgrade.apply_plan(root, root / args.plan, decisions))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    root = Path(args.root)
    if args.action == "auto-upgrade":
        if not args.plan:
            raise Usage("doctor auto-upgrade expects --plan")
        return cmd_doctor_auto_upgrade(args)
    if args.action == "scan":
        rows = scan.scan(root, scan.parse_depth(args.depth), _repo_path(args), HERE)
        for row in rows:
            sys.stdout.write(json.dumps(row, ensure_ascii=False) + "\n")
        return 0
    if args.depth is not None:
        raise Usage("--depth goes with 'doctor scan' only")
    rep = doctor.report(root, _repo_path(args), HERE)
    rep["proposal"] = doctor.proposal(rep, gates.read_policy(root)) if rep["equipped"] != "none" else None
    if not args.no_journal:
        rep["journal"] = doctor.write_journal(root, rep)
    emit(rep)
    return 0


# --------------------------------------------------------------- factory-run

def cmd_fr_start(args: argparse.Namespace) -> int:
    root = Path(args.root)
    arguments = ""
    try:
        if args.arguments_file:
            arguments = read_file(args.arguments_file)
    finally:
        # The transit file is removed once read, the command succeeded or not.
        _remove_transit(root, args.arguments_file)
    try:
        # X6: --models "role=model;..." taken out of $ARGUMENTS, for this cycle only.
        rest, override = models.extract_option(arguments)
    except models.ModelsError as exc:
        raise Usage(str(exc))
    try:
        rest, run_options = chain.extract_options(rest)      # CH-03, AP-03: before the routing, which reads a spec path
    except chain.ChainError as exc:
        raise Usage(str(exc))
    # LP-05, LP-06: the input is routed to a profile of the stamp.
    stamped = stamp.resolve(root)
    instance = args.orchestrator if args.orchestrator != "factory-run" else None
    try:
        routed = loop.route(rest, root, stamped["profiles"], instance, forced_profile=args.profile)
    except loop.LoopUsage as exc:
        raise Usage(str(exc))
    except loop.LoopStop as exc:
        raise ghflow.Stop(str(exc))
    if routed["gate"]:
        emit({"profile": None, "gate": routed["gate"], "candidates": routed["candidates"],
              "default": routed["default"], "need": routed["need"], "source": stamped["source"]})
        return 0
    prof = profiles.get(routed["profile"])
    rest = loop.extract_profile_option(rest)[0]
    flag = args.autonomy or run_options["autonomy"]
    lab_machine = loop.machine_of(prof) == "lab"
    try:
        resolved = models.resolve_root(root, tuple(prof["model_roles"]), override, "lab" if lab_machine else None)
    except models.ModelsError as exc:
        raise Usage(str(exc))
    assignment = {r: e["model"] for r, e in resolved["roles"].items()}
    if lab_machine:
        entered = labrun.entry(rest, root)
        state = labrun.new_state(assignment, entered["mode"])
        choice = {"mode": "build" if entered["mode"] == "build" else "plan", "protocol": entered["protocol"]}
    else:
        choice = factory.entry(rest, root)
        state = factory.new_state(assignment)
    state["profile"] = prof["name"]
    state.update(spec=choice.get("spec"), ship=run_options["ship"], no_ship=run_options["no_ship"],
                 deliver_only=run_options["deliver_only"], worktree=run_options["worktree"],
                 chained_from=run_options["chained_from"],
                 chain_id=run_options["chain_id"] or os.environ.get("HARNESS_CHAIN_ID") or None,
                 dirty_at_start=_git_dirty(root), untracked_at_start=scope.untracked_snapshot(root))
    run = runs.create(root, args.orchestrator, state)
    policy = _note_autonomy(run, root, flag)
    result = dict(choice, profile=prof["name"], orchestrator=args.orchestrator, run=runs.display(run),
                  models=resolved["roles"], autonomy=policy, dirty_at_start=state["dirty_at_start"],
                  options={k: v for k, v in run_options.items() if k != "autonomy"})
    if choice["mode"] == "plan":
        result["need"] = rest
    if resolved["warnings"]:
        result["model_warnings"] = resolved["warnings"]
    if choice["mode"] == "stop":
        # BQ-07: a blocked spec with open questions, or a delivered one, stops here.
        if choice["stop"] == "blocked":
            journal.append(run, {"event": "blocked", "questions": choice["questions"]})
            _stop(root, args.orchestrator, "blocked", "the spec %s has blocking question(s) without answer"
                  % choice["spec"], {"items": ["%s — %s" % (q["id"], q["text"]) for q in choice["questions"]],
                                     "spec": choice["spec"], "phase": "entry"}, run)
        _stop(root, args.orchestrator, "gate", "spec already delivered: %s" % choice["spec"],
              {"spec": choice["spec"], "phase": "entry",
               "command": "(nothing to run: the spec is delivered)"}, run)
    emit(result)
    return 0


def _profile_extra(state: Dict[str, Any]) -> Dict[str, Any]:
    """LP-07: every journal line of a routed run carries its profile."""
    return {"profile": state["profile"]} if state.get("profile") else {}


def cmd_fr_record(args: argparse.Namespace) -> int:
    run = _run_path(args.run)
    state = runs.load_state(run)
    if state.get("profile") and loop.machine_of(profiles.get(state["profile"])) == "lab":
        return cmd_lr_record(args)
    measure = _measure(state, args, args.agent)
    decision, fields = factory.record(state, args.agent, read_output(args.output), args.dead)
    model = _model(args, state, factory.AGENT_ROLES[args.agent])
    extra = _profile_extra(state)
    if decision["action"] == "success" and state.get("spec"):
        # BQ-09: the spec is delivered at success, whatever the policy.
        try:
            done = specfile.set_role(Path(state["root"]) / state["spec"], "delivered")
            extra["spec_status"] = done["to"]
            decision["spec_status"] = done["to"]
        except (specfile.SpecError, OSError) as exc:
            decision["spec_status_warning"] = str(exc)
    elif decision["action"] == "fail":
        # SX-01: three iterations without approval, or an agent dead twice, are explicit stops.
        kind = "agent" if "died twice" in decision["reason"] else "iterations"
        _log(run, args, args.agent, measure, fields, decision, model, **extra)
        runs.save_state(run, state)
        emit(decision)
        _stop(Path(state["root"]), state.get("skill", "factory-run"), kind, decision["reason"],
              {"items": ["%s: %s" % (i.get("severity", "?"), i.get("description", i)) if isinstance(i, dict) else str(i)
                         for i in decision.get("issues", [])][:10], "spec": state.get("spec"), "phase": "verify"}, run)
    _log(run, args, args.agent, measure, fields, decision, model, **extra)
    runs.save_state(run, state)
    emit(decision)
    return 0


# ------------------------------------------------------------------- ship-pr

def _reject_duplicate_keys(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    seen: Dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise Usage("--not-fixed-file: finding %s given twice" % key)
        seen[key] = value
    return seen


def _ship_ctx(args: argparse.Namespace):
    run = _run_path(args.run)
    state = runs.load_state(run)
    return run, state, Runner(cwd=state["root"]), registry.Registry(runs.registry_path(run))


def cmd_sp_start(args: argparse.Namespace) -> int:
    try:
        rounds = counters.parse_rounds(args.rounds)
        themes = counters.parse_themes(args.themes)
    except counters.BoundsError as exc:
        raise Usage(str(exc))
    root = Path(args.root).resolve()
    adopted, previous = None, {}
    flag = args.autonomy
    if args.run:
        # Run opened by the ship-pr.target gate: its journal (gate events) is continued.
        adopted = _run_path(args.run)
        previous = runs.load_state(adopted)
        if previous.get("skill") != "ship-pr" or not previous.get("gate_only"):
            raise Usage("--run %s: start only continues a run opened by 'gate decide'" % args.run)
        root = Path(previous["root"])
    try:
        resolved = models.resolve_root(root, models.SHIPPR_ROLES, args.models)
    except models.ModelsError as exc:
        raise Usage(str(exc))
    run_cmd = Runner(cwd=str(root))
    info = ghflow.preflight_checks(run_cmd, args.target)
    excluded = ghflow.ensure_exclude(run_cmd, root)
    info.update(ghflow.worktree_and_repo(run_cmd))
    assignment = {r: e["model"] for r, e in resolved["roles"].items()}
    state = shippr.new_state(args.target, rounds, themes, assignment)
    # HK-05: the scope hooks only apply to a run whose state says `running`.
    state.update(branch=info["branch"], owner_repo=info["owner_repo"], spec=args.spec, status="running")
    # RG-04: the project registry is read once, when the run starts.
    state["project"] = {k: {"status": e["status"], "source": e["source"]}
                        for k, e in registry.ProjectRegistry(root).read().items()}
    if adopted is None:
        run = runs.create(root, "ship-pr", state)
    else:
        run = adopted
        runs.save_state(run, dict(state, skill="ship-pr", root=str(root), begun=previous.get("begun", {})))
    policy = _note_autonomy(run, root, flag)
    result = dict(info, run=runs.display(run), registry=runs.display(runs.registry_path(run)),
                  rounds=rounds, themes=themes, round=1, exclude_added=excluded, models=resolved["roles"],
                  project_findings=len(state["project"]), autonomy=policy, spec=args.spec)
    if resolved["warnings"]:
        result["model_warnings"] = resolved["warnings"]
    emit(result)
    return 0


def cmd_sp_sync(args: argparse.Namespace) -> int:
    run, state, run_cmd, _ = _ship_ctx(args)
    emit(ghflow.sync_upstream(run_cmd))
    return 0


def cmd_sp_pr(args: argparse.Namespace) -> int:
    run, state, run_cmd, _ = _ship_ctx(args)
    try:
        title = args.title
        if args.title_file is not None:
            if title is not None:
                raise Usage("--title and --title-file are exclusive")
            title = _one_line("--title-file", read_file(args.title_file))
        if (title is None) != (args.body_file is None):
            raise Usage("--title (or --title-file) and --body-file go together")
        body = read_output(args.body_file) if args.body_file else None
    finally:
        for source in (args.title_file, args.body_file):
            _remove_transit(Path(state["root"]), source)
    result = ghflow.find_or_create_pr(run_cmd, state, title, body)
    runs.save_state(run, state)
    emit(result)
    return 0


def cmd_sp_pr_state(args: argparse.Namespace) -> int:
    run, state, run_cmd, _ = _ship_ctx(args)
    emit(ghflow.pr_state(run_cmd, state["pr"]))
    return 0


def cmd_sp_diff(args: argparse.Namespace) -> int:
    run, state, run_cmd, _ = _ship_ctx(args)
    sys.stdout.write(ghflow.collect_diff(run_cmd, state))
    return 0


def cmd_sp_reviewer(args: argparse.Namespace) -> int:
    run, state, _, reg = _ship_ctx(args)
    measure = _measure(state, args, "reviewer:" + args.theme)
    decision, fields = shippr.record_reviewer(state, reg, args.theme, read_output(args.output), args.dead)
    model = _model(args, state, shippr.AGENT_ROLES[shippr.REVIEWER])
    _log(run, args, shippr.REVIEWER, measure, fields, decision, model, theme=args.theme)
    runs.save_state(run, state)
    emit(decision)
    return 0


def cmd_sp_validator(args: argparse.Namespace) -> int:
    run, state, _, reg = _ship_ctx(args)
    measure = _measure(state, args, "validator:" + args.finding)
    decision, fields = shippr.record_validator(state, reg, args.finding, read_output(args.output), args.dead)
    model = _model(args, state, shippr.AGENT_ROLES[shippr.VALIDATOR])
    _log(run, args, shippr.VALIDATOR, measure, fields, decision, model, finding=args.finding)
    runs.save_state(run, state)
    emit(decision)
    return 0


def cmd_sp_drop(args: argparse.Namespace) -> int:
    run, state, _, reg = _ship_ctx(args)
    try:
        reason = read_file(args.reason_file).strip() if args.reason_file else ""
    finally:
        _remove_transit(Path(state["root"]), args.reason_file)
    result = shippr.drop_duplicate(state, reg, args.finding, args.of, reason or None)
    runs.save_state(run, state)
    emit(result)
    return 0


def cmd_sp_comment(args: argparse.Namespace) -> int:
    run, state, run_cmd, _ = _ship_ctx(args)
    result = ghflow.post_comment(run_cmd, state, args.finding)
    runs.save_state(run, state)
    emit(result)
    return 0


def _commit_inputs(args: argparse.Namespace) -> Tuple[str, Dict[str, str], List[str]]:
    summary = args.summary or ""
    if args.summary_file is not None:
        if summary.strip():
            raise Usage("--summary and --summary-file are exclusive")
        summary = read_file(args.summary_file)
    if summary.strip():
        summary = _one_line("--summary-file" if args.summary_file is not None else "--summary", summary)
    entries: List[Tuple[str, str]] = []
    for item in args.not_fixed or []:
        fid, sep, reason = item.partition(":")
        if not sep or not reason.strip():
            raise Usage("--not-fixed expects ID:reason, got: %r" % item)
        entries.append((fid.strip(), reason.strip()))
    if args.not_fixed_file is not None:
        try:
            data = json.loads(read_file(args.not_fixed_file), object_pairs_hook=_reject_duplicate_keys)
        except ValueError as exc:
            raise Usage("--not-fixed-file is not valid JSON: %s" % exc)
        if not isinstance(data, dict):
            raise Usage('--not-fixed-file expects a JSON object {"<id>": "<reason>"}')
        for fid, reason in data.items():
            if not isinstance(reason, str) or not reason.strip() or not fid.strip():
                raise Usage("--not-fixed-file: %r needs a non-empty reason" % (fid,))
            entries.append((fid.strip(), reason.strip()))
    not_fixed: Dict[str, str] = {}
    for fid, reason in entries:
        if fid in not_fixed:
            raise Usage("finding %s given twice as not fixed (--not-fixed / --not-fixed-file)" % fid)
        not_fixed[fid] = reason
    trailers = [_one_line("--trailer", t) for t in args.trailer or []]
    if args.fixed and not summary.strip():
        raise Usage("--summary or --summary-file is required when a fix is committed")
    return summary, not_fixed, trailers


def cmd_sp_commit(args: argparse.Namespace) -> int:
    run, state, run_cmd, reg = _ship_ctx(args)
    try:
        pending = state.get("unpushed")
        if pending and pending["round"] == state["round"]:
            # Relaunch after a failed push: the recorded commit is pushed, the
            # inputs (whose transit files are already consumed) are not needed.
            summary, not_fixed, trailers = "", {}, []
        else:
            summary, not_fixed, trailers = _commit_inputs(args)
    finally:
        for source in (args.summary_file, args.not_fixed_file):
            _remove_transit(Path(state["root"]), source)
    result = ghflow.commit_round(run_cmd, state, reg, summary, args.fixed or [], not_fixed, trailers,
                                 checkpoint=lambda: runs.save_state(run, state))
    runs.save_state(run, state)
    emit(result)
    return 0


def cmd_sp_threads(args: argparse.Namespace) -> int:
    run, state, run_cmd, _ = _ship_ctx(args)
    result = ghflow.handle_threads(run_cmd, state, checkpoint=lambda: runs.save_state(run, state))
    runs.save_state(run, state)
    emit(result)
    return 0


def cmd_sp_end_round(args: argparse.Namespace) -> int:
    run, state, run_cmd, reg = _ship_ctx(args)
    head = run_cmd(["git", "rev-parse", "HEAD"])
    result = shippr.end_round(state, head.out.strip() if head.ok else None)
    if result["exit"] is not None:
        # RG-03: end of the review loop, the run registry is published to the
        # project BEFORE the final state is saved: a failed publication (lock
        # timeout, exit 1) leaves the round open, and 'end-round' run again
        # publishes (idempotent) then exits.
        result["project_registry"] = registry.ProjectRegistry(Path(state["root"])).publish(
            reg.last.values(), "ship-pr", Path(run).name)
    runs.save_state(run, state)
    emit(result)
    return 0


def _ship_settings(root: Path) -> Dict[str, Any]:
    try:
        data = json.loads((root / gates.POLICY_FILE).read_text(encoding="utf-8-sig"))
        ship = data.get("ship_pr")
    except (OSError, UnicodeDecodeError, ValueError, AttributeError):
        ship = None
    return ship if isinstance(ship, dict) else {}


def _sleeper():
    """SM-01 waits; the tests of the core skip them (HCORE_TEST_MODE=1 and HCORE_NO_SLEEP=1)."""
    if proc.test_mode() and os.environ.get("HCORE_NO_SLEEP") == "1":
        return lambda _s: None
    return time.sleep


def cmd_sp_merge(args: argparse.Namespace) -> int:
    run, state, run_cmd, _ = _ship_ctx(args)
    root = Path(state["root"])
    if args.auto:
        # SM-02 and SM-03: the five conditions, the comment, then the merge or not. Never a question.
        settings = _ship_settings(root)
        in_worktree = worktrees.entry_of(run_cmd, root, root) is not None
        wait_min = float(settings.get("ci_wait_min", automerge.DEFAULT_CI_WAIT_MIN))
        result = automerge.run_auto(run_cmd, root, state, list(settings.get("protected") or []), in_worktree,
                                    wait_min, _sleeper())
        journal.append(run, dict({"event": "merge_decision", "merged": bool(result["merged"]),
                                  "conditions": {k: v["ok"] for k, v in result["conditions"].items()}},
                                 **({"refusal": result["refusal"]} if result.get("refusal") else {})))
        runs.save_state(run, state)
        emit(result)
        return 0
    if args.check and args.conditions:
        # SM-05: under `gate` the summary shows the five conditions, for information.
        settings = _ship_settings(root)
        info = automerge.evaluate(run_cmd, state, list(settings.get("protected") or []),
                                  0.0, _sleeper())
        result = ghflow.merge(run_cmd, state, True)
        result["conditions"] = info["conditions"]
        emit(result)
        return 0
    result = ghflow.merge(run_cmd, state, args.check)
    runs.save_state(run, state)
    emit(result)
    return 0


# ------------------------------------------------------------------ findings

def _findings_file(value: str) -> List[Any]:
    try:
        return findings.load_findings(read_file(value))
    except ValueError as exc:
        raise Usage("%s is not valid JSON: %s" % (value, exc))


def _valid_findings(value: str) -> List[Dict[str, Any]]:
    """FD-01 findings of a file; a non-conforming finding is a usage error."""
    items = _findings_file(value)
    for f in items:
        errors, _ = findings.validate(f)
        if errors:
            raise Usage("%s: %s" % (value, "; ".join(errors)))
    return items


def _one_finding(value: str) -> Dict[str, Any]:
    items = _valid_findings(value)
    if len(items) != 1:
        raise Usage("%s must hold exactly one finding" % value)
    return items[0]


def cmd_finding_validate(args: argparse.Namespace) -> int:
    report = findings.validate_text(read_file(args.path))
    emit(dict(report, path=args.path))
    return 0 if report["valid"] else 1


def cmd_finding_adapt(args: argparse.Namespace) -> int:
    try:
        data = json.loads(read_file(args.input))
    except ValueError as exc:
        raise Usage("%s is not valid JSON: %s" % (args.input, exc))
    if isinstance(data, dict) and "id" not in data:
        data = data.get("findings", data.get("issues", data))
    items = data if isinstance(data, list) else [data]
    ids = [x.strip() for x in (args.ids or "").split(",") if x.strip()]
    if ids and len(ids) != len(items):
        raise Usage("--ids gives %d ids for %d findings" % (len(ids), len(items)))
    out = [findings.adapt(args.format, obj, ids[i] if ids else None, args.axis, args.source)
           for i, obj in enumerate(items)]
    emit({"format": args.format, "findings": out})
    return 0


def cmd_finding_verdict(args: argparse.Namespace) -> int:
    try:
        emit(dict(findings.parse_verdict(read_output(args.output), args.source), usable=True))
    except verdicts.UnusableOutput as exc:
        # FD-06: an unusable output is a dead agent, for the skill to relaunch.
        emit({"usable": False, "reason": "unusable output: %s" % exc})
    return 0


def cmd_finding_apply(args: argparse.Namespace) -> int:
    f = _one_finding(args.finding)
    try:
        parsed = findings.parse_verdict(read_output(args.verdict), args.source)
    except verdicts.UnusableOutput as exc:
        raise Usage("unusable verdict: %s" % exc)
    emit({"findings": findings.apply_verdict(f, parsed)})
    return 0


def cmd_finding_spec(args: argparse.Namespace) -> int:
    f = _one_finding(args.finding)
    try:
        sections = json.loads(read_file(args.sections))
    except ValueError as exc:
        raise Usage("--sections is not valid JSON: %s" % exc)
    if not isinstance(sections, dict):
        raise Usage("--sections expects a JSON object")
    text = findings.to_spec(f, sections, args.prefix, args.number)
    emit({args.dest: fusion.install_file(Path(args.root) / args.dest, text)})
    return 0


def cmd_finding_note(args: argparse.Namespace) -> int:
    emit(findings.to_note(_one_finding(args.finding)))
    return 0


def cmd_finding_forbidden(args: argparse.Namespace) -> int:
    found = {}
    for path in args.paths:
        hits = findings.check_forbidden(read_file(path))
        if hits:
            found[path] = hits
    emit({"clean": not found, "hits": found})
    if found:
        raise ghflow.Stop("forbidden string ('Status:' followed by the ready status of an artefact) in: %s"
                          % ", ".join(sorted(found)))
    return 0


# ------------------------------------------------------------------ registry

def cmd_registry_publish(args: argparse.Namespace) -> int:
    run = _run_path(args.run)
    state = runs.load_state(run)
    reg = registry.Registry(runs.registry_path(run))
    emit(registry.ProjectRegistry(Path(state["root"])).publish(
        reg.last.values(), args.source or state["skill"], Path(run).name, args.deliverable))
    return 0


def cmd_registry_check(args: argparse.Namespace) -> int:
    project = registry.ProjectRegistry(Path(args.root))
    entries = project.read()
    dismissed, history, new = [], [], []
    for f in _valid_findings(args.file):
        look = project.lookup(registry.project_key(f), entries)
        item = {"id": f["id"], "key": look["key"], "previous": look["previous"]}
        if look["dismiss"]:
            dismissed.append(item)
        elif look["previous"]:
            history.append(item)
        else:
            new.append(f["id"])
    emit({"dismissed": dismissed, "history": history, "new": new})
    return 0


# -------------------------------------------------------------- stack, tshirt

def cmd_stack_detect(args: argparse.Namespace) -> int:
    root = Path(args.root)
    if not root.is_dir():
        raise ghflow.Stop("the path %s does not exist" % args.root)
    emit(stack.survey(root))
    return 0


def cmd_stack_journal(args: argparse.Namespace) -> int:
    emit(stack.journal_bounds(Path(args.file)))
    return 0


def cmd_tshirt(args: argparse.Namespace) -> int:
    items = tshirt.ordered(_valid_findings(args.file), args.order)
    result: Dict[str, Any] = {"order": [f["id"] for f in items], "quick_wins": tshirt.quick_wins(items)}
    if args.order == "VIB":
        result["raised_criticals"] = tshirt.raised_criticals(items)
    if args.grouping:
        result["specs"] = tshirt.plan_specs(items, args.grouping)
    if args.cap is not None:
        result["cap"] = tshirt.apply_cap(result.get("specs") or result["order"], args.cap)
    if args.load_by:
        result["load"] = tshirt.load(items, args.load_by)
    emit(result)
    return 0


# -------------------------------------------------------------------- audits

def _audit_ctx(args: argparse.Namespace):
    run = _run_path(args.run)
    state = runs.load_state(run)
    if "audit_skill" not in state:
        raise Usage("not an audit run: %s" % args.run)
    return run, state, audit.profile(state["audit_skill"]), registry.Registry(runs.registry_path(run))


def _json_file(option: str, value: str) -> Any:
    try:
        return json.loads(read_file(value))
    except ValueError as exc:
        raise Usage("%s is not valid JSON: %s" % (option, exc))


def cmd_audit_start(args: argparse.Namespace) -> int:
    """AU-01 to AU-03, AU-09, AU-12: arguments, git probe, resume, run opened
    in the audited repository (X5: journal under its .harness/runs/)."""
    p = audit.profile(args.skill)
    try:
        arguments = read_file(args.arguments_file) if args.arguments_file else ""
    finally:
        _remove_transit(Path(args.root), args.arguments_file)
    parsed = audit.parse_arguments(args.skill, arguments, Path(args.root))
    if args.only:
        # AD-03: /audit selects single lenses; the engine selects units (an axis may be partial).
        try:
            wanted = profiles.split_list(args.only)
        except profiles.SelectionError as exc:
            raise Usage("--only: %s" % exc)
        outside = [w for w in wanted if w not in parsed["auditors"]]
        if outside:
            raise Usage("--only: lens not in this audit: %s" % ", ".join(outside))
        parsed["auditors"] = [a for a in parsed["auditors"] if a in wanted]
        parsed["units"] = [u for u in parsed["units"]
                           if any(x.unit == u and x.name in wanted for x in p.auditors)]
    repo = Path(parsed["repo"])
    try:
        # X5: an audit writes nothing but its allowed paths: no models.json migration.
        resolved = models.resolve_readonly(repo, p.roles, parsed["models"], p.aliases)
    except models.ModelsError as exc:
        raise Usage(str(exc))
    deliverable = repo / p.deliverable
    resumed = None
    imports: List[Dict[str, str]] = []
    import_warnings: List[str] = []
    if args.unified:
        # MG-01 to MG-03: AUDIT.md first, then the previous deliverable if it was never imported.
        found = unified.resume(repo, args.skill)
        resumed, imports, import_warnings = found["parsed"], found["imports"], found["warnings"]
    elif deliverable.is_file():
        resumed = audit.parse_deliverable(args.skill, deliverable.read_text(encoding="utf-8"))
    run_cmd = Runner(cwd=str(repo))
    git_absent = audit.probe_git(run_cmd, repo)
    # AU-12: with GIT_ABSENT, no git command afterwards (no exclusion either).
    excluded = None if git_absent else registry.git_exclude(
        repo, audit.RUN_EXCLUDE + registry.PROJECT_EXCLUDE, run_cmd)
    project = {k: {"status": e["status"], "source": e["source"]}
               for k, e in registry.ProjectRegistry(repo).read().items()}
    factory_here = (repo / ".claude" / "skills" / "factory-run" / "SKILL.md").is_file()
    assignment = {r: e["model"] for r, e in resolved["roles"].items()}
    state = audit.new_state(args.skill, parsed, git_absent, factory_here, project, resumed, assignment)
    state["unified"] = bool(args.unified)
    state["imports"] = imports
    run = runs.create(repo, args.skill, state)
    reg = registry.Registry(runs.registry_path(run))
    count = audit.register_resumed(resumed, reg) if resumed else 0
    auditors = []
    lab_here = audit.lab_present(repo) if args.skill == "ai-lab-audit" else False
    for a in p.auditors:
        if a.name in parsed["auditors"]:
            role = audit.auditor_role(p, a.name)
            auditors.append(dict(resolved["roles"][role], auditor=a.name, unit=a.unit, prefix=a.prefix, role=role,
                                 subagent_type=audit.subagent_type(p, a.name, lab_here)))
    result = {"run": runs.display(run), "repo": str(repo), "skill": args.skill, "units": parsed["units"],
              "auditors": auditors, "validator": dict(resolved["roles"][p.validator_role], role=p.validator_role),
              "cap": parsed["cap"], "git_absent": git_absent, "exclude_added": excluded, "factory": factory_here,
              "lab": lab_here,
              "resumed": None if resumed is None else {"entries": count, "numbering": resumed["numbering"],
                                                       "ids": audit.resumed_ids(resumed)},
              "project_findings": len(project), "allowed_writes": audit.allowed_writes(args.skill)}
    if args.unified:
        result["imports"] = imports
        result["allowed_writes"] = [unified.DELIVERABLE] + result["allowed_writes"][1:]
    if import_warnings:
        result["import_warnings"] = import_warnings
    if resolved["warnings"]:
        result["model_warnings"] = resolved["warnings"]
    emit(result)
    return 0


def cmd_audit_auditor(args: argparse.Namespace) -> int:
    run, state, p, reg = _audit_ctx(args)
    measure = _measure(state, args, "auditor:" + args.unit)
    peers = unified.cycle_peers(Path(state["repo"]), run) if state.get("unified") else None
    decision, fields = audit.record_auditor(state, reg, args.unit, read_output(args.output), args.dead, peers)
    model = _model(args, state, audit.auditor_role(p, args.unit))
    _log(run, args, "auditor", measure, fields, decision, model, unit=args.unit)
    runs.save_state(run, state)
    emit(decision)
    return 0


def cmd_audit_validator(args: argparse.Namespace) -> int:
    run, state, p, reg = _audit_ctx(args)
    measure = _measure(state, args, "validator:" + args.finding)
    decision, fields = audit.record_validator(state, reg, args.finding, read_output(args.output), args.dead)
    model = _model(args, state, p.validator_role)
    _log(run, args, "validator", measure, fields, decision, model, finding=args.finding)
    runs.save_state(run, state)
    emit(decision)
    return 0


def cmd_audit_next(args: argparse.Namespace) -> int:
    run, state, p, reg = _audit_ctx(args)
    emit(audit.next_step(state))
    return 0


def cmd_audit_consolidate(args: argparse.Namespace) -> int:
    run, state, p, reg = _audit_ctx(args)
    result = audit.consolidate(state)
    runs.save_state(run, state)
    ordered = [state["findings"][fid] for prefix, _ in p.numbering for fid in result["order"][prefix]]
    keys = ("id", "title", "severity", "size", "axis", "nature", "category", "depends_on", "status")
    # The resumed ids per section (vibecode-audit: an explicit order lists the
    # resumed attack lines too) are returned so that the skill need not reread
    # the deliverable.
    out = dict(result, findings=[{k: f.get(k) for k in keys} for f in ordered],
               quick_wins=tshirt.quick_wins(ordered), resumed_ids=audit.resumed_ids(state["resumed"]))
    if "VIB" in result["order"]:
        out["raised_criticals"] = tshirt.raised_criticals(ordered)
    emit(out)
    return 0


def cmd_audit_plan(args: argparse.Namespace) -> int:
    if args.run is None:
        return cmd_audit_select(args)
    if args.data_file is None:
        raise Usage("audit plan expects --data-file with --run")
    run, state, p, reg = _audit_ctx(args)
    data = _json_file("--data-file", args.data_file)     # read, not consumed: 'render' takes it later
    if not isinstance(data, dict):
        raise Usage("--data-file expects a JSON object")
    emit(audit.plan(state, data))
    runs.save_state(run, state)
    return 0


def cmd_audit_settle(args: argparse.Namespace) -> int:
    emit({"status": audit.settle_status(args.audit_date, args.entry_date)})
    return 0


def cmd_audit_themes(args: argparse.Namespace) -> int:
    emit(audit.gate_calls(args.skill, args.count))
    return 0


def cmd_audit_readjust(args: argparse.Namespace) -> int:
    run, state, p, reg = _audit_ctx(args)
    result = audit.readjust(state)
    runs.save_state(run, state)
    emit(result)
    return 0


def cmd_audit_render(args: argparse.Namespace) -> int:
    run, state, p, reg = _audit_ctx(args)
    try:
        data = _json_file("--data-file", args.data_file)
    finally:
        _remove_transit(Path(state["repo"]), args.data_file)
    if not isinstance(data, dict):
        raise Usage("--data-file expects a JSON object")
    text = audit.render(p.skill, audit.document(state, data))
    if state.get("unified"):
        # SO-05: the old deliverable is never written; the set block waits for 'audit assemble'.
        section = Path(state["repo"]) / runs.RUNS_DIR / ("section-%s.md" % p.skill)
        section.parent.mkdir(parents=True, exist_ok=True)
        with open(str(section), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(unified.strip_set_block(p.skill, text))
        state["section_file"] = section.name
        runs.save_state(run, state)
        emit({"section": section.name, "deliverable": unified.DELIVERABLE})
        return 0
    result = audit.write_text(p.skill, Path(state["repo"]), Path(state["repo"]) / p.deliverable, text)
    runs.save_state(run, state)
    emit(result)
    return 0


def cmd_audit_open(args: argparse.Namespace) -> int:
    """AD-12: the journal audit-<timestamp>.jsonl of /audit; `--via` is journaled (AX-04, E-A8)."""
    root = Path(args.root)
    run = runs.create(root, "audit", {"unified": True})
    if args.via:
        journal.append(run, {"event": "alias", "alias": args.via, "target": "audit"})
    emit({"run": runs.display(run), "via": args.via})
    return 0


def cmd_audit_assemble(args: argparse.Namespace) -> int:
    """SO-01, MG-02: AUDIT.md from the section blocks of the runs, in the order given."""
    repo = Path(args.root).resolve()
    blocks_: List[Tuple[str, str]] = []
    imports: List[Dict[str, str]] = []
    consumed: List[Path] = []
    for value in args.set_run:
        run = _run_path(value)
        state = runs.load_state(run)
        if not state.get("section_file"):
            raise Usage("run %s has no rendered section: call 'audit render' first" % value)
        path = repo / runs.RUNS_DIR / state["section_file"]
        blocks_.append((state["audit_skill"], read_file(str(path))))
        imports += state.get("imports", [])
        consumed.append(path)
    result = unified.assemble(repo, blocks_, args.date or datetime.now().strftime("%Y-%m-%d"),
                              [n for n in args.profiles.split(";") if n], [n for n in args.lenses.split(";") if n],
                              args.sampled, imports)
    for path in consumed:
        path.unlink()
    emit(result)
    return 0


def cmd_audit_spec(args: argparse.Namespace) -> int:
    run, state, p, reg = _audit_ctx(args)
    try:
        sections = _json_file("--sections", args.sections)
    finally:
        _remove_transit(Path(state["repo"]), args.sections)
    f = state["findings"].get(args.finding)
    if f is None or f["status"] != findings.CONFIRMED:
        raise Usage("finding %s is not a CONFIRMED finding of this run" % args.finding)
    if not isinstance(sections, dict):
        raise Usage("--sections expects a JSON object")
    emit(audit.write_spec(p.skill, Path(state["repo"]), f, sections, args.number, args.slug))
    return 0


def cmd_audit_publish(args: argparse.Namespace) -> int:
    run, state, p, reg = _audit_ctx(args)
    emit(audit.publish(state, reg, Path(run).name, args.deliverable))
    return 0


# ---------------------------------------------------------------- code-index

def _json_text(option: str, text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError as exc:
        raise Usage("%s is not valid JSON: %s" % (option, exc))


def cmd_cx_args(args: argparse.Namespace) -> int:
    try:
        text = read_file(args.arguments_file) if args.arguments_file else ""
    finally:
        _remove_transit(Path(args.root), args.arguments_file)
    parsed = upgrade.parse_arguments(text)                                      # CX-17
    if parsed:
        if "--reindex" in text.split():
            raise Usage("--upgrade excludes --reindex")
        emit(dict(parsed, reindex=False, no_wire=False, extra=""))
        return 0
    emit(cx.parse_arguments(text))
    return 0


def cmd_cx_rg(args: argparse.Namespace) -> int:
    emit(cx.probe_rg())
    return 0


def cmd_cx_survey(args: argparse.Namespace) -> int:
    emit(cx.survey(Path(args.root)))
    return 0


def cmd_cx_install(args: argparse.Namespace) -> int:
    emit(cx.install_server(Path(args.asset), Path(args.root)))
    return 0


def cmd_cx_register(args: argparse.Namespace) -> int:
    emit(cx.register(Path(args.root), args.interp.split()))
    return 0


def cmd_cx_scope(args: argparse.Namespace) -> int:
    emit(cx.scope(Path(args.root)))
    return 0


def cmd_cx_wire(args: argparse.Namespace) -> int:
    if args.blocks_file is None:                 # RD-06: the blocks are templates
        command = cx.interpreter(args.interp.split())
        contents = upgrade.rgkb_contents(Path(args.root), command, _repo_path(args)["describe"])
    else:
        try:
            contents = _json_text("--blocks-file", read_file(args.blocks_file))
        finally:
            _remove_transit(Path(args.root), args.blocks_file)
        if not isinstance(contents, dict):
            raise Usage("--blocks-file expects a JSON object {name: block text}")
    emit(cx.wire(Path(args.root), contents, args.interp.split()))
    return 0


# ------------------------------------------------------------------- ai-lab

def cmd_lab_apply(args: argparse.Namespace) -> int:
    emit(lab.apply(Path(args.root), args.test_command, args.test_windows))
    return 0


def cmd_lab_ci(args: argparse.Namespace) -> int:
    emit(lab.install_ci(Path(args.root), args.allow or []))
    return 0


def cmd_lr_start(args: argparse.Namespace) -> int:
    root = Path(args.root)
    arguments = ""
    try:
        if args.arguments_file:
            arguments = read_file(args.arguments_file)
    finally:
        _remove_transit(root, args.arguments_file)
    try:
        rest, override = models.extract_option(arguments)
        resolved = models.resolve_root(root, models.LAB_ROLES, override, "lab")
    except models.ModelsError as exc:
        raise Usage(str(exc))
    choice = labrun.entry(rest, root)
    state = labrun.new_state({r: e["model"] for r, e in resolved["roles"].items()}, choice["mode"])
    run = runs.create(root, "lab-run", state)
    result = dict(choice, run=runs.display(run), models=resolved["roles"])
    if choice["mode"] == "cadrage":
        result["question"] = rest
    if resolved["warnings"]:
        result["model_warnings"] = resolved["warnings"]
    emit(result)
    return 0


def cmd_lr_record(args: argparse.Namespace) -> int:
    run = _run_path(args.run)
    state = runs.load_state(run)
    slot = args.agent + (":" + args.mode if args.mode else "")
    measure = _measure(state, args, slot)
    decision, fields = labrun.record(state, args.agent, args.mode, read_output(args.output), args.dead)
    model = _model(args, state, labrun.role_of(args.agent, args.mode))
    _log(run, args, args.agent, measure, fields, decision, model, **_profile_extra(state))
    runs.save_state(run, state)
    emit(decision)
    return 0


# --------------------------------------------------------------- brainstorm
# Nothing is written before the final gate (X5): the session state travels
# on stdin ('-'), and 'finish' writes the journal with the synthesis.

def _stdin_json(option: str, value: str) -> Any:
    return _json_text(option, read_output(value) or "")


def cmd_bs_args(args: argparse.Namespace) -> int:
    emit(bs.parse_arguments(read_output(args.arguments) or ""))
    return 0


def cmd_bs_resume(args: argparse.Namespace) -> int:
    emit(bs.resume(Path(args.root), args.path))
    return 0


def cmd_bs_round(args: argparse.Namespace) -> int:
    data = _stdin_json("--data", args.data)
    emit(bs.round_step(int(data["round"]), int(data["rounds"]), data["lenses"], data.get("discarded") or [],
                       data.get("relaunched") or []))
    return 0


def cmd_bs_render(args: argparse.Namespace) -> int:
    data = _stdin_json("--data", args.data)
    previous = bs.resume(Path(args.root), bs.synthesis_path(data["subject"]))["rows"]
    sys.stdout.write(bs.render(data, previous))
    return 0


def cmd_bs_finish(args: argparse.Namespace) -> int:
    emit(bs.finish(Path(args.root), _stdin_json("--data", args.data)))
    return 0


# -------------------------------------------------------------------- parser

# ------------------------------------------------ Phases 4 and 5: stops, specs, chains

def _git_dirty(root: Path) -> bool:
    res = Runner(cwd=str(root))(["git", "status", "--porcelain"])
    return bool(res.ok and res.out.strip())


def cmd_stop_write(args: argparse.Namespace) -> int:
    """SX-02: details written by Write (consumed), then the STOP file."""
    root = Path(args.root)
    try:
        details = json.loads(read_file(args.details_file))
    except ValueError as exc:
        raise Usage("--details-file is not valid JSON: %s" % exc)
    finally:
        _remove_transit(root, args.details_file)
    if not isinstance(details, dict):
        raise Usage("--details-file must hold a JSON object")
    run = _run_path(args.run) if args.run else None
    _stop(root, args.skill, args.kind, str(details.get("reason") or "stop requested"), details, run)
    return 1


def cmd_spec_check(args: argparse.Namespace) -> int:
    result = specfile.check(Path(args.file))
    emit(result)
    if result["errors"]:
        raise Usage("; ".join(result["errors"]))
    return 0


def cmd_spec_set_status(args: argparse.Namespace) -> int:
    emit(specfile.set_status(Path(args.file), args.status))
    return 0


def cmd_spec_settle(args: argparse.Namespace) -> int:
    """BQ-05 and BQ-06 under `auto`: a spec with open blocking questions becomes blocked and stops the cycle;
    otherwise every non-blocking question takes its default. Under `gate` nothing is changed."""
    run = _run_path(args.run)
    state = runs.load_state(run)
    root = Path(state["root"])
    path = Path(args.file)
    parsed = specfile.parse(path.read_text(encoding="utf-8-sig", errors="replace"))
    if parsed["errors"]:
        raise Usage("; ".join(parsed["errors"]))
    policy = _note_autonomy(run, root, args.autonomy)
    rel = stops.rel(root, path)
    if policy != "auto":
        emit({"policy": policy, "action": "ask", "unanswered": parsed["unanswered"]})
        return 0
    if parsed["unanswered"]:
        open_ = [q for q in parsed["questions"] if q["id"] in parsed["unanswered"]]
        if parsed["role"] == "proposed":
            specfile.set_role(path, "blocked")
        journal.append(run, {"event": "blocked", "questions": [{"id": q["id"], "text": q["text"]} for q in open_]})
        _stop(root, state.get("skill", "factory-run"), "blocked",
              "the spec holds %d blocking question(s) without answer" % len(open_),
              {"items": ["%s — %s" % (q["id"], q["text"]) for q in open_], "spec": rel}, run)
    done = specfile.apply_defaults(path)
    if done["missing_default"]:
        _stop(root, state.get("skill", "factory-run"), "gate",
              "non-blocking question(s) without a `Défaut :` line: %s" % ", ".join(done["missing_default"]),
              {"spec": rel}, run)
    if done["applied"]:
        event = gates.decide("factory-run.questions", "auto")
        event["applied"] = done["applied"]
        journal.append(run, event)
        journal.append(run, dict(gates.answer("factory-run.questions", event, "default", None),
                                 applied=done["applied"]))
    emit({"policy": "auto", "action": "defaults", "applied": done["applied"]})
    return 0


def cmd_stamp_autonomy(args: argparse.Namespace) -> int:
    """AP-05: `autonomy` written at installation when absent; an existing value is never replaced."""
    emit(stamp.ensure_autonomy(Path(args.root), args.autonomy))
    return 0


def cmd_activate(args: argparse.Namespace) -> int:
    root = Path(args.root)
    run = _run_path(args.run)
    state = runs.load_state(run)
    try:
        emit(scope.activate(root, args.skill, stops.rel(root, run), args.session, args.phase,
                            args.spec or state.get("spec"), state.get("target")))
    except scope.ScopeError as exc:
        raise Usage(str(exc))
    return 0


def cmd_deactivate(args: argparse.Namespace) -> int:
    run = _run_path(args.run)
    emit(scope.deactivate(Path(args.root), stops.rel(Path(args.root), run), args.session))
    return 0


def cmd_hooks_install(args: argparse.Namespace) -> int:
    root = Path(args.root)
    lint = None
    if args.lint_file:
        try:
            entries = json.loads(read_file(args.lint_file))
        except ValueError as exc:
            raise Usage("--lint-file is not valid JSON: %s" % exc)
        finally:
            _remove_transit(root, args.lint_file)
        if not isinstance(entries, list):
            raise Usage("--lint-file must hold a JSON list")
        try:
            lint = stamp.set_hooks_lint(root, entries)
        except stamp.StampError as exc:
            raise Usage(str(exc))
    result = hooks_install.apply(root, args.py)
    if lint is not None:
        result["lint"] = lint
    emit(result)
    return 0


def cmd_fr_deliver(args: argparse.Namespace) -> int:
    """CH-02: the delivery commit of a successful cycle (no push), when the conditions hold."""
    run = _run_path(args.run)
    state = runs.load_state(run)
    root = Path(state["root"])
    run_cmd = Runner(cwd=str(root))
    target = chain.resolve_target(run_cmd, root, state.get("ship"))
    reasons = chain.deliver_conditions(state, target)
    if state.get("status") != "success":
        reasons.append("the cycle did not end in success")
    if state.get("no_ship"):
        reasons.append("--no-ship was given")
    if reasons:
        journal.append(run, {"event": "chain", "id": "deliver", "skill": "ship-pr", "args": state.get("spec") or "",
                             "parent_run": runs.display(run), "result": "skipped: " + "; ".join(reasons)})
        emit({"delivered": False, "reasons": reasons,
              "suggestion": "/ship-pr %s" % (target or "<target>")})
        return 0
    try:
        result = chain.deliver(run_cmd, root, state, target, args.trailer or [])
    except chain.ChainStop as exc:
        _stop(root, "factory-run", exc.kind, str(exc), None, run)
    state["delivered"] = result
    runs.save_state(run, state)
    ship = not state.get("deliver_only")
    emit(dict(result, ship=ship, ship_args="%s --autonomy %s --spec %s" % (target, state.get("autonomy", "gate"),
                                                                           state["spec"]) if ship else None))
    return 0


def cmd_fr_bind(args: argparse.Namespace) -> int:
    """The spec written by a Plan phase becomes the spec of the run (delivered at success, delivery title)."""
    run = _run_path(args.run)
    state = runs.load_state(run)
    path = Path(state["root"]) / args.spec
    if not path.is_file():
        raise Usage("spec not found: %s" % args.spec)
    state["spec"] = Path(args.spec).as_posix()
    runs.save_state(run, state)
    emit({"spec": state["spec"]})
    return 0


def cmd_chain_spawn(args: argparse.Namespace) -> int:
    root = Path(args.root)
    try:
        text = read_file(args.args_file)
    finally:
        _remove_transit(root, args.args_file)
    parent = _run_path(args.parent_run) if args.parent_run else None
    policy = _policy(root, args.autonomy)[0]
    try:
        card = chain.spawn(root, args.skill, text.strip(), runs.display(parent) if parent else None, policy,
                           cwd=Path(args.cwd) if args.cwd else None)
    except chain.ChainStop as exc:
        _stop(root, args.stop_skill, exc.kind, str(exc), None, parent)
    except chain.ChainError as exc:
        raise Usage(str(exc))
    if parent:
        journal.append(parent, {"event": "chain", "id": card["id"], "skill": args.skill, "args": text.strip(),
                                "parent_run": runs.display(parent), "result": "started"})
    emit({k: card[k] for k in ("id", "skill", "args", "pid", "status", "out", "err")})
    return 0


def cmd_chain_status(args: argparse.Namespace) -> int:
    try:
        emit(chain.status(Path(args.root), args.id))
    except chain.ChainError as exc:
        raise Usage(str(exc))
    return 0


def cmd_chain_wait(args: argparse.Namespace) -> int:
    root = Path(args.root)
    try:
        result = chain.wait(root, args.id, args.max_seconds)
    except chain.ChainError as exc:
        raise Usage(str(exc))
    problem = chain.failure(result)
    emit(result)
    if problem:
        parent = root / result["parent_run"] if result.get("parent_run") else None
        run = parent if parent is not None and runs.state_path(parent).exists() else None
        _stop(root, "factory-run", "child", problem, {"child_stop": (result["stops"] or [None])[-1],
                                                      "child_command": "/%s %s" % (result["skill"], result["args"])}, run)
    return 0


def cmd_chain_exec(args: argparse.Namespace) -> int:
    return chain.exec_card(Path(args.root), args.id)


def cmd_audit_prioritize(args: argparse.Namespace) -> int:
    """PZ-05: milestones, ranks and `Priority` from the CONFIRMED findings; the decision is journaled."""
    run, state, p, reg = _audit_ctx(args)
    confirmed = [f for f in state["findings"].values() if f["status"] == findings.CONFIRMED]
    result = prioritize.rank(confirmed)
    root = Path(state["repo"])
    policy = _note_autonomy(run, root, args.autonomy)
    event = gates.decide("audit.software.assignment", policy)
    event["computed"] = {"assignment": result["assignment"], "ranks": result["ranks"]}
    journal.append(run, event)
    result["policy"] = policy
    if args.max_specs is not None:
        result["cap"] = prioritize.apply_cap(sorted(result["ranks"], key=result["ranks"].get), args.max_specs)
    emit(result)
    return 0


def cmd_audit_chain_plan(args: argparse.Namespace) -> int:
    """CH-04: the specs to queue after `audit.chain`, in the PZ order."""
    root = Path(args.root)
    only = [o for o in (args.only or "").split(";") if o.strip()]
    rows = prioritize.chain_plan(root, only)
    policy = _policy(root, args.autonomy)[0]
    factory_here = (root / ".claude" / "harness" / "harness.py").is_file()
    event = gates.decide("audit.chain", policy)
    if args.run:
        journal.append(_run_path(args.run), event)
    chained = policy == "auto" and factory_here and bool(rows)
    emit({"chain": chained, "decision": event["decision"], "policy": policy, "factory": factory_here,
          "specs": [r["file"] for r in rows], "only": ";".join(r["file"] for r in rows)})
    return 0


def cmd_doctor_auto_upgrade(args: argparse.Namespace) -> int:
    """UA-01 and UA-02."""
    root = Path(args.root)
    try:
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise Usage("--plan is not readable JSON: %s" % exc)
    emit(autoupgrade.decide(plan, _policy(root, args.autonomy)[0]))
    return 0


def _target_of(root: Path, flag: Optional[str]) -> str:
    target = chain.resolve_target(Runner(cwd=str(root)), root, flag)
    if not target:
        raise Usage("no target branch: pass --target, set ship_pr.target, or set origin/HEAD")
    return target


def cmd_queue_list(args: argparse.Namespace) -> int:
    root = Path(args.root)
    emit(queue_mod.scan(Runner(cwd=str(root)), root, _target_of(root, args.target), fetch=not args.no_fetch))
    return 0


def cmd_queue_next(args: argparse.Namespace) -> int:
    root = Path(args.root)
    run_cmd = Runner(cwd=str(root))
    listing = queue_mod.scan(run_cmd, root, _target_of(root, args.target), fetch=not args.no_fetch)
    skip = {Path(s).name for s in (args.attempted or "").split(";") if s.strip()}
    emit({"next": queue_mod.next_spec(listing, skip), "left": queue_mod.leftovers(listing, skip)})
    return 0


def cmd_queue_run(args: argparse.Namespace) -> int:
    root = Path(args.root)
    run_cmd = Runner(cwd=str(root))
    policy = _policy(root, args.autonomy)[0]
    holder: Dict[str, Path] = {}
    try:
        emit(queue_mod.run_queue(root, _target_of(root, args.target), (args.only or "").split(";") if args.only else None,
                                 policy, run_cmd, on_run=lambda p: holder.__setitem__("run", p)))
    except stops.StopRequest as exc:
        _stop_request(exc, root, "factory-queue", holder.get("run"))
    return 0


def cmd_worktree(args: argparse.Namespace) -> int:
    root = Path(args.root)
    run_cmd = Runner(cwd=str(root))
    try:
        if args.wcmd == "list":
            emit({"worktrees": worktrees.listing(run_cmd, root)})
        elif args.wcmd == "create":
            emit(worktrees.create(run_cmd, root, args.slug, _target_of(root, args.target), args.spec, "worktree create"))
        elif args.wcmd == "remove":
            emit(worktrees.remove(run_cmd, root, args.path, args.merged))
        elif args.wcmd == "transfer":
            worktrees.require(run_cmd, root, args.path)                            # WT-03
            emit(worktrees.transfer_spec(run_cmd, root, Path(args.path), args.spec, _target_of(root, args.target)))
    except worktrees.BaseMissing as exc:
        _stop(root, "factory-run", "gate", str(exc))
    except worktrees.WorktreeError as exc:
        raise ghflow.Stop(str(exc))
    return 0


def cmd_batch_run(args: argparse.Namespace) -> int:
    root = Path(args.root)
    run_cmd = Runner(cwd=str(root))
    try:
        parallel = batch.parse_parallel(args.parallel)
    except batch.BatchUsage as exc:
        raise Usage(str(exc))
    policy = _policy(root, args.autonomy)[0]
    holder: Dict[str, Path] = {}
    try:
        emit(batch.run_batch(root, args.globs, parallel, _target_of(root, args.target), policy, run_cmd,
                             resume=args.resume, on_run=lambda p: holder.__setitem__("run", p)))
    except batch.BatchUsage as exc:
        raise Usage(str(exc))
    except stops.StopRequest as exc:
        _stop_request(exc, root, "factory-batch", getattr(exc, "run", None) or holder.get("run"))
    return 0


def _agent_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--run", required=True, help="run journal (.harness/runs/<skill>-<timestamp>.jsonl)")
    p.add_argument("--model", default=None,
                   help="model used; default: the model resolved for the agent's role at start")
    p.add_argument("--duration-ms", default=None, help="duration from the usage block; otherwise 'begin' (absent = null)")
    p.add_argument("--tokens", default=None, help="tokens from the usage block, absent = null")
    p.add_argument("--dead", action="store_true", help="the agent died (API error, disconnection)")
    p.add_argument("--output", default=None, help="file holding the agent output, '-' = stdin")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="harness.py", description="claude-skills deterministic core")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("version").set_defaults(func=cmd_version)
    p = sub.add_parser("plugin", help="plugin distribution (PL-01 to PL-03)")
    plsub = p.add_subparsers(dest="plcmd", required=True)
    q = plsub.add_parser("skill-name", help="name of a skill for the Skill tool: <plugin>:<skill> or <skill>")
    q.add_argument("--skill", required=True)
    q.add_argument("--skill-dir", default=None, help="${CLAUDE_SKILL_DIR} of the calling skill (not resolved)")
    q.set_defaults(func=cmd_plugin_skill_name)
    q = plsub.add_parser("version-guard", help="fails when skills/ changed without a version bump")
    q.add_argument("--root", default=str(HERE.parent.parent))
    q.set_defaults(func=cmd_plugin_version_guard)
    p = sub.add_parser("interp", help="interpreter detection (D5)")
    p.add_argument("--timeout", type=float, default=interp.TIMEOUT)
    p.set_defaults(func=cmd_interp)
    p = sub.add_parser("journal", help="cycle journal")
    jsub = p.add_subparsers(dest="jcmd", required=True)
    q = jsub.add_parser("validate", help="validates the schema of a journal")
    q.add_argument("path")
    q.set_defaults(func=cmd_journal_validate)
    q = jsub.add_parser("amend", help="completes the last entry of a slot (measurements known after the call)")
    q.add_argument("--run", required=True)
    q.add_argument("--slot", required=True, help="agent, reviewer:<theme> or validator:<id>")
    q.add_argument("--duration-ms", default=None, help="fills a null duration_ms")
    q.add_argument("--tokens", default=None, help="fills null tokens")
    q.set_defaults(func=cmd_journal_amend)
    p = sub.add_parser("begin", help="timestamp before an agent call")
    p.add_argument("--run", required=True)
    p.add_argument("--slot", required=True, help="agent, reviewer:<theme> or validator:<id>")
    p.set_defaults(func=cmd_begin)
    p = sub.add_parser("models", help="model per role: .claude/models.json and --models (MD-01 to MD-08)")
    msub = p.add_subparsers(dest="mcmd", required=True)
    q = msub.add_parser("resolve", help="effective assignment {role: {model, pass_param}}")
    q.add_argument("--root", default=".")
    q.add_argument("--roles", default=None, help="factory, shippr, audit, lab or roles, comma-separated")
    q.add_argument("--models", default=None, help='"role=model;..." for this cycle only')
    q.add_argument("--aliases", default=None, choices=sorted(models.ALIAS_TABLES), help="short role names")
    q.set_defaults(func=cmd_models_resolve)
    q = msub.add_parser("write", help="merges a preset and explicit choices into .claude/models.json")
    q.add_argument("--root", default=".")
    q.add_argument("--preset", default=None, choices=sorted(models.PRESETS))
    q.add_argument("--set", default=None, help='explicit choices "role=model;...", they win')
    q.set_defaults(func=cmd_models_write)
    p = sub.add_parser("gate", help="human gates (GT-01 to GT-08)")
    gsub = p.add_subparsers(dest="gcmd", required=True)
    for name, func in (("decide", cmd_gate_decide), ("answer", cmd_gate_answer)):
        q = gsub.add_parser(name)
        q.add_argument("--id", required=True, help="gate id of the GT-08 catalog")
        q.add_argument("--run", default=None, help="run journal; absent (decide only): a new run of the gate's skill")
        q.add_argument("--root", default=".")
        q.set_defaults(func=func)
        if name == "decide":
            q.add_argument("--ambiguous", action="store_true",
                           help="AR-02: the scout proposal is ambiguous (gates declared `proposal`)")
            q.add_argument("--verdict", default=None, help="scout verdict file, named in the stop message")
            q.add_argument("--autonomy", default=None, help="gate|auto for this execution only (AP-03)")
            q.add_argument("--input", action="append", default=[],
                           help="name=true|false: input of the stop_when condition of a kind a gate (AP-01)")
    gsub.add_parser("list", help="the gate catalog (core and profiles)").set_defaults(func=cmd_gate_list)
    q.add_argument("--answer", default=None, help="option chosen by the user")
    q.add_argument("--answer-file", default=None, help="free answer, file written by Write (consumed)")
    q.add_argument("--headless", action="store_true", help="AskUserQuestion unavailable: headless fallback (GT-06)")
    q.add_argument("--default", action="store_true", help="applies the default (decision default)")
    p = sub.add_parser("finding", help="finding schema (FD-01 to FD-11)")
    fdsub = p.add_subparsers(dest="fdcmd", required=True)
    q = fdsub.add_parser("validate", help="validates a finding file (FD-03, FD-04); exit 1 if not conforming")
    q.add_argument("path")
    q.set_defaults(func=cmd_finding_validate)
    q = fdsub.add_parser("adapt", help="agent output block -> FD-01 findings (FD-05)")
    q.add_argument("--format", required=True, choices=findings.FORMATS)
    q.add_argument("--input", required=True, help="JSON file: one finding, a list, or {findings|issues: [...]}")
    q.add_argument("--ids", default=None, help="ids, comma-separated, when the format has none")
    q.add_argument("--axis", default=None, help="axis (theme of a ship-pr reviewer)")
    q.add_argument("--source", default=None, choices=journal.SKILLS)
    q.set_defaults(func=cmd_finding_adapt)
    q = fdsub.add_parser("verdict", help="parses a validator output (FD-06); usable false = dead agent")
    q.add_argument("--source", required=True, choices=sorted(findings.VERDICT_FIELDS))
    q.add_argument("--output", required=True, help="file holding the validator output, '-' = stdin")
    q.set_defaults(func=cmd_finding_verdict)
    q = fdsub.add_parser("apply", help="applies a validator verdict to a finding (FD-07)")
    q.add_argument("--finding", required=True)
    q.add_argument("--verdict", required=True, help="file holding the validator output")
    q.add_argument("--source", required=True, choices=sorted(findings.VERDICT_FIELDS))
    q.set_defaults(func=cmd_finding_apply)
    q = fdsub.add_parser("spec", help="finding -> factory spec, never overwritten (FD-08, FD-11)")
    q.add_argument("--root", default=".")
    q.add_argument("--finding", required=True)
    q.add_argument("--sections", required=True, help="JSON file of the judgment sections, written by Write")
    q.add_argument("--prefix", required=True, help="leg, lab, ...")
    q.add_argument("--number", required=True, type=int)
    q.add_argument("--dest", required=True, help="specs/SPEC-<prefix>-<nn>-<slug>.md")
    q.set_defaults(func=cmd_finding_spec)
    q = fdsub.add_parser("note", help="finding -> rgkb note (FD-09)")
    q.add_argument("--finding", required=True)
    q.set_defaults(func=cmd_finding_note)
    q = fdsub.add_parser("forbidden", help="forbidden strings of rendered deliverables (FD-11); exit 1 if found")
    q.add_argument("paths", nargs="+")
    q.set_defaults(func=cmd_finding_forbidden)
    p = sub.add_parser("registry", help="project registry .claude/findings.jsonl (RG-01 to RG-07)")
    rsub = p.add_subparsers(dest="rgcmd", required=True)
    q = rsub.add_parser("publish", help="publishes the run registry to the project (RG-03)")
    q.add_argument("--run", required=True)
    q.add_argument("--source", default=None, choices=journal.SKILLS, help="default: the skill of the run")
    q.add_argument("--deliverable", action="store_true", help="the deliverable holding the CONFIRMED is written")
    q.set_defaults(func=cmd_registry_publish)
    q = rsub.add_parser("check", help="what a run does with new findings (RG-04)")
    q.add_argument("--root", default=".")
    q.add_argument("--file", required=True, help="FD-01 findings file")
    q.set_defaults(func=cmd_registry_check)
    p = sub.add_parser("stack", help="stack detection and bounds (ST-01 to ST-05)")
    stsub = p.add_subparsers(dest="stcmd", required=True)
    q = stsub.add_parser("detect", help="markers, manifests, core stacks, artifact folders, file count")
    q.add_argument("--root", default=".")
    q.set_defaults(func=cmd_stack_detect)
    q = stsub.add_parser("journal", help="sampling of a long journal (ST-05)")
    q.add_argument("--file", required=True)
    q.set_defaults(func=cmd_stack_journal)
    p = sub.add_parser("tshirt", help="orderings, quick wins, spec mapping, load (TS-01 to TS-05)")
    p.add_argument("--file", required=True, help="FD-01 findings file")
    p.add_argument("--order", required=True, choices=tshirt.ORDERS)
    p.add_argument("--grouping", default=None, choices=tshirt.GROUPINGS, help="finding -> spec mapping (TS-02)")
    p.add_argument("--load-by", default=None, help="key of the load groups (milestone, ...)")
    p.add_argument("--cap", type=int, default=None, help="ceiling: first N specs (or findings) kept, excess listed")
    p.set_defaults(func=cmd_tshirt)
    p = sub.add_parser("audit", help="shared audit engine (AU-01 to AU-16)")
    asub = p.add_subparsers(dest="aucmd", required=True)
    q = asub.add_parser("start", help="arguments, git probe, resume; opens the run in the audited repository")
    q.add_argument("--skill", required=True, choices=sorted(audit.PROFILES))
    q.add_argument("--root", default=".", help="current directory of the session (REPO is relative to it)")
    q.add_argument("--only", default=None, help="l;l: keeps these lenses only (AD-03)")
    q.add_argument("--unified", action="store_true", help="/audit: resume from AUDIT.md and the previous deliverables (MG-01)")
    q.add_argument("--arguments-file", default=None, help="file holding $ARGUMENTS, written by Write (consumed)")
    q.set_defaults(func=cmd_audit_start)
    q = asub.add_parser("auditor", help="return of a lens or family auditor (AU-04, AU-06)")
    _agent_args(q)
    q.add_argument("--unit", required=True, help="lens or family of the auditor")
    q.set_defaults(func=cmd_audit_auditor)
    q = asub.add_parser("validator", help="return of a validator (AU-05)")
    _agent_args(q)
    q.add_argument("--finding", required=True)
    q.set_defaults(func=cmd_audit_validator)
    for name, func in (("next", cmd_audit_next), ("consolidate", cmd_audit_consolidate),
                       ("readjust", cmd_audit_readjust)):
        q = asub.add_parser(name)
        q.add_argument("--run", required=True)
        q.set_defaults(func=func)
    q = asub.add_parser("plan", help="with --run: ceiling, spec mapping, load and quick wins (LA-10); without: "
                                     "lens selection, waves and caps of /audit (AD-03, AD-04, AD-09)")
    q.add_argument("--run", default=None)
    q.add_argument("--data-file", default=None, help="the render data of the skill (not consumed)")
    q.add_argument("--root", default=".")
    q.add_argument("--profile", default=None, help="p[;p]: forces the active profiles")
    q.add_argument("--lenses", default=None)
    q.add_argument("--max-specs", default=None)
    q.add_argument("--max-findings", default=None)
    q.add_argument("--max-questions", default=None)
    q.add_argument("--via", default=None, help="alias that invoked the audit (journaled)")
    q.set_defaults(func=cmd_audit_plan)
    q = asub.add_parser("open", help="opens the audit-<timestamp> journal of /audit, journals --via (AD-12)")
    q.add_argument("--root", default=".")
    q.add_argument("--via", default=None)
    q.set_defaults(func=cmd_audit_open)
    q = asub.add_parser("assemble", help="writes AUDIT.md from the rendered set blocks (SO-01, MG-02)")
    q.add_argument("--root", default=".", help="the audited repository")
    q.add_argument("--set-run", action="append", required=True, help="run of a lens set, in the order of the stamp")
    q.add_argument("--profiles", required=True, help="p;p")
    q.add_argument("--lenses", required=True, help="l;l")
    q.add_argument("--date", default=None)
    q.add_argument("--sampled", action="store_true")
    q.set_defaults(func=cmd_audit_assemble)
    q = asub.add_parser("alias-args", help="arguments of an old audit skill -> arguments of /audit (AX-05)")
    q.add_argument("--skill", required=True, choices=sorted(aliases.ALIASES))
    q.add_argument("--root", default=".")
    q.add_argument("--arguments-file", default=None, help="file holding $ARGUMENTS, written by Write (consumed)")
    q.add_argument("--skill-dir", default=None, help="${CLAUDE_SKILL_DIR} of the alias (gives `invoke`, PL-02)")
    q.set_defaults(func=cmd_audit_alias_args)
    q = asub.add_parser("settle", help="TRANCHEE (journal <date>) when the entry is later than the audit (AA-10)")
    q.add_argument("--audit-date", required=True)
    q.add_argument("--entry-date", required=True)
    q.set_defaults(func=cmd_audit_settle)
    q = asub.add_parser("prioritize", help="PZ-05: milestones, ranks and Priority of the CONFIRMED findings")
    q.add_argument("--run", required=True)
    q.add_argument("--autonomy", default=None)
    q.add_argument("--max-specs", type=int, default=None)
    q.set_defaults(func=cmd_audit_prioritize)
    q = asub.add_parser("chain-plan", help="CH-04: the specs to queue after audit.chain, in the PZ order")
    q.add_argument("--root", default=".")
    q.add_argument("--only", default=None, help="a;b: the specs written by the audit")
    q.add_argument("--run", default=None)
    q.add_argument("--autonomy", default=None)
    q.set_defaults(func=cmd_audit_chain_plan)
    q = asub.add_parser("themes", help="gate calls for N themes (AU-16)")
    q.add_argument("--skill", required=True, choices=sorted(audit.PROFILES))
    q.add_argument("--count", required=True, type=int)
    q.set_defaults(func=cmd_audit_themes)
    q = asub.add_parser("render", help="writes the deliverable from the run and the skill's data (AU-11)")
    q.add_argument("--run", required=True)
    q.add_argument("--data-file", required=True, help="JSON file of the skill's choices, written by Write (consumed)")
    q.set_defaults(func=cmd_audit_render)
    q = asub.add_parser("spec", help="spec of a CONFIRMED finding, never overwritten (AU-14)")
    q.add_argument("--run", required=True)
    q.add_argument("--finding", required=True)
    q.add_argument("--sections", required=True, help="JSON file of the judgment sections, written by Write")
    q.add_argument("--number", required=True, type=int)
    q.add_argument("--slug", required=True)
    q.set_defaults(func=cmd_audit_spec)
    q = asub.add_parser("publish", help="publishes the run registry to the project (RG-03, AU-12)")
    q.add_argument("--run", required=True)
    q.add_argument("--deliverable", action="store_true", help="the deliverable holding the CONFIRMED is written")
    q.set_defaults(func=cmd_audit_publish)
    p = sub.add_parser("profile", help="profiles: data folders (PF-01 to PF-05)")
    prsub = p.add_subparsers(dest="prcmd", required=True)
    q = prsub.add_parser("validate", help="exit 0 if conforming, 2 with the list of errors (PF-03)")
    q.add_argument("path")
    q.set_defaults(func=cmd_profile_validate)
    q = prsub.add_parser("list", help="profiles, lenses and roles (PF-04)")
    q.add_argument("--profiles-dir", default=None)
    q.set_defaults(func=cmd_profile_list)
    q = prsub.add_parser("show", help="roles and fragments of a profile, for the generation (EP-04)")
    q.add_argument("name")
    q.add_argument("--profiles-dir", default=None)
    q.set_defaults(func=cmd_profile_show)
    p = sub.add_parser("stamp", help="profile stamp of .claude/harness.json (HJ-01 to HJ-05); no removal")
    stsub2 = p.add_subparsers(dest="stampcmd", required=True)
    q = stsub2.add_parser("add")
    q.add_argument("--root", default=".")
    q.add_argument("--profiles", required=True, help="p[;p]")
    q.add_argument("--by", required=True, choices=stamp.BY_VALUES)
    q.add_argument("--date", default=None, help="YYYY-MM-DD (default: today)")
    q.set_defaults(func=cmd_stamp_add)
    q = stsub2.add_parser("resolve", help="{source, profiles}: stamp, inferred or none (HJ-04)")
    q.add_argument("--root", default=".")
    q.set_defaults(func=cmd_stamp_resolve)
    q = stsub2.add_parser("compare", help="added / kept / vanished against a scout verdict (EP-03)")
    q.add_argument("--root", default=".")
    q.add_argument("--verdict", required=True)
    q.set_defaults(func=cmd_stamp_compare)
    q = stsub2.add_parser("validate", help="exit 0 if the stamp file is conform (HJ-07, HJ-12), 2 otherwise")
    q.add_argument("file")
    q.set_defaults(func=cmd_stamp_validate)
    q = stsub2.add_parser("autonomy", help="AP-05: writes `autonomy` when absent (default auto), never replaces it")
    q.add_argument("--root", default=".")
    q.add_argument("--autonomy", default=None)
    q.set_defaults(func=cmd_stamp_autonomy)
    q = stsub2.add_parser("show", help="the stamp of a project (HJ-15)")
    q.add_argument("--root", default=".")
    q.set_defaults(func=cmd_stamp_show)
    p = sub.add_parser("core", help="the core itself (CO-01)")
    csub0 = p.add_subparsers(dest="corecmd", required=True)
    q = csub0.add_parser("history", help="history.json: --rebuild from git (development), --check the tree")
    q.add_argument("--rebuild", action="store_true")
    q.add_argument("--check", action="store_true")
    q.set_defaults(func=cmd_core_history)
    p = sub.add_parser("upgrade", help="upgrade of an equipped project (UP-01 to UP-13)")
    usub = p.add_subparsers(dest="upcmd", required=True)
    q = usub.add_parser("plan", help="computes every change and writes the plan and the diff under .harness/runs/")
    q.add_argument("--root", default=".")
    q.add_argument("--skill", required=True, choices=(upgrade.SF, upgrade.CI))
    q.add_argument("--params-file", default=None, help="JSON of the judgement parameters, written by Write (consumed)")
    q.add_argument("--convert", action="store_true", help="converts the files with local changes (CV-04)")
    q.add_argument("--interpreter", default=None, help="interpreter command, e.g. 'py -3'")
    q.add_argument("--profiles", default=None, help="p[;p]: profiles of a fresh installation (EP-11)")
    q.add_argument("--by", default="scout", choices=stamp.BY_VALUES)
    q.set_defaults(func=cmd_upgrade_plan)
    q = usub.add_parser("apply", help="applies a plan: all or nothing, the stamp last (UP-05)")
    q.add_argument("--root", default=".")
    q.add_argument("--plan", required=True, help="plan file, relative to the root")
    q.add_argument("--decisions-file", default=None,
                   help='JSON {"files": {path: "keep"|"convert"}, "source": "user|headless|default"}')
    q.set_defaults(func=cmd_upgrade_apply)
    p = sub.add_parser("doctor", help="state of the installation of a project, or 'doctor scan' of a tree (DR, SN)")
    p.add_argument("action", nargs="?", choices=("scan", "auto-upgrade"), default=None)
    p.add_argument("--root", default=".")
    p.add_argument("--plan", default=None, help="auto-upgrade: upgrade plan JSON (UA-01)")
    p.add_argument("--autonomy", default=None, help="gate|auto for this execution only (AP-03)")
    p.add_argument("--depth", default=None)
    p.add_argument("--no-journal", action="store_true")
    p.set_defaults(func=cmd_doctor)
    p = sub.add_parser("scout", help="profile scout (SC-01 to SC-08)")
    scsub = p.add_subparsers(dest="sccmd", required=True)
    q = scsub.add_parser("signals", help="deterministic signals of every profile and the judgement criteria")
    q.add_argument("--root", default=".")
    q.set_defaults(func=cmd_scout_signals)
    q = scsub.add_parser("verdict", help="verifies the proofs, scores and ranks the profiles (SC-05)")
    q.add_argument("--root", default=".")
    q.add_argument("--signals", default=None, help="the agent's JSON block, written by Write (consumed)")
    q.add_argument("--scout-failed", action="store_true", help="the agent died twice (SC-06)")
    q.add_argument("--run", default=None)
    q.add_argument("--skill", default="software-factory", choices=("software-factory", "audit"))
    q.add_argument("--models", default=None)
    q.add_argument("--duration-ms", default=None)
    q.add_argument("--tokens", default=None)
    q.set_defaults(func=cmd_scout_verdict)
    p = sub.add_parser("block", help="delimited blocks (D9, BK-01)")
    p.add_argument("action", choices=("get", "upsert", "remove"))
    p.add_argument("--file", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--style", choices=blocks.STYLES, default="md")
    p.add_argument("--content", default=None, help="content file, '-' = stdin")
    p.set_defaults(func=cmd_block)

    p = sub.add_parser("sf", help="software-factory")
    ssub = p.add_subparsers(dest="sfcmd", required=True)
    q = ssub.add_parser("apply", help="merges settings.json, tasks.json, .gitignore (SF-01 to SF-04)")
    q.add_argument("--root", default=".")
    q.add_argument("--stacks", default="", help="node,dotnet,python,cpp,rust (comma-separated)")
    q.add_argument("--profiles", default=None, help="p[;p]: union of the profiles' deny rules, tasks, .gitignore (HJ-06)")
    q.add_argument("--test-command", default=None, help="with --profiles: test task of the profiles that have one")
    q.add_argument("--test-windows", default=None)
    q.set_defaults(func=cmd_sf_apply)
    q = ssub.add_parser("install", help="writes a generated file without overwriting (SF-04)")
    q.add_argument("--root", default=".")
    q.add_argument("--dest", required=True)
    q.add_argument("--source", required=True,
                   help="content file, '-' = stdin; removed once read when it lies under .harness/runs/")
    q.add_argument("--model-role", default=None, choices=models.ROLES,
                   help="generated agent: its role's model written in a frontmatter block (X1)")
    q.set_defaults(func=cmd_sf_install)
    q = ssub.add_parser("args", help="entry point arguments: --profile, --rescan, first need (EP-01)")
    q.add_argument("--root", default=".")
    q.add_argument("--arguments-file", default=None, help="file holding $ARGUMENTS, written by Write (consumed)")
    q.add_argument("--via", default=None, choices=("ai-lab",), help="translation of the arguments of /ai-lab (EP-10)")
    q.set_defaults(func=cmd_sf_args)
    q = ssub.add_parser("copy-core", help="copies the core under .claude/harness/ (D1); exit 1 if blocked")
    q.add_argument("--root", default=".")
    mode = q.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="strict comparison with this core; exit 1 if it differs")
    mode.add_argument("--refresh", action="store_true",
                      help="replaces a recognized core copy, never touching a file that is not a core file")
    q.add_argument("--force", action="store_true", help="with --refresh: replaces a copy declaring a newer CORE_VERSION")
    q.set_defaults(func=cmd_sf_copy_core)

    p = sub.add_parser("factory-run", help="factory-run orchestration (FR-01 to FR-04)")
    fsub = p.add_subparsers(dest="frcmd", required=True)
    q = fsub.add_parser("start")
    q.add_argument("--root", default=".")
    q.add_argument("--profile", default=None, help="forces the profile of this cycle (LP-05)")
    q.add_argument("--orchestrator", default="factory-run",
                   choices=sorted({p["orchestrator"]["name"] for p in profiles.discover().values()}),
                   help="instance of the orchestrator template (LP-06)")
    q.add_argument("--arguments-file", default=None,
                   help="file holding $ARGUMENTS, written by the Write tool (never through the shell)")
    q.add_argument("--autonomy", default=None, help="gate|auto for this cycle only (AP-03)")
    q.set_defaults(func=cmd_fr_start)
    q = fsub.add_parser("record")
    _agent_args(q)
    q.add_argument("--agent", required=True, help="an agent of the run's profile")
    q.add_argument("--mode", default=None, help="director mode of a loop with consultation (cadrage|interpretation)")
    q.set_defaults(func=cmd_fr_record)
    q = fsub.add_parser("deliver", help="CH-02: delivery commit of a successful cycle (no push)")
    q.add_argument("--run", required=True)
    q.add_argument("--trailer", action="append", default=[], help="trailer line (attribution), repeatable")
    q.set_defaults(func=cmd_fr_deliver)
    q = fsub.add_parser("bind", help="records the spec written by the Plan phase in the run")
    q.add_argument("--run", required=True)
    q.add_argument("--spec", required=True)
    q.set_defaults(func=cmd_fr_bind)

    p = sub.add_parser("cx", help="code-index rules (CX-01 to CX-16)")
    csub = p.add_subparsers(dest="cxcmd", required=True)
    q = csub.add_parser("args", help="--reindex, --no-wire; the rest handed back (CX-01)")
    q.add_argument("--root", default=".")
    q.add_argument("--arguments-file", default=None, help="file holding $ARGUMENTS, written by Write (consumed)")
    q.set_defaults(func=cmd_cx_args)
    csub.add_parser("rg", help="ripgrep probe and install command (CX-02)").set_defaults(func=cmd_cx_rg)
    q = csub.add_parser("survey", help="repository state (CX-04)")
    q.add_argument("--root", default=".")
    q.set_defaults(func=cmd_cx_survey)
    q = csub.add_parser("install", help="copies rgkb.py (CX-05)")
    q.add_argument("--root", default=".")
    q.add_argument("--asset", required=True, help="assets/rgkb.py of the skill")
    q.set_defaults(func=cmd_cx_install)
    q = csub.add_parser("register", help=".mcp.json, settings.json, tasks.json (CX-06, CX-07, CX-15)")
    q.add_argument("--root", default=".")
    q.add_argument("--interp", required=True, help="interpreter command, e.g. 'py -3'")
    q.set_defaults(func=cmd_cx_register)
    q = csub.add_parser("scope", help="config.json exclusions and .gitignore counter-rule (CX-08, CX-10)")
    q.add_argument("--root", default=".")
    q.set_defaults(func=cmd_cx_scope)
    q = csub.add_parser("wire", help="blocks, tools and CI allow lists (CX-12 to CX-14)")
    q.add_argument("--root", default=".")
    q.add_argument("--interp", required=True)
    q.add_argument("--blocks-file", default=None,
                   help="JSON {target: block text}, written by Write (consumed); absent: the templates (RD-06)")
    q.set_defaults(func=cmd_cx_wire)

    p = sub.add_parser("lab", help="ai-lab installation rules (AL-01 to AL-09)")
    lsub = p.add_subparsers(dest="labcmd", required=True)
    q = lsub.add_parser("apply", help="settings.json deny rules, tasks.json, .gitignore (AL-01 to AL-03)")
    q.add_argument("--root", default=".")
    q.add_argument("--test-command", default=None, help="detected test command (task 'Lab: tests unitaires')")
    q.add_argument("--test-windows", default=None, help="its windows variant when different")
    q.set_defaults(func=cmd_lab_apply)
    q = lsub.add_parser("ci", help="ci/lab.sh and ci/lab.ps1 (AL-07)")
    q.add_argument("--root", default=".")
    q.add_argument("--allow", action="append", help="allow-list entry added to the base list (repeatable)")
    q.set_defaults(func=cmd_lab_ci)

    p = sub.add_parser("lab-run", help="lab-run orchestration (LR-01 to LR-09)")
    lrsub = p.add_subparsers(dest="lrcmd", required=True)
    q = lrsub.add_parser("start")
    q.add_argument("--root", default=".")
    q.add_argument("--arguments-file", default=None, help="file holding $ARGUMENTS, written by Write (consumed)")
    q.set_defaults(func=cmd_lr_start)
    q = lrsub.add_parser("record")
    _agent_args(q)
    q.add_argument("--agent", required=True, choices=labrun.AGENTS)
    q.add_argument("--mode", default=None, choices=labrun.MODES, help="Director only")
    q.set_defaults(func=cmd_lr_record)

    p = sub.add_parser("bs", help="brainstorm rules (BS-01 to BS-12); state on stdin, nothing written before 'finish'")
    bsub = p.add_subparsers(dest="bscmd", required=True)
    q = bsub.add_parser("args")
    q.add_argument("--arguments", default="-", help="'-' = $ARGUMENTS on stdin")
    q.set_defaults(func=cmd_bs_args)
    q = bsub.add_parser("resume")
    q.add_argument("--root", default=".")
    q.add_argument("--path", required=True)
    q.set_defaults(func=cmd_bs_resume)
    q = bsub.add_parser("round")
    q.add_argument("--data", default="-")
    q.set_defaults(func=cmd_bs_round)
    for name, func in (("render", cmd_bs_render), ("finish", cmd_bs_finish)):
        q = bsub.add_parser(name)
        q.add_argument("--root", default=".")
        q.add_argument("--data", default="-")
        q.set_defaults(func=func)

    p = sub.add_parser("stop", help="STOP files (SX-01 to SX-07)")
    spsub = p.add_subparsers(dest="stopcmd", required=True)
    q = spsub.add_parser("write", help="writes .harness/runs/STOP-<ts>.md, the stop event and exits 1 (SX-02, SX-05)")
    q.add_argument("--root", default=".")
    q.add_argument("--skill", required=True)
    q.add_argument("--kind", required=True, choices=stops.KINDS)
    q.add_argument("--run", default=None)
    q.add_argument("--details-file", required=True, help="JSON {reason, items, spec, phase, ...}, written by Write (consumed)")
    q.set_defaults(func=cmd_stop_write)
    p = sub.add_parser("spec", help="spec files: header, questions, statuses (BQ-01 to BQ-09)")
    sbsub = p.add_subparsers(dest="speccmd", required=True)
    q = sbsub.add_parser("check", help="{status, blocking, unanswered, perimeter, depends_on, priority, errors}; exit 2 if malformed")
    q.add_argument("file")
    q.set_defaults(func=cmd_spec_check)
    q = sbsub.add_parser("set-status", help="rewrites the `Status:` line only, along the admitted transitions (BQ-08)")
    q.add_argument("file")
    q.add_argument("status")
    q.set_defaults(func=cmd_spec_set_status)
    q = sbsub.add_parser("settle", help="BQ-05, BQ-06: blocked and stop, or defaults applied, under auto")
    q.add_argument("--file", required=True)
    q.add_argument("--run", required=True)
    q.add_argument("--autonomy", default=None)
    q.set_defaults(func=cmd_spec_settle)
    p = sub.add_parser("chain", help="headless child processes (CH-01)")
    chsub = p.add_subparsers(dest="chaincmd", required=True)
    q = chsub.add_parser("spawn", help="launches `claude -p \"/<skill> <args>\"`, detached")
    q.add_argument("--root", default=".")
    q.add_argument("--skill", required=True, choices=chain.SKILLS)
    q.add_argument("--args-file", required=True, help="the arguments of the child, written by Write (consumed)")
    q.add_argument("--parent-run", default=None)
    q.add_argument("--autonomy", default=None)
    q.add_argument("--cwd", default=None, help="working directory of the child (a registered worktree)")
    q.add_argument("--stop-skill", default="factory-run", help="skill named in the STOP file of a refused launch")
    q.set_defaults(func=cmd_chain_spawn)
    q = chsub.add_parser("status")
    q.add_argument("--root", default=".")
    q.add_argument("--id", required=True)
    q.set_defaults(func=cmd_chain_status)
    q = chsub.add_parser("wait", help="waits at most --max-seconds; a failed child is a stop of kind child")
    q.add_argument("--root", default=".")
    q.add_argument("--id", required=True)
    q.add_argument("--max-seconds", type=float, default=540)
    q.set_defaults(func=cmd_chain_wait)
    q = chsub.add_parser("exec", help=argparse.SUPPRESS)
    q.add_argument("--root", default=".")
    q.add_argument("--id", required=True)
    q.set_defaults(func=cmd_chain_exec)
    p = sub.add_parser("queue", help="the spec queue (FQ-01 to FQ-10)")
    qsub = p.add_subparsers(dest="queuecmd", required=True)
    for name, func in (("list", cmd_queue_list), ("next", cmd_queue_next), ("run", cmd_queue_run)):
        q = qsub.add_parser(name)
        q.add_argument("--root", default=".")
        q.add_argument("--target", default=None)
        if name != "run":
            q.add_argument("--no-fetch", action="store_true")
        if name == "next":
            q.add_argument("--attempted", default=None, help="a;b: specs already attempted")
        if name == "run":
            q.add_argument("--only", default=None, help="a;b")
            q.add_argument("--autonomy", default=None)
        q.set_defaults(func=func)
    p = sub.add_parser("worktree", help="worktrees of the harness (WT-01 to WT-09)")
    wsub = p.add_subparsers(dest="wcmd", required=True)
    q = wsub.add_parser("list")
    q.add_argument("--root", default=".")
    q.set_defaults(func=cmd_worktree)
    q = wsub.add_parser("create")
    q.add_argument("--root", default=".")
    q.add_argument("--slug", required=True)
    q.add_argument("--target", default=None)
    q.add_argument("--spec", default=None)
    q.set_defaults(func=cmd_worktree)
    q = wsub.add_parser("transfer", help="WT-05: copies the spec into the worktree, moves the original to handoff/")
    q.add_argument("--root", default=".")
    q.add_argument("--path", required=True)
    q.add_argument("--spec", required=True)
    q.add_argument("--target", default=None)
    q.set_defaults(func=cmd_worktree)
    q = wsub.add_parser("remove", help="refused outside the registry (WT-03); kept unless --merged")
    q.add_argument("--root", default=".")
    q.add_argument("--path", required=True)
    q.add_argument("--merged", action="store_true")
    q.set_defaults(func=cmd_worktree)
    p = sub.add_parser("batch", help="parallel worktrees, one ship at a time (FB-01 to FB-08)")
    bsub2 = p.add_subparsers(dest="batchcmd", required=True)
    q = bsub2.add_parser("run")
    q.add_argument("globs", nargs="*")
    q.add_argument("--root", default=".")
    q.add_argument("--parallel", default=None)
    q.add_argument("--target", default=None)
    q.add_argument("--autonomy", default=None)
    q.add_argument("--resume", default=None)
    q.set_defaults(func=cmd_batch_run)
    p = sub.add_parser("hooks", help="Claude Code hooks (HK-02)")
    hsub = p.add_subparsers(dest="hookscmd", required=True)
    q = hsub.add_parser("install", help="merges the `hooks` key of .claude/settings.json")
    q.add_argument("--root", default=".")
    q.add_argument("--py", required=True, help="the detected interpreter command, e.g. 'py -3'")
    q.add_argument("--lint-file", default=None,
                   help="HL-01: JSON list [{glob, argv, timeout_s}] of the verification linters, written by Write (consumed)")
    q.set_defaults(func=cmd_hooks_install)
    for name, func in (("activate", cmd_activate), ("deactivate", cmd_deactivate)):
        p = sub.add_parser(name, help="HK-05: cycle context marker of the hooks")
        p.add_argument("--root", default=".")
        p.add_argument("--run", required=True)
        p.add_argument("--session", default=None, help='"${CLAUDE_SESSION_ID}"')
        if name == "activate":
            p.add_argument("--skill", required=True, choices=scope.CONTEXT_SKILLS)
            p.add_argument("--phase", default="build")
            p.add_argument("--spec", default=None)
        p.set_defaults(func=func)
    p = sub.add_parser("ship-pr", help="ship-pr orchestration (SP-01 to SP-19)")
    psub = p.add_subparsers(dest="spcmd", required=True)
    q = psub.add_parser("start")
    q.add_argument("--root", default=".")
    q.add_argument("--target", required=True)
    q.add_argument("--run", default=None, help="run opened by the ship-pr.target gate")
    q.add_argument("--spec", default=None, help="SM-07: the spec delivered by this PR (link to the queue)")
    q.add_argument("--autonomy", default=None, help="gate|auto for this execution only (AP-03)")
    q.add_argument("--models", default=None, help='"role=model;..." for this cycle only (X6)')
    q.add_argument("--rounds", default=None)
    q.add_argument("--themes", default=None,
                   help="1 to 4 themes separated by ';' (default: 3 themes); empty list, more than 4 "
                        "or a theme given twice = explicit error (D8)")
    q.set_defaults(func=cmd_sp_start)
    for name, func in (("sync", cmd_sp_sync), ("pr-state", cmd_sp_pr_state), ("diff", cmd_sp_diff),
                       ("threads", cmd_sp_threads), ("end-round", cmd_sp_end_round)):
        q = psub.add_parser(name)
        q.add_argument("--run", required=True)
        q.set_defaults(func=func)
    q = psub.add_parser("pr")
    q.add_argument("--run", required=True)
    q.add_argument("--title", default=None)
    q.add_argument("--title-file", default=None, help="file holding the title (one line), written by Write")
    q.add_argument("--body-file", default=None, help="body file, '-' = stdin")
    q.set_defaults(func=cmd_sp_pr)
    q = psub.add_parser("reviewer")
    _agent_args(q)
    q.add_argument("--theme", required=True)
    q.set_defaults(func=cmd_sp_reviewer)
    q = psub.add_parser("validator")
    _agent_args(q)
    q.add_argument("--finding", required=True)
    q.set_defaults(func=cmd_sp_validator)
    q = psub.add_parser("drop", help="sets aside a PENDING finding as a duplicate of another (SP-07)")
    q.add_argument("--run", required=True)
    q.add_argument("--finding", required=True, help="PENDING finding of the current round")
    q.add_argument("--of", required=True, help="existing finding it duplicates")
    q.add_argument("--reason-file", default=None, help="file holding the reason, written by Write")
    q.set_defaults(func=cmd_sp_drop)
    q = psub.add_parser("comment")
    q.add_argument("--run", required=True)
    q.add_argument("--finding", required=True)
    q.set_defaults(func=cmd_sp_comment)
    q = psub.add_parser("commit")
    q.add_argument("--run", required=True)
    q.add_argument("--summary", default="")
    q.add_argument("--summary-file", default=None, help="file holding the summary, written by Write")
    q.add_argument("--fixed", nargs="*", default=[])
    q.add_argument("--not-fixed", action="append", default=[], help="ID:reason, repeatable")
    q.add_argument("--not-fixed-file", default=None,
                   help='JSON file {"<id>": "<reason>"}, written by Write; an id given twice is refused')
    q.add_argument("--trailer", action="append", default=[],
                   help="trailer line added after a blank line (attribution), repeatable")
    q.set_defaults(func=cmd_sp_commit)
    q = psub.add_parser("merge")
    q.add_argument("--run", required=True)
    q.add_argument("--check", action="store_true", help="checks and summarizes without merging")
    q.add_argument("--auto", action="store_true",
                   help="SM-02: evaluate the five conditions, comment, merge only if all hold (never a question)")
    q.add_argument("--conditions", action="store_true", help="with --check: also report the five conditions (SM-05)")
    q.set_defaults(func=cmd_sp_merge)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    try:
        run = _existing_run(args)
        if run is None:
            return args.func(args)
        with runs.locked(run):
            if args.cmd not in ("stop", "deactivate", "journal"):
                stops.refuse_if_stopped(runs.load_state(run))      # SX-06
            return args.func(args)
    except (ghflow.Stop, runs.LockTimeout, gates.GateStop, audit.AuditStop, stops.Stopped) as exc:
        sys.stderr.write("STOP: %s\n" % exc)
        return 1
    except (Usage, ValueError, factory.RunOver, labrun.RunOver, shippr.LoopError, fusion.FusionError,
            blocks.BlockError, FileNotFoundError, KeyError, upgrade.UpgradeError, chain.ChainError) as exc:
        sys.stderr.write("ERROR: %s\n" % exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
