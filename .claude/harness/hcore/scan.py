# /harness-doctor --scan <root> (SN-01 to SN-06): one line per equipped project.
#
# - SN-01: depth 3 by default (the root is depth 0); a depth that is not an integer >= 0
#   or a missing root is a usage error.
# - SN-02: equipped = a stamp or the DT-01 markers. Not walked: .git/, .claude/, the
#   artifact folders (ST-03), links and junctions. The walk continues below an equipped
#   project (monorepo).
# - SN-03: {path, equipped_by, skills: {skill: describe}, lag, last_cycle, next}, sorted by path.
# - SN-04: last cycle = the journal .harness/runs/<name>-<YYYYMMDD-HHMMSS>.jsonl with the
#   most recent timestamp IN THE NAME (never the modification date); verdict = the last
#   line carrying one. .registry.jsonl, .state.json, .plan.json, .diff, .lock are ignored.
# - SN-05: absolute read only: nothing is created or modified under the root.
# - SN-06: a scan never launches an upgrade.

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import CORE_VERSION, history, stack, stamp, version

JOURNAL = re.compile(r"^(?P<name>.+)-(?P<ts>\d{8}-\d{6})\.jsonl$")
NEXT = {"software-factory": "/software-factory --upgrade", "code-index": "/code-index --upgrade"}


class ScanError(ValueError):
    """Invalid depth or missing root (exit 2)."""


def parse_depth(value: Optional[str]) -> int:
    if value is None:
        return 3
    if not re.fullmatch(r"\d+", str(value).strip()):
        raise ScanError("--depth expects an integer >= 0, got %r" % (value,))
    return int(value)


def is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attrs = os.lstat(str(path)).st_file_attributes        # Windows junctions are reparse points
    except (AttributeError, OSError):
        return False
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def last_cycle(project: Path) -> Optional[Dict[str, Any]]:
    folder = project / ".harness" / "runs"
    best = None
    for entry in (os.scandir(str(folder)) if folder.is_dir() else []):
        match = JOURNAL.match(entry.name)
        if match and entry.is_file() and not entry.name.endswith(".registry.jsonl"):
            key = (match.group("ts"), match.group("name"))
            if best is None or key > best[0]:
                best = (key, Path(entry.path))
    if best is None:
        return None
    verdict = None
    try:
        for line in best[1].read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line).get("verdict")
            except (ValueError, AttributeError):
                continue
            if value is not None:
                verdict = value
    except (OSError, UnicodeDecodeError):
        pass
    ts = best[0][0]
    at = "%s-%s-%sT%s:%s:%s" % (ts[0:4], ts[4:6], ts[6:8], ts[9:11], ts[11:13], ts[13:15])
    return {"skill": best[0][1], "at": at, "verdict": verdict}


def project_row(project: Path, rel: str, repo: Dict[str, Any], core_dir: Path, run: Any = None) -> Optional[Dict[str, Any]]:
    try:
        data = stamp._read(project)                                # noqa: SLF001
    except stamp.StampError:
        data = None
    skills = [s for s in stamp.SKILL_NAMES if s in ((data or {}).get("skills") or {})] if data else []
    equipped_by = "stamp" if data and data.get("skills") else None
    if not equipped_by:
        found = stamp.inferred_skills(project)
        if not found and not data:
            return None
        skills = skills or found
        equipped_by = "stamp" if data else "inferred"
    described: Dict[str, str] = {}
    lags = []
    for skill in skills:
        installed = _installed(data, skill, project, core_dir)
        described[skill] = installed["describe"] or ("core-%s" % installed["core_version"]
                                                      if installed["core_version"] else "unknown")
        lags.append(version.lag(installed["commit"], repo.get("commit"), Path(repo.get("path") or "."),
                                installed["core_version"], CORE_VERSION, run))
    lag = lags[0] if lags else {"status": "unknown", "commits": None}
    for item in lags:
        if item["status"] == "behind" and (lag["status"] != "behind" or (item["commits"] or 0) > (lag["commits"] or 0)):
            lag = item
    behind = lag["status"] == "behind" or equipped_by == "inferred"
    nxt = NEXT.get(skills[0]) if behind and skills else None
    return {"path": rel, "equipped_by": equipped_by, "skills": described, "lag": lag,
            "last_cycle": last_cycle(project), "next": nxt}


def _installed(data: Optional[Dict[str, Any]], skill: str, project: Path, core_dir: Path) -> Dict[str, Any]:
    from . import upgrade
    return upgrade._installed(data, skill, project, core_dir)       # noqa: SLF001


def scan(root: Path, depth: int, repo: Dict[str, Any], core_dir: Optional[Path] = None,
         run: Any = None) -> List[Dict[str, Any]]:
    """SN-01 to SN-06."""
    root = Path(root)
    if not root.is_dir():
        raise ScanError("root not found: %s" % root)
    core_dir = Path(core_dir) if core_dir else history.CORE_DIR
    rows: List[Dict[str, Any]] = []

    def walk(folder: Path, level: int) -> None:
        rel = folder.relative_to(root).as_posix() if folder != root else "."
        row = project_row(folder, rel, repo, core_dir, run)
        if row is not None:
            rows.append(row)
        if level >= depth:
            return
        try:
            children = sorted(os.scandir(str(folder)), key=lambda e: e.name)
        except OSError:
            return
        for entry in children:
            path = Path(entry.path)
            if not entry.is_dir(follow_symlinks=False) or is_link(path):
                continue
            if entry.name in (".git", ".claude") or stack._is_artifact(entry.name):      # noqa: SLF001
                continue
            walk(path, level + 1)

    walk(root, 0)
    return sorted(rows, key=lambda r: r["path"])
