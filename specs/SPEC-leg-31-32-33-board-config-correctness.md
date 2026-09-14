# LEG-31/32/33 — bspgen emits what no board declared, and validates nothing the kernel assumes
Status: APPROVED

Supersedes `SPEC-leg-31-i2c-guard-vs-bus-zero.md`,
`SPEC-leg-32-drv-logic-emitted-for-every-board.md`,
`SPEC-leg-33-requires-over-declaration.md`. One branch, one cycle, one PR.

## Corrections to the superseded specs

Stated first: three of these change the work.

1. **SPEC-leg-31 named one consumer; there are three.** It cited `vfs.c:362` (the
   `board.info` text) and missed the *functional* one — `i2c_bus.c:11-16` opens
   the bus as `.port = 0` from `DUNEOS_I2C0_SDA_PIN/_SCL_PIN/_FREQ_HZ` — and
   `drv_i2c.c:67` `.name = "i2c-0"`. The kernel I2C stack is single-bus by
   construction (one `static duneos_hal_i2c_t *s_bus`). Its "direction 2 — the
   code discovers buses" is therefore a multi-bus I2C driver, not an `S`.
2. **Every bspgen line number in all three specs is stale.** Real today:
   emission `794-798`, I2C guard `800`, I2C macros `322-333`, SPI counter `306`
   and `406`, WiFi polarity `881`. Cardputer's commented-out `i2c:` is at
   `board.yaml:98-102`, not `65-69`.
3. **There are eight boards, not five.** `esp32c3-devkitc` and `esp32p4-devkitm`
   appear in no criterion of any superseded spec. They are paper boards (no
   `arch/riscv32/arch.cmake`) but bspgen generates for them, and they are where
   the WiFi polarity misfires: **an ESP32-P4 has no radio and gets
   `CONFIG_DUNEOS_DRV_WIFI=y` today.**
4. **The unifying theme does not hold for all three.** Exact for LEG-32 and for
   LEG-33B. Half-true for LEG-31: bspgen's emission *does* match the
   declaration; what is missing is a validation of an invariant the kernel holds
   silently. False for LEG-33A, which is CMake hygiene touching no generated
   output. Recorded rather than forced.

Re-checked and correct: `esp_netif` at `arch/xtensa_esp32s3/arch.cmake:77`
duplicates `kernel/duneos_kernel/CMakeLists.txt:77`; seven REQUIRES entries are
genuinely referenced; no `board.yaml` carries `logic:`; SPI's
`enumerate(raw_buses, start=1)` makes SPI immune.

## Context

**LEG-31.** `bspgen:800` emits `CONFIG_DUNEOS_DRV_I2C=y` on the presence of an
`i2c:` list; `:322-333` emits `DUNEOS_I2C{yaml_id}_*`. Under that symbol the
kernel compiles code naming bus 0 (the three sites above). **Latent, verified
across all eight boards**: `esp32s3-devkitc:37`, `lilygo-t-embed-cc1101:38` and
`kincony-A16:17` all declare `id: 0`; cardputer's block is commented out; four
declare none. Nothing instantiates the break. The fix belongs in bspgen —
`validate()` (`:177`) already does cross-reference checks of this kind (`:201`,
`sd_card.spi_id` against `spi[]`).

**LEG-32.** `bspgen:794-798` emits `CONFIG_DUNEOS_DRV_LOGIC=y` in the same
literal list as `NULL`/`UART`/`KLOG`/`GPIO`, against `Kconfig:31-33` `default n`.
Not free but small: `drv_logic.c` statics are `s_cfg` + `s_configured`,
`hal_logic.c` one `portMUX_TYPE`, and the constructor only calls
`duneos_dev_register()` — no boot-time hardware touch, unlike SPEC-leg-28's
`esp_adc`. The cost is a `/dev` node no board declared, two objects, and an
`esp_hw_support` dependency. No byte figure is claimed; the criteria assert
linkage, not size.

**LEG-33A — REQUIRES.** One verified duplicate (`esp_netif`). One
build-verifiable candidate: the `driver` umbrella at `arch.cmake:66`, whose every
in-tree `driver/*.h` consumer is already covered by a named `esp_driver_*`.
Nothing else is over-declared. The failure this can reintroduce is the one
`CLAUDE.md` names — `CONFIG_*` is empty during the requirements phase, so a
*guarded* REQUIRES entry hides headers at compile time. This change only
*deletes*; criterion 10 makes guarding a hard failure, and criterion 12 proves
a deletion by link identity, not by grep.

**LEG-33B — WiFi polarity.** `bspgen:881` `if board.get("wifi", True)` — opt-out,
uniquely; `i2c`, raw `spi`, `battery`, `display`, `network`, `gpio_expanders` all
test presence.

**The two halves of LEG-33 are two findings**, not one: different files,
mechanisms and verification (link identity vs generated text), and only B belongs
to the theme. They share a branch, never a criterion.

**Verification context.** CI builds the kernel for `m5stack-cardputer` only,
boots the two QEMU boards, builds the `cardputer-contest` profile. Five boards
are built by nobody. Widening that is LEG-10, out of scope. `FLOOR=377` against
384 tests at `ci.yml:51`.

## Product Owner decisions

1. **WiFi: flip to opt-in.** The only resolution that stops `esp32p4-devkitm`
   claiming a radio it does not have, and it makes the generator uniform. The
   three boards that must keep the radio gain an explicit `wifi: true`.
2. **Absorb the GPIO contradiction.** `CONFIG_DUNEOS_DRV_GPIO` is `default n` in
   `Kconfig:25-27` while bspgen emits it for every board — the same contradiction
   this cycle fixes for LOGIC, with the opposite correct resolution (`default y`;
   GPIO is platform, not peripheral). One line, changes no board's build today,
   and it lets criteria 8/9 be strengthened into "every unconditionally-emitted
   symbol is `default y`" — closing the class rather than a case.
3. **No radio-capability guard.** Rejecting `wifi: true` on a radioless CPU is
   not deducible from these three rows. Opt-in already corrects the P4; add the
   guard when a RISC-V board is real.

## Scope

- `tools/duneos-bspgen.py`
  - `validate()`: reject an `i2c:` list containing no `id: 0`, naming the board,
    the offending ids and the invariant. (LEG-31)
  - Move `CONFIG_DUNEOS_DRV_LOGIC=y` out of the unconditional list at `:794-798`,
    gate it on a bare `logic:` key; add `logic` to `KNOWN_BOARD_KEYS`. (LEG-32)
  - Flip `:881` to opt-in. (LEG-33B)
  - Schema comment block (`:17-92`): document the bus-0 invariant, the `logic:`
    key and the WiFi polarity. One line at `:306` recording that the SPI index
    must stay a dense counter.
- `kernel/duneos_kernel/Kconfig` — `DUNEOS_DRV_GPIO` to `default y` (PO 2).
- `boards/m5stack-cardputer/board.yaml` — bare `logic:` key, and `wifi: true`.
  `esp32s3-devkitc` and `lilygo-t-embed-cc1101` — `wifi: true`. `kincony-A16`
  already declares it; the QEMU boards already say `wifi: false`;
  `esp32c3-devkitc` and `esp32p4-devkitm` gain nothing and lose the symbol.
- `arch/xtensa_esp32s3/arch.cmake` — move `hal/hal_logic.c` from the unguarded
  block (`:30-35`) into the guarded peripheral-HAL block (`:44-58`), SRCS only.
  Delete `esp_netif` (`:77`). Attempt the `driver` deletion (`:66`). Consumer
  `file:line` comment on every surviving REQUIRES entry.
- `kernel/duneos_kernel/CMakeLists.txt`, `main/CMakeLists.txt` — consumer
  comments. No deletions expected.
- `tools/dbt/tests/` — new cases. `conftest.py::regenerated_root` and the
  `BSPGEN.generate_sdkconfig_board(dict)` / `BSPGEN.generate(dict)` pure-function
  entry points are the vehicles. No test reads `boards/*/sdkconfig.board` or
  `board_config.h`.
- `.github/workflows/ci.yml` — raise `FLOOR`; optionally one grep step in the
  existing `qemu-smoke` job (criterion 6). **No new board job.**
- `ROADMAP.md` — the three rows to DONE; correct the "five boards" framing.

## Acceptance criteria

pytest unless marked. "Generated" always means into `tmp_path` or returned
in-process.

**LEG-31**

1. `validate()` raises `SystemExit` on `i2c: [{"id": 1, ...}]`, message naming
   the board and `id: 0`. Does not raise for `[{"id": 0}]`, `[{"id": 0},
   {"id": 1}]`, or no `i2c:` key.
2. For every tracked `boards/*/board.yaml`, `validate()` does not raise — the new
   rule rejects no existing board.
3. For every tracked board: each `DUNEOS_*` macro referenced by the kernel
   sources gated by a `CONFIG_DUNEOS_DRV_*` symbol the fragment emits — extracted
   from `kernel/duneos_kernel/CMakeLists.txt` and `arch/*/arch.cmake`
   `if(CONFIG_…)` blocks — is defined in the freshly generated `board_config.h`,
   minus macros defined by a tracked header (computed, not hand-listed).
4. **Negative control for 3**: the same check on a synthetic `i2c: [{id: 1}]`
   board built in the test body, bypassing `validate()`, **fails**, naming
   `DUNEOS_I2C0_SCL_PIN` (or `_SDA_PIN`/`_FREQ_HZ`). A criterion-3 implementation
   that cannot be made to fail here does not satisfy criterion 3.

**LEG-32**

5. `generate_sdkconfig_board()` emits `CONFIG_DUNEOS_DRV_LOGIC=y` **iff** the
   board carries a `logic:` key. Both directions, and per tracked board exactly
   one (`m5stack-cardputer`) emits it.
6. On a board without `logic:`, neither `drv_logic.c.obj` nor `hal_logic.c.obj`
   is compiled and `drv_logic_register` is absent from `duneos.map`. Verified on
   `esp32s3-qemu` by a grep step in the existing `qemu-smoke` job, or failing
   that recorded in the PR with the map excerpt.
7. `logic` is in `KNOWN_BOARD_KEYS`: a board declaring it passes `validate()`.
8. The set of `CONFIG_DUNEOS_DRV_*` symbols emitted unconditionally for all eight
   boards is exactly `{NULL, UART, KLOG, GPIO}`. Pinning test — the next
   unconditional addition must be deliberate.
9. Every `CONFIG_DUNEOS_DRV_*` any board emits is declared in `Kconfig`, **and
   every unconditionally-emitted symbol is `default y` there** (PO 2). Catches
   both a typo emitting a symbol Kconfig drops, and the LOGIC/GPIO contradiction
   class.

**LEG-33A**

10. No `list(APPEND DUNEOS_KERNEL_REQUIRES …)` in `kernel/duneos_kernel/CMakeLists.txt`
    or any `arch/*/arch.cmake` sits inside an `if(CONFIG_DUNEOS_*)` block.
    Asserted by parsing the CMake text. The `USB_MSC OR …_CDC` append at
    `CMakeLists.txt:82-84` is pre-existing: the Builder either justifies it as a
    named exception in the test or brings it into line. It may **not** be
    silently admitted by a loose pattern.
11. No component appears both in the unconditional `DUNEOS_KERNEL_REQUIRES` and
    in any `arch/*/arch.cmake` REQUIRES list — the `esp_netif` defect class.
12. **Manual, recorded in the PR.** For `m5stack-cardputer`, the sorted symbol
    list from `duneos.map` is byte-identical before and after the deletions. If
    it differs, the entry goes back with a comment naming what pulled it in.
    Sole gate on the `driver` umbrella.
13. **Manual, recorded in the PR.** `esp32s3-devkitc`, `lilygo-t-embed-cc1101`
    and `kincony-A16` each build clean after a fullclean, into their own build
    dir with `-D SDKCONFIG=<build-dir>/sdkconfig`.
14. Every surviving REQUIRES entry in the three CMake files carries a comment
    naming at least one consumer by `file:line`.

**LEG-33B**

15. `generate_sdkconfig_board()` emits `CONFIG_DUNEOS_DRV_WIFI=y` **iff** the
    board has `wifi: true`. Asserted for true, false, and key absent.
16. Per tracked board, the emitted WiFi symbol matches a map **built in the test
    body from `board.yaml`**: present for `m5stack-cardputer`,
    `esp32s3-devkitc`, `lilygo-t-embed-cc1101`, `kincony-A16`; absent for the
    two QEMU boards, `esp32c3-devkitc`, `esp32p4-devkitm`.
17. The three radio boards emit the symbol after the flip exactly as today — the
    flip changes behaviour only for `esp32c3-devkitc` and `esp32p4-devkitm`.

**Cycle-wide**

18. `python -m pytest -q` and `make -C tests/host test` pass. `FLOOR` raised to
    (new ran count − 7), preserving today's margin.
19. `dbt qemu` exits 0 for `esp32s3-qemu` and `esp32s3-qemu-psram`.
20. Cardputer's kernel build is green in CI, and the stale-sdkconfig guard
    refuses a build dir predating the `logic:`/`wifi:` additions rather than
    silently keeping the old fragment.
21. No generated file is hand-edited and no test opens one.

**On a synthetic board plus a real configure**: unnecessary under this direction.
The id-1 board is *rejected by bspgen*, so the observable behaviour is the
rejection — a pure-Python assertion (1). Criteria 3+4 keep the compile-level
invariant checked without a toolchain.

## Out of scope

LEG-10 and any new board job (criteria 12/13 are manual precisely because of
this, and that is the accepted cost); multi-bus I2C in the kernel; renumbering
any board's buses; SPI (no defect); `drv_logic.c`/`hal_logic.c`/ADR 020 design —
this cycle decides whether the device is *linked*, never how it captures; the
`esp_eth`/`esp_netif` layering wart at `arch/xtensa_esp32/arch.cmake:11-13`;
adding any component to any REQUIRES list; guarding any REQUIRES entry; a
radio-capability guard (PO 3); `main/main.c:72`'s stale comment (SPEC-leg-20);
the ABI — no exported symbol or struct layout moves, no `DUNEOS_ABI_VERSION`
bump, and if any diff reaches `abi.h` that assumption is void.

## Risks

- **The `driver` umbrella can fail in the invisible direction.** Its transitive
  dependencies appear in no `#include`. Criterion 12 compares the link, not the
  compile, and is all that stands between a clean-looking deletion and a board
  that stops building next month.
- **The five boards CI never builds are where a REQUIRES trim lands.**
  `kincony-A16` is the worst case: the only `esp_eth` consumer, borrowing the S3
  file's requirements. Criterion 13 is manual and therefore skippable — if
  skipped, the trim is not proven and the entries go back.
- **Criterion 3 is the most valuable and the most likely to be built vacuous.** A
  false-positive-heavy version invites an ever-growing allow-list until it
  asserts nothing — the LEG-38-08 failure mode in a new costume. Criterion 4 is
  the bite test; if it cannot be made to fail, criterion 3 is not done.
- **Removing `/dev/logic0` is a userspace-visible platform change** on four
  boards. No in-tree app on them opens it (`i2cscope` is the sole consumer and
  ships in `cardputer-contest`), but nothing in the kernel build would catch one
  that did.
- **Two boards silently lose `CONFIG_DUNEOS_DRV_WIFI=y`.** For
  `esp32p4-devkitm` that is a correction; for `esp32c3-devkitc` a real change on
  a board nobody builds. Both are paper boards, so it is inert until a RISC-V
  arch lands — at which point it is the state they will want.
- **Sdkconfig staleness.** Cardputer's fragment changes twice in this cycle.
  Every board needs a fullclean; criterion 20 leans on the LEG-37 guard to say so.
- **LEG-37 restated, the one way to get half A wrong**: `CONFIG_*` is empty
  during the requirements phase. Deleting is safe; guarding is not. Criterion 10
  is the gate.
- No PSRAM or kernel-heap pressure: this cycle only removes code from boards.

## Open questions

None.
