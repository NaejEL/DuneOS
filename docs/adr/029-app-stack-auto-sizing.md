# ADR 029 — Computed app stacks: dbt sizes the stack, not the manifest

**Status:** Accepted · 2026-06-08

## Context

Each app reserves a fixed task stack at launch (real DRAM, whole, not lazy —
[[ADR-025]]). The size came from `stack_size` in the app's manifest. Measured on
the CardPuter, **>2/3 of every reserved stack was unused**:

| app | reserved | peak used (`ps`) |
|---|---|---|
| usb_shell | 16384 | 4032 |
| wifi_daemon | 8192 | 2656 |
| kb_iomatrix | 4096 | 1136 |
| battery_pub | 4096 | 1232 |

Trusting the manifest is structurally wrong: a dev who hits any bug just bumps
`stack_size` "to be safe" and never lowers it. On a 320 KiB board that waste is
unaffordable — the stacks alone leaked ~23 KiB.

## Decision

**dbt computes each app's stack at build time from the compiler's own
measurements; the manifest no longer declares it.** `stack_size` is removed from
app manifests and becomes an optional, *visible* override for the rare justified
case.

### How the size is computed

The app + SDK sources compile with `-fstack-usage -fcallgraph-info=su`, which
emit per-function frame sizes (`.su`) and the call graph (`.ci`).
`tools/dbt/stackusage.py` merges them and walks the graph from `app_main`, taking
the deepest path = the app's own worst-case stack. Then:

```
stack = align16( (own_worst_case + RESERVE) * (1 + MARGIN) )    # floor STACK_FLOOR
RESERVE = 3072   MARGIN = 0.20   FLOOR = 3072
```

dbt writes the result into the embedded manifest (`.duneos_manifest`), so the
supervisor allocates exactly that.

### Why the RESERVE (the honest limitation)

Static analysis cannot see depth reached through **function pointers**: the
DuneOS kernel ABI (function-table calls that run on the app's stack) and the SDK
ops tables (libgfx→libdisp→spi). Cross-validating own-worst-case against the
runtime high-water (`ps`) showed this hidden depth is ~1–2 KiB across apps, so
`RESERVE = 3072` covers it. The **FreeRTOS stack canary + supervisor kill**
([[ADR-025]], Phase 20) are the backstop: a rare under-estimate kills *that app*
cleanly, never the kernel — the same safety net the heap will use for
over-limit kills under the future sandboxing work.

### The override (and when it is legitimate)

`stack_size` in a manifest still works and wins when larger than computed, but
the build **prints `computed X, manifest overrides to Y (Z% needed)`** so an
inflated value is visible in review, never silent. The legitimate case is an app
that **hosts another app's code on its own stack**: a shell that runs *captured*
bin apps (`ps`/`ls`/`cat`) in its own task (ADR 016) plus the loader — depth no
analysis of the shell alone can see. **All three shells** (`usb_shell`,
`g_shell`, `uart_shell`, sharing `shell_core`'s `duneos_loader_run_captured`)
keep an 8192 override; everything else is auto-sized. Apps that *spawn* children
(the launcher → `duneos_supervisor_launch`) do not need it — the child gets its
own auto-sized task stack.

## Consequences

- ~23 KiB of stack waste reclaimed (e.g. usb_shell 16384→8192, wifi 8192→4464)
  with **no app code change** — purely the build. Frees contiguous DRAM, which is
  what a fragmented no-PSRAM heap actually needs ([[ADR-008]]).
- Devs cannot silently over-reserve; the default is the computed minimum and any
  override is reported.
- Every app must be rebuilt for its embedded manifest to carry the new stack;
  boot daemons live in the sysbin, so the saving lands after a sysbin reflash.
- Recursion (graph cycle) or no `app_main` → dbt can't size safely and keeps the
  manual value / a safe default, with a printed reason.
- Heap is the next target but harder (can't be computed statically). Per the
  plan it stays a per-app reserved pool for now and moves to a shared pool with
  over-limit kill when sandboxing lands — out of scope here.

## Alternatives considered

- **Trust the manifest (status quo).** Rejected: structurally encourages waste.
- **Runtime-measure then hand-tune** (`ps` high-water → set manifest). Needs full
  path coverage to be safe and still trusts a human to lower it. The static
  number needs no execution and is the floor; `ps` is the cross-check, not the
  source.
- **Full precision incl. kernel ABI depth.** Analyse the kernel's own `.su`/`.ci`
  for the deepest ABI chain (`loader.load`→FatFS→SPI) and add exactly that per
  app. Worth doing later to shrink RESERVE; the empirical 3 KiB + canary is
  enough now.

## Cross-references

- [[ADR-025]] — RAM-limited concurrency + the stack canary safety net.
- [[ADR-008]] — memory fragmentation strategy (reclaimed stack = contiguous DRAM).
- [[ADR-006]] — manifest extensibility (`stack_size` now optional/override).
- ADR 016 — captured-app exit: why shells host child code on their own stack.
