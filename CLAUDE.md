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

# Build the USB shell (system app, ships with DuneOS)
cd apps/system/usb_shell
python ../../../tools/dbt.py build
python ../../../tools/dbt.py deploy E:\   # E: = SD card drive

# Build a DuneOS app
cd apps/user/test_exit
python ../../../tools/dbt.py build
python ../../../tools/dbt.py deploy E:\   # E: = SD card drive

# Inspect built ELF
python ../../../tools/dbt.py info

# BSP generator (legacy positional usage retained, but normal usage is the form on line 23)
python tools/duneos-bspgen.py boards/esp32s3-devkitc/board.yaml

# Build sysbin image + flash it (LittleFS image with /flash/init.yaml + embedded apps)
python tools/dbt.py flashimg                # uses .duneos_port

# Interactive TUI (board picker, init editor, build/flash actions)
python tools/dbt.py tui
```

## Repository Structure

```text
DuneOS/
├── main/
│   └── main.c                      # Kernel entry: VFS init → supervisor init → launch → wait
├── kernel/
│   ├── duneos_kernel/
│   │   ├── include/duneos/
│   │   │   ├── abi.h               # ABI: DUNEOS_ABI_VERSION, duneos_symbol_t, duneos_app_manifest_t
│   │   │   ├── init.h              # Init system: duneos_init_load(), duneos_service_desc_t
│   │   │   ├── klog.h              # klog_i/w/e/d macros, ring buffer API
│   │   │   ├── supervisor.h        # Multi-app supervisor: launch/launch_policy/send/recv/wait_all
│   │   │   ├── task.h              # FreeRTOS abstraction
│   │   │   └── vfs.h               # VFS init/mount API
│   │   ├── Kconfig                 # CONFIG_DUNEOS_DRV_* driver selection options
│   │   └── src/
│   │       ├── init.c              # Parse /flash/init.yaml then /sd/init.yaml, return service list (Phase 17 migration; minimal YAML reader)
│   │       ├── klog.c              # 4 KB ring buffer, /dev/klog backend, ESP_LOGx forwarding
│   │       ├── supervisor.c        # 4-slot app supervisor, restart policies, IPC queues
│   │       ├── task.c
│   │       ├── vfs.c               # SD (SPI+FatFS) mount, registers /tmp and /dev
│   │       ├── vfs_tmp.c           # /tmp — heap-backed RAM filesystem, 16 inodes/fds
│   │       ├── vfs_dev.c           # /dev pure VFS router; calls drv_*_register() per Kconfig
│   │       ├── symbols.c           # POSIX export table with per-symbol permission gates
│   │       └── drivers/            # One file per device — compiled only when CONFIG_DUNEOS_DRV_*=y
│   │           ├── drv_null.c      # /dev/null, /dev/zero
│   │           ├── drv_uart.c      # /dev/uart0
│   │           ├── drv_klog.c      # /dev/klog (ring buffer reader)
│   │           ├── drv_i2c.c       # /dev/i2c-0 (raw I2C master)
│   │           ├── i2c_bus.c/h     # Shared I2C bus handle (used by drv_i2c + bq27220)
│   │           ├── gpio/
│   │           │   └── drv_gpio.c  # /dev/gpiochip0
│   │           └── battery/
│   │               ├── drv_battery_adc_simple.c  # /dev/battery0 — ADC voltage divider
│   │               └── drv_battery_bq27220.c     # /dev/battery0 — BQ27220 I2C fuel gauge
│   └── duneos_loader/
│       ├── include/duneos/
│       │   ├── loader.h            # scan/select/load/run/unload/run_captured API
│       │   └── elf.h               # ELF32 structs and Xtensa relocation constants
│       └── src/
│           └── loader.c            # Full ET_REL loader: parse, relocate, resolve, run
├── arch/                           # Architecture-specific code (no SDK deps)
│   ├── xtensa_esp32s3/
│   │   ├── hal/                    # HAL stubs — Phase 24 (DHI) implementation target
│   │   └── reloc/
│   │       └── loader_reloc_xtensa.c  # STUB — Phase 28 extraction target (extracts ~150 lines from loader.c)
│   └── riscv32/
│       ├── hal/                    # STUB — Phase 28 (ESP32-C6 + P4)
│       └── reloc/
│           └── loader_reloc_riscv.c   # STUB — Phase 28 (R_RISCV_32, HI20/LO12, CALL, BRANCH, JAL)
├── third_party/                    # Portable C libs — git submodules, no ESP-IDF managed
│   ├── cjson/                      # DaveGamble/cJSON v1.7.19 — compiled directly into loader
│   └── littlefs/                   # littlefs-project/littlefs v2.11.3 — compiled into kernel
├── libdune/                        # Userspace POSIX wrapper library (built by dbt — libdune.a)
│   └── src/                        # NB: the public header lives in kernel/duneos_kernel/
│       ├── libdune_ptr.c           # __duneos_api_ptr = (duneos_api_t *)0  → .data anchor
│       ├── libdune_fs.c            # read/write/open/close/ioctl/dprintf + opendir/readdir
│       ├── libdune_mem.c           # malloc/free/realloc/calloc → kernel api.mem
│       ├── libdune_thread.c        # pthreads + POSIX semaphores → kernel api.thread
│       ├── libdune_time.c          # clock_gettime/gettimeofday/usleep/sleep/nanosleep
│       └── libdune_sys.c           # duneos_exit, IPC, supervisor, loader, __errno bridge
├── boards/                         # All per-board files except board.yaml are bspgen-generated
│   ├── esp32s3-devkitc/
│   │   ├── board.yaml              # source of truth — hand-edited
│   │   ├── board_config.h          # generated: SD SPI pins, PSRAM config, DUNEOS_HAS_SD
│   │   ├── sdkconfig.board         # generated: 8 MB flash, OPI PSRAM, CONFIG_DUNEOS_DRV_*
│   │   ├── partitions.csv          # generated: factory + sysbin (LittleFS) + storage (FAT)
│   │   ├── idf_target.txt          # generated: "esp32s3" — read by CMakeLists.txt
│   │   └── init.yaml               # hand-edited: /flash/init.yaml — boot services
│   ├── m5stack-cardputer/          # same layout — SD pins MOSI=14, MISO=39, CLK=40, CS=12; no PSRAM
│   ├── lilygo-t-embed-cc1101/      # same layout — 16 MB flash, 8 MB OPI PSRAM, BQ27220 fuel gauge
│   ├── esp32c3-devkitc/            # same layout — RISC-V, future Phase 28 target
│   └── esp32p4-devkitm/            # same layout — RISC-V, future Phase 28 target
├── apps/
│   ├── system/                     # apps that ship with DuneOS — maintained by the project
│   │   ├── shell_core/
│   │   │   └── shell_core.c        # Shared VT100 shell engine; #include'd by backends
│   │   ├── usb_shell/              # USB CDC shell — deploy on boards with CONFIG_DUNEOS_DRV_USB_CDC
│   │   │   ├── usb_shell.c         # Opens /dev/ttyUSB0, calls shell_run()
│   │   │   └── duneos.yaml
│   │   ├── wifi_daemon/            # WiFi STA daemon — connects, holds the link, reconnects on backoff
│   │   └── bin/                    # System utilities (ls, cat, free, klog, gpio, ifconfig, ping, …)
│   │       └── <cmd>/
│   │           ├── <cmd>.c
│   │           └── duneos.yaml
│   └── user/                       # third-party / developer demo apps (template for new apps)
│       ├── g_shell/                # Graphical shell — any board with display + keyboard
│       │   ├── g_shell.c           # ST7789 terminal, evdev input, captured bin output
│       │   ├── font8x8.h           # 8×8 pixel font
│       │   └── duneos.yaml
│       ├── gfx_demo/               # GFX API demo (shapes, text) for boards with display
│       ├── test_exit/              # Minimal app: app_main calls duneos_exit(0)
│       ├── test_hardening/         # Memory/WDT hardening test
│       ├── hello_world/            # Writes "Hello World" to stdout then exits
│       ├── uart_echo/              # Echo loop on /dev/uart0, Ctrl-C to exit
│       └── tcp_client/             # TCP socket demo (requires wifi_daemon running)
├── tools/
│   ├── dbt.py                      # thin wrapper → tools/dbt/cli.py
│   ├── dbt/                        # DuneBuild package: cli/builder/deploy/kernel/flashimg/bspgen/manifest/tui
│   │   ├── cli.py                  # entry point (commands: build, deploy, info, flashimg, monitor, tui …)
│   │   ├── tui.py                  # Textual TUI (board/port picker, init config, build/flash actions)
│   │   ├── toolchain/              # SDK plugins — esp_idf.py today; pico_sdk.py etc. future
│   │   └── …
│   └── duneos-bspgen.py            # BSP generator: board.yaml → board_config.h + sdkconfig.board + partitions.csv + idf_target.txt
├── .duneos_board                   # Active board name (gitignored)
├── .duneos_port                    # Active serial port (gitignored) — read by dbt flashimg
├── .duneos_sd                      # Active SD mount path (gitignored) — written by TUI
├── .duneos_idf                     # ESP-IDF root override (gitignored) — read by esp_idf plugin
├── .vscode/settings.json           # idf.portWin=COM13, flashType=UART
└── sdkconfig.defaults              # Common Kconfig (FreeRTOS HZ, log level). Board-specific entries live in boards/<board>/sdkconfig.board.
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
│               │    /tmp  — RAM tmpfs             │  vfs_tmp.c
│               │    /dev/null, /dev/zero          │  vfs_dev.c
│               │    /dev/uart0, /dev/klog         │
│               │    /dev/gpiochip0  (Phase 8)     │  native GPIO ioctl
│               │    /dev/i2c-0      (Phase 8)     │  raw I2C bus
│               │    /dev/spi-1      (Phase 8)     │  raw SPI bus
│               │    /dev/fb0        (Phase 8)     │  framebuffer
│               │    /dev/input/event0 (Phase 8)   │  evdev-style input
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
**Do not add `-DDUNEOS_BOARD=` to `idf.cmakeAdditionalArgs` in `.vscode/settings.json`** —
it is silently overridden by the file read and causes confusion.

```bash
echo m5stack-cardputer > .duneos_board   # switch board
# Then do Full Clean before building (VS Code: Ctrl+Shift+P → ESP-IDF: Full Clean)
```

CMake writes `build/duneos_last_board.stamp` and aborts with FATAL_ERROR if the board changes
without a Full Clean — this prevents the sdkconfig drifting silently out of sync.

`IDF_TARGET` is read from `boards/<board>/idf_target.txt` (generated by `duneos-bspgen.py`),
so it does not need to be set manually in VS Code settings.

### Kernel driver selection model

The kernel binary is **board-specific**: same source tree, different binary per board (like Linux with
Kconfig). `duneos-bspgen.py` generates three artefacts from a board YAML:

1. `board_config.h` — pin assignments and hardware constants (`DUNEOS_*` defines)
2. `idf_target.txt` — single-line IDF_TARGET value read by CMakeLists.txt
3. `sdkconfig.board` — Kconfig fragment with `CONFIG_DUNEOS_DRV_*=y` entries

Driver selection uses `CONFIG_DUNEOS_DRV_*` Kconfig options (defined in
`kernel/duneos_kernel/Kconfig`). Each driver lives in its own file under
`src/drivers/` and is compiled only when the matching option is set:

```text
src/drivers/
  drv_null.c                      CONFIG_DUNEOS_DRV_NULL=y      (always)
  drv_uart.c                      CONFIG_DUNEOS_DRV_UART=y      (always)
  drv_klog.c                      CONFIG_DUNEOS_DRV_KLOG=y      (always)
  gpio/drv_gpio.c                 CONFIG_DUNEOS_DRV_GPIO=y
  i2c_bus.c, drv_i2c.c           CONFIG_DUNEOS_DRV_I2C=y
  battery/drv_battery_adc_simple.c  CONFIG_DUNEOS_DRV_BATTERY_ADC_SIMPLE=y
  battery/drv_battery_bq27220.c     CONFIG_DUNEOS_DRV_BATTERY_BQ27220=y
```

`i2c_bus.c` is a shared internal module (not a driver itself) providing a mutex-protected
`i2c_bus_write_read()` used by both `drv_i2c.c` and `drv_battery_bq27220.c`. It is compiled
whenever either `CONFIG_DUNEOS_DRV_I2C` or `CONFIG_DUNEOS_DRV_BATTERY_BQ27220` is set.

### Adding a new kernel driver

1. Create `src/drivers/<category>/drv_<name>.c` — implement `duneos_dev_driver_t` + export `drv_<name>_register()`
2. Add `config DUNEOS_DRV_<NAME>` to `kernel/duneos_kernel/Kconfig`
3. Add conditional `list(APPEND DUNEOS_KERNEL_SRCS ...)` to `kernel/duneos_kernel/CMakeLists.txt`
4. Add `#ifdef CONFIG_DUNEOS_DRV_<NAME>` block to `vfs_dev.c` (forward decl + call in mount)
5. Enable it for a board: edit `boards/<board>/board.yaml` (add the relevant section, e.g. `i2c:`, `battery:`) then re-run `python tools/duneos-bspgen.py boards/<board>/board.yaml`. Bspgen emits `CONFIG_DUNEOS_DRV_<NAME>=y` into the generated `sdkconfig.board`. Never hand-edit `sdkconfig.board` — it is overwritten by bspgen.
6. Update `tools/duneos-bspgen.py` to emit the config entry when the matching YAML section is present

### Adding a new target architecture

**Zero changes to `duneos_kernel/CMakeLists.txt`.**

1. Create `arch/<new_arch>/arch.cmake` — self-selects via dual guard (see below)
2. In `arch.cmake`: append HAL source files to `DUNEOS_KERNEL_SRCS` and SDK component deps to `DUNEOS_KERNEL_REQUIRES`
3. Implement `arch/<new_arch>/hal/hal_uart.c`, `hal_gpio.c`, `hal_i2c.c`, `hal_spi.c`, `hal_adc.c`, `hal_time.c` — conforming to the pure-C headers in `include/duneos/`
4. Add `arch/<new_arch>/reloc/loader_reloc_<arch>.c` — RELA `apply()` function for the loader
5. Add `tools/dbt/toolchain/<sdk>.py` plugin for the new SDK/arch combination — must set `DUNEOS_ARCH` and `DUNEOS_BOARD_SOC` CMake variables before the build

`duneos_kernel/CMakeLists.txt` discovers all `arch/*/arch.cmake` via `file(GLOB)` and `include()`s them. Each `arch.cmake` calls `return()` if its guard is not met. No arch names are hardcoded anywhere in the core kernel build.

**`arch.cmake` guard pattern — always check all three:**

```cmake
# Three guards for three contexts:
#   CONFIG_IDF_TARGET_ARCH_XTENSA — set from sdkconfig.cmake in normal ESP-IDF build phase
#   IDF_TARGET_ARCH               — set via __build_write_properties in ESP-IDF requirements
#                                   phase (sdkconfig.cmake is NOT loaded there, so CONFIG_*
#                                   vars are empty — this is why a two-variable guard fails)
#   DUNEOS_ARCH                   — set by non-ESP-IDF toolchain plugins (dbt, pico-sdk, …)
if(NOT CONFIG_IDF_TARGET_ARCH_XTENSA
   AND NOT IDF_TARGET_ARCH STREQUAL "xtensa"
   AND NOT DUNEOS_ARCH STREQUAL "xtensa_esp32s3")
    return()
endif()
```

`DUNEOS_ARCH` must be set by the toolchain plugin via `-DDUNEOS_ARCH=<name>` before CMake configure. Without it, only ESP-IDF builds work.

**Critical:** `IDF_TARGET_ARCH` (written to `build_properties.temp.cmake` by `__build_write_properties`) is the ONLY arch signal available during the ESP-IDF requirements phase. Without it in the guard, `list(APPEND DUNEOS_KERNEL_REQUIRES ...)` in arch.cmake is never executed during requirements resolution, and arch-specific component headers (e.g. `esp_timer.h`) are missing from the build.

**ARM Cortex-M — unified arch directory:**

M0+, M4, M4F, M7 share ARM Thumb-2 relocations. Use `arch/arm_cortex_m/` for all:

```
arch/arm_cortex_m/
    arch.cmake                    # guard: DUNEOS_ARCH STREQUAL "arm_cortex_m"
    reloc/loader_reloc_arm.c      # shared for all ARM Thumb-2
    hal/
        rp2040/                   # HAL via pico-sdk
        stm32h7/                  # HAL via STM32CubeHAL
        nrf52/                    # HAL via nRF5 SDK
        samd51/                   # HAL via CMSIS/ASF4
```

`arch.cmake` uses `include("${CMAKE_CURRENT_LIST_DIR}/hal/${DUNEOS_BOARD_SOC}/hal_sources.cmake")` to select the SoC HAL. The toolchain plugin must also set `DUNEOS_BOARD_SOC`.

### Third-party library strategy

DuneOS uses **two tiers** for external C libraries, chosen by portability need:

| Tier | Mechanism | When to use |
| --- | --- | --- |
| **`third_party/` git submodule** | Compiled directly into the consuming component via its `CMakeLists.txt` | Pure-C libs with no ESP-IDF dependency (cJSON, LittleFS, miniz…) |
| **ESP-IDF managed component** | `idf_component.yml` in the component directory | ESP-specific or hardware-tied libs (TinyUSB, esp_tinyusb, lcd drivers…) |

**Rule:** if a library compiles cleanly with `xtensa-esp32s3-elf-gcc` alone (no IDF headers), put it in `third_party/`. This keeps the kernel and loader portable for non-ESP targets (Phase 24 onward).

Current submodules:
- `third_party/cjson` — DaveGamble/cJSON v1.7.19 — compiled into `duneos_loader`
- `third_party/littlefs` — littlefs-project/littlefs v2.11.3 — compiled into `duneos_kernel`

Managed components stay in `duneos_hal_esp32s3/` (Phase 24) — never in `duneos_kernel` or `duneos_loader` core.

### Kernel vs userspace hardware boundary

**Decision rule:**

| Put it in kernel if | Put it in userspace if |
| --- | --- |
| Shared between multiple concurrent apps | Used by at most one app at a time |
| Requires a hardware ISR | Pure register-level protocol over a bus |
| Board-global resource (ADC, bus controller) | Per-chip protocol (ST7789, CC1101, MPU6050…) |
| Small runtime state (tens of bytes) | RAM cost too high for DRAM-only boards |

**In the kernel (`CONFIG_DUNEOS_DRV_*=y`, compiled per board):**

- Bus controllers: I2C master, SPI master, UART — one instance per physical bus
- Native GPIO — `/dev/gpiochip0`; GPIO expanders declared in BSP YAML — `/dev/gpiochip1..N`
- Battery monitoring — `/dev/battery0` (ADC + GPIO pins, or I2C fuel gauge, backend from BSP YAML)
- Framebuffer — `/dev/fb0` **only when `CONFIG_SPIRAM=y`** (240×135×2 = 63 KB — dealbreaker in DRAM)

**In userspace (SDK libraries linked into the app, no kernel changes per chip):**

- Display drivers (ST7789, GC9A01, SSD1306) — app links `libst7789.c` from SDK, opens `/dev/spi-1`
- I2C sensors and peripherals (MPU6050, SHT31 …) — app opens `/dev/i2c-0`, speaks register protocol
- SPI radio modules (CC1101, SX1276/LoRa, NRF24L01) — app opens `/dev/spi-1`
- WiFi connection management — `wifi_daemon.dap` service, uses existing lwIP socket exports

**Portability consequence:** a `.dap` app that links `libst7789.c` is tied to that display chip and
will not run unchanged on a board with a different display. Phase 15 (`libgfx`) will provide a
display-agnostic API with pluggable backends to restore source-level portability.

## Implementation Status

| Phase | Status | Description |
| --- | --- | --- |
| Phase 1 — Kernel foundations | **DONE** | Boot, SD mount, ELF scan, load, relocate, run |
| Phase 2 — VFS & POSIX completeness | **DONE** | `/tmp`, `/dev/null/zero/uart0`, ioctl, dprintf, stdout capture |
| Phase 3 — Kernel logging overhaul | **DONE** | klog ring buffer, `/dev/klog`, `CONFIG_DUNEOS_KERNEL_SILENT`, ESP_LOGx removed |
| Phase 4 — Loader hardening & multi-app | **DONE** | DIFF32 relocs, supervisor, 4-slot multi-app, IPC, permissions, `.dap` extension |
| Phase 5 — Shell | **DONE** | `system/shell/` — interactive POSIX shell app with history and line editing |
| Phase 6 — BSP generator | **DONE** | `tools/duneos-bspgen.py` — YAML → board_config.h; lilygo-t7-s3.yaml reference |
| Phase 7 — App SDK & DX | **DONE** | `dbt.py info` footprint, `dbt.py deploy` → `.dap`, `hello_world`, `uart_echo` demos |
| Phase 8 — GPIO | **DONE** (native) | `/dev/gpiochip0` via `esp_driver_gpio`; `gpio_ioctl.h` SDK header; shell `gpio` command; expander support pending |
| Phase 9 — Init system | **DONE** | `/sd/init.json` (cJSON), supervisor restart policies (`no`/`always`/`on-failure`), `duneos_service_ready()`; autoboot fallback retained |
| Phase 10 — I2C + battery | **DONE** | `/dev/i2c-0`, `/dev/battery0` (adc_simple for CardPuter, bq27220 for T-Embed CC1101) |
| Phase 11 — SPI | **DONE** | `/dev/spi-1` (SPI3_HOST); per-fd `spi_bus_add_device`; `role: raw` in BSP YAML selects the raw bus; boards without a free SPI host omit the config flag |
| Phase 12 — Input | **DONE** | `/dev/input/event0`; IOMatrix scan (CardPuter), GPIO buttons + quadrature encoder (T-Embed); 3-layer keymap (normal/shift/fn); `DUNEOS_PERM_INPUT`; shell `input` + `tail` commands |
| Phase 13 — Framebuffer + display SDK | **DONE** | `/dev/disp0` streaming driver (all boards); `/dev/fb0` PSRAM back-buffer (T-Embed); `st7789_hw.c` shared HW module; `disp_ioctl.h` POSIX API; `g_shell` graphical terminal (30×16 8×8 font, stdout capture, PATH bin execution) |
| Phase 14 — WiFi daemon + raw frame injection | **DONE** | `drv_wifi.c` kernel wrappers; `wifi_daemon.dap` (STA, backoff, reconnect); `duneos_netif_wait_ip()`; `/dev/raw80211` (`DUNEOS_PERM_NET_RAW`); `apps/system/bin/ifconfig`; BSD socket exports |
| Phase 15 — Multi-target portability | Superseded by 18 | Merged into Phase 18 (libgfx + board.info). |
| Phase 16 — Shell refactor: built-ins vs PATH | **DONE** | Shell stripped to 6 built-ins (`cd`, `pwd`, `echo`, `exit`, `help`, `run`). 14 commands moved to `apps/system/bin/` as `.dap` files. Args via `/tmp/.exec_args` (`bin_args.h`). `dbt.py deploy --bin` copies to `/sd/bin/`. |
| Phase 17 — Tooling DX | **DONE** | `dbt.py` split into sub-modules (`cli`, `builder`, `deploy`, `toolchain`, `manifest`); `duneos.yaml` replaces `manifest.json`; `init.yaml` replaces `init.json`; `bspgen.py` purged of ESP-IDF dep. |
| Phase 18 — libgfx (display portability) | **DONE** | `/flash/board.info` (+ `/sd/board.info`) written at boot; `libgfx` display-agnostic API with `gfx_st7789` and `gfx_fb` backends; `dbt.py` selects backend from `.duneos_board`. |
| Phase 19 — Flash storage (boot sans SD) | **DONE** | `sysbin` LittleFS partition (1 MB) at `/flash`; boot order: flash → SD; `duneos_vfs_provision_flash()` copies firmware-embedded blobs to `/flash/bin/`; init.yaml cascade (`/flash` then `/sd`); loader cascade (`/flash/bin/` → `/sd/bin/` → `/sd/apps/`); `bspgen` generates per-board `partitions.csv` from `flash_size_mb`; `DUNEOS_HAS_SD` flag; `dbt.py flashimg` builds LittleFS image and flashes directly (port from `.duneos_port` / `--port` / `DUNEOS_PORT`). `boards/<board>/init.yaml` user-controlled per-board flash boot service list (replaces hardcoded `_BOOT_SERVICES`); init.c deduplication by app name (not path) so `/flash/bin/usb_shell.dap` and `/sd/bin/usb_shell.dap` resolve to the same service. |
| Phase 20 — Memory hardening | **PARTIAL** | DONE: per-app heap caps (DRAM pool, `heap_caps_malloc`); software WDT per slot (`esp_task_wdt`); per-app Xtensa exception handler in `supervisor.c` (kill on crash, no kernel reboot); supervisor pointer validation (`check_user_ptr` for read buffers, `check_app_writable_ptr` for write buffers) used by `api.c` and `symbols.c`. TODO: stack canary per task; TLSF userspace allocator in `libdune.a` (today `malloc` calls kernel `heap_caps_malloc` via `__duneos_api_ptr->mem`). |
| Phase 21 — dbt: multi-arch toolchain plugin model | **DONE** | `board.yaml` gains `arch:` + `sdk:` fields (all boards updated). `tools/dbt/toolchain/` package replaces `toolchain.py`: `__init__.py` exposes `load_plugin(sdk)` + `get_board_plugin()`; `esp_idf.py` is the first plugin (SDK/ARCH constants, `find_compiler`, `cflags`, `ldflags`, `build_kernel`, `flash_kernel`, `monitor`, `find_toolchain_root`). `builder.py`, `cli.py`, `flashimg.py`, `kernel.py` dispatch through `get_board_plugin()`. |
| Phase 22 — Syscalls + PicoLibc migration | **DONE** | `duneos_api_t` typed dispatch table (ABI v3) in `duneos/api.h`; `api.c` owns the singleton. Loader injects `duneos_api_get()` into app's `__duneos_api_ptr` before `app_main` — O(1), no string search. Static stack alloc gives exact bounds for `check_app_writable_ptr`. cJSON manifest parser. `libdune.a` (6 sources) wraps POSIX + DuneOS calls; `dbt build` caches it per arch. |
| Phase 23 — USB Device Subsystem | **DONE** | `espressif/esp_tinyusb ^2.0.1~1` managed component; `drv_usb.c` TinyUSB MSC+CDC composite device; `drv_usb_cdc.c` mutex-serialised TX (no concurrent flush collisions) + semaphore-gated RX → `/dev/ttyUSB0`; `apps/system/usb_shell/` + `apps/system/shell_core/` replace `system/shell/`; `bspgen.py` adds `console: none` → `CONFIG_ESP_CONSOLE_NONE=y` (default for OTG boards — keeps UART0 free for apps; klog redirected to CDC at runtime via `esp_log_set_vprintf`); `board.yaml` gains `usb:` section. |
| Phase 24 — DHI (DuneOS Hardware Interface) | **DONE** (HAL scope) | 6 pure-C HAL headers (`hal_uart`/`gpio`/`i2c`/`spi`/`adc`/`time`.h), ESP-IDF impl in `arch/xtensa_esp32s3/hal/`, all HW driver backends + input drivers delegate to HAL, `dev_driver.h`+`i2c_bus.h` return `int`, `arch.cmake` self-selection via triple-guard, `arch` in manifest + cross-ISA rejection, numeric SPI host / ADC unit in BSP. **Scope restricted to HAL hardware headers**: the 4 core public headers (`init.h`, `task.h`, `vfs.h`, `supervisor.h`) still return `esp_err_t` but their migration is distributed to Phases 26-27 (each header migrated when its underlying API actually changes — avoids throwaway translation code). |
| Phase 24.5 — Design Decisions (ADR) | **DONE** | `docs/adr/` — 16 short ADRs (1 page each, ~50 lines): process, error model (`int`/-errno), OSAL API (static-storage handles only), memory caps (`OSAL_MEM_*` with silent DRAM fallback), task priorities (5 symbolic levels), path conventions (`/flash` mandatory), manifest extensibility (unknown fields ignored), multi-arch smoke test, memory fragmentation (4 pillars + no compaction), driver kernel/userspace boundary, architecture-specific accelerators (Pattern A capability HAL), threat model (permissions advisory, no MMU isolation), test strategy (Greatest header-only, host-side first), network architecture (POSIX sockets + vendored lwIP + per-medium HAL), **capability-based source resolution** (apps declare `capabilities: [display]`, dbt resolves `lib${board.display.driver}.c` automatically — app manifests stay board-portable). All consumed by Phases 24 debt + 24.7 + 25-29. |
| Phase 24.7 — Safe boot & recovery | Not started | Avoid bricking-by-bad-init.yaml. Circuit breaker in supervisor (N crashes in M sec → disable service), hold-key-at-boot fallback via `board.yaml` `recovery_pin`, guarantee USB MSC up before init.yaml services, `dbt flash sysbin --safe` for one-shot recovery. Prerequisite for Phase 25 (`dbt system deploy` could otherwise brick remote boards). |
| Phase 24.8 — `<duneos/board.h>` auto-generated by dbt (ADR 015 P2) | Not started | dbt generates `build/<app>/_board.h` from `board.yaml` + capabilities. Apps include `<duneos/board.h>` and use defines (`DUNEOS_DISPLAY_DEV`, `DUNEOS_INPUT_DEV`, …) instead of hardcoded `/dev/disp0`. Moving a chip to a different bus = one yaml edit, no SDK rewrite. Eliminates runtime `/flash/board.info` parsing in `libst7789.c` etc. |
| Phase 24.9 — Kernel driver self-registration (ADR 015 P1) | Not started | `DUNEOS_DRIVER_REGISTER()` macro + `.duneos_driver_init` ELF section + linker script update. `vfs_dev.c` becomes a loop over `__duneos_drivers_start..__duneos_drivers_end` — zero `#ifdef`, zero hardcoded driver list. Adding a kernel driver: 1 macro + 1 Kconfig + 1 CMakeLists line. **No more vfs_dev.c edits.** |
| Phase 28.5 — ESP-IDF de-coupling (entry + build root + dbt) | Not started | Split `main/main.c` into `kernel/duneos_kernel/src/duneos_main.c` (SDK-agnostic core) + `arch/<arch>/entry/entry_<sdk>.c` (per-SDK wrapper providing `app_main()` / `int main()`). Root `CMakeLists.txt` dispatches to per-SDK setup via `tools/dbt/toolchain/<sdk>/setup.cmake`. `sdkconfig.defaults` documented as ESP-IDF-only. dbt audit: extract IDF-specific code from `setup.py` (61 refs), `tui.py` (51 refs), `flashimg.py` (12 refs) into plugin methods (`setup_wizard`, `flash_partition`, `build_env`, `version`). Hard prerequisite for Phase 29 (first non-ESP-IDF port). |
| Phase 25 — dbt system (Image Recipes) | Not started | `profile.yaml` (kernel feature selection), `system.yaml` (apps + profile), `capability_map.py` (`DUNEOS_PERM_*` ↔ `CONFIG_DUNEOS_DRV_*`). `dbt system check` validates app permissions against kernel profile before build. `dbt system build/deploy` orchestrates full image. |
| Phase 26 — OSAL + scheduler portability **(was 27)** | Not started | `duneos_osal.h` abstracts FreeRTOS primitives (per ADR 002). Per ADRs 003-004: memory caps + priority scale. `freertos_osal.c` (ESP32-S3/C6/RP2040), `pthread_osal.c` (sim Linux). PicoLibc moved to `third_party/`. WiFi blob confinement behind `freertos_osal.c`. **Includes `task.h`+`supervisor.h` migration to `int`/-errno** (Phase 24 dette héritée). **Migrations: `loader.c`** (`heap_caps_malloc(MALLOC_CAP_SPIRAM/INTERNAL)` → `osal_mem_alloc`, `esp_rom_printf` → `osal_panic_print`, `soc/soc.h` → arch-provided memmap struct), `supervisor.c`, `task.c`, `symbols.c`, `klog.c`, `api.c`, `vfs_dev.c`, `vfs_tmp.c`, `drv_fb_st7789.c`. |
| Phase 27 — VFS native + networking **(was 26)** | Not started | Native `duneos_vfs` replacing `esp_vfs`; `poll()`/`select()` support backed by `osal_sem` wait queues; `hal_net.h` then WiFi driver rewrite; Ethernet RMII + LwIP; BSD socket routing. **Includes `vfs.h` migration to `int`/-errno** (Phase 24 dette héritée). `vfs.c`+`st7789_hw.c` migrate from `driver/spi_master.h` to `hal_spi.h` here. |

**Current state (2026-05-19 audit + roadmap re-sequencing):** Phases 1–14, 16–19, 21–24 fully implemented (Phase 24 on its HAL scope; the 4 core public headers still using `esp_err_t` are migrated organically in Phases 26/27, not as a separate "24b"). Phase 20 is **PARTIAL** — heap/WDT/exception/pointer-validation done, stack canary + TLSF pending. Boot: kernel always mounts `/flash` (LittleFS, fatal if missing); SD is secondary and optional. `init.yaml` is read first from `/flash/init.yaml`, then `/sd/init.yaml`. App scan: `/flash/bin/` → `/sd/bin/` → `/sd/apps/`. Each board has a bspgen-generated `partitions.csv` sized to its `flash_size_mb`. `dbt flashimg` builds and flashes the sysbin LittleFS image in one command. On OTG boards, TinyUSB exposes SD as MSC + a virtual CDC console at `/dev/ttyUSB0` (`usb_shell.dap` runs on it). `console: none` in `board.yaml` emits `CONFIG_ESP_CONSOLE_NONE=y` — UART0 stays free for apps; klog is redirected to CDC at runtime via `esp_log_set_vprintf`. Phase 22 (ABI v3, libdune.a — 6 sources) is done. **Roadmap re-sequenced 2026-05**: Phase 26 (was VFS+net) and Phase 27 (was OSAL) have been swapped — OSAL is now Phase 26 (smaller surface, unlocks `dbt sim`, validates abstraction on ESP32-C6 before risky VFS rewrite). Phase 24.5 (ADRs) inserted before Phase 25 to freeze design decisions (error model, OSAL API, memory caps, priorities) once and for all. **Kernel error convention acted by ADR 001: `int` style POSIX, return 0 on success, -errno on failure. No `duneos_status_t` enum.**

## Multi-arch extensibility model

The goal: an external maintainer can add a new board — or a new SDK family — **without touching core dbt or kernel code**.

### Two independent axes

| Axis | Determined by | Affects |
| --- | --- | --- |
| **arch** (`xtensa-esp32s3`, `arm-cortex-m0plus`, `riscv32`) | `board.yaml → arch:` | Compiler prefix, CFLAGS, ELF relocation constants in loader |
| **sdk** (`esp-idf`, `pico-sdk`, `stm32-hal`, `wch-sdk`) | `board.yaml → sdk:` | Build system invocation, flash tool, monitor tool, kernel HAL implementation |

These are separate. Two boards can share an `arch` (ESP32-S3 + ESP32-C3 both RISC-V eventually) but different `sdk`. Or share an `sdk` (two STM32 boards) but different `arch` (M4 vs M33).

### dbt side — `tools/dbt/toolchain/` plugin directory

```
tools/dbt/toolchain/
  __init__.py        # load_plugin(arch, sdk) → plugin module
  esp_idf.py         # SDK=esp-idf  / ARCH=xtensa-esp32s3  (exists today, refactor target)
  pico_sdk.py        # SDK=pico-sdk / ARCH=arm-cortex-m0plus (Phase 23)
  stm32_hal.py       # SDK=stm32-hal / ARCH=arm-cortex-m*   (future)
  wch_sdk.py         # SDK=wch-sdk  / ARCH=riscv32           (future)
```

Each plugin is a plain Python module exporting:

```python
SDK      = "esp-idf"                  # matched against board.yaml sdk:
ARCH     = "xtensa-esp32s3"           # matched against board.yaml arch:
COMPILER = "xtensa-esp32s3-elf-gcc"   # binary name; searched in PATH + known install dirs

def cflags(board_cfg: dict) -> list[str]: ...       # per-board flags from board.yaml fields
def ldflags(board_cfg: dict) -> list[str]: ...
def linker_script(board_dir: Path) -> Path: ...     # returns path to the .ld file
def build_kernel(board_dir: Path, build_dir: Path, port: str | None) -> int: ...
def flash_kernel(build_dir: Path, port: str, baud: int) -> int: ...
def monitor(port: str) -> None: ...                 # may call with self.suspend()
def find_toolchain_root() -> Path | None: ...       # locate SDK install (IDF root, pico-sdk, etc.)
```

`builder.py` and `cli.py` never import a specific plugin — they call `load_plugin(arch, sdk)` which discovers the module by name convention. Adding a new SDK = dropping one `.py` file.

### Kernel side — architecture actually shipped (Phase 24)

The HAL is **not** a separate kernel component as the V1 sketch suggested. It lives under `arch/<arch>/hal/` with one `.c` per peripheral, and the public headers (`hal_uart.h`, `hal_gpio.h`, …) sit in `kernel/duneos_kernel/include/duneos/`. The kernel component depends on the HAL headers and on `arch.cmake` which appends the right `.c` files to the build per active arch.

```
kernel/
  duneos_kernel/
    include/duneos/
      hal_uart.h, hal_gpio.h, hal_i2c.h, hal_spi.h, hal_adc.h, hal_time.h   # pure C, no SDK headers
    src/                          # kernel core — uses HAL via duneos/hal_*.h
  duneos_loader/
    src/loader.c                  # arch-agnostic dispatch — still has Xtensa reloc inline today;
                                  # Phase 28 extracts it via duneos_arch_ops_t (see arch/*/reloc/)

arch/
  xtensa_esp32s3/
    arch.cmake                    # self-selects on CONFIG_IDF_TARGET_ARCH_XTENSA / IDF_TARGET_ARCH / DUNEOS_ARCH
    hal/hal_*.c                   # implements duneos/hal_*.h via ESP-IDF
    reloc/loader_reloc_xtensa.c   # STUB — Phase 28 extraction target
    exception/exc_xtensa.c        # PLANNED Phase 28 — extracted from supervisor.c
  riscv32/                        # PLANNED Phase 28 (ESP32-C6, ESP32-P4)
  arm_cortex_m/                   # PLANNED Phase 29 (RP2040 and beyond)
```

The HAL exposes only what the kernel needs across architectures: uart write/read, i2c write_read, spi transfer, gpio set/get, monotonic tick, busy-wait. It is **not** a full HAL reimplementation; boards with richer peripherals expose them through `CONFIG_DUNEOS_DRV_*` Kconfig (ESP-IDF) or CMake option equivalent (other SDKs).

### What an external maintainer must do

**New board, existing SDK family** (e.g. a second ESP32-S3 board):
1. Add `boards/<name>/board.yaml` with `arch: xtensa-esp32s3` and `sdk: esp-idf`
2. `bspgen.py` generates `board_config.h`, `sdkconfig.board`, `partitions.csv`, `idf_target.txt`
3. Done — zero changes to dbt or kernel.

**New SDK family** (e.g. RP2040):
1. Add `boards/<name>/board.yaml` with `arch: arm-cortex-m0plus` and `sdk: pico-sdk`
2. Add `tools/dbt/toolchain/pico_sdk.py` implementing the plugin interface
3. Add `arch/arm_cortex_m/arch.cmake` + `arch/arm_cortex_m/hal/rp2040/hal_*.c` implementing `duneos/hal_*.h`
4. Add `arch/arm_cortex_m/reloc/loader_reloc_arm.c` (registered via `duneos_arch_ops_t` once Phase 28 lands)
5. Done — `kernel/duneos_kernel`, `kernel/duneos_loader`, and existing toolchain plugins are untouched.

### Toolchain source discovery

Each plugin's `find_toolchain_root()` searches in this priority order:
1. `DUNEOS_<SDK>_ROOT` environment variable (e.g. `DUNEOS_IDF_ROOT`, `DUNEOS_PICO_SDK`)
2. `.duneos_idf` / `.duneos_pico_sdk` file at repo root (gitignored, per-developer)
3. Known default install paths (e.g. `~/.espressif/v6.0.x/esp-idf`, `~/pico/pico-sdk`)
4. `PATH` scan for the compiler binary

`dbt setup` runs the wizard for whichever SDK the active board needs — not all SDKs at once.

## Key Technical Decisions

- **ABI = function pointer table**, not CPU syscalls (no MMU → no privilege separation). Defined in `abi.h`.
- **ELF format = ET_REL** (relocatable object), not ET_DYN. Same as Flipper Zero FAP.
- **No manifest.json on SD** — manifest is a JSON string embedded in `.duneos_manifest` ELF section.
- **Scheduler strategy**: FreeRTOS stays the scheduler on all supported targets (ESP32, RP2040, STM32 all have FreeRTOS ports). DuneOS does not implement its own scheduler. Phase 27 (OSAL) abstracts FreeRTOS behind `duneos_osal.h` so the kernel is portable to targets without FreeRTOS (Linux simulator via `pthread_osal.c`).
- **`.dap` binaries are ISA-specific** — an Xtensa `.dap` cannot run on RISC-V and vice versa. App source code is portable; the binary is not. Phase 24 adds `arch` to the manifest so the loader rejects incompatible `.dap` files gracefully instead of crashing.
- **dbt system** (Phase 25) is the DuneOS equivalent of a Yocto image recipe: `profile.yaml` selects kernel features, `system.yaml` declares apps, `dbt system check` validates app permissions against the kernel profile before any build starts.
- **VFS = ESP-IDF `esp_vfs_register`** reused, not reinvented (until Phase 26).
- **Board config** selected at build time via `.duneos_board` file → `boards/<board>/` directory. `bspgen.py` generates `board_config.h`, `sdkconfig.board`, `idf_target.txt`, and `partitions.csv` (sized to `flash_size_mb`).
- **Flash-first VFS**: `/flash` (LittleFS, `sysbin` partition) is always mounted first and is fatal if absent. SD is optional (`DUNEOS_HAS_SD` in `board_config.h`, defaults 1). Init and scan cascade: `/flash` before `/sd`.
- **Flash boot services**: `boards/<board>/init.yaml` controls which services auto-start from flash (even without an SD card). `dbt flashimg` stages it as-is. Deduplication in `init.c` is name-based (basename without `.dap`) so a service in flash wins over the same service in `/sd/init.yaml`.
- **Port config**: `.duneos_port` file at repo root (gitignored) stores the active serial port. `dbt.py flashimg` reads it; `--port` overrides; `DUNEOS_ESPTOOL` overrides the esptool binary path.
- **PSRAM auto-detection**: `#ifdef CONFIG_SPIRAM` in loader.c — PSRAM boards allocate app sections in SPIRAM, others in DRAM.
- **Xtensa relocations**: ET_REL uses RELA (with addend). Implemented: R_XTENSA_32, R_XTENSA_SLOT0_OP (L32R), R_XTENSA_ASM_EXPAND (ignored), R_XTENSA_DIFF8/16/32.
- **`.bss` section**: SHT_NOBITS — not in ELF, allocated by loader + `memset(0)`.
- **Multi-app**: supervisor owns 4 `app_slot_t` entries; each slot has its own FreeRTOS task + mailbox queue. `duneos_supervisor_wait_all()` blocks on a binary semaphore given when the last slot clears.
- **IPC**: `duneos_send(dest, data, len)` / `duneos_recv(out, timeout_ms)` route through the destination slot's mailbox queue. No shared memory.
- **Permissions**: `duneos_symbol_t.required_perm` bitmask checked against `manifest.permissions` at symbol resolution. Unknown or missing symbol returns NULL and logs a warning.
- **`dup`/`dup2`**: not inline in ESP-IDF v6.0.1 newlib, but cannot safely take their address — wrapped as `duneos_dup`/`duneos_dup2` calling `dup`/`dup2` directly.
- **Hardware abstraction**: kernel owns buses (`/dev/i2c-N`, `/dev/spi-N`, `/dev/gpiochip0`). Per-chip drivers (SX1509, SSD1306, CC1101 …) live in userspace as `.c` files linked into the app — no kernel changes per chip. Same model as Linux `/dev/i2c-dev` + userspace `libi2c`.
- **Circular dependency (duneos_kernel ↔ duneos_loader)**: broken via registered function pointers. `duneos_loader_init()` calls `duneos_supervisor_register_loader(&ops)` before the first `duneos_supervisor_launch()`. The kernel never includes `duneos/loader.h`.
- **Init system**: `main.c` tries `/flash/init.yaml` first, then `/sd/init.yaml`; falls back to scan+autoboot if neither exists. `init.c` includes a minimal hand-rolled YAML reader (fixed schema: `services:` list with `path:` + `restart:`). The `s_want_restart` counter in the supervisor prevents `wait_all()` from firing spuriously during the unload→relaunch window of a restarting service.
- **System apps vs user apps**: `system/` holds apps that ship with DuneOS (shell, future daemons). `apps/` holds developer-facing apps and demos (formerly `examples/`). Both use `dbt.py` to build — the distinction is semantic, not toolchain.

## Hard-Won Lessons (do not repeat these mistakes)

- **`xtensa-esp-elf-gcc` defaults to big-endian Xtensa** — always use `xtensa-esp32s3-elf-gcc` for app builds. `dbt.py` now tries target-specific prefix first.
- **`idf.cmakeAdditionalArgs: ["-DDUNEOS_BOARD=..."]` in VS Code silently overrides `.duneos_board`** — `CMakeLists.txt` now always reads `.duneos_board` and ignores cmake args. Keep `idf.cmakeAdditionalArgs` as `[]`.
- **`sdkconfig` at project root caches board-specific settings** — must delete it when switching boards (Full Clean).
- **M5Stack CardPuter = ESP32-S3FN8 = no PSRAM** — bspgen emits `CONFIG_SPIRAM=n` into `boards/m5stack-cardputer/sdkconfig.board` from `board.yaml` (no `psram:` section).
- **FAT SD card returns 8.3 uppercase filenames** — use `strcasecmp` for `.elf` extension filtering.
- **`manifest.json` on the SD card is wrong** — manifest is embedded in the ELF (Flipper Zero pattern).
- **`printf` must NOT be in the export table** — apps call it through newlib → `_write()` → VFS `write()`. Exporting `printf` directly bypasses the VFS.
- **`vTaskDelay` must NOT be exported** — apps use `nanosleep()`/`usleep()`. FreeRTOS is an implementation detail.
- **`nanosleep` is not in ESP-IDF newlib** — implemented as `duneos_nanosleep()` via `usleep()`.
- **Inline comments in `partitions.csv` are parsed as flags by ESP-IDF** — remove all inline comments.
- **`CMAKE_SOURCE_DIR` is unreliable during ESP-IDF requirements phase** — use `CMAKE_CURRENT_LIST_DIR` instead.
- **`CONFIG_ESP_CONSOLE_USB_CDC=y` conflicts with TinyUSB** — if set alongside TinyUSB, the device doesn't enumerate at all (nothing in `lsusb`). For OTG boards use `console: none` in `board.yaml`; bspgen emits `CONFIG_ESP_CONSOLE_NONE=y`. The `console:` field controls only the ESP-IDF early-boot serial — DuneOS redirects all log output to CDC at runtime via `esp_log_set_vprintf` regardless.
- **Never hand-edit bspgen-generated files** — `sdkconfig.board`, `board_config.h`, `partitions.csv`, and `idf_target.txt` under `boards/<name>/` are generated by `tools/duneos-bspgen.py`. Fix the YAML or bspgen logic, then re-run bspgen.
- **Concurrent `tinyusb_cdcacm_write_flush` calls cause "Flush failed"** — all CDC TX must flow through a single mutex (`xSemaphoreCreateMutex`) with a non-blocking flush (`timeout_ticks=0`). TinyUSB's `cdcd_xfer_cb` auto-flushes remaining FIFO data after each transfer. Using `timeout_ticks > 0` from a second task loops on `tud_cdc_n_write_flush` and collides with the first task.
- **UART0 (GPIO43/44) on the CardPuter has no physical header** — it is inaccessible. Never route klog or the ESP-IDF console to UART0; it must stay free for application use.
- **`dprintf` through a `va_list`-accepting function pointer is UB in C** — `int (*dprintf)(int, const char *, ...)` in the API table cannot be called by forwarding a `va_list`. libdune's `dprintf` wrapper uses `vsnprintf` + `write()` instead; the API table entry is kept for apps that call `api_ptr->fs.dprintf` directly with explicit args.
- **`__duneos_api_ptr` must be in `.data`, not `.bss`** — `libdune_ptr.c` initialises it to `(const duneos_api_t *)0` explicitly. An uninitialised (BSS) symbol has section index `SHN_COMMON` or a zero-size section entry that the loader's DEFINED-symbol scan skips. The explicit zero initialiser gives it a concrete `.data` section entry.
- **cJSON est vendorisé dans `duneos_loader/src/`** — ESP-IDF v6.0.1 ne fournit plus `json` comme composant intégré (`espressif__cjson` est dans managed_components). Pour éviter la dépendance au component manager, `cJSON.h` et `cJSON.c` sont copiés directement dans le composant. Ne jamais remettre `json` ou `espressif__cjson` dans les REQUIRES du loader.
- **`duneos_slot_info_t` in libdune.h must stay in sync with supervisor.h** — both define the same struct with hardcoded `64` instead of `DUNEOS_APP_NAME_MAX` (apps don't include supervisor.h). If the kernel struct changes, both must be updated together and `DUNEOS_ABI_VERSION` bumped.
- **Xtensa stack alignment for `xTaskCreateStaticPinnedToCore`** — stack buffer must be 16-byte aligned. Use `heap_caps_aligned_alloc(16, size, MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT)`. The `StaticTask_t` TCB buffer must also outlive the task (keep it in the slot struct, not on a local stack frame).
- **`RINGBUF_TYPE_BYTEBUF` + counting semaphore is wrong for CDC RX** — `vRingbufferReturnItem` advances the read pointer by the size of the contiguous block it returned, not by the `maxSize` you passed to `xRingbufferReceiveUpTo`. With `maxSize=1`, bytes after the first in a multi-byte USB packet are silently dropped and their semaphore tokens become orphaned. Use `xStreamBufferCreate(size, 1)` + `xStreamBufferReceive`/`xStreamBufferSend` instead — single-producer/single-consumer byte stream, correct by construction.
- **`static char buf[]` in bin apps breaks `api_read` pointer validation** — bin apps run captured (synchronously in the shell task). The app's static BSS data lives in its own heap-allocated pool, outside the shell slot's bounds. `check_app_writable_ptr` sees the shell slot and rejects the pointer (EFAULT), making `read()` return -1. Use stack-allocated buffers instead; they live on the shell task's stack which is within the shell slot's bounds.
- **Two tasks reading the same `/dev/ttyUSB0` fd simultaneously cause interleaved character reads** — when `usb_shell` was launched from both `/flash/init.yaml` and `/sd/init.yaml`, two FreeRTOS tasks each calling `read(fd, &c, 1)` concurrently on the CDC RX stream buffer stole alternating bytes: typing "ls"+Enter produced "l: command not found" + "s: command not found". Fix: `init.c` deduplication must compare by app name (basename without `.dap`), not by exact path — `/flash/bin/usb_shell.dap` and `/sd/bin/usb_shell.dap` are the same service; the flash entry wins (loaded first).
- **DHI phase boundary: FreeRTOS stays until Phase 27** — Phase 24 (DHI) removes hardware-peripheral ESP-IDF includes (`driver/gpio.h`, `esp_adc/`, `esp_rom_sys.h`, etc.) from driver files. FreeRTOS primitives (`vTaskDelay`, `xTaskCreate`, `xQueueCreate`, `freertos/*.h`) are **not** Phase 24 scope; they stay until Phase 27 (OSAL). Do not attempt to remove FreeRTOS calls from drivers or the supervisor while working on Phase 24.
- **ADC unit ID convention: 1-based in HAL, 0-based in ESP-IDF** — `DUNEOS_BATTERY_ADC_UNIT` in `board_config.h` is `1` for ADC1, `2` for ADC2 (1-based). `hal_adc.c` converts with `(adc_unit_t)(cfg->unit_id - 1)` before passing to ESP-IDF. `bspgen.py` emits the integer directly from the YAML `adc_unit: 1`. Never store the ESP-IDF enum name (`ADC_UNIT_1`) in `board_config.h` — it leaks an ESP-IDF symbol into the public board config.
- **`hal_time.h` delay vs OS delay** — `duneos_hal_delay_us()` is a hardware busy-wait (`esp_rom_delay_us`) with no OS yield. Use it only for short delays (µs-level scan timing in input drivers). For task-level delays, use `vTaskDelay()` (Phase 27: migrated to `duneos_osal_sleep_ms()`). Never use `duneos_hal_delay_us()` for delays > ~1 ms — it blocks the FreeRTOS scheduler.
- **`esp_rom_printf` in exception/WDT handlers is NOT the same as `esp_rom_delay_us`** — `supervisor.c` uses `esp_rom_printf` (not `esp_rom_delay_us`) in its exception, stack-overflow, and WDT callbacks. `esp_rom_printf` is a last-resort panic output that bypasses the VFS/UART stack entirely — it works even when the allocator and task scheduler are dead. Do NOT replace it with `klog_e()` or `dprintf()` (those may crash in a crashed app context). It belongs to Phase 27 (OSAL): an `osal_panic_print()` abstraction backed by `esp_rom_printf` on ESP32. Phase 24 DHI scope is limited to public header types — `esp_rom_printf` in a private `.c` implementation is not a Phase 24 violation.
- **`xTaskGetTickCount() * portTICK_PERIOD_MS` for event timestamps is wrong** — FreeRTOS tick count wraps, has coarse resolution, and is task-scheduler-dependent. Use `(uint32_t)(duneos_hal_monotonic_us() / 1000)` for event `time_ms` fields; it reads the hardware ESP timer directly and has µs precision.
- **`arch.cmake` guard must check `IDF_TARGET_ARCH`, not just `CONFIG_IDF_TARGET_ARCH_XTENSA`** — ESP-IDF's `__component_get_requirements` runs each component's CMakeLists.txt in a separate `cmake -P` script to extract REQUIRES. That script loads build properties (including `IDF_TARGET_ARCH = "xtensa"`) but does NOT load `sdkconfig.cmake`, so all `CONFIG_*` variables are empty. An arch.cmake that only checks `CONFIG_IDF_TARGET_ARCH_XTENSA` will always `return()` during the requirements phase, silently skipping `list(APPEND DUNEOS_KERNEL_REQUIRES esp_timer ...)`. Result: arch-specific headers are missing at compile time even though the files are compiled. Fix: always use the three-variable guard (see arch.cmake guard pattern above).

## Code Style & Constraints

- **Kernel:** C17, no C++, no exceptions, no external dependencies beyond ESP-IDF
- **Tooling** (`dbt.py`, future `duneos-bspgen`): Python
- **ABI stability:** any breaking change to exported symbols or struct layouts requires `DUNEOS_ABI_VERSION` bump in `abi.h`
- **No MMU** → strict conventions, Task WDT, stack canaries (not yet), per-app exception handlers (not yet)
- **No comments explaining what code does** — only comments for non-obvious WHY

## References

- **Architecture Decision Records** — [`docs/adr/`](docs/adr/) (16 ADRs as of 2026-05). Start at [`docs/adr/README.md`](docs/adr/README.md). Authoritative for design intent.
- **Unscheduled ideas** — [`docs/backlog.md`](docs/backlog.md). Things noticed during planning, not yet committed to a phase.
- **Contest sprint 2026** — [`docs/contest-2026.md`](docs/contest-2026.md). M5Stack Global Innovation Contest 2026 submission plan: timeline, app portfolio, demo video arc, Hackster pitch draft. If active, freezes phases 26-29 until 2026-09.
- Flipper Zero FAP loader: [flipperzero-firmware](https://github.com/flipperdevices/flipperzero-firmware)
- ESP-IDF VFS: [ESP-IDF VFS docs](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/storage/vfs.html)
- Zephyr Device Tree: [Zephyr DTS intro](https://docs.zephyrproject.org/latest/build/dts/intro.html)
- newlib POSIX on ESP32: [newlib on ESP32](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-guides/newlib.html)
