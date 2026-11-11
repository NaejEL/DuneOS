# Shared tools of the Phase 2 tests (not a test module): fixture projects under their real names.

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

import helpers

RENAMES = {"github": ".github", "claude-md.txt": "CLAUDE.md", "gitignore.txt": ".gitignore"}


def project(dest: Path, *fixtures: str) -> Path:
    """Copies the fixture projects into `dest` (a dotted name is stored without its dot, README convention)."""
    dest.mkdir(parents=True, exist_ok=True)
    for name in fixtures:
        src = helpers.FIXTURES / name
        for path in sorted(src.rglob("*")):
            if path.is_dir():
                continue
            rel = path.relative_to(src)
            parts = [RENAMES.get(p, p) for p in rel.parts]
            target = dest.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(str(path), str(target))
    return dest


def signals_for(root: Path, found: Dict[str, Any]) -> Optional[Path]:
    """The scout agent's JSON block, written as the Write tool would."""
    return helpers.write_output(root, "scout-signals.json", json.dumps(found))
