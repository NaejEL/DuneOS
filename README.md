# DuneOS

A minimalist OS for ESP32-S3 microcontrollers that dynamically loads separately-compiled applications (`.dap` files) at runtime — inspired by the Flipper Zero `.fap` model.

Built on ESP-IDF v6.0.1. No Linux. No MMU. Just FreeRTOS, a POSIX subset (PicoLibc + `libdune.a`), an ELF loader, and a YAML-driven init system.

## Status (2026-05)

DuneOS is past the POC stage. The kernel boots from on-chip flash even without an SD card, loads apps from `/flash/bin/`, `/sd/bin/`, or `/sd/apps/`, and exposes a stable typed ABI (v3) to userspace via `libdune.a`. The full phase status lives in [`ROADMAP_v2.md`](ROADMAP_v2.md); a quick snapshot:

| Phase | Status |
| --- | --- |
| 1–14 — Kernel, VFS, GPIO, I2C, SPI, input, display, WiFi, sockets | ✅ |
| 16 — Shell built-ins vs PATH bins (`system/bin/`) | ✅ |
| 17 — Tooling DX (`dbt` package, TUI, `duneos.yaml`, `init.yaml`) | ✅ |
| 18 — libgfx display abstraction (`sdk/display/gfx.c`) | ✅ |
| 19 — Flash storage, boot without SD (`sysbin` LittleFS) | ✅ |
| 20 — Memory hardening (heap, WDT, exception handler, ptr validation) | 🟡 partial — stack canary + TLSF userspace pending |
| 21 — Toolchain plugin model (`tools/dbt/toolchain/`) | ✅ |
| 22 — ABI v3 typed dispatch table + `libdune.a` + PicoLibc | ✅ |
| 23 — USB device subsystem (TinyUSB MSC + CDC, `usb_shell`) | ✅ |
| 24 — DHI (DuneOS Hardware Interface — HAL scope) | ✅ |
| 24.5 — Design Decisions / ADR (`docs/adr/`) | ✅ (13 ADRs) |
| 24.7 — Safe boot & recovery (circuit breaker, hold-key, `--safe` flash) | pending |
| 25 — `dbt system` image recipes | pending |
| 26 — OSAL + scheduler portability (incl. `task.h`+`supervisor.h` → `int`/-errno) | pending |
| 27 — Native VFS + networking (incl. `vfs.h` → `int`/-errno) | pending |
| 28–29 — RISC-V Espressif + first non-ESP-IDF port (RP2040) | pending |

> **Note** : the 4 core public headers (`init.h`/`task.h`/`vfs.h`/`supervisor.h`) still return `esp_err_t` as of 2026-05. Their migration to POSIX-style `int` (-errno) is distributed across Phases 26-27 (each header migrates when its underlying API actually changes — avoids throwaway translation code).

Validated boards: **M5Stack CardPuter** (ESP32-S3FN8, 8 MB flash, no PSRAM), **LilyGo T-Embed CC1101** (ESP32-S3 + 8 MB OPI PSRAM + BQ27220), **ESP32-S3-DevKitC** (reference).

## Quick Start

### Prerequisites

- ESP-IDF v6.0.1 (`IDF_PATH` set or `.duneos_idf` pointing at the install)
- VS Code + ESP-IDF extension, or `idf.py` in PATH
- Python 3.10+ with `pip install textual pyyaml littlefs-python esptool`
- `xtensa-esp32s3-elf-gcc` (ships with ESP-IDF)

### 1. Select your board and port

```bash
echo m5stack-cardputer > .duneos_board    # gitignored, per-developer
echo /dev/ttyACM0     > .duneos_port      # used by dbt flashimg
python tools/duneos-bspgen.py boards/m5stack-cardputer/board.yaml
```

Bspgen regenerates `board_config.h`, `sdkconfig.board`, `partitions.csv`, and `idf_target.txt`. Never hand-edit those files — re-run bspgen after editing `board.yaml`.

### 2. Build & flash the kernel

```bash
idf.py build
idf.py -p /dev/ttyACM0 flash
```

Switching boards requires a Full Clean (`idf.py fullclean`) — CMake aborts otherwise.

### 3. Build & flash the sysbin image (init + apps in flash, no SD needed)

```bash
python tools/dbt.py flashimg
```

This packs `boards/<board>/init.yaml` + every embedded `.dap` into a LittleFS image and writes it to the `sysbin` partition. The kernel mounts it at `/flash` at boot.

### 4. (Optional) Build & deploy an app to SD

```bash
cd apps/hello_world
python ../../tools/dbt.py build
python ../../tools/dbt.py deploy /run/media/$USER/SDCARD
```

`dbt build` invokes the toolchain plugin for the active board, builds `libdune.a` (cached per arch), compiles app sources, and produces a `.dap` ELF with an embedded manifest.

### 5. Interactive TUI

```bash
python tools/dbt.py tui
```

A Textual full-screen UI: board picker, port picker, init-config editor, build/flash actions. Handy when you don't want to remember CLI flags.

## Writing an App

```bash
cd apps
python ../tools/dbt.py new myapp
cd myapp
# edit myapp.c and duneos.yaml
python ../../tools/dbt.py build
python ../../tools/dbt.py deploy /mnt/sdcard
```

Apps are plain C with a `duneos.yaml` manifest and an `app_main()` entry. With `libdune.a` (linked automatically by dbt) you write standard POSIX:

```c
#include <unistd.h>
#include <stdio.h>
#include <duneos/libdune.h>

void app_main(void)
{
    dprintf(1, "hello from DuneOS\n");
    duneos_exit(0);
}
```

**`duneos.yaml`:**

```yaml
name: myapp
version: "0.1.0"
required_abi_version: 3
permissions: 96            # FS_READ | FS_WRITE
stack_size: 8192
heap_size: 16384           # optional, defaults to 8 KB
sources:
  - myapp.c
```

Permission bits: `GPIO=1` `UART=2` `SPI=4` `I2C=8` `NET=16` `FS_READ=32` `FS_WRITE=64` `INPUT=128` `NET_RAW=256`.

> **Security note — permissions are advisory, not enforced.** DuneOS runs on microcontrollers without an MMU. The permission bitmask declares what an app *intends* to use; the loader binds or refuses kernel symbols accordingly. **A malicious or buggy app can bypass these checks by scanning memory and calling kernel functions directly.** Only run `.dap` files from sources you trust. See [ADR 011](docs/adr/011-threat-model.md) for the full threat model. Signature-based provenance (Ed25519) is planned for Phase 32.

## Init system (`init.yaml`)

`init.yaml` lists the boot services. The kernel reads `/flash/init.yaml` first, then `/sd/init.yaml` (per-board flash list is hand-edited in `boards/<board>/init.yaml` and copied as-is by `dbt flashimg`).

```yaml
services:
  - path: /flash/bin/usb_shell.dap
    restart: always
  - path: /flash/bin/wifi_daemon.dap
    restart: on-failure
```

Dedup is by app *name* (basename without `.dap`), so a service in flash wins over the same service in `/sd/init.yaml`.

| Field | Required | Values | Default |
| --- | --- | --- | --- |
| `path` | yes | absolute path to `.dap` | — |
| `restart` | no | `no` / `always` / `on-failure` | `no` |

## Repository Structure

See [CLAUDE.md](CLAUDE.md) for the authoritative tree and the rules around `arch.cmake` guards, third-party libs (cJSON, LittleFS in `third_party/`; TinyUSB as ESP-IDF managed component), and the kernel↔userspace boundary.

Headline directories:

```text
DuneOS/
├── main/                      Kernel entry (VFS init → supervisor → init.yaml)
├── kernel/
│   ├── duneos_kernel/         Kernel core (VFS, supervisor, klog, api, drivers/)
│   └── duneos_loader/         ELF ET_REL loader (Xtensa relocations vendored)
├── arch/
│   ├── xtensa_esp32s3/        HAL impl + arch.cmake self-selection
│   └── riscv32/               Skeleton (Phase 28)
├── libdune/src/               libdune.a sources (ptr/fs/mem/thread/time/sys)
├── sdk/display/               Userspace display SDK (libst7789.c + gfx.c)
├── boards/<board>/            board.yaml + bspgen-generated headers/configs + init.yaml
├── system/
│   ├── shell_core/            Shared shell engine (VT100)
│   ├── usb_shell/             USB CDC shell (system app)
│   └── bin/                   System utilities (ls, cat, free, klog, gpio, ifconfig, ping, ...)
├── apps/                      Developer apps (hello_world, gfx_demo, tcp_client, ...)
├── tools/
│   ├── dbt/                   DuneBuild Tool (cli, builder, deploy, kernel, flashimg, tui, toolchain/)
│   └── duneos-bspgen.py       BSP generator (board.yaml → headers + sdkconfig + partitions)
└── third_party/               cJSON + LittleFS (git submodules)
```

## Architecture

```text
┌────────────────────────────────────────────────────┐
│  Apps (.dap)         Per-chip drivers (userspace)  │   ET_REL ELF
│   ↳ libdune.a → PicoLibc + duneos_api_t            │
├────────────────────────────────────────────────────┤
│  duneos_api_t  (ABI v3, typed dispatch table)      │   loader writes &api into app
│  Permissions   (bitmask per service)               │   checked at load time
├──────────────┬─────────────────────────────────────┤
│ ELF Loader   │  VFS                                │
│              │   /flash  (LittleFS, sysbin)        │
│              │   /sd     (FatFS, optional)         │
│              │   /tmp    (RAM tmpfs)               │
│              │   /dev/uart0, /dev/klog, /dev/null  │
│              │   /dev/gpiochip0                    │
│              │   /dev/i2c-0, /dev/spi-1            │
│              │   /dev/disp0, /dev/fb0              │
│              │   /dev/input/event0                 │
│              │   /dev/raw80211, /dev/ttyUSB0       │
│              │   /dev/battery0                     │
├──────────────┼─────────────────────────────────────┤
│ Supervisor   │   Init (init.yaml: /flash then /sd) │
│  4 slots     │   Restart policies                  │
│  IPC queues  │   Per-app heap + WDT + exc handler  │
├──────────────┴─────────────────────────────────────┤
│  HAL (hal_uart/gpio/i2c/spi/adc/time.h, pure C)    │   Phase 24a
├────────────────────────────────────────────────────┤
│  ESP-IDF v6.0.1 + FreeRTOS                         │
└────────────────────────────────────────────────────┘
```

No MMU. Isolation is convention + pointer validation at the syscall boundary (`check_user_ptr` / `check_app_writable_ptr`), not hardware-enforced.

## References

- [ROADMAP_v2.md](ROADMAP_v2.md) — phase-by-phase plan and current TODO list
- [CLAUDE.md](CLAUDE.md) — exhaustive design notes, hard-won lessons, conventions
- [Flipper Zero FAP loader](https://github.com/flipperdevices/flipperzero-firmware) — ET_REL loader reference
- [ESP-IDF VFS](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/storage/vfs.html)
- [Zephyr Device Tree](https://docs.zephyrproject.org/latest/build/dts/intro.html) — BSP generator inspiration
