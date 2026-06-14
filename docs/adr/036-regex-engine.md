# ADR 036 — Vendored tiny regex engine for CLI tools

**Status:** Accepted · 2026-06-13

## Context

`grep`, a minimal `sed`, and `find -name` all need pattern matching. ESP-IDF's
newlib does not reliably ship POSIX `<regex.h>` (`regcomp`/`regexec`), and a full
regex (groups, alternation, backrefs) is large and heap-hungry. We need one small
matcher every text tool can share, with no malloc and a bounded footprint —
consistent with the memory discipline ([[ADR-008]]).

## Decision

A single self-contained backtracking matcher in
[sdk/re/re.c](../../sdk/re/re.c) + [re.h](../../sdk/re/include/duneos/re.h),
pulled in by tools via `sources: [$SDK/re/re.c]` (the standard SDK-source
mechanism; no link/ABI surface).

**Supported** (a useful POSIX-BRE subset): `.`, `^`, `$`, `*`, `+`, `?`,
`[abc]`/`[^abc]`/`[a-z]`, `\d \D \w \W \s \S`, and `\<c>` literal escapes.
**Not supported:** groups `()`, alternation `|`, backreferences, lazy
quantifiers — out of scope for an embedded coreutil; the scripting path
([[ADR-035]], MicroPython) covers anything richer.

No heap: a pattern compiles into a fixed static node array; matching is recursive
with greedy quantifiers + backtracking. One static buffer ⇒ **not reentrant** —
compile-then-match one pattern at a time, which is exactly how the tools use it.

## Consequences

- `grep` / `sed` / `find -name` (glob lowered to regex) share one ~250-line
  matcher; adding a regex tool is a `sources:` line.
- Zero heap, bounded nodes (`MAX_NODES`/`MAX_CLASS_BUF`); a pattern too complex
  fails to compile rather than allocating.
- Not reentrant and single-pattern — fine for the CLI, a constraint to remember
  if anything ever needs concurrent matches (it would copy the compiled program).
- Feature ceiling is deliberate; "I need `(a|b)+` / capture groups" is the signal
  to reach for the scripting interpreter, not to grow this engine.

## Alternatives considered

- **newlib `regcomp`/`regexec`.** Not dependably present in the ESP-IDF newlib,
  and pulls global state + a heavier engine than we need.
- **A full vendored regex (Oniguruma/PCRE/musl).** Tens to hundreds of KiB and
  heap-backed — wrong scale for a no-PSRAM coreutil.
- **Per-tool ad-hoc matching** (`strstr` + manual globbing). Diverges immediately
  and can't express `grep` patterns; the shared engine is the point.

## Cross-references

- [cli-audit-2026-06](../cli-audit-2026-06.md) — Tier-B text tools that consume this.
- [[ADR-034]] — pipelines (where `grep`/`sed` become most useful).
- [[ADR-035]] — MicroPython (the escape hatch for richer pattern/processing needs).
- [[ADR-008]] — memory discipline (why no-heap / bounded).
