# Spec files: machine header, open questions, statuses (BQ-01 to BQ-09).
#
# - BQ-01: header block right after the `Status:` line, contiguous optional
#   lines: `Blocking questions`, `Files scope`, `Depends on`, `Priority`.
# - BQ-02: under `## Open questions`, items `- Q<n> — <text>` with the
#   sub-lines `Default:` and `Answer:` (answered = non-empty `Answer:`).
# - Languages: every keyword has an English form (written by the core) and a
#   French form (still accepted: `Statut`, `Questions bloquantes`, `Périmètre
#   fichiers`, `Dépend de`, `Priorité`, `Défaut`, `Réponse`, `## Questions
#   ouvertes`, `aucune`, and the `status_legacy` words of the profiles). Matching
#   is case-insensitive and spacing tolerant. A spec keeps its own language: a
#   status transition or an applied default on a French spec is written in French.
# - BQ-03: `check` returns {status, blocking, unanswered, perimeter, depends_on,
#   priority, errors}; any error is a usage error (exit 2) at the CLI.
# - BQ-04: the status words come from the artefacts of the profiles
#   (`status_proposed`, `status_blocked`, `status_ready`, `status_delivered`):
#   nothing profile specific is written here. A status has a role (proposed,
#   blocked, ready, delivered) and a family (the artefact that declares it).
# - BQ-06: `apply_defaults` answers every non-blocking question with its default.
# - BQ-07: `entry_mode` maps a spec to build | approve | stop (blocked | delivered).
# - BQ-08: `set_status` changes the `Statut :` line only, along the admitted
#   transitions (proposed -> ready | blocked, blocked -> ready | proposed,
#   ready -> delivered); every other byte is kept.
# The field names and status words (both languages) are part of the spec format.

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import profiles

ROLE_KEYS = (("status_proposed", "proposed"), ("status_blocked", "blocked"), ("status_ready", "ready"),
             ("status_delivered", "delivered"), ("status_done", "delivered"))
ROLE_TRANSITIONS = {"proposed": ("ready", "blocked"), "blocked": ("ready", "proposed"), "ready": ("delivered",)}
EN, FR = "en", "fr"
# Header keys: canonical (English) name -> (English spelling, French spelling).
HEADER_KEYS = {"blocking": ("Blocking questions", "Questions bloquantes"),
               "scope": ("Files scope", "Périmètre fichiers"),
               "depends": ("Depends on", "Dépend de"),
               "priority": ("Priority", "Priorité")}


def _spelling(word: str) -> str:
    return re.escape(word).replace(r"\ ", r"[ \t]+")


_HEADER_ALIAS = {_w.lower(): _k for _k, _ws in HEADER_KEYS.items() for _w in _ws}
STATUS_LINE = re.compile(r"^(?P<lead>[ \t]*(?P<key>Status|Statut)[ \t]*:[ \t]*)(?P<value>[^\r\n]*?)(?P<eol>\r?\n|\Z)",
                         re.M | re.I)
STATUS_START = re.compile(r"^[ \t]*(?:Status|Statut)[ \t]*:", re.I)
HEADER_LINE = re.compile(r"^[ \t]*(?P<key>%s)[ \t]*:[ \t]*(?P<value>.*?)[ \t]*$"
                         % "|".join(_spelling(w) for ws in HEADER_KEYS.values() for w in ws), re.I)
QUESTION = re.compile(r"^[ \t]*[-*][ \t]+(?P<id>Q\d+)[ \t]*[—–-][ \t]*(?P<text>.*?)[ \t]*$")
SUBLINE = re.compile(r"^[ \t]+(?:[-*][ \t]+)?(?P<key>Default|Answer|Défaut|Réponse)[ \t]*:[ \t]*(?P<value>.*?)[ \t]*$",
                     re.I)
SECTION = re.compile(r"^##[ \t]+(?:Open[ \t]+questions|Questions[ \t]+ouvertes)[ \t]*$", re.I)
ABSOLUTE = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/])")
AUTO_NOTE = {EN: "(default applied automatically)", FR: "(défaut appliqué automatiquement)"}
ANSWER_KEY = {EN: "Answer", FR: "Réponse"}
NONE_WORDS = ("none", "aucune")

PROPOSED_MODE, APPROVE, BUILD = "proposed", "approve", "build"


class SpecError(ValueError):
    """Malformed spec or refused transition (exit 2)."""


def status_word(value: str) -> str:
    """`<WORD> — <verdict>` -> WORD; any other status is its first token."""
    return value.strip().split(" ")[0] if value.strip() else ""


def vocabulary(profiles_dir=None, name: Optional[str] = None) -> Dict[str, Tuple[str, str, str]]:
    """BQ-04: {word: (role, family, status line)} from the artefacts of the profiles. A word shared by two
    families (PROPOSED, BLOCKED) belongs to the family whose glob matches the file `name`, else to the first."""
    arts = [(prof, art) for prof in profiles.discover(profiles_dir).values() for art in prof.get("artefacts", [])
            if art.get("status_ready")]
    if name:
        arts.sort(key=lambda pa: 0 if Path(name).match(pa[1].get("glob") or "*") else 1)
    out: Dict[str, Tuple[str, str, str]] = {}
    for prof, art in arts:
        family = "%s/%s" % (prof["name"], art["kind"])
        for key, role in ROLE_KEYS:
            line = art.get(key)
            if line:
                out.setdefault(status_word(line.split(":", 1)[1]), (role, family, line))
        for role, line in (art.get("status_legacy") or {}).items():
            if line:
                out.setdefault(status_word(line.split(":", 1)[1]), (role, family, line))
    return out


def lang_of_line(line: str) -> str:
    """Language of a status line (`Statut : X` is French, `Status: X` English)."""
    return FR if line.strip().lower().startswith("statut") else EN


def role_of(word: str) -> Optional[str]:
    entry = vocabulary().get(word.upper())
    return entry[0] if entry else None


def _lines(text: str) -> List[str]:
    return text.replace("\r\n", "\n").split("\n")


def parse(text: str) -> Dict[str, Any]:
    """Structured reading of a spec; `errors` lists every BQ-03 defect."""
    errors: List[str] = []
    lines = _lines(text)
    index = next((i for i, ln in enumerate(lines) if STATUS_START.match(ln)), None)
    status: Optional[str] = None
    lang = EN
    header: Dict[str, str] = {}
    if index is None:
        errors.append("the `Status:` line is missing")
    else:
        status = lines[index].split(":", 1)[1].strip()
        lang = lang_of_line(lines[index])
        known = vocabulary()
        if status_word(status).upper() not in known:
            errors.append("status %r is not one of %s" % (status, ", ".join(sorted(known))))
        for ln in lines[index + 1:]:
            m = HEADER_LINE.match(ln)
            if not m:
                break
            header[_HEADER_ALIAS[re.sub(r"[ \t]+", " ", m.group("key")).lower()]] = m.group("value")
    blocking = _ids(header.get("blocking", ""))
    perimeter: List[str] = []
    if "scope" in header:
        for glob in header["scope"].split(","):
            glob = glob.strip()
            if not glob:
                errors.append("`Files scope` holds an empty glob")
            elif ABSOLUTE.match(glob):
                errors.append("`Files scope`: absolute glob %r (globs are relative to the root)" % glob)
            else:
                perimeter.append(glob)
    depends = [d.strip() for d in header.get("depends", "").split(",") if d.strip()]
    priority: Optional[int] = None
    if "priority" in header:
        try:
            priority = int(header["priority"])
            if priority < 1:
                raise ValueError
        except ValueError:
            errors.append("`Priority` must be an integer >= 1, got %r" % header["priority"])
            priority = None
    questions = parse_questions(lines)
    known = {q["id"] for q in questions}
    for qid in blocking:
        if qid not in known:
            errors.append("blocking question %s is absent from `## Open questions`" % qid)
    for q in questions:
        q["blocking"] = q["id"] in blocking
    unanswered = [q["id"] for q in questions if q["blocking"] and not q["answer"]]
    word = status_word(status or "")
    return {"status": status, "word": word.upper(), "role": role_of(word), "blocking": blocking,
            "unanswered": unanswered, "perimeter": perimeter, "depends_on": depends, "priority": priority,
            "questions": questions, "errors": errors, "status_index": index, "lang": lang}


def _ids(value: str) -> List[str]:
    if not value.strip() or value.strip().lower() in NONE_WORDS:
        return []
    return [p.strip() for p in value.split(",") if p.strip()]


def parse_questions(lines: List[str]) -> List[Dict[str, Any]]:
    start = next((i for i, ln in enumerate(lines) if SECTION.match(ln)), None)
    if start is None:
        return []
    out: List[Dict[str, Any]] = []
    for i in range(start + 1, len(lines)):
        ln = lines[i]
        if ln.startswith("## "):
            break
        m = QUESTION.match(ln)
        if m:
            out.append({"id": m.group("id"), "text": m.group("text"), "default": "", "answer": "",
                        "line": i, "default_line": None, "answer_line": None})
            continue
        s = SUBLINE.match(ln)
        if s and out:
            key = "default" if s.group("key").lower() in ("default", "défaut") else "answer"
            out[-1][key] = s.group("value")
            out[-1][key + "_line"] = i
    return out


def check(path: Path) -> Dict[str, Any]:
    """BQ-03 on a file."""
    parsed = parse(Path(path).read_text(encoding="utf-8-sig", errors="replace"))
    return {k: parsed[k] for k in ("status", "blocking", "unanswered", "perimeter", "depends_on", "priority",
                                   "errors")}


def entry_mode(parsed: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    """BQ-07: (mode, stop) for a spec under specs/; stop is `blocked` or `delivered` for the exit 1 cases."""
    if parsed["errors"]:
        raise SpecError("; ".join(parsed["errors"]))
    role = parsed["role"]
    if role == "ready":
        return BUILD, None
    if role == "proposed":
        return APPROVE, None
    if role == "blocked":
        return (APPROVE, None) if not parsed["unanswered"] else ("stop", "blocked")
    return "stop", "delivered"


def set_status(path: Path, new: str) -> Dict[str, str]:
    """BQ-08: rewrites the `Status:` line only (bytes elsewhere untouched). The word written is in the
    language of the spec, whatever the language of `new`."""
    path = Path(path)
    raw = path.read_bytes().decode("utf-8")
    m = STATUS_LINE.search(raw)
    if not m:
        raise SpecError("the `Status:` line is missing")
    old = status_word(m.group("value")).upper()
    target = status_word(new).upper()
    known = vocabulary(name=path.name)
    if old not in known or target not in known:
        raise SpecError("unknown status: %s -> %s" % (old, target))
    allowed = [w for w, (role, family, _line) in known.items()
               if family == known[old][1] and role in ROLE_TRANSITIONS.get(known[old][0], ())]
    if target not in allowed:
        raise SpecError("transition %s -> %s is not admitted (admitted from %s: %s)"
                        % (old, target, old, ", ".join(sorted(allowed)) or "none"))
    lang = FR if m.group("key").lower() == "statut" else EN
    written = _word_in(known, known[target][0], known[target][1], lang) or target
    value = new.strip()
    value = written + value[len(status_word(value)):]
    text = raw[:m.start("value")] + value + raw[m.end("value"):]
    path.write_bytes(text.encode("utf-8"))
    return {"from": old, "to": value}


def _word_in(known: Dict[str, Tuple[str, str, str]], role: str, family: str, lang: str) -> Optional[str]:
    """The status word of `role` in `family` written in `lang` (None when the family has none)."""
    for word, (r, f, line) in known.items():
        if r == role and f == family and lang_of_line(line) == lang:
            return word
    return None


def apply_defaults(path: Path) -> Dict[str, Any]:
    """BQ-06: every non-blocking question with a default and no answer takes
    `Answer: <default> (default applied automatically)` (`Réponse : ...` on a
    French spec). Returns the applied list and the non-blocking questions that
    have no default."""
    path = Path(path)
    raw = path.read_bytes().decode("utf-8")
    eol = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.split(eol)
    parsed = parse(raw)
    applied: List[Dict[str, str]] = []
    missing: List[str] = []
    edits: List[Tuple[int, bool, str]] = []
    for q in parsed["questions"]:
        if q["blocking"] or q["answer"]:
            continue
        if not q["default"]:
            missing.append(q["id"])
            continue
        indent = re.match(r"[ \t]*", lines[q["default_line"]]).group(0)
        lang = parsed["lang"]
        note = "%s%s%s %s %s" % (indent, ANSWER_KEY[lang], " :" if lang == FR else ":", q["default"],
                                 AUTO_NOTE[lang])
        if q["answer_line"] is not None:
            edits.append((q["answer_line"], True, note))
        else:
            edits.append((q["default_line"], False, note))
        applied.append({"id": q["id"], "default": q["default"]})
    for line_no, replace, note in sorted(edits, key=lambda e: e[0], reverse=True):
        if replace:
            lines[line_no] = note
        else:
            lines.insert(line_no + 1, note)
    if edits:
        path.write_bytes(eol.join(lines).encode("utf-8"))
    return {"applied": applied, "missing_default": missing}


def word_for(path: Path, role: str) -> str:
    """The status word of `role` in the family and the language of the spec."""
    parsed = parse(Path(path).read_text(encoding="utf-8-sig", errors="replace"))
    known = vocabulary(name=Path(path).name)
    if parsed["word"] not in known:
        raise SpecError("unknown status: %s" % parsed["word"])
    family = known[parsed["word"]][1]
    word = _word_in(known, role, family, parsed["lang"]) or _word_in(known, role, family, EN) \
        or _word_in(known, role, family, FR)
    if word is None:
        raise SpecError("no %s status for this kind of spec" % role)
    return word


def set_role(path: Path, role: str) -> Dict[str, str]:
    """BQ-08 by role (proposed, blocked, ready, delivered)."""
    return set_status(path, word_for(path, role))
