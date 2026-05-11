# DuneOS Roadmap

## Phase 1 — Kernel foundations ✅ DONE

- [x] VFS init — SD card mount via SPI+FatFS at `/sd`
- [x] App discovery — `duneos_loader_scan()` walks `/sd/apps/*.elf`
- [x] Embedded manifest — JSON in `.duneos_manifest` ELF section (no filesystem manifest)
- [x] ABI definition — `duneos_symbol_t` function pointer table, `duneos_app_manifest_t`
- [x] ELF loader — ET_REL parse, section loading, Xtensa relocations (R_XTENSA_32, SLOT0_OP, ASM_EXPAND)
- [x] Symbol resolution — POSIX export table in `symbols.c`
- [x] App lifecycle — load → run → unload, `duneos_exit()` via `vTaskSuspend`
- [x] Autoboot — `/sd/autoboot` text file, fallback to first app
- [x] Board config — `.duneos_board` file, per-board `sdkconfig.defaults` + `board_config.h`
- [x] `dbt.py` — build tool: compile, link (ET_REL), embed manifest, deploy to SD
- [x] Memory — `#ifdef CONFIG_SPIRAM` auto-selects PSRAM or DRAM for app sections
- [x] **First successful boot: test_exit.elf loaded and jumped to on M5Stack CardPuter**

---

## Phase 2 — VFS & POSIX completeness ✅ DONE

- [x] `/tmp` — RAM-backed tmpfs: heap-allocated buffers, mutex-protected, 16 file slots (`vfs_tmp.c`)
- [x] `/dev/uart0` — read/write via `uart_read_bytes`/`uart_write_bytes`; UART driver installed at VFS init (`vfs_dev.c`)
- [x] `/dev/null`, `/dev/zero` — standard POSIX character devices (`vfs_dev.c`)
- [x] `ioctl` exported in symbol table; VFS layer already supported it via `esp_vfs_t.ioctl`
- [x] `dprintf` exported; stdout fd routing via `esp_vfs_dev_uart_use_driver(0)` after UART driver install
- [x] `mkdir`, `rmdir`, `statvfs` exported in symbol table (FatFS VFS already implements them on `/sd`)
- [x] App stdout capture — `duneos_loader_run_captured()`: dup2 redirects fd 1 to `/tmp/.duneos_stdout`, restores after app exits, returns heap buffer; `dup`/`dup2` exported to apps

---

## Phase 3 — Kernel logging overhaul ✅ DONE

- [x] `klog_i/w/e/d` API in `klog.h` — macros call `klog_write()` or compile to nothing
- [x] `CONFIG_DUNEOS_KERNEL_SILENT` — add to `sdkconfig.defaults` to silence all kernel logs
- [x] 4 KB ring buffer in DRAM; spinlock-protected (portMUX, ISR-safe); wraps on overflow
- [x] `/dev/klog` — read-only device; each open starts from oldest buffered byte
- [x] Debug build forwards every message to ESP_LOGx for USB/UART console visibility
- [x] All `ESP_LOGx` in `loader.c`, `vfs.c`, `vfs_tmp.c`, `vfs_dev.c`, `task.c`, `main.c` migrated

---

## Phase 4 — ELF loader hardening & multi-app ✅ DONE

- [x] Additional Xtensa relocation types (`R_XTENSA_DIFF8/16/32`)
- [ ] Stack canary per app task
- [ ] Per-app exception handler — catch crash, log to `/dev/klog`, unload without rebooting kernel
- [ ] Task WDT: supervisor feeds WDT on behalf of app, kicks app on timeout
- [x] Multi-app: run multiple apps concurrently as separate FreeRTOS tasks (supervisor + 4 slots)
- [x] App-to-app messaging (`duneos_send` / `duneos_recv` via per-slot FreeRTOS queues)
- [x] Permission enforcement — `required_perm` field in `duneos_symbol_t`, checked at resolution time
- [x] Custom file extension `.dap` (DuneOS Application) — loader accepts both `.dap` and `.elf`

---

## Phase 5 — Shell ✅ DONE

**Goal: a usable interactive shell over `/dev/uart0`.**

The shell is a regular DuneOS app (`.dap` file on SD) — it is not part of the kernel.
It uses only exported POSIX symbols: `read`, `write`, `opendir`, `readdir`, `open`, `stat`, etc.

Built-in commands (implemented inside the shell app, no child process fork):

- [x] `ls [-l] [path]` — list directory contents
- [x] `cat <file>` — print file to stdout
- [x] `echo <text>` — print text
- [x] `cd <path>` / `pwd` — navigate filesystem
- [x] `mkdir <path>` / `rm <file>` / `mv <src> <dst>` — filesystem ops
- [x] `run <app>` — load and run a DuneOS app, wait for `duneos_exit()`
- [x] `free` — show available heap (DRAM and PSRAM if present)
- [x] `klog` — dump kernel ring buffer (equivalent of `cat /dev/klog`)
- [x] `reboot` — restart ESP32 via `esp_restart()` (exported in symbol table)
- [x] Line editing: backspace, left/right arrows (via ANSI escape sequences)
- [x] History: up/down arrows cycle through last N commands (stored in RAM, 16-entry ring)

---

## Phase 6 — BSP generator ✅ DONE

- [x] YAML schema for board descriptors (pins, flash, PSRAM, peripherals)
- [x] `duneos-bspgen.py` tool (Python): `boards/myboard.yaml` → `boards/myboard/board_config.h`; `--dry-run` and `--out` flags
- [x] Reference BSPs: esp32s3-devkitc, m5stack-cardputer, lilygo-t7-s3
- [ ] Validate board config at build time (missing pins → CMake fatal error with helpful message)

---

## Phase 7 — App SDK & developer experience ✅ DONE

- [ ] Public headers installable separately (no full DuneOS tree needed)
- [x] `dbt.py new <name>` template covers GPIO, UART, threads use cases
- [x] `dbt.py info` shows manifest content + memory footprint estimate (.text, .rodata, .data, .bss)
- [x] Demo apps: `hello_world`, `uart_echo`
- [ ] Documentation: porting a C app to DuneOS
- [ ] App store concept: index of `.dap` files shareable between users

---

## Phase 8 — Hardware abstraction layer (HAL devices)

**Design principle: everything is a file.**

The kernel initialises hardware buses once at boot and exposes them as `/dev` entries.
Apps access hardware through standard POSIX `open/read/write/ioctl` — they never call
ESP-IDF driver APIs directly.  This means a `.dap` binary runs unchanged on CardPuter,
LilyGo T-Embed CC1101, M5Stick, Kincony A16, etc., as long as `board_config.h` names
the right pins.

**Kernel responsibilities (in `vfs_dev.c` / new `vfs_hw.c`):**

| `/dev` entry | Backed by | Notes |
|---|---|---|
| `/dev/gpiochip0` | `esp_driver_gpio` | Native ESP32-S3 GPIO lines; ioctl-based (see below) |
| `/dev/i2c-0`, `/dev/i2c-1` | `esp_driver_i2c` | Raw I2C bus; apps speak device protocol in userspace |
| `/dev/spi-1`, `/dev/spi-2` | `esp_driver_spi` | Raw SPI bus; CS line passed via ioctl |
| `/dev/fb0` | board-specific LCD driver | Framebuffer: `write()` pushes pixels; `ioctl` for resolution/rotation |
| `/dev/input/event0` | GPIO ISR or keyboard matrix | Blocking `read()` returns `input_event_t` structs (like Linux evdev) |
| `/dev/net/wlan0` | `esp_wifi` | Future — raw L2 frames or lwIP socket passthrough |

**Userspace responsibilities (app `.dap` libraries):**

- GPIO expanders behind I2C (SX1509, PCF8574, MCP23017): app opens `/dev/i2c-0`
  and speaks the chip's protocol — no kernel changes needed per chip.
- Display drivers (SSD1306, ST7789, GC9A01): app either draws to `/dev/fb0`
  (kernel provides the framebuffer) or speaks SPI directly via `/dev/spi-1`.
- Wireless protocols (CC1101, LoRa): app opens `/dev/spi-1`, implements the
  register protocol.  No kernel changes per radio chip.
- Network stack: apps use standard BSD sockets (`socket/connect/send/recv`)
  already exported in the symbol table via lwIP.

**GPIO ioctl API (inspired by Linux `linux/gpio.h`):**

```c
/* ioctl request codes on /dev/gpiochip0 */
#define GPIOCHIP_GET_INFO    0x01  /* → gpiochip_info_t  */
#define GPIOCHIP_SET_DIR     0x02  /* ← gpio_req_t {line, dir=INPUT|OUTPUT} */
#define GPIOCHIP_GET_VALUE   0x03  /* ← gpio_req_t {line} → .val */
#define GPIOCHIP_SET_VALUE   0x04  /* ← gpio_req_t {line, val} */
#define GPIOCHIP_SET_PULL    0x05  /* ← gpio_req_t {line, pull=NONE|UP|DOWN} */
```

**Roadmap items:**

- [ ] `/dev/gpiochip0` — native GPIO lines via ioctl (see API above)
- [ ] `/dev/i2c-0` — raw I2C bus; `write()` = transmit, `read()` = receive; `ioctl` for speed/addr
- [ ] `/dev/spi-1` — raw SPI; `write()` = full-duplex transfer; `ioctl` for mode/speed/CS
- [ ] `/dev/fb0` — kernel-side framebuffer backed by `board_config.h` display driver
- [ ] `/dev/input/event0` — evdev-style input events (buttons, keyboard matrix)
- [ ] BSP YAML: add `display`, `i2c`, `spi`, `gpio` sections for all target boards
  (CardPuter keyboard matrix, M5Stick buttons, T-Embed CC1101 SPI bus, Kincony A16 relay GPIOs)
- [ ] Permission bits: `DUNEOS_PERM_GPIO`, `DUNEOS_PERM_I2C`, `DUNEOS_PERM_SPI` enforced in symbol table

---

## Known technical debt

| Item | Notes |
|---|---|
| Stack canary per app task | Not yet implemented |
| Per-app exception handler | Not yet implemented — crash still reboots kernel |
| Task WDT supervision | Supervisor does not yet feed WDT on app's behalf |
| malloc/free exported from kernel heap | Should allocate from per-app PSRAM budget |
| FAT SD filenames are 8.3 uppercase | Long filename support depends on FatFS LFN config |
| BSP build-time validation | Missing pins do not yet cause CMake fatal error |
