# Stack detection and size bounds of a project (single copy of the six
# detections that were written in prose).
#
# - ST-01: union of the markers of the six copies, searched at the root and
#   one level of subfolders (software-factory:31).
# - ST-02: output: markers and paths of the manifests found, plus the core
#   stacks (node, dotnet, python, cpp, rust) that feed SF-01. The core stacks
#   come from the markers of software-factory:31 only (as at BASE1): the
#   markers added by the other copies (environment.yml, *.ipynb) give the
#   python family, never the python stack. Reading the manifests (scripts,
#   dependencies) stays with the skill's judgment.
# - ST-03: list of the artifact and bulky folders, union of legacy-audit:37
#   and code-index:49; the ones present are reported.
#   Every folder of the first level is searched for markers, whatever its
#   name (software-factory:31).
# - ST-04: count of the files outside artifact folders: those of
#   legacy-audit:37 at any depth, the bulky folders added by code-index:49
#   at the root only. Beyond 2000: sampled, tree limited to 3 levels, 40
#   files read per auditor.
# - ST-05: a journal longer than 1500 lines is sampled: its 15 last entries.

from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# (pattern, family, core stack or None). A pattern ending with "/" is a folder.
MARKERS: Tuple[Tuple[str, str, Optional[str]], ...] = (
    ("*.sln", "dotnet", "dotnet"),
    ("*.csproj", "dotnet", "dotnet"),
    ("pyproject.toml", "python", "python"),
    ("requirements.txt", "python", "python"),
    ("setup.py", "python", "python"),
    ("environment.yml", "python", None),
    ("*.ipynb", "python", None),
    ("package.json", "node", "node"),
    ("CMakeLists.txt", "cpp", "cpp"),
    ("Makefile", "cpp", "cpp"),
    ("go.mod", "go", None),
    ("Cargo.toml", "rust", "rust"),
    ("Dockerfile", "containers", None),
    ("docker-compose*.yml", "containers", None),
    ("compose*.yml", "containers", None),
    ("ansible.cfg", "ansible", None),
    ("playbooks/", "ansible", None),
    ("roles/", "ansible", None),
    ("*.tf", "terraform", None),
)
CORE_STACKS = ("node", "dotnet", "python", "cpp", "rust")

# ST-03: legacy-audit:37 (artifacts, at any depth), then the bulky folders
# added by code-index:49 (relative to the root).
ANY_DEPTH_DIRS = ("node_modules", ".venv", "venv", "bin", "obj", "build", "dist", "target",
                  "__pycache__", ".git")
ROOT_DIRS = ("data", "checkpoints", "models", "wandb", "runs", "experiments/results", "factory-logs",
             "lab-logs")
ARTIFACT_DIRS = ANY_DEPTH_DIRS + ROOT_DIRS

SAMPLING_THRESHOLD = 2000
SAMPLED_TREE_DEPTH = 3
SAMPLED_FILES_PER_AUDITOR = 40
JOURNAL_THRESHOLD = 1500
JOURNAL_LAST_ENTRIES = 15


def _is_artifact(rel: str) -> bool:
    """rel: posix path of a folder relative to the root."""
    return rel.split("/")[-1] in ANY_DEPTH_DIRS or rel in ROOT_DIRS


def _entries(folder: Path) -> List[os.DirEntry]:
    try:
        with os.scandir(str(folder)) as it:
            return sorted(it, key=lambda e: e.name)
    except OSError:
        return []


def _match(entry: os.DirEntry) -> List[Tuple[str, str, Optional[str]]]:
    is_dir = entry.is_dir()
    found = []
    for pattern, family, stack in MARKERS:
        if pattern.endswith("/"):
            if is_dir and entry.name == pattern[:-1]:
                found.append((pattern, family, stack))
        elif not is_dir and fnmatch.fnmatchcase(entry.name, pattern):
            found.append((pattern, family, stack))
    return found


def detect(root: Path) -> Dict[str, Any]:
    """ST-01, ST-02, ST-03: markers at the root and one level of subfolders."""
    root = Path(root)
    hits: List[Dict[str, Any]] = []
    artifacts: List[str] = []
    levels = [("", root)]
    for entry in _entries(root):
        if entry.is_dir():
            levels.append((entry.name + "/", Path(entry.path)))
    for prefix, folder in levels:
        for entry in _entries(folder):
            rel = prefix + entry.name
            if entry.is_dir() and _is_artifact(rel):
                artifacts.append(rel + "/")
            for pattern, family, stack in _match(entry):
                hits.append({"marker": pattern, "path": rel + ("/" if pattern.endswith("/") else ""),
                             "family": family, "stack": stack})
    stacks = [s for s in CORE_STACKS if any(h["stack"] == s for h in hits)]
    families: List[str] = []
    for h in hits:
        if h["family"] not in families:
            families.append(h["family"])
    return {
        "markers": sorted({h["marker"] for h in hits}),
        "manifests": [h["path"] for h in hits],
        "found": hits,
        "families": families,
        "stacks": stacks,
        "artifact_dirs": sorted(artifacts),
    }


def count_files(root: Path) -> int:
    """ST-04: files outside the artifact folders (at any depth)."""
    root = Path(root)
    total = 0
    for current, dirs, files in os.walk(str(root)):
        rel = Path(current).relative_to(root).as_posix()
        prefix = "" if rel == "." else rel + "/"
        dirs[:] = [d for d in dirs if not _is_artifact(prefix + d)]
        total += len(files)
    return total


def bounds(root: Path) -> Dict[str, Any]:
    """ST-04: sampling of a large project."""
    files = count_files(root)
    sampled = files > SAMPLING_THRESHOLD
    return {"files": files, "sampled": sampled,
            "tree_depth": SAMPLED_TREE_DEPTH if sampled else None,
            "files_per_auditor": SAMPLED_FILES_PER_AUDITOR if sampled else None}


def journal_bounds(path: Path) -> Dict[str, Any]:
    """ST-05: a journal of more than 1500 lines is read through its 15 last
    entries (what an entry is stays with the skill: the journal format)."""
    with open(str(path), encoding="utf-8", errors="replace", newline="") as fh:
        lines = sum(1 for _ in fh)
    sampled = lines > JOURNAL_THRESHOLD
    return {"file": Path(path).as_posix(), "lines": lines, "sampled": sampled,
            "last_entries": JOURNAL_LAST_ENTRIES if sampled else None}


def survey(root: Path) -> Dict[str, Any]:
    """ST-01 to ST-04 in one call."""
    result = detect(root)
    result.update(bounds(root))
    return result
