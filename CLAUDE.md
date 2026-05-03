# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**DuneOS** is a minimalist OS for ESP32 microcontrollers built on ESP-IDF. Its core goals:
- Dynamically load separately-compiled applications (`.elf` files) from an SD card at runtime (similar to Flipper Zero `.fap` files)
- Expose a partial POSIX layer (files, threads, sockets, time) via newlib + custom VFS
- Provide declarative board configuration (YAML → generated C header), inspired by Zephyr Device Tree

**Primary target:** ESP32-S3 with PSRAM. **Toolchain:** ESP-IDF, CMake, `xtensa-esp32-elf`.

## Build & Test Commands

> The project is in its early phase — build infrastructure does not yet exist. Once implemented:

```bash
# Kernel firmware (ESP-IDF project)
idf.py build
idf.py flash monitor

# Host unit tests (CMock/Unity, hardware-independent parts only)
cmake -B build-host -DTARGET=host && cmake --build build-host
ctest --test-dir build-host

# BSP generator (Go or Python tooling)
duneos-bspgen board.yaml   # → board_config.h
```

## Architecture

```
┌─────────────────────────────────────┐
│         Applications (.elf)          │  ← built separately, stored on SD card
├─────────────────────────────────────┤
│       SDK / Public API (fixed ABI)   │  ← symbol table exported by kernel
├──────────────┬──────────────────────┤
│  ELF Loader  │  VFS + POSIX layer   │  ← core of the project
├──────────────┴──────────────────────┤
│        Kernel / Scheduler            │  ← FreeRTOS wrapper (via ESP-IDF)
├─────────────────────────────────────┤
│     BSP (Board Support Package)      │  ← generated from YAML board descriptor
├─────────────────────────────────────┤
│          ESP-IDF / Hardware          │
└─────────────────────────────────────┘
```

### Implementation Phases

**Phase 1 — Kernel foundations:** static memory map, secondary bootloader (reads `manifest.json` from SD), FreeRTOS abstraction API (`duneos_task_create`, `duneos_task_yield`…), ABI definition (syscall table, `app_main` entry point, ABI version checking).

**Phase 2 — VFS & POSIX layer:** FatFS on SD, VFS mount tree (`/sd`, `/tmp`, `/dev`), POSIX file syscalls (`open/read/write/close/stat/opendir/readdir/lseek`), device files (`/dev/uart0`, `/dev/gpio`…), `pthread_*`, `sem_*`, `clock_gettime`.

**Phase 3 — ELF Loader (most complex):** relocatable ELF format with embedded JSON manifest (name, version, required ABI version, permissions); parser for ELF headers and sections (`.text`, `.data`, `.bss`, `.rodata`); Xtensa-specific relocation types (`R_XTENSA_32`, `R_XTENSA_SLOT0_OP`); kernel export symbol table; app lifecycle (load → `app_main` → `duneos_exit()` / crash handling).

**Phase 4 — BSP:** YAML board descriptor schema → `duneos-bspgen` tool → `board_config.h`. Reference BSPs for ESP32-DevKitC, ESP32-S3-DevKitC, TTGO T-Display, Lilygo T7-S3.

**Phase 5 — App SDK & toolchain:** CMake template for relocatable apps, public headers (`duneos/vfs.h`, `duneos/task.h`, `duneos/gpio.h`…), demo apps (`hello_world`, `blink`, `file_reader`).

### Suggested First Milestone

> Bootloader that reads `/sd/apps/manifest.json`, loads a minimal relocatable ELF into RAM, resolves 3 symbols (`printf`, `vTaskDelay`, `duneos_exit`), and jumps to `app_main`.

## Code Style & Constraints

- **Kernel:** C17, no C++, no exceptions, no external dependencies beyond ESP-IDF
- **Tooling** (`duneos-bspgen`, etc.): Go or Python
- **Public API is stable from Phase 3 onward** — any breaking change requires an ABI version bump and loader version check
- **No MMU** → use strict conventions, watchdog, stack canaries, per-app exception handlers instead of hardware isolation
- **PSRAM performance:** pin hot `.text` sections in IRAM via `IRAM_ATTR` to avoid cache-miss penalties

## Key Technical Risks

| Risk | Mitigation |
|---|---|
| No MMU → no real memory isolation | Strict conventions + watchdog + stack canaries + per-app exception handler |
| Tight SRAM (320 KB on classic ESP32) | Target ESP32-S3 + PSRAM from the start |
| Xtensa ELF relocation complexity | Reference the Flipper Zero FAP loader (open source, well-documented relocation types) |
| ABI stability | Version number in manifest + reject load on mismatch |
| PSRAM execution latency | `IRAM_ATTR` for critical `.text` sections |

## References

- Flipper Zero FAP loader (primary ELF loader reference on no-MMU MCU): https://github.com/flipperdevices/flipperzero-firmware
- ESP-IDF VFS: https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/storage/vfs.html
- Zephyr Device Tree (BSP YAML inspiration): https://docs.zephyrproject.org/latest/build/dts/intro.html
- newlib POSIX on ESP32: https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-guides/newlib.html
