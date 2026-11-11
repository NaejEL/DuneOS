# Deterministic rules of the ai-lab skill (AL-01 to AL-09).
#
# - AL-01: permissions.deny: Python rules, generic rules, then the data,
#   weights and cache folders that are PRESENT in the project only.
# - AL-02: tasks.json: input labProtocol and four "Lab: ..." tasks, with
#   their windows variants.
# - AL-03: .gitignore += lab-logs/, experiments/results/ and .harness/runs/
#   (deviation E2).
# - AL-04: generated files are never overwritten, states reported
#   (fusion.install_file, FU-04).
# - AL-05, AL-06: .claude/models.json replaces lab-models.json (MD-05) and the
#   presets are data (MD-08): hcore/models.py.
# - AL-07: ci/lab.sh and ci/lab.ps1 rendered from templates/lab-ci.*: mandatory
#   argument, pre-registered status line checked, --max-turns 150, allow list
#   carrying the interpreter of the core, lab-logs/, exit code propagated.
# - AL-08: the core is copied under .claude/harness/ (deviation E1):
#   fusion.copy_core.
# - AL-09: gate ai-lab.code-index: hcore/gates.py.

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from . import fusion

PYTHON_DENY = ["Read(./.venv/**)", "Read(./venv/**)", "Read(./**/__pycache__/**)", "Read(./.pytest_cache/**)",
               "Read(./**/*.egg-info/**)"]
GENERIC_DENY = ["Read(./lab-logs/**)", "Read(./experiments/results/**)", "Read(./.git/objects/**)"]
DATA_DIRS = ("data", "checkpoints", "models", "wandb", "runs")
WEIGHT_SUFFIXES = (".safetensors", ".pt", ".bin")
GITIGNORE_LINES = ["lab-logs/", "experiments/results/", ".harness/runs/", ".harness/tmp/"]
SKIP_WALK = {".git", ".venv", "venv", "node_modules", "__pycache__", "lab-logs"}
WALK_LIMIT = 20000

LAB_INPUT = {"id": "labProtocol", "type": "promptString",
             "description": "Path of the pre-registered protocol (experiments/EXP-*.md)"}
REMINDER = 'Open Claude Code and type: /lab-run "<your question>"  or  /lab-run suivant'


def present_rules(root: Path) -> List[str]:
    """AL-01: deny rules for the data, weights and caches that exist."""
    root = Path(root)
    rules = ["Read(./%s/**)" % d for d in DATA_DIRS if (root / d).is_dir()]
    suffixes, cache, seen = set(), False, 0
    for current, dirs, files in os.walk(str(root)):
        dirs[:] = [d for d in dirs if d not in SKIP_WALK]
        cache = cache or ".cache" in dirs
        suffixes.update(os.path.splitext(f)[1] for f in files)
        seen += len(files)
        if seen > WALK_LIMIT:
            break
    rules += ["Read(./**/*%s)" % s for s in WEIGHT_SUFFIXES if s in suffixes]
    if cache:
        rules.append("Read(./**/.cache/**)")
    return rules


def deny_rules(root: Path) -> List[str]:
    rules: List[str] = []
    for rule in PYTHON_DENY + GENERIC_DENY + present_rules(root):
        if rule not in rules:
            rules.append(rule)
    return rules


def lab_tasks(test_command: Optional[str] = None, test_windows: Optional[str] = None) -> List[Dict[str, Any]]:
    tasks = [
        {"label": "Lab: run headless", "type": "shell", "command": "./ci/lab.sh ${input:labProtocol}",
         "windows": {"command": "pwsh -File ci/lab.ps1 ${input:labProtocol}"}, "problemMatcher": []},
        {"label": "Lab: run headless (YOLO)", "type": "shell", "command": "./ci/lab.sh ${input:labProtocol} --yolo",
         "windows": {"command": "pwsh -File ci/lab.ps1 ${input:labProtocol} -Yolo"}, "problemMatcher": []},
    ]
    if test_command:
        test: Dict[str, Any] = {"label": "Lab: tests unitaires", "type": "shell", "command": test_command,
                                "problemMatcher": []}
        if test_windows and test_windows != test_command:
            test["windows"] = {"command": test_windows}
        tasks.append(test)
    tasks.append({"label": "Lab: nouvelle expérience (session)", "type": "shell",
                  "command": "echo '%s'" % REMINDER, "problemMatcher": []})
    return tasks


def apply(root: Path, test_command: Optional[str] = None, test_windows: Optional[str] = None) -> Dict[str, str]:
    """AL-01 to AL-03: the three merges planned, then written (all or nothing)."""
    root = Path(root)
    plans = {
        ".claude/settings.json": fusion.plan_json(
            root / ".claude" / "settings.json", [fusion.op_array_union(("permissions", "deny"), deny_rules(root))]),
        ".vscode/tasks.json": fusion.plan_json(
            root / ".vscode" / "tasks.json", [fusion.op_tasks([LAB_INPUT], lab_tasks(test_command, test_windows))]),
        ".gitignore": fusion.plan_lines(root / ".gitignore", GITIGNORE_LINES),
    }
    return fusion.write_plans(root, plans)


# ---------------------------------------------------------------- AL-07

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
BASE_ALLOW = ["Read", "Glob", "Grep", "Write", "Edit", "Bash(git *)"]


def allow_list(extra: Iterable[str]) -> str:
    items = list(BASE_ALLOW)
    for tool in extra:
        tool = tool.strip()
        if tool and tool not in items:
            items.append(tool)
    for tool in items:
        if any(c in tool for c in "\"',\r\n"):
            raise fusion.FusionError("invalid allow-list entry: %r" % (tool,))
    return ",".join(items)


def ci_scripts(extra_tools: Sequence[str]) -> Dict[str, str]:
    """ci/lab.sh and ci/lab.ps1 text (LF endings), allow list included."""
    allowed = allow_list(extra_tools)
    out = {}
    for rel, name in (("ci/lab.sh", "lab-ci.sh"), ("ci/lab.ps1", "lab-ci.ps1")):
        text = (TEMPLATES / name).read_text(encoding="utf-8").replace("@ALLOWED@", allowed)
        out[rel] = text
    return out


def install_ci(root: Path, extra_tools: Sequence[str]) -> Dict[str, str]:
    """AL-04, AL-07: the scripts, never overwriting a different existing one."""
    return {rel: fusion.install_file(Path(root) / rel, text) for rel, text in ci_scripts(extra_tools).items()}
