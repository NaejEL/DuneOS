# Migrations of an upgrade (MI-01 to MI-07). Pure: they compute the new content of
# the target files, never touch a source file, and are idempotent.
#
# - MI-01: lab-models.json -> models.json (MD-05); the old file stays byte for byte.
# - MI-02: the run registries .harness/runs/*.registry.jsonl are published to
#   .claude/findings.jsonl (RG-03) except CONFIRMED (the deliverable that holds
#   the finding is unknown); each source is recorded in migrations[] by its sha256
#   and is never published again. An entry already in the project registry is kept.
# - MI-05: the old audit deliverables are only reported.

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import models, registry

LEGACY_DELIVERABLES = ("ROADMAP-legacy.md", "AUDIT-vibecode.md", "AUDIT-lab.md")
RUN_REGISTRY = re.compile(r"^(?P<skill>.+)-\d{8}-\d{6}\.registry\.jsonl$")


def models_text(root: Path) -> Optional[str]:
    """MI-01: the content of models.json to create, or None."""
    root = Path(root)
    target, legacy = root / models.MODELS_FILE, root / models.LEGACY_LAB_FILE
    if target.exists() or not legacy.exists():
        return None
    data = models._read_mapping(legacy, prefix="lab.")     # noqa: SLF001
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def migrated_sources(stamp: Optional[Dict[str, Any]]) -> set:
    done = set()
    for mig in (stamp or {}).get("migrations", []):
        done.update(s.get("sha256") for s in mig.get("sources", []))
    return done


def registries_text(root: Path, done: set, at: str) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """MI-02: (new content of findings.jsonl or None, migrations[] entries to append)."""
    root = Path(root)
    runs = root / ".harness" / "runs"
    sources: List[Dict[str, str]] = []
    chosen: Dict[str, Dict[str, Any]] = {}
    for path in sorted(runs.glob("*.registry.jsonl")) if runs.is_dir() else []:
        match = RUN_REGISTRY.match(path.name)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if not match or digest in done:
            continue
        source = match.group("skill")
        run = path.name[:-len(".registry.jsonl")]
        for key, event in registry.Registry(path).last.items():
            if event.get("verdict") in registry.PUBLISHED:
                chosen[key] = registry.ProjectRegistry.entry_of(event, source, run)
        sources.append({"path": path.relative_to(root).as_posix(), "sha256": digest})
    if not sources:
        return None, []
    project = registry.ProjectRegistry(root)
    entries = project.read()
    for key, entry in chosen.items():
        entries.setdefault(key, entry)
    text = project.dumps(entries)
    current = project.path.read_text(encoding="utf-8") if project.path.exists() else None
    migration = {"id": "run-registries", "at": at, "sources": sources}
    return (text if text != current else None), [migration]


def legacy_deliverables(root: Path) -> List[str]:
    """MI-05."""
    return [n for n in LEGACY_DELIVERABLES if (Path(root) / n).is_file()]
