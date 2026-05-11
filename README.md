# DuneOS

A minimalist OS for ESP32-S3 microcontrollers that dynamically loads separately-compiled applications (`.elf` files) from an SD card at runtime — inspired by Flipper Zero `.fap` files.

Built on ESP-IDF v5.x. No Linux. No MMU. Just FreeRTOS, a POSIX subset, and an ELF loader.

## Status

**Phase 1 complete** — kernel boots, SD mounts, ELF apps are discovered, loaded into RAM, and executed.

Tested on **M5Stack CardPuter** (ESP32-S3FN8, no PSRAM, 8 MB flash).

## Quick Start

### Prerequisites

- ESP-IDF v5.5.1 installed (`IDF_PATH` set)
- VS Code + ESP-IDF extension, or `idf.py` in PATH
- Xtensa toolchain: `xtensa-esp32s3-elf-gcc` (ships with ESP-IDF)

### 1. Select your board

```
echo m5stack-cardputer > .duneos_board
```

The file `.duneos_board` is gitignored — each developer sets their own. Fallback default is `esp32s3-devkitc`.

### 2. Build and flash the kernel

Via VS Code ESP-IDF extension: **Ctrl+E B** then **Ctrl+E F**.

Or from the terminal (with IDF_PATH set):

```bash
idf.py build
idf.py -p COM13 flash monitor
```

### 3. Build an app

```bash
cd examples/test_exit
python ../../tools/dbt.py build
python ../../tools/dbt.py deploy E:\   # E: = your SD card drive letter
```

`dbt.py` (DuneBuild Tool) compiles a C source file into a relocatable ELF and deploys it to the SD card.

### 4. Prepare the SD card

```
SD:\
├── apps\
│   └── test_exit.elf     ← deployed by dbt.py
└── autoboot              ← text file containing: test_exit
```

The `autoboot` file contains the app name (no extension). If absent, the first discovered app is launched.

### 5. Boot

Insert SD, power on. Expected output:

```
I duneos: DuneOS 0.1.0 (ABI v1)
I duneos/vfs: SD mounted at /sd — 1.9 GB
I duneos/loader: manifest: 'test_exit' v0.1.0 (ABI>=1)
I duneos/loader: scan: 1 app(s) found in /sd/apps
I duneos/loader: autoboot: 'test_exit'
I duneos: launching 'test_exit' v0.1.0
I duneos/loader: app_main @ 0x3fcea698
I duneos/loader: jumping to app_main @ 0x3fcea698
```

## Repository Structure

```
DuneOS/
├── main/                       # Kernel entry point
│   └── main.c                  # VFS init → scan → select → load → run
├── components/
│   ├── duneos_kernel/          # Core kernel component
│   │   ├── include/duneos/
│   │   │   ├── abi.h           # ABI: duneos_symbol_t, duneos_app_manifest_t
│   │   │   ├── task.h          # FreeRTOS abstraction
│   │   │   └── vfs.h           # VFS init/mount API
│   │   └── src/
│   │       ├── task.c
│   │       ├── vfs.c           # SD (SPI+FatFS), /tmp, /dev mounts
│   │       └── symbols.c       # Kernel POSIX export table
│   └── duneos_loader/          # ELF loader
│       ├── include/duneos/
│       │   ├── loader.h        # scan/select/load/run/unload API
│       │   └── elf.h           # ELF32 types and constants
│       └── src/
│           └── loader.c        # ET_REL parser, relocation engine, app lifecycle
├── boards/
│   ├── esp32s3-devkitc/
│   │   ├── board_config.h
│   │   └── sdkconfig.defaults
│   └── m5stack-cardputer/
│       ├── board_config.h
│       └── sdkconfig.defaults
├── examples/
│   └── test_exit/              # Minimal app: calls duneos_exit(0)
├── tools/
│   └── dbt.py                  # DuneBuild Tool — build, deploy, inspect apps
├── .duneos_board               # Active board (gitignored, set per developer)
├── partitions.csv
└── sdkconfig.defaults          # Common ESP32-S3 settings
```

## Writing an App

```bash
cd examples
python ../tools/dbt.py new myapp
cd myapp
# edit myapp.c
python ../../tools/dbt.py build
python ../../tools/dbt.py deploy E:\
```

Apps are plain C with a `manifest.json` and an `app_main()` entry point:

```c
void app_main(void)
{
    write(1, "hello\n", 6);
    extern void duneos_exit(int);
    duneos_exit(0);
}
```

Available symbols (resolved at load time from the kernel):

| Category | Symbols |
|---|---|
| File I/O | `open` `read` `write` `close` `lseek` `stat` `fstat` `unlink` `rename` |
| Directory | `opendir` `readdir` `closedir` |
| Memory | `malloc` `free` `realloc` `calloc` |
| Threads | `pthread_create/join/exit` `pthread_mutex_*` `sem_*` |
| Time | `clock_gettime` `gettimeofday` `usleep` `sleep` `nanosleep` |
| String | `memcpy` `memset` `strlen` `strcpy` `sprintf` `snprintf` … |
| Lifecycle | `duneos_exit(int code)` |

`printf` is **not** exported. Use `write(STDOUT_FILENO, buf, len)` or `dprintf(1, "...")`.

## Board Support

Board is selected at build time via `.duneos_board` file (or `DUNEOS_BOARD` CMake variable).
Each board has a directory under `boards/` with:
- `board_config.h` — pin assignments, hardware config
- `sdkconfig.defaults` — flash size, PSRAM mode, etc.

PSRAM allocation is **automatic**: if `CONFIG_SPIRAM=y` in the board sdkconfig, app sections are allocated in PSRAM. Otherwise DRAM is used (CardPuter has no PSRAM).

## Architecture

```
┌─────────────────────────────────────┐
│         Applications (.elf)          │  ET_REL ELF, SD card
├─────────────────────────────────────┤
│       Kernel ABI (function table)    │  POSIX subset, fixed symbols
├──────────────┬──────────────────────┤
│  ELF Loader  │  VFS + POSIX         │
├──────────────┴──────────────────────┤
│     FreeRTOS (via ESP-IDF)           │
├─────────────────────────────────────┤
│     BSP — board_config.h            │
├─────────────────────────────────────┤
│     ESP-IDF / Hardware               │
└─────────────────────────────────────┘
```

No MMU. ABI is a function pointer table, not CPU syscalls. Apps call kernel functions through the table — isolation is convention-based, not hardware-enforced.

## References

- [Flipper Zero FAP loader](https://github.com/flipperdevices/flipperzero-firmware) — ET_REL loader reference
- [ESP-IDF VFS](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/storage/vfs.html)
- [Xtensa ISA Reference](https://www.cadence.com/content/dam/cadence-www/global/en_US/documents/tools/ip/tensilica-ip/isa-summary.pdf) — relocation encoding
