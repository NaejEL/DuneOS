# Deterministic rendering of the generated files (Phase 3).
#
# - RD-01: a generated file is written as: version header (EN), harness blocks,
#   free zones outside the blocks. Blocks: `harness.frontmatter` (hash style, inside
#   the YAML frontmatter), `harness.body` and `harness.profile.<p>` (md style),
#   `harness.ci` (hash style, scripts).
# - RD-02: the content of a block is a pure function of (fragment, params,
#   contributions): same inputs, same bytes (LF). No call to the LLM.
# - RD-05: the `tools:` list of an agent and the --allowedTools of a script are the
#   union of the tools of the profile and of the contributions of the stamp.
# - RD-08: an agent that runs commands (`Bash` in its tools) also gets the tools
#   named by the `extra_tools` parameter (`PowerShell` for `PowerShell(idf.py *)`):
#   the commands the project declares must be runnable by the Verifier too.
# - RD-06: the `rgkb` blocks of code-index are templates parameterized by the
#   interpreter and the agent name.
# - EN-01 to EN-04: one English header line, no date (idempotence), none in strict
#   JSON nor in copied files.

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Sequence, Tuple

from . import blocks, lab, profiles

FRONTMATTER = "harness.frontmatter"
BODY = "harness.body"
CI = "harness.ci"
PLACEHOLDER = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)(?:\|([^}]*))?\}\}")
ALLOWED_MARK = "@ALLOWED@"
DETECTED = ("stacks", "interpreter")


class RenderError(ValueError):
    """A fragment needs a parameter that was not provided."""


class Layout(NamedTuple):
    kind: str                                  # md | sh | ps1
    header: str
    blocks: Tuple[Tuple[str, str, str], ...]   # (name, style, content)


def header(describe: str, core_version: str) -> str:
    """EN-01, EN-04."""
    return "%s%s (core %s)" % (blocks.HEADER_PREFIX, describe, core_version)


def rgkb_header(describe: str, tool_version: str) -> str:
    return "<!-- rgkb: claude-skills %s (rgkb %s) -->" % (describe, tool_version)


def substitute(text: str, params: Dict[str, Any]) -> str:
    """RD-02: `{{name}}` or `{{name|default}}` replaced by a parameter."""
    def one(match: "re.Match[str]") -> str:
        value = params.get(match.group(1))
        if value in (None, "", []):
            if match.group(2) is None:
                raise RenderError("parameter %r is required by a fragment" % match.group(1))
            return match.group(2)
        return ", ".join(str(v) for v in value) if isinstance(value, (list, tuple)) else str(value)
    return PLACEHOLDER.sub(one, text)


def union(first: Iterable[str], more: Iterable[str]) -> List[str]:
    out: List[str] = []
    for item in list(first) + list(more):
        item = item.strip()
        if item and item not in out:
            out.append(item)
    return out


def _yaml(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _extra_tools(params: Optional[Dict[str, Any]]) -> List[str]:
    more = (params or {}).get("extra_tools") or []
    return [t.strip() for t in (more.split(",") if isinstance(more, str) else more) if t.strip()]


def shell_tools(role_tools: Sequence[str], params: Optional[Dict[str, Any]]) -> List[str]:
    """RD-08: tool names of `extra_tools` for an agent that has `Bash`, in declaration order."""
    if "Bash" not in [t.strip() for t in role_tools]:
        return []
    return union([t.split("(", 1)[0].strip() for t in _extra_tools(params)], [])


def agent_frontmatter(role: Dict[str, Any], model: str, contributions: Sequence[str],
                      params: Optional[Dict[str, Any]] = None) -> str:
    lines = ["name: %s" % role["name"], "description: %s" % _yaml(role.get("description") or role["name"])]
    if role.get("tools"):
        base = [t for t in role["tools"].split(",")]
        tools = union(union(base, shell_tools(base, params)), contributions)
        lines.append("tools: %s" % ", ".join(tools))
    lines.append("model: %s" % model)
    return "\n".join(lines)


def skill_frontmatter(orch: Dict[str, Any]) -> str:
    lines = ["name: %s" % orch["name"], "description: %s" % _yaml(orch.get("description") or orch["name"])]
    lines.append("disable-model-invocation: true")
    if orch.get("argument_hint"):
        lines.append("argument-hint: %s" % _yaml(orch["argument_hint"]))
    return "\n".join(lines)


def interpreter_rule(interpreter: Optional[str]) -> List[str]:
    return ["Bash(%s *)" % interpreter.split()[0]] if interpreter and interpreter.split() else []


def allowed_tools(params: Dict[str, Any], contributions: Sequence[str]) -> str:
    """RD-05 for the ci/* scripts: base, interpreter, real commands, extra tools, contributions."""
    extra = interpreter_rule(params.get("interpreter"))
    for key in ("build_command", "test_command", "lint_command"):
        words = str(params.get(key) or "").split()
        if words:
            extra.append("Bash(%s *)" % words[0])
    extra += _extra_tools(params)
    return lab.allow_list(union(extra, contributions))


def _ci_layouts(prof: Dict[str, Any], hdr: str, allowed: str, templates: Path) -> Dict[str, Layout]:
    out: Dict[str, Layout] = {}
    script = prof.get("ci", {}).get("script")
    if not script:
        return out
    for ext, kind in (("sh", "sh"), ("ps1", "ps1")):
        src = templates / ("%s-ci.%s" % (script, ext))
        if not src.is_file():
            continue
        text = src.read_text(encoding="utf-8").replace("\r\n", "\n").replace(ALLOWED_MARK, allowed)
        if kind == "sh" and text.startswith("#!"):
            text = text.split("\n", 1)[1]
        out["ci/%s.%s" % (script, ext)] = Layout(kind, hdr, ((CI, "hash", text.strip("\n")),))
    return out


def mission_parts(role: Dict[str, Any], text: str) -> Tuple[str, str]:
    """(description, body) of a role without explicit `body`: drawn from its mission fragment
    `**`name`** (...; *description*). instructions`."""
    match = re.search(r"\*([^*]+)\*\)\.\s*", text)
    if not match:
        return role["name"], text
    return match.group(1).strip(), "Tu es `%s`. %s" % (role["name"], text[match.end():].strip())


def orchestrator_parts(text: str) -> Tuple[Optional[str], Optional[str], str]:
    """(description, argument-hint, body) of an orchestrator fragment that still holds its frontmatter line."""
    desc = re.search(r"`description: (.*?)` ;", text)
    hint = re.search(r'`argument-hint: "(.*?)"`', text)
    lines = text.split("\n")
    body = "\n".join(lines[2:]).strip() if len(lines) > 2 else text
    return (desc.group(1) if desc else None,
            hint.group(1).replace('\\"', '"') if hint else None, body)


def profile_layouts(prof: Dict[str, Any], params: Dict[str, Any], contributions: Sequence[str],
                    models_by_role: Dict[str, str], hdr: str,
                    templates: Optional[Path] = None) -> Dict[str, Layout]:
    """RD-01, RD-02: layout of every generated file of a profile, by relative path."""
    templates = templates or lab.TEMPLATES
    out: Dict[str, Layout] = {}
    for role in prof["roles"]:
        if role.get("body"):
            body = profiles.fragment(prof, role["body"])
        else:
            derived, body = mission_parts(role, profiles.fragment(prof, role["mission"]))
            role = dict(role, description=role.get("description") or derived)
        body = substitute(body, params)
        fm = agent_frontmatter(role, models_by_role.get(role["model_role"], role.get("model_default", "inherit")),
                               contributions, params)
        out[role["agent_file"]] = Layout("md", hdr, ((FRONTMATTER, "hash", fm.strip("\n")),
                                                      (BODY, "md", body.strip("\n"))))
    orch = dict(prof["orchestrator"])
    if prof["fragments"].get("orchestrator_body"):
        body = profiles.fragment(prof, prof["fragments"]["orchestrator_body"])
    else:
        d, h, body = orchestrator_parts(profiles.fragment(prof, prof["fragments"]["orchestrator"]))
        orch["description"] = orch.get("description") or d
        orch["argument_hint"] = orch.get("argument_hint") or h
    body = substitute(body, params)
    out[".claude/skills/%s/SKILL.md" % orch["name"]] = Layout(
        "md", hdr, ((FRONTMATTER, "hash", skill_frontmatter(orch).strip("\n")),
                    ("harness.profile.%s" % prof["name"], "md", body.strip("\n"))))
    out.update(_ci_layouts(prof, hdr, allowed_tools(params, contributions), templates))
    return out


# ----------------------------------------------------------- file assembly

def render_new(layout: Layout) -> str:
    """A generated file that does not exist yet."""
    parts: List[str] = []
    if layout.kind == "md":
        head = "---\n%s\n" % layout.header
        fm = [b for b in layout.blocks if b[0] == FRONTMATTER]
        for name, style, content in fm:
            start, end = blocks.markers(name, style)
            head += "%s\n%s\n%s\n" % (start, content, end)
        head += "---\n"
        parts = [head] + [_block(*b) for b in layout.blocks if b[0] != FRONTMATTER]
        return "\n".join(parts)
    first = "#!/usr/bin/env bash\n" if layout.kind == "sh" else ""
    return first + layout.header + "\n" + "\n".join(_block(*b) for b in layout.blocks)


def _block(name: str, style: str, content: str) -> str:
    start, end = blocks.markers(name, style)
    return "%s\n%s\n%s\n" % (start, content, end)


def _set_header(text: str, kind: str, hdr: str) -> str:
    match = re.search(r"^" + re.escape(blocks.HEADER_PREFIX) + r"[^\r\n]*", text, re.M)
    if match:
        return text[:match.start()] + hdr + text[match.end():]
    lines = text.split("\n")
    index = 0
    if kind == "md" and lines and lines[0].rstrip("\r") == "---":
        index = 1
    elif kind == "sh" and lines and lines[0].startswith("#!"):
        index = 1
    lines.insert(index, hdr)
    return "\n".join(lines)


def _insert_frontmatter_block(text: str, content: str) -> str:
    lines = text.split("\n")
    if not lines or lines[0].rstrip("\r") != "---":
        raise blocks.BlockError("no YAML frontmatter to hold the harness.frontmatter block")
    close = next((i for i in range(1, len(lines)) if lines[i].rstrip("\r") == "---"), None)
    if close is None:
        raise blocks.BlockError("YAML frontmatter is not closed")
    start, end = blocks.markers(FRONTMATTER, "hash")
    lines[close:close] = [start] + content.split("\n") + [end]
    return "\n".join(lines)


def update(current: str, layout: Layout) -> str:
    """The blocks and the header of an existing generated file, replaced in place;
    the text outside them is never modified (UP-03)."""
    text = current
    for name, style, content in layout.blocks:
        if name == FRONTMATTER and blocks.get_block(text, name, style) is None:
            text = _insert_frontmatter_block(text, content)
        else:
            text = blocks.upsert_block(text, name, content, style)
    return _set_header(text, layout.kind, layout.header)


def block_hashes(text: str, layout: Layout) -> Dict[str, str]:
    """sha256 of the content of each block of a layout present in `text` (HJ-10)."""
    import hashlib
    out: Dict[str, str] = {}
    for name, style, _content in layout.blocks:
        inner = blocks.get_block(text, name, style)
        if inner is not None:
            out[name] = hashlib.sha256(inner.encode("utf-8")).hexdigest()
    return out


# -------------------------------------------------------- rgkb blocks (RD-06)

RGKB_KINDS = {"read-only": "read-only.md", "builder": "builder.md", "orchestrator": "orchestrator.md"}


def rgkb_block(template: str, agent: str, interpreter: str, describe: str, tool_version: str) -> str:
    """Content of a `rgkb` block: header line, then the template rendered."""
    body = substitute(template, {"interpreter": interpreter, "agent": agent})
    return rgkb_header(describe, tool_version) + "\n" + body.strip("\n")
