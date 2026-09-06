Status: APPROVED

# SPEC-leg-37 — Declare the main-task stack per board, and make its overflow loud

## Context

Finding LEG-37, found by flashing a working CardPuter and bricking it into a reboot loop.

`main_task` runs the entire boot on ESP-IDF's 3584 B default: it mounts LittleFS, mounts the SD
card, then scans every `.dap` in `/bin`. Reading a file from `/flash` descends through
`lfs_bd_crc` → `lfs_bd_read` → `esp_partition_read` → `spi_flash_op_lock`, which is already deep.
LEG-25's parser extraction added one struct local and one call frame on exactly that path — on
Xtensa's windowed ABI a frame spills a register window — and there was no margin left.

**Measured on hardware**, with the stack raised to 12288 B so the measurement itself could not
overflow: `uxTaskGetStackHighWaterMark` reported **8524 B free**, i.e. a peak of **3764 B** against
a 3584 B default. The board was overflowing by **180 bytes**.

The failure mode is what makes this expensive, and it is the part worth engineering against. The
overflow ran into the adjacent heap instead of tripping a guard: TLSF's free list took stack
contents (return addresses where the free poison `0xfefefefe` belonged), and the crash surfaced
thousands of instructions later inside an unrelated `malloc`, at a different place on each boot —
once in cJSON under `extract_manifest`, once during the SD mount. Heap poisoning reported
`CORRUPT HEAP` with no culprit. `CONFIG_FREERTOS_WATCHPOINT_END_OF_STACK` named it in a single run.

Attribution was established by bisect on the hardware, not by reading code: `5e1dd97` boots clean,
`8f1d3e6` corrupts the heap, and a control build of the baseline with the same poisoning stayed
clean on the same board with the same SD card.

**Why this must not live in `sdkconfig.defaults`.** That file is the Kconfig common to every board.
A stack watermark is a property of (architecture × that board's boot workload): Xtensa spills a
register window per frame and RISC-V does not, so `esp32c3` will not need the same figure and may
well fit the default; a board with no SD never walks the `sdspi` path at all. Declaring it at the
root makes every board pay one board's maximum. It is also an ESP-IDF artefact — RP2040 has no
`sdkconfig` — so the portable shape is to declare the intent in `board.yaml` and let the generator
translate it per SDK, exactly as `console:` and `psram:` already do (ADR 015).

## Scope

1. A `main_task_stack` key in `board.yaml`, emitted by `tools/duneos-bspgen.py` as
   `CONFIG_ESP_MAIN_TASK_STACK_SIZE` into that board's `sdkconfig.board`.
2. `boards/m5stack-cardputer/board.yaml` declares the measured value, with the measurement and its
   date recorded in a comment beside it.
3. `CONFIG_FREERTOS_WATCHPOINT_END_OF_STACK=y` so a future overflow that writes into the last 32
   bytes of a stack is a named panic instead of silent heap corruption. This one is a project-wide
   policy, not a per-board measurement, so it belongs in `sdkconfig.defaults`. It is **not** free:
   ESP-IDF's Kconfig help says the watchpoint's alignment requirement "decreases the usable stack
   size by up to 60 bytes" per task, and that it "only triggers if the stack overflow writes within
   32 bytes near the end of the stack, rather than overshooting further". The cost and the limit are
   accepted deliberately and must be stated wherever the symbol is justified.
4. ADR 040 recording the layering rule this case exposed: what belongs to the root Kconfig, what
   belongs to the board, and how a board-declared value survives a non-ESP-IDF SDK.
5. A `dbt` guard refusing a build or flash whose cached `sdkconfig` contradicts the declared layers.
   Declaring a value the build directory then discards is the same silent failure one layer up.
6. `duneos-bspgen.py` refuses an unknown top-level `board.yaml` key. `main_task_stack` is the first
   typed key where a typo generates cleanly, emits nothing, and leaves the board on the default that
   bricks it — silence is no longer an acceptable response to a key it does not recognise.

## Acceptance criteria

1. `main_task_stack: N` in a `board.yaml` produces `CONFIG_ESP_MAIN_TASK_STACK_SIZE=N` in that
   board's `sdkconfig.board`; omitting the key emits nothing and leaves the SDK default in force.
2. A non-integer, non-positive or non-4-byte-aligned value is rejected by bspgen with a message
   naming the board and the offending value — not silently coerced.
3. Only `boards/m5stack-cardputer` declares the key. Regenerating all eight boards changes exactly
   one `sdkconfig.board`; the other seven are byte-identical.
4. The CardPuter's declared value is justified in a comment by the measurement (peak 3764 B,
   measured 2026-09-05 with `uxTaskGetStackHighWaterMark` against a 12288 B stack) and states the
   margin it carries. A bare number with no derivation fails this criterion.
5. `CONFIG_FREERTOS_WATCHPOINT_END_OF_STACK=y` is in `sdkconfig.defaults` with a comment saying why
   it is there rather than per board, and stating its true cost and limit as ESP-IDF documents them:
   one hardware watchpoint, **up to 60 bytes of usable stack per task**, and detection only for an
   overflow writing within the last 32 bytes. The comment must name the consequence for the boards
   still on the unmeasured default rather than presenting the symbol as free.
6. `python -m pytest tools/dbt/tests -q` passes, with new cases covering criteria 1 and 2.
7. `dbt qemu` still exits 0 with all five assertions on **both** QEMU boards — neither declares the
   key, so neither may change.
8. **On the physical CardPuter**: the kernel boots, `duneos_loader_scan` completes, the services in
   `init.yaml` start, and the board enumerates as `303a:4dae`. No watchdog reset, no
   `Guru Meditation`, no `CORRUPT HEAP`. This criterion cannot be met by any bench and must be
   verified by flashing.
9. ADR 040 exists, is referenced from `docs/adr/README.md`, and states the rule in terms that
   survive a second SDK for **both** halves: where a board's measurement is declared, and where a
   project-wide policy lives when the SDK has no `sdkconfig` at all.
10. `dbt` refuses a build or flash when the build directory's cached `sdkconfig` disagrees with
    `sdkconfig.defaults` or the active board's `sdkconfig.board`, naming the symbol, the stale value,
    the declared value and the remedy. A symbol the build directory does not carry at all is not a
    disagreement — Kconfig legitimately drops a symbol whose dependencies are unmet, and refusing
    that would block the build rather than protect the board. The check is pure text comparison over
    files already on disk, adding no measurable time to a build.
11. `main_task_stac: 4608` (or any other unrecognised top-level key) fails bspgen with a non-zero
    exit naming the key and, when one is close, suggesting the intended one. All eight boards in the
    tree still validate — including `lora`/`rfid` on `lilygo-t-embed-cc1101`, declared hardware that
    no generator models yet and which the key whitelist admits deliberately.

## Out of scope

Reducing the loader's stack footprint (~60 B recoverable by heap-allocating `duneos_elf_image_t`;
not worth the churn for the margin it buys); moving the loader scan off `main_task` onto a dedicated
task (it would cost a whole additional task stack to save 1 KB on one); auditing every other task's
stack; measuring watermarks on the other seven boards; the RP2040 translation itself.

## Risks

The measured 3764 B is one boot, one SD card, 35 apps in `/bin`. Depth does not accumulate across
scanned files, but LittleFS state and SD presence do move it. A margin that merely covers the
measurement would be a stack whose overflow corrupts the heap silently — which is precisely the bug
this spec exists to close. Criterion 5 is what makes a wrong margin survivable, so it must not be
dropped as "nice to have".

Seven boards remain unmeasured, and they are not in the same position. Six keep ESP-IDF's 3584 B
default; `kincony-A16` is on 8192 through the raw `kconfig:` passthrough — evidence that someone hit
this before and worked around it untraceably, with no recorded measurement and no owner. If any of
the six runs the same scan on the same LittleFS path, it is one call frame away from the same
failure, and nothing in this spec tells us. Enabling the watchpoint costs those six up to 60 B of
usable stack, moving them marginally closer to that failure — a trade taken so the failure is named
when it happens. Both gaps are recorded here deliberately rather than silently closed by raising the
default for everyone.

The stale-`sdkconfig` trap is the same failure one layer up: ESP-IDF applies a defaults file only to
symbols not already present in a build directory's cached `sdkconfig`, so a build dir configured
before this change keeps `CONFIG_ESP_MAIN_TASK_STACK_SIZE=3584` and the watchpoint off, exits 0, and
flashes the bricking configuration. `dbt` therefore refuses a build or flash whose cached
`sdkconfig` disagrees with the active board's fragment.

## Open questions

None
