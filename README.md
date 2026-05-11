# DuneOS

A minimalist OS for ESP32-S3 microcontrollers that dynamically loads separately-compiled applications (`.dap` files) from an SD card at runtime — inspired by Flipper Zero `.fap` files.

Built on ESP-IDF v5.x. No Linux. No MMU. Just FreeRTOS, a POSIX subset, an ELF loader, and an init system.

## Status

**Phase 9 complete** — kernel boots, mounts SD, reads `/sd/init.json` to launch services with configurable restart policies. Interactive shell ships as a system app. Supervisor handles up to 4 concurrent apps with IPC.

Tested on **M5Stack CardPuter** (ESP32-S3FN8, no PSRAM, 8 MB flash).

| Phase | Status | Summary |
| --- | --- | --- |
| 1 — Kernel foundations | ✅ | Boot, SD mount, ELF load & run |
| 2 — VFS & POSIX | ✅ | `/tmp`, `/dev/null/zero/uart0`, ioctl, stdout capture |
| 3 — Kernel logging | ✅ | klog ring buffer, `/dev/klog` |
| 4 — Loader hardening | ✅ | Multi-app supervisor, IPC, permissions, `.dap` extension |
| 5 — Shell | ✅ | Interactive POSIX shell (`system/shell/`) |
| 6 — BSP generator | ✅ | YAML → `board_config.h` |
| 7 — App SDK & DX | ✅ | `dbt.py` build/deploy/info, `hello_world`, `uart_echo` |
| 8 — GPIO | ✅ | `/dev/gpiochip0`, `gpio_ioctl.h`, shell `gpio` command |
| 9 — Init system | ✅ | `/sd/init.json`, restart policies, `duneos_service_ready()` |
| 10 — I2C + battery | ⬜ | `/dev/i2c-0`, `/dev/battery0` |
| 11 — SPI | ⬜ | `/dev/spi-1` |
| 12 — Input | ⬜ | `/dev/input/event0`, keyboard daemon |
| 13 — Framebuffer | ⬜ | `/dev/fb0` (PSRAM boards only) + display SDK |
| 14 — WiFi daemon | ⬜ | Userspace `wifi_daemon.dap` |
| 15 — Portability | ⬜ | `libgfx`, `board.info`, multi-target SDK |

## Quick Start

### Prerequisites

- ESP-IDF v5.5.1 (`IDF_PATH` set, toolchain in PATH)
- VS Code + ESP-IDF extension, or `idf.py` in PATH
- Xtensa toolchain: `xtensa-esp32s3-elf-gcc` (ships with ESP-IDF)

### 1. Select your board

```bash
echo m5stack-cardputer > .duneos_board
```

`.duneos_board` is gitignored — each developer sets their own. Default fallback is `esp32s3-devkitc`.

### 2. Build and flash the kernel

Via VS Code: **Ctrl+E B** to build, **Ctrl+E F** to flash, **Ctrl+E M** to monitor.

Or from the terminal:

```bash
idf.py build
idf.py -p COM13 flash monitor
```

### 3. Build the shell

```bash
cd system/shell
python ../../tools/dbt.py build
python ../../tools/dbt.py deploy E:\   # E: = your SD card drive letter
```

### 4. Prepare the SD card

The simplest setup uses `init.json` to declare what to run at boot:

```
SD:\
├── apps\
│   └── shell.dap          ← deployed by dbt.py
└── init.json              ← boot configuration (see below)
```

**`/sd/init.json`:**

```json
{
  "version": 1,
  "services": [
    { "path": "/sd/apps/shell.dap", "restart": "no" }
  ]
}
```

If `init.json` is absent, DuneOS falls back to the legacy autoboot path: it scans `/sd/apps/` and launches the app named in `/sd/autoboot` (or the first found).

### 5. Boot

Insert SD, power on. Expected output:

```
I duneos: DuneOS 0.2.0 (ABI v1)
I duneos/vfs: SD mounted at /sd
I duneos/init: loaded 1 service(s) from /sd/init.json
I duneos: starting service '/sd/apps/shell.dap' (restart=0)
I duneos/supervisor: launched 'shell' (stack 16384 B, restart=0)

DuneOS shell v0.1 — type 'help' for commands
cwd: /sd
[/sd]$
```

## init.json Reference

`/sd/init.json` lists services to launch at boot. Services start in order; each gets its own FreeRTOS task.

```json
{
  "version": 1,
  "services": [
    { "path": "/sd/apps/shell.dap",      "restart": "no" },
    { "path": "/sd/apps/wifi.dap",       "restart": "always" },
    { "path": "/sd/apps/watchdog.dap",   "restart": "on-failure" }
  ]
}
```

| Field | Required | Values | Default |
| --- | --- | --- | --- |
| `path` | yes | absolute path to `.dap` on SD | — |
| `restart` | no | `"no"` `"always"` `"on-failure"` | `"no"` |

**Restart policies:**

- `no` — service exits and is not restarted. `wait_all()` returns once all `no`-policy services exit.
- `always` — service is relaunched unconditionally after every exit. The system runs indefinitely.
- `on-failure` — service is relaunched only when it exits with a non-zero code.

Services can call `duneos_service_ready()` to signal that they have finished initialising (logged; future phases use it for dependency ordering).

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
    extern void duneos_exit(int);
    write(STDOUT_FILENO, "hello\n", 6);
    duneos_exit(0);
}
```

**`manifest.json`:**

```json
{
  "name": "myapp",
  "version": "0.1.0",
  "required_abi_version": 1,
  "permissions": 96,
  "stack_size": 8192
}
```

Permission bits: `GPIO=1` `UART=2` `SPI=4` `I2C=8` `NET=16` `FS_READ=32` `FS_WRITE=64`.

### Available kernel symbols

| Category | Symbols |
|---|---|
| File I/O | `open` `read` `write` `close` `lseek` `stat` `fstat` `ioctl` `dprintf` `dup` `dup2` |
| Directory | `opendir` `readdir` `closedir` `mkdir` `rmdir` `unlink` `rename` |
| Memory | `malloc` `free` `realloc` `calloc` |
| Threads | `pthread_create/join/exit/self` `pthread_mutex_*` `sem_*` |
| Time | `clock_gettime` `gettimeofday` `usleep` `sleep` `nanosleep` |
| String | `memcpy` `memmove` `memset` `memcmp` `strlen` `strcpy` `strncpy` `strlcpy` `strcmp` `sprintf` `snprintf` `vsnprintf` `sscanf` `strerror` … |
| GPIO | `open("/dev/gpiochip0")` + `ioctl` with `gpio_ioctl.h` structs |
| System | `esp_restart` `esp_get_free_heap_size` |
| Lifecycle & IPC | `duneos_exit` `duneos_run` `duneos_send` `duneos_recv` `duneos_service_ready` |
| Loader | `duneos_loader_load` `duneos_loader_run` `duneos_loader_unload` |

`printf` is **not** exported — use `write(STDOUT_FILENO, …)` or `dprintf(1, "…")`.  
`vTaskDelay` is **not** exported — use `usleep()` / `nanosleep()`.

## System Apps vs Examples

| Directory | Purpose |
|---|---|
| `system/` | Apps that ship with DuneOS (shell, future daemons). Deploy these to every SD card. |
| `examples/` | Developer demos showing API usage (`test_exit`, `hello_world`, `uart_echo`). |

Both use `dbt.py` to build and deploy.

## Repository Structure

```
DuneOS/
├── main/
│   └── main.c                      # Boot: VFS → loader → supervisor → init.json → wait
├── components/
│   ├── duneos_kernel/
│   │   ├── include/duneos/
│   │   │   ├── abi.h               # DUNEOS_ABI_VERSION, duneos_symbol_t, manifest
│   │   │   ├── init.h              # duneos_init_load(), duneos_service_desc_t
│   │   │   ├── klog.h              # klog_i/w/e/d, ring buffer API
│   │   │   ├── supervisor.h        # launch/launch_policy/send/recv/wait_all
│   │   │   ├── task.h              # FreeRTOS abstraction
│   │   │   └── vfs.h               # VFS init/mount API
│   │   └── src/
│   │       ├── init.c              # /sd/init.json parser (cJSON)
│   │       ├── klog.c              # 4 KB ring buffer, /dev/klog
│   │       ├── supervisor.c        # 4-slot supervisor, restart policies, IPC
│   │       ├── vfs.c               # SD (SPI+FatFS), /tmp, /dev
│   │       ├── vfs_tmp.c           # /tmp — heap RAM filesystem
│   │       ├── vfs_dev.c           # /dev/null, zero, uart0, klog, gpiochip0
│   │       └── symbols.c           # Kernel export table
│   └── duneos_loader/
│       ├── include/duneos/
│       │   ├── loader.h            # scan/select/load/run/unload API
│       │   └── elf.h               # ELF32 structs, Xtensa relocation constants
│       └── src/
│           └── loader.c            # ET_REL loader: parse, relocate, resolve, run
├── system/
│   └── shell/                      # Interactive shell — ships with DuneOS
│       ├── shell.c                 # ls/cat/cd/run/gpio/klog/...
│       └── manifest.json
├── boards/
│   ├── esp32s3-devkitc/            # board_config.h + sdkconfig.defaults (PSRAM)
│   ├── m5stack-cardputer/          # board_config.h + sdkconfig.defaults (no PSRAM)
│   └── lilygo-t7-s3.yaml           # BSP generator reference
├── examples/
│   ├── test_exit/                  # Minimal: duneos_exit(0)
│   ├── hello_world/                # Write "Hello World" then exit
│   └── uart_echo/                  # Echo loop on /dev/uart0
├── tools/
│   ├── dbt.py                      # DuneBuild Tool: new/build/info/deploy/clean
│   └── duneos-bspgen.py            # BSP generator: board YAML → board_config.h
├── .duneos_board                   # Active board (gitignored)
├── partitions.csv                  # 1.5 MB kernel + 6.4 MB FAT storage
└── sdkconfig.defaults              # FreeRTOS 1 kHz, Task WDT
```

## Board Support

Board is selected at build time via `.duneos_board` (or `DUNEOS_BOARD` CMake variable). Each board has a directory under `boards/` with `board_config.h` (pins, hardware config) and `sdkconfig.defaults` (flash size, PSRAM).

PSRAM allocation is automatic: if `CONFIG_SPIRAM=y`, app ELF sections are allocated in PSRAM. Otherwise DRAM is used. The CardPuter has no PSRAM — keep app footprints small.

## Architecture

```
┌─────────────────────────────────────────────────┐
│    Applications (.dap)                           │  ET_REL ELF, /sd/apps/
│    USERSPACE: chip drivers as .c libraries       │
├─────────────────────────────────────────────────┤
│    Kernel ABI (function pointer table)           │  duneos_symbol_table_get()
│    Permissions (bitmask per symbol)              │  checked at load time
├───────────────┬─────────────────────────────────┤
│  ELF Loader   │  VFS + POSIX layer              │
│               │    /sd    — FAT on SD card       │
│               │    /tmp   — heap RAM tmpfs        │
│               │    /dev/null, /dev/zero           │
│               │    /dev/uart0, /dev/klog          │
│               │    /dev/gpiochip0                 │
├───────────────┼─────────────────────────────────┤
│  Supervisor   │  Init system (init.json)         │
│  4 app slots  │  Restart policies                │
│  IPC queues   │  duneos_service_ready()          │
├───────────────┴─────────────────────────────────┤
│     FreeRTOS (via ESP-IDF)                       │
├─────────────────────────────────────────────────┤
│     BSP — board_config.h                        │
└─────────────────────────────────────────────────┘
```

No MMU. ABI is a function pointer table, not CPU syscalls. Isolation is convention-based, not hardware-enforced.

## References

- [Flipper Zero FAP loader](https://github.com/flipperdevices/flipperzero-firmware) — ET_REL loader reference
- [ESP-IDF VFS](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/storage/vfs.html)
- [ESP-IDF cJSON](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/protocols/json.html) — used by init.json parser
- [Zephyr Device Tree](https://docs.zephyrproject.org/latest/build/dts/intro.html) — BSP generator inspiration
