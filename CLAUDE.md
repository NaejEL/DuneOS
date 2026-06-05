# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**DuneOS** is a minimalist OS for ESP32-S3 microcontrollers built on ESP-IDF. Its core goals:

- Dynamically load separately-compiled applications (`.dap` / `.elf` files) from an SD card at runtime (similar to Flipper Zero `.fap` files)
- Expose a partial POSIX layer (files, threads, time) via newlib + ESP-IDF VFS
- Provide declarative board configuration (YAML → generated C header), inspired by Zephyr Device Tree

**Current dev board:** M5Stack CardPuter (ESP32-S3FN8 — 8 MB embedded flash, **no PSRAM**).
**Reference board:** ESP32-S3-DevKitC (with PSRAM).
**Toolchain:** ESP-IDF v6.0.1, CMake, `xtensa-esp32s3-elf-gcc`.

## Build & Test Commands

```bash
# First-time setup (or after switching boards):
echo m5stack-cardputer > .duneos_board                       # gitignored, per-developer
echo COM13 > .duneos_port                                    # gitignored, used by dbt flashimg
python tools/duneos-bspgen.py boards/m5stack-cardputer/board.yaml  # generates sdkconfig.board, partitions.csv, board_config.h, idf_target.txt

# Build kernel: idf.py build  (or IDE equivalent)
# Flash kernel: idf.py -p COM13 flash
# Monitor:      idf.py -p COM13 monitor

# Clean when switching boards — sdkconfig is board-specific
# idf.py fullclean

# Build a DuneOS app
cd apps/user/test_exit
python ../../../tools/dbt.py build
python ../../../tools/dbt.py deploy E:\   # E: = SD card drive

# Inspect built ELF
python ../../../tools/dbt.py info

# Build sysbin image + flash it (LittleFS image with /flash/init.yaml + embedded apps)
python tools/dbt.py flashimg                # uses .duneos_port

# Interactive TUI (board picker, init editor, build/flash actions)
python tools/dbt.py tui
```

## Repository Structure

```text
DuneOS/
├── main/main.c                      # Kernel entry: VFS init → supervisor init → launch → wait
├── kernel/
│   ├── duneos_kernel/
│   │   ├── include/duneos/          # Public headers: abi.h, init.h, klog.h, supervisor.h, task.h, vfs.h, hal_*.h
│   │   ├── Kconfig                  # CONFIG_DUNEOS_DRV_* driver selection options
│   │   └── src/
│   │       ├── init.c / klog.c / supervisor.c / task.c / vfs.c / vfs_tmp.c / vfs_dev.c / symbols.c / api.c
│   │       └── drivers/             # drv_null, drv_uart, drv_klog, drv_i2c, i2c_bus, drv_gpio, drv_battery_adc_simple
│   └── duneos_loader/
│       ├── include/duneos/loader.h  # scan/select/load/run/unload/run_captured API
│       └── src/loader.c            # Full ET_REL loader: parse, relocate, resolve, run
├── arch/
│   ├── xtensa_esp32s3/
│   │   ├── hal/hal_*.c             # ESP-IDF HAL impl for uart/gpio/i2c/spi/adc/time
│   │   └── reloc/loader_reloc_xtensa.c  # STUB — Phase 28 extraction target
│   └── riscv32/                    # STUB — Phase 28 (ESP32-C6 + P4)
├── third_party/cjson/ littlefs/    # Pure-C git submodules (no IDF dep)
├── libdune/src/                    # libdune.a: libdune_ptr/fs/mem/thread/time/sys.c (public header in duneos_kernel)
├── boards/<name>/
│   ├── board.yaml                  # Source of truth — hand-edited
│   ├── board_config.h / sdkconfig.board / partitions.csv / idf_target.txt  # bspgen-generated, never hand-edit
│   └── init.yaml                   # Boot services — hand-edited
├── apps/system/                    # shell_core, usb_shell, wifi_daemon, bin/ (ships with DuneOS)
├── apps/user/                      # g_shell, gfx_demo, test_exit, hello_world, uart_echo, tcp_client
├── profiles/                       # Image recipes — Phase 25 (Yocto-like)
├── tools/dbt.py                    # Thin wrapper → tools/dbt/cli.py
├── tools/dbt/                      # cli, builder, deploy, kernel, flashimg, bspgen, manifest, tui, toolchain/
├── tools/duneos-bspgen.py          # board.yaml → board_config.h + sdkconfig.board + partitions.csv + idf_target.txt
├── .duneos_board / .duneos_port / .duneos_sd / .duneos_idf  # gitignored, per-developer
└── sdkconfig.defaults              # Common Kconfig; board-specific in boards/<board>/sdkconfig.board
```

## Architecture

```text
┌─────────────────────────────────────────────────┐
│    Applications (.dap / .elf)                    │  ET_REL ELF, stored on /sd/apps/
│    USERSPACE: device drivers as libraries        │
│      SX1509/PCF8574 (I2C expander)               │  app opens /dev/i2c-0, speaks
│      SSD1306/ST7789 (display)                    │  the chip protocol in C
│      CC1101/LoRa (radio)                         │  app opens /dev/spi-1
├─────────────────────────────────────────────────┤
│    Kernel ABI (function table)                   │  duneos_symbol_table_get()
│    Permissions (bitmask per symbol)              │  checked at load time
├───────────────┬─────────────────────────────────┤
│  ELF Loader   │  VFS + POSIX layer              │  loader.c / vfs.c
│               │    /tmp, /dev/null, /dev/zero    │
│               │    /dev/uart0, /dev/klog         │
│               │    /dev/gpiochip0, /dev/i2c-0   │
│               │    /dev/spi-1, /dev/fb0          │
│               │    /dev/input/event0             │
├───────────────┼─────────────────────────────────┤
│  Supervisor   │  klog ring buffer               │  supervisor.c / klog.c
├───────────────┴─────────────────────────────────┤
│     FreeRTOS (via ESP-IDF)                       │
├─────────────────────────────────────────────────┤
│     BSP — board_config.h (generated by bspgen)  │
└─────────────────────────────────────────────────┘
```

### Build system — board selection

Board selection is controlled by `.duneos_board` (gitignored, each developer sets their own).
`CMakeLists.txt` always reads this file and ignores any `-DDUNEOS_BOARD=` cmake argument.
**Do not add `-DDUNEOS_BOARD=` to `idf.cmakeAdditionalArgs` in `.vscode/settings.json`** — it is silently overridden.

`IDF_TARGET` is read from `boards/<board>/idf_target.txt` (generated by `duneos-bspgen.py`), so it does not need to be set manually. CMake aborts with FATAL_ERROR if the board changes without a Full Clean.

### Kernel driver selection model

`duneos-bspgen.py` generates three artefacts from a board YAML: `board_config.h` (pin assignments), `sdkconfig.board` (Kconfig fragment with `CONFIG_DUNEOS_DRV_*=y`), `idf_target.txt`, `partitions.csv`.

```text
src/drivers/
  drv_null.c                        CONFIG_DUNEOS_DRV_NULL=y      (always)
  drv_uart.c                        CONFIG_DUNEOS_DRV_UART=y      (always)
  drv_klog.c                        CONFIG_DUNEOS_DRV_KLOG=y      (always)
  gpio/drv_gpio.c                   CONFIG_DUNEOS_DRV_GPIO=y
  i2c_bus.c, drv_i2c.c             CONFIG_DUNEOS_DRV_I2C=y
  battery/drv_battery_adc_simple.c  CONFIG_DUNEOS_DRV_BATTERY_ADC_SIMPLE=y
```

`i2c_bus.c` provides a mutex-protected `i2c_bus_write_read()` used by `drv_i2c.c`. I2C/SPI fuel gauges live in userspace as `apps/system/battery_daemon` + `sdk/sensor/lib<chip>.c`.

### Adding a new kernel driver

Since Phase 24.9, drivers self-register via a GCC constructor — no edits to `vfs_dev.c` needed.

1. Create `src/drivers/<category>/drv_<name>.c` — implement `duneos_dev_driver_t` callbacks + `drv_<name>_register()`. Add `#include "duneos/driver_init.h"` and one line at file scope:
   ```c
   DUNEOS_DRIVER_REGISTER(<prio>, drv_<name>_register);
   ```
   Priority: `1` = bus controllers, `2` = native peripherals, `5` = standard, `8` = bus consumers.
2. Add `config DUNEOS_DRV_<NAME>` to `kernel/duneos_kernel/Kconfig`
3. Add conditional `list(APPEND DUNEOS_KERNEL_SRCS ...)` to `kernel/duneos_kernel/CMakeLists.txt`
4. Enable for a board: edit `boards/<board>/board.yaml`, re-run `python tools/duneos-bspgen.py boards/<board>/board.yaml`
5. Update `tools/duneos-bspgen.py` to emit `CONFIG_DUNEOS_DRV_<NAME>=y` when the YAML section is present

USB MSC + USB CDC remain manually wired in `vfs_dev.c` — different init phase, intentionally do NOT use `DUNEOS_DRIVER_REGISTER`.

### Adding a battery backend (userspace, I2C/SPI fuel gauges)

1. Create `sdk/sensor/lib<chip>.c` — implement `battery_backend_ops_t` (`open`/`read`/`close`), export as `const battery_backend_ops_t duneos_battery_ops`. Read device path + address from `<duneos/board.h>`. Use `libbq27220.c` as reference.
2. Declare on a board: `board.yaml` → `battery: { type: <chip>, i2c_id: 0, gauge_addr: 0xNN }`
3. Re-run `bspgen.py`, add `battery_daemon.dap` to `boards/<board>/init.yaml` with `restart: always`.

Capability resolver pulls `lib<chip>.c` automatically. Zero kernel edits, zero daemon edits.

### Adding a new target architecture

**Zero changes to `duneos_kernel/CMakeLists.txt`.**

1. Create `arch/<new_arch>/arch.cmake` — self-selects via triple guard (see below)
2. In `arch.cmake`: append HAL sources to `DUNEOS_KERNEL_SRCS` and SDK deps to `DUNEOS_KERNEL_REQUIRES`
3. Implement `arch/<new_arch>/hal/hal_uart.c`, `hal_gpio.c`, `hal_i2c.c`, `hal_spi.c`, `hal_adc.c`, `hal_time.c`
4. Add `arch/<new_arch>/reloc/loader_reloc_<arch>.c` — RELA `apply()` function for the loader
5. Add `tools/dbt/toolchain/<sdk>.py` plugin (exports `SDK`, `ARCH`, `cflags`, `ldflags`, `build_kernel`, `flash_kernel`, `monitor`, `find_toolchain_root`)

`duneos_kernel/CMakeLists.txt` discovers all `arch/*/arch.cmake` via `file(GLOB)`. Adding a new SDK = dropping one `.py` file. Toolchain root discovery: `DUNEOS_<SDK>_ROOT` env var → `.duneos_<sdk>` file at repo root → known install paths → PATH scan.

**`arch.cmake` guard pattern — always check all three:**

```cmake
if(NOT CONFIG_IDF_TARGET_ARCH_XTENSA
   AND NOT IDF_TARGET_ARCH STREQUAL "xtensa"
   AND NOT DUNEOS_ARCH STREQUAL "xtensa_esp32s3")
    return()
endif()
```

`CONFIG_IDF_TARGET_ARCH_XTENSA` comes from sdkconfig.cmake (normal build phase). `IDF_TARGET_ARCH` is the ONLY arch signal during the ESP-IDF requirements phase (`sdkconfig.cmake` is NOT loaded there — `CONFIG_*` vars are empty). `DUNEOS_ARCH` is set by non-IDF toolchain plugins. Without `IDF_TARGET_ARCH` in the guard, `DUNEOS_KERNEL_REQUIRES` is never populated during requirements resolution → arch-specific headers missing at compile time.

### Third-party library strategy

Two tiers: `third_party/` git submodules for pure-C libs (cJSON, LittleFS — no IDF headers needed); ESP-IDF managed components for ESP-specific libs (TinyUSB). Rule: if it compiles with `xtensa-esp32s3-elf-gcc` alone, put it in `third_party/`.

## Implementation Status

**DONE:** Phases 1–14, 16–19, 21–24 (Phase 24: HAL scope; core headers `init.h`/`task.h`/`vfs.h`/`supervisor.h` still `esp_err_t`, migrate in Phases 26–27). Phase 15 superseded by 18. Phase 24.5 (17 ADRs in `docs/adr/`).

**PARTIAL:** Phase 20 — Memory hardening: heap caps, WDT, exception handler, pointer validation done; stack canary + TLSF pending.

| Phase | Status | Notes |
| --- | --- | --- |
| Phase 24.7 — Safe boot & recovery | Not finished | `board.yaml recovery_pin` + hold-key in `init.c` still required. Don't claim DONE until hardware-tested. |
| Phase 24.8 — `<duneos/board.h>` auto-generated | Not finished | `boardgen.py` works for storage/display/input/i2c. NOT closed until: capabilities filtering, mtime rebuild trigger, `libst7789.c` migrated. |
| Phase 24.9 — Kernel driver self-registration | Not started | `DUNEOS_DRIVER_REGISTER()` macro + `.duneos_driver_init` ELF section. `vfs_dev.c` becomes a loop — zero `#ifdef`, zero hardcoded list. |
| Phase 24.9.5 — Captured-app exit semantics (ADR 016) | Not started | `loader_run_captured` installs setjmp; `duneos_exit` longjmps back instead of `vTaskDelete(NULL)`. **Critical before contest launcher.** ~50 LoC. |
| Phase 24.10 — libgfx streaming mode | Not started | `gfx_open(GFX_MODE_STREAM)` draws line-by-line to `/dev/disp0`, no userspace back-buffer. Saves 64 KiB on CardPuter. |
| Phase 25 — dbt system (Image Recipes) | Not started | `profile.yaml` + `system.yaml` + `capability_map.py`. `dbt system check` validates permissions before build. |
| Phase 26 — OSAL + scheduler portability | Not started | `duneos_osal.h` abstracts FreeRTOS. `freertos_osal.c` + `pthread_osal.c`. Migrates `task.h`/`supervisor.h` to `int`/-errno. |
| Phase 27 — VFS native + networking | Not started | Native `duneos_vfs` replacing `esp_vfs`; `poll()`/`select()`; `hal_net.h`; WiFi rewrite; BSD sockets. Migrates `vfs.h` to `int`/-errno. |
| Phase 28.5 — ESP-IDF de-coupling | Not started | Split `main/main.c` into SDK-agnostic core + per-SDK `entry_<sdk>.c`. Prerequisite for Phase 29. |

**Boot order:** `/flash` (LittleFS, fatal if absent) → SD (optional). Init: `/flash/init.yaml` then `/sd/init.yaml`. App scan: `/flash/bin/` → `/sd/bin/` → `/sd/apps/`. Error convention (ADR 001): `int`, return 0 on success, -errno on failure.

## Key Technical Decisions

- **ABI = function pointer table**, not CPU syscalls (no MMU). Defined in `abi.h`.
- **ELF format = ET_REL** (relocatable object), not ET_DYN. Same as Flipper Zero FAP.
- **No manifest.json on SD** — manifest is a JSON string embedded in `.duneos_manifest` ELF section.
- **`.dap` binaries are ISA-specific** — loader rejects incompatible arch at load time (manifest `arch` field).
- **Flash-first VFS**: `/flash` (LittleFS, `sysbin` partition) always mounted first and fatal if absent. SD is optional.
- **Flash boot services**: `boards/<board>/init.yaml` controls auto-start services. Deduplication in `init.c` is name-based (basename without `.dap`) — flash entry wins over SD.
- **Board config** selected at build time via `.duneos_board` → `boards/<board>/`. `bspgen.py` generates all derived files.
- **PSRAM auto-detection**: `#ifdef CONFIG_SPIRAM` in loader.c — PSRAM boards allocate in SPIRAM, others in DRAM.
- **Xtensa relocations**: ET_REL uses RELA. Implemented: R_XTENSA_32, R_XTENSA_SLOT0_OP (L32R), R_XTENSA_ASM_EXPAND (ignored), R_XTENSA_DIFF8/16/32.
- **Multi-app**: supervisor owns 4 `app_slot_t` entries; each has its own FreeRTOS task + mailbox queue.
- **IPC**: `duneos_send`/`duneos_recv` route through destination slot's mailbox queue. No shared memory.
- **Permissions**: `duneos_symbol_t.required_perm` bitmask checked against `manifest.permissions` at symbol resolution.
- **`dup`/`dup2`**: cannot safely take their address in newlib — wrapped as `duneos_dup`/`duneos_dup2`.
- **Circular dependency (duneos_kernel ↔ duneos_loader)**: broken via registered function pointers. The kernel never includes `duneos/loader.h`.
- **FreeRTOS stays until Phase 26** (OSAL). Phase 24 (DHI) only removes hardware-peripheral IDF includes from drivers.
- **Kernel error convention**: `int`, return 0 on success, -errno on failure. No `duneos_status_t` enum (ADR 001).

## Hard-Won Lessons (do not repeat these mistakes)

- **Captured apps don't get a per-app heap** — `manifest.heap_size` is consumed by `supervisor_launch` (spawned mode), not by `loader_run_captured`. A captured app's `malloc()` falls back to the global kernel heap (~50-80 KiB free on CardPuter). Large allocations silently fail. **Workaround**: if `heap_size > 0` in manifest, shell spawns instead of capturing.
- **`while (running_count > N) usleep(...)` busy-polls and may hang** — replaced with `duneos_supervisor_wait_for_completion(target_count)` which blocks on a counting semaphore. Apply this pattern any time a task waits for app completion.
- **`duneos_exit()` from a captured app used to kill the caller's task** — `vTaskDelete(NULL)` in a captured context = "kill the current task" = "kill the shell that launched me". Fixed by Phase 24.9.5 (ADR 016): the loader installs a `setjmp` checkpoint before `app_main()` (`s_captured_jmp` in `loader.c`); `duneos_exit()` calls `duneos_supervisor_captured_active()` and longjmps instead of vTaskDelete. Captured-mode invariant: the app body runs in the **shell's task**, with three contracts spelled out in `<duneos/libdune.h>` ("Captured app contract" block): (1) no `pthread_create` — the kernel enforces this; `api_pthread_create` returns `EPERM` when captured. (2) free what you malloc. (3) close what you open. Apps that need any of those should declare `heap_size: <bytes>` in their manifest — the shell auto-dispatches them to spawned mode (their own task) where all three contracts are lifted.
- **`xtensa-esp-elf-gcc` defaults to big-endian Xtensa** — always use `xtensa-esp32s3-elf-gcc` for app builds.
- **`idf.cmakeAdditionalArgs: ["-DDUNEOS_BOARD=..."]` in VS Code silently overrides `.duneos_board`** — keep `idf.cmakeAdditionalArgs` as `[]`.
- **`sdkconfig` at project root caches board-specific settings** — must delete it when switching boards (Full Clean).
- **M5Stack CardPuter = ESP32-S3FN8 = no PSRAM** — bspgen emits `CONFIG_SPIRAM=n` from `board.yaml` (no `psram:` section).
- **FAT SD card returns 8.3 uppercase filenames** — use `strcasecmp` for `.elf` extension filtering.
- **`printf` must NOT be in the export table** — apps call it through newlib → `_write()` → VFS. Exporting it directly bypasses VFS.
- **`vTaskDelay` must NOT be exported** — apps use `nanosleep()`/`usleep()`. FreeRTOS is an implementation detail.
- **`nanosleep` is not in ESP-IDF newlib** — implemented as `duneos_nanosleep()` via `usleep()`.
- **Inline comments in `partitions.csv` are parsed as flags by ESP-IDF** — remove all inline comments.
- **`CMAKE_SOURCE_DIR` is unreliable during ESP-IDF requirements phase** — use `CMAKE_CURRENT_LIST_DIR` instead.
- **`CONFIG_ESP_CONSOLE_USB_CDC=y` conflicts with TinyUSB** — device won't enumerate. Use `console: none` in `board.yaml`; bspgen emits `CONFIG_ESP_CONSOLE_NONE=y`. DuneOS redirects logs to CDC at runtime via `esp_log_set_vprintf`.
- **Never hand-edit bspgen-generated files** — `sdkconfig.board`, `board_config.h`, `partitions.csv`, `idf_target.txt`. Fix the YAML or bspgen, then re-run.
- **Concurrent `tinyusb_cdcacm_write_flush` calls cause "Flush failed"** — all CDC TX must flow through a single mutex with `timeout_ticks=0`. TinyUSB's `cdcd_xfer_cb` auto-flushes remaining FIFO data.
- **UART0 (GPIO43/44) on the CardPuter has no physical header** — inaccessible. Never route klog or console to UART0.
- **`dprintf` through a `va_list`-accepting function pointer is UB in C** — libdune's `dprintf` uses `vsnprintf` + `write()` instead.
- **`__duneos_api_ptr` must be in `.data`, not `.bss`** — `libdune_ptr.c` initialises it to `(const duneos_api_t *)0` explicitly. A BSS symbol has `SHN_COMMON` and the loader's DEFINED-symbol scan skips it.
- **cJSON est vendorisé dans `duneos_loader/src/`** — ESP-IDF v6.0.1 ne fournit plus `json` comme composant intégré. Ne jamais remettre `json` ou `espressif__cjson` dans les REQUIRES du loader.
- **`duneos_slot_info_t` in libdune.h must stay in sync with supervisor.h** — both hardcode `64` instead of `DUNEOS_APP_NAME_MAX`. If the kernel struct changes, both must be updated + `DUNEOS_ABI_VERSION` bumped.
- **Xtensa stack alignment for `xTaskCreateStaticPinnedToCore`** — stack buffer must be 16-byte aligned: `heap_caps_aligned_alloc(16, size, MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT)`. `StaticTask_t` TCB must outlive the task.
- **`RINGBUF_TYPE_BYTEBUF` + counting semaphore is wrong for CDC RX** — `vRingbufferReturnItem` advances by the contiguous block size, not `maxSize`. Bytes after the first in a multi-byte USB packet are silently dropped. Use `xStreamBufferCreate(size, 1)` instead.
- **`static char buf[]` in bin apps breaks `api_read` pointer validation** — static BSS lives in the app's heap pool, outside the shell slot's bounds; `check_app_writable_ptr` returns EFAULT. Use stack-allocated buffers instead.
- **`/dev/spi-N` cannot be shared by two concurrent apps using the same CS pin** — `spi_bus_add_device` warns and overwrites the CS; transactions go to the wrong handle. Don't declare interactive display apps (g_shell, gfx_demo) as daemons in `init.yaml`.
- **Two tasks reading the same `/dev/ttyUSB0` fd steal alternating bytes** — init deduplication must compare by app name (basename without `.dap`), not path. Flash entry wins.
- **DHI phase boundary: FreeRTOS stays until Phase 26** — Phase 24 removes hardware-peripheral IDF includes from drivers. FreeRTOS primitives (`vTaskDelay`, `xTaskCreate`, `freertos/*.h`) are NOT Phase 24 scope.
- **ADC unit ID convention: 1-based in HAL, 0-based in ESP-IDF** — `DUNEOS_BATTERY_ADC_UNIT=1` for ADC1. `hal_adc.c` converts with `(adc_unit_t)(cfg->unit_id - 1)`. Never store the ESP-IDF enum name in `board_config.h`.
- **`duneos_hal_delay_us()` is a hardware busy-wait** — no OS yield. Use only for µs-level delays in drivers. For task delays use `vTaskDelay()`. Never use for delays > ~1 ms.
- **`esp_rom_printf` in exception/WDT handlers is last-resort panic output** — bypasses VFS/UART entirely, works when allocator and scheduler are dead. Do NOT replace with `klog_e()` or `dprintf()`. Phase 26 (OSAL) abstracts it as `osal_panic_print()`.
- **`xTaskGetTickCount() * portTICK_PERIOD_MS` for event timestamps is wrong** — use `(uint32_t)(duneos_hal_monotonic_us() / 1000)` instead (hardware ESP timer, µs precision, no wrap issues).
- **`arch.cmake` guard must check `IDF_TARGET_ARCH`, not just `CONFIG_IDF_TARGET_ARCH_XTENSA`** — `CONFIG_*` vars are empty during the requirements phase. Without `IDF_TARGET_ARCH` in the guard, `DUNEOS_KERNEL_REQUIRES` is never populated and arch-specific headers are missing at compile time.

## Code Style & Constraints

- **Kernel:** C17, no C++, no exceptions, no external dependencies beyond ESP-IDF
- **Tooling** (`dbt.py`, `duneos-bspgen`): Python
- **ABI stability:** any breaking change to exported symbols or struct layouts requires `DUNEOS_ABI_VERSION` bump in `abi.h`
- **No MMU** → strict conventions, Task WDT, stack canaries (pending), per-app exception handlers
- **No comments explaining what code does** — only comments for non-obvious WHY

## References

- **Architecture Decision Records** — [`docs/adr/`](docs/adr/) (17 ADRs as of 2026-05). Authoritative for design intent.
- **Unscheduled ideas** — [`docs/backlog.md`](docs/backlog.md)
- **Contest sprint 2026** — [`docs/contest-2026.md`](docs/contest-2026.md). Freezes phases 26-29 until 2026-09.
- Flipper Zero FAP loader: [flipperzero-firmware](https://github.com/flipperdevices/flipperzero-firmware)
- ESP-IDF VFS: [ESP-IDF VFS docs](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/storage/vfs.html)
