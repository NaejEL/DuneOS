# ADR 022 — App ELF section reduction at link time

**Status:** Proposed · 2026-06-06

## Context

DuneOS apps are `.dap` ET_REL objects produced by `ld -r` over the app's object
files (`tools/dbt/builder.py`). App TUs compile with `-ffunction-sections
-fdata-sections` (`CFLAGS_COMMON`), so every function and every static object
lands in its own section (`.text.foo`, `.bss.bar`, plus a `.rela` twin each).
The relocatable link keeps **all** of them — `ld -r` performs no garbage
collection — and the kernel loader (`loader.c`) then maps **every** `SHF_ALLOC`
section into memory: it has no GC either.

Two costs follow, and both surfaced as the multi-file `i2cscope` grew:

1. **Section-count inflation.** A multi-file app reaches the hundreds of
   sections. On 2026-06-06, `i2cscope` hit 584 and tripped the loader's
   `MAX_SECTIONS = 512`. **Stopgap shipped:** the cap was raised to 1024 (the
   bound only sizes `section_bases[]`, calloc'd per load, freed on unload —
   ~4 B/entry, transient). This buys headroom but does not address the cause.
2. **Dead code in the exec pool.** Unused library functions (e.g. `ui_*` /
   `gfx_*` an app never calls) are still linked in and still loaded into the
   shared **64 KB IRAM exec pool** ([[ADR-008]] territory). `-ffunction-sections`
   normally exists to enable `--gc-sections`, but the app link does not GC — so
   today the flag only inflates the section count and buys nothing back.

The exec pool is the scarcest resource on a no-PSRAM CardPuter, so loading dead
`.text` is the more important of the two costs long-term.

## Decision (proposed — measure before committing)

Reduce app section count **and** exec-pool footprint at app link time. The work
is to pick among (and possibly combine) these levers after measuring the real
dead-code weight of a representative app:

- **(A) Drop `-ffunction-sections`/`-fdata-sections` for app builds.** Each TU
  emits one `.text`/`.data`/`.rodata`/`.bss`. Section count collapses to a few
  per TU. Cheapest change; zero loader impact. Cost: granularity for any future
  GC drops to whole-TU. Since the app link does no GC *today*, this is currently
  a pure win (kills inflation, loses nothing real).
- **(B) Real function-level dead-code elimination.** Keep per-function sections
  and make the link drop unreferenced ones from the manifest/entry root. `ld -r`
  does not `--gc-sections`; this needs a partial-link strategy (entry +
  `KEEP`/`-u` roots, or a final-link-then-relocatable two-stage, or a loader that
  resolves on demand). Highest payoff (frees dead `.text` from the exec pool),
  highest complexity/risk.
- **(C) Merge sections post-link with a linker script.** `SECTIONS { .text :
  { *(.text .text.*) } … }` collapses the hundreds of `.text.*` into one per
  class. Fixes the count cheaply and keeps all code (no GC). Middle ground.

**Recommendation:** measure first (`size`/`readelf` on i2cscope: how many bytes
are unreferenced lib functions?). If dead code is negligible, take **(A)** — it
removes the section-count problem outright at zero cost. If dead code is a real
fraction of the exec pool, invest in **(B)**; keep **(C)** as the fallback that
at least solves the count. The `MAX_SECTIONS = 1024` bump stays regardless as
cheap headroom.

## Consequences

- App `.dap` layout changes (fewer, coarser sections). The loader handles merged
  sections already (it keys on section flags, not names), so (A)/(C) need no
  loader change. (B) may need loader cooperation depending on approach.
- Frees IRAM exec-pool budget on no-PSRAM boards — directly extends how large /
  how many apps can be co-resident. Ties into [[ADR-008]] (memory strategy).
- A `dbt`-level decision: the lever lives in `tools/dbt/constants.py`
  (`CFLAGS_COMMON`/`LDFLAGS`) and/or a new app linker script, not in app code.

## Cross-references

- [[ADR-008]] — memory fragmentation / budget strategy (exec pool is the scarce
  resource this optimisation protects).
- [[ADR-009]] — driver/SDK boundary (SDK libs are the main source of
  potentially-dead linked code).
- Loader cap: `kernel/duneos_loader/src/loader.c` `MAX_SECTIONS` (raised
  512→1024 as the 2026-06-06 stopgap).
