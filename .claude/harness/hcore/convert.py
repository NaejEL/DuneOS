# First upgrade of a generated file that holds no harness block (BACKLOG Q6, CV-01 to CV-05).
#
# - CV-01: the foreign blocks (`rgkb`) are removed from the current file and from its
#   BASE (content at the oldest commit that added the file). Classes: pristine
#   (current == base), suffix (current starts with base, the rest is only appended
#   at the end), modified (anything else), untracked (no git, or never committed).
# - CV-02: pristine -> converted: rendered layout, then the foreign blocks as they were.
# - CV-03: suffix -> converted: the suffix of the user is kept byte for byte, outside
#   the blocks, after the harness blocks; the order between suffix and foreign blocks
#   is kept.
# - CV-04: modified | untracked -> gate `upgrade.convert` (default: keep as is);
#   a converted file is rendered, the foreign blocks are carried over.

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

from . import blocks, render
from .proc import Runner

PRISTINE, SUFFIX, MODIFIED, UNTRACKED = "pristine", "suffix", "modified", "untracked"
_PH = ""


def git_base(root: Path, rel: str, run: Optional[Callable[..., Any]] = None) -> Optional[str]:
    """Content of `rel` at the oldest commit that added it, or None (CV-01)."""
    runner = run or Runner(cwd=str(root), timeout=30)
    res = runner(["git", "log", "--diff-filter=A", "--format=%H", "--", rel])
    commits = res.out.split() if res.ok else []
    if not commits:
        return None
    shown = runner(["git", "show", "%s:./%s" % (commits[-1], rel)])
    return shown.out if shown.ok else None


def _split_foreign(text: str) -> Tuple[str, List[str]]:
    """(text with one placeholder per foreign block, the blocks in order)."""
    spans = blocks.foreign_spans(text)
    out, last, found = [], 0, []
    for start, end in spans:
        out.append(text[last:start])
        out.append(_PH)
        found.append(text[start:end])
        last = end
    out.append(text[last:])
    return "".join(out), found


def _restore(text: str, found: List[str]) -> str:
    parts = text.split(_PH)
    out = parts[0]
    for block, tail in zip(found, parts[1:]):
        out += block + tail
    return out


def classify(current: str, base: Optional[str]) -> Tuple[str, Optional[str]]:
    """CV-01. Returns (class, the text that stays after the harness blocks): the foreign
    blocks of the generated region, then what the user appended."""
    if base is None:
        return UNTRACKED, None
    marked, found = _split_foreign(current)
    base_marked, _ = _split_foreign(base)
    if not marked.startswith(base_marked):
        return MODIFIED, None                 # a foreign block is compared by position, not by content
    inside = base_marked.count(_PH)
    tail = marked[len(base_marked):]
    kept = "\n".join(found[:inside])
    rest = kept + ("\n" if kept and tail.replace(_PH, "").strip() else "") + _restore(tail, found[inside:])
    return (PRISTINE if not tail.replace(_PH, "").strip() else SUFFIX), rest


def foreign_text(current: str) -> str:
    return "\n".join(current[s:e] for s, e in blocks.foreign_spans(current))


def converted(layout: render.Layout, current: str, cls: str, rest: Optional[str]) -> str:
    """CV-02, CV-03, CV-04: the file as a layout, the user's text and the foreign blocks kept."""
    text = render.render_new(layout)
    keep = rest if cls in (PRISTINE, SUFFIX) and rest is not None else foreign_text(current)
    keep = keep.lstrip("\r\n")
    return text + ("\n" + keep if keep.strip() else "")
