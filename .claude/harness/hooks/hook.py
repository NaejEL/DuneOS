#!/usr/bin/env python3
# Claude Code hooks of the harness (HK-01, HK-04, HG, HP, HD, HR, HL).
#
# Usage: <PY> hook.py pre-edit | pre-bash | post-edit   (the hook JSON on stdin)
#
# - pre-edit: scope of the spec during factory-run / lab-run (HP), scope of the
#   PR diff during ship-pr (HD), secret files (Q15); second drift = stop (HR).
# - pre-bash: the permanent destructive-command guard (HG), whatever the context.
# - post-edit: the configured linters, in verification mode, never blocking (HL).
# - Exit 2 only for a wanted blocking. Any internal error (invalid stdin,
#   unreadable marker, git failure, 5 s budget exceeded, any exception) exits 0,
#   prints a systemMessage on stdout and appends a line to
#   .harness/runs/hook-errors.jsonl (HK-04). Never a blocking traceback.
#
# Standard library only, Python >= 3.9. The rules live in hcore (guard, scope),
# loaded lazily so that a missing core is an internal error, not a crash.

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import threading
from datetime import datetime

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.dirname(HERE)
BUDGET_S = 5.0
LINT_SUMMARY_LINES = 20
LINT_SUMMARY_CHARS = 2000
NAMES = ("pre-edit", "pre-bash", "post-edit")


def core(name):
    if CORE not in sys.path:
        sys.path.insert(0, CORE)
    return importlib.import_module("hcore." + name)


def project_root(payload):
    return os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd()


def report_error(name, tool, root, exc):
    """HK-04: allow, tell the user, keep a trace."""
    kind = type(exc).__name__
    sys.stdout.write(json.dumps({"systemMessage": "harness hook %s: internal error (%s); action allowed"
                                 % (name, kind)}) + "\n")
    sys.stdout.flush()
    try:
        folder = os.path.join(root, ".harness", "runs")
        os.makedirs(folder, exist_ok=True)
        line = {"ts": datetime.now().isoformat(timespec="seconds"), "hook": name, "tool": tool,
                "error": "%s: %s" % (kind, str(exc)[:300])}
        with open(os.path.join(folder, "hook-errors.jsonl"), "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        pass


def block(message, extra=None):
    sys.stderr.write(message + "\n")
    if extra:
        sys.stdout.write(json.dumps(extra) + "\n")
    return 2


def current_context(root, session):
    """The cycle context of the session; an unreadable marker is reported, not fatal."""
    try:
        return core("scope").current(root, session), None
    except Exception as exc:   # noqa: BLE001 - HK-04: a corrupt marker never blocks
        return None, exc


def pre_bash(payload, root):
    guard, scope = core("guard"), core("scope")
    tool = payload.get("tool_name", "")
    command = (payload.get("tool_input") or {}).get("command", "")
    if not isinstance(command, str) or not command.strip():
        return 0, None
    ctx, ctx_error = current_context(root, payload.get("session_id"))
    check = scope.deletable_factory(root, ctx)
    violation = guard.analyze(command, powershell=(tool == "PowerShell"), deletable=check)
    if violation is None:
        return 0, ctx_error
    if ctx:       # HG-05: with an active cycle the block is also a stop
        try:
            core("stops").write(root, ctx["skill"], "destructive",
                                {"reason": violation.message(), "items": [violation.fragment],
                                 "spec": ctx.get("spec"), "phase": ctx.get("phase")},
                                os.path.join(root, ctx["run"]))
        except Exception as exc:   # noqa: BLE001 - the block itself must still happen
            report_error("pre-bash", tool, root, exc)
    return block(violation.message()), None


def pre_edit(payload, root):
    scope = core("scope")
    ctx, ctx_error = current_context(root, payload.get("session_id"))
    if ctx is None:
        return 0, ctx_error
    data = payload.get("tool_input") or {}
    path = data.get("file_path") or data.get("notebook_path") or data.get("path")
    if not path:
        return 0, None
    rel = scope.normalize(path, root)
    if rel is None:
        return 0, None
    if scope.secret_hit(rel):
        core("stops").write(root, ctx["skill"], "secret", {"reason": "The cycle tried to edit a secret file: %s" % rel,
                                                         "items": [rel], "spec": ctx.get("spec")},
                            os.path.join(root, ctx["run"]))
        return block("Blocked: %s is a secret file; edit it yourself if it is wanted." % rel), None
    if ctx["skill"] == "ship-pr":
        message = scope.diff_violation(root, ctx, rel)
    else:
        message = scope.build_violation(root, ctx, rel)
    if message is None:
        return 0, None
    drift = scope.record_drift(root, ctx, rel)
    if drift["stop"]:
        final = "second scope drift in this cycle: cycle stopped (see %s)" % drift["stop"]
        return block(message + "\n" + final, {"continue": False, "stopReason": final}), None
    return block(message), None


def post_edit(payload, root):
    try:
        with open(os.path.join(root, ".claude", "harness.json"), encoding="utf-8-sig") as fh:
            lint = json.load(fh).get("hooks", {}).get("lint") or []
    except (OSError, ValueError, AttributeError):
        return 0, None
    if not lint:
        return 0, None
    scope = core("scope")
    data = payload.get("tool_input") or {}
    path = data.get("file_path") or data.get("notebook_path")
    rel = scope.normalize(path, root) if path else None
    if rel is None:
        return 0, None
    notes = []
    for entry in lint:
        glob = entry.get("glob", "*")
        if not (scope.matches(glob, rel) or scope.matches(glob, rel.rsplit("/", 1)[-1])):
            continue
        argv = [str(a).replace("{file}", os.path.join(root, rel)) for a in entry.get("argv", [])]
        try:
            res = subprocess.run(argv, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                 timeout=float(entry.get("timeout_s", 30)))
            text = "\n".join(((res.stdout or "") + (res.stderr or "")).splitlines()[:LINT_SUMMARY_LINES])
            notes.append("lint %s: exit %d%s" % (argv[0], res.returncode, "\n" + text if text.strip() else ""))
        except FileNotFoundError:
            notes.append("lint %s: command not found (skipped)" % (argv[0] if argv else "?"))
        except subprocess.TimeoutExpired:
            notes.append("lint %s: timed out after %ss (skipped)" % (argv[0], entry.get("timeout_s", 30)))
    if notes:
        summary = "\n".join(notes)[:LINT_SUMMARY_CHARS]
        sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                                           "additionalContext": summary}}) + "\n")
    return 0, None


def main(argv):
    name = argv[1] if len(argv) > 1 else ""
    if name not in NAMES:
        sys.stderr.write("usage: hook.py pre-edit|pre-bash|post-edit\n")
        return 0       # an unknown sub-command never blocks a tool
    root = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    tool = ""

    def expire():
        report_error(name, tool, root, TimeoutError("budget of %gs exceeded" % BUDGET_S))
        os._exit(0)

    timer = threading.Timer(BUDGET_S, expire)
    timer.daemon = True
    timer.start()
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("stdin is not a JSON object")
        except ValueError as exc:
            report_error(name, tool, root, exc)
            return 0
        root = project_root(payload)
        tool = payload.get("tool_name", "")
        if os.environ.get("HCORE_TEST_MODE") == "1" and os.environ.get("HCORE_HOOK_FAIL") == "1":
            raise RuntimeError("injected failure")
        code, warning = {"pre-edit": pre_edit, "pre-bash": pre_bash, "post-edit": post_edit}[name](payload, root)
        if warning is not None:
            report_error(name, tool, root, warning)
        return code
    except Exception as exc:   # noqa: BLE001 - HK-04: any failure lets the action pass
        report_error(name, tool, root, exc)
        return 0
    finally:
        timer.cancel()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
