# ADR 039 — QEMU boards as the hardware-free test bench

**Status:** Accepted · 2026-09-03 (accepted 2026-09-05, once SPEC-leg-27/28/29/30 shipped the bench described here)

## Context

The ELF loader accepts input from untrusted files: `duneos_loader_scan()` reads the headers of every candidate on `/flash/bin/`, `/sd/bin/` and `/sd/apps/` before anything is launched. A 2026-09 audit found several unbounded reads on that path (`e_shstrndx`, `sh_link`, `sh_name`/`st_name` indexed before validation). Hardening them is a handful of bounds checks — but there is currently **no way to run `mount → scan → load .dap → run → exit` without a board on the desk**, so a fix cannot be shown to work and a regression cannot be caught.

That gap is wider than the loader. [ADR 012](012-test-strategy.md) prescribes host-side tests first, and `tests/host/` exists with three suites already wired into CI, but no test exercises the kernel *running*. [ADR 007](007-multi-arch-smoke-test.md) commits to a build-only matrix and explicitly defers execution: "real tests come from QEMU/board-in-the-loop and are a separate effort". This ADR is that effort.

What the ground already offers, verified 2026-09-02:

- Espressif's `qemu-xtensa` supports `esp32` and `esp32s3`; `qemu-riscv32` supports `esp32c3` (`$IDF_PATH/tools/tools.json`, version `esp_develop_9.2.2_20250817`). The binary is not installed by default (`idf_tools.py install qemu-xtensa`).
- `idf.py qemu` ships with ESP-IDF v6.0.1 (`tools/idf_py_actions/qemu_ext.py`): it builds `qemu_flash.bin` + `qemu_efuse.bin`, exposes the serial port on a socket and GDB on 3333, and accepts extra QEMU arguments.
- **PSRAM is emulated** — an `ssi_psram` device, quad mode by default, octal selected from `CONFIG_SPIRAM_MODE_OCT` (`qemu_ext.py:305-307`). Since bspgen already emits that symbol from a board's `psram:` section, the chain `board.yaml → bspgen → sdkconfig → QEMU arguments` needs no new glue.
- DuneOS mounts `/flash` (LittleFS, `sysbin`) first and scans `/flash/bin/` before the SD card ([ADR 005](005-path-conventions.md)), and `dbt flashimg` already builds that image with the apps embedded. QEMU boots a whole flash image, so **the end-to-end `.dap` path is reachable with no peripheral emulation at all**.

## Decision

**A QEMU target is a board.** `boards/esp32s3-qemu/` and `boards/esp32s3-qemu-psram/` are ordinary `board.yaml` entries, selected through `.duneos_board` and composed into images through profiles like any other board. bspgen generates their `sdkconfig.board`, `partitions.csv`, `board_config.h` and `idf_target.txt` unchanged.

**They declare only what QEMU models**: UART, flash, `has_sd: false`. No display, no I2C keyboard, no SD. This is the point rather than a limitation — boot then falls through to `/flash` alone, which is exactly the path the bench must exercise, and no driver initialises against absent hardware.

**Two boards, because the loader branches on PSRAM.** `loader.c` allocates app sections under `#ifdef CONFIG_SPIRAM` — SPIRAM on PSRAM boards, DRAM otherwise. Only the DRAM branch is exercised today, the CardPuter being the daily driver and the T-Embed rarely flashed. The pair covers both.

**dbt drives the emulator through the toolchain plugin**, as `plugin.run_qemu()`, alongside the existing `build_kernel`/`flash_kernel`/`monitor` from Phase 21. This is deliberately the shape [Phase 28.5](../../ROADMAP.md) already plans for the extended plugin interface, so the SDK decoupling does not have to redo it.

**The emulator's missing SRAM alias is answered by a board-scoped compile-time guard** (SPEC-leg-29, decided 2026-09-04). `qemu-xtensa`'s `esp32s3` machine maps the IRAM (`0x40…`) and DRAM (`0x3fc…`) windows of internal SRAM as two *separate memories* rather than two views of one, confirmed bidirectionally under GDB. The loader writes an app's relocated `.text` through the DRAM alias — the only way that works on silicon — and executes at the IRAM address, so under emulation it executes zeroes and dies with `cause=0` four words in.

A board declares `qemu: true`; bspgen emits `CONFIG_DUNEOS_TARGET_QEMU=y`; `loader.c` selects `s_build_scratch = iram` under that symbol and keeps `iram_word_dram_alias()` otherwise. The rationale:

- **Real boards never define the symbol, so the hardware write path is literally unchanged** — not "probably safe", unchanged. The byte stores that would raise `LoadStoreError` on silicon only ever compile into a binary that never runs on silicon.
- It reuses the established `board.yaml` → bspgen symbol → conditional compilation model rather than inventing a mechanism, and `loader.c` already branches this way on `CONFIG_SPIRAM`.
- **The coverage argument is the decisive one.** The guard gives up proving that writes through the DRAM alias land at the IRAM view — and QEMU *cannot test that anyway*, since it does not implement the alias. The real choice is therefore **zero coverage** (nothing runs at all) versus **everything covered except a property the emulator is incapable of checking**. Parsing, relocation arithmetic, symbol resolution, scan, select, launch, VFS and mount are identical in both builds.
- **The honest cost: two divergent write paths.** If someone later breaks the alias arithmetic, the bench stays green. That is the standard test-double trade-off; it is bounded, the guard carries a comment saying so, and a periodic hardware run remains the only proof of the DRAM-alias path.

The `qemu:` key's meaning is deliberately narrow — "internal SRAM is not dual-mapped here" — and must not drift into a general "relax things under emulation" switch. Fixing the alias upstream in Espressif's QEMU fork (`memory_region_init_alias()`) stays the better long-term answer and would let the guard be deleted; it was not chosen as *the* fix because its timeline is outside the project's control (Espressif tags their own DIRAM aliasing tests `[heap][qemu-ignore]`, i.e. they exclude the property rather than fix it).

**What must not be re-attempted:** making the loader write at the IRAM address *unconditionally*. It was tested and it does make the `.dap` run under QEMU, but it is incorrect on real silicon — IRAM is 32-bit-access-only (`mem_alloc.rst:126`; `SOC_BYTE_ACCESSIBLE_*` covers the DRAM view only) while the loader performs unaligned byte stores into the instruction stream and reads sections straight into the exec block. Doing nothing at all was the third option, rejected because it leaves SPEC-leg-27's criterion 6 permanently red and every loader change validated blind on the one thing the bench was built for.

**In two steps.** Step one is the dedicated boards above. Step two — later, and not committed here — would let the shipping CardPuter configuration itself be booted under emulation. Step two is deferred because it is unproven: the kernel would initialise ST7789 over SPI and the I2C keyboard against hardware QEMU does not model, and whether those drivers degrade or hang has not been established. The trajectory is recorded; the commitment is not.

> **Superseded in one detail, by the SRAM-alias decision above.** This paragraph originally described step two as "adding a `qemu:` field to the *real* boards". That is no longer possible: `qemu: true` now selects an exec write path that is wrong on silicon, so putting it on a physical board's `board.yaml` would make the shipping build unable to load an app on the device. Step two needs a different mechanism. See the Consequences bullet "The `qemu:` key constrains step two".

## Consequences

- The loader hardening of milestone 1 becomes verifiable: a malformed `.dap` placed in the flash image either loads safely or does not, and CI can say which.
- The PSRAM allocation branch gains its first regression coverage.
- **The `qemu:` key constrains step two.** Because `qemu: true` now selects an exec write path that is wrong on silicon, it cannot simply be added to a real board's `board.yaml`: that would make the shipping build unable to load an app on the physical device. Step two therefore needs a separate mechanism — a QEMU-flavoured variant of the real board, or a build-time override — not this flag. Recorded here so the collision is found at design time rather than on a bricked CardPuter.
- **Two loader write paths now exist**, and only one of them is exercised by CI. The DRAM-alias path that real hardware uses is proven by hardware runs alone. Deleting the guard — by fixing the alias upstream in QEMU — removes that gap and stays the preferred end state.
- **What the bench proves is bounded, and must be stated when reporting it**: it validates the loader, the VFS and the boot sequence — not the CardPuter. A green QEMU run says nothing about the shipping board's display, keyboard or SD path. Step two is what would close that gap.
- **SD is out of scope.** Whether Espressif's ESP32-S3 QEMU models SDMMC or SPI-SD was not established. `/sd/bin` and `/sd/apps` therefore stay untested, along with FAT's 8.3 uppercase filenames — a documented trap that only hardware currently catches.
- Switching `.duneos_board` requires a fullclean (`sdkconfig` is board-specific and CMake raises FATAL_ERROR on an unclean switch), so a two-board CI matrix needs one build directory per board rather than a shared one.
- `qemu-xtensa` becomes a CI dependency, pinned like the rest of the toolchain.
- `qemu-riscv32` covering the ESP32-C3 means the same bench extends to a second ISA when Phase 28 lands — supporting [ADR 007](007-multi-arch-smoke-test.md) with execution, not only a build.

## Alternatives

- **A `qemu:` field on the real boards, now** — rejected for this step. It tests the configuration that actually ships, which is strictly more valuable, but it depends on drivers tolerating absent hardware. That is an unverified assumption, and building the bench on it risks a bench that never boots. **It was kept as step two when this ADR was drafted, and no longer is:** the SRAM-alias decision gave `qemu: true` an effect that is incorrect on silicon, so this alternative is now closed rather than deferred. See the Consequences bullet "The `qemu:` key constrains step two".
- **A runner concept separate from boards** (`runners/qemu.yaml` or similar) — rejected. It is arguably the cleaner model, since QEMU-ness is a way of running rather than a property of hardware. But it invents a third axis next to boards and profiles for no capability that boards do not already provide, and every piece of machinery it needs (bspgen, `.duneos_board`, profiles, dbt dispatch) exists and is board-keyed.
- **Emulating the peripherals** (ST7789, I2C keyboard, SD) — rejected here. It requires device models in C inside Espressif's QEMU fork, plus maintaining that fork. [ADR 007](007-multi-arch-smoke-test.md) already places a Linux simulator target in Phase 26, which is the natural home for mocked drivers.
- **Renode** instead of QEMU — rejected. Better peripheral modelling, but no ESP32-S3 support of the maturity Espressif's fork has, and no integration with `idf.py`.
- **Hardware-in-the-loop** (a board wired to CI) — not rejected on merit, deferred on cost. It is the only thing that tests the real peripherals, and it remains the right answer for the display/keyboard/SD paths this bench cannot reach.
