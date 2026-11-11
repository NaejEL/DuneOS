# Delimited blocks in a text file (D9: standalone module; used by the model
# block of generated agents, X1).
#
# - BK-01: carries the code-index wiring block of agents and skills, between
#   the literal markers <!-- rgkb:debut --> and <!-- rgkb:fin -->
#   (code-index:123): RGKB, style md, reentrant (one block, replaced in place).
#
# Styles:
#   md   : <!-- name:debut --> ... <!-- name:fin -->
#   hash : # >>> name >>> ... # <<< name <<<
# Insertion is reentrant: an existing block is replaced in place, otherwise it
# is appended at the end of the file; text outside the block is never modified.
# The "debut"/"fin" markers are part of the on-disk format and are kept as is.

from __future__ import annotations

import re
from typing import Optional, Tuple

STYLES = ("md", "hash")
RGKB = "rgkb"


class BlockError(ValueError):
    """Malformed block (opening marker without closing marker, or the reverse)."""


def markers(name: str, style: str) -> Tuple[str, str]:
    if not re.match(r"^[A-Za-z0-9_.-]+$", name):
        raise BlockError("invalid block name: %r" % (name,))
    if style == "md":
        return "<!-- %s:debut -->" % name, "<!-- %s:fin -->" % name
    if style == "hash":
        return "# >>> %s >>>" % name, "# <<< %s <<<" % name
    raise BlockError("unknown style: %r" % (style,))


def _span(text: str, name: str, style: str) -> Optional[Tuple[int, int]]:
    start_m, end_m = markers(name, style)
    start = text.find(start_m)
    end = text.find(end_m)
    if start == -1 and end == -1:
        return None
    if start == -1 or end == -1 or end < start:
        raise BlockError("block %r is malformed" % name)
    if text.find(start_m, start + 1) != -1:
        raise BlockError("block %r is present several times" % name)
    return start, end + len(end_m)


def get_block(text: str, name: str, style: str) -> Optional[str]:
    span = _span(text, name, style)
    if span is None:
        return None
    start_m, end_m = markers(name, style)
    inner = text[span[0] + len(start_m): span[1] - len(end_m)]
    return inner.strip("\n")


def upsert_block(text: str, name: str, content: str, style: str) -> str:
    start_m, end_m = markers(name, style)
    block = "%s\n%s\n%s" % (start_m, content.strip("\n"), end_m)
    span = _span(text, name, style)
    if span is not None:
        return text[: span[0]] + block + text[span[1]:]
    if text and not text.endswith("\n"):
        text += "\n"
    sep = "\n" if text else ""
    return text + sep + block + "\n"


def remove_block(text: str, name: str, style: str) -> str:
    span = _span(text, name, style)
    if span is None:
        return text
    before, after = text[: span[0]], text[span[1]:]
    if after.startswith("\n"):
        after = after[1:]
    if before.endswith("\n\n"):
        before = before[:-1]
    return before + after


# RD-07: the "out of block" part of a file is the text deprived of every harness
# block (`harness.*`, `rgkb`) and of the version header line (EN-02).
HEADER_PREFIX = "# harness: claude-skills "
_MD_BLOCK = re.compile(r"<!-- (harness\.[A-Za-z0-9_.-]+|rgkb):debut -->.*?<!-- \1:fin -->(?:\r?\n)?", re.S)
_HASH_BLOCK = re.compile(r"# >>> (harness\.[A-Za-z0-9_.-]+|rgkb) >>>.*?# <<< \1 <<<(?:\r?\n)?", re.S)
_HEADER_LINE = re.compile(r"^" + re.escape(HEADER_PREFIX) + r"[^\r\n]*(?:\r?\n)?", re.M)
_FOREIGN_BLOCK = re.compile(r"<!-- rgkb:debut -->.*?<!-- rgkb:fin -->", re.S)


def strip_blocks(text: str) -> str:
    """RD-07."""
    text = _MD_BLOCK.sub("", text)
    text = _HASH_BLOCK.sub("", text)
    return _HEADER_LINE.sub("", text)


def has_harness_blocks(text: str) -> bool:
    return bool(re.search(r"<!-- harness\.[A-Za-z0-9_.-]+:debut -->|# >>> harness\.[A-Za-z0-9_.-]+ >>>", text))


LAYOUT_BLOCKS = ("harness.frontmatter", "harness.body", "harness.ci")


def has_layout_blocks(text: str) -> bool:
    """RD-01: True when the file is written in the Phase 3 layout (frontmatter, body or ci block).
    The `harness.model` block (X1) and the `harness.profile.*` block (EP-06) of Phases 1 and 2 do
    not make a layout: such a file still goes through the conversion (MI-04, CV)."""
    return any(name in LAYOUT_BLOCKS for name in harness_block_names(text))


def foreign_spans(text: str) -> list:
    """(start, end) of the `rgkb` blocks of a text (blocks owned by another skill)."""
    return [m.span() for m in _FOREIGN_BLOCK.finditer(text)]


def harness_block_names(text: str) -> list:
    names = re.findall(r"<!-- (harness\.[A-Za-z0-9_.-]+):debut -->", text)
    names += re.findall(r"# >>> (harness\.[A-Za-z0-9_.-]+) >>>", text)
    return names


def upsert_rgkb(text: str, content: str) -> str:
    """BK-01: inserts or replaces the <!-- rgkb:debut --> ... <!-- rgkb:fin --> block."""
    return upsert_block(text, RGKB, content, "md")
