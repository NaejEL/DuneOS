# Cumulative registries of findings: the run registry (ship-pr, SP-07,
# SP-09, D2) and the project registry (RG-01 to RG-07, below).
#
# File .harness/runs/ship-pr-<timestamp>.registry.jsonl, one JSON line per
# finding event, keys file/line/title/verdict. The last event of a key gives
# its current status. Deterministic deduplication on file + normalized title,
# all statuses included (a REJECTED finding does not come back to life).
# Semantic dedup (same subject, other title) is left to the prompt's judgment;
# the prompt records it with 'ship-pr drop' (status DUPLICATE, key 'of').
# Adding is idempotent: an event identical to the last event of its key is
# not rewritten (a relaunch after an interruption does not duplicate).

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

# FD-02 statuses a run registry can hold (SETTLED: an audit question settled by the journal).
STATUSES = ("CONFIRMED", "REJECTED", "UNVALIDATED", "FIXED", "NOT_FIXED", "DUPLICATE", "SETTLED")


def normalize_title(title: str) -> str:
    text = unicodedata.normalize("NFKD", title)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^\w]+", " ", text.lower())
    return " ".join(text.split())


def finding_key(file: str, title: str) -> str:
    path = file.replace("\\", "/").strip()
    while path.startswith("./"):
        path = path[2:]
    return path + "::" + normalize_title(title)


class Registry:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.status: Dict[str, str] = {}
        self.last: Dict[str, Dict[str, Any]] = {}
        if self.path.exists():
            with open(self.path, encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        entry = json.loads(line)
                        self.status[entry["key"]] = entry["verdict"]
                        self.last[entry["key"]] = entry

    def known(self, key: str) -> bool:
        return key in self.status

    def current(self, key: str) -> Optional[str]:
        return self.status.get(key)

    def add(self, finding: Dict[str, Any], verdict: str, **extra: Any) -> Dict[str, Any]:
        if verdict not in STATUSES:
            raise ValueError("unknown registry status: %r" % (verdict,))
        key = finding_key(finding["file"], finding["title"])
        entry: Dict[str, Any] = {
            "key": key,
            "file": finding["file"],
            "line": finding["line"],
            "title": finding["title"],
            "verdict": verdict,
        }
        entry.update({k: v for k, v in extra.items() if v is not None})
        if self.last.get(key) == entry:
            return entry
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.status[key] = verdict
        self.last[key] = entry
        return entry


# ------------------------------------------------------------ project registry
#
# - RG-01: .claude/findings.jsonl, one line per finding (its last state),
#   sorted by key, rewritten atomically under lock.
# - RG-02: the key is the D2 key (finding_key). A pattern with occurrences:
#   "::<normalized pattern>"; a finding without a file: "::<normalized title>".
# - RG-03: fed at the end of a run from the run registry. Published: REJECTED,
#   FIXED, NOT_FIXED, SETTLED; CONFIRMED only when the deliverable that holds
#   it is written; neither DUPLICATE nor UNVALIDATED. Idempotent (an unchanged
#   content is not rewritten).
# - RG-04: read when a run starts. Only REJECTED and SETTLED prevent a
#   resurrection: the finding is set aside before any validator, noted
#   previous=<status>@<source>. FIXED and NOT_FIXED are passed on as history
#   without setting the finding aside (a regression with the same title must
#   be able to come back).
# - RG-05: git exclusion through .git/info/exclude (the registry, its lock
#   and the temporary files of its atomic rewrite), idempotent, rewritten
#   atomically under lock; ship-pr ensures it from its preflight; a
#   directory without git: nothing (Q-E).
# - RG-06: inter-process lock, the one of runs.locked (.claude/findings.lock).
# - RG-07: the existing D2 run registries stay readable without migration
#   (Registry above is unchanged; publish reads its last event per key).

PROJECT_FILE = Path(".claude") / "findings.jsonl"
PROJECT_EXCLUDE = (".claude/findings.jsonl", ".claude/findings.lock", ".claude/findings.jsonl.*.tmp")
PUBLISHED = ("REJECTED", "FIXED", "NOT_FIXED", "SETTLED")
BLOCKING = ("REJECTED", "SETTLED")


def project_key(finding: Dict[str, Any]) -> str:
    """RG-02: key of an FD-01 finding (or of a {file, title} entry)."""
    if finding.get("occurrences"):
        return "::" + normalize_title(finding["title"])
    file = finding.get("file")
    if file is None:
        file = (finding.get("evidence") or {}).get("file")
    if isinstance(file, str) and file.strip():
        return finding_key(file, finding["title"])
    return "::" + normalize_title(finding["title"])


def git_exclude(root: Path, lines: Iterable[str], run: Optional[Callable[..., Any]] = None) -> Optional[List[str]]:
    """RG-05: adds `lines` to .git/info/exclude when missing, under lock and
    atomically (two skills may add their lines at the same time). Returns the
    lines added, or None when `root` is not in a git repository."""
    from . import runs          # local: runs imports the journal, which imports the gates
    from .proc import Runner    # local: keeps this module free of process code at import
    run = run or Runner(cwd=str(root))
    res = run(["git", "rev-parse", "--git-path", "info/exclude"])
    if not res.ok or not res.out.strip():
        return None
    path = Path(res.out.strip())
    if not path.is_absolute():
        path = Path(root) / path
    path.parent.mkdir(parents=True, exist_ok=True)
    with runs.locked_file(path.with_name(path.name + ".hcore-lock")):
        text = ""
        if path.exists():
            with open(str(path), encoding="utf-8", newline="") as fh:
                text = fh.read()
        present = {line.strip() for line in text.splitlines()}
        missing = [line for line in lines if line not in present]
        if missing:
            nl = "\r\n" if "\r\n" in text else "\n"
            if text and not text.endswith("\n"):
                text += nl
            runs.write_atomic(path, text + "".join(line + nl for line in missing))
    return missing


class ProjectRegistry:
    """RG-01 to RG-06: .claude/findings.jsonl of a project."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.path = self.root / PROJECT_FILE

    def read(self) -> Dict[str, Dict[str, Any]]:
        entries: Dict[str, Dict[str, Any]] = {}
        if self.path.exists():
            with open(str(self.path), encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        entry = json.loads(line)
                        entries[entry["key"]] = entry
        return entries

    def lookup(self, key: str, entries: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
        """RG-04: what a run does with a new finding of this key."""
        entry = (self.read() if entries is None else entries).get(key)
        if entry is None:
            return {"key": key, "dismiss": False, "previous": None}
        previous = "%s@%s" % (entry["status"], entry["source"])
        return {"key": key, "dismiss": entry["status"] in BLOCKING, "previous": previous}

    @staticmethod
    def dumps(entries: Dict[str, Dict[str, Any]]) -> str:
        return "".join(json.dumps(entries[k], ensure_ascii=False, sort_keys=True) + "\n" for k in sorted(entries))

    @staticmethod
    def entry_of(e: Dict[str, Any], source: str, run: str) -> Dict[str, Any]:
        """RG-03: the project entry of a run registry event."""
        status = e.get("verdict") or e.get("status")
        entry = {"key": e["key"], "status": status, "source": source, "run": run,
                 "title": e.get("title"), "file": e.get("file"), "line": e.get("line"),
                 "reason": e.get("reason"), "fix_commit": e.get("commit") or e.get("fix_commit"),
                 "settled_by": e.get("settled_by")}
        return {k: v for k, v in entry.items() if v is not None}

    def publish(self, run_entries: Iterable[Dict[str, Any]], source: str, run: str,
                deliverable_written: bool = False, exclude: bool = True) -> Dict[str, Any]:
        """RG-03, RG-05, RG-06: publishes the last state of each finding of a run.
        exclude=False (AU-12, GIT_ABSENT): no git command, no git exclusion."""
        from . import runs   # local: runs imports the journal, which imports the gates
        chosen: Dict[str, Dict[str, Any]] = {}
        for e in run_entries:
            status = e.get("verdict") or e.get("status")
            if status in PUBLISHED or (status == "CONFIRMED" and deliverable_written):
                chosen[e["key"]] = self.entry_of(e, source, run)
        excluded = git_exclude(self.root, PROJECT_EXCLUDE) if exclude else None
        if not chosen:
            return {"file": PROJECT_FILE.as_posix(), "published": 0, "changed": False,
                    "excluded": excluded}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with runs.locked(self.path):
            before = self.path.read_text(encoding="utf-8") if self.path.exists() else None
            entries = self.read()
            entries.update(chosen)
            text = self.dumps(entries)
            changed = text != before
            if changed:
                runs.write_atomic(self.path, text)
        return {"file": PROJECT_FILE.as_posix(), "published": len(chosen), "changed": changed,
                "excluded": excluded}
