Status: APPROVED

# SPEC-leg-29 — Make the loader's exec path reachable under QEMU (IRAM/DRAM alias)

## Context

Blocker 2 of SPEC-leg-27's QEMU bench, found on 2026-09-03 with blocker 1 (SPEC-leg-28) patched
out, and confirmed bidirectionally with GDB against the running emulator.

On ESP32-S3 silicon, internal SRAM is visible through two windows onto **one** memory: a DRAM
view (`0x3fc…`, byte-accessible) and an IRAM view (`0x40…`, instruction fetch), a fixed
`0x6F0000` apart. The loader relies on that: it writes an application's text through the DRAM
alias — the only way to do it on hardware — and executes at the IRAM address.

`qemu-xtensa` (`esp_develop_9.2.2_20250817`, machine `esp32s3`) does not alias them. Proof, from
GDB against the live machine: writing `0x12345678` at `0x3fca3454` leaves `0x40393454` at zero,
and writing `0xdeadbeef` at `0x40393460` leaves `0x3fca3460` unchanged. Two separate memories,
not two views of one SRAM, in both directions. Consequently the loaded `.dap` executes zeroes
and dies immediately: `cause=0` (illegal instruction) at `PC=0x40393490`, four words into its own
text.

This is a known emulator defect, not a DuneOS one: Espressif's own DIRAM aliasing tests carry
the Unity tag `[heap][qemu-ignore]`, i.e. they exclude the property rather than test it.

Consequence: SPEC-leg-27's acceptance criterion 6 — proving a `.dap` was loaded **and its own
code ran** — cannot go green under this emulator as things stand, and the `qemu-smoke` CI job
stays red even once SPEC-leg-28 lands.

### What must not be re-attempted

Changing the loader's **hardware** write path to write at the IRAM address instead of the DRAM
alias was tested as a throwaway experiment. It makes the `.dap` run correctly under QEMU, and it
is **incorrect on real silicon**. Keep this on the record so nobody rediscovers it as a fix:

- IRAM is 32-bit-access-only (`mem_alloc.rst:126`); the `SOC_BYTE_ACCESSIBLE_*` range covers the
  DRAM view only.
- The loader performs unaligned **byte** stores into the instruction stream while relocating
  (`kernel/duneos_loader/src/loader.c`, `apply_slot0_op()` at `:535`, called from `:916`) and
  reads sections straight into the exec block (`:449`). Both raise `LoadStoreError` through the
  IRAM window on hardware.

  *Line numbers refreshed 2026-09-04 against the post-SPEC-leg-25 tree (ELF parser extracted
  into `elf_parse.c`). The `:520-540` / `:429-430` originally cited here were pre-extraction.*

Option 1 below is *not* that change: it compiles the IRAM path only into boards that never run
on silicon.

## Scope

Decide between the three options below and implement the chosen one. This spec deliberately
records all three rather than pre-committing: the trade-off is a product decision, not a
technical one.

> **Decision (2026-09-04): option 1**, approved by the product owner and implemented.
> `boards/esp32s3-qemu*/board.yaml` declare `qemu: true`; `tools/duneos-bspgen.py` emits
> `CONFIG_DUNEOS_TARGET_QEMU=y` for those boards only; `kernel/duneos_loader/Kconfig` declares
> the symbol; `loader.c` selects `s_build_scratch = iram` under it and keeps
> `iram_word_dram_alias()` otherwise. Rationale and the two-divergent-write-paths cost are
> recorded in `docs/qemu-test-bench.md` (blocker 2) and ADR 039. Option 2 stays the preferred
> end state and would let the guard be deleted; option 3 was rejected. Options 1 and 2 are not
> exclusive — the guard is the unblocker, not a reason to stop wanting the upstream fix.

### Option 1 (recommended) — board-scoped compile-time guard

`boards/esp32s3-qemu*/board.yaml` declares an emulation flag (for example `qemu: true`);
`tools/duneos-bspgen.py` emits `CONFIG_DUNEOS_TARGET_QEMU=y`; `loader.c` selects
`s_build_scratch = iram` under that symbol and keeps the DRAM alias otherwise.

Properties, stated precisely:

- **Real boards never define the symbol, so the hardware write path is literally unchanged** —
  not "probably safe", unchanged. The unaligned byte stores that would raise `LoadStoreError` on
  silicon only ever compile into a binary that never runs on silicon.
- There is existing precedent for conditional compilation of exactly this kind in exactly this
  file: `#ifdef CONFIG_SPIRAM` already selects section placement (CLAUDE.md, "PSRAM
  auto-detection").
- It follows the established `board.yaml` declaration → bspgen symbol → conditional compilation
  model rather than introducing a new mechanism.
- **The coverage argument, which is the strongest point.** The only thing the guard gives up is
  proving that writes through the DRAM alias land at the IRAM view — and QEMU *cannot test that
  anyway*, because it does not implement the alias. So the real choice is not "full coverage vs
  degraded coverage"; it is **zero coverage (nothing runs at all)** vs **everything covered
  except a property the emulator is incapable of checking**. ELF parsing, relocation arithmetic,
  symbol resolution, scan, select, launch, VFS and mount are identical in both builds.
- **The honest cost**: two divergent write paths. If someone later breaks the alias arithmetic,
  the bench stays green. That is the standard test-double trade-off; it is bounded, and the
  guard itself must carry a comment saying so.

### Option 2 — fix it upstream in Espressif's QEMU fork

Add `memory_region_init_alias()` for the ESP32-S3 IRAM/DRAM windows. The correct fix in
principle: it benefits every ESP-IDF user, and it touches no DuneOS code — the loader keeps one
write path and the bench proves the real one.

Cost: not under our control. Espressif's `[heap][qemu-ignore]` tags say they know about the gap
and have chosen exclusion over a fix, so a patch may sit unmerged indefinitely, and the bench
stays red meanwhile. The minimal bidirectional GDB reproduction from the Context section already
exists and is ready to file.

### Option 3 — do nothing

The loader's exec path stays untestable under emulation. Cost to record: SPEC-leg-27 criterion 6
never goes green; the `qemu-smoke` job proves boot, mount and scan but not execution; every
loader change stays validated blind on the one thing this bench was built for, which was the
original justification for the bench.

## Acceptance criteria

1. One of the three options is chosen and the choice is recorded with its rationale, in
   `docs/qemu-test-bench.md` and in ADR 039.
2. If option 1: `python tools/dbt.py qemu --board esp32s3-qemu` exits 0 with all five SPEC-leg-27
   assertions matched, including the payload's own marker.
3. If option 1: a build for a non-QEMU board (`m5stack-cardputer`) is byte-for-byte unaffected in
   the loader's write path — verifiable by the absence of the symbol in its `sdkconfig.board` and
   by preprocessed output or a link-map comparison.
4. If option 1: the guard carries a comment naming the trade-off (two divergent write paths; the
   bench cannot catch a break in the hardware alias arithmetic).
5. If option 2: the upstream issue or patch is filed and referenced from
   `docs/qemu-test-bench.md`; the CI job stays honest about being blocked until it merges.
6. The "must not be re-attempted" record above survives whatever is chosen.

## Out of scope

- Changing the loader's hardware write path unconditionally — see "What must not be
  re-attempted".
- Emulating any peripheral (SD, display, keyboard). SPEC-leg-27's boundary is unchanged.
- The ADC constructor blocker — SPEC-leg-28. Both must land before `qemu-smoke` can go green.

## Risks

- **`loader.c` currently carries another session's uncommitted work** (SPEC-leg-25, ELF parser
  extraction — ~600 lines). Whoever implements this must coordinate with that work and branch
  from its result, not from a stale copy: the line numbers cited above are pre-extraction.
- Option 1's divergence risk is real and is stated above rather than mitigated. If it is
  chosen, a periodic hardware run remains the only proof of the DRAM-alias path.
- Option 1 adds a `qemu:` key to the `board.yaml` schema. A flag meaning "this board is not
  real" invites future misuse as a general "relax things here" switch; its documented meaning
  should be narrow.
- Option 2's timeline is outside the project's control.

## Open questions

- ~~Option 1's symbol name and YAML key~~ — **resolved: `qemu: true` / `CONFIG_DUNEOS_TARGET_QEMU`.**
  The property-naming alternative (`sram_aliased: false`) is the more honest name and was a close
  call. `qemu:` was kept because it matches the board names, the `dbt qemu` subcommand and ADR
  039's vocabulary, so a reader meets one word rather than two. The catch-all risk the question
  raises is real and is answered by documentation rather than by the name: the Kconfig help, the
  `board.yaml` comments, ADR 039 and `docs/qemu-test-bench.md` all state the meaning as narrowly
  as `sram_aliased` would have ("internal SRAM is not dual-mapped here", not "relax things under
  emulation"). Revisit the name if a second, unrelated emulator workaround ever wants this key —
  that is the signal that the narrow meaning has failed.
- ~~Options 1 and 2 exclusive?~~ — **resolved: not exclusive, and option 1 is explicitly
  temporary.** ADR 039 and `docs/qemu-test-bench.md` both record that the upstream
  `memory_region_init_alias()` fix stays the preferred end state and that landing it deletes the
  guard. The bidirectional GDB reproduction in the Context section above is ready to file;
  filing it is not gated on this spec.
