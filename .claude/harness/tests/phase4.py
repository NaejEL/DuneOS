# Shared tools of the Phase 4 and 5 tests (not a test module).

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

import helpers
from helpers import git, init_repo

TESTS = helpers.TESTS
HOOK = helpers.CORE / "hooks" / "hook.py"


def spec_text(status: str = "PROPOSEE", title: str = "Feature", header: Sequence[str] = (),
              questions: Sequence[str] = ()) -> str:
    """A spec in the Phase 4 format: status line, machine header, open questions."""
    body = "\n".join(questions) if questions else "Aucune"
    return "Statut : %s\n%s\n# SPEC — %s\n\n## Contexte\nx\n\n## Questions ouvertes\n%s\n" % (
        status, "".join(h + "\n" for h in header), title, body)


def write_spec(root: Path, name: str, text: str) -> str:
    path = Path(root) / "specs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return "specs/" + name


def project(tmp: Path, specs: Optional[Dict[str, str]] = None) -> Tuple[Path, Path]:
    """A work repository with a bare remote, the harness files committed (WT-09) and the specs pushed."""
    remote = Path(tmp) / "remote.git"
    remote.mkdir()
    git(remote, "init", "-q", "--bare", "-b", "main")
    work = Path(tmp) / "work"
    init_repo(work)
    (work / ".claude" / "harness").mkdir(parents=True)
    (work / ".claude" / "harness" / "harness.py").write_text("# stub of the copied core\n", encoding="utf-8")
    (work / ".claude" / "settings.json").write_text("{}\n", encoding="utf-8")
    for name, text in (specs or {}).items():
        write_spec(work, name, text)
    (work / "README.md").write_text("project\n", encoding="utf-8")
    exclude = work / ".git" / "info" / "exclude"
    exclude.parent.mkdir(exist_ok=True)
    exclude.write_text(".harness/runs/\n.claude/findings.jsonl\n.claude/findings.lock\n", encoding="utf-8")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "initial")
    git(work, "remote", "add", "origin", str(remote))
    git(work, "push", "-q", "-u", "origin", "main")
    return work, remote


def env_for(tmp: Path, **extra: str) -> Dict[str, str]:
    """HCORE_TEST_MODE environment with the simulated claude and a logging git."""
    env = {"HCORE_TEST_MODE": "1", "HCORE_NO_SLEEP": "1",
           "HCORE_CLAUDE": json.dumps([sys.executable, str(TESTS / "fake_claude.py")]),
           "FAKE_CLAUDE_LOG": str(Path(tmp) / "claude.log"), "FAKE_CLAUDE_SCENARIO": str(Path(tmp) / "scenario.json"),
           "FAKE_GIT_LOG": str(Path(tmp) / "git.log"),
           "HCORE_GIT": json.dumps([sys.executable, str(TESTS / "fake_git.py")])}
    env.update(extra)
    return env


def scenario(tmp: Path, data: dict) -> None:
    Path(tmp, "scenario.json").write_text(json.dumps(data), encoding="utf-8")


def claude_calls(tmp: Path):
    path = Path(tmp) / "claude.log"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def git_calls(tmp: Path):
    path = Path(tmp) / "git.log"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def stamp(root: Path, **data) -> None:
    path = Path(root) / ".claude" / "harness.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def hook(name: str, payload, root: Path, env: Optional[Dict[str, str]] = None, raw: Optional[str] = None):
    """Runs hook.py <name> with the JSON payload on stdin; CLAUDE_PROJECT_DIR = root."""
    import subprocess
    full = dict(os.environ, CLAUDE_PROJECT_DIR=str(root), PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    full.update(env or {})
    return subprocess.run([sys.executable, "-B", str(HOOK), name], input=raw if raw is not None else json.dumps(payload),
                          capture_output=True, text=True, encoding="utf-8", env=full, timeout=60, cwd=str(root))
