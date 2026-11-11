# File merges of the generators, never an overwrite.
#
# Generic engine (Phase 1, used by software-factory, then code-index and ai-lab):
# - FU-01: JSON operations: array union at a path, key added if absent
#   (never overwritten), tasks deduplicated by label and inputs by id.
#   JSONC, BOM and CRLF kept; all or nothing (every plan computed before any
#   write); states créé / fusionné / identique / personnalisé-conservé (an
#   existing entry that differs from the wanted one is kept and reported).
# - FU-02: SF-01 to SF-03 below are expressed on FU-01, output unchanged.
# - FU-03: generic .gitignore lines (plan_lines).
# - FU-04: install_file (SF-04) for every generator.
# - FU-05: union into the `tools:` list of an agent frontmatter, never a removal.
# - FU-06: union into the --allowedTools "..." value of a ci/* script, never a
#   removal; a value it cannot complete safely is reported (warnings).
#
# software-factory:
# - SF-01: .claude/settings.json, permissions.deny rules as Read(...) for the
#   detected stack plus the generic rules; arrays merged without duplicates,
#   every existing key preserved.
# - SF-02: .vscode/tasks.json (version 2.0.0), factorySpec input and three
#   Factory tasks added only once; existing tasks and inputs preserved.
# - SF-03: .gitignore completed without duplicating a line (factory-logs/,
#   .harness/runs/).
# - SF-04: state of each file: created / merged / identical /
#   customized-kept (the state values below are the French labels shared by
#   all the skills and are kept as is). An unchanged file is not rewritten
#   (second application byte-for-byte identical).
# - Tolerant reading: UTF-8 BOM and JSONC (comments, trailing commas)
#   accepted, as VS Code and Windows tools write them. A strict JSON file is
#   re-serialized; a JSONC file is completed by inserting text, its comments
#   kept (see jsonc.py). BOM and CRLF line endings kept.
# - All or nothing: the three merges are computed before any write; an
#   unreadable file makes the whole operation fail without modifying anything.
# - D1: copy of _core into the project under .claude/harness/, never mixed
#   with a different existing core (nothing is copied in that case).
# - CO-02: the copy carries MANIFEST.json (version of the repository that produced it).
#   The upgrade of a copy (CO-03 to CO-06) lives in history.py and upgrade.py.
# - X1: a generated agent file carries its model in the harness.model block
#   of its frontmatter (install_agent). The block is regenerated in place even
#   in a customized file, which is then "bloc-mis-à-jour" (written, its text
#   outside the block kept); a file without the block (generated before) is kept.

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, NamedTuple, Optional, Sequence, Tuple

from . import jsonc, models, profiles

CREATED = "créé"
MERGED = "fusionné"
IDENTICAL = "identique"
KEPT = "personnalisé-conservé"
BLOCK_UPDATED = "bloc-mis-à-jour"

# PX-04, PF-05: the deny rules, the .gitignore lines, the tasks and the input of
# the base installation are profile data (the fallback profile), unchanged.
_BASE = profiles.get(profiles.fallback_name())
DENY_BY_STACK = _BASE["settings"]["deny_by_stack"]
GENERIC_DENY = _BASE["settings"]["deny_generic"]
GITIGNORE_LINES = _BASE["gitignore"]
BASE_INPUTS = _BASE["tasks"]["inputs"]
BASE_TASKS = _BASE["tasks"]["tasks"]


class FusionError(ValueError):
    """Unreadable existing file: it is not rewritten."""


def deny_rules(stacks: Iterable[str]) -> List[str]:
    """SF-01: rules of the detected stack only, then the generic ones."""
    rules: List[str] = []
    for stack in stacks:
        if stack not in DENY_BY_STACK:
            raise FusionError("unknown stack: %r (known: %s)" % (stack, ", ".join(DENY_BY_STACK)))
        rules.extend(r for r in DENY_BY_STACK[stack] if r not in rules)
    rules.extend(r for r in GENERIC_DENY if r not in rules)
    return rules


# ----------------------------------------------------- generic engine (FU-01)
#
# An operation mutates a JSON document in place and returns the paths it had
# to leave as they were (existing value different from the wanted one): it
# only ever adds, never overwrites nor removes.

Op = Callable[[Dict[str, Any]], List[str]]


def _container(data: Dict[str, Any], path: Sequence[str]) -> Dict[str, Any]:
    """Object at `path` (created when missing); a non-object on the way is refused."""
    node = data
    for i, key in enumerate(path):
        child = node.setdefault(key, {})
        if not isinstance(child, dict):
            raise FusionError("%s is not an object" % ".".join(path[: i + 1]))
        node = child
    return node


def op_array_union(path: Sequence[str], values: Iterable[Any]) -> Op:
    """FU-01: array at `path` completed with the missing `values`, in order."""
    path, values = tuple(path), list(values)

    def run(data: Dict[str, Any]) -> List[str]:
        parent = _container(data, path[:-1])
        array = parent.setdefault(path[-1], [])
        if not isinstance(array, list):
            raise FusionError("%s is not an array" % ".".join(path))
        for value in values:
            if value not in array:
                array.append(json.loads(json.dumps(value)))
        return []
    return run


def op_add_key(path: Sequence[str], key: str, value: Any) -> Op:
    """FU-01: key added if absent; an existing different value is kept and reported."""
    path = tuple(path)

    def run(data: Dict[str, Any]) -> List[str]:
        parent = _container(data, path)
        if key not in parent:
            parent[key] = json.loads(json.dumps(value))
            return []
        return [] if parent[key] == value else [".".join(path + (key,))]
    return run


def op_tasks(inputs: Iterable[Dict[str, Any]], tasks: Iterable[Dict[str, Any]],
             version: str = "2.0.0") -> Op:
    """FU-01: tasks.json, inputs deduplicated by id and tasks by label (an
    existing entry of the same id or label is never replaced)."""
    inputs, tasks = list(inputs), list(tasks)

    def run(data: Dict[str, Any]) -> List[str]:
        data.setdefault("version", version)
        have_inputs = data.setdefault("inputs", [])
        have_tasks = data.setdefault("tasks", [])
        if not isinstance(have_inputs, list) or not isinstance(have_tasks, list):
            raise FusionError("inputs and tasks must be arrays")
        ids = {i.get("id") for i in have_inputs if isinstance(i, dict)}
        for item in inputs:
            if item["id"] not in ids:
                have_inputs.append(json.loads(json.dumps(item)))
        labels = {t.get("label") for t in have_tasks if isinstance(t, dict)}
        for task in tasks:
            if task["label"] not in labels:
                have_tasks.append(json.loads(json.dumps(task)))
        return []
    return run


def merge_json(existing: Any, ops: Iterable[Op], what: str = "the file") -> Tuple[Dict[str, Any], List[str]]:
    """Applies `ops` to a copy of `existing` (None = new file); returns the
    merged document and the paths kept as they were."""
    data = json.loads(json.dumps(existing)) if existing is not None else {}
    if not isinstance(data, dict):
        raise FusionError("%s is not a JSON object" % what)
    kept: List[str] = []
    for op in ops:
        kept.extend(op(data))
    return data, kept


def merge_settings(existing: Optional[Dict[str, Any]], stacks: Iterable[str]) -> Dict[str, Any]:
    """SF-01 expressed on FU-01."""
    return merge_json(existing, [op_array_union(("permissions", "deny"), deny_rules(stacks))],
                      "settings.json")[0]


def merge_tasks(existing: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """SF-02 expressed on FU-01."""
    return merge_json(existing, [op_tasks(BASE_INPUTS, BASE_TASKS)], "tasks.json")[0]


def merge_gitignore(text: Optional[str], lines: Iterable[str] = GITIGNORE_LINES) -> str:
    """SF-03 / FU-03: lines added with the file's line ending (CRLF kept)."""
    content = text or ""
    present = {line.strip() for line in content.splitlines()}
    missing = [line for line in lines if line not in present]
    if not missing:
        return content
    nl = "\r\n" if "\r\n" in content else "\n"
    if content and not content.endswith("\n"):
        content += nl
    return content + "".join(line + nl for line in missing)


def dump_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _read_text(path: Path) -> Optional[str]:
    """Raw text (line endings and BOM intact), or None if absent."""
    if not path.exists():
        return None
    with open(path, encoding="utf-8", newline="") as fh:
        try:
            return fh.read()
        except UnicodeDecodeError as exc:
            raise FusionError("%s is not UTF-8 encoded (expected encoding: UTF-8, BOM accepted; "
                              "invalid byte at position %d), file left untouched" % (path, exc.start))


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".hcore-tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    os.replace(str(tmp), str(path))


class Plan(NamedTuple):
    """What a merge would do to one file: state, new text (None = no write),
    paths kept as they were."""
    state: str
    text: Optional[str]
    kept: Tuple[str, ...] = ()


def plan_json(path: Path, ops: Iterable[Op]) -> Plan:
    """FU-01: plan of a JSON/JSONC file, without writing anything. JSONC
    comments, BOM and CRLF kept; a file left as it was while an entry differs
    from the wanted one is customized-kept."""
    ops = list(ops)
    raw = _read_text(path)
    if raw is None:
        return Plan(CREATED, dump_json(merge_json(None, ops, str(path))[0]))
    try:
        doc = jsonc.loads(raw)
    except ValueError as exc:
        raise FusionError("%s is not valid JSON, merge refused: %s" % (path, exc))
    merged, kept = merge_json(doc.value, ops, str(path))
    if json.dumps(merged) == json.dumps(doc.value):
        return Plan(KEPT if kept else IDENTICAL, None, tuple(kept))
    if doc.loose:
        try:
            return Plan(MERGED, doc.patched(merged), tuple(kept))
        except ValueError as exc:
            raise FusionError("%s: JSONC merge impossible, file left untouched: %s" % (path, exc))
    text = dump_json(merged)
    if doc.newline == "\r\n":
        text = text.replace("\n", "\r\n")
    return Plan(MERGED, (jsonc.BOM if doc.bom else "") + text, tuple(kept))


def plan_lines(path: Path, lines: Iterable[str]) -> Plan:
    """FU-03: a line file (.gitignore) completed without duplicating a line."""
    before = _read_text(path)
    after = merge_gitignore(before, list(lines))
    if before is None:
        return Plan(CREATED, after)
    if after == before:
        return Plan(IDENTICAL, None)
    return Plan(MERGED, after)


def plan_text(path: Path, edit: Callable[[str], str]) -> Plan:
    """An existing text file completed by `edit` (FU-05, FU-06); an absent
    file has nothing to complete and stays absent."""
    before = _read_text(path)
    if before is None:
        return Plan(IDENTICAL, None)
    after = edit(before)
    return Plan(IDENTICAL, None) if after == before else Plan(MERGED, after)


def write_plans(root: Path, plans: Dict[str, Plan]) -> Dict[str, str]:
    """All or nothing: the plans are all computed before this call (an
    unreadable file has already failed); writes, then returns the states."""
    for rel, plan in plans.items():
        if plan.text is not None:
            _write(Path(root) / rel, plan.text)
    return {rel: plan.state for rel, plan in plans.items()}


def apply(root: Path, stacks: Iterable[str]) -> Dict[str, str]:
    """SF-01 to SF-04 (FU-02): computes the three merges, then writes (all or nothing)."""
    root = Path(root)
    deny = deny_rules(list(stacks))
    plans = {
        ".claude/settings.json": plan_json(root / ".claude" / "settings.json",
                                           [op_array_union(("permissions", "deny"), deny)]),
        ".vscode/tasks.json": plan_json(root / ".vscode" / "tasks.json",
                                        [op_tasks(BASE_INPUTS, BASE_TASKS)]),
        ".gitignore": plan_lines(root / ".gitignore", GITIGNORE_LINES),
    }
    return write_plans(root, plans)


def apply_merged(root: Path, merged: Dict[str, Any]) -> Dict[str, str]:
    """HJ-06, EP-05: the same three merges, from the union of several profiles (additive only)."""
    root = Path(root)
    plans = {
        ".claude/settings.json": plan_json(root / ".claude" / "settings.json",
                                           [op_array_union(("permissions", "deny"), merged["deny"])]),
        ".vscode/tasks.json": plan_json(root / ".vscode" / "tasks.json",
                                        [op_tasks(merged["inputs"], merged["tasks"])]),
        ".gitignore": plan_lines(root / ".gitignore", merged["gitignore"]),
    }
    return write_plans(root, plans)


# ------------------------------------------------- frontmatter tools (FU-05)

_TOOLS_LINE = re.compile(r"^(?P<lead>tools[ \t]*:[ \t]*)(?P<value>[^\r\n]*?)[ \t]*(?=\r?\n|\Z)", re.M)
_ITEM = re.compile(r"\r?\n(?P<indent>[ \t]*)-[ \t]*(?P<value>[^\r\n]*)")


def _split_comment(value: str) -> Tuple[str, str]:
    """(body, trailing YAML comment with its leading blanks); a '#' inside quotes
    or glued to a word is not a comment."""
    quote = None
    for i, c in enumerate(value):
        if quote:
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c == "#" and (i == 0 or value[i - 1] in " \t"):
            body = value[:i].rstrip()
            return body, value[len(body):]
    return value, ""


def _unquote(item: str) -> Tuple[str, str]:
    """(name, quote char or '') of one YAML scalar."""
    item = item.strip()
    if len(item) >= 2 and item[0] == item[-1] and item[0] in "\"'":
        return item[1:-1], item[0]
    return item, ""


def union_tools(text: str, tools: Iterable[str]) -> str:
    """FU-05: the `tools:` list of a YAML frontmatter completed with the
    missing tools, none removed. Forms: inline (`tools: A, B`), quoted
    (`tools: "A, B"`), flow (`[A, "B"]`) and block list (`- A` lines); a
    trailing YAML comment is kept after the list. No `tools:` line, or an
    empty `tools:` (YAML null), means the agent inherits every tool: nothing
    is added, which would restrict it."""
    tools = list(tools)
    match = models.frontmatter(text)
    if not match:
        raise FusionError("no YAML frontmatter (--- ... ---) holding a tools list")
    inner = match.group(2)
    line = _TOOLS_LINE.search(inner)
    if line is None:
        return text
    eol = "\r\n" if "\r\n" in inner else "\n"
    body, comment = _split_comment(line.group("value"))
    if body:
        bracket = body.startswith("[") and body.endswith("]")
        quoted = not bracket and len(body) >= 2 and body[0] == body[-1] and body[0] in "\"'"
        core = body[1:-1] if bracket or quoted else body
        items = [i for i in core.split(",") if i.strip()]
        present = [_unquote(i)[0] for i in items]
        missing = [t for t in tools if t not in present]
        if not missing:
            return text
        q = _unquote(items[0])[1] if bracket and items else ""
        joined = ", ".join([i.strip() for i in items] + ["%s%s%s" % (q, t, q) for t in missing])
        if bracket:
            joined = "[%s]" % joined
        elif quoted:
            joined = body[0] + joined + body[0]
        new_inner = inner[: line.start()] + line.group("lead") + joined + comment + inner[line.end():]
    else:
        rest = inner[line.end():]
        found = []
        pos = 0
        for item in _ITEM.finditer(rest):
            if item.start() != pos:
                break
            found.append(item)
            pos = item.end()
        if not found:
            return text            # empty tools: inherits every tool
        present = [_unquote(_split_comment(i.group("value"))[0])[0] for i in found]
        missing = [t for t in tools if t not in present]
        if not missing:
            return text
        indent = found[0].group("indent")
        q = _unquote(_split_comment(found[0].group("value"))[0])[1]
        cut = line.end() + pos
        added = "".join("%s%s- %s%s%s" % (eol, indent, q, t, q) for t in missing)
        new_inner = inner[:cut] + added + inner[cut:]
    return match.group(1) + new_inner + match.group(3) + text[match.end():]


# ------------------------------------------ CI script --allowedTools (FU-06)

_ALLOWED = re.compile(r"--allowedTools(?:[\"'][ \t]*,[ \t]*|[ \t]*=[ \t]*|[ \t]+)"
                      r"(?:\"(?P<dq>[^\"\r\n]*)\"|'(?P<sq>[^'\r\n]*)')")
_ALLOWED_ANY = re.compile(r"--allowedTools\b")


def union_allowed_tools(text: str, tools: Iterable[str]) -> Tuple[str, List[str]]:
    """FU-06: every `--allowedTools "..."` value of a ci/* script (bash
    `--allowedTools "A,B"` or PowerShell `'--allowedTools', 'A,B'`) completed
    with the missing entries, none removed. Returns (text, warnings). A value
    holding the other quote character, or an --allowedTools whose value is
    not a quoted string, is left unchanged and reported; a script without
    --allowedTools is left unchanged."""
    tools = list(tools)
    for tool in tools:
        if not tool.strip() or any(c in tool for c in "\"',\r\n"):
            raise FusionError("invalid --allowedTools entry: %r" % (tool,))
    warnings: List[str] = []
    done: List[int] = []

    def complete(m: "re.Match[str]") -> str:
        done.append(m.start())
        quote = '"' if m.group("dq") is not None else "'"
        value = m.group("dq") if quote == '"' else m.group("sq")
        line = text.count("\n", 0, m.start()) + 1
        if ('"' if quote == "'" else "'") in value:
            warnings.append("line %d: --allowedTools value holds a quote character, left unchanged" % line)
            return m.group(0)
        present = [t.strip() for t in value.split(",") if t.strip()]
        missing = [t for t in tools if t not in present]
        if not missing:
            return m.group(0)
        sep = ", " if ", " in value else ","
        start = m.start("dq") if quote == '"' else m.start("sq")
        return text[m.start(): start] + sep.join(present + missing) + quote
    new = _ALLOWED.sub(complete, text)
    for m in _ALLOWED_ANY.finditer(text):
        if m.start() not in done:
            line = text.count("\n", 0, m.start()) + 1
            warnings.append("line %d: --allowedTools without a quoted value, left unchanged" % line)
    return new, warnings


# ------------------------------------------------ generated files (FU-04)

def install_file(dest: Path, content: str) -> str:
    """SF-04 / FU-04: a generated file, of any generator, never overwrites a
    different existing file."""
    dest = Path(dest)
    state = file_state(dest, content)
    if state == CREATED:
        _write(dest, content)
    return state


def install_agent(dest: Path, content: str, model: str) -> Dict[str, str]:
    """SF-04 and X1: installs a generated agent whose frontmatter receives the
    model block. Returns the file state and what happened to the block."""
    dest = Path(dest)
    generated = models.with_model_block(content, model)
    if not dest.exists():
        _write(dest, generated)
        return {"state": CREATED, "model_block": "written"}
    current = _read_text(dest) or ""
    norm = lambda text: text.replace("\r\n", "\n")  # noqa: E731
    if norm(current) == norm(generated):
        return {"state": IDENTICAL, "model_block": "unchanged"}
    if not models.has_block(current):
        return {"state": KEPT, "model_block": "absent"}
    try:
        regenerated = models.with_model_block(current, model)
    except models.ModelsError:
        return {"state": KEPT, "model_block": "absent"}
    same_rest = norm(models.without_model_block(current)) == norm(models.without_model_block(generated))
    if regenerated == current:
        return {"state": MERGED if same_rest else KEPT, "model_block": "unchanged"}
    _write(dest, regenerated)
    # A customized file whose block alone is rewritten is not "customized-kept":
    # it was written, hence its own state.
    return {"state": MERGED if same_rest else BLOCK_UPDATED, "model_block": "updated"}


def file_state(dest: Path, content: str) -> str:
    """State that installing `content` at `dest` would have, without writing anything."""
    if not dest.exists():
        return CREATED
    current = _read_text(dest) or ""
    if current.replace("\r\n", "\n") == content.replace("\r\n", "\n"):
        return IDENTICAL
    return KEPT


CORE_MARKERS = ("harness.py", "hcore/__init__.py")
# CRLF tolerated: a copy checked out with Windows line endings stays recognized.
_CORE_VERSION_LINE = re.compile(r'^CORE_VERSION = "([^"]+)"\r?$', re.M)


def _tree(folder: Path) -> List[str]:
    """Relative posix paths of the files of a tree, bytecode excluded (no file is read)."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    paths = []
    for src in sorted(folder.rglob("*")):
        rel = src.relative_to(folder)
        if src.is_dir() or "__pycache__" in rel.parts or src.suffix in (".pyc", ".pyo")                 or rel.as_posix() == "MANIFEST.json":
            continue
        paths.append(rel.as_posix())
    return paths


def core_version_of(folder: Path) -> Optional[str]:
    """CORE_VERSION declared by a core tree, or None (absent or unreadable)."""
    try:
        text = (Path(folder) / "hcore" / "__init__.py").read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    match = _CORE_VERSION_LINE.search(text)
    return match.group(1) if match else None


def _version_key(version: str) -> Tuple[int, ...]:
    return tuple(int(x) if x.isdigit() else -1 for x in re.split(r"[.\-]", version))


def is_core_copy(dest_dir: Path) -> bool:
    """A recognized core copy: harness.py and hcore/__init__.py carrying a
    CORE_VERSION line."""
    dest_dir = Path(dest_dir)
    if not all((dest_dir / m).is_file() for m in CORE_MARKERS):
        return False
    return core_version_of(dest_dir) is not None


def check_core_copy(core_dir: Path, root: Path) -> Dict[str, Any]:
    """Strict comparison of <root>/.claude/harness/ with the core (on demand:
    'sf copy-core --check'), byte for byte; a file that is not a core file is
    listed, never read."""
    dest_dir = Path(root) / ".claude" / "harness"
    source, present = _tree(core_dir), set(_tree(dest_dir))
    differs = sorted(r for r in source if r in present
                     and (dest_dir / r).read_bytes() != (Path(core_dir) / r).read_bytes())
    missing = sorted(r for r in source if r not in present)
    extra = sorted(present - set(source))
    return {"identical": not (differs or missing or extra), "recognized": is_core_copy(dest_dir),
            "differs": differs, "missing": missing, "extra": extra}


def write_manifest(core_dir: Path, root: Path, repo: Optional[Dict[str, Any]] = None) -> None:
    """CO-02: MANIFEST.json of the copy (core version, repository version, hashes)."""
    from . import history, version   # local: both read the core tree this module copies
    repo = repo if repo is not None else version.report(Path(core_dir))["repo"]
    text = history.manifest_text(Path(core_dir), repo, history.load(Path(core_dir)))
    _write(Path(root) / ".claude" / "harness" / "MANIFEST.json", text)


def refresh_core(core_dir: Path, root: Path, force: bool = False) -> Dict[str, Any]:
    """Replaces a recognized core copy by the current core: every core file is
    written, byte for byte (created or replaced); a file that is not a core
    file is never touched nor read (reported in 'foreign'). Left as it is
    (refreshed False): an existing .claude/harness/ that is not a recognized
    core copy, and a copy declaring a higher CORE_VERSION than the source
    (downgrade), unless force."""
    core_dir, dest_dir = Path(core_dir), Path(root) / ".claude" / "harness"
    if dest_dir.exists() and not is_core_copy(dest_dir):
        return {"refreshed": False, "files": {}, "foreign": [],
                "reason": "%s is not a recognized core copy" % dest_dir.as_posix()}
    if dest_dir.exists() and not force:
        have, want = core_version_of(dest_dir), core_version_of(core_dir)
        if have is not None and want is not None and _version_key(have) > _version_key(want):
            return {"refreshed": False, "files": {}, "foreign": [],
                    "reason": "the copy declares CORE_VERSION %s, newer than the core %s (downgrade refused; "
                              "--force to replace it anyway)" % (have, want)}
    source, present = _tree(core_dir), set(_tree(dest_dir))
    states: Dict[str, str] = {}
    for rel in source:
        shown = (Path(".claude/harness") / rel).as_posix()
        content = (core_dir / rel).read_bytes()
        if rel not in present:
            states[shown] = CREATED
        elif (dest_dir / rel).read_bytes() == content:
            states[shown] = IDENTICAL
            continue
        else:
            states[shown] = MERGED
        target = dest_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".hcore-tmp")
        tmp.write_bytes(content)
        os.replace(str(tmp), str(target))
    foreign = sorted((Path(".claude/harness") / r).as_posix() for r in present - set(source))
    write_manifest(core_dir, root)
    return {"refreshed": True, "files": states, "foreign": foreign}


def copy_core(core_dir: Path, root: Path) -> Dict[str, Any]:
    """D1: copies _core as is under <root>/.claude/harness/, without bytecode.

    Never a mixed core: if a file already present differs from the source
    (customized core or another version), it stays customized-kept (KEPT)
    and NOTHING is copied, not even the missing files; `copied` is False and
    `blocked_by` lists the files at fault, to be reported to the user."""
    core_dir, dest_dir = Path(core_dir), Path(root) / ".claude" / "harness"
    plan: List[Tuple[str, Path, str, str]] = []
    for src in sorted(core_dir.rglob("*")):
        rel = src.relative_to(core_dir)
        if src.is_dir() or "__pycache__" in rel.parts or src.suffix in (".pyc", ".pyo"):
            continue
        content = src.read_text(encoding="utf-8")
        shown = (Path(".claude/harness") / rel).as_posix()
        plan.append((shown, dest_dir / rel, content, file_state(dest_dir / rel, content)))
    blocked = [shown for shown, _, _, state in plan if state == KEPT]
    if blocked:
        return {"copied": False, "blocked_by": blocked,
                "not_copied": [shown for shown, _, _, state in plan if state == CREATED],
                "files": {shown: state for shown, _, _, state in plan if state != CREATED}}
    for shown, dest, content, state in plan:
        if state == CREATED:
            _write(dest, content)
    write_manifest(core_dir, root)
    return {"copied": True, "blocked_by": [], "not_copied": [],
            "files": {shown: state for shown, _, _, state in plan}}
