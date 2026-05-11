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
- [x] Multi-arch CPU support: `DUNEOS_CPU_ARCH` ("xtensa"|"riscv") + `DUNEOS_ELF_MACHINE` (EM_XTENSA|EM_RISCV) emitted for every board
- [x] Extended CPU family: esp32c2, esp32c3, esp32c5, esp32c6, esp32h2, esp32p4 all recognised; RISC-V boards get correct arch constants
- [x] Reference RISC-V BSPs: esp32c3-devkitc, esp32p4-devkitm
- [ ] Validate board config at build time (missing pins → CMake fatal error with helpful message)
- [ ] Generate `sdkconfig.board` Kconfig fragment alongside `board_config.h` — controls which kernel drivers are compiled in (see kernel driver selection model below)

---

## Phase 7 — App SDK & developer experience ✅ DONE

- [ ] Public headers installable separately (no full DuneOS tree needed)
- [x] `dbt.py new <name>` template covers GPIO, UART, threads use cases
- [x] `dbt.py info` shows manifest content + memory footprint estimate (.text, .rodata, .data, .bss)
- [x] `dbt.py build` arch-aware: reads board YAML → detects Xtensa vs RISC-V → selects correct toolchain prefix and CFLAGS (`-mlongcalls` Xtensa-only; `-mno-relax -mabi=ilp32` RISC-V)
- [x] Toolchain auto-discovery: Xtensa tries `xtensa-{cpu}-elf-` then `xtensa-esp-elf-`; RISC-V tries `riscv32-esp-elf-` (ESP-IDF v5) then `riscv32-unknown-elf-` (distro packages)
- [x] Demo apps: `hello_world`, `uart_echo`
- [ ] Documentation: porting a C app to DuneOS
- [ ] App store concept: index of `.dap` files shareable between users

---

## Build system

**Board selection via `.duneos_board`** — the active board name is stored in `.duneos_board`
(gitignored; each developer sets their own). `CMakeLists.txt` always reads this file, ignoring
any `-DDUNEOS_BOARD=` cmake argument. The VS Code extension's `idf.cmakeAdditionalArgs`
must therefore be left empty; the extension re-runs cmake automatically when `.duneos_board` changes.

**Board switch detection** — `CMakeLists.txt` writes a stamp file `build/duneos_last_board.stamp`
at configure time. If the board name in the stamp differs from `.duneos_board`, the build aborts
with a FATAL_ERROR directing the user to do a Full Clean. This prevents silent sdkconfig drift.

**IDF_TARGET** — each board directory contains `idf_target.txt` (generated by `duneos-bspgen.py`).
`CMakeLists.txt` reads it and sets `IDF_TARGET` before `include(project.cmake)`. No need to set
`IDF_TARGET` manually in VS Code settings.

**`duneos-bspgen.py` outputs (per board):**

| File | Purpose |
| --- | --- |
| `board_config.h` | Pin assignments and hardware constants (`#define DUNEOS_*`) |
| `idf_target.txt` | Single-line `IDF_TARGET` value read by CMakeLists.txt |
| `sdkconfig.board` | Kconfig fragment: `CONFIG_DUNEOS_DRV_*=y` entries for the board |

The `sdkconfig.board` file is informational — the actual driver selection is checked into
`boards/<board>/sdkconfig.defaults` and committed to git. Re-running bspgen regenerates it.

---

## Kernel driver selection model

The kernel binary is **board-specific** — same source tree, different binary per board.
This is the Linux Kconfig model applied to ESP32-S3: drivers are compiled in or out at build time,
not loaded at runtime (no MMU, no module loader).

`duneos-bspgen.py` generates three artefacts from a board YAML (see Build system section above).
The driver selection fragment uses `CONFIG_DUNEOS_DRV_*` keys:

```ini
# Example: boards/m5stack-cardputer/sdkconfig.defaults (driver section)
CONFIG_DUNEOS_DRV_NULL=y
CONFIG_DUNEOS_DRV_UART=y
CONFIG_DUNEOS_DRV_KLOG=y
CONFIG_DUNEOS_DRV_GPIO=y
CONFIG_DUNEOS_DRV_I2C=y
CONFIG_DUNEOS_DRV_BATTERY_ADC_SIMPLE=y
```

```ini
# Example: boards/lilygo-t-embed-cc1101/sdkconfig.defaults (driver section)
CONFIG_DUNEOS_DRV_NULL=y
CONFIG_DUNEOS_DRV_UART=y
CONFIG_DUNEOS_DRV_KLOG=y
CONFIG_DUNEOS_DRV_GPIO=y
CONFIG_DUNEOS_DRV_I2C=y
CONFIG_DUNEOS_DRV_BATTERY_BQ27220=y
```

**Kernel driver source layout:**

```text
components/duneos_kernel/src/drivers/
  drv_null.c              → /dev/null, /dev/zero
  drv_uart.c              → /dev/uart0
  drv_klog.c              → /dev/klog
  i2c_bus.c / i2c_bus.h   → shared I2C bus handle (used by drv_i2c + battery/bq27220)
  drv_i2c.c               → /dev/i2c-0
  gpio/
    drv_gpio.c            → /dev/gpiochip0
  battery/
    drv_battery_adc_simple.c  → /dev/battery0 (ADC voltage divider)
    drv_battery_bq27220.c     → /dev/battery0 (BQ27220 I2C fuel gauge)
```

Each driver exports a single `drv_<name>_register()` function called from `vfs_dev.c`
under a `#ifdef CONFIG_DUNEOS_DRV_*` guard. No driver code reaches the binary unless its
`CONFIG_DUNEOS_DRV_*=y` is set in the board's `sdkconfig.defaults`.

**Decision rule — kernel vs userspace:**

| Put it in kernel if | Put it in userspace if |
| --- | --- |
| Shared between multiple concurrent apps | Used by at most one app at a time |
| Requires a hardware ISR | Pure register-level protocol over a bus |
| Board-global resource (ADC, bus controller) | Per-chip protocol (ST7789, CC1101, MPU6050…) |
| Small runtime state (tens of bytes) | RAM cost too high for DRAM-only boards |

**Portability consequence:** a `.dap` app that links `libst7789.c` directly is tied to that display chip.
It will not run as-is on a board with a different display, and cannot share the screen with other apps.
This is an accepted limitation at the current scale — future SDK tooling (Phase 15) will address it
by providing a display-agnostic `libgfx` API with pluggable backends.

---

## Phase 8 — GPIO (`/dev/gpiochip0`, `/dev/gpiochipN`) ✅ DONE (native GPIO)

**Kernel space.** Foundation for every subsequent phase — SPI CS lines, interrupt pins, LEDs all go through here.

**Cross-family note:** `esp_driver_gpio` works identically on all ESP32 variants (Xtensa + RISC-V). `/dev/gpiochip0` is board-agnostic at the source level; `GPIO_NUM_MAX` is chip-specific and provided by ESP-IDF. The RISC-V loader support (R_RISCV_CALL, PCREL_HI20/LO12 pairs, etc.) was also completed as part of this phase to enable GPIO apps on C3/C6/H2/P4 targets.

**Kernel vs userspace boundary for GPIO:**

- **Native GPIO lines** → `/dev/gpiochip0` (kernel, `esp_driver_gpio`)
- **GPIO expanders behind I2C** (SX1509, PCF8574, MCP23017) → `/dev/gpiochip1`, `/dev/gpiochip2`, … (kernel)
  The kernel initialises the I2C communication to the expander at boot (declared in BSP YAML) and registers
  an additional `gpiochip` device with the same ioctl API. Apps see a uniform interface regardless of whether
  a line is native or behind an expander — same model as Linux `gpio_chip` registration.
- **App-specific GPIO-like devices** (LED drivers, PWM expanders over I2C) → userspace `.dap` library,
  speaks `/dev/i2c-0` directly. Not exposed as gpiochip because they are not general-purpose GPIO.

**BSP YAML extension for expanders:**

```yaml
gpio_expanders:
  - type: sx1509        # sx1509 | pcf8574 | mcp23017 | pca9555
    bus: i2c-0
    addr: 0x3E
    lines: 16
    name: gpiochip1     # → /dev/gpiochip1
```

**GPIO ioctl API (uniform across native + expander gpiochips, inspired by Linux `linux/gpio.h`):**

```c
#define GPIOCHIP_GET_INFO    0x01  /* → gpiochip_info_t {name, lines}          */
#define GPIOCHIP_SET_DIR     0x02  /* ← gpio_req_t {line, dir=INPUT|OUTPUT}    */
#define GPIOCHIP_GET_VALUE   0x03  /* ← gpio_req_t {line} → .val              */
#define GPIOCHIP_SET_VALUE   0x04  /* ← gpio_req_t {line, val}                */
#define GPIOCHIP_SET_PULL    0x05  /* ← gpio_req_t {line, pull=NONE|UP|DOWN}  */
#define GPIOCHIP_SET_IRQ     0x06  /* ← gpio_irq_req_t {line, edge, signo}    */
```

**Roadmap items:**

- [x] `/dev/gpiochip0` merged into `/dev` VFS (`vfs_dev.c`) — ESP-IDF rejects registering a sub-path when the parent prefix is already registered; single VFS handles all `/dev/*` entries including `gpiochip0`
- [x] `/dev` VFS `opendir`/`readdir` — `ls /dev` now lists all device nodes
- [x] Shell: synthetic `ls /` and `cd /` — ESP-IDF VFS has no root; mount points (dev, sd, tmp) enumerated in shell
- [x] Shell: `gpio` command — `info`, `get <pin>`, `set <pin> <val>`, `mode <pin> in|out`, `pull <pin> none|up|down`
- [x] RISC-V ELF loader — R_RISCV_32, CALL/CALL_PLT, PCREL_HI20/LO12_I/LO12_S, HI20/LO12, BRANCH, JAL, RVC_BRANCH/JUMP; HI20 cache for PCREL pair resolution; all Xtensa-specific code guarded by `CONFIG_IDF_TARGET_ARCH_XTENSA`
- [ ] BSP YAML: `gpio_expanders` section; kernel registers `/dev/gpiochip1..N` at boot
- [x] `DUNEOS_PERM_GPIO` permission bit enforced in symbol table (already defined in abi.h; shell uses permissions=127)

---

## Phase 9 — Init system (`/sd/init.json`)

**Kernel + supervisor extension.** Must land before WiFi daemon, display daemon, and other long-running
services make manual slot management unwieldy.

**Design:** `/sd/init.json` replaces the flat `/sd/autoboot` file. Backward compat: if `init.json` absent,
fall back to `autoboot`. The supervisor reads and executes `init.json` at boot; the shell can trigger
a re-read with a `reinit` command.

```json
{
  "version": 1,
  "services": [
    { "name": "wifi",    "exec": "wifi_daemon.dap", "type": "daemon",  "restart": "always",       "provides": ["network"] },
    { "name": "shell",   "exec": "shell.dap",        "type": "daemon",  "restart": "always",       "after": ["network"] },
    { "name": "ntp",     "exec": "ntp_sync.dap",     "type": "oneshot", "restart": "on-failure",   "after": ["network"] }
  ]
}
```

**Service types:**

- `daemon` — FreeRTOS task that runs indefinitely; occupies a supervisor slot
- `oneshot` — runs to `duneos_exit()`, slot is freed; used for setup tasks

**Restart policies:** `never` | `on-failure` | `always`

**Dependency resolution:** topological sort on `after` arrays at boot; trivial with ≤4 slots.
A service waits until all its `provides` tags are signalled ready by preceding services.

**New ABI symbol: `duneos_service_ready()`** — daemon calls this once initialised
(analogue of `sd_notify(READY=1)`). Unblocks services that declared `after: ["this_service_name"]`.
Implemented as a bitmask on `app_slot_t`; no dynamic allocation.

**Supervisor extensions needed:**

```c
typedef enum { RESTART_NEVER, RESTART_ON_FAILURE, RESTART_ALWAYS } restart_policy_t;
typedef enum { SLOT_EMPTY, SLOT_STARTING, SLOT_READY, SLOT_RUNNING, SLOT_STOPPED } slot_state_t;
// added to app_slot_t:
restart_policy_t  restart_policy;
slot_state_t      state;
char              name[16];
```

**Roadmap items:**

- [ ] `duneos_service_ready()` — new ABI symbol; sets slot state to READY, releases dependency waiters
- [ ] `restart_policy_t` + `slot_state_t` fields in `app_slot_t`
- [ ] `duneos_supervisor_init_from_json()` — parse `/sd/init.json`, topological sort, launch in order
- [ ] Fallback: if `init.json` absent, read `autoboot` (existing behaviour)
- [x] Shell: `services` built-in — lists slots with name/state/restart_count (like `systemctl status`)
- [x] Shell: `restart <name>` — force restart of a named daemon slot

---

## Phase 10 — I2C + battery (`/dev/i2c-0`, `/dev/battery0`) ✅ DONE

**Kernel space.** I2C is the most widely used sensor bus; battery monitoring is a board-level resource.

**Kernel vs userspace boundary for I2C:**

- **I2C bus controller** → `/dev/i2c-0` … `/dev/i2c-N` (kernel, `esp_driver_i2c`). Raw byte transfer.
- **Per-chip protocol** (MPU6050, SHT31, SSD1306 over I2C, …) → userspace `.dap` library.
  App opens `/dev/i2c-0`, calls `ioctl(I2C_SET_ADDR, 0x68)`, then `write()/read()` for register access.
  No kernel change per chip — same model as Linux `/dev/i2c-dev` + userspace `libi2c`.
- **GPIO expander initialisation** — exception: kernel talks I2C internally to expanders declared in BSP
  YAML (Phase 8), but that path is entirely within `vfs_hw.c`, not exposed as a raw bus transaction.

**I2C ioctl API (`duneos/i2c_ioctl.h`):**

```c
#define I2C_SET_ADDR      0x01  /* ← uint16_t* slave address (7-bit)           */
#define I2C_SET_SPEED     0x02  /* ← uint32_t* speed in Hz (accepted, future)   */
#define I2C_RDWR          0x03  /* ← i2c_rdwr_t* — write-then-read, no STOP     */

typedef struct {
    const uint8_t *tx_buf;  uint16_t tx_len;
          uint8_t *rx_buf;  uint16_t rx_len;
} i2c_rdwr_t;
```

**Battery management — `/dev/battery0` (`duneos/battery_ioctl.h`):**

DuneOS has no sysfs. The equivalent is `/dev/battery0` with an ioctl API, kernel-owned because
ADC is a hardware resource (like GPIO). Board type determines the backend:

| Charger / gauge | Interface | Backend |
|---|---|---|
| TP4057 (CardPuter) | Analog: CHRG/STBY GPIO + ADC | Kernel reads `board_config.h` ADC channel + GPIO pins |
| IP5306 (some M5Stack) | I2C 0x75 | Kernel reads I2C internally; same `/dev/battery0` interface |
| MAX17043 / BQ27220 | I2C coulomb counter | Kernel reads I2C internally |

```c
#define BATTERY_GET_INFO  0x01  /* → battery_info_t                           */

typedef struct {
    uint16_t voltage_mv;        /* raw cell voltage                            */
    uint8_t  percent;           /* 0–100, estimated from discharge curve       */
    uint8_t  status;            /* BATTERY_DISCHARGING | CHARGING | FULL       */
} battery_info_t;
```

**BSP YAML extension:**

```yaml
battery:
  type: adc_simple      # adc_simple | ip5306 | max17043 | bq27220
  adc_unit: ADC_UNIT_1  # (adc_simple only)
  adc_channel: 6        # ESP32-S3 ADC1 channel number (adc_simple only)
  full_mv: 4200
  empty_mv: 3300
  chrg_gpio: -1         # CHRG output of TP4057; -1 if not wired
```

**Roadmap items:**

- [x] `/dev/i2c-0` — raw I2C master (new ESP-IDF v5 `i2c_master` API); `ioctl(I2C_SET_ADDR)` + `write()/read()` + `ioctl(I2C_RDWR)`; mutex-protected for concurrent app access
- [x] BSP YAML: `i2c` bus section — `bspgen.py` emits `DUNEOS_HAVE_I2C` + pin defines; m5stack-cardputer and esp32s3-devkitc both enabled
- [x] `/dev/battery0` — kernel device framework; `adc_simple` backend in `vfs_dev.c`; compiled only when `DUNEOS_HAVE_BATTERY` defined in board_config.h
- [x] BSP YAML: `battery` section; `bspgen.py` emits `DUNEOS_HAVE_BATTERY` + backend + pin defines; kernel registers `/dev/battery0` only when present
- [x] `DUNEOS_PERM_I2C` (bit 3, already existed); `DUNEOS_PERM_BATTERY` (bit 7) added to `abi.h`; shell manifest updated to `permissions: 255`
- [x] Shell: `battery` built-in — opens `/dev/battery0`, prints voltage/charge/status; graceful message if device absent

---

## Phase 11 — SPI (`/dev/spi-1`)

**Kernel space.** SPI3_HOST (VSPI) only — SPI2_HOST is permanently taken by FatFS/SD card.

**Kernel vs userspace boundary for SPI:**

- **SPI bus controller** → `/dev/spi-1` (kernel, `esp_driver_spi`, SPI3_HOST). Full-duplex transfer.
- **Per-chip protocol** (CC1101, SX1276/LoRa, NRF24L01, W5500, …) → userspace `.dap` library.
  App opens `/dev/spi-1`, configures mode/speed/CS via ioctl, then `write()` for full-duplex transfer.
- **Display chips over SPI** (ST7789, GC9A01) → userspace OR kernel framebuffer (Phase 13 decides).

**SPI ioctl API:**

```c
#define SPI_SET_MODE      0x01  /* ← uint8_t  CPOL/CPHA (0–3)                 */
#define SPI_SET_SPEED     0x02  /* ← uint32_t Hz                               */
#define SPI_SET_CS        0x03  /* ← int      GPIO pin for CS (-1 = none)      */
#define SPI_TRANSFER      0x04  /* ← spi_xfer_t {tx, rx, len} — full-duplex   */
```

`write()` on `/dev/spi-1` = transmit only (rx discarded). `ioctl(SPI_TRANSFER)` for simultaneous tx+rx.
CS is asserted/deasserted around each `write()` or `ioctl(SPI_TRANSFER)` automatically.

**Roadmap items:**

- [ ] `/dev/spi-1` — SPI3_HOST; ioctl API above; CS pin stored per open fd
- [ ] BSP YAML: `spi` bus section
- [ ] `DUNEOS_PERM_SPI` permission bit
- [ ] Guard against opening `/dev/spi-1` on boards where SPI3 pins conflict with another peripheral

---

## Phase 12 — Input (`/dev/input/event0`)

**Kernel space (interrupt / scan side) + userspace driver daemon for complex matrices.**

**Kernel vs userspace boundary for input:**

- **Simple GPIO buttons** → kernel ISR registers event directly into `/dev/input/event0` ring buffer.
  Declared in BSP YAML as `buttons:` entries.
- **Keyboard matrix via I2C** (CardPuter — keyboard controller behind I2C) → **userspace daemon**
  `keyboard.dap`: opens `/dev/i2c-0`, polls/IRQ, pushes `input_event_t` to a kernel-provided
  pipe or to a shared `/tmp/input_fifo`. The kernel `/dev/input/event0` reads from that pipe.
  This avoids adding a per-board keyboard controller driver to the kernel.
- **Keyboard matrix via raw GPIO scan** → kernel task (declared in BSP YAML as `keyboard_matrix:`).

**evdev-style API (blocking `read()` returns one or more `input_event_t`):**

```c
typedef struct {
    uint32_t time_ms;
    uint8_t  type;    /* INPUT_EV_KEY | INPUT_EV_BTN */
    uint16_t code;    /* key code — ASCII for printable, special codes for arrows/enter */
    int32_t  value;   /* 1=press, 0=release, 2=repeat */
} input_event_t;
```

**Roadmap items:**

- [ ] `/dev/input/event0` — ring buffer + blocking `read()` (like Linux eventfd + read)
- [ ] BSP YAML: `buttons:` section for simple GPIO buttons
- [ ] Userspace keyboard daemon protocol: FIFO or IPC queue to feed `/dev/input/event0`
- [ ] Shell integration: shell reads from `/dev/input/event0` instead of (or in addition to) `/dev/uart0`
- [ ] `DUNEOS_PERM_INPUT` permission bit

---

## Phase 13 — Framebuffer (`/dev/fb0`) and display SDK

**Two-tier approach depending on board capabilities.**

### Tier A — Userspace display (all boards, including DRAM-only)

The default. The app links a display library from the SDK and owns the SPI bus for drawing.
No kernel framebuffer, no shared screen access between apps.

```text
app.dap
  └─ libst7789.c  (SDK userspace lib)
       └─ opens /dev/spi-1, drives ST7789 registers directly
```

Portability limitation: an app that links `libst7789.c` is tied to that chip.
Running it on a board with a different display requires recompilation with a different lib.
Phase 15 will introduce `libgfx` to abstract this.

SDK display libraries to provide:

- [ ] `libst7789.c` — SPI, 240×135 or 240×320 (CardPuter, many M5Stack boards)
- [ ] `libgc9a01.c` — SPI, 240×240 round (T-Display-S3 and others)
- [ ] `libssd1306.c` — I2C, 128×64 monochrome OLED (devkits, small boards)

### Tier B — Kernel framebuffer (`CONFIG_DUNEOS_HAVE_FB=y`, PSRAM boards only)

Only compiled in when BSP YAML declares a display AND `CONFIG_SPIRAM=y`.
240×135×2 = 63 KB — acceptable in PSRAM, dealbreaker in DRAM.
When active, the kernel owns the display SPI and Tier A libs must not be used simultaneously.

**ioctl API:**

```c
#define FB_GET_INFO   0x01  /* → fb_info_t {width, height, bpp, stride}  */
#define FB_SET_POS    0x02  /* ← fb_pos_t  {x, y}                        */
#define FB_FLUSH      0x03  /* flush back-buffer to display               */
```

**BSP YAML extension:**

```yaml
display:
  type: st7789          # st7789 | gc9a01 | ssd1306 | ili9341
  bus: spi-1
  width: 240
  height: 135
  cs_gpio: 37
  dc_gpio: 35
  rst_gpio: 33
  bl_gpio: 38
  rotation: 1
```

**Roadmap items (Tier B):**

- [ ] `/dev/fb0` — kernel framebuffer; enabled only when `CONFIG_DUNEOS_HAVE_FB=y`
- [ ] ST7789 kernel driver (first target: esp32s3-devkitc with PSRAM)
- [ ] `DUNEOS_PERM_FB` permission bit
- [ ] Shell: optional status bar drawn to `/dev/fb0` (board name, free heap, battery %)

---

## Phase 14 — WiFi daemon (userspace)

**Userspace service.** BSD sockets are already exported via lwIP. The missing piece is a daemon
that initialises `esp_wifi`, connects to a network, and signals `duneos_service_ready()` so
apps with `after: ["network"]` in `init.json` can start.

**No kernel changes required.** `wifi_daemon.dap` calls `esp_wifi_init()` + `esp_netif_*` which are
already (or will be) exported in `symbols.c`.

**Userspace daemon responsibilities:**

- Read `/sd/wifi.conf` (SSID + password, simple INI format)
- Connect; retry with back-off; call `duneos_service_ready()` on IP acquired
- Write `/tmp/net_status` (IP address, RSSI) for other apps to read
- Optionally expose `/dev/net/status` via a kernel-registered thin shim (one `read()` returns JSON)

**New symbol exports needed in `symbols.c`:**
`esp_wifi_init`, `esp_wifi_start`, `esp_wifi_connect`, `esp_netif_init`, `esp_netif_create_default_wifi_sta`,
`esp_event_loop_create_default`, `esp_event_handler_register`

**Roadmap items:**

- [ ] Export WiFi + netif + event loop symbols (with `DUNEOS_PERM_WIFI` permission gate)
- [ ] `wifi_daemon.dap` example app: reads `/sd/wifi.conf`, connects, signals ready
- [ ] `/sd/wifi.conf` format documented in SDK
- [ ] Shell: `ifconfig` built-in (reads `/tmp/net_status`)
- [ ] Shell: `ping <host>` built-in (uses exported `getaddrinfo` + raw socket)

---

## Phase 15 — Multi-target portability & `libgfx`

**Goal:** an app that draws to the screen should compile and run unchanged on CardPuter (ST7789,
no PSRAM), DevKitC (ST7789, PSRAM), or a board with an SSD1306 OLED — without `#ifdef` soup in
the app source.

**Approach — `libgfx` abstraction layer in the SDK:**

```c
/* app code — display-chip agnostic */
#include <duneos/gfx.h>

gfx_ctx_t *ctx = gfx_open();          /* opens /dev/fb0 if present, else /dev/spi-1 + chip init */
gfx_fill(ctx, 0x0000);
gfx_text(ctx, 10, 10, "Hello", 0xFFFF);
gfx_flush(ctx);
gfx_close(ctx);
```

`gfx_open()` selects the backend at runtime by probing available `/dev` entries and reading
`/sd/board.info` (a small file written by the kernel at boot from `board_config.h` — display type,
resolution, whether `/dev/fb0` is present).

**Backends compiled into `libgfx.a` (all boards, selected at link time via `dbt.py`):**

| Backend | When used |
| --- | --- |
| `gfx_fb.c` | `/dev/fb0` present (PSRAM board with kernel framebuffer) |
| `gfx_st7789.c` | `/dev/fb0` absent + board declares ST7789 |
| `gfx_gc9a01.c` | `/dev/fb0` absent + board declares GC9A01 |
| `gfx_ssd1306.c` | `/dev/fb0` absent + board declares SSD1306 |

`dbt.py build` reads the active `.duneos_board`, picks the right backend, links it into the app.
The app binary is still board-specific (different `.dap` per board), but the **source is portable**.

**`/sd/board.info` — kernel-written at boot:**

```json
{"board":"m5stack-cardputer","display":"st7789","width":240,"height":135,"fb":false}
```

Apps that need runtime discovery (e.g., a generic drawing demo) can read this file.

**Roadmap items:**

- [ ] `board.info` written to `/sd` at kernel boot from `board_config.h` constants
- [ ] `libgfx.h` public API: `gfx_open/close/fill/pixel/text/flush/get_info`
- [ ] `gfx_st7789.c`, `gfx_ssd1306.c` backends (Tier A — userspace SPI/I2C)
- [ ] `gfx_fb.c` backend (Tier B — wraps `/dev/fb0` when present)
- [ ] `dbt.py build` auto-selects `libgfx` backend from `.duneos_board`
- [ ] Demo app `gfx_demo.dap` — draws shapes + text, runs on all boards unchanged
- [ ] `dbt.py new` template includes `libgfx` option

---

## Phase 16 — Shell refactor: built-ins vs PATH

**Goal:** keep the shell binary minimal; every command that doesn't need shell-internal state becomes
a standalone `.dap` in `system/bin/`, searchable via a fixed PATH.

**Built-ins that must stay in the shell (modify shell-internal state):**
`cd`, `pwd`, `exit`, `echo`, `help`

**Commands to move to `system/bin/` as `.dap` files:**
`ls`, `cat`, `mkdir`, `rm`, `mv`, `free`, `klog`, `gpio`, `services`, `restart`, `reboot`

**PATH resolution in `exec_line`:**
If a token is not a built-in, try `/flash/bin/<cmd>.dap` then `/sd/bin/<cmd>.dap` and run
synchronously via the existing `duneos_loader_load` → `run` → `unload` path.
No `PATH` env var, no `execve` — fixed search order, zero infrastructure overhead.

**Tooling:**
`dbt.py deploy --bin` copies to `/sd/bin/` instead of `/sd/apps/`.
Phase 17 will add `dbt.py flashimg` to also embed them in `/flash/bin/`.

**Roadmap items:**

- [ ] Reduce shell built-ins to `cd`, `pwd`, `exit`, `echo`, `help` + PATH fallback in `exec_line`
- [ ] Move `ls`, `cat`, `mkdir`, `rm`, `mv` to `system/bin/` (one `.c` + `manifest.json` each)
- [ ] Move `free`, `klog`, `reboot` to `system/bin/`
- [ ] Move `gpio`, `services`, `restart` to `system/bin/`
- [ ] `dbt.py deploy --bin` flag (target `/sd/bin/` instead of `/sd/apps/`)
- [ ] Update `system/bin/` build: shared `CMakeLists.txt` or per-app `dbt.py` invocation

---

## Phase 17 — Flash app storage (`/flash`, SD-less boards)

**Goal:** DuneOS boots and is fully usable with no SD card. Essential system apps are embedded in
the firmware image and available at `/flash/bin/` on every board. Boards with SD keep SD as an
extension layer (`/sd/apps/`), not a boot requirement.

**Storage backend:** LittleFS partition (`sysbin`, ~1 MB in `partitions.csv`), mounted at `/flash`.
LittleFS chosen over SPIFFS for power-cut safety, better wear levelling, and no filename length limit.

**Embedding strategy — first-boot provisioning:**
System app blobs are embedded in the firmware via CMake `COMPONENT_EMBED_FILES` (same mechanism
ESP-IDF uses for TLS certificates). On first boot (or when `/flash/bin/shell.dap` is absent), the
kernel writes them from the embedded blob into the LittleFS partition.
This keeps the partition writable — apps can be updated by copying a new `.dap` to `/flash/bin/`
via SD or WiFi — while still guaranteeing a known-good factory state.

**Loader search order:**
`/flash/bin/<name>.dap` → `/sd/bin/<name>.dap` → `/sd/apps/<name>.dap`

**SD-less board support:**
BSP YAML gains `has_sd: false`. When set, `vfs.c` skips SD mount entirely and `/sd` is not
registered. `init.json` is read from `/flash/init.json` instead.

**`dbt.py flashimg`:**
Produces a ready-to-flash LittleFS image containing all `system/bin/*.dap` files.
Flashable via `esptool.py write_flash` or the ESP-IDF VS Code extension.

**`partitions.csv` change:**
Add `sysbin` data/littlefs partition (~1 MB). Shrink `storage` FAT partition accordingly on
flash-constrained boards (e.g. 8 MB: kernel 1.5 MB + sysbin 1 MB + FAT 5.3 MB).

**Roadmap items:**

- [ ] Add `sysbin` LittleFS partition to `partitions.csv`; mount at `/flash` in `vfs.c`
- [ ] CMake: embed `system/bin/*.dap` blobs via `COMPONENT_EMBED_FILES`
- [ ] First-boot provisioning: kernel copies missing blobs from flash to LittleFS at boot
- [ ] Loader: search `/flash/bin/` before `/sd/bin/` and `/sd/apps/`
- [ ] BSP YAML: `has_sd: false` flag; `vfs.c` skips SD mount; `init.json` path falls back to `/flash`
- [ ] `dbt.py flashimg` — build LittleFS image from `system/bin/`; document flash procedure
- [ ] Shell: `flash ls` / `flash install <file.dap>` — manage `/flash/bin/` contents from SD
- [ ] Update `system/bin/` deploy docs: SD path for dev, `flashimg` for production

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
| Shell `ls /` mount list is hardcoded | Should query a kernel-side mount registry via ABI |
