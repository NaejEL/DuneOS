# Architecture Decision Records

This directory holds DuneOS' Architecture Decision Records (ADRs) — short, dated documents recording the architectural choices that shape the kernel and tooling. See [ADR 000](000-process.md) for the format and conventions.

## Index

| # | Title | Status | Consumes / consumed by |
|---|---|---|---|
| [000](000-process.md) | ADR process | Accepted 2026-05-19 | — |
| [001](001-error-model.md) | Kernel error model: `int` with POSIX `-errno` | Accepted 2026-05-19 | Phases 26-27 (`task.h`/`supervisor.h`/`vfs.h` migrations), all OSAL/VFS APIs |
| [002](002-osal-api.md) | OSAL API: static-storage handles, minimal surface | Accepted 2026-05-19 | Phase 26 (OSAL implementation), reinforces [008](008-memory-fragmentation.md) |
| [003](003-memory-caps.md) | Memory caps: placement hints with silent DRAM fallback | Accepted 2026-05-19 | Phase 26 (`osal_mem_alloc`), enables [008](008-memory-fragmentation.md) Pillar 4 |
| [004](004-task-priorities.md) | Task priorities: 5 symbolic levels, no runtime change | Accepted 2026-05-19 | Phase 26 (OSAL task creation), Phase 25 (`dbt system` validates `priority` in manifest) |
| [005](005-path-conventions.md) | Path conventions: `/flash` mandatory, `/sd` optional, `/proc` reserved | Accepted 2026-05-19 | Kernel boot, app portability across boards |
| [006](006-manifest-extensibility.md) | Manifest extensibility: unknown fields ignored | Accepted 2026-05-19 | Loader, `dbt build` validation, Phase 25 (`dbt system check`) |
| [007](007-multi-arch-smoke-test.md) | Multi-arch smoke test before Phase 29 | Accepted 2026-05-19 | CI infrastructure, Phases 26-28 portability discipline |
| [008](008-memory-fragmentation.md) | Memory fragmentation strategy: design layer, not allocator | Accepted 2026-05-19 | Phase 20 (TLSF TODO), kernel PR review checklist, future allocation policy |

## Reading order for new contributors

1. [000](000-process.md) — how to read and write these docs.
2. [001](001-error-model.md), [005](005-path-conventions.md), [006](006-manifest-extensibility.md) — surface contracts (errors, paths, manifest).
3. [002](002-osal-api.md), [003](003-memory-caps.md), [004](004-task-priorities.md) — kernel-internal contracts (OSAL).
4. [008](008-memory-fragmentation.md) — the strategy that ties allocation decisions together.
5. [007](007-multi-arch-smoke-test.md) — how we guard against portability regressions.

## Writing a new ADR

1. Pick the next free number (`NNN`).
2. Copy the structure from [000](000-process.md) — Context / Decision / Consequences / Alternatives.
3. One page max; cross-reference existing ADRs with `[NNN](NNN-slug.md)`.
4. Add a row to the index above.
5. Submit as a PR with status `Proposed`; flip to `Accepted · YYYY-MM-DD` once merged.
