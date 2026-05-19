# ADR 000 — ADR process

**Status:** Accepted · 2026-05-19

## Context

DuneOS' roadmap reaches phases where the same question keeps coming back: what kind of error code, which task priority scale, where do paths live, how do memory caps fall back. These choices are cheap to make once and expensive to relitigate every time a contributor (or future-me) lands on a related file. They also leak across phases — an OSAL choice shapes the VFS rewrite, which shapes the first non-ESP-IDF port.

Without a written record, every design decision is implicit and discoverable only by reading the code. That fails at three moments: when a contributor sends a PR that breaks an unspoken rule, when the author returns six months later and forgets the rationale, and when the project is audited and the question is "why this and not that?".

## Decision

Architectural decisions are recorded as short Markdown files in `docs/adr/`, numbered sequentially, using the Michael Nygard format:

```
# ADR NNN — Short title

**Status:** Proposed | Accepted · YYYY-MM-DD | Superseded by ADR-MMM

## Context
Why this question exists. Constraints. What forces collided.

## Decision
The chosen answer, stated in one or two sentences, then the details.

## Consequences
What becomes easier, what becomes harder, what we accept losing.

## Alternatives
Options considered and rejected, with one-line reasons.
```

Conventions:

- **One page max.** If an ADR runs longer, the decision is probably two decisions in disguise — split it.
- **No code blocks longer than 10 lines.** ADRs document *what* and *why*, not the implementation. Cross-link to the relevant file in the codebase.
- **Statuses:** `Proposed` (open for discussion), `Accepted · YYYY-MM-DD` (locked in), `Superseded by ADR-MMM` (keep the file, point to the replacement). Never delete an ADR; replacement preserves history.
- **Numbering is monotonic.** Never renumber. A withdrawn proposal stays as `000-process.md` with status "Withdrawn".
- **Cross-references** use `[[ADR-NNN]]` shorthand in prose.
- **Authoritative for design, not implementation.** Code that diverges from an ADR is a bug — fix the code or write a replacement ADR.

## Consequences

- New contributors have one folder to read to learn "how DuneOS thinks".
- Phase work (especially 26/27/29) consumes ADRs as input — no rediscovery of "why does the kernel use -errno?" mid-implementation.
- Cost of maintaining the index: low (8-10 short docs total for the foreseeable future).

## Alternatives

- **MADR (Markdown Any Decision Records)** — more structured but heavier per file; overkill for a single-maintainer project.
- **Doc strings in headers only** — invisible from the outside, no statuses, no supersession trail.
- **Wiki / GitHub issues** — outside the repo, can't be diffed alongside the code that depends on them.
