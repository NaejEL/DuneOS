# Reading and completing JSON files as VS Code and Windows tools write them
# (SF-01, SF-02):
#
# - UTF-8 BOM accepted (and kept on rewrite);
# - JSONC: // and /* */ comments outside strings, trailing commas.
#
# A JSONC file (comments or trailing commas) is never re-serialized: the
# merge only inserts the text of the added keys and items, at the end of the
# object or array concerned, with the file's indentation and line ending.
# Existing comments, formatting and order are kept.

from __future__ import annotations

import json
import re
from json.decoder import scanstring
from typing import Any, Dict, List, Optional, Tuple

BOM = "﻿"
_LITERAL = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null")


class JsoncError(ValueError):
    """Invalid JSON/JSONC text."""


class Node:
    """Parsed value with its position: start/end (end exclusive) in the text."""

    def __init__(self, kind: str, start: int):
        self.kind = kind          # "object", "array" or "value"
        self.start = start
        self.end = start
        self.lead = start         # start of the entry (the key for an object member)
        self.value: Any = None
        self.members: List[Tuple[str, "Node"]] = []   # object
        self.items: List["Node"] = []                  # array


class _Parser:
    def __init__(self, text: str):
        self.text = text
        self.loose = False   # comment or trailing comma encountered

    def fail(self, pos: int, what: str) -> JsoncError:
        line = self.text.count("\n", 0, pos) + 1
        return JsoncError("%s (line %d)" % (what, line))

    def skip(self, i: int) -> int:
        t = self.text
        while i < len(t):
            c = t[i]
            if c in " \t\r\n":
                i += 1
            elif t.startswith("//", i):
                self.loose = True
                j = t.find("\n", i)
                i = len(t) if j < 0 else j + 1
            elif t.startswith("/*", i):
                self.loose = True
                j = t.find("*/", i + 2)
                if j < 0:
                    raise self.fail(i, "unterminated /* comment")
                i = j + 2
            else:
                break
        return i

    def value(self, i: int) -> Node:
        i = self.skip(i)
        t = self.text
        if i >= len(t):
            raise self.fail(i, "value expected, end of text")
        c = t[i]
        if c == "{":
            return self.container(i, "object", "}")
        if c == "[":
            return self.container(i, "array", "]")
        node = Node("value", i)
        if c == '"':
            try:
                node.value, node.end = scanstring(t, i + 1)
            except ValueError as exc:
                raise self.fail(i, "invalid string: %s" % exc)
            return node
        m = _LITERAL.match(t, i)
        if not m:
            raise self.fail(i, "unexpected value %r" % t[i:i + 10])
        node.value, node.end = json.loads(m.group(0)), m.end()
        return node

    def container(self, i: int, kind: str, close: str) -> Node:
        node = Node(kind, i)
        result: Any = {} if kind == "object" else []
        i = self.skip(i + 1)
        t = self.text
        while True:
            if i < len(t) and t[i] == close:
                node.end, node.value = i + 1, result
                return node
            if kind == "object":
                if i >= len(t) or t[i] != '"':
                    raise self.fail(i, "key expected")
                key_start = i
                key, i = scanstring(t, i + 1)
                i = self.skip(i)
                if i >= len(t) or t[i] != ":":
                    raise self.fail(i, "':' expected")
                child = self.value(i + 1)
                child.lead = key_start
                node.members.append((key, child))
                result[key] = child.value
            else:
                child = self.value(i)
                node.items.append(child)
                result.append(child.value)
            i = self.skip(child.end)
            if i < len(t) and t[i] == ",":
                i = self.skip(i + 1)
                if i < len(t) and t[i] == close:
                    self.loose = True
            elif i >= len(t) or t[i] != close:
                raise self.fail(i, "',' or '%s' expected" % close)


class Document:
    """JSON/JSONC file read: value, positioned tree, BOM, line ending."""

    def __init__(self, raw: str):
        self.bom = raw.startswith(BOM)
        self.text = raw[1:] if self.bom else raw
        parser = _Parser(self.text)
        self.root = parser.value(0)
        end = parser.skip(self.root.end)
        if end != len(self.text):
            raise parser.fail(end, "unexpected content after the value")
        self.loose = parser.loose
        self.value = self.root.value
        self.newline = "\r\n" if "\r\n" in self.text else "\n"

    def patched(self, merged: Any) -> str:
        """Full text (BOM included) where `merged` is obtained by additions only."""
        inserts: List[Tuple[int, str]] = []
        self._collect(self.root, merged, inserts)
        text = self.text
        for pos, chunk in sorted(inserts, key=lambda x: x[0], reverse=True):
            text = text[:pos] + chunk + text[pos:]
        return (BOM if self.bom else "") + text

    # -- internal

    def _unit(self) -> str:
        m = re.search(r"\n([ \t]+)\S", self.text)
        if not m:
            return "  "
        return "\t" if m.group(1).startswith("\t") else m.group(1)

    def _line_indent(self, pos: int) -> str:
        start = self.text.rfind("\n", 0, pos) + 1
        m = re.match(r"[ \t]*", self.text[start:])
        return m.group(0) if m else ""

    def _collect(self, node: Node, merged: Any, out: List[Tuple[int, str]]) -> None:
        if node.kind == "object" and isinstance(merged, dict):
            present = dict(node.members)
            for key, child in node.members:
                if key in merged:
                    self._collect(child, merged[key], out)
            extra = [(k, v) for k, v in merged.items() if k not in present]
            if extra:
                self._append(node, [json.dumps(k, ensure_ascii=False) + ": " for k, _ in extra],
                             [v for _, v in extra], node.members[-1][1] if node.members else None, out)
        elif node.kind == "array" and isinstance(merged, list):
            if merged[: len(node.items)] != node.value:
                raise JsoncError("JSONC merge: the existing array is not a prefix of the result")
            for child, value in zip(node.items, merged):
                self._collect(child, value, out)
            extra = merged[len(node.items):]
            if extra:
                self._append(node, [""] * len(extra), extra,
                             node.items[-1] if node.items else None, out)
        elif node.value != merged:
            raise JsoncError("JSONC merge: only adding keys or items is supported")

    def _append(self, node: Node, prefixes: List[str], values: List[Any], last: Optional[Node],
                out: List[Tuple[int, str]]) -> None:
        nl, unit = self.newline, self._unit()
        outer = self._line_indent(node.start)
        indent = outer + unit
        if last is not None:
            before = self.text[self.text.rfind("\n", 0, last.lead) + 1:last.lead]
            if not before.strip():
                indent = before   # the last item opens its line: same indentation
        rendered = []
        for prefix, value in zip(prefixes, values):
            body = json.dumps(value, ensure_ascii=False, indent=unit)
            body = body.replace("\n", nl + indent)
            rendered.append(indent + prefix + body)
        joined = ("," + nl).join(rendered)
        tail = self._comment_tail(last.end) if last is not None else None
        if tail is not None:
            # end-of-line comment: it stays with the item it followed
            eol, has_comma = tail
            if not has_comma:
                out.append((last.end, ","))
            out.append((eol, nl + joined))
        elif last is not None:
            out.append((last.end, "," + nl + joined))
        else:
            out.append((node.start + 1, nl + joined + nl + outer))

    def _comment_tail(self, pos: int) -> Optional[Tuple[int, bool]]:
        """If the rest of the line after `pos` holds only blanks, at most one
        comma and at least one comment, returns (end of line, comma present)."""
        t = self.text
        eol = t.find("\n", pos)
        if eol < 0:
            return None
        if eol > pos and t[eol - 1] == "\r":
            eol -= 1
        i, comma, comment = pos, False, False
        while i < eol:
            if t[i] in " \t":
                i += 1
            elif t[i] == "," and not comma:
                comma, i = True, i + 1
            elif t.startswith("//", i):
                comment, i = True, eol
            elif t.startswith("/*", i):
                j = t.find("*/", i + 2)
                if j < 0 or j + 2 > eol:
                    return None
                comment, i = True, j + 2
            else:
                return None
        return (eol, comma) if comment else None


def loads(raw: str) -> Document:
    """Parses a JSON or JSONC text (BOM accepted)."""
    return Document(raw)
