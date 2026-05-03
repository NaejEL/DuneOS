# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**DuneOS** is a minimalist OS for ESP32 microcontrollers built on ESP-IDF. Its core goals:
- Dynamically load separately-compiled applications (`.elf` files) from an SD card at runtime (similar to Flipper Zero `.fap` files)
- Expose a partial POSIX layer (files, threads, sockets, time) via newlib + ESP-IDF VFS
- Provide declarative board configuration (YAML → generated C header), inspired by Zephyr Device Tree

**Primary target:** ESP32-S3 with PSRAM. **Toolchain:** ESP-IDF v5.x, CMake, `xtensa-esp32-elf`.

## Build & Test Commands

```bash
# Select board (defaults to esp32s3-devkitc if unset)
export DUNEOS_BOARD=esp32s3-devkitc

# Kernel firmware
idf.py build
idf.py -p /dev/ttyUSB0 flash monitor

# Host unit tests (CMock/Unity, hardware-independent parts only)
cmake -B build-host -DTARGET=host && cmake --build build-host
ctest --test-dir build-host

# BSP generator (Phase 4 — not yet implemented)
duneos-bspgen boards/esp32s3-devkitc.yaml   # → boards/esp32s3-devkitc/board_config.h
```

## Repository Structure

```
DuneOS/
├── main/                           # Kernel entry point (ESP-IDF app)
│   └── main.c                      # app_main: VFS init → manifest → load → run
├── components/
│   ├── duneos_kernel/              # Core kernel component
│   │   ├── include/duneos/
│   │   │   ├── abi.h               # ABI contract: duneos_symbol_t, duneos_app_manifest_t
│   │   │   ├── task.h              # FreeRTOS abstraction API
│   │   │   ├── vfs.h               # VFS init/mount API
│   │   │   └── manifest.h          # manifest.json parser API
│   │   └── src/
│   │       ├── task.c              # StaticTask wrapper, PSRAM stack option
│   │       ├── vfs.c               # SD (SPI+FatFS), /tmp, /dev mounts
│   │       ├── manifest.c          # cJSON parser for /sd/apps/manifest.json
│   │       └── symbols.c           # Kernel export symbol table (function pointer table)
│   └── duneos_loader/              # ELF loader component (Phase 3)
│       ├── include/duneos/
│       │   └── loader.h            # Load/run/unload API for ET_REL ELF apps
│       └── src/
│           └── loader.c            # Scaffold — full implementation is Phase 3
├── boards/
│   ├── esp32s3-devkitc.yaml        # BSP descriptor (source of truth for bspgen)
│   └── esp32s3-devkitc/
│       └── board_config.h          # Generated header (manual for now, bspgen in Phase 4)
├── sdkconfig.defaults              # ESP32-S3, OPI PSRAM, Task WDT, 1 kHz tick
└── partitions.csv                  # 1 MB kernel + 15 MB FAT storage
```

## Architecture

```
┌─────────────────────────────────────┐
│         Applications (.elf)          │  ← ET_REL ELF, built separately, stored on SD
├─────────────────────────────────────┤
│       SDK / Public API (fixed ABI)   │  ← function pointer table in duneos_kernel
├──────────────┬──────────────────────┤
│  ELF Loader  │  VFS + POSIX layer   │  ← core of the project
├──────────────┴──────────────────────┤
│        Kernel / Scheduler            │  ← FreeRTOS wrapper (via ESP-IDF)
├─────────────────────────────────────┤
│     BSP (Board Support Package)      │  ← board_config.h generated from YAML
├─────────────────────────────────────┤
│          ESP-IDF / Hardware          │
└─────────────────────────────────────┘
```

## Memory Map (ESP32-S3)

| Zone | Size | DuneOS usage |
|---|---|---|
| IRAM | ~400 KB | Kernel hot-path (`IRAM_ATTR`), ISRs |
| DRAM | ~300 KB available | Kernel data, FreeRTOS stacks, kernel heap |
| PSRAM | 2–8 MB | App `.text`/`.data`/`.bss`/`.rodata`, app heap |
| Flash XIP | 4–16 MB | Kernel cold code, `.rodata` |

Apps are allocated from PSRAM via `heap_caps_malloc(size, MALLOC_CAP_SPIRAM)`.

## Implementation Status

| Phase | Status | Description |
|---|---|---|
| Phase 1 — Kernel foundations | **In progress** | VFS init + SD mount done; manifest reader done; loader stub in place |
| Phase 2 — VFS & POSIX layer | Not started | `/tmp`, `/dev`, full POSIX file + thread + time APIs |
| Phase 3 — ELF Loader | Not started | ET_REL parse, Xtensa relocations, symbol resolution, app lifecycle |
| Phase 4 — BSP generator | Not started | `duneos-bspgen` YAML → `board_config.h` |
| Phase 5 — App SDK | Not started | CMake template, public headers, demo apps |

**Next milestone:** boot from a real ESP32-S3-DevKitC, read `/sd/apps/manifest.json`, log discovered apps. No ELF loading yet.

## Key Technical Decisions

- **ABI = function pointer table**, not CPU syscalls (no privilege separation without MMU). Defined in `abi.h`.
- **ELF format = ET_REL** (relocatable object), not ET_DYN. Same as Flipper Zero FAP loader.
- **VFS = ESP-IDF `esp_vfs_register`** reused — not reinvented. `duneos_kernel` registers drivers into it.
- **Board config = `board_config.h`** selected at build time via `DUNEOS_BOARD` CMake variable.
- **Xtensa relocations**: ~30 types; `R_XTENSA_SLOT0_OP` and variants are the hard part — reference Flipper Zero source.
- **`.bss` section**: allocated by loader + `memset(0)`. Never copied from ELF image.
- **Permissions**: capability model enforced at symbol resolution time (software only, no MMU).

## Code Style & Constraints

- **Kernel:** C17, no C++, no exceptions, no external dependencies beyond ESP-IDF
- **Tooling** (`duneos-bspgen`, etc.): Go or Python
- **Public API stable from Phase 3 onward** — any breaking change requires `DUNEOS_ABI_VERSION` bump
- **No MMU** → strict conventions, watchdog, stack canaries, per-app exception handlers
- **PSRAM execution latency**: pin hot kernel `.text` in IRAM via `IRAM_ATTR`
- **No comments explaining what code does** — only comments for non-obvious WHY (constraints, workarounds)

## Key Technical Risks

| Risk | Mitigation |
|---|---|
| No MMU → no real isolation | Strict conventions + Task WDT + stack canaries + per-app exception handler |
| Tight SRAM | All app sections in PSRAM (`MALLOC_CAP_SPIRAM`) from day one |
| ~30 Xtensa relocation types, SLOT*_OP is complex | Port directly from Flipper Zero FAP loader |
| ABI stability | `DUNEOS_ABI_VERSION` in manifest + reject on mismatch |
| PSRAM execution latency | `IRAM_ATTR` for kernel hot-path |
| `.bss` not in ELF image | `memset(bss_ptr, 0, bss_size)` explicitly in loader |
| Task WDT killed by looping app | Decide ownership (app vs supervisor) before Phase 3 |

## References

- Flipper Zero FAP loader: https://github.com/flipperdevices/flipperzero-firmware
- ESP-IDF VFS: https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/storage/vfs.html
- Zephyr Device Tree: https://docs.zephyrproject.org/latest/build/dts/intro.html
- newlib POSIX on ESP32: https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-guides/newlib.html
