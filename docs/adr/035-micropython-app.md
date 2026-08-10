# ADR 035 — MicroPython as a PSRAM-gated `.dap` app

**Status:** Proposed · 2026-06-13

## Context

DuneOS' shell is a command dispatcher, and the CLI roadmap
([cli-audit-2026-06](../cli-audit-2026-06.md)) stops short of a real scripting
language: a bespoke `awk` is a full interpreter (lexer/parser/arrays/regex,
~25-30 KiB golfed) that competes for the same scarce SRAM as everything else.
The recurring question — "can we embed a real interpreter?" — points at
MicroPython, which already has a mature ESP32 port.

The blocker is memory. The MicroPython ESP32 firmware is **~600 KiB–1.5 MiB of
flash** (VM + runtime + port + frozen modules) and wants a **GC heap**: minimal
usable ~32–64 KiB, comfortable ≥128 KiB. On the no-PSRAM CardPuter (~320 KiB
total SRAM, ~50–80 KiB free at the shell) that heap can't fit alongside DuneOS.
On a PSRAM board (the DevKitC reference target, any S3 with 2–8 MiB) the GC heap
lives in PSRAM — exactly where MicroPython's port puts it by default.

## Decision (proposed)

**Ship MicroPython as a normal DuneOS `.dap` app, not as the shell, gated to
PSRAM boards.** No kernel change — it is just a large app.

- The app declares a large `heap_size` served from the PSRAM app arena
  ([[ADR-025]]/[[ADR-031]] arena). On PSRAM boards the arena (and per-app pool)
  can be sized for it.
- The **capability system** ([[ADR-014]]) gates it: a `requires: [psram]`
  capability (or a board-profile include rule) keeps `micropython.dap` out of
  the flash/SD set on no-PSRAM boards, so it never appears in the launcher there.
- DuneOS is exposed to scripts as a Python C-module: a `machine`/`os`-flavoured
  shim over `/dev` and the syscall ABI (`open`/`read`/`write`/`ioctl`,
  `/dev/i2c-*`, `/dev/gpiochip0`, sockets). This is the same "userspace speaks
  the device protocol" model the rest of DuneOS uses ([[ADR-019]]).
- It **replaces the need for `awk`**: scripting belongs in Python, not a bespoke
  mini-language. `grep`/`sed`/`find` (small, regex-based) still ship for the
  common cases on every board.

## Consequences

- A real, batteries-included scripting language on PSRAM boards, with zero kernel
  surface change — the app model ([[ADR-025]]) carries it.
- The board-capability gate keeps the no-PSRAM contest image unaffected; the tool
  simply isn't present there.
- The `.dap`/heap sizing and the PSRAM-arena placement need validation — this is
  by far the largest single app DuneOS would load, and exercises the arena and
  the loader's exec-pool budget ([[ADR-008]]) harder than anything to date.
- Building it means vendoring the MicroPython source as a DuneOS app target (its
  build is non-trivial; likely a dedicated `apps/user/micropython/` with a custom
  source list rather than the usual single-`.c` app) — a real chunk of work,
  hence Proposed/backlog, not scheduled.

## Alternatives considered

- **A bespoke `awk`.** Rejected as the *general* scripting answer: still an
  interpreter to write and maintain, less capable than Python, same RAM cost
  class. A minimal `sed`/`grep` covers the non-scripting common cases.
- **Extend the shell into a scripting language** (variables, control flow). Worth
  doing for light glue (separate roadmap item) but it is not a substitute for a
  real language with data structures — and job control needs processes we don't
  have.
- **Run MicroPython on no-PSRAM boards with a tiny heap.** Rejected: ~32 KiB GC
  heap on a board with ~50–80 KiB free starves the rest of the system; cramped to
  unusable.
- **MicroPython *as* the shell.** Rejected: couples the always-present shell to a
  PSRAM-only, heavy runtime; the dispatcher shell must run on every board.

## Cross-references

- [[ADR-014]] — capability resolution (the PSRAM gate).
- [[ADR-025]] — RAM-limited app concurrency / arena (where the GC heap lives).
- [[ADR-019]] — Linux-faithful `/dev` semantics (the Python `machine` shim shape).
- [cli-audit-2026-06](../cli-audit-2026-06.md) — why scripting, and what `grep`/
  `sed`/`find` cover instead on every board.
