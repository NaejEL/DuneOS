# Profile scout: deterministic signals, verification of the agent's proofs,
# verdict per profile.
#
# - SC-01: `detection` of a profile = {accept, reject, criteria[]}; criterion
#   kinds: glob, dir, dependency, content (200 KB read per file at most),
#   judgement (evaluated by the agent).
# - SC-02: `signals` evaluates every non-judgement criterion of every profile
#   and returns the proofs {file, line, excerpt}; artifact folders are excluded
#   (ST-03), and so are test-data folders (SC-09): a fixture describes another
#   project, never the scanned one.
# - SC-03: the agent only receives the judgement criteria (`judgement_criteria`).
# - SC-04: role harness.scout, default model haiku (models.py).
# - SC-05: `verdict`: a judgement proof must name an existing file, lines inside
#   it and an excerpt present at those lines (whitespace normalized); an
#   unverifiable proof is discarded and listed. score = sum of the weights
#   found; retained if score >= accept, rejected if score < reject, otherwise
#   uncertain; clear = no uncertain profile.
# - SC-06: dead agent or unusable output twice -> the verdict is computed on the
#   deterministic signals alone, clear false, uncertainty "scout non recueilli".
# - SC-07: nothing retained, nothing uncertain -> proposal = [fallback], by
#   fallback, clear true.

from __future__ import annotations

import fnmatch
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import profiles, stack

READ_LIMIT = 200 * 1024
NOT_COLLECTED = "scout non recueilli"
DEPENDENCY_FILES = ("requirements.txt", "pyproject.toml", "environment.yml", "package.json")
# SC-09: folders of test data, at any depth; their manifests and journals are samples.
TEST_DATA_DIRS = ("fixtures", "__fixtures__", "testdata")


def _skipped(rel: str) -> bool:
    """rel: posix path of a folder relative to the root."""
    return stack._is_artifact(rel) or rel.split("/")[-1] in TEST_DATA_DIRS


def _files(root: Path, max_depth: int) -> List[str]:
    """Posix paths of the files outside artifact folders, down to max_depth components."""
    out: List[str] = []
    root = Path(root)
    for current, dirs, files in os.walk(str(root)):
        rel = Path(current).relative_to(root).as_posix()
        prefix = "" if rel == "." else rel + "/"
        dirs[:] = sorted(d for d in dirs if not _skipped(prefix + d))
        depth = 0 if rel == "." else rel.count("/") + 1
        if depth >= max_depth:
            dirs[:] = []
        for name in sorted(files):
            out.append(prefix + name)
    return out


def _dirs(root: Path) -> List[str]:
    out: List[str] = []
    for current, dirs, _files_ in os.walk(str(root)):
        rel = Path(current).relative_to(root).as_posix()
        prefix = "" if rel == "." else rel + "/"
        dirs[:] = sorted(d for d in dirs if not _skipped(prefix + d))
        out += [prefix + d for d in dirs]
        if rel != ".":
            dirs[:] = []
    return out


def _match(path: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(path, pattern) or fnmatch.fnmatchcase(path, "*/" + pattern)


def _read(root: Path, rel: str) -> List[str]:
    try:
        with open(str(Path(root) / rel), "rb") as fh:
            raw = fh.read(READ_LIMIT)
    except OSError:
        return []
    return raw.decode("utf-8", "replace").splitlines()


def _norm(text: str) -> str:
    return " ".join(text.split())


def _dependencies(root: Path, rel: str) -> List[Tuple[str, int, str]]:
    """(normalized name, line, excerpt) of the dependencies declared in a manifest."""
    lines = _read(root, rel)
    base = rel.rsplit("/", 1)[-1]
    found: List[Tuple[str, int, str]] = []

    def norm(name: str) -> str:
        return re.sub(r"[-_.]+", "-", name.strip().lower())

    if base == "requirements.txt":
        for i, line in enumerate(lines, 1):
            m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", line)
            if m and not line.lstrip().startswith(("#", "-")):
                found.append((norm(m.group(1)), i, line.strip()))
    elif base == "environment.yml":
        for i, line in enumerate(lines, 1):
            m = re.match(r"\s*-\s*([A-Za-z0-9][A-Za-z0-9._-]*)", line)
            if m:
                found.append((norm(m.group(1)), i, line.strip()))
    elif base == "package.json":
        try:
            data = json.loads("\n".join(lines))
        except ValueError:
            data = {}
        keys = list((data.get("dependencies") or {})) + list((data.get("devDependencies") or {}))
        for key in keys:
            for i, line in enumerate(lines, 1):
                if '"%s"' % key in line:
                    found.append((norm(key), i, line.strip()))
                    break
    elif base == "pyproject.toml":
        in_project = in_deps = False
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("["):
                in_project = stripped == "[project]"
                in_deps = False
                continue
            if in_project and re.match(r"dependencies\s*=", stripped):
                in_deps = True
            if in_deps:
                for m in re.finditer(r"[\"']([A-Za-z0-9][A-Za-z0-9._-]*)", stripped):
                    found.append((norm(m.group(1)), i, stripped))
                if "]" in stripped.split("=", 1)[-1]:
                    in_deps = False
    return found


def _evaluate(root: Path, crit: Dict[str, Any], files: List[str], dirs: List[str]) -> List[Dict[str, Any]]:
    kind = crit["kind"]
    proofs: List[Dict[str, Any]] = []
    if kind == "glob":
        depth = crit["pattern"].count("/") + 2
        for rel in files:
            if rel.count("/") + 1 <= depth + 1 and _match(rel, crit["pattern"]):
                proofs.append({"file": rel, "line": None, "excerpt": None})
                break
    elif kind == "dir":
        for rel in dirs:
            if rel == crit["path"] or rel.endswith("/" + crit["path"]) and rel.count("/") <= 1:
                proofs.append({"file": rel + "/", "line": None, "excerpt": None})
                break
    elif kind == "dependency":
        wanted = {re.sub(r"[-_.]+", "-", n.lower()) for n in crit["names"]}
        for rel in files:
            if rel.rsplit("/", 1)[-1] in DEPENDENCY_FILES and rel.count("/") <= 1:
                for name, line, excerpt in _dependencies(root, rel):
                    if name in wanted:
                        proofs.append({"file": rel, "line": line, "excerpt": excerpt})
                        break
            if proofs:
                break
    elif kind == "content":
        regex = re.compile(crit["regex"], re.M)
        for rel in files:
            if not _match(rel, crit["glob"]):
                continue
            for i, line in enumerate(_read(root, rel), 1):
                if regex.search(line):
                    proofs.append({"file": rel, "line": i, "excerpt": line.strip()})
                    break
            if proofs:
                break
    return proofs


def signals(root: Path, profiles_dir: Optional[Path] = None) -> Dict[str, Any]:
    """SC-02."""
    root = Path(root)
    files = _files(root, 4)
    dirs = _dirs(root)
    result: Dict[str, Any] = {}
    for name, prof in profiles.discover(profiles_dir).items():
        found = []
        for crit in prof["detection"]["criteria"]:
            if crit["kind"] == "judgement":
                continue
            proofs = _evaluate(root, crit, files, dirs)
            found.append({"id": crit["id"], "kind": crit["kind"], "weight": crit["weight"],
                          "found": bool(proofs), "proofs": proofs})
        result[name] = {"criteria": found, "accept": prof["detection"]["accept"],
                        "reject": prof["detection"]["reject"]}
    return {"profiles": result}


def judgement_criteria(profiles_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """SC-03: what the scout agent receives."""
    out = []
    for name, prof in profiles.discover(profiles_dir).items():
        for crit in prof["detection"]["criteria"]:
            if crit["kind"] == "judgement":
                out.append({"profile": name, "criterion": crit["id"], "question": crit["question"],
                            "weight": crit["weight"]})
    return out


def proof_error(root: Path, signal: Dict[str, Any]) -> Optional[str]:
    """SC-05: why a judgement proof cannot be verified, or None."""
    ev = signal.get("evidence")
    if not isinstance(ev, dict) or not isinstance(ev.get("file"), str):
        return "no evidence file"
    path = Path(root) / ev["file"]
    try:
        path.resolve().relative_to(Path(root).resolve())
    except ValueError:
        return "evidence file outside the project"
    if not path.is_file():
        return "file not found: %s" % ev["file"]
    lines = _read(root, ev["file"])
    span = ev.get("lines")
    if not (isinstance(span, dict) and isinstance(span.get("start"), int) and isinstance(span.get("end"), int)):
        return "no line range"
    start, end = span["start"], span["end"]
    if start < 1 or end < start or end > len(lines):
        return "lines %s-%s are outside the file (%d lines)" % (start, end, len(lines))
    excerpt = _norm(str(ev.get("excerpt") or ""))
    if not excerpt or excerpt not in _norm(" ".join(lines[start - 1:end])):
        return "excerpt not present at lines %d-%d" % (start, end)
    return None


def verdict(root: Path, agent_output: Optional[Dict[str, Any]] = None, scout_failed: bool = False,
            profiles_dir: Optional[Path] = None) -> Dict[str, Any]:
    """SC-05 to SC-07."""
    root = Path(root)
    base = signals(root, profiles_dir)["profiles"]
    known = profiles.discover(profiles_dir)
    judgement = {(j["profile"], j["criterion"]): j for j in judgement_criteria(profiles_dir)}
    accepted: Dict[str, List[Dict[str, Any]]] = {}
    rejected: List[Dict[str, Any]] = []
    uncertainties: List[str] = []
    seen = set()
    if scout_failed:
        uncertainties.append(NOT_COLLECTED)
    elif agent_output is not None:
        uncertainties += [str(u) for u in agent_output.get("uncertainties", [])]
        for sig in agent_output.get("signals", []):
            key = (sig.get("profile"), sig.get("criterion"))
            if key not in judgement:
                rejected.append(dict(sig, reason="not a judgement criterion"))
            elif sig.get("found") is not True:
                continue
            else:
                why = proof_error(root, sig)
                if why:
                    rejected.append(dict(sig, reason=why))
                elif key not in seen:
                    seen.add(key)
                    accepted.setdefault(key[0], []).append(sig)
    rows: List[Dict[str, Any]] = []
    for name in known:
        info = base[name]
        found = [c["id"] for c in info["criteria"] if c["found"]]
        score = sum(c["weight"] for c in info["criteria"] if c["found"])
        for sig in accepted.get(name, []):
            found.append(sig["criterion"])
            score += judgement[(name, sig["criterion"])]["weight"]
        status = "retained" if score >= info["accept"] else "rejected" if score < info["reject"] else "uncertain"
        rows.append({"name": name, "score": score, "status": status, "found": found,
                     "accept": info["accept"], "reject": info["reject"]})
    retained = [r["name"] for r in rows if r["status"] == "retained"]
    uncertain = [r["name"] for r in rows if r["status"] == "uncertain"]
    proposal, by = retained, "scout"
    if not retained and not uncertain:
        fallback = profiles.fallback_name(profiles_dir)
        proposal, by = ([fallback] if fallback else []), "fallback"
    return {"profiles": rows, "proposal": proposal, "by": by, "clear": not uncertain and not scout_failed,
            "uncertainties": uncertainties, "rejected_signals": rejected}
