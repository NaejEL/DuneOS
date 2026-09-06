#!/usr/bin/env python3
# rgkb - base de connaissance locale adossee a ripgrep.
#
# Indexe le depot avec ripgrep, ecrit des artefacts versionnables sous
# .knowledge/ (texte = verite, sqlite = cache derive) et les expose aux agents
# Claude Code par un serveur MCP stdio et par une CLI equivalente.
#
# Aucune dependance : bibliotheque standard Python uniquement. Seul ripgrep
# (rg) doit etre installe et accessible.

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

TOOL_VERSION = "1.0.0"
SCHEMA = 1
KB_DIRNAME = ".knowledge"
MCP_PROTOCOL = "2025-06-18"


# --------------------------------------------------------------------------
# Racine du projet et configuration
# --------------------------------------------------------------------------

def find_root() -> Path:
    env = os.environ.get("RGKB_ROOT")
    if env:
        return Path(env).resolve()
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".git").exists() or (parent / KB_DIRNAME).is_dir() or (parent / ".claude").is_dir():
            return parent
    return Path.cwd().resolve()


ROOT = find_root()
KB = ROOT / KB_DIRNAME

DEFAULT_CONFIG = {
    "schema": SCHEMA,
    "globs": [],
    "exclude": [
        "!.knowledge/**",
        "!.git/**",
        "!**/node_modules/**",
        "!**/.venv/**",
        "!**/venv/**",
        "!**/__pycache__/**",
        "!**/bin/**",
        "!**/obj/**",
        "!**/dist/**",
        "!**/build/**",
        "!**/target/**",
        "!**/*.min.js",
        "!**/*.lock",
    ],
    "include_hidden": False,
    "max_file_bytes": 1000000,
    "max_names": 5000,
    "max_edges": 20000,
    "min_name_len": 4,
    "rg_path": None,
    "languages": None,
    # Intention de hooks git, versionnee : elle voyage avec le depot pour que
    # chaque utilisateur qui clone les obtienne sans rien avoir a lancer.
    # "off" | "auto" (post-merge + post-rewrite) | "commit" (+ pre-commit)
    "hooks": "off",
}


def load_config() -> dict:
    # config.json est versionne (perimetre partage par l'equipe) ;
    # config.local.json ne l'est pas (reglages propres a une machine, ex. rg_path).
    cfg = dict(DEFAULT_CONFIG)
    for name in ("config.json", "config.local.json"):
        path = KB / name
        if not path.is_file():
            continue
        try:
            user = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SystemExit("rgkb: %s illisible : %s" % (path, exc))
        if not isinstance(user, dict):
            raise SystemExit("rgkb: %s doit contenir un objet JSON" % path)
        cfg.update(user)
    return cfg


# --------------------------------------------------------------------------
# Langages et motifs de definition
# --------------------------------------------------------------------------

EXT_LANG = {
    "py": "python",
    "js": "ts", "jsx": "ts", "mjs": "ts", "cjs": "ts", "ts": "ts", "tsx": "ts",
    "cs": "csharp",
    "go": "go",
    "rs": "rust",
    "java": "java", "kt": "java",
    "c": "cpp", "h": "cpp", "cc": "cpp", "cpp": "cpp", "cxx": "cpp", "hpp": "cpp", "hh": "cpp",
    "rb": "ruby",
    "php": "php",
    "sh": "shell", "bash": "shell", "zsh": "shell",
    "ps1": "powershell", "psm1": "powershell",
    "sql": "sql",
    "tf": "terraform", "tfvars": "terraform",
    "yml": "yaml", "yaml": "yaml",
    "md": "markdown", "markdown": "markdown",
}

PATTERNS = {
    "python": [
        ("class", r"^\s*class\s+(?P<name>[A-Za-z_]\w*)"),
        ("function", r"^\s*(?:async\s+)?def\s+(?P<name>[A-Za-z_]\w*)"),
        ("constant", r"^(?P<name>[A-Z][A-Z0-9_]{2,})\s*[:=]"),
    ],
    "ts": [
        ("class", r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(?P<name>[A-Za-z_$][\w$]*)"),
        ("interface", r"^\s*(?:export\s+)?interface\s+(?P<name>[A-Za-z_$][\w$]*)"),
        ("type", r"^\s*(?:export\s+)?type\s+(?P<name>[A-Za-z_$][\w$]*)"),
        ("enum", r"^\s*(?:export\s+)?(?:const\s+)?enum\s+(?P<name>[A-Za-z_$][\w$]*)"),
        ("function", r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s+(?P<name>[A-Za-z_$][\w$]*)"),
        ("const", r"^\s*(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*[:=]"),
    ],
    "csharp": [
        ("type", r"^\s*(?:[A-Za-z]+\s+)*(?:class|struct|interface|record|enum)\s+(?P<name>[A-Za-z_]\w*)"),
        ("method", r"^\s*(?:public|private|protected|internal)\s+(?:[A-Za-z_][\w<>,\[\]\.\?]*\s+)+(?P<name>[A-Za-z_]\w*)\s*\("),
    ],
    "go": [
        ("method", r"^func\s*\([^)]*\)\s*(?P<name>[A-Za-z_]\w*)\s*\("),
        ("function", r"^func\s+(?P<name>[A-Za-z_]\w*)\s*\("),
        ("type", r"^type\s+(?P<name>[A-Za-z_]\w*)\s"),
    ],
    "rust": [
        ("function", r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+(?P<name>[A-Za-z_]\w*)"),
        ("type", r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait|type|union)\s+(?P<name>[A-Za-z_]\w*)"),
        ("impl", r"^\s*impl(?:<[^>]*>)?\s+(?P<name>[A-Za-z_]\w*)"),
    ],
    "java": [
        ("type", r"^\s*(?:[A-Za-z@]+\s+)*(?:class|interface|enum|record)\s+(?P<name>[A-Za-z_]\w*)"),
        ("method", r"^\s*(?:public|private|protected)\s+(?:static\s+)?(?:[A-Za-z_][\w<>,\[\]\.]*\s+)(?P<name>[A-Za-z_]\w*)\s*\("),
    ],
    "cpp": [
        ("macro", r"^\s*#define\s+(?P<name>[A-Za-z_]\w*)"),
        ("type", r"^\s*(?:typedef\s+)?(?:struct|class|union|enum|namespace)\s+(?P<name>[A-Za-z_]\w*)"),
        ("function", r"^[A-Za-z_][\w\s\*&:<>,]*?\b(?P<name>[A-Za-z_]\w*)\s*\([^;]*\)\s*\{?\s*$"),
    ],
    "ruby": [
        ("class", r"^\s*(?:class|module)\s+(?P<name>[A-Za-z_][\w:]*)"),
        ("method", r"^\s*def\s+(?P<name>[A-Za-z_][\w\?!\.]*)"),
    ],
    "php": [
        ("class", r"^\s*(?:abstract\s+|final\s+)?(?:class|interface|trait)\s+(?P<name>[A-Za-z_]\w*)"),
        ("function", r"^\s*(?:public\s+|private\s+|protected\s+|static\s+)*function\s+(?P<name>[A-Za-z_]\w*)"),
    ],
    "shell": [
        ("function", r"^\s*(?:function\s+)?(?P<name>[A-Za-z_][\w-]*)\s*\(\s*\)"),
    ],
    "powershell": [
        ("function", r"^\s*function\s+(?P<name>[A-Za-z][\w-]*)"),
    ],
    "sql": [
        ("object", r"(?i)^\s*create\s+(?:or\s+replace\s+)?(?:table|view|function|procedure|index)\s+(?:if\s+not\s+exists\s+)?(?P<name>[\w\.]+)"),
    ],
    "terraform": [
        ("block", r"^\s*(?:resource|module|variable|output|data|provider)\s+\"(?P<name>[^\"]+)\""),
    ],
    "yaml": [
        ("task", r"^\s*-\s+name:\s+(?P<name>\S.{2,79}?)\s*$"),
        ("play", r"^-\s+hosts:\s*(?P<name>\S+)"),
    ],
    "markdown": [
        ("heading", r"^#{1,3}\s+(?P<name>\S.{2,79}?)\s*$"),
    ],
}

STOPWORDS = {
    "true", "false", "null", "none", "self", "this", "class", "type", "return", "import",
    "export", "const", "function", "public", "private", "static", "value", "result", "data",
    "test", "tests", "main", "init", "name", "string", "object", "array", "index", "config",
    "error", "params", "args", "kwargs", "list", "dict", "print", "async", "await", "void",
}

LANG_EXTS = {}
for _ext, _lang in EXT_LANG.items():
    LANG_EXTS.setdefault(_lang, []).append(_ext)
for _lang in LANG_EXTS:
    LANG_EXTS[_lang].sort()


# --------------------------------------------------------------------------
# Invocation de ripgrep
# --------------------------------------------------------------------------

class RgMissing(RuntimeError):
    pass


def rg_bin(cfg: dict) -> str:
    for candidate in (cfg.get("rg_path"), os.environ.get("RGKB_RG")):
        if candidate:
            resolved = shutil.which(candidate) or (candidate if Path(candidate).is_file() else None)
            if resolved:
                return resolved
            raise RgMissing("ripgrep introuvable au chemin configure : %s" % candidate)
    found = shutil.which("rg")
    if not found:
        raise RgMissing(
            "ripgrep (rg) est introuvable dans le PATH. Installer ripgrep "
            "(winget install BurntSushi.ripgrep.MSVC | scoop install ripgrep | "
            "apt install ripgrep | brew install ripgrep), ou renseigner "
            '"rg_path" dans .knowledge/config.local.json (non versionne), '
            "ou definir RGKB_RG."
        )
    return found


def glob_args(cfg: dict, extra=None) -> list:
    args = []
    for pattern in list(cfg.get("globs") or []) + list(extra or []):
        args += ["-g", pattern]
    for pattern in cfg.get("exclude") or []:
        args += ["-g", pattern]
    if cfg.get("include_hidden"):
        args.append("--hidden")
    return args


def run_rg(cfg: dict, args: list) -> str:
    cmd = [rg_bin(cfg)] + args
    proc = subprocess.run(
        cmd, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode not in (0, 1):
        raise RuntimeError("rg a echoue (code %s) : %s" % (proc.returncode, proc.stderr.strip()))
    return proc.stdout


def rg_pattern(regex: str) -> str:
    # rg fusionne les motifs -e en une alternance : deux groupes nommes identiques
    # y seraient un doublon interdit. Le nom ne sert qu'au re-matching Python.
    return regex.replace("(?P<name>", "(")


def norm_path(raw: str) -> str:
    text = raw.replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


def iter_rg_json(output: str):
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        yield event


def event_path(event: dict):
    data = event.get("data") or {}
    path = (data.get("path") or {}).get("text")
    if not path:
        return None
    return norm_path(path)


# --------------------------------------------------------------------------
# Indexation
# --------------------------------------------------------------------------

def list_files(cfg: dict) -> list:
    out = run_rg(cfg, ["--files", "--no-messages"] + glob_args(cfg) + ["."])
    return sorted({norm_path(line) for line in out.splitlines() if line.strip()})


def hash_file(path: Path):
    digest = hashlib.sha256()
    lines = 0
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                break
            digest.update(chunk)
            lines += chunk.count(b"\n")
            size += len(chunk)
    return digest.hexdigest(), lines, size


def scan_files(cfg: dict, files: list) -> list:
    max_bytes = int(cfg.get("max_file_bytes") or 0)
    records = []
    for rel in files:
        abs_path = ROOT / rel
        try:
            if not abs_path.is_file():
                continue
            if max_bytes and abs_path.stat().st_size > max_bytes:
                continue
            sha, lines, size = hash_file(abs_path)
        except OSError:
            continue
        ext = rel.rsplit(".", 1)[-1].lower() if "." in rel else ""
        records.append({
            "path": rel,
            "lang": EXT_LANG.get(ext, "other"),
            "sha256": sha,
            "lines": lines,
            "size": size,
        })
    records.sort(key=lambda r: r["path"])
    return records


def extract_symbols(cfg: dict, file_records: list) -> list:
    known = {rec["path"] for rec in file_records}
    langs_present = sorted({rec["lang"] for rec in file_records if rec["lang"] in PATTERNS})
    wanted = cfg.get("languages")
    if wanted:
        langs_present = [lang for lang in langs_present if lang in wanted]

    symbols = []
    for lang in langs_present:
        patterns = PATTERNS[lang]
        compiled = [(kind, re.compile(rx)) for kind, rx in patterns]
        args = ["--json", "--no-messages"]
        for _kind, rx in patterns:
            args += ["-e", rg_pattern(rx)]
        args += glob_args(cfg, ["*.%s" % ext for ext in LANG_EXTS[lang]])
        args.append(".")
        for event in iter_rg_json(run_rg(cfg, args)):
            if event.get("type") != "match":
                continue
            rel = event_path(event)
            if rel is None or rel not in known:
                continue
            data = event["data"]
            text = (data.get("lines") or {}).get("text") or ""
            line_no = int(data.get("line_number") or 0)
            for kind, rx in compiled:
                found = rx.search(text)
                if not found:
                    continue
                name = (found.group("name") or "").strip()
                if name:
                    symbols.append({
                        "name": name,
                        "kind": kind,
                        "lang": lang,
                        "path": rel,
                        "line": line_no,
                        "signature": text.strip()[:200],
                    })
                break
    symbols.sort(key=lambda s: (s["path"], s["line"], s["kind"], s["name"]))
    seen = {}
    for sym in symbols:
        base = "%s::%s::%s" % (sym["path"], sym["kind"], sym["name"])
        count = seen.get(base, 0)
        seen[base] = count + 1
        sym["id"] = base if count == 0 else "%s#%d" % (base, count)
    return symbols


def edge_names(cfg: dict, symbols: list) -> list:
    min_len = int(cfg.get("min_name_len") or 4)
    candidates = set()
    for sym in symbols:
        name = sym["name"]
        if len(name) < min_len:
            continue
        if not re.fullmatch(r"[A-Za-z_]\w*", name):
            continue
        if name.lower() in STOPWORDS:
            continue
        candidates.add(name)
    ordered = sorted(candidates, key=lambda n: (-len(n), n))
    return sorted(ordered[: int(cfg.get("max_names") or 5000)])


def build_edges(cfg: dict, file_records: list, names: list) -> list:
    if not names:
        return []
    known = {rec["path"] for rec in file_records}
    handle = tempfile.NamedTemporaryFile("w", suffix=".rgkb", delete=False, encoding="utf-8")
    try:
        handle.write("\n".join(names) + "\n")
        handle.close()
        args = ["--json", "--no-messages", "-w", "-F", "-f", handle.name]
        args += glob_args(cfg)
        args.append(".")
        output = run_rg(cfg, args)
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass

    counts = {}
    for event in iter_rg_json(output):
        if event.get("type") != "match":
            continue
        rel = event_path(event)
        if rel is None or rel not in known:
            continue
        for sub in event["data"].get("submatches") or []:
            token = (sub.get("match") or {}).get("text")
            if not token:
                continue
            key = (rel, token)
            counts[key] = counts.get(key, 0) + 1

    edges = [{"src": src, "dst": dst, "count": count} for (src, dst), count in counts.items()]
    edges.sort(key=lambda e: (-e["count"], e["src"], e["dst"]))
    edges = edges[: int(cfg.get("max_edges") or 20000)]
    edges.sort(key=lambda e: (e["src"], e["dst"]))
    return edges


# --------------------------------------------------------------------------
# Ecriture des artefacts
# --------------------------------------------------------------------------

def write_if_changed(path: Path, content: str) -> bool:
    payload = content.encode("utf-8")
    if path.is_file() and path.read_bytes() == payload:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return True


def jsonl(records: list) -> str:
    return "".join(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n" for rec in records)


def sha_text(*parts) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
    return digest.hexdigest()


def read_jsonl(path: Path) -> list:
    if not path.is_file():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def load_manifest() -> dict:
    path = KB / "manifest.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


KB_README = """# Base de connaissance rgkb

Ce dossier est **versionne**. Il porte l'index de code et les notes partagees
entre les utilisateurs du depot.

| Fichier | Role | Verite |
|---|---|---|
| `config.json` | perimetre de l'index, intention de hooks git | source |
| `config.local.json` | surcharges propres a une machine, **non versionne** | source |
| `notes.jsonl` | notes de connaissance ecrites par les humains et les agents | source |
| `files.jsonl` | fichiers indexes (chemin, langage, sha256, lignes) | derive |
| `symbols.jsonl` | definitions trouvees par ripgrep | derive |
| `graph.json` | aretes fichier vers symbole mentionne | derive |
| `manifest.json` | comptes et empreinte de l'index | derive |
| `index.sqlite` | cache de requete reconstruit depuis les fichiers texte | derive |

Les fichiers derives sont commites pour qu'un `git clone` donne une base
immediatement interrogeable, sans reindexation.

## Apres un clone ou un pull

Rien a faire. Pour verifier la fraicheur :

    python .claude/rgkb/rgkb.py status

Si l'index est perime (des fichiers ont change depuis la derniere indexation) :

    python .claude/rgkb/rgkb.py index

## En cas de conflit git

- `notes.jsonl`, `files.jsonl`, `symbols.jsonl` : conflit sur du texte, une
  ligne par element. Garder les deux cotes, puis relancer `index`.
- `graph.json`, `manifest.json`, `index.sqlite` : ne pas fusionner a la main.
  Prendre n'importe quel cote (`git checkout --ours .knowledge/`) puis relancer
  `python .claude/rgkb/rgkb.py index --force`, qui les regenere depuis les
  sources.

`index.sqlite` est declare binaire dans `.knowledge/.gitattributes` : git ne
tente pas de le fusionner ligne a ligne.

## Hooks git

`config.json` porte la cle `hooks` : `off` (defaut), `auto` (post-merge et
post-rewrite) ou `commit` (plus pre-commit, qui reindexe et met .knowledge/ a
l'index avant chaque commit). Comme elle est versionnee, elle voyage avec le
depot : le premier `status` ou `index` d'un utilisateur qui vient de cloner
installe les hooks chez lui, sans qu'il ait rien a lancer.

    python .claude/rgkb/rgkb.py hooks install --with-commit   # active pour tous
    python .claude/rgkb/rgkb.py hooks status
    python .claude/rgkb/rgkb.py hooks remove                  # desactive pour tous

`RGKB_NO_HOOK=1 git pull` neutralise les hooks ponctuellement.

## Reglages propres a une machine

`config.local.json` (ignore par git, memes cles que `config.json`) est fusionne
par-dessus `config.json`. C'est la seule facon correcte de renseigner un chemin
absolu, par exemple si ripgrep n'est pas dans le PATH :

    {"rg_path": "C:/Users/moi/scoop/shims/rg.exe"}

Ne jamais mettre un chemin absolu dans `config.json` : il casserait la base pour
les autres utilisateurs du depot.
"""

KB_GITATTRIBUTES = """index.sqlite binary -diff -merge
*.jsonl text eol=lf
*.json text eol=lf
"""

# Seul fichier non versionne du dossier : les reglages propres a une machine.
KB_GITIGNORE = """config.local.json
*.tmp
"""


def cmd_init(args) -> int:
    written = []
    KB.mkdir(parents=True, exist_ok=True)
    if write_if_changed(KB / "config.json", json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False) + "\n"):
        written.append("%s/config.json" % KB_DIRNAME)
    if not (KB / "notes.jsonl").is_file():
        write_if_changed(KB / "notes.jsonl", "")
        written.append("%s/notes.jsonl" % KB_DIRNAME)
    if write_if_changed(KB / "README.md", KB_README):
        written.append("%s/README.md" % KB_DIRNAME)
    if write_if_changed(KB / ".gitattributes", KB_GITATTRIBUTES):
        written.append("%s/.gitattributes" % KB_DIRNAME)
    if write_if_changed(KB / ".gitignore", KB_GITIGNORE):
        written.append("%s/.gitignore" % KB_DIRNAME)
    emit({"root": str(ROOT), "kb": KB_DIRNAME, "written": written}, args)
    return 0


def cmd_index(args) -> int:
    if not (KB / "config.json").is_file():
        cmd_init(argparse.Namespace(quiet=getattr(args, "quiet", False)))
    cfg = load_config()

    file_records = scan_files(cfg, list_files(cfg))
    symbols = extract_symbols(cfg, file_records)
    names = edge_names(cfg, symbols)
    edges = build_edges(cfg, file_records, names)

    files_text = jsonl(file_records)
    symbols_text = jsonl(symbols)
    graph = {
        "schema": SCHEMA,
        "nodes": {"files": [rec["path"] for rec in file_records], "names": names},
        "edges": edges,
    }
    graph_text = json.dumps(graph, indent=1, ensure_ascii=False, sort_keys=True) + "\n"
    digest = sha_text(files_text, symbols_text, graph_text)

    notes = read_jsonl(KB / "notes.jsonl")
    manifest = {
        "schema": SCHEMA,
        "tool_version": TOOL_VERSION,
        "digest": digest,
        "config_digest": sha_text(
            (KB / "config.json").read_text(encoding="utf-8") if (KB / "config.json").is_file() else ""),
        "counts": {
            "files": len(file_records),
            "symbols": len(symbols),
            "edges": len(edges),
            "notes": len(notes),
        },
        "languages": sorted({rec["lang"] for rec in file_records}),
    }
    manifest_text = json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n"

    changed = []
    for name, text in (
        ("files.jsonl", files_text),
        ("symbols.jsonl", symbols_text),
        ("graph.json", graph_text),
        ("manifest.json", manifest_text),
    ):
        if write_if_changed(KB / name, text):
            changed.append("%s/%s" % (KB_DIRNAME, name))

    if build_db(force=bool(getattr(args, "force", False))):
        changed.append("%s/%s" % (KB_DIRNAME, DB_PATH_NAME))

    payload = {
        "counts": manifest["counts"],
        "languages": manifest["languages"],
        "digest": digest,
        "changed": changed,
    }
    hooks = reconcile_hooks(cfg)
    if hooks:
        payload["hooks_installes"] = hooks
    emit(payload, args)
    return 0


# --------------------------------------------------------------------------
# Cache sqlite derive
# --------------------------------------------------------------------------

DB_PATH_NAME = "index.sqlite"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY, lang TEXT, sha256 TEXT, lines INTEGER, size INTEGER);
CREATE TABLE IF NOT EXISTS symbols (
    id TEXT PRIMARY KEY, name TEXT, kind TEXT, lang TEXT, path TEXT,
    line INTEGER, signature TEXT);
CREATE TABLE IF NOT EXISTS edges (
    src TEXT, dst TEXT, count INTEGER, PRIMARY KEY (src, dst));
CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY, subject TEXT, kind TEXT, text TEXT, tags TEXT,
    author TEXT, created_at TEXT, source TEXT);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_path ON symbols(path);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst);
CREATE INDEX IF NOT EXISTS idx_notes_subject ON notes(subject);
"""


def db_digest_current() -> str:
    manifest = load_manifest()
    return sha_text(manifest.get("digest", ""), sha_text(jsonl(read_jsonl(KB / "notes.jsonl"))))


def build_db(force: bool = False) -> bool:
    db_path = KB / DB_PATH_NAME
    target = db_digest_current()
    if not force and db_path.is_file():
        try:
            probe = sqlite3.connect(str(db_path))
            try:
                row = probe.execute("SELECT value FROM meta WHERE key = 'digest'").fetchone()
            finally:
                probe.close()
            if row and row[0] == target:
                return False
        except sqlite3.DatabaseError:
            pass

    tmp_path = db_path.parent / (DB_PATH_NAME + ".tmp")
    if tmp_path.exists():
        tmp_path.unlink()
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(tmp_path))
    try:
        conn.executescript(SCHEMA_SQL)
        conn.executemany(
            "INSERT OR REPLACE INTO files VALUES (?,?,?,?,?)",
            [(r["path"], r["lang"], r["sha256"], r["lines"], r["size"])
             for r in read_jsonl(KB / "files.jsonl")],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO symbols VALUES (?,?,?,?,?,?,?)",
            [(s["id"], s["name"], s["kind"], s["lang"], s["path"], s["line"], s.get("signature", ""))
             for s in read_jsonl(KB / "symbols.jsonl")],
        )
        graph_path = KB / "graph.json"
        graph = json.loads(graph_path.read_text(encoding="utf-8")) if graph_path.is_file() else {"edges": []}
        conn.executemany(
            "INSERT OR REPLACE INTO edges VALUES (?,?,?)",
            [(e["src"], e["dst"], e["count"]) for e in graph.get("edges", [])],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO notes VALUES (?,?,?,?,?,?,?,?)",
            [(n["id"], n["subject"], n.get("kind", "topic"), n["text"], ",".join(n.get("tags", [])),
              n.get("author", ""), n.get("created_at", ""), n.get("source", ""))
             for n in read_jsonl(KB / "notes.jsonl")],
        )
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('digest', ?)", (target,))
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('tool_version', ?)", (TOOL_VERSION,))
        conn.commit()
        conn.execute("VACUUM")
    finally:
        conn.close()
    if db_path.exists():
        db_path.unlink()
    tmp_path.replace(db_path)
    return True


def db() -> sqlite3.Connection:
    db_path = KB / DB_PATH_NAME
    if not db_path.is_file():
        raise SystemExit(
            "rgkb: %s/%s absent. Lancer : python .claude/rgkb/rgkb.py index" % (KB_DIRNAME, DB_PATH_NAME)
        )
    conn = sqlite3.connect("file:%s?mode=ro" % db_path.as_posix(), uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def rows(cursor) -> list:
    return [dict(row) for row in cursor.fetchall()]


# --------------------------------------------------------------------------
# Hooks git : reindexation apres une operation qui change l'arbre de travail
# --------------------------------------------------------------------------
#
# post-merge (donc git pull) et post-rewrite (rebase) sont installes par
# defaut : ils reindexent apres coup, sans jamais bloquer l'operation git.
#
# pre-commit est optionnel (--with-commit) : il reindexe et met .knowledge/ a
# l'index avant chaque commit, de sorte que tout commit porte un index coherent
# avec son code. C'est ce qui rend le changement de branche silencieux.
#
# post-checkout n'est deliberement PAS installe : reindexer apres un changement
# de branche salit l'arbre de travail a chaque fois, et git refuse alors le
# checkout suivant. Sans pre-commit, chaque branche porte un index perime que
# le hook contredirait aussitot ; avec pre-commit, il n'a plus rien a faire.

HOOK_NAMES = ("post-merge", "post-rewrite")
COMMIT_HOOK = "pre-commit"
ALL_HOOK_NAMES = HOOK_NAMES + (COMMIT_HOOK,)
HOOK_BEGIN = "# >>> rgkb >>>"
HOOK_END = "# <<< rgkb <<<"


def hook_block(hook: str) -> str:
    conditions = [
        '[ "${RGKB_NO_HOOK:-0}" != "1" ]',
        '[ -f .knowledge/manifest.json ]',
        '[ -f .claude/rgkb/rgkb.py ]',
    ]
    if hook == COMMIT_HOOK:
        purpose = ("# Reindexe et met .knowledge/ a l'index avant le commit, pour que le\n"
                   "# commit porte un index coherent avec son code.")
        action = ('            "$rgkb_py" .claude/rgkb/rgkb.py index >/dev/null 2>&1 '
                  '&& git add -A .knowledge >/dev/null 2>&1 || true')
    else:
        purpose = ("# Reindexe la base de connaissance rgkb apres une operation git qui a pu\n"
                   "# changer l'arbre de travail.")
        action = '            "$rgkb_py" .claude/rgkb/rgkb.py index >/dev/null 2>&1 || true'
    lines = [
        HOOK_BEGIN,
        purpose,
        "# N'ecrit que si le contenu a change et ne fait jamais echouer git.",
        "# Neutralisable ponctuellement avec RGKB_NO_HOOK=1.",
        "if " + " && ".join(conditions) + "; then",
        "    for rgkb_py in python3 python py; do",
        '        if command -v "$rgkb_py" >/dev/null 2>&1; then',
        action,
        "            break",
        "        fi",
        "    done",
        "fi",
        HOOK_END,
        "",
    ]
    return "\n".join(lines)


def git_hooks_dir():
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--git-path", "hooks"], cwd=str(ROOT),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace")
    except OSError:
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    path = Path(proc.stdout.strip())
    if not path.is_absolute():
        path = ROOT / path
    return path


def strip_hook_block(text: str) -> str:
    start = text.find(HOOK_BEGIN)
    if start == -1:
        return text
    end = text.find(HOOK_END, start)
    if end == -1:
        return text[:start]
    return text[:start] + text[end + len(HOOK_END):].lstrip("\n")


def write_hook(path: Path, text: str) -> None:
    # write_bytes plutot que write_text : garantit des fins de ligne LF, que
    # git exige pour un hook, y compris sous Windows.
    path.write_bytes(text.encode("utf-8"))
    try:
        os.chmod(path, 0o755)
    except OSError:
        pass


def set_config_hooks(mode: str) -> bool:
    # L'intention est ecrite dans config.json, qui est versionne : c'est ce qui
    # fait voyager les hooks d'un utilisateur a l'autre. Les autres cles sont
    # preservees et leur ordre aussi.
    path = KB / "config.json"
    data = dict(DEFAULT_CONFIG)
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                data = existing
        except json.JSONDecodeError:
            pass
    if data.get("hooks") == mode:
        return False
    data["hooks"] = mode
    return write_if_changed(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def reconcile_hooks(cfg: dict):
    # Applique l'intention declaree dans config.json. Appelee par index, donc
    # par le premier `rgkb index` d'un utilisateur qui vient de cloner : il
    # obtient les hooks sans avoir rien a lancer.
    mode = str(cfg.get("hooks") or "off").lower()
    if mode not in ("auto", "commit"):
        return None
    report = op_hooks("install", with_commit=(mode == "commit"))
    if not report.get("ok"):
        return None
    changed = {k: v for k, v in report.get("hooks", {}).items() if v != "identique"}
    return {"mode": mode, "hooks": changed} if changed else None


def op_hooks(action: str = "status", with_commit: bool = False, persist: bool = False) -> dict:
    hooks_dir = git_hooks_dir()
    if hooks_dir is None:
        return {"ok": False, "reason": "pas un depot git, ou git introuvable dans le PATH"}

    report = {"ok": True, "hooks_dir": str(hooks_dir), "action": action, "hooks": {}}

    if action == "status":
        for name in ALL_HOOK_NAMES:
            path = hooks_dir / name
            if not path.is_file():
                report["hooks"][name] = "absent"
            elif HOOK_BEGIN in path.read_text(encoding="utf-8", errors="replace"):
                report["hooks"][name] = "installe"
            else:
                report["hooks"][name] = "present sans rgkb"
        return report

    targets = ALL_HOOK_NAMES if action == "remove" else (
        HOOK_NAMES + ((COMMIT_HOOK,) if with_commit else ()))

    hooks_dir.mkdir(parents=True, exist_ok=True)
    for name in targets:
        path = hooks_dir / name
        existing = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""

        if action == "remove":
            if HOOK_BEGIN not in existing:
                report["hooks"][name] = "rien a retirer"
                continue
            stripped = strip_hook_block(existing)
            if stripped.strip() in ("", "#!/bin/sh"):
                path.unlink()
                report["hooks"][name] = "supprime"
            else:
                write_hook(path, stripped)
                report["hooks"][name] = "bloc retire, hook existant conserve"
            continue

        block = hook_block(name)
        if not existing:
            # Meme forme que la branche de mise a jour, sinon une relance
            # rapporterait "mis a jour" sur un hook pourtant inchange.
            write_hook(path, "#!/bin/sh\n\n" + block)
            report["hooks"][name] = "cree"
        elif HOOK_BEGIN in existing:
            updated = strip_hook_block(existing).rstrip("\n") + "\n\n" + block
            if updated == existing:
                report["hooks"][name] = "identique"
            else:
                write_hook(path, updated)
                report["hooks"][name] = "mis a jour"
        else:
            # Hook deja ecrit par quelqu'un d'autre : on ajoute, on n'ecrase pas.
            write_hook(path, existing.rstrip("\n") + "\n\n" + block)
            report["hooks"][name] = "bloc ajoute au hook existant"

    if persist:
        mode = "off" if action == "remove" else ("commit" if with_commit else "auto")
        if set_config_hooks(mode):
            report["config"] = ("%s/config.json : hooks = %s (versionne, donc applique "
                                "automatiquement chez les autres utilisateurs au premier "
                                "index)" % (KB_DIRNAME, mode))

    if action == "install" and not with_commit:
        report["note"] = ("pre-commit non installe : ajouter --with-commit pour que chaque "
                          "commit porte un index a jour et que le changement de branche "
                          "ne salisse jamais l'arbre de travail")
    return report


# --------------------------------------------------------------------------
# Operations exposees (CLI et MCP partagent ces fonctions)
# --------------------------------------------------------------------------

def op_search(pattern: str, glob: str = None, fixed: bool = False,
              ignore_case: bool = False, max_results: int = 50, context: int = 0) -> dict:
    cfg = load_config()
    args = ["--json", "--no-messages"]
    if fixed:
        args.append("-F")
    if ignore_case:
        args.append("-i")
    if context:
        args += ["-C", str(int(context))]
    args += ["-e", pattern]
    args += glob_args(cfg, [glob] if glob else None)
    args.append(".")
    results = []
    truncated = False
    for event in iter_rg_json(run_rg(cfg, args)):
        if event.get("type") != "match":
            continue
        if len(results) >= max_results:
            truncated = True
            break
        data = event["data"]
        results.append({
            "path": event_path(event),
            "line": int(data.get("line_number") or 0),
            "text": ((data.get("lines") or {}).get("text") or "").rstrip("\n"),
        })
    return {"pattern": pattern, "count": len(results), "truncated": truncated, "results": results}


def op_definition(name: str, exact: bool = True, limit: int = 50) -> dict:
    conn = db()
    try:
        if exact:
            cur = conn.execute(
                "SELECT id, name, kind, lang, path, line, signature FROM symbols "
                "WHERE name = ? ORDER BY path, line LIMIT ?", (name, limit))
        else:
            cur = conn.execute(
                "SELECT id, name, kind, lang, path, line, signature FROM symbols "
                "WHERE name LIKE ? ORDER BY name, path, line LIMIT ?", ("%" + name + "%", limit))
        found = rows(cur)
    finally:
        conn.close()
    return {"name": name, "exact": exact, "count": len(found), "symbols": found}


def op_symbols(path: str = None, name_prefix: str = None, kind: str = None,
               lang: str = None, limit: int = 100) -> dict:
    clauses = []
    params = []
    if path:
        clauses.append("path = ?")
        params.append(norm_path(path))
    if name_prefix:
        clauses.append("name LIKE ?")
        params.append(name_prefix + "%")
    if kind:
        clauses.append("kind = ?")
        params.append(kind)
    if lang:
        clauses.append("lang = ?")
        params.append(lang)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    conn = db()
    try:
        cur = conn.execute(
            "SELECT id, name, kind, lang, path, line, signature FROM symbols %s "
            "ORDER BY path, line LIMIT ?" % where, params)
        found = rows(cur)
    finally:
        conn.close()
    return {"count": len(found), "symbols": found}


def op_neighbors(node: str, limit: int = 30) -> dict:
    conn = db()
    try:
        as_file = conn.execute("SELECT path FROM files WHERE path = ?", (norm_path(node),)).fetchone()
        if as_file:
            path = norm_path(node)
            mentions = rows(conn.execute(
                "SELECT dst AS name, count FROM edges WHERE src = ? ORDER BY count DESC, dst LIMIT ?",
                (path, limit)))
            for item in mentions:
                defs = rows(conn.execute(
                    "SELECT path, line, kind FROM symbols WHERE name = ? ORDER BY path, line LIMIT 3",
                    (item["name"],)))
                item["defined_in"] = defs
            defines = rows(conn.execute(
                "SELECT name, kind, line FROM symbols WHERE path = ? ORDER BY line LIMIT ?", (path, limit)))
            return {"node": path, "node_kind": "file", "defines": defines, "mentions": mentions}

        name = node
        defs = rows(conn.execute(
            "SELECT id, kind, lang, path, line, signature FROM symbols WHERE name = ? ORDER BY path, line LIMIT ?",
            (name, limit)))
        used_by = rows(conn.execute(
            "SELECT src AS path, count FROM edges WHERE dst = ? ORDER BY count DESC, src LIMIT ?",
            (name, limit)))
        return {"node": name, "node_kind": "symbol", "defined_in": defs, "used_by": used_by}
    finally:
        conn.close()


def op_file_card(path: str, limit: int = 40) -> dict:
    rel = norm_path(path)
    conn = db()
    try:
        info = conn.execute(
            "SELECT path, lang, sha256, lines, size FROM files WHERE path = ?", (rel,)).fetchone()
        if info is None:
            return {"path": rel, "indexed": False,
                    "hint": "Fichier absent de l'index. Verifier le chemin ou relancer index."}
        card = dict(info)
        card["indexed"] = True
        card["symbols"] = rows(conn.execute(
            "SELECT name, kind, line, signature FROM symbols WHERE path = ? ORDER BY line LIMIT ?",
            (rel, limit)))
        card["mentions"] = rows(conn.execute(
            "SELECT dst AS name, count FROM edges WHERE src = ? ORDER BY count DESC, dst LIMIT ?",
            (rel, limit)))
        card["used_by"] = rows(conn.execute(
            "SELECT DISTINCT e.src AS path FROM edges e JOIN symbols s ON s.name = e.dst "
            "WHERE s.path = ? AND e.src <> ? ORDER BY e.src LIMIT ?", (rel, rel, limit)))
        card["notes"] = rows(conn.execute(
            "SELECT id, subject, kind, text, tags, author, created_at, source FROM notes "
            "WHERE subject = ? ORDER BY created_at LIMIT ?", (rel, limit)))
    finally:
        conn.close()
    stale = file_is_stale(rel, card.get("sha256"))
    card["stale"] = stale
    return card


def file_is_stale(rel: str, indexed_sha) -> bool:
    abs_path = ROOT / rel
    if not abs_path.is_file():
        return True
    try:
        sha, _lines, _size = hash_file(abs_path)
    except OSError:
        return True
    return sha != indexed_sha


def op_notes(subject: str = None, tag: str = None, query: str = None, limit: int = 50) -> dict:
    clauses = []
    params = []
    if subject:
        clauses.append("subject = ?")
        params.append(norm_path(subject))
    if tag:
        clauses.append("(',' || tags || ',') LIKE ?")
        params.append("%," + tag + ",%")
    if query:
        clauses.append("(text LIKE ? OR subject LIKE ?)")
        params += ["%" + query + "%", "%" + query + "%"]
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    conn = db()
    try:
        found = rows(conn.execute(
            "SELECT id, subject, kind, text, tags, author, created_at, source FROM notes %s "
            "ORDER BY created_at DESC, id LIMIT ?" % where, params))
    finally:
        conn.close()
    return {"count": len(found), "notes": found}


def op_note_add(subject: str, text: str, tags=None, kind: str = "topic",
                author: str = None, source: str = None) -> dict:
    if not subject or not text:
        raise ValueError("subject et text sont obligatoires")
    path = KB / "notes.jsonl"
    existing = read_jsonl(path)
    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    note = {
        "id": "n-" + sha_text(subject, text, created_at)[:12],
        "subject": norm_path(subject),
        "kind": kind,
        "text": text.strip(),
        "tags": sorted({t.strip() for t in (tags or []) if t.strip()}),
        "author": author or os.environ.get("RGKB_AUTHOR") or "",
        "created_at": created_at,
        "source": source or "",
    }
    for old in existing:
        if old.get("subject") == note["subject"] and old.get("text") == note["text"]:
            return {"added": False, "reason": "note identique deja presente", "note": old}
    existing.append(note)
    existing.sort(key=lambda n: (n.get("subject", ""), n.get("created_at", ""), n.get("id", "")))
    write_if_changed(path, jsonl(existing))
    build_db(force=True)
    return {"added": True, "note": note, "file": "%s/notes.jsonl" % KB_DIRNAME}


def op_note_rm(note_id: str) -> dict:
    path = KB / "notes.jsonl"
    existing = read_jsonl(path)
    kept = [n for n in existing if n.get("id") != note_id]
    if len(kept) == len(existing):
        return {"removed": False, "reason": "identifiant introuvable", "id": note_id}
    write_if_changed(path, jsonl(kept))
    build_db(force=True)
    return {"removed": True, "id": note_id}


def op_status() -> dict:
    manifest = load_manifest()
    if not manifest:
        return {"initialized": False, "stale": True,
                "hint": "Base absente. Lancer : python .claude/rgkb/rgkb.py index"}
    cfg = load_config()
    indexed = {rec["path"]: rec["sha256"] for rec in read_jsonl(KB / "files.jsonl")}
    try:
        current = set(list_files(cfg))
    except RgMissing as exc:
        return {"initialized": True, "stale": None, "error": str(exc)}
    added = sorted(current - set(indexed))
    removed = sorted(set(indexed) - current)
    modified = []
    max_bytes = int(cfg.get("max_file_bytes") or 0)
    for rel in sorted(current & set(indexed)):
        abs_path = ROOT / rel
        try:
            if max_bytes and abs_path.stat().st_size > max_bytes:
                continue
            sha, _lines, _size = hash_file(abs_path)
        except OSError:
            continue
        if sha != indexed[rel]:
            modified.append(rel)
    db_ok = False
    db_path = KB / DB_PATH_NAME
    if db_path.is_file():
        try:
            probe = sqlite3.connect(str(db_path))
            try:
                row = probe.execute("SELECT value FROM meta WHERE key = 'digest'").fetchone()
            finally:
                probe.close()
            db_ok = bool(row) and row[0] == db_digest_current()
        except sqlite3.DatabaseError:
            db_ok = False
    stale = bool(added or removed or modified) or not db_ok
    # status est la premiere commande d'un cycle : c'est le point le plus tot
    # ou l'intention de hooks declaree dans config.json peut etre appliquee
    # chez un utilisateur qui vient de cloner.
    hooks = reconcile_hooks(cfg)
    return {
        "initialized": True,
        "stale": stale,
        "hooks_installes": hooks,
        "db_in_sync": db_ok,
        "counts": manifest.get("counts", {}),
        "added": added[:50],
        "modified": modified[:50],
        "removed": removed[:50],
        "added_total": len(added),
        "modified_total": len(modified),
        "removed_total": len(removed),
    }


def op_doctor() -> dict:
    cfg = load_config()
    report = {
        "root": str(ROOT),
        "python": sys.version.split()[0],
        "kb_present": KB.is_dir(),
        "db_present": (KB / DB_PATH_NAME).is_file(),
        "hooks_declares": str(cfg.get("hooks") or "off").lower(),
    }
    hooks_state = op_hooks("status")
    report["hooks"] = hooks_state.get("hooks") if hooks_state.get("ok") else hooks_state.get("reason")
    try:
        binary = rg_bin(cfg)
        proc = subprocess.run([binary, "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, encoding="utf-8", errors="replace")
        report["rg"] = binary
        report["rg_version"] = proc.stdout.splitlines()[0] if proc.stdout else ""
        report["ok"] = True
    except RgMissing as exc:
        report["rg"] = None
        report["ok"] = False
        report["error"] = str(exc)
    return report


# --------------------------------------------------------------------------
# Serveur MCP stdio
# --------------------------------------------------------------------------

TOOLS = [
    {
        "name": "search",
        "description": "Recherche ripgrep dans le depot, resultats structures (chemin, ligne, texte). "
                       "Respecte les exclusions de .knowledge/config.json.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Expression reguliere (ou chaine litterale si fixed)."},
                "glob": {"type": "string", "description": "Glob ripgrep restreignant les fichiers, ex. *.py"},
                "fixed": {"type": "boolean", "description": "Traiter le motif comme une chaine litterale."},
                "ignore_case": {"type": "boolean"},
                "context": {"type": "integer", "description": "Lignes de contexte autour de chaque resultat."},
                "max_results": {"type": "integer", "description": "Plafond de resultats, defaut 50."},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "definition",
        "description": "Retrouve ou un symbole est defini (fichier, ligne, signature) depuis l'index versionne.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "exact": {"type": "boolean", "description": "Correspondance exacte, defaut true."},
                "limit": {"type": "integer"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "symbols",
        "description": "Liste les definitions indexees, filtrables par fichier, prefixe de nom, type ou langage.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "name_prefix": {"type": "string"},
                "kind": {"type": "string"},
                "lang": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "neighbors",
        "description": "Voisinage dans le graphe : pour un fichier, ce qu'il definit et ce qu'il mentionne ; "
                       "pour un symbole, ou il est defini et quels fichiers l'utilisent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node": {"type": "string", "description": "Chemin de fichier ou nom de symbole."},
                "limit": {"type": "integer"},
            },
            "required": ["node"],
        },
    },
    {
        "name": "file_card",
        "description": "Fiche d'un fichier : langage, taille, symboles definis, symboles mentionnes, "
                       "fichiers qui en dependent, notes de connaissance attachees, fraicheur de l'index.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["path"],
        },
    },
    {
        "name": "notes",
        "description": "Lit la base de connaissance partagee (notes versionnees), par sujet, tag ou recherche plein texte.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "Chemin de fichier, nom de symbole ou sujet libre."},
                "tag": {"type": "string"},
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "note_add",
        "description": "Ajoute une note durable a la base de connaissance versionnee (.knowledge/notes.jsonl). "
                       "A utiliser pour ce qui ne se deduit pas du code : intention, piege, decision, resultat acquis.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "Chemin de fichier, nom de symbole ou sujet libre."},
                "text": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "kind": {"type": "string", "description": "file, symbol ou topic. Defaut topic."},
                "source": {"type": "string", "description": "Origine, ex. SPEC-x.md, EXP-2026-09-06-y.md, audit."},
            },
            "required": ["subject", "text"],
        },
    },
    {
        "name": "status",
        "description": "Fraicheur de l'index : fichiers ajoutes, modifies, supprimes depuis la derniere indexation.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "reindex",
        "description": "Reconstruit l'index et le cache sqlite depuis le code source courant.",
        "inputSchema": {"type": "object", "properties": {"force": {"type": "boolean"}}},
    },
]


def dispatch_tool(name: str, args: dict) -> dict:
    args = args or {}
    if name == "search":
        return op_search(
            args["pattern"], args.get("glob"), bool(args.get("fixed")),
            bool(args.get("ignore_case")), int(args.get("max_results") or 50),
            int(args.get("context") or 0))
    if name == "definition":
        exact = args.get("exact")
        return op_definition(args["name"], True if exact is None else bool(exact),
                             int(args.get("limit") or 50))
    if name == "symbols":
        return op_symbols(args.get("path"), args.get("name_prefix"), args.get("kind"),
                          args.get("lang"), int(args.get("limit") or 100))
    if name == "neighbors":
        return op_neighbors(args["node"], int(args.get("limit") or 30))
    if name == "file_card":
        return op_file_card(args["path"], int(args.get("limit") or 40))
    if name == "notes":
        return op_notes(args.get("subject"), args.get("tag"), args.get("query"),
                        int(args.get("limit") or 50))
    if name == "note_add":
        return op_note_add(args["subject"], args["text"], args.get("tags"),
                           args.get("kind") or "topic", args.get("author"), args.get("source"))
    if name == "status":
        return op_status()
    if name == "reindex":
        cmd_index(argparse.Namespace(force=bool(args.get("force")), json=True, quiet=True))
        return op_status()
    raise ValueError("outil inconnu : %s" % name)


def mcp_send(message: dict) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def mcp_serve() -> int:
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            request = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(request, list):
            continue
        method = request.get("method")
        req_id = request.get("id")

        if method == "initialize":
            params = request.get("params") or {}
            client_version = params.get("protocolVersion")
            mcp_send({"jsonrpc": "2.0", "id": req_id, "result": {
                "protocolVersion": client_version if isinstance(client_version, str) else MCP_PROTOCOL,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "rgkb", "version": TOOL_VERSION},
                "instructions": (
                    "Base de connaissance locale du depot, adossee a ripgrep et versionnee dans "
                    ".knowledge/. Utiliser search pour chercher, definition et neighbors pour "
                    "naviguer, file_card avant de lire un fichier entier, notes pour ce que le "
                    "code ne dit pas, note_add pour consigner ce qui doit survivre a la session."
                ),
            }})
            continue
        if method in ("notifications/initialized", "notifications/cancelled"):
            continue
        if method == "ping":
            mcp_send({"jsonrpc": "2.0", "id": req_id, "result": {}})
            continue
        if method == "tools/list":
            mcp_send({"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}})
            continue
        if method == "tools/call":
            params = request.get("params") or {}
            tool_name = params.get("name") or ""
            try:
                payload = dispatch_tool(tool_name, params.get("arguments") or {})
                text = json.dumps(payload, ensure_ascii=False, indent=2)
                mcp_send({"jsonrpc": "2.0", "id": req_id, "result": {
                    "content": [{"type": "text", "text": text}], "isError": False}})
            except Exception as exc:  # remonte l'erreur au client sans tuer le serveur
                mcp_send({"jsonrpc": "2.0", "id": req_id, "result": {
                    "content": [{"type": "text", "text": "rgkb: %s: %s" % (type(exc).__name__, exc)}],
                    "isError": True}})
            continue
        if req_id is not None:
            mcp_send({"jsonrpc": "2.0", "id": req_id,
                      "error": {"code": -32601, "message": "methode inconnue : %s" % method}})
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def emit(payload, args=None) -> None:
    # En mode MCP, stdout porte le protocole JSON-RPC : rien d'autre ne doit y aller.
    if args is not None and getattr(args, "quiet", False):
        return
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="rgkb",
        description="Base de connaissance locale du depot, adossee a ripgrep.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="cree .knowledge/ (config, notes, README, gitattributes)")

    p_index = sub.add_parser("index", help="reindexe le depot et reconstruit le cache sqlite")
    p_index.add_argument("--force", action="store_true", help="reconstruit le sqlite meme si a jour")

    p_search = sub.add_parser("search", help="recherche ripgrep structuree")
    p_search.add_argument("pattern")
    p_search.add_argument("--glob")
    p_search.add_argument("--fixed", action="store_true")
    p_search.add_argument("--ignore-case", action="store_true")
    p_search.add_argument("--context", type=int, default=0)
    p_search.add_argument("--max-results", type=int, default=50)

    p_def = sub.add_parser("definition", help="ou un symbole est defini")
    p_def.add_argument("name")
    p_def.add_argument("--partial", action="store_true")
    p_def.add_argument("--limit", type=int, default=50)

    p_sym = sub.add_parser("symbols", help="liste les definitions indexees")
    p_sym.add_argument("--path")
    p_sym.add_argument("--name-prefix")
    p_sym.add_argument("--kind")
    p_sym.add_argument("--lang")
    p_sym.add_argument("--limit", type=int, default=100)

    p_nb = sub.add_parser("neighbors", help="voisinage d'un fichier ou d'un symbole")
    p_nb.add_argument("node")
    p_nb.add_argument("--limit", type=int, default=30)

    p_card = sub.add_parser("file", help="fiche d'un fichier")
    p_card.add_argument("path")
    p_card.add_argument("--limit", type=int, default=40)

    p_notes = sub.add_parser("notes", help="lit la base de connaissance")
    p_notes.add_argument("--subject")
    p_notes.add_argument("--tag")
    p_notes.add_argument("--query")
    p_notes.add_argument("--limit", type=int, default=50)

    p_add = sub.add_parser("note-add", help="ajoute une note versionnee")
    p_add.add_argument("subject")
    p_add.add_argument("text")
    p_add.add_argument("--tag", action="append", default=[])
    p_add.add_argument("--kind", default="topic")
    p_add.add_argument("--author")
    p_add.add_argument("--source")

    p_rm = sub.add_parser("note-rm", help="supprime une note par identifiant")
    p_rm.add_argument("id")

    p_status = sub.add_parser("status", help="fraicheur de l'index")
    p_status.add_argument("--check", action="store_true",
                          help="sort en code 1 si l'index est perime (usage CI)")

    p_hooks = sub.add_parser("hooks", help="hooks git de reindexation (post-merge, post-rewrite, pre-commit)")
    p_hooks.add_argument("action", nargs="?", default="status", choices=["status", "install", "remove"])
    p_hooks.add_argument("--with-commit", action="store_true",
                         help="installer aussi pre-commit (reindexe et met .knowledge/ a l'index)")

    sub.add_parser("doctor", help="verifie ripgrep, python et la presence de la base")
    sub.add_parser("mcp", help="demarre le serveur MCP sur stdio")

    args = parser.parse_args(argv)

    try:
        if args.command == "init":
            return cmd_init(args)
        if args.command == "index":
            return cmd_index(args)
        if args.command == "mcp":
            return mcp_serve()
        if args.command == "hooks":
            report = op_hooks(args.action, getattr(args, "with_commit", False), persist=True)
            emit(report)
            return 0 if report.get("ok") else 1
        if args.command == "doctor":
            report = op_doctor()
            emit(report)
            return 0 if report.get("ok") else 1
        if args.command == "search":
            emit(op_search(args.pattern, args.glob, args.fixed, args.ignore_case,
                           args.max_results, args.context))
            return 0
        if args.command == "definition":
            emit(op_definition(args.name, not args.partial, args.limit))
            return 0
        if args.command == "symbols":
            emit(op_symbols(args.path, args.name_prefix, args.kind, args.lang, args.limit))
            return 0
        if args.command == "neighbors":
            emit(op_neighbors(args.node, args.limit))
            return 0
        if args.command == "file":
            emit(op_file_card(args.path, args.limit))
            return 0
        if args.command == "notes":
            emit(op_notes(args.subject, args.tag, args.query, args.limit))
            return 0
        if args.command == "note-add":
            emit(op_note_add(args.subject, args.text, args.tag, args.kind, args.author, args.source))
            return 0
        if args.command == "note-rm":
            result = op_note_rm(args.id)
            emit(result)
            return 0 if result.get("removed") else 1
        if args.command == "status":
            report = op_status()
            emit(report)
            if args.check and (report.get("stale") or report.get("error")):
                return 1
            return 0
    except RgMissing as exc:
        print("rgkb: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
