# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**DuneOS** is a minimalist OS for ESP32-S3 microcontrollers built on ESP-IDF. Its core goals:

- Dynamically load separately-compiled applications (`.dap` / `.elf` files) from an SD card at runtime (similar to Flipper Zero `.fap` files)
- Expose a partial POSIX layer (files, threads, time) via newlib + ESP-IDF VFS
- Provide declarative board configuration (YAML → generated C header), inspired by Zephyr Device Tree

**Current dev board:** M5Stack CardPuter (ESP32-S3FN8 — 8 MB embedded flash, **no PSRAM**).
**Reference board:** ESP32-S3-DevKitC (with PSRAM).
**Toolchain:** ESP-IDF v5.5.1, CMake, `xtensa-esp32s3-elf-gcc`.

## Build & Test Commands

```bash
# Select active board (gitignored, each dev sets their own)
echo m5stack-cardputer > .duneos_board

# Build kernel (VS Code ESP-IDF extension: Ctrl+E B)
# Flash + monitor (Ctrl+E F then Ctrl+E M, or combined Ctrl+E D)
# Port is COM13 (configured in .vscode/settings.json)

# Clean when switching boards — sdkconfig is board-specific
# Use VS Code: Ctrl+Shift+P → "ESP-IDF: Full Clean"

# Build a DuneOS app (from the app directory)
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
│   │   │   ├── klog.h              # klog_i/w/e/d macros, ring buffer API
│   │   │   ├── supervisor.h        # Multi-app supervisor: launch/send/recv/wait_all
│   │   │   ├── task.h              # FreeRTOS abstraction
│   │   │   └── vfs.h               # VFS init/mount API
│   │   └── src/
│   │       ├── klog.c              # 4 KB ring buffer, /dev/klog backend, ESP_LOGx forwarding
│   │       ├── supervisor.c        # 4-slot app supervisor, FreeRTOS task per app, IPC queues
│   │       ├── task.c
│   │       ├── vfs.c               # SD (SPI+FatFS) mount, registers /tmp and /dev
│   │       ├── vfs_tmp.c           # /tmp — heap-backed RAM filesystem, 16 inodes/fds
│   │       ├── vfs_dev.c           # /dev — null, zero, uart0, klog
│   │       └── symbols.c           # POSIX export table with per-symbol permission gates
│   └── duneos_loader/
│       ├── include/duneos/
│       │   ├── loader.h            # scan/select/load/run/unload/run_captured API
│       │   └── elf.h               # ELF32 structs and Xtensa relocation constants
│       └── src/
│           └── loader.c            # Full ET_REL loader: parse, relocate, resolve, run
├── boards/
│   ├── esp32s3-devkitc/
│   │   ├── board_config.h          # SD SPI pins, PSRAM config
│   │   └── sdkconfig.defaults      # 8 MB flash, OPI PSRAM
│   ├── m5stack-cardputer/
│   │   ├── board_config.h          # SD SPI pins: MOSI=14, MISO=39, CLK=40, CS=12
│   │   └── sdkconfig.defaults      # 8 MB flash, no PSRAM (ESP32-S3FN8)
│   └── lilygo-t7-s3.yaml           # Reference YAML for BSP generator
├── examples/
│   ├── test_exit/                  # Minimal app: app_main calls duneos_exit(0)
│   ├── hello_world/                # Writes "Hello World" to stdout then exits
│   ├── uart_echo/                  # Echo loop on /dev/uart0, Ctrl-C to exit
│   └── shell/                      # Interactive POSIX shell (.dap); ls/cat/cd/run/klog/...
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

### Kernel driver selection model

The kernel binary is **board-specific**: same source tree, different binary per board (like Linux with
Kconfig). `duneos-bspgen.py` generates two artefacts from a board YAML:

1. `board_config.h` — pin assignments and hardware constants
2. `sdkconfig.board` — Kconfig fragment that controls which kernel drivers are compiled in

This means no unused driver code in flash and no unused runtime state in RAM. The build is already
board-specific today (`sdkconfig.defaults` per board); `sdkconfig.board` formalises driver selection.

### Kernel vs userspace hardware boundary

**Decision rule:**

| Put it in kernel if | Put it in userspace if |
| --- | --- |
| Shared between multiple concurrent apps | Used by at most one app at a time |
| Requires a hardware ISR | Pure register-level protocol over a bus |
| Board-global resource (ADC, bus controller) | Per-chip protocol (ST7789, CC1101, MPU6050…) |
| Small runtime state (tens of bytes) | RAM cost too high for DRAM-only boards |

**In the kernel (`CONFIG_DUNEOS_HAVE_x=y`, compiled per board):**

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
| Phase 5 — Shell | **DONE** | `examples/shell/` — interactive POSIX shell app with history and line editing |
| Phase 6 — BSP generator | **DONE** | `tools/duneos-bspgen.py` — YAML → board_config.h; lilygo-t7-s3.yaml reference |
| Phase 7 — App SDK & DX | **DONE** | `dbt.py info` footprint, `dbt.py deploy` → `.dap`, `hello_world`, `uart_echo` demos |
| Phase 8 — GPIO | **DONE** (native) | `/dev/gpiochip0` via `esp_driver_gpio`; `gpio_ioctl.h` SDK header; shell `gpio` command; expander support pending |
| Phase 9 — Init system | Not started | `/sd/init.json`, supervisor restart policies, `duneos_service_ready()` |
| Phase 10 — I2C + battery | Not started | `/dev/i2c-0`, `/dev/battery0` (ADC/I2C backend from BSP YAML) |
| Phase 11 — SPI | Not started | `/dev/spi-1` (SPI3_HOST only — SPI2 taken by SD) |
| Phase 12 — Input | Not started | `/dev/input/event0` ring buffer; keyboard daemon feeds it via IPC |
| Phase 13 — Framebuffer + display SDK | Not started | `/dev/fb0` PSRAM-only; userspace `libst7789/gc9a01/ssd1306` for all boards |
| Phase 14 — WiFi daemon | Not started | Userspace `wifi_daemon.dap`; lwIP sockets already exported |
| Phase 15 — Multi-target portability | Not started | `libgfx` display-agnostic API; `board.info` written at boot; `dbt.py` backend selection |

**Current state:** All seven phases implemented. The kernel boots, mounts SD, initialises supervisor, and launches apps as separate FreeRTOS tasks. The shell (`examples/shell/shell.dap`) provides an interactive POSIX interface over UART0. Stack canaries and per-app exception handlers remain as known technical debt.

## Key Technical Decisions

- **ABI = function pointer table**, not CPU syscalls (no MMU → no privilege separation). Defined in `abi.h`.
- **ELF format = ET_REL** (relocatable object), not ET_DYN. Same as Flipper Zero FAP.
- **No manifest.json on SD** — manifest is a JSON string embedded in `.duneos_manifest` ELF section.
- **VFS = ESP-IDF `esp_vfs_register`** reused, not reinvented.
- **Board config** selected at build time via `.duneos_board` file → `boards/<board>/` directory.
- **PSRAM auto-detection**: `#ifdef CONFIG_SPIRAM` in loader.c — PSRAM boards allocate app sections in SPIRAM, others in DRAM.
- **Xtensa relocations**: ET_REL uses RELA (with addend). Implemented: R_XTENSA_32, R_XTENSA_SLOT0_OP (L32R), R_XTENSA_ASM_EXPAND (ignored), R_XTENSA_DIFF8/16/32.
- **`.bss` section**: SHT_NOBITS — not in ELF, allocated by loader + `memset(0)`.
- **Multi-app**: supervisor owns 4 `app_slot_t` entries; each slot has its own FreeRTOS task + mailbox queue. `duneos_supervisor_wait_all()` blocks on a binary semaphore given when the last slot clears.
- **IPC**: `duneos_send(dest, data, len)` / `duneos_recv(out, timeout_ms)` route through the destination slot's mailbox queue. No shared memory.
- **Permissions**: `duneos_symbol_t.required_perm` bitmask checked against `manifest.permissions` at symbol resolution. Unknown or missing symbol returns NULL and logs a warning.
- **`dup`/`dup2`**: not inline in ESP-IDF v5.5.1 newlib, but cannot safely take their address — wrapped as `duneos_dup`/`duneos_dup2` calling `dup`/`dup2` directly.
- **Hardware abstraction**: kernel owns buses (`/dev/i2c-N`, `/dev/spi-N`, `/dev/gpiochip0`). Per-chip drivers (SX1509, SSD1306, CC1101 …) live in userspace as `.c` files linked into the app — no kernel changes per chip. Same model as Linux `/dev/i2c-dev` + userspace `libi2c`.
- **Circular dependency (duneos_kernel ↔ duneos_loader)**: broken via registered function pointers. `duneos_loader_init()` calls `duneos_supervisor_register_loader(&ops)` before the first `duneos_supervisor_launch()`. The kernel never includes `duneos/loader.h`.

## Hard-Won Lessons (do not repeat these mistakes)

- **`xtensa-esp-elf-gcc` defaults to big-endian Xtensa** — always use `xtensa-esp32s3-elf-gcc` for app builds. `dbt.py` now tries target-specific prefix first.
- **Board selection via `idf.cmakeAdditionalArgs` in VS Code doesn't work reliably** — use `.duneos_board` file read by CMakeLists.txt instead.
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
