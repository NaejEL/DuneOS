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

# Build the shell (system app, ships with DuneOS)
cd system/shell
python ../../tools/dbt.py build
python ../../tools/dbt.py deploy E:\   # E: = SD card drive

# Build a DuneOS example app
cd examples/test_exit
python ../../tools/dbt.py build
python ../../tools/dbt.py deploy E:\   # E: = SD card drive

# Inspect built ELF
python ../../tools/dbt.py info

# BSP generator
python tools/duneos-bspgen.py boards/esp32s3-devkitc.yaml --out boards/esp32s3-devkitc/board_config.h
```

## Repository Structure

```text
DuneOS/
├── main/
│   └── main.c                      # Kernel entry: VFS init → supervisor init → launch → wait
├── components/
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
│   │       ├── init.c              # Parse /sd/init.json (cJSON), return service list
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
├── boards/
│   ├── esp32s3-devkitc/
│   │   ├── board_config.h          # SD SPI pins, PSRAM config
│   │   ├── sdkconfig.defaults      # 8 MB flash, OPI PSRAM, CONFIG_DUNEOS_DRV_*
│   │   └── idf_target.txt          # "esp32s3" — read by CMakeLists.txt
│   ├── m5stack-cardputer/
│   │   ├── board_config.h          # SD SPI pins: MOSI=14, MISO=39, CLK=40, CS=12
│   │   ├── sdkconfig.defaults      # 8 MB flash, no PSRAM, CONFIG_DUNEOS_DRV_*
│   │   └── idf_target.txt          # "esp32s3" — read by CMakeLists.txt
│   ├── lilygo-t-embed-cc1101/
│   │   ├── board_config.h          # 16 MB flash, 8 MB OPI PSRAM, BQ27220 battery
│   │   ├── sdkconfig.defaults      # SPIRAM, CONFIG_DUNEOS_DRV_BATTERY_BQ27220=y
│   │   └── idf_target.txt          # "esp32s3" — read by CMakeLists.txt
│   └── *.yaml                      # Board YAML descriptors (source of truth for bspgen)
├── system/
│   └── shell/                      # DuneOS interactive shell (ships with the OS)
│       ├── shell.c                 # ls/cat/cd/run/gpio/klog/... built-in commands
│       └── manifest.json           # permissions=127, stack=16 KB
├── examples/
│   ├── test_exit/                  # Minimal app: app_main calls duneos_exit(0)
│   ├── hello_world/                # Writes "Hello World" to stdout then exits
│   └── uart_echo/                  # Echo loop on /dev/uart0, Ctrl-C to exit
├── tools/
│   ├── dbt.py                      # DuneBuild Tool: new/build/info/deploy/clean
│   └── duneos-bspgen.py            # BSP generator: board YAML → board_config.h
├── .duneos_board                   # Active board name (gitignored)
├── .vscode/settings.json           # idf.portWin=COM13, flashType=UART
├── partitions.csv                  # 1.5 MB kernel + 6.4 MB FAT storage
└── sdkconfig.defaults              # Common: FreeRTOS 1kHz, Task WDT, partition table
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
`components/duneos_kernel/Kconfig`). Each driver lives in its own file under
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
2. Add `config DUNEOS_DRV_<NAME>` to `components/duneos_kernel/Kconfig`
3. Add conditional `list(APPEND DUNEOS_KERNEL_SRCS ...)` to `components/duneos_kernel/CMakeLists.txt`
4. Add `#ifdef CONFIG_DUNEOS_DRV_<NAME>` block to `vfs_dev.c` (forward decl + call in mount)
5. Add `CONFIG_DUNEOS_DRV_<NAME>=y` to `boards/<board>/sdkconfig.defaults` for boards that use it
6. Update `duneos-bspgen.py` to emit the config entry when the matching YAML section is present

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
| Phase 14 — WiFi daemon + raw frame injection | **DONE** | `drv_wifi.c` kernel wrappers; `wifi_daemon.dap` (STA, backoff, reconnect); `duneos_netif_wait_ip()`; `/dev/raw80211` (`DUNEOS_PERM_NET_RAW`); `system/bin/ifconfig`; BSD socket exports |
| Phase 15 — Multi-target portability | Superseded by 18 | Merged into Phase 18 (libgfx + board.info). |
| Phase 16 — Shell refactor: built-ins vs PATH | **DONE** | Shell stripped to 6 built-ins (`cd`, `pwd`, `echo`, `exit`, `help`, `run`). 14 commands moved to `system/bin/` as `.dap` files. Args via `/tmp/.exec_args` (`bin_args.h`). `dbt.py deploy --bin` copies to `/sd/bin/`. |
| Phase 17 — Tooling DX | **DONE** | `dbt.py` split into sub-modules (`cli`, `builder`, `deploy`, `toolchain`, `manifest`); `duneos.yaml` replaces `manifest.json`; `init.yaml` replaces `init.json`; `bspgen.py` purged of ESP-IDF dep. |
| Phase 18 — libgfx (display portability) | **DONE** | `/flash/board.info` (+ `/sd/board.info`) written at boot; `libgfx` display-agnostic API with `gfx_st7789` and `gfx_fb` backends; `dbt.py` selects backend from `.duneos_board`. |
| Phase 19 — Flash storage (boot sans SD) | **DONE** | `sysbin` LittleFS partition (1 MB) at `/flash`; boot order: flash → SD; `duneos_vfs_provision_flash()` copies firmware-embedded blobs to `/flash/bin/`; init.yaml cascade (`/flash` then `/sd`); loader cascade (`/flash/bin/` → `/sd/bin/` → `/sd/apps/`); `bspgen` generates per-board `partitions.csv` from `flash_size_mb`; `DUNEOS_HAS_SD` flag; `dbt.py flashimg` builds LittleFS image and flashes directly (port from `.duneos_port` / `--port` / `DUNEOS_PORT`). |
| Phase 20 — Memory hardening | **DONE** | Per-app heap caps (DRAM pool, `heap_caps_malloc`); software WDT per slot (`esp_task_wdt`); per-app exception handler (`esp_register_shared_stack_event_handler`); supervisor recovery on WDT timeout or exception. |
| Phase 21 — dbt: multi-arch toolchain plugin model | **DONE** | `board.yaml` gains `arch:` + `sdk:` fields (all boards updated). `tools/dbt/toolchain/` package replaces `toolchain.py`: `__init__.py` exposes `load_plugin(sdk)` + `get_board_plugin()`; `esp_idf.py` is the first plugin (SDK/ARCH constants, `find_compiler`, `cflags`, `ldflags`, `build_kernel`, `flash_kernel`, `monitor`, `find_toolchain_root`). `builder.py`, `cli.py`, `flashimg.py`, `kernel.py` dispatch through `get_board_plugin()`. |
| Phase 22 — Kernel HAL abstraction | Not started | Define `components/duneos_hal/include/duneos/hal.h` (gpio, uart, i2c, spi, flash primitives). Refactor ESP-IDF calls out of `duneos_kernel` into `components/duneos_hal_esp32s3/`. Extract Xtensa ELF relocation constants to `loader_reloc_xtensa.c` behind a `duneos_reloc_ops_t` interface. `duneos_kernel` depends only on `duneos_hal.h`, not ESP-IDF directly. |
| Phase 23 — First non-ESP32 port | Not started | Validate Phases 21–22 against a real second target. RP2040 (Cortex-M0+, pico-sdk) is the leading candidate: FreeRTOS first-class in pico-sdk, LittleFS portable, `arm-none-eabi-gcc` widely available, `picotool` for flash. Adds `boards/rp2040-pico/board.yaml`, `tools/dbt/toolchain/pico_sdk.py`, `components/duneos_hal_rp2040/`. |

**Current state:** Phases 1–14, 16–21 implemented. The kernel boots from `/flash` (LittleFS) even without an SD card; SD is mounted as a secondary, non-fatal filesystem. `init.yaml` is read first from `/flash/init.yaml`, then `/sd/init.yaml`. Apps are scanned from `/flash/bin/` → `/sd/bin/` → `/sd/apps/`. Each board has a generated `partitions.csv` sized to its `flash_size_mb`. `dbt flashimg` builds and flashes the sysbin LittleFS image in one command. Phases 21–23 (multi-arch extensibility) are planned next.

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

### Kernel side — `components/duneos_hal/`

```
components/
  duneos_hal/
    include/duneos/hal.h        # pure interface — no SDK headers
  duneos_hal_esp32s3/           # implements duneos/hal.h using ESP-IDF (Phase 22)
  duneos_hal_rp2040/            # implements duneos/hal.h using pico-sdk  (Phase 23)
  duneos_kernel/                # depends only on duneos/hal.h (after Phase 22 refactor)
  duneos_loader/
    src/
      loader.c                  # arch-agnostic dispatch
      loader_reloc_xtensa.c     # Xtensa RELA constants + apply()
      loader_reloc_arm.c        # ARM RELA constants + apply()   (Phase 23)
      loader_reloc_riscv.c      # RISC-V RELA constants + apply() (future)
```

`duneos/hal.h` exposes only what the kernel actually needs — roughly: uart write/read, i2c write_read, spi transfer, gpio set/get, flash read/write/erase, a monotonic tick, and task yield. It is **not** a full HAL reimplementation; boards with richer peripherals expose them through the normal `CONFIG_DUNEOS_DRV_*` Kconfig (ESP-IDF) or CMake option equivalent (other SDKs).

### What an external maintainer must do

**New board, existing SDK family** (e.g. a second ESP32-S3 board):
1. Add `boards/<name>/board.yaml` with `arch: xtensa-esp32s3` and `sdk: esp-idf`
2. `bspgen.py` generates `board_config.h`, `sdkconfig.board`, `partitions.csv`, `idf_target.txt`
3. Done — zero changes to dbt or kernel.

**New SDK family** (e.g. RP2040):
1. Add `boards/<name>/board.yaml` with `arch: arm-cortex-m0plus` and `sdk: pico-sdk`
2. Add `tools/dbt/toolchain/pico_sdk.py` implementing the plugin interface
3. Add `components/duneos_hal_rp2040/` implementing `duneos/hal.h`
4. Add `components/duneos_loader/src/loader_reloc_arm.c`
5. Done — core `duneos_kernel`, `duneos_loader`, and all existing toolchain plugins are untouched.

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
- **VFS = ESP-IDF `esp_vfs_register`** reused, not reinvented.
- **Board config** selected at build time via `.duneos_board` file → `boards/<board>/` directory. `bspgen.py` generates `board_config.h`, `sdkconfig.board`, `idf_target.txt`, and `partitions.csv` (sized to `flash_size_mb`).
- **Flash-first VFS**: `/flash` (LittleFS, `sysbin` partition) is always mounted first and is fatal if absent. SD is optional (`DUNEOS_HAS_SD` in `board_config.h`, defaults 1). Init and scan cascade: `/flash` before `/sd`.
- **Port config**: `.duneos_port` file at repo root (gitignored) stores the active serial port. `dbt.py flashimg` reads it; `--port` overrides; `DUNEOS_ESPTOOL` overrides the esptool binary path.
- **PSRAM auto-detection**: `#ifdef CONFIG_SPIRAM` in loader.c — PSRAM boards allocate app sections in SPIRAM, others in DRAM.
- **Xtensa relocations**: ET_REL uses RELA (with addend). Implemented: R_XTENSA_32, R_XTENSA_SLOT0_OP (L32R), R_XTENSA_ASM_EXPAND (ignored), R_XTENSA_DIFF8/16/32.
- **`.bss` section**: SHT_NOBITS — not in ELF, allocated by loader + `memset(0)`.
- **Multi-app**: supervisor owns 4 `app_slot_t` entries; each slot has its own FreeRTOS task + mailbox queue. `duneos_supervisor_wait_all()` blocks on a binary semaphore given when the last slot clears.
- **IPC**: `duneos_send(dest, data, len)` / `duneos_recv(out, timeout_ms)` route through the destination slot's mailbox queue. No shared memory.
- **Permissions**: `duneos_symbol_t.required_perm` bitmask checked against `manifest.permissions` at symbol resolution. Unknown or missing symbol returns NULL and logs a warning.
- **`dup`/`dup2`**: not inline in ESP-IDF v5.5.1 newlib, but cannot safely take their address — wrapped as `duneos_dup`/`duneos_dup2` calling `dup`/`dup2` directly.
- **Hardware abstraction**: kernel owns buses (`/dev/i2c-N`, `/dev/spi-N`, `/dev/gpiochip0`). Per-chip drivers (SX1509, SSD1306, CC1101 …) live in userspace as `.c` files linked into the app — no kernel changes per chip. Same model as Linux `/dev/i2c-dev` + userspace `libi2c`.
- **Circular dependency (duneos_kernel ↔ duneos_loader)**: broken via registered function pointers. `duneos_loader_init()` calls `duneos_supervisor_register_loader(&ops)` before the first `duneos_supervisor_launch()`. The kernel never includes `duneos/loader.h`.
- **Init system**: `main.c` tries `/sd/init.json` first; falls back to scan+autoboot if absent. cJSON is already an ESP-IDF component (`json`). The `s_want_restart` counter in the supervisor prevents `wait_all()` from firing spuriously during the unload→relaunch window of a restarting service.
- **System apps vs examples**: `system/` holds apps that ship with DuneOS (shell, future daemons). `examples/` holds developer demos. Both use `dbt.py` to build — the distinction is semantic, not toolchain.

## Hard-Won Lessons (do not repeat these mistakes)

- **`xtensa-esp-elf-gcc` defaults to big-endian Xtensa** — always use `xtensa-esp32s3-elf-gcc` for app builds. `dbt.py` now tries target-specific prefix first.
- **`idf.cmakeAdditionalArgs: ["-DDUNEOS_BOARD=..."]` in VS Code silently overrides `.duneos_board`** — `CMakeLists.txt` now always reads `.duneos_board` and ignores cmake args. Keep `idf.cmakeAdditionalArgs` as `[]`.
- **`sdkconfig` at project root caches board-specific settings** — must delete it when switching boards (Full Clean).
- **M5Stack CardPuter = ESP32-S3FN8 = no PSRAM** — `CONFIG_SPIRAM=n` in its sdkconfig.defaults.
- **FAT SD card returns 8.3 uppercase filenames** — use `strcasecmp` for `.elf` extension filtering.
- **`manifest.json` on the SD card is wrong** — manifest is embedded in the ELF (Flipper Zero pattern).
- **`printf` must NOT be in the export table** — apps call it through newlib → `_write()` → VFS `write()`. Exporting `printf` directly bypasses the VFS.
- **`vTaskDelay` must NOT be exported** — apps use `nanosleep()`/`usleep()`. FreeRTOS is an implementation detail.
- **`nanosleep` is not in ESP-IDF newlib** — implemented as `duneos_nanosleep()` via `usleep()`.
- **Inline comments in `partitions.csv` are parsed as flags by ESP-IDF** — remove all inline comments.
- **`CMAKE_SOURCE_DIR` is unreliable during ESP-IDF requirements phase** — use `CMAKE_CURRENT_LIST_DIR` instead.

## Code Style & Constraints

- **Kernel:** C17, no C++, no exceptions, no external dependencies beyond ESP-IDF
- **Tooling** (`dbt.py`, future `duneos-bspgen`): Python
- **ABI stability:** any breaking change to exported symbols or struct layouts requires `DUNEOS_ABI_VERSION` bump in `abi.h`
- **No MMU** → strict conventions, Task WDT, stack canaries (not yet), per-app exception handlers (not yet)
- **No comments explaining what code does** — only comments for non-obvious WHY

## References

- Flipper Zero FAP loader: [flipperzero-firmware](https://github.com/flipperdevices/flipperzero-firmware)
- ESP-IDF VFS: [ESP-IDF VFS docs](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/storage/vfs.html)
- Zephyr Device Tree: [Zephyr DTS intro](https://docs.zephyrproject.org/latest/build/dts/intro.html)
- newlib POSIX on ESP32: [newlib on ESP32](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-guides/newlib.html)
