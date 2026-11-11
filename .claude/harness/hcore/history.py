# History of the core and state of the project copies (.claude/harness/).
#
# - CO-01: history.json = one entry per distinct state of skills/_core in the git
#   history: {id, commit, core_version, files: {rel: sha256 of the content with LF
#   line endings}}. `id` = sha256 of the sorted `files` table; `commit` is null for
#   the current state when it is not committed yet. Excluded from the tables:
#   history.json, MANIFEST.json, __pycache__/, *.pyc. `rebuild` (a development tool
#   of the claude-skills repository) regenerates the file from git; `check` says
#   whether the tree is an entry of the file.
# - CO-02: MANIFEST.json of a copy = {core_version, repo, entry, files}.
# - CO-03: state of each file of a copy: pristine (hash of the MANIFEST, or, without
#   MANIFEST, hash found in an entry for that path), customized, missing, extra
#   (never deleted, reported).
# - CO-05: installed version of a copy without MANIFEST: the last entry whose
#   files all match (exact), or files matching different entries (mixed).

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import version

CORE_DIR = Path(__file__).resolve().parent.parent
HISTORY = "history.json"
MANIFEST = "MANIFEST.json"
NOT_TABLED = (HISTORY, MANIFEST)
CORE_PREFIX = "skills/_core/"


def sha(data: bytes) -> str:
    """Hash of a content, line endings normalized to LF."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def tabled(rel: str) -> bool:
    parts = rel.split("/")
    return rel not in NOT_TABLED and "__pycache__" not in parts and not rel.endswith((".pyc", ".pyo"))


def tree(folder: Path) -> List[str]:
    """Relative posix paths of a core tree (bytecode and MANIFEST.json excluded)."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    out = []
    for src in sorted(folder.rglob("*")):
        rel = src.relative_to(folder).as_posix()
        if src.is_file() and rel != MANIFEST and "__pycache__" not in rel.split("/")                 and not rel.endswith((".pyc", ".pyo")):
            out.append(rel)
    return out


def table(folder: Path) -> Dict[str, str]:
    return {rel: sha((Path(folder) / rel).read_bytes()) for rel in tree(folder) if tabled(rel)}


def entry_id(files: Dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def load(core_dir: Path) -> List[Dict[str, Any]]:
    try:
        data = json.loads((Path(core_dir) / HISTORY).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return []
    return data if isinstance(data, list) else []


def check(core_dir: Path) -> Dict[str, Any]:
    """CO-01: exit 0 when the table of the tree is an entry of history.json."""
    entries = load(core_dir)
    current = table(core_dir)
    if any(e.get("id") == entry_id(current) for e in entries):
        return {"ok": True, "files": []}
    last = entries[-1]["files"] if entries else {}
    files = sorted(r for r in set(current) | set(last) if current.get(r) != last.get(r))
    return {"ok": False, "files": files or [HISTORY], "history_entries": len(entries)}


# ---------------------------------------------------------------- rebuild

def _run(repo: Path, args: Sequence[str], data: Optional[bytes] = None) -> bytes:
    res = subprocess.run(["git"] + list(args), cwd=str(repo), input=data, capture_output=True, timeout=300)
    if res.returncode != 0:
        raise ValueError("git %s failed: %s" % (" ".join(args), res.stderr.decode("utf-8", "replace").strip()))
    return res.stdout


def _blobs(repo: Path, oids: Sequence[str]) -> Dict[str, bytes]:
    """Contents of blobs through one `git cat-file --batch` process."""
    out = _run(repo, ["cat-file", "--batch"], ("\n".join(oids) + "\n").encode("ascii")) if oids else b""
    blobs: Dict[str, bytes] = {}
    pos = 0
    while pos < len(out):
        end = out.index(b"\n", pos)
        oid, _kind, size = out[pos:end].decode("ascii").split()
        pos = end + 1 + int(size) + 1
        blobs[oid] = out[end + 1:end + 1 + int(size)]
    return blobs


def rebuild(core_dir: Path, repo: Path) -> List[Dict[str, Any]]:
    """CO-01: history.json content, from git (one entry per distinct state)."""
    repo = Path(repo)
    commits = _run(repo, ["log", "--format=%H", "--reverse", "--", CORE_PREFIX.rstrip("/")]).decode().split()
    hashes: Dict[str, str] = {}
    entries: List[Dict[str, Any]] = []
    seen = set()
    for commit in commits:
        listing = _run(repo, ["ls-tree", "-r", commit, "--", CORE_PREFIX.rstrip("/")]).decode("utf-8")
        files: Dict[str, str] = {}
        oids: Dict[str, str] = {}
        for line in listing.splitlines():
            meta, path = line.split("\t", 1)
            oid = meta.split()[2]
            rel = path[len(CORE_PREFIX):]
            if tabled(rel):
                oids[rel] = oid
        fetched = _blobs(repo, sorted({o for o in oids.values() if o not in hashes}))
        for oid, data in fetched.items():
            hashes[oid] = sha(data)
        files = {rel: hashes[oid] for rel, oid in sorted(oids.items())}
        eid = entry_id(files)
        if eid in seen:
            continue
        seen.add(eid)
        init = oids.get("hcore/__init__.py")
        text = (fetched.get(init) if init in fetched else _blobs(repo, [init]).get(init, b"")) if init else b""
        entries.append({"id": eid, "commit": commit, "core_version": _core_version(text), "files": files})
    current = table(core_dir)
    if entry_id(current) not in seen:
        init = (Path(core_dir) / "hcore" / "__init__.py").read_bytes()
        entries.append({"id": entry_id(current), "commit": None, "core_version": _core_version(init),
                        "files": dict(sorted(current.items()))})
    return entries


def _core_version(data: bytes) -> Optional[str]:
    m = re.search(rb'^CORE_VERSION = "([^"]+)"', data, re.M)
    return m.group(1).decode() if m else None


def dumps(entries: List[Dict[str, Any]]) -> str:
    return json.dumps(entries, indent=2, ensure_ascii=False) + "\n"


# ---------------------------------------------------------- project copies

def installed_core(dest: Path, entries: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """CO-05 (and CO-02 when a MANIFEST exists)."""
    dest = Path(dest)
    manifest = version.read_manifest(dest)
    if manifest and manifest.get("core_version"):
        return {"core_version": manifest["core_version"], "exact": True, "mixed": False, "source": "manifest"}
    files = table(dest)
    if not files:
        return {"core_version": None, "exact": False, "mixed": False, "source": None}
    for entry in reversed(list(entries)):
        if entry.get("files") == files:
            return {"core_version": entry.get("core_version"), "exact": True, "mixed": False,
                    "source": "history", "entry": entry.get("id")}
    matched: Dict[str, List[Dict[str, Any]]] = {}
    for rel, h in files.items():
        matched[rel] = [e for e in entries if e.get("files", {}).get(rel) == h]
    if all(matched.values()):
        used: Dict[str, Dict[str, Any]] = {}
        for rel, found in matched.items():
            if not any(e["id"] in used for e in found):
                used[found[-1]["id"]] = found[-1]
        versions = sorted({e.get("core_version") or "" for e in used.values()}, key=version.semver)
        return {"core_version": versions[-1] or None, "exact": False, "mixed": True, "source": "history",
                "entries": sorted(used), "versions": versions}
    return {"core_version": None, "exact": False, "mixed": False, "source": "history"}


PRISTINE, CUSTOMIZED, MISSING, EXTRA = "pristine", "customized", "missing", "extra"


def copy_states(core_dir: Path, dest: Path, entries: Sequence[Dict[str, Any]]) -> Dict[str, str]:
    """CO-03: state of each file of a project copy against the core to install."""
    core_dir, dest = Path(core_dir), Path(dest)
    manifest = version.read_manifest(dest)
    known = (manifest or {}).get("files") if manifest else None
    states: Dict[str, str] = {}
    wanted = tree(core_dir)
    present = tree(dest)
    for rel in sorted(set(wanted) | set(present)):
        path = dest / rel
        if not path.is_file():
            states[rel] = MISSING
            continue
        if rel == HISTORY:
            states[rel] = PRISTINE          # data file of the core, never customized
            continue
        h = sha(path.read_bytes())
        if known is not None and rel in known:
            ok = known[rel] == h
        elif known is not None:
            ok = False
        else:
            ok = any(e.get("files", {}).get(rel) == h for e in entries)
        if ok:
            states[rel] = PRISTINE
        elif rel in wanted or (known is not None and rel in known) or \
                any(rel in e.get("files", {}) for e in entries):
            states[rel] = CUSTOMIZED
        else:
            states[rel] = EXTRA
    return states


def manifest_text(core_dir: Path, repo: Dict[str, Any], entries: Sequence[Dict[str, Any]]) -> str:
    """CO-02."""
    files = {rel: sha((Path(core_dir) / rel).read_bytes()) for rel in tree(core_dir)}
    current = entry_id(table(core_dir))
    entry = current if any(e.get("id") == current for e in entries) else None
    data = {"core_version": version.CORE_VERSION,
            "repo": {k: repo.get(k) for k in ("commit", "describe", "dirty")},
            "entry": entry, "files": files}
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def customized(states: Dict[str, str]) -> Tuple[List[str], List[str]]:
    return ([r for r, s in states.items() if s == CUSTOMIZED], [r for r, s in states.items() if s == EXTRA])
