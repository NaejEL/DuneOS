# DuneOS Roadmap

Single roadmap. Merged 2026-09-06 from `ROADMAP_v2.md` (the phase plan) and `ROADMAP-legacy.md`
(the 2026-09 legacy audit). Both files are deleted; this one replaces them.

**Two vocabularies, deliberately kept apart.** A **phase** is a design decision about what DuneOS
becomes next. A **LEG finding** is an audit observation about what the repository already is and
should not be. They are not interchangeable and are never merged into one another: phases are the
backbone (§4 delivered, §6 next), the audit debt has its own section with its own milestones (§5),
sitting between them.

## 1. The Manifesto

NuttX and Zephyr offer incredible POSIX conformance, but at the price of a brutal learning curve (Kconfig hell, complex Device Trees). DuneOS aims for the Sweet Spot: the power of a POSIX RTOS with the smooth developer experience (DX) of a console like the Playdate or the Flipper Zero.

The user must never have to touch CMake or the kernel internals. A single YAML file and some C code must be enough to compile, link and dynamically deploy an application on any supported hardware.

---

## 2. POC legacy (Phases 1 to 24 validated, residual debt addressed by 26-27)

DuneOS is no longer just an idea — the foundations are proven on ESP32-S3 (M5Stack CardPuter, LilyGo T-Embed CC1101, ESP32-S3-DevKitC):

- **Execution and ABI**: working Xtensa ELF loader (ET_REL) (load/run/unload cycle, complete Xtensa relocations). ABI v3: typed `duneos_api_t` dispatch table injected into `__duneos_api_ptr` by the loader — O(1) resolution.
- **VFS and devices**: `/` (LittleFS on the `sysbin` partition — the flash filesystem **is** the
  root, `vfs.c:31` `FLASH_MOUNT_POINT ""`; there is no `/flash` prefix), `/sd` (optional FatFS),
  `/tmp` (tmpfs), `/dev/uart0`, `/dev/klog`, `/dev/gpiochip0`, `/dev/i2c-0`, `/dev/spi-1`, `/dev/disp0`, `/dev/fb0` (PSRAM boards), `/dev/input/event0`, `/dev/raw80211`, `/dev/ttyUSB0`, `/dev/battery0`.
- **Display**: `/dev/disp0` streaming driver (all boards) + `/dev/fb0` PSRAM back-buffer (T-Embed). `libgfx` (`sdk/display/gfx.c`) selects the backend dynamically at runtime (`/dev/fb0` if available, `/dev/disp0` fallback).
- **USB**: composite TinyUSB MSC + CDC on OTG boards; `/dev/ttyUSB0` exposed through `drv_usb_cdc.c`; klog redirected to CDC at runtime.
- **Userspace**: `libdune.a` (6 sources: ptr, fs, mem, thread, time, sys) — PicoLibc + ABI v3 dispatch. Lets apps write standard POSIX C.
- **Ecosystem**: modular shell (`apps/system/usb_shell/` + `apps/system/shell_core/`, builtins + 28 commands in `apps/system/bin/`), tooling (`dbt` package + Textual TUI + toolchain plugins), init system (`boards/<board>/init.yaml` flashed into sysbin + optional `/sd/init.yaml`), restart policies.
- **Networking**: WiFi daemon, `duneos_netif_wait_ip()`, raw 802.11 frame injection (`/dev/raw80211`), complete BSD socket exports, `apps/system/bin/ifconfig` + `ping`.
- **Hardening (Phase 20 partial)**: per-app heap (heap_caps_malloc), per-slot Task WDT, per-app Xtensa exception handler (clean kill without a kernel reboot), syscall pointer validation (`check_user_ptr` + `check_app_writable_ptr`), FreeRTOS stack canary
  (`sdkconfig.defaults:30` `CONFIG_FREERTOS_CHECK_STACKOVERFLOW_CANARY=y` +
  `supervisor.c:497` `vApplicationStackOverflowHook`). Remaining: userspace TLSF, and that alone.
- **DHI (Phase 24)**: 6 pure HAL headers (`hal_uart.h`, `hal_gpio.h`, `hal_i2c.h`, `hal_spi.h`, `hal_adc.h`, `hal_time.h`), Xtensa implementations in `arch/xtensa_esp32s3/hal/`, HW drivers delegating to the HAL, `arch.cmake` self-selection. Strict HAL scope; purging `esp_err_t` from the 4 core headers (`init.h`/`task.h`/`vfs.h`/`supervisor.h`) is spread over Phases 26-27 (as each underlying .c changes its API).

**V2 goal**: turn this POC, tightly coupled to ESP-IDF/FreeRTOS, into a genuine independent OS, secure without an MMU, with world-class tooling.

---

## 3. Target architecture

```text
DuneOS/
├── apps/
│   ├── system/           (Apps shipping with DuneOS: usb_shell, shell_core, wifi_daemon, bin/*)
│   └── user/             (Third-party apps / developer templates)
├── tools/                (modular dbt/, bspgen.py)
└── kernel/               (Pure kernel source)
    ├── arch/             (CPU-specific: context switch, MPU/PMP, exceptions)
    ├── boards/           (YAML configuration and generated headers)
    ├── osal/             (OS abstraction: threads, mutexes, FreeRTOS stubs)
    ├── vfs/              (native duneos_vfs, devfs)
    ├── drivers/          (DHI implementation: UART, I2C, SPI, TinyUSB)
    ├── loader/           (ELF loader, core dump generator)
    └── libdune/          (PicoLibc + static syscall stubs)
```

---

## 4. Phases — delivered and in progress

Phases 17 to 25.6. Everything below this heading is either shipped or actively being built.
The audit debt that sits across it has its own section (§5); the phases still to come are §6.

### Phase 17 — Tooling DX (DX First) ✅

Stop writing throwaway tools. Prepare the ground for multiple architectures without touching CMake.

- [x] **Splitting up `dbt.py`**: turned into submodules (`tools/dbt/cli.py`, `builder.py`, `deploy.py`, `toolchain.py`).
- [x] **Application `duneos.yaml`**: replaces `manifest.json`. Fields: `stack`, `heap`, `permissions`, `sources`. `manifest.json` backward compatibility kept for one phase.
- [x] **`init.yaml`**: `/sd/init.json` migrated to `/sd/init.yaml`. YAML parser instead of cJSON in `init.c`.
- [x] **Universal `bspgen.py`**: ESP-IDF dependencies removed; generates pure C `board_config.h` with no `esp_err_t`.
- [x] **dbt TUI**: full-screen btop/ranger-style interface (Textual) — ACTIONS + OUTPUT panels. Flash Kernel, Flash Sysbin, Build All, Build App, Flash SD, BSP Gen, Init Config (`boards/<board>/init.yaml`), board picker, port picker, SD path persistence (`.duneos_sd`).

> **Native simulator** (`dbt.py run --sim`) — deferred past Phase 19, needs a phase of its own.

---

### Phase 18 — libgfx (display portability) ✅

An app that draws must compile and run identically on the CardPuter (ST7789, no PSRAM) and the T-Embed (ST7789, PSRAM, `/dev/fb0`) with no `#ifdef` in the source.

- [x] **`/board.info`** (+ `/sd/board.info`): YAML file written by the kernel at boot from `board_config.h` (fields: `board`, `display`, `width`, `height`, `fb`).
- [x] **`<duneos/gfx.h>`**: public API — `gfx_open/close/fill/pixel/text/flush/get_info`. RGB565 pixel format, host byte order.
- [x] **`sdk/display/gfx.c`**: a single implementation, **runtime** backend selection in `gfx_open()` — Tier B (`/dev/fb0`, kernel PSRAM framebuffer) if available, otherwise Tier A (`/dev/disp0`, SPI streaming). Drawing goes to a userspace back-buffer; `gfx_flush()` pushes it in one shot.
- [x] **Demo app** `gfx_demo.dap` — draws shapes + text, runs on every board with a display without recompiling the source.

> **Note vs the original design**: the roadmap initially mentioned two separate backend files (`gfx_st7789.c` + `gfx_fb.c`) selected at build time through `.duneos_board`. The implementation chosen is simpler: a single library that sniffs `/dev/fb0` at runtime. Same result (source-level portability), less dbt code.

---

### Phase 19 — Flash storage (boot without SD) ✅

DuneOS must boot and be usable even with no SD card inserted.

- [x] **LittleFS `sysbin` partition** in `partitions.csv` (~1 MB); mounted as the **root** in `vfs.c` (`FLASH_MOUNT_POINT ""`, `vfs.c:31`) — not under a `/flash` prefix.
- [x] **Embedding the vital apps** (`shell.dap`, `apps/system/bin/` commands) as firmware blobs through `COMPONENT_EMBED_FILES`.
- [x] **First-boot provisioning**: the kernel copies the missing blobs to `/bin/` on first start.
- [x] **Loader cascade**: search `/bin` → `/sd/bin` → `/sd/apps` (`loader.c:1541-1546`, `s_scan_dirs`).
- [x] **BSP YAML**: `has_sd: false` field; `vfs.c` skips the SD mount and reads `/init.yaml` from the flash root.
- [x] **`dbt.py flashimg`**: produces a LittleFS image directly flashable through `esptool` (port from `.duneos_port` / `--port` / `DUNEOS_PORT`).
- [x] **`bspgen.py`**: generates a per-board `partitions.csv` from `flash_size_mb`; `sdkconfig.board` replaces the handwritten `sdkconfig.defaults`.
- [x] **Init dedup by app name**: `init.c` deduplicates services by name (basename without `.dap`) — avoids double-launching when the same service appears in both `/init.yaml` and `/sd/init.yaml`. The flash entry wins (loaded first).
- [x] **`boards/<board>/init.yaml`**: a per-board file versioned in the repo, controlled by the user. `dbt flashimg` copies it as-is (no more hardcoded `_BOOT_SERVICES` list). Editable through the TUI (`i` → Init Config): app selection + restart policy (`always`/`on-failure`/`no`).

---

### Phase 20 — Memory hardening + technical debt 🟡 ONE ITEM OPEN (userspace TLSF)

Guarantee that an application cannot crash the system. Purge the accumulated technical debt.

- [x] **Per-app exception handler**: intercept the crash → log to `/dev/klog` → clean unload without a kernel reboot. (`supervisor.c` → `app_exception_handler`).
- [x] **Task WDT**: the supervisor feeds the WDT for the app; kicks the app on timeout.
- [x] **Per-app heap**: dedicated DRAM pool per app through `heap_caps_malloc` (contiguous Code+Data+Heap block). Monolithic TLSF block deferred.
- [x] **Syscall validation**: `duneos_supervisor_check_user_ptr` (permissive, source buffers) + `duneos_supervisor_check_app_writable_ptr` (strict, target buffers). Used by `api.c` (read/write) and `symbols.c`. Rejects the peripheral space and IRAM.
- [x] **Stack canary** per application task — `sdkconfig.defaults:30`
  `CONFIG_FREERTOS_CHECK_STACKOVERFLOW_CANARY=y` (bottom of every task stack filled with
  `0xa5a5a5a5`, checked at each context switch) and `supervisor.c:497`
  `vApplicationStackOverflowHook()`, which gives the supervisor its chance to kill the app instead
  of rebooting the kernel. LEG-37 later added the hardware watchpoint
  (`CONFIG_FREERTOS_WATCHPOINT_END_OF_STACK`) alongside it, because the canary is only *checked* at
  a context switch and an overflow that reaches the adjacent heap between two switches is already
  gone.
- [ ] **Userspace TLSF allocator**: `libdune.a` manages its own `malloc()` within that pool only — currently the app-side `malloc()` calls the kernel's `heap_caps_malloc` directly through `__duneos_api_ptr->mem`.

---

### Phase 21 — dbt: multi-arch toolchain plugin ✅

`board.yaml` gains the `arch:` and `sdk:` fields. `tools/dbt/toolchain/` becomes a plugin directory.

- [x] **`board.yaml`**: `arch:` and `sdk:` fields added to the 4 existing boards.
- [x] **`tools/dbt/toolchain/__init__.py`**: `load_plugin(sdk)` + `get_board_plugin()` — dispatches to the right plugin module.
- [x] **`tools/dbt/toolchain/esp_idf.py`**: first plugin — `SDK`, `ARCH`, `find_compiler()`, `cflags()`, `ldflags()`, `linker_script()`, `build_kernel()`, `flash_kernel()`, `monitor()`, `find_toolchain_root()`.
- [x] **`builder.py`**, **`cli.py`**, **`flashimg.py`**, **`kernel.py`**: dispatch through `get_board_plugin()` — no more direct import of `toolchain.py`.
- [x] Old `toolchain.py` removed — replaced by the `toolchain/` package.

---

### Phase 22 — Syscalls and PicoLibc migration ✅

Speed and lightness: make the ABI reliable.

- [x] **`duneos_api_t` — typed API table (ABI v3)**: `kernel/duneos_kernel/include/duneos/api.h` defines a struct of function pointers covering `fs`, `mem`, `thread`, `time`, `sys`. The loader injects the address of the kernel singleton into the app's `__duneos_api_ptr` symbol before calling `app_main`. O(1) resolution, no need for a string lookup.
- [x] **`DUNEOS_ABI_VERSION` → 3**: `abi.h` bumped; forward compatibility guaranteed (v1/v2 apps without `__duneos_api_ptr` still use the `duneos_symbol_table_get()` symbol table).
- [x] **`kernel/duneos_kernel/src/api.c`**: static `s_api` instance + `duneos_api_get()`. Thin wrappers for `read` (check_app_writable_ptr), `write` (check_user_ptr), `dup`/`dup2`, `dprintf`, `opendir`/`readdir`/`closedir`, loader and IPC through `void *` (avoids the kernel↔loader circular dependency).
- [x] **Statically allocated stack (`xTaskCreateStaticPinnedToCore`)**: exact bounds `[stack_mem, stack_mem+stack_size)` known → `check_app_writable_ptr` can validate kernel→app write pointers.
- [x] **Two-level pointer validation**: `check_user_ptr` (permissive, for source buffers) + `check_app_writable_ptr` (strict, for target buffers). Rejects the peripheral space and IRAM.
- [x] **cJSON manifest parser**: replaces the old `strstr`/`sscanf` with `cJSON` in `loader.c`. Non-fatal on invalid JSON (boots with defaults).
- [x] **`duneos_exit` relocated into `supervisor.c`**: semantically correct home (a lifecycle operation). Removed from `symbols.c`.
- [x] **PicoLibc migration**: `-isystem picolibc/include` already injected by `esp_idf.py`. `-D_POSIX_C_SOURCE=200809L` exposes `clock_gettime` etc. `-D__PICOLIBC_ERRNO_FUNCTION=__errno` routes `errno` through the kernel's FreeRTOS TLS.
- [x] **`libdune.a` — userspace POSIX library**: `libdune/src/`: `libdune_ptr.c` (`__duneos_api_ptr` anchor), `libdune_fs.c` (file + directory I/O), `libdune_mem.c` (malloc/free/realloc/calloc), `libdune_thread.c` (pthreads + semaphores), `libdune_time.c` (clock, gettimeofday, usleep, sleep, nanosleep), `libdune_sys.c` (DuneOS lifecycle, IPC, supervisor, loader, PicoLibc `__errno`).
- [x] **`<duneos/libdune.h>`**: complete app-facing header — declares `duneos_restart_policy_t`, `duneos_slot_info_t`, every non-POSIX DuneOS function, `duneos_sys_restart()`, `duneos_sys_free_heap()`.
- [x] **`dbt build` integrates libdune**: `builder.py` calls `build_libdune()` before the link — per-arch cache in `libdune/build/{arch}/libdune.a`, invalidated by the mtime of the sources and headers. `find_compiler()` in `esp_idf.py` now exposes `ar` in the `tc` dict.

---

### Phase 23 — The Flipper DX (USB device subsystem) ✅

The ultimate plug-and-play experience.

- [x] **TinyUSB**: composite MSC+CDC on `espressif/esp_tinyusb ^2.0.1~1`. `drv_usb.c` initialises the device stack.
- [x] **USB MSC (mass storage)**: `/sd` exposed for drag-and-drop. `duneos_vfs_get_sd_card()` passes the sdmmc handle to the MSC backend.
- [x] **USB CDC (console)**: `drv_usb_cdc.c` — mutex-serialised TX (a single non-blocking `write_flush`, no collision), RX through a ring buffer + semaphore → `/dev/ttyUSB0`. `apps/system/usb_shell/` + `apps/system/shell_core/` replace `system/shell/`.
- [x] **`console: none`**: `bspgen.py` emits `CONFIG_ESP_CONSOLE_NONE=y` for OTG boards — UART0 stays free for apps. klog is redirected to CDC at runtime through `esp_log_set_vprintf`.

---

### Phase 24 — DHI (DuneOS Hardware Interface) ✅

Isolate the kernel to begin the exit from the Espressif framework. **The scope of this phase is the hardware HAL only**: 6 pure HAL headers (UART/GPIO/I2C/SPI/ADC/time), Xtensa implementation, HW driver migration, multi-arch infrastructure. The complete purge of the other ESP-IDF types (`esp_err_t` in the core headers, FreeRTOS handles, `esp_*` networking) happens *incrementally* in the phases that actually touch those files (26, 27).
- [x] **Hardware DHI headers**: `hal_uart.h`, `hal_gpio.h`, `hal_i2c.h`, `hal_spi.h`, `hal_adc.h`, `hal_time.h` — pure types (`uint32_t`, `int`, standard C callbacks). Zero ESP-IDF dependency.
- [x] **ESP-IDF implementations**: `arch/xtensa_esp32s3/hal/hal_*.c` — ESP-IDF types **internally only**. Conditional compilation through the `arch.cmake` triple guard.
- [x] **HW backend migration**: `drv_uart.c`, `drv_gpio.c`, `drv_i2c.c`, `drv_spi.c`, `i2c_bus.c`, `drv_battery_adc_simple.c` delegate to the HAL. Input drivers (`btn_gpio.c`, `enc_quadrature.c`, `kb_iomatrix.c`) migrated. `dev_driver.h` and `i2c_bus.h` return `int`.
- [x] **Interrupt abstraction**: `duneos_hal_gpio_set_intr()` (kernel-internal use). **Userspace delivery implemented 2026-06-06** (contest sprint, i2cscope app): `GPIOCHIP_SET_IRQ` arms a line for edge detection, `read()` on `/dev/gpiochip0` returns timestamped `gpio_event_t` — Linux's GPIO chardev line-events model ([ADR 019](docs/adr/019-linux-device-interface-semantics.md)). No signal/`poll` required any more for the edge-stream case.
- [x] **Arch in the manifest**: `arch[32]` field in `duneos_app_manifest_t`. The loader rejects cross-ISA `.dap` files. `dbt builder.py` injects `arch` from the toolchain plugin.
- [x] **Numeric IDs in the BSP**: `bspgen.py` emits the SPI host and ADC unit as integers.
- [x] **`arch.cmake` self-selection**: `file(GLOB arch/*/arch.cmake)` + triple guard. Adding an arch = creating a single file, zero changes to the kernel core.

**`esp_err_t` debt in the core headers — spread by target phase (not by a separate "Phase 24b")**:

| Public header | Planned migration | Rationale |
|---|---|---|
| `init.h` (1 fn) | **Pre-25, micro-task** | Calls no ESP-IDF API internally, just POSIX. Trivial isolated migration. |
| `task.h`, `supervisor.h` (4 fns + 2 typedefs) | **Phase 26 (OSAL)** | `xTaskCreate`/`xTaskNotify`/etc. disappear in favour of `osal_*` — the return type changes naturally. |
| `vfs.h` (6 fns) | **Phase 27 (native VFS)** | `esp_vfs_register`/`esp_vfs_fat_*`/`esp_vfs_littlefs_*` disappear in favour of native `duneos_vfs_*`. |

**Decided return convention (ADR — see Phase 24.5)**: `int` returning **0** on success, **-errno** on failure (`-ENOENT`, `-EIO`, `-ENOMEM`, `-EINVAL`, …). No dedicated `duneos_status_t` enum. A private `esp_to_errno()` helper during the transition, removed when its calling `.c` migrates.

**Documentation micro-fix to include in the init.h micro-task**:

- Stale comment at `input_ioctl.h:47` (mentions `xTaskGetTickCount() * portTICK_PERIOD_MS` whereas the field is now filled through `duneos_hal_monotonic_us() / 1000`).
- Comment at `task.h:8-10` "FreeRTOS abstraction" — to be reworded as "OSAL abstraction" at Phase 26.

**Driver placement debt** — stemming from [ADR 009](docs/adr/009-driver-boundary.md) (kernel/userspace boundary) and [ADR 010](docs/adr/010-arch-accelerators.md). Six items. This list reflects the **honest state** (revision 2026-05-20): an item is only marked `[x]` when it is *completely* closed (clean kernel + apps migrated + tests passing).

- [x] **#4 — `hal_encoder` capability HAL (ADR 010 Pattern A)**: `hal_encoder.h` + Xtensa PCNT backend (strong symbol) + GPIO polling fallback (weak symbol). `enc_quadrature.c` reduced to a thin shim. RP2040 backend (PIO) to be added in Phase 29.

- [x] **#6 — Expose SPI3 as /dev/spi-N + #2 ST7789 userspace** (combined) — **delivered 2026-05-20**:
  - `drv_spi.c` is multi-host (`s_slots[MAX_RAW_BUSES]`, one driver per bus with `container_of` to recover the bus from the callback)
  - `bspgen.py` emits `DUNEOS_SPI{1..N}_HOST/MOSI/CLK/MISO/MAX_FREQ_HZ/BUS_SHARED` for each `role: raw` plus `DUNEOS_NUM_RAW_SPI_BUSES`
  - `boards/m5stack-cardputer/board.yaml` declares SPI3 (id=3) as `role: raw`; display pins split out as device-specific (`cs_pin/dc_pin/rst_pin/bl_pin` in the `display` section) with `spi_id: 3`
  - `drv_disp_st7789.c` removed from the repo, `CONFIG_DUNEOS_DRV_DISP` removed from Kconfig, `vfs_dev.c` purged of the register call, `CMakeLists.txt` simplified
  - `libst7789.c` is pure userspace: opens `/dev/spi-<DUNEOS_DISPLAY_DEV_INDEX>` + `/dev/gpiochip0`, complete ST7789 init sequence, exports `duneos_disp_ops`
  - `libdisp.c` is a thin dispatcher to `duneos_disp_ops` (a real vtable)
  - `capabilities.py`: the `display` capability now pulls in `lib${board.display.driver}.c`
  - `boardgen.py` `_board.h` emits `DUNEOS_DISPLAY_DEV_INDEX` + device pins + MADCTL/rotation/offsets on non-PSRAM boards; emits `DUNEOS_DISPLAY_DEV "/dev/fb0"` on PSRAM
  - Build status: kernel ✓, buildall 25/25 ✓
  - **Device tests still to validate**: g_shell + gfx_demo on the CardPuter through /dev/spi-1; T-Embed unchanged through /dev/fb0

- [ ] **#1 — BQ27220 daemon** (code-complete on the CardPuter, T-Embed device validation pending):
  - **Status 2026-05-20**: implementation done (commit `022bec6`). Chosen IPC mechanism = a `/tmp/battery` file (binary `battery_info_t`) rewritten every 2 s by the daemon; the client does `read()` then `close()`. No named pipe, no kernel driver — maximum simplicity thanks to the existing `/tmp` tmpfs.
  - **Done**:
    - `apps/system/battery_daemon/battery_daemon.c` — `#ifdef DUNEOS_BATTERY_GAUGE_ADDR` guard (stub `exit(0)` on boards without a BQ27220) → the daemon builds everywhere without capability filtering
    - `apps/system/bin/battery/battery.c` — `/tmp/battery` → `/dev/battery0` fallback (covers the T-Embed BQ27220 *and* the CardPuter ADC)
    - `tools/dbt/boardgen.py` — a new `_battery_block()` emits `DUNEOS_BATTERY_I2C_DEV`/`_GAUGE_ADDR`/`_TMPFS_PATH` into `_board.h`
    - Kernel purged: `drv_battery_bq27220.c` removed, `CONFIG_DUNEOS_DRV_BATTERY_BQ27220` dropped (Kconfig + CMakeLists + vfs_dev.c), `DUNEOS_BATTERY_GAUGE_ADDR` dropped from `board_config.h`
    - `boards/lilygo-t-embed-cc1101/init.yaml` adds `/bin/battery_daemon.dap restart: always`
    - `sdk/sensor/libbq27220.c` — drop `<sys/ioctl.h>` (header does not exist in `xtensa-esp-elf`)
    - `dbt buildall` → 27/27 OK; CardPuter kernel build OK
  - **Left to close**: flash the T-Embed → check that `battery_daemon` shows up in klog at boot, that `/tmp/battery` is filled, and that the `battery` shell command returns `voltage/charge/status`. **Until that device test is done, the item stays `[ ]`.**

- [ ] **#3 — GPIO expander C drivers** (code-complete, hardware validation pending — no expander board owned today)
  - **Status 2026-05-20**: 3 drivers delivered, T-Embed kernel builds OK with all 3 enabled, 27/27 apps OK on the CardPuter.
  - **Done**:
    - `kernel/duneos_kernel/src/drivers/gpio/drv_gpiochip_sx1509.c` — 16-bit SX1509, RMW shadows for DIR/DATA/PULLUP, per-driver mutex, container_of pattern (multi-instance through `MAX_SX1509_INSTANCES=4`)
    - `kernel/duneos_kernel/src/drivers/gpio/drv_gpiochip_pcf8574.c` — 8-bit quasi-bidirectional PCF8574 (input = write 1, output = direct SET_VALUE, SET_PULL rejects NONE/DOWN because the internal pull-ups are always on)
    - `kernel/duneos_kernel/src/drivers/gpio/drv_gpiochip_mcp23017.c` — explicit STUB (klog warning at boot, inline doc pointing to the SX1509 as a template)
    - CMakeLists.txt: 3 `if(CONFIG_DUNEOS_DRV_GPIOCHIP_*)` blocks
    - vfs_dev.c: extern decls + register calls (order: *after* `drv_i2c_register()` so that `i2c_bus_init()` has run)
    - Test: T-Embed board.yaml with `gpio_expanders: [sx1509, pcf8574, mcp23017]` → kernel links OK, 3 `.obj` produced
  - **Note**: a Phase 24.9 (driver self-registration) will later remove the repetitive `#ifdef` blocks; each driver will use `DUNEOS_DRIVER_REGISTER()` instead. Not a blocker for this closure.

- [?] **#5 — uinput + userspace input drivers** — **CONTESTED, state unknown**
  - **Status 2026-05-20**: implementation done; CardPuter + T-Embed kernels build OK with the daemons enabled; 29/29 apps OK on both boards. **Keyboard tested on the CardPuter device — kb_iomatrix.dap injects the events, the shell and g_shell still receive keystrokes.**
  - **Done**:
    - Kernel: `kb_iomatrix.c` + `btn_gpio.c` removed. `drv_input.c` gains the `INPUT_INJECT_EVENT` ioctl (single handler for userspace injection; internal `drv_input_push_event()` remains for the encoder ISR path). Kconfig purged of `DUNEOS_DRV_INPUT_IOMATRIX/BTNGPIO` (the encoder stays).
    - Userspace: `apps/system/kb_iomatrix/` + `apps/system/btn_gpio/` (10 ms polling daemons, opening `/dev/gpiochip0` + `/dev/input/event0`, `#ifdef` guard to build as a stub on boards without the feature)
    - `boardgen.py`: a new `_input_block` emits `DUNEOS_KB_*` + `DUNEOS_BTN_GPIO_*` + `DUNEOS_INPUT_DEV` into `<duneos/board.h>` (per-app); `duneos-bspgen.py` no longer emits them into `board_config.h` (kernel) since the kernel no longer consumes them
    - CardPuter `init.yaml`: adds `/bin/kb_iomatrix.dap restart: always`. T-Embed: adds `/bin/btn_gpio.dap`.
  - **Left to close**: validate on device (CardPuter) that `g_shell` still receives its events after boot. Strict process — `[ ]` pending hardware observation.
  - **Why this item is marked `[?]` and not resolved either way.** The header claims "validated on device 2026-05-20" and the status line says the keyboard was tested on the CardPuter; the "Left to close" line above says the same validation is still pending. Both were written in the same revision. **Nothing in the tree can settle it** — a hardware observation leaves no artefact here, and no test covers it (the QEMU bench models no keyboard, ADR 039). Picking a side would be inventing a fact, and the rule directly below this list forbids exactly that. It stays contested until somebody puts a CardPuter on the desk and says which line is true.
  - **Architecture note**: scan latency increases (each ioctl is a function call, but 8 row selects × 7 col reads = ~56 ioctls per scan). On the CardPuter without an MMU and with the API dispatch table, this is µs-level — it should stay << 1 ms per scan. To be measured if fast typing feels less fluid.

> `[?]` is not a third state in this process — it is the honest record of a claim the tree cannot arbitrate, and it resolves to `[ ]` or `[x]` on hardware, never by argument.

> No item is marked `[x]` until **the kernel no longer holds the obsolete code** + **the migrated apps pass the tests**. No "MVP/PARTIAL" mid-implementation — every item is either `[ ]` or `[x]`, never in between. This discipline enacts the **100 % closure process** established 2026-05-20.

---

### Phase 24.5 — Design decisions / ADR ✅

**13** short ADRs closed this phase — the ones listed below, ADR 000 to ADR 012. (The heading used to say "11"; the list under it has always had 13 entries.) Michael Nygard format, 1 page maximum each, status `Accepted · 2026-05-19`. All the choices consumed by phase 24 (debt) and 25-29.

**`docs/adr/` holds 41 ADRs today** (`docs/adr/*.md` minus `README.md`), not 13: ADR 013 to ADR 040 were written after this phase, by the phases and sprints that needed them.

> **Recorded gap, deliberately not closed here.** ADRs 025-038 describe work that is delivered and that **no phase in this roadmap accounts for** (app concurrency under RAM pressure, file-type associations, ambient system state, offscreen canvas, stack auto-sizing, frugal WiFi, navigation handoff, captured-output streaming, coreutils conventions, shell pipelines and scripting, MicroPython, the regex engine, userspace logging). Redistributing them into the phase structure is a design decision the owner has not taken, so it is not taken here. The gap is the record.

- [x] **ADR 000** — ADR process
- [x] **ADR 001** — `int`/-errno error model (consumed by 26-27)
- [x] **ADR 002** — OSAL API, static-storage handles only (consumed by 26)
- [x] **ADR 003** — memory caps, silent fallback to DRAM (consumed by 26)
- [x] **ADR 004** — task priorities, 5 symbolic levels (consumed by 26)
- [x] **ADR 005** — path conventions, a mandatory flash filesystem (consumed by 25, kernel boot). The ADR text still spells it `/flash`; the kernel mounts it at the root (`vfs.c:31`). Drift to fix in LEG-20's documentation sweep.
- [x] **ADR 006** — manifest extensibility, unknown fields ignored (consumed by 25, loader)
- [x] **ADR 007** — multi-arch smoke test (consumed by 26-28, prerequisite for 29)
- [x] **ADR 008** — memory fragmentation strategy (consumed by 20 TLSF, kernel review policy)
- [x] **ADR 009** — kernel/userspace boundary for drivers (consumed by the Phase 24 debt, every new driver PR)
- [x] **ADR 010** — architecture-specific accelerators (consumed by the Phase 24 debt hal_encoder, Phase 29 PIO)
- [x] **ADR 011** — threat model: permissions are advisory (consumed by Phase 32 signing, README warning, future security claims)
- [x] **ADR 012** — test strategy: host-side first (consumed by Phase 26+, prerequisite of the OSAL refactor)

---

### Phase 24.7 — Safe boot & recovery 🟡 CODE COMPLETE, hardware validation open

**Why here (before Phase 25):** an `init.yaml` that launches a `restart: always` service which crashes at startup puts the board into a restart loop. On the CardPuter, with no dedicated boot button and no accessible UART, the only way out is `dbt flash sysbin` — which assumes USB MSC mounts BEFORE the crash. That is not guaranteed. Phase 25 (`dbt system deploy`) will automate deployments that can introduce exactly this bug → we must be able to recover before then.

**Minimal scope**:

- [x] **Circuit breaker in the supervisor**: by default 3 crashes in 30 s → `breaker_tripped`, restart skipped, klog "circuit breaker tripped, restart disabled". `breaker_max_crashes` and `breaker_window_ms` fields in the slot, defaults used when 0. A per-service configurable `init.yaml` field is deferred (the defaults are enough for development).
- [~] **Hold-key-at-boot fallback**: **implemented, never run on hardware.** `init.c:17-47` `recovery_pin_held()` sets the pin to input with the matching pull, settles it, reads the level; `init.c:181-184` swaps the normal `/init.yaml` + `/sd/init.yaml` chain for `/init.yaml.safe` when it is held. `boards/lilygo-t-embed-cc1101/board.yaml:118` declares `recovery_pin:`, `duneos-bspgen.py:463` translates it (both the shorthand `recovery_pin: 6` and the `{pin, active_low}` form), and `flashimg.py:265` stages the safe image. **`[~]`, not `[x]`**: the whole point of this item is that a button rescues a bricked board, and nothing but a board can prove that. Requires hardware testing.
- [x] **Guaranteed USB MSC ordering before init**: verified in the `main.c` flow — `duneos_vfs_init()` calls `drv_usb_preinit()` as its first action (TinyUSB + MSC + CDC drivers installed), then the VFS mounts, then `drv_usb_register()` (MSC phase 2), and only then `launch_from_init_yaml()`. USB CDC stays reachable even if every init.yaml service trips its breaker. Documented in the README.
- [x] **`dbt flash sysbin --safe`**: option implemented. Generates a minimal init.yaml (`usb_shell.dap` only, restart: always) and flashes the sysbin. Touches neither the factory partition (kernel) nor anything else.
- [x] **README.md doc**: a "Recovering from a bad init.yaml" section added, describing the circuit breaker + `--safe` flash + the USB guarantee. Hold-key will be documented when the item above is delivered.

> This is NOT an OTA system, nor A/B partitions, nor a custom bootloader. Just the safety nets needed not to brick a board during development. Full OTA is a separate future phase.

---

### Phase 24.8 — `<duneos/board.h>` auto-generated by dbt (ADR 015 Pattern 2) ✅

Eliminates hardcoded device paths (`/dev/disp0`, `/dev/spi-1`, etc.) in the SDK libs and the apps. `dbt build` generates a per-app `_board.h` header from `board.yaml` + capability resolution, containing every needed define. Apps include `<duneos/board.h>` (an alias resolved through `-I<build_dir>`).

- [x] **`tools/dbt/boardgen.py` generator**: reads the active `board.yaml`, emits `build/<app>/_board.h`. The MVP ships 4 sections: storage (DUNEOS_HAS_SD, DUNEOS_FLASH_MOUNT, DUNEOS_SD_MOUNT), display (DUNEOS_DISPLAY_DEV "/dev/disp0" or "/dev/fb0" depending on PSRAM, DRIVER, WIDTH, HEIGHT), input (DUNEOS_INPUT_DEV), i2c (DUNEOS_I2C0_DEV, …). Capability filtering deferred until the list grows.
- [x] **`builder.py`**: calls `boardgen.write_to(build_dir, ...)` before compiling, adds `-I<build_dir>` to the include paths.
- [x] **`kernel/duneos_kernel/include/duneos/board.h` alias header**: `#include "_board.h"` — resolved through the app include paths.
- [x] **`libdisp.c` migration**: uses `DUNEOS_DISPLAY_DEV` instead of a hardcoded `"/dev/disp0"`. The future switch to userspace SPI (Phase 24.6) is transparent for libdisp.
- [x] **`libst7789.c` migration**: delivered within the Phase 24 debt #6+#2 (commit `109d53c`). Reads `DUNEOS_DISPLAY_DEV_INDEX` + device pins from `<duneos/board.h>` — opens `/dev/spi-N` according to the board, no more `/board.info` parsing.
- [x] **Rebuild dependency on `board.yaml`**: implicit — `boardgen.write_text()` rewrites `_board.h` on every `dbt build`, so the mtime is fresh → gcc recompiles the `.c` files including it. No dedicated stamp file needed.
- [x] **Capability filtering** (Phase 24.8 commit): `boardgen.py`'s `CAPABILITY_TO_BLOCKS` maps `display→_display_block`, `input→_input_block`, `battery→_battery_block`. Apps declaring `capabilities: [...]` receive only the matching blocks. Apps with no `capabilities:` keep every block (backward compatibility). `capabilities.py` accepts marker-only capabilities (no board_key → no driver dispatch, no sources). The input daemons (kb_iomatrix, btn_gpio) declare `capabilities: [input]`. Validation: g_shell (`capabilities: [display]`) no longer receives the `DUNEOS_KB_*` defines but keeps `DUNEOS_INPUT_DEV` (always on for consumers).
- [x] **Practical validation**: `dbt buildall` → 29/29 OK with filtering. g_shell, gfx_demo, battery_daemon, kb_iomatrix, btn_gpio tested.

> **Apps that benefit immediately**: libdisp.c (already migrated). Future apps that do `open(DUNEOS_INPUT_DEV)`, `open(DUNEOS_I2C0_DEV)`, etc. instead of hardcoding the paths.

> **Why before 24.9?** Immediate user-visible impact: it lets an app be ported to a second board without touching the SDK libs. 24.9 is kernel-internal — useful but invisible from the app side.

---

### Phase 24.9.5 — Captured-app exit semantics (ADR 016) ✅

**Why this phase**: an observation made while flashing gfx_demo onto the CardPuter. `duneos_exit(1)` from a captured app (a bin launched by the shell) did a `vTaskDelete(NULL)` which kills **the shell's task** (because captured = the same task as the caller). Symptom observed: "every time gfx_demo fails, my shell restarts". A classic footgun for apps written assuming POSIX `exit()` semantics. A small phase, critical for stability, to be done before the contest launcher (which will launch several captured apps).

- [x] **Shell auto-dispatch spawned/captured**: `try_run_bin` (`apps/system/shell_core/shell_cmds.c`) reads `manifest->heap_size` after `loader_load`; if > 0, unload + `supervisor_launch` (spawned mode, dedicated heap). Otherwise, the captured fast path. Lets gfx_demo run with its heap_size=81920 when the user types `gfx_demo` without `run` in front. First piece delivered 2026-05-20 — fixes the gfx_demo crash on the CardPuter.
- [x] **`setjmp`/`longjmp` mechanism**: `loader.c` installs `s_captured_jmp` (jmp_buf*) + `s_captured_code` + `s_captured_mux` (portMUX) + `s_captured_lock` (anti-nesting mutex). `duneos_loader_run_captured` does the `setjmp(env)` before `app->entry()`; `duneos_loader_captured_longjmp(code)` is called by `duneos_exit` through the `s_loader_ops.captured_longjmp` callback registered in `duneos_loader_ops_t`. Nested captured runs are refused through `xSemaphoreTake(s_captured_lock, 0) != pdTRUE`.
- [x] **Robust stdout restoration**: the `close(STDOUT_FILENO)` + `open(CAPTURE_PATH)` is reused as-is after the setjmp join — both paths (normal return + longjmp) converge at the same place after the `setjmp` block, so the capture+restore code stays linear. No double close.
- [x] **Anti-`pthread_create` hook when captured**: `api.c` wraps `pthread_create` in `api_pthread_create`, which calls `duneos_supervisor_captured_active()` (a new helper exposed by `supervisor.h`, reading `s_loader_ops.captured_active`) and returns `EPERM` when captured. Blocks the app in kernel space; libdune needs no separate check.
- [x] **Doc in `<duneos/libdune.h>`**: a new "Captured app contract" section at the top of the header — lists the 3 constraints (no pthread, free what you malloc, close what you open) + explains the dispatch through the manifest's `heap_size`. The `duneos_exit` documentation points to that block.
- [ ] **Regression test on device**: `run test_crash` from usb_shell must return to the prompt with exit code 1 without restarting the shell. Manual test with the CardPuter pending.
- [x] **CLAUDE.md Hard-Won Lessons**: the "duneos_exit captured" entry updated to reflect that the fix has shipped (longjmp through captured_active+captured_longjmp) + the captured-mode contracts.

> **Real cost**: ~30 LoC added (3 files: supervisor.h, supervisor.c, api.c). The setjmp mechanism was already implemented on the loader side before this session; only the pthread_create hook and the documentation were missing. No ABI change.

---

### Phase 24.10 — libgfx streaming mode (frugal memory) ✅

**Why this phase**: an observation made while debugging Tier A — on the CardPuter without PSRAM, the userspace back-buffer costs 64 KiB per gfx app. With 320 KiB of DRAM in total and several potentially co-resident gfx apps (launcher + game + …), the memory budget becomes critical.

- [x] **New mode in `gfx_open()`**: `gfx_open_mode(GFX_MODE_STREAM)` does not malloc a back-buffer. `gfx_open()` stays backward-compatible (= `gfx_open_mode(GFX_MODE_BUFFERED)`).
- [x] **`gfx_pixel(x,y)` supported in STREAM but slow**: emits a 1×1 area through `disp_write_area` (1 SPI tx per pixel — apps optimise by moving to `gfx_rect`/`gfx_text` for batched row writes).
- [x] **Streaming-compatible primitives**: `gfx_fill`, `gfx_rect`, `gfx_text` — each composes a stack row buffer (≤640 bytes for 320px max) then calls `stream_write_area` per row. No heap allocation.
- [x] **`gfx_flush()` is a no-op in STREAM**: drawings already committed; FB_FLUSH if the backend is FB0.
- [x] **Runtime choice**: `gfx_open_mode(mode)`; the mode is stored in `gfx_ctx`. Every draw op branches on the mode.
- [x] **`gfx_demo` migration**: moves to `GFX_MODE_STREAM` + `heap_size: 4096` (instead of 81920). Test to be validated on device — it must coexist with g_shell without exit 16.
- [x] **gfx.h doc**: explains BUFFERED vs STREAM + use cases.

> **Real cost**: ~100 LoC in gfx.c. API added: `gfx_open_mode(mode)`. Existing apps (g_shell, gfx_demo, future ones) opt in through the flag.
> **Device test pending**: `dbt flash kernel` + `dbt flashimg` then `gfx_demo` on the CardPuter with g_shell in init.yaml must no longer exit 16. The SPI conflict warning may remain — that is Phase 24.11.

---

### Phase 24.11 — drv_spi multi-owner sharing (refcount per CS) ✅

**Why this phase**: a hardware-visible regression introduced by the Phase 24 debt #6+#2 (userspace libst7789 migration). Before: the kernel `/dev/disp0` was single-owner — several apps could write to it and the kernel driver serialised the SPI transactions for free. After: each app doing `open("/dev/spi-N")` + `ioctl(SPI_SET_CS, N)` adds its own ESP-IDF `spi_device_handle_t`. Two apps with the same CS pin (obvious case: g_shell + gfx_demo, both CS=37 for the display) trip `"GPIO N is conflict with others and be overwritten"`, the GPIO matrix routing is overwritten for the second device, and when the second closes its fd the first one's routing is not restored → the first app loses its transactions. Observed 2026-05-20 on the CardPuter: `gfx_demo` exits 1 (without STREAM) or g_shell freezes after gfx_demo has run.

**Status**: ✅ validated on device 2026-05-20.

- [x] **Design: a pool of shared devices keyed by CS pin** — each `spi_bus_slot_t` has a `pool[MAX_DEVICES_PER_BUS]`; the first fd to `SPI_SET_CS=N` creates the ESP-IDF handle, the following ones refcount++. Close decrements, the last user removes it from the bus. Last-writer-wins on SET_MODE/SPEED (acceptable for same-chip sharing). Public API unchanged, transparent for apps.
- [x] **First attempt `c74f234` broke the boot** (root cause not identified) → reverted in `ed95012`.
- [x] **Second attempt `b19496b`**: lazy mutex (created on first use instead of in `register_one_bus`) + klog tracing at each step. Boot OK + multi-app SPI tested on device — g_shell + gfx_demo concurrently with no GPIO conflict, no more g_shell freeze after gfx_demo.
- [x] **Final device test**: g_shell + gfx_demo concurrently OK 2026-05-20.

---

### Phase 24.12 — Aligning the device interfaces on Linux (ADR 019) 🟡 IN PROGRESS
**Why**: the `/dev` interfaces (app-facing) must follow Linux semantics, not ESP-IDF vocabulary — so that Linux userspace knowledge and code transpose directly, and to hold to the "partial POSIX layer" goal. ESP-IDF stops at the HAL ([ADR 009](docs/adr/009-driver-boundary.md)); the layer above speaks Linux. Decision recorded in [ADR 019](docs/adr/019-linux-device-interface-semantics.md). Started during the contest sprint (i2cscope app, sniffer).

- [x] **I²C → i2c-dev**: `struct i2c_msg {addr, flags, len, buf}` + `I2C_M_RD`, `I2C_SLAVE`/`I2C_RDWR` ioctls (array of msgs), kernel primitive `i2c_transfer()`. A probe is a write msg of len 0. Drivers (drv_i2c, expanders, libbq27220) migrated; `i2c_bus_write_read`/`I2C_SET_ADDR` removed from the ABI.
- [x] **GPIO events → gpio chardev line-events**: `GPIOCHIP_SET_IRQ` arms an edge, `read()` returns timestamped `gpio_event_t`.
- [x] **`select()` on `/dev`**: `esp_vfs` bridge (`start_select`/`end_select` in `vfs_dev.c`) + a per-driver `readable()` callback; gpio/input wake the waiting selectors. The exported `select` is esp_vfs's `select` (VFS + sockets), no longer the NET-only `lwip_select`. (`poll()` on `/dev` is still to be done — esp_vfs has no `poll`.)
- [x] **I²C reinit**: `ioctl(I2C_RESET)` (a DuneOS extension) re-initialises the controller to restore the SCL/SDA mux after a sniffer has taken the pins back as GPIO — changes use without a reboot.
- [ ] **SPI → spidev**: `struct spi_ioc_transfer`, `SPI_IOC_MESSAGE(n)`, `SPI_IOC_WR_MODE`/`WR_MAX_SPEED_HZ`/`WR_BITS_PER_WORD`. Replaces the current homemade SPI ioctl.
- [ ] **UART → termios**: `tcgetattr`/`tcsetattr`, `cfsetspeed`.
- [ ] **input (evdev) / fb (fbdev) review**: check the drift against the Linux models.

---

### Phase 24.13 — Reducing the app ELF section count (ADR 022) ✅

**Why**: apps are ET_REL files produced by `ld -r` with `-ffunction-sections`/`-fdata-sections`, hence one section per function/object (+ its `.rela` twin). The relocatable link GCs nothing, and the loader maps **every** `SHF_ALLOC` section (no GC there either). Two costs: (1) the **section count** explodes — the modular `i2cscope` reached 584 and exceeded `MAX_SECTIONS=512` on 2026-06-06; (2) the **dead code of the libs** (uncalled `ui_*`/`gfx_*` functions) is loaded into the 64 KB IRAM exec pool — the scarcest resource on the CardPuter without PSRAM. `-ffunction-sections` only makes sense with `--gc-sections`, which the app link does not do → today the flag only inflates the count without returning anything. Decision recorded in [ADR 022](docs/adr/022-app-elf-section-optimization.md).

**Status**: ✅ closed. The stopgap raised the cap; lever (A) then removed the cause. The measurement, the decision and the implementation all landed.

- [x] **Stopgap 2026-06-06**: `MAX_SECTIONS` 512→1024 in `loader.c` (the `section_bases[]` array is `calloc`ed per app and freed on unload — ~4 B/entry, transient). Unblocks multi-file apps; does not address the cause.
- [x] **Measure**: done on the launcher — **528 sections** with per-function/per-data sections, **18** without (`sdkconfig.defaults:106`). The section-header table the loader reads dropped from ~28 K to <1 K, which is what unblocked the app arena.
- [x] **Choose the lever** (ADR 022): **(A) chosen** — drop `-ffunction-sections`/`-fdata-sections` from the app CFLAGS (free, kills the inflation, loses the granularity of a future GC); (B) real function-level GC (partial link with an entry root — pays the most, riskier); (C) merge the sections through a linker script (`*(.text .text.*)` — fixes the count, keeps all the code).
- [x] **Implement** in `tools/dbt/constants.py` — `CFLAGS_COMMON` (`constants.py:45-46`) carries neither flag, and the comment there records why (`ld -r` does no `--gc-sections`, so per-function sections eliminate nothing and only inflate the loader's per-load transient). No app source changed, no linker script needed. Outcome in `sdkconfig.defaults:100-107`.

---

### Phase 24.9 — Kernel-side driver self-registration (ADR 015 Pattern 1) ✅

Eliminates the hardcoded `#ifdef CONFIG_DUNEOS_DRV_*` + `extern void drv_*_register(void)` in `vfs_dev.c`. The Linux `module_init` pattern, **achieved through GCC constructors rather than an ELF section** (dodging ESP-IDF v6 linker friction).

**Status 2026-05-21: shipped.** Solution C (constructor-based) chosen after the custom ELF-section solution hit ESP-IDF v6's `--orphan-handling=error` (custom sections create a gap between `.flash.appdesc` and `.flash.rodata` that `esp_app_format` rejects).

- [x] **`kernel/duneos_kernel/include/duneos/driver_init.h` header**: a `DUNEOS_DRIVER_REGISTER(prio, fn)` macro emitting a GCC constructor `__attribute__((constructor(101)))`. At runtime the constructor calls `_duneos_driver_record(prio, fn, name)`, which appends to a static list. Constructor execution priority 101 guarantees we run after `esp_libc_init` (klog/malloc already OK).
- [x] **Registry in `vfs_dev.c`**: `s_registry[DUNEOS_MAX_REGISTERED_DRIVERS=32]`, `_duneos_driver_record()` (simple append, single-threaded at constructor time), `duneos_drivers_run_init()` which insertion-sorts by priority then calls each init.
- [x] **`vfs_dev.c` simplified**: the extern `#ifdef` block and the call `#ifdef` block replaced by a single `duneos_drivers_run_init()`. ~50 lines removed, ~30 added (registry + iterator). USB MSC/CDC stay manual (different init phase — `drv_usb_preinit` runs BEFORE the mount, from `vfs.c`).
- [x] **CMakeLists.txt — `WHOLE_ARCHIVE`**: added to `idf_component_register`. Without it, the `.o` files of drivers with no external reference (the `register` fn is no longer called by `vfs_dev.c`) would be skipped from the static archive. No perceptible binary overhead (the drivers were all already linked through the externs).
- [x] **13 drivers migrated**: `drv_null`/`uart`/`klog` (prio 5), `drv_gpio` (prio 2 — provides gpiochip0), `drv_i2c`/`spi` (prio 1 — bus controllers), `drv_battery_adc_simple`/`input`/`fb_st7789`/`raw80211` (prio 5), `drv_gpiochip_sx1509`/`pcf8574`/`mcp23017` (prio 8 — bus consumers). Each one: `#include <duneos/driver_init.h>` + one `DUNEOS_DRIVER_REGISTER(prio, drv_X_register);` line at the end of the file.
- [x] **Smoke test**: `dbt flash kernel --build-only` OK on the CardPuter AND the T-Embed (both Kconfig configurations). `dbt buildall` → 29/29 OK.

> **What it changes concretely**: adding a new kernel driver = create `drv_newchip.c`, add 1 `DUNEOS_DRIVER_REGISTER(prio, drv_newchip_register);` line at the end of the file, 1 Kconfig entry, 1 CMakeLists line. **Zero `vfs_dev.c` change**. Adding a driver now matches the Linux `module_init` UX. Before: 4 files to touch, including 2 sites in vfs_dev.c.

> **Why constructors rather than an ELF section?** ESP-IDF v6's `--orphan-handling=error` + the `esp_app_format` check make custom ELF sections non-trivial without a dedicated linker fragment (poorly documented for v6). GCC constructors go into `.init_array`, a section ESP-IDF knows about, with zero friction. Bonus: portable across arch/toolchain (future RP2040, STM32).

---

> ### Contest sprint 2026 — over, and no longer a rule
>
> The M5Stack Global Innovation Contest sprint froze Phases 26-29 until 2026-08-31. **That freeze has
> lapsed and nothing in this roadmap is frozen any more.** `docs/contest-2026.md` is deleted; what it
> held that was not recorded elsewhere is salvaged into Phase 25.6 below.
>
> What the sprint left behind, and why the roadmap still shows its shape: it absorbed Phase 24.7 and a
> minimum-viable Phase 25, and it is where `i2cscope`, the games, the launcher and the per-app icon
> model ([ADR 023](docs/adr/023-app-icon-assets.md) — `icon:` is a name, the `.dr` lives in
> `/share/icons`, not in the `.dap`) came from. **Phase 25.6 is that work**, and it is the only place
> the contest still matters.

---

### Phase 25 — dbt system (image recipes & verification)

A Yocto without Yocto's complexity. The central concept is the **profile**: a YAML file in `profiles/<name>/profile.yaml` declaring board + apps_flash + init_flash + apps_sd + init_sd. A repo has many profiles (as it has many boards and many apps). The user chooses which one through `dbt system use <name>` or a per-command `--profile` flag.

**Sub-phases**:

- [x] **25.1 — Foundation** (shipped 2026-05-21)
  - `profiles/<name>/profile.yaml` schema (name, board, description, apps_flash, init_flash, apps_sd, init_sd)
  - `tools/dbt/capability_map.py`: `DUNEOS_PERM_*` ↔ `CONFIG_DUNEOS_DRV_*` table (parsed from `boards/<board>/sdkconfig.board`)
  - `tools/dbt/system.py`: parser + active-profile resolver + check/build/flash logic
  - `dbt system list`: lists the available profiles, marks the active one
  - `dbt system use <name>`: writes `.duneos_profile` (gitignored) + aligns `.duneos_board`
  - `dbt system check [--profile X]`: validates app references + init_flash/sd coherence + warnings on perms vs kernel CONFIG
  - `dbt system build [--profile X]`: compiles only the profile's apps (apps_flash + apps_sd, deduplicated)
  - `dbt system flash [--profile X]`: stages only apps_flash + renders init_flash → flashes the sysbin (reuses `cmd_flashimg` with the profile attached)
  - `dbt system deploy <sd> [--profile X]`: copies apps_sd to the SD card (reuses `deploy_single`)
  - 4 example profiles delivered: `cardputer-default`, `cardputer-recovery`, `t-embed-default`, `t-embed-recovery`
- [x] **25.2 — TUI refactor** (shipped 2026-05-21)
  - `ProfilePickScreen`: profile selector (similar to BoardPickScreen), Enter writes `.duneos_profile` + aligns `.duneos_board`
  - `ProfileEditorScreen`: two-column editor with flash/SD side by side, `←→` navigation between columns, `↑↓` cursor, `Space` cycles (□/☑/☑+init), `R` cycles the restart policy, `S` saves → `profiles/<active>/profile.yaml`
  - Round-trip YAML serialisation (`yaml.safe_dump` block style, key order preserved)
  - Menu reorganised into sections: Image composition (profile edit/pick/check) / Flash device (kernel/sysbin/monitor) / SD / Build / Legacy / Settings
  - 3 new `DbtApp` handlers: `_run_profile_edit`, `_run_profile_pick`, `_run_system_check` (captures check_profile's stdout to display it in the RichLog)
  - The legacy `InitCfgScreen` (boards/<board>/init.yaml) is **kept** for boards without a profile — labelled "Init Config (board legacy)"
- [x] **25.3 — Polish** (shipped 2026-05-22)
  - `dbt system size`: reports kernel + sysbin + SD with `[████░░] X KB / Y KB (pp%)` bars. Parses `boards/<board>/partitions.csv` for `factory` (kernel) and `sysbin` (flash). Lists the top-5 apps by size for the flash image and for /sd. Non-zero exit when an overflow is detected.
  - `dbt system diff <other>`: compares the active profile against another, reads `apps_flash`/`apps_sd`/`init_flash`/`init_sd`, shows +/- per section; flags differing boards as a warning.
  - `system.py`: helpers `parse_partition_sizes(board)`, `app_elf_size(name)`, `kernel_image_size()`, `compute_image_sizes(profile)`, `_fmt_kb`, `_bar`, `report_sizes`, `report_diff`.
  - TUI editor: each column's border title becomes a live progress bar `[██░░░░░░] 157.7/1024 KB 15%`. Updated on every cycle. `✗ OVERFLOW` status when the `sysbin` partition is exceeded. The SD column shows count + total (informational, since SD card storage is dynamic).
  - TUI editor: an orange `⚠` indicator in front of every app whose perms mismatch the kernel CONFIG (reads `sdkconfig.board` once at load). Each app's size shown in an `N.N KB` column.
- [x] **25.5 — Standard config + image format** (shipped 2026-05-22)
  - The `/etc/<app>/config.yaml` convention is the DuneOS standard. The libdune helper `duneos_config_path(app_name, buf, sz)` returns the canonical path. `boards/<board>/etc/` is the source of truth; `dbt flashimg` copies it verbatim to `/etc/` in the LittleFS sysbin (skipping `.example`/`.template`). `.gitignore` targets the sensitive files (wifi credentials); a documented `config.yaml.example` stays committed for the schema.
  - `.dr` raster format (Dune Raster): an 8-byte header `{ magic=0xD12E, w, h, fmt=0 (RGB565 LE) }` + raw pixels. `sdk/image/libimage.c` loads the file into a malloc → a buffer ready for `gfx_blit()`. No external dependency; PNG decoding stays in the backlog (`libimage_png`).
  - Converter `dbt img convert <input.png> <output.dr> [--resize WxH] [--background R,G,B]` — lazy Pillow import (a clear error if absent, no effect on the daily build).
  - `wifi_daemon` migration: reads `/etc/wifi_daemon/config.yaml` (`:` or `=` separator), with a `/sd/wifi.conf` fallback for the transition.
  - `splash` migration: if `/etc/splash/config.yaml` contains `logo: /etc/splash/logo.dr`, blit the logo centred (libimage), otherwise procedural dune art. The CardPuter ships `boards/m5stack-cardputer/etc/splash/logo.dr` (120×120, from `duneos_logo.png`).
  - Backlog: `libimage_png` (a full PNG decoder when the need arises — picopng or the ESP-IDF jpeg_decoder), writable state `/var/<app>/state.yaml`.
- [x] **25.4 — Branding + sequenced boot** (shipped 2026-05-22)
  - Kernel: an `after:` field in `init.yaml` (`duneos_service_desc_t.after`), an exit observer in `supervisor.c` (`duneos_supervisor_set_exit_observer`), `duneos_init_run()` orchestrating immediate vs deferred launch with a pending-queue mutex. Refuses dependencies on `restart: always` services (warns but launches anyway). `main.c` delegates to `duneos_init_run`.
  - `apps/user/splash`: a one-shot libgfx STREAM mode — desert gradient + dune silhouette + centred "DuneOS" wordmark, ~1.5 s then exit. The CardPuter `init.yaml` launches `splash` then defers `kb_iomatrix` through `after: splash` (a demo).
  - `duneos.yaml`: the `icon:` field is recognised (string ≤ 64 chars), with parse validation and a warning on unknown keys (typo guard). Embedded as-is in the JSON manifest; no ABI bump (consumed by the future launcher).
  - `dbt` TUI: a `SplashScreen` with an ASCII "DuneOS" wordmark + desert tagline, 1 s at startup, dismissable by any key. Can be suppressed through `DUNEOS_TUI_NO_SPLASH=1` (CI).

---

### Phase 25.6 — Contest demo apps + launcher icons 🟡 IN PROGRESS

**Why**: the contest demo needs a visual identity (launcher icons) and apps showing that DuneOS is a real OS. `i2cscope` is delivered (✅, 2026-06-06 — scan/xfer/sniff/scenarios, `/dev/logic0`, libsmbus). What remains is the identity + the remaining apps.

**Recommended order** (from the strongest lever to the weakest): **icons → file explorer → games**. Icons are a multiplier (every app added afterwards inherits them, and the launcher is the demo's front door) and they freeze an architectural decision; the explorer is the best "real OS" showcase (VFS, the flash root + /sd, side-loading); the games are the fun cherry on top, the weakest lever.

- [x] **i2cscope** — the I²C Swiss army knife (scan / xfer / Pulseable VCD sniff / SD scenarios). Modular structure (1 file per screen) = the reference layout for multi-screen apps.
- [x] **Per-app icons (ADR 023, freedesktop model)** — standard size **32×32 RGB565**. Delivered. **The shared theme dir is `/share/icons`, not `/flash/share/icons`** — the flash filesystem is the root (`vfs.c:31`):
  - [x] `dbt build`: build-time conversion of `icon.png` → `build/icon.dr` (`build_app_icon`, non-fatal if Pillow is absent). The developer drops in a PNG, that's all. (2026-06-06)
  - [x] `dbt`: icon install at image build — `flashimg.py:207-218` prefers a hand-authored `apps/<app>/icon.dr`, falls back to the build-time `build/icon.dr`, and stages it as `/share/icons/<icon-name>.dr` under the manifest's `icon:` name. `deploy.py:68-70` copies the `.dr` next to the `.dap` on the SD card.
  - [x] launcher: name→file resolution in `sdk/ui/ui_carousel.c`, three steps rather than the four planned — (1) a `.dr` adjacent to the `.dap` (the side-loaded case), (2) `/share/icons/<icon-name>.dr` by manifest name (`:146`), (3) `/share/icons/application.dr` as the generic fallback (`:150`). No separate `/sd/share/icons` step: the adjacent-file rule already covers the SD card. Carousel UI delivered.
  - [x] a generic fallback set shipped in the image — `flashimg.py:332-353` (`_install_default_icons`) converts `assets/icons/*.png` into `/share/icons/*.dr` at image build, and never overwrites a per-app icon of the same name.
- [ ] **Responsive views (ADR 024)** — the demo runs on the CardPuter **and** the T-Embed without changing the apps. The layout derives from `ui_size()` / `board.info` (`width`/`height`), never from hardcoded pixels. Launcher: icon size derived (48 px focus, scaled previews). ✅; audit/fix of the rest (the `i2cscope` value column at `x=150`, review of `g_shell`/`gfx_demo`/`splash`). ⏳
- [ ] **Graphical file explorer**: tree navigation of the flash root + `/sd` (`opendir`/`readdir`/`stat`), libui `list` + `textview`, hexview for binaries / textview for text, `.dap` launching (loader/launcher tie-in). Reuses the i2cscope modular pattern.
- [ ] **Games**: `snake` + `tetris` (libgfx STREAM + input), `lua` REPL. The weakest architectural lever — demo fun, doable at any time.

#### Salvaged from `docs/contest-2026.md` (deleted 2026-09-06)

That file's only content not recorded elsewhere was its **"In-flight polish (2026-06)" queue** — an
ordered work list whose sequencing rationale existed nowhere else: *each item reuses the foundation
laid by the ones above, and **no item adds an always-on daemon**, because RAM is the binding
constraint on the CardPuter* ([ADR 025](docs/adr/025-app-concurrency-ram-limited.md)). That rule
outlives the contest and is why the WiFi app is on-demand rather than a boot service. Checked
against the tree today:

- [x] **Boot OOM / frugal WiFi buffers** — `sdkconfig.defaults`, [ADR 030](docs/adr/030-wifi-frugal-memory.md).
- [x] **Ambient state** — `/tmp/state/*`, `kernel/duneos_kernel/include/duneos/ambient.h`, [ADR 027](docs/adr/027-ambient-system-state.md).
- [x] **Status bar widget** — `sdk/ui/ui_statusbar.c`.
- [x] **Modifier lock (Fn/Shift/Opt)** — latched in `apps/system/kb_iomatrix/kb_iomatrix.c`, published to `/tmp/state/kbd`.
- [x] **`wifi` config app** — `apps/user/wifi/`, on-demand join, credentials in `/data/wifi/known.yaml` seeded from `/etc/wifi/known.yaml`. (The contest file said `/flash/etc/wifi/known.yaml`; both halves of that path were wrong.)
- [ ] **`edit` — a minimalist nano-style text editor.** **The one item with no other record anywhere.** No app, no ADR, no spec, no backlog entry: it existed only in the deleted file. Explicitly independent of the five above, which is why it was listed as a good standalone milestone. Kept here rather than dropped — deleting it would have lost the idea silently.
- [x] **`waves` partial rendering** — `gfx_canvas_new`/`_present` in `sdk/display/include/duneos/gfx.h`, [ADR 028](docs/adr/028-offscreen-canvas-partial-rendering.md).

Nothing else from that file survived scrutiny. Its icon design (a `.duneos_icon` ELF section plus
`iconconv.py`) is **superseded** by [ADR 023](docs/adr/023-app-icon-assets.md), which put the `.dr`
in `/share/icons` and kept it out of the `.dap`; its Lua choice is superseded by
[ADR 035](docs/adr/035-micropython-app.md); its two risk rows (the 64 KiB gfx back-buffer, the
captured-app exit killing the shell) became Phases 24.10 and 24.9.5 and CLAUDE.md lessons; the rest
was submission logistics — pitch text, video arc, prize targets — with no engineering content.

> **These last three checkboxes are stale and were not re-scored here, deliberately.** `apps/user/`
> contains `files`, `launcher`, `snake` and `tetris` today, so the explorer and the games clearly
> exist in some form — but "the directory exists" is not the same claim as "the item is closed", and
> this roadmap's own rule (§4, Phase 24 debt) forbids marking `[x]` on anything short of closure.
> Whoever built them should say so. The scripting item drifted too: no `lua` app exists, while
> [ADR 035](docs/adr/035-micropython-app.md) picks MicroPython.

---

## 5. Technical debt — the 2026-09 legacy audit

**This section speaks a different language from the phases above and below it.** A phase is a
decision about what DuneOS becomes. A **LEG finding** is an observation about what the repository
already is and should not be. Nothing is promoted from one vocabulary to the other: a finding that
gets fixed stays a finding with a `DONE` status, and a phase never absorbs one.

### Where these findings come from

Produced by `/legacy-audit` on 2026-09-02, revised 2026-09-03, 2026-09-05 and **2026-09-06**
(this merge). **Complete sweep, not sampled**: the four families `hygiene`, `portability`,
`quality`, `safety-net` across **396 tracked files**. **24 raw findings, 24 confirmed by adversarial
validation, 0 rejected.** No `critical` finding: no committed secrets, no tracked build artefacts,
no observed data loss. Priority criterion chosen at arbitration: **risk first**.

**38 IDs total = 24 audit findings + 3 enablers + 11 found by building, running and reviewing.**
LEG-25/26/27 are enablers decided at arbitration. LEG-28/29/30 are defects the Milestone 0 bench
found while being built, fixed in the same PR. LEG-31/32/33 are findings from the same work, specced
but not executed. LEG-34/35 came out of the LEG-26 corpus. LEG-36/37/38 came from flashing hardware
and from reading two pull requests. **None of the last fourteen came from the sweep** — they came
from running the thing the sweep asked for, which is the point of the paragraph below. Knowing which
kind a finding is changes how much you trust its severity, so provenance is stated per finding and
not averaged away.

Execution order is carried by milestone order, then table order, then "Remaining execution order" at
the end of this section, which overrides table order where the two disagree.

### Why a Milestone 0 before any hardening

The maintainer has no hardware on hand. The most severe findings in this audit (LEG-01 to LEG-03)
sit on the application loading path: hardening `elf_validate()` without being able to run
`flash → scan → load .dap` end to end amounts to **fixing blind**, on code where a regression makes
the system entirely unusable. The safety net therefore comes before the hardening.

This test bench is achievable with no hardware and without emulating a single peripheral:

- Espressif's `qemu-xtensa` supports `esp32` and `esp32s3`, and `qemu-riscv32` supports `esp32c3`
  (ESP-IDF v6.0.1 `tools.json`, version `esp_develop_9.2.2_20250817`).
- `idf.py qemu` ships with IDF v6.0.1 (`tools/idf_py_actions/qemu_ext.py`): it produces
  `qemu_flash.bin` + `qemu_efuse.bin`, exposes serial on `socket:5555` and GDB on `3333`.
- DuneOS mounts its LittleFS `sysbin` filesystem **first** and scans `/bin` before the SD card, and
  `dbt flashimg` already builds that image with embedded apps. **The end-to-end `.dap` path is
  therefore testable under QEMU with no emulated peripheral.**
- `tests/host/Makefile` already exists with three tests and the CI job `Run host unit tests`
  already runs them: the host bench is grafted onto it, not created from scratch.

**Deliberately narrow bench scope:** QEMU covers **boot and the loader via the flash filesystem
only**. Zero emulated peripherals, no emulated SD card (SDMMC/SPI-SD support in QEMU S3 is
unconfirmed and stays out of scope), no display or keyboard mock. **Peripheral mocks belong to the
Linux simulator of ADR 007 (Phase 26), not to QEMU** — this boundary is deliberate and must not be
crossed along the way.

Relevant targets: `m5stack-cardputer` and `esp32s3-devkitc` (Xtensa); `esp32c3-devkitc` as a RISC-V
bonus. The repository contains no non-S3 `esp32` board.

### Milestone 0 — Test bench (prerequisite for all hardening)

The structural decision behind this milestone — **a minimal QEMU bench before hardening, peripheral
mocks deferred to the Phase 26 Linux simulator** — is recorded in
[ADR 039](docs/adr/039-qemu-test-bench.md), **Accepted** (2026-09-05, once SPEC-leg-27/28/29/30
shipped the bench it describes); it fixes the shape LEG-27 implements (QEMU targets are ordinary
boards declaring only what QEMU models, driven from dbt through `plugin.run_qemu()`) and it carries
its own supersession — step two, booting the shipping CardPuter configuration under emulation, can
**not** be done by putting `qemu: true` on a real board's `board.yaml`, because that flag now
selects an exec write path that is wrong on silicon (`docs/adr/039:43`, and the Consequences bullet
"The `qemu:` key constrains step two" at `:49`).

The hard blocker came first: `kernel/duneos_loader/src/loader.c` (1720 lines) includes
`freertos/FreeRTOS.h`, `freertos/task.h`, `freertos/semphr.h`, `esp_heap_caps.h`, `esp_rom_sys.h`,
`soc/soc.h`, plus `duneos/supervisor.h`, `duneos/api.h`, `duneos/klog.h`, `duneos/shellpipe.h` and
`cJSON.h`. Nothing in it compiled on the host as it stood.

| ID | Finding | Severity | Size | Depends on | Spec | Status |
|---|---|---|---|---|---|---|
| LEG-25 | ELF parser/validator not extractable: `loader.c` is coupled to FreeRTOS, the IDF heap and the supervisor | enabler | L | — | [specs/SPEC-leg-25-extract-elf-parser-host.md](specs/SPEC-leg-25-extract-elf-parser-host.md) | DONE (`8f1d3e6`) |
| LEG-05 | `duneos_loader_load()` 229 lines, 10 steps, 6 resources on a single `out` label | minor | M | LEG-25 | [specs/SPEC-leg-05-split-loader-load.md](specs/SPEC-leg-05-split-loader-load.md) | TODO |
| LEG-26 | No tests and no malformed-ELF corpus for the validation path | enabler | M | LEG-25 | [specs/SPEC-leg-26-host-harness-elf-corpus.md](specs/SPEC-leg-26-host-harness-elf-corpus.md) | **DONE** — `tests/host/elf_corpus.c` + `test_elf_validate.c` + `fuzz_elf_validate.c`, run by CI (`ci.yml:14-15` host suites, `ci.yml:55-89` the libFuzzer job) |
| LEG-27 | No way to run `flash → scan → load .dap` without hardware | enabler | M | — | [specs/SPEC-leg-27-smoke-qemu-s3.md](specs/SPEC-leg-27-smoke-qemu-s3.md) | DONE (PR #4, `1d8a14d`) |
| LEG-28 | `esp_adc` linked unconditionally: its `.init_array` constructor busy-waits forever under `qemu-xtensa` on a board with no ADC | major | S | LEG-27 | [specs/SPEC-leg-28-adc-linked-unconditionally.md](specs/SPEC-leg-28-adc-linked-unconditionally.md) | DONE (PR #4, `1d8a14d`) |
| LEG-29 | Loader's exec path unreachable under QEMU (IRAM/DRAM alias) | major | M | LEG-27 | [specs/SPEC-leg-29-qemu-iram-dram-alias.md](specs/SPEC-leg-29-qemu-iram-dram-alias.md) | DONE (PR #4, `1d8a14d`) |
| LEG-30 | `esp32s3-qemu-psram` exhausts the external-memory vaddr window and cannot find `sysbin` | major | M | LEG-27 | [specs/SPEC-leg-30-qemu-psram-vaddr-exhaustion.md](specs/SPEC-leg-30-qemu-psram-vaddr-exhaustion.md) | DONE (PR #4, `1d8a14d`) |

LEG-25, LEG-26 and LEG-27 are not audit findings: they are **enablers** decided at arbitration.
LEG-27 is independent of the other three and could run in parallel.

LEG-28, LEG-29 and LEG-30 are not audit findings either: they are **blockers discovered by LEG-27's
bench during its own construction**, and were fixed in the same pull request that delivered it.
They are recorded here rather than in a milestone of their own because they exist only as
consequences of Milestone 0, and because they are the evidence for the paragraph below.

#### What the bench cost and what it found

The bench was argued for on the grounds that hardening blind is not hardening. It paid for itself
before it was finished. Building it surfaced **five real defects**, each of which would have
mis-attributed a later failure:

1. **`esp_adc`'s constructor** — `WHOLE_ARCHIVE` force-linked an ADC HAL no board asked for, and
   its `.init_array` calibration busy-waited forever under `qemu-xtensa` (LEG-28).
2. **IRAM/DRAM alias** — the loader's exec path was unreachable under emulation (LEG-29).
3. **`fd 1` routing** — console output not reaching the expected descriptor, fixed in PR #4.
4. **PSRAM vaddr exhaustion** — the external-memory window filled and `sysbin` could not be found
   on `esp32s3-qemu-psram` (LEG-30).
5. **`.duneos_board` race** — concurrent board resolution during the build, fixed in PR #4.

Three further findings came out of the same work and are now tracked rather than fixed:
**LEG-31**, **LEG-32** and **LEG-33** (Milestones 2 and 3). None of the eight was visible to the
static audit that produced this roadmap; all eight came from running the system. **That is the
argument for Milestone 0, and it is now an observation rather than a prediction.**

### Milestone 1 — ELF loader robustness

The loader parses ELF files coming from a removable SD card, that is, from an untrusted source.
Three findings described the same defect class: indices and offsets read from the file used without
bounds checking. `duneos_loader_scan()` walks this path for **every file encountered**, so a single
file dropped on the SD card triggers it, without launching an app. **The three are now fixed
(SPEC-leg-01, `Status: APPROVED`), each guard verified at its site.**

| ID | Finding | Severity | Size | Depends on | Spec | Status |
|---|---|---|---|---|---|---|
| LEG-01 | `e_shstrndx` unbounded before indexing `shdrs` | major | XS | LEG-26 | [specs/SPEC-leg-01-harden-elf-validation.md](specs/SPEC-leg-01-harden-elf-validation.md) | **DONE** — `elf_parse.c:36` `if (hdr->e_shstrndx >= hdr->e_shnum) return -EINVAL` (`DUNEOS_ELF_REJ_SHSTRNDX`) |
| LEG-02 | `sh_link` unbounded before indexing `shdrs` | major | XS | LEG-01, LEG-26 | same spec | **DONE** — bounded once at parse time (`elf_parse.c:138-145`, only for the section types that actually index with it) and again at the site that indexes (`loader.c:1292-1297`, guard deliberately duplicated where the index is used). The link's TARGET TYPE is bounded alongside it since SPEC-leg-34's verification (`elf_parse.c:147-154`, `loader.c:1305-1312`): an in-range index to a `SHT_NOBITS` section still carried an unbounded `sh_size` into `malloc()` |
| LEG-03 | `sh_name` / `st_name` offsets unbounded before `strcmp` | major | S | LEG-01, LEG-26 | same spec | **DONE** — `sh_name` at `elf_parse.c:131-137`, every string read through `duneos_elf_string()` at `elf_parse.c:243-248`, and all `st_name` bounded in one pass at `loader.c:1328-1336` before any of the three lookups |
| LEG-04 | `calloc` exposed to apps without `n * size` overflow detection | major | XS | — | [specs/SPEC-leg-04-calloc-overflow.md](specs/SPEC-leg-04-calloc-overflow.md) | **DONE** (PR #8) — `supervisor.c:1301` `if (__builtin_mul_overflow(n, size, &total)) return NULL;`, the same primitive `heap_caps_calloc_base()` uses, so the slot-heap and global-heap branches agree by construction. Guards both the `multi_heap_malloc` size and the `memset` length — the latter was the actual corruption vector. A zero product is deliberately not special-cased. Gated on both QEMU boards by `apps/user/qemu_calloc` |
| LEG-34 | No section extent check: `sh_offset + sh_size` may wrap past `UINT32_MAX` or run past EOF and is still read; `duneos_elf_io_t` carries no file size | major | S | LEG-26 | [specs/SPEC-leg-34-bound-section-extents.md](specs/SPEC-leg-34-bound-section-extents.md) | **DONE** — `duneos_elf_io_t` carries the image size; `elf_parse.c` `check_section_extents()` rejects `sh_offset > size` (`DUNEOS_ELF_REJ_SH_OFFSET`) then `sh_size > size - sh_offset` (`DUNEOS_ELF_REJ_SH_SIZE`), before any allocation, so the 2.4 GiB `malloc` the CI fuzzer found is unreachable through `duneos_elf_image_open()` — including through `shdrs[symtab->sh_link]`, whose target must now be an `SHT_STRTAB` (`DUNEOS_ELF_REJ_SH_LINK_TYPE`). `SHT_NOBITS` keeps the `sh_size` exemption only, and only when it is not the section name table (a `.bss` bigger than the object is well-formed); `SHT_NULL` gets no exemption, its `sh_size` being meaningless and zero in every object that reaches the check (`duneos_elf_validate()` already rejects the `e_shnum == 0` extended-numbering form that is the one convention giving `shdrs[0].sh_size` a meaning), pinned by corpus case `null_size_past_eof`; `load_sections()` caps every section against a single memory ceiling (`LOADER_MAX_SECTION_BYTES`) before the 4-byte round-up, which is `size_t` and wraps to 0 on a 32-bit target, and guards both pool accumulators against a sum that overflows `size_t` — 1024 sections at the 4 MiB PSRAM ceiling reach exactly 2^32 and would otherwise wrap to a NULL data pool |
| LEG-35 | A zero-size `.symtab` is not refused by the validator: `symcount = 0`, `malloc(0)`, and the image is rejected only later by an `app_main not found` message that names the wrong cause | minor | XS | LEG-26 | [docs/backlog.md](docs/backlog.md) `BL-ELF-EMPTY-SYMTAB` | TODO |
| LEG-36 | `dbt flash sysbin` ignores the active profile and stages every built app (71 files, 1263 KiB) into a 1024 KiB partition, failing with a raw `LittleFSError -28` traceback; `dbt system flash` stages the same board's profile correctly (35 apps, 500 KiB per `dbt system size`). Two commands disagree on what belongs in the image, and the one that is wrong is the one whose name suggests it. Found by flashing a real CardPuter, not by any test. Aggravating detail: the failure surfaces as a Python traceback from inside the LittleFS binding although the staged size is known before a single byte is written and `dbt system size` already reports the correct figure — a one-line pre-flight would say "staging 1263 KiB > partition 1024 KiB" | major | S | — | — | TODO |
| LEG-37 | `main_task` runs the whole boot — LittleFS mount, SD mount, and the loader scan of every `.dap` — on the 3584 B ESP-IDF default stack, with no measured margin. Adding one call frame to the loader path overflowed it into the adjacent heap: TLSF free-list corruption and a watchdog reboot loop, never a clean stack-overflow report. Fixed by declaring a measured 4608 B in `boards/m5stack-cardputer/board.yaml`, translated by bspgen (peak 3764 B on hardware, 784 B of usable margin after the watchpoint's 60 B), and by arming `CONFIG_FREERTOS_WATCHPOINT_END_OF_STACK` project-wide (`sdkconfig.defaults:57`) so the next overflow reports itself. Covered by `tools/dbt/tests` | major | S | — | [specs/SPEC-leg-37-per-board-main-stack.md](specs/SPEC-leg-37-per-board-main-stack.md) | FIXED |

**One detail of LEG-36 generalises past it and stays separate.** `tools/dbt/flashimg.py:29` hardcodes `_SYSBIN_SIZE = 0x100000` with the comment "must match partitions.csv" — an **unlinked duplicate of the partition table**, the same class of defect `loader_limits.h` removed on the loader side. It is wrong whatever `dbt flash sysbin` decides to stage, so fixing LEG-36's profile handling does not fix it.

**LEG-37 was found the hard way, and it is the most important entry in this table.** Merging
LEG-25/27/28/29/01 bricked a working CardPuter into a reboot loop. Bisecting on hardware isolated
LEG-25 — the ELF parser extraction whose commit message says "extraction at constant behaviour" —
and a control build proved it was not a pre-existing bug: baseline clean, LEG-25 corrupt, same
board, same SD, same heap poisoning. The extraction added one struct local and one call frame on a
path that reaches `lfs_bd_crc`, and 3584 B left nothing to absorb it. The failure mode is what makes
it costly: no stack-overflow message, just a corrupted heap detected thousands of instructions
later, at a different place on each boot. `CONFIG_FREERTOS_WATCHPOINT_END_OF_STACK` named it in one
run — that flag belongs in the diagnostic playbook.

**What no bench caught, and why.** ADR 039 bounds the QEMU bench to "the loader, the VFS and the
boot sequence, not the CardPuter": no display, no SD, no TinyUSB, and a different stack watermark.
The host corpus runs the parser with a host-sized stack. Both did exactly what they were specified
to do. What was missing is a rule, not a tool: **a change that alters what a physical board links or
how deep it recurses must be run on that board before it reaches `main`.** (Also recorded in
CLAUDE.md's Hard-Won Lessons, where every agent reads it.)

**LEG-34 and LEG-35 were found by the LEG-26 corpus, not by the audit sweep**, and were both
outside SPEC-leg-01's scope: that spec bounds indices and string offsets, while these two needed a
notion the pure unit did not have. LEG-34 is now fixed by SPEC-leg-34, which gave `duneos_elf_io_t`
the image size and bounded every section extent against it; its corpus case is green and its
`UNPLANNED` marker is gone. Its adversarial verification found two ways the same exemption stayed
reachable and both were closed in that spec's own change rather than deferred: an `SHT_SYMTAB`
whose `sh_link` named a `SHT_NOBITS` section handed the loader an unbounded `sh_size` to `malloc`
(the fuzzer's 2.4 GiB request through another field — corpus case `symtab_strtab_nobits`), and an
`SHF_ALLOC` `.bss` with `sh_size = 0xfffffffe` wrapped `(sh_size + 3) & ~3` to 0 in 32-bit `size_t`
and got a 4 GiB `memset` into a zero-sized placement — heap corruption, invisible on a 64-bit host,
now capped in `load_sections()` before the round-up. LEG-35 remains the single expected failure marked `UNPLANNED`, and the
suite still prints that count separately so it stays visible until specced.

### Milestone 2 — Foundations: hygiene, licensing and build reproducibility

| ID | Finding | Severity | Size | Depends on | Spec | Status |
|---|---|---|---|---|---|---|
| LEG-06 | No LICENSE file on a public repository open to PRs | major | XS | — | [specs/SPEC-leg-06-add-license.md](specs/SPEC-leg-06-add-license.md) | TODO |
| LEG-07 | `.gitignore:15` (`*.lock`) hides `dependencies.lock`. **The file is tracked today** (`git ls-files` finds it), so what remains is the ignore rule that will silently drop the next one | major | XS | — | [specs/SPEC-leg-07-version-dependencies-lock.md](specs/SPEC-leg-07-version-dependencies-lock.md) | TODO |
| LEG-08 | CI runs the dbt Python suite (`ci.yml:25-34`, `python -m pytest tools/dbt/tests -q`). **What is missing is the gate's own proof**: a deliberately failing test showing the job actually goes red, and an explicit collection root so a collection error cannot pass for a green run. `ci.yml:21-24` names both as this spec's scope | major | XS | — | [specs/SPEC-leg-08-run-python-tests-in-ci.md](specs/SPEC-leg-08-run-python-tests-in-ci.md) | TODO |
| LEG-09 | `_DEPS` (`tools/dbt.py:25`) and `pip install pytest pyyaml textual` / `pyyaml littlefs-python` in CI without version constraints | major | S | — | [specs/SPEC-leg-09-pin-python-dependencies.md](specs/SPEC-leg-09-pin-python-dependencies.md) | TODO |
| LEG-10 | CI covers **3 boards out of 8** (`m5stack-cardputer` kernel build, `esp32s3-qemu` and `esp32s3-qemu-psram` under QEMU) and **1 profile out of 7** (`cardputer-contest`). **No RISC-V target is built** — that half of the finding is unchanged, and `esp32c3-devkitc` / `esp32p4-devkitm` exist as boards | major | M | — | [specs/SPEC-leg-10-ci-board-profile-matrix.md](specs/SPEC-leg-10-ci-board-profile-matrix.md) | TODO |
| LEG-11 | `.devcontainer/` gitignored (`.gitignore:23`): development environment not shared | minor | XS | — | [specs/SPEC-leg-11-repo-hygiene-batch.md](specs/SPEC-leg-11-repo-hygiene-batch.md) | TODO |
| LEG-12 | No `.gitattributes` despite `ci/factory.ps1` and Windows contributors | minor | XS | — | [specs/SPEC-leg-11-repo-hygiene-batch.md](specs/SPEC-leg-11-repo-hygiene-batch.md) | TODO |
| LEG-13 | `duneos_logo.png` 1.5 MB tracked, merely the source of a 120×120 asset | minor | XS | — | [specs/SPEC-leg-11-repo-hygiene-batch.md](specs/SPEC-leg-11-repo-hygiene-batch.md) | TODO |
| LEG-14 | No CONTRIBUTING file and no "Contributing" section | minor | XS | — | [specs/SPEC-leg-11-repo-hygiene-batch.md](specs/SPEC-leg-11-repo-hygiene-batch.md) | TODO |
| LEG-15 | `file(GLOB)` over `arch.cmake` without `CONFIGURE_DEPENDS` (`kernel/duneos_kernel/CMakeLists.txt:91`; the same applies to the blob glob at `:182`) | minor | XS | — | [specs/SPEC-leg-15-glob-configure-depends.md](specs/SPEC-leg-15-glob-configure-depends.md) | TODO |
| LEG-16 | 4 IDF components at `version: "*"` outside the lock's scope (`kernel/duneos_kernel/idf_component.yml:20-35` — lan87xx, ksz80xx, rtl8201, ip101) | minor | S | LEG-07 | [specs/SPEC-leg-16-pin-idf-components.md](specs/SPEC-leg-16-pin-idf-components.md) | TODO |
| LEG-31 | Latent compile break: `CONFIG_DUNEOS_DRV_I2C` means "an I2C section exists" but gates code that dereferences bus 0 by name — a board declaring `i2c: [{id: 1}]` does not build | major | S | — | [specs/SPEC-leg-31-i2c-guard-vs-bus-zero.md](specs/SPEC-leg-31-i2c-guard-vs-bus-zero.md) | TODO |
| LEG-32 | `CONFIG_DUNEOS_DRV_LOGIC=y` emitted for every board against a `default n` Kconfig | minor | M | — | [specs/SPEC-leg-32-drv-logic-emitted-for-every-board.md](specs/SPEC-leg-32-drv-logic-emitted-for-every-board.md) | TODO |
| LEG-38 | A test that reads a gitignored build artefact is green only on the machine that produced it. Shipped twice: `tools/dbt/tests/test_bspgen_uart.py` (PR #5) and `tools/dbt/tests/test_sdkconfig_check.py` (PR #7), both caught by a human reading a diff. Compounded by a collection-time `ImportError` that aborted the whole dbt suite while the job looked green — 202 tests unrun | major | S | — | [specs/SPEC-leg-38-tests-must-not-read-generated-files.md](specs/SPEC-leg-38-tests-must-not-read-generated-files.md) | TODO |

LEG-31 and LEG-32 come from the Milestone 0 bench work, not from the audit sweep. Both are build
reproducibility, which is why they sit here rather than in Milestone 1.

LEG-31 is **latent**: no board in the repository instantiates it, which is the only reason it has
never fired. It is scored `major` on the failure it produces (a compile error inside `vfs.c` that
points at the kernel instead of at the board file), not on its current frequency.

LEG-32's **product ruling is signed** (2026-09-05, parts 1 and 2 in the spec): `/dev/logic0` is a
peripheral, declared by a bare `logic:` key with no pin list, on `m5stack-cardputer` only, and
`CONFIG_DUNEOS_DRV_GPIO` is explicitly out of its scope. Its four open questions are closed. **It is
ready to build** — the `M` size is schema plus guard, not design.

**LEG-38 sits in this milestone rather than in a "not retained" list**, which is where it was
first filed by mistake: it is active, specced
([SPEC-leg-38](specs/SPEC-leg-38-tests-must-not-read-generated-files.md), `Status: PROPOSED`), and
it is a build-reproducibility finding like the two above it. Its rule — **a test must construct what
it asserts against, and must never read a gitignored artefact** — is also in CLAUDE.md's Hard-Won
Lessons, because both occurrences were written by agents reading that file.

### Milestone 3 — Remaining test safety net and documentation consistency

| ID | Finding | Severity | Size | Depends on | Spec | Status |
|---|---|---|---|---|---|---|
| LEG-17 | ADR 012 (Accepted) prescribes **Greatest**, a `dbt test` entry point and `docs/testing.md`. A host harness exists but not the one the ADR names: `tests/host/tassert.h` (hand-rolled), 5 suites plus a libFuzzer target, driven by `make` and not `dbt test`; `docs/testing.md` still absent. **LEG-39 was merged into this row on 2026-09-06** — it prescribed the same `dbt test` verb, and one verb cannot have two owners. It brings the measured evidence that **2 of the 4 gates are CI-only on a fresh clone** (no `pytest`, no libFuzzer-capable `clang`; ESP-IDF's `esp-clang` cannot substitute — Xtensa/RISC-V backends only, no host `libclang_rt.fuzzer`, broken `ld.lld`), which is criterion 4 of this spec already failing today, and the `dbt doctor` half that ADR 012 never anticipated. A **third outcome (C)** now sits beside A and B: keep `tassert.h`, adopt `dbt test` + `docs/testing.md`, supersede ADR 012 clause by clause. It must be chosen deliberately, not by default | major | M | — | [specs/SPEC-leg-17-realign-adr-012.md](specs/SPEC-leg-17-realign-adr-012.md) | TODO |
| LEG-18 | `tests/host/test_known_yaml.c` tests a verbatim copy of the parser, manual resync | major | S | — | [specs/SPEC-leg-18-remove-yaml-parser-copy.md](specs/SPEC-leg-18-remove-yaml-parser-copy.md) | TODO |
| LEG-19 | Untested pure logic: LD2450 decoder, `validate_manifest`, `decode_perms`, `resolve` | major | M | LEG-08 | [specs/SPEC-leg-19-pure-logic-tests.md](specs/SPEC-leg-19-pure-logic-tests.md) | TODO |
| LEG-20 | `font8x8.h` duplicated byte-for-byte between `apps/user/g_shell` and `sdk/display/include/duneos` | minor | XS | — | [specs/SPEC-leg-20-doc-coherence-batch.md](specs/SPEC-leg-20-doc-coherence-batch.md) | TODO |
| LEG-21 | No test command in CLAUDE.md or README. **Half-closed**: CLAUDE.md's Build & Test section now names `make -C tests/host test`, `python -m pytest tools/dbt/tests -q` and `dbt qemu`. The README still does not | minor | XS | — | [specs/SPEC-leg-20-doc-coherence-batch.md](specs/SPEC-leg-20-doc-coherence-batch.md) | TODO |
| LEG-22 | README.md:23 and CLAUDE.md announce 17 ADRs against **41 actual** (`docs/adr/*.md` minus `README.md`; the audit's own figure of 40 was one behind). CLAUDE.md is corrected; the README is not | minor | XS | — | [specs/SPEC-leg-20-doc-coherence-batch.md](specs/SPEC-leg-20-doc-coherence-batch.md) | TODO |
| LEG-23 | No git tag across 213 commits, no VERSION and no CHANGELOG | minor | S | — | [specs/SPEC-leg-20-doc-coherence-batch.md](specs/SPEC-leg-20-doc-coherence-batch.md) | TODO |
| LEG-33 | REQUIRES over-declaration in `arch.cmake` / kernel `CMakeLists.txt`, plus the WiFi opt-out polarity in bspgen | minor | XS | — | [specs/SPEC-leg-33-requires-over-declaration.md](specs/SPEC-leg-33-requires-over-declaration.md) | TODO |
| LEG-40 | `dbt tui` exposes 13 actions and **no test action** (zero occurrences of qemu/pytest/test/fuzz in `tui.py`), though `_stream()` + `RichLog` already stream a `Popen` live. Blocked twice over: the TUI only shells to `dbt`, so it needs LEG-17's `dbt test`; and the QEMU gate is **unreachable without clobbering `.duneos_board`** — `dbt qemu` reads that file and refuses when `--board` differs (`qemu.py:925`), so exposing the action as-is ships a trap that breaks the developer's physical-board selection | minor | M | LEG-17 | [specs/SPEC-leg-40-tui-tests-menu.md](specs/SPEC-leg-40-tui-tests-menu.md) | PROPOSED |

LEG-33 was **re-sized from S to XS after re-verification**: seven of the nine entries the audit
listed as over-declared are in fact referenced and must stay. What remains is one verified duplicate
(`esp_netif` in `arch/xtensa_esp32s3/arch.cmake`) plus one build-verifiable candidate (the `driver`
umbrella). The bulk of the spec's work is now documenting, by `file:line`, why each surviving entry
stays — which is what stops the next audit re-filing it. Its `main/main.c:72` stale-comment item
migrated to LEG-20's batch, where the documentation sweep of the flash-root paths lives.

### Deferred — not scheduled in this cycle

| ID | Finding | Reason |
|---|---|---|
| LEG-24 | `arch/riscv32/` has **no `arch.cmake` and no `hal/`**: an `esp32c3-devkitc` build would proceed with no arch HAL. **The relocations are not the gap** — `apply_riscv_reloc()` is implemented in `loader.c:735` with its table at `elf.h:89-108`; `arch/riscv32/reloc/loader_reloc_riscv.c` is a 24-line comment stub. What is missing is the build glue and the six HAL files | Severity lowered at arbitration: acknowledged and documented STUB. To reopen when Phase 28 opens — see Phase 28, whose RISC-V bullets carry the same correction. |

### Findings rejected at validation

**None.** All 24 raw findings survived adversarial contestation, each piece of evidence having been
re-read at the source by an independent validator. Two estimate corrections were applied: LEG-24
lowered in severity, and LEG-05 corrected — the claim "only function above 200 lines" was false,
`supervisor_task` is about 213.

### Remaining execution order

Milestone order and table order carry the default sequencing. Three of the four specs this section
originally sequenced are executed (LEG-26, then LEG-01/02/03, then LEG-04), so what is left of that
decision is two entries and one piece of reasoning worth keeping.

LEG-04 is executed (PR #8), so what this section sequences is now:

1. **LEG-35** — the last corpus finding still failing, once somebody specs what a validator should
   say about a zero-size symbol table. (LEG-34, its sibling, is done: see SPEC-leg-34.)
2. **LEG-36** — a hardware-found packaging defect with no spec yet, plus its `_SYSBIN_SIZE`
   duplicate. It is the only open finding that broke a real workflow on real hardware.

**LEG-31, LEG-32 and LEG-33 are deliberately NOT next**, despite LEG-32's ruling being signed and
LEG-33 being XS. They are build-system correctness, not security, and LEG-31 is latent — no board
in the repository instantiates it, so nothing is failing today. They wait behind the loader
hardening the bench was built to enable. Taking them first would be choosing the cheap work over
the work that motivated Milestone 0.

---

## 6. Phases — what comes next

### Phase 25.9 — Config layering: get `sdkconfig.defaults` out of the root 🔵 OPPORTUNISTIC

**Not a milestone. Do it in slices, whenever a phase already touches a config line** — the point is
to arrive at Phase 26 with less to untangle, not to spend a sprint here.

`sdkconfig.defaults` holds 28 `CONFIG_*` lines at the repository root, and every board pays all of
them. That was fine while every target was an ESP32. It stops being fine for two reasons, and
[ADR 040](docs/adr/040-config-layering.md) records the rule:

- **A value can be a project policy or a board measurement, and the two do not belong together.**
  LEG-37 is the worked example: `main_task_stack` is a per-board figure measured on hardware (the
  CardPuter peaks at 3764 B where ESP-IDF defaults to 3584; an `esp32c3` will not need the same
  number, because RISC-V does not spill a register window per call frame the way Xtensa does), while
  `CONFIG_FREERTOS_WATCHPOINT_END_OF_STACK` is a policy that should hold everywhere. It is not free —
  ESP-IDF's Kconfig help states the usable stack "decreases by up to 60 bytes" per task — but the cost
  is uniform across boards rather than derived from any one of them, which is what puts it at the root.
  Putting the first at the root makes every board pay one board's maximum.
- **`sdkconfig.defaults` is an ESP-IDF artefact.** RP2040 (Phase 29) has no such file, and neither
  will the Linux simulator that Phase 26's `pthread_osal.c` unblocks. Anything expressed only as a
  Kconfig symbol has to be re-expressed for each new SDK; anything expressed in `board.yaml` is
  translated by the generator, which is the shape [ADR 015](docs/adr/015-declarative-driven-architecture.md)
  already established for `console:`, `psram:` and `qemu:`.

**Rough shape of the sort** (28 lines today: 12 `ESP_*`, 7 `LWIP_*`, 3 `FREERTOS_*`, 2 `VFS_*`,
2 `FATFS_*`, 1 `LOG_*`, 1 `DUNEOS_*`). Each line lands in exactly one of:

| Where | What | Example |
|---|---|---|
| root — **policy** | true for every board; any cost is uniform, not derived from one target | stack-overflow watchpoint, log level |
| `board.yaml` — **measurement or capability** | a number derived from *this* board, or a peripheral it has | `main_task_stack`, PSRAM, console, SD |
| `arch.cmake` — **architecture** | true for an ISA/SDK family, not for one board | anything conditioned on Xtensa vs RISC-V |
| delete | inherited, no longer justified by anything | to be found by reading each line's history |

**How to make progress without a sprint**: when a phase edits a `CONFIG_*` line for its own reasons,
classify it and move it then. Do not migrate lines nobody is touching — an untested move is worse
than a misplaced line, since the failure mode is a board that silently loses a setting.

**What this buys Phase 26**: the OSAL has to answer "what is target-specific?" for every primitive it
abstracts. Every line already sorted is one fewer to sort under the pressure of a rewrite, and the
`board.yaml` ones are the ones that survive the arrival of a second SDK unchanged.

---

### Phase 25.10 — Distribution model: reaching a board somebody already owns 🔵 OPEN QUESTION

**No arbitration and no ADR in this phase.** Two candidate routes are recorded here so the
constraint is visible before Phase 26 starts moving code around the boot path. Choosing one, both,
or neither is a later decision.

**Context.** The ESP32 community installs firmware through launchers (M5Launcher, Bruce,
M5Stack-SD-Updater). They copy a `.bin` from the SD card into an OTA app slot, set the boot
partition and reboot. Two facts follow, and they are what the routes below split on: what a launcher
writes is the **app image alone**, and **the partition table stays the launcher's** — our
`partitions.csv` is never flashed. So `sysbin` and `userdata` do not exist under a launcher, and
today that is a dead boot: `duneos_vfs_mount_flash()` is the first thing `duneos_vfs_init()` does
and its failure is fatal (`vfs.c:394`), so `main.c:112` goes straight to `kernel_idle()` — **before**
the SD is even mounted (`vfs.c:405`). Not a degraded boot: no `/init.yaml`, no `/bin`, no SD, no
`.dap`.

**Route A — launcher-compatible (SD-only boot).** Ship `DuneOS.bin` as something a launcher can
start. What it costs:

- Make the flash mount **non-fatal** and add an SD-only boot path (init from `/sd/init.yaml`, apps
  from `/sd/apps`). This is the real change: it makes "flash-first, fatal if absent" conditional,
  and that is a boot invariant, not a detail.
- `duneos_vfs_provision_flash()` (firmware-embedded blobs → `/bin`) has nowhere to write. Either it
  targets the SD or the SD image has to ship those apps.
- No `userdata` partition → nothing survives a reflash (wifi `known.yaml`, scores, prefs need an SD
  fallback or disappear).
- Fit inside the launcher's app slot (`factory` is `0x180000` today — probably fine, to be checked
  per launcher).
- A way **back** to the launcher (`esp_ota_set_boot_partition()` on a key combo), otherwise the user
  is stuck in DuneOS. Ties into Phase 24.7's hold-key work.
- Console/USB: TinyUSB takes the USB back from the launcher; behaviour to be checked, not assumed.

Value: "try DuneOS without overwriting your Cardputer" — the cheapest possible first contact for
somebody who already owns the board.

**Route B — full-image install.** One artefact (bootloader + partition table + app + `sysbin`
image) flashed with esptool or M5Burner. This is the honest shape for an OS: it takes the device
over, table included, and every invariant holds unchanged. `dbt flashimg` today builds and flashes
**only** the `sysbin` LittleFS image, so what is missing is the merge into a single distributable
`.bin`, a documented one-liner, a release artefact, and possibly an M5Burner entry.

**Open questions, deliberately unanswered here:**

- Are both wanted, or does B make A a distraction?
- If A: build variant (`board.yaml` → `boot: sd_only`?) or runtime fallback on a missing `sysbin`?
- Does relaxing the flash-first invariant need an ADR before any code?
- Ordering: Phase 26 does not touch `vfs.c`'s mount logic, but **Phase 27 rewrites the VFS
  wholesale**. Doing Route A before 27 may mean writing the SD-only path twice.

---

### Phase 26 — OSAL and scheduler portability

**Swapped with the former Phase 26 (native VFS).** Reason: the OSAL is a smaller API surface, unblocks `dbt sim` sooner, validates the abstraction on 2 Espressif arches (Phase 28) before the riskier VFS rewrite, and provides the primitives (mutex, sem) the native VFS will use in Phase 27.

Abstract FreeRTOS behind a clean interface. **DuneOS does not reimplement a scheduler** — FreeRTOS stays the scheduler on every target that supports it. The OSAL lets targets without native FreeRTOS (the Linux simulator, future architectures) use an alternative implementation. **API frozen by ADR 002, 003, 004 — do not deviate without a new ADR.**

- [ ] **`duneos_osal.h`**: implement per ADR 002. Task, mutex, sem, queue, mem primitives (with the ADR 003 flags), monotonic_us, panic_print. No software timer in this first version.
- [ ] **`freertos_osal.c`**: implementation for every FreeRTOS platform (ESP32-S3 Xtensa, ESP32-C6 RISC-V, RP2040 SMP). A single file reused across the toolchain plugins.
- [ ] **`pthread_osal.c`**: pthread-based implementation — enables `dbt sim` (a native Linux/macOS simulator). ADR 004 mapping onto `SCHED_OTHER` (priorities handled through nice).
- [ ] **WiFi blob confinement**: isolate the Espressif WiFi blob dependency behind `freertos_osal.c` (the FreeRTOS callbacks and structs the blob expects stay in the ESP-IDF OSAL `.c`).

**Associated migrations — purging `freertos/*.h` and `esp_heap_caps.h` from the kernel core**:

- [ ] **`task.h`, `supervisor.h`**: migrate the public `esp_err_t` signatures → `int` (-errno). Debt inherited from Phase 24; it happens naturally here because the underlying implementations change.
- [ ] **`supervisor.c`, `task.c`, `symbols.c`, `klog.c`, `api.c`**: replace `freertos/*.h` + `esp_heap_caps.h` + `esp_system.h` with the `duneos_osal.h` primitives. Covers task creation, queues/mutexes/semaphores, internal memory allocation.
- [ ] **`esp_rom_printf` → `osal_panic_print()`**: last-resort output (exception/WDT/stack-overflow handlers). Implemented by `esp_rom_printf` on the ESP32, `fprintf(stderr,...)` on the Linux simulator. No `klog_e()` fallback (crash risk in a crashed context).
- [ ] **`vfs_dev.c`, `vfs_tmp.c`**: replace `freertos/*.h` (ring buffers, semaphores) with `duneos_osal.h`. Note: `esp_vfs.h` stays, Phase 27 is what removes it.
- [ ] **`drv_fb_st7789.c`**: replace `esp_heap_caps.h` (PSRAM allocation) with `osal_mem_alloc(size, OSAL_MEM_EXTERNAL)`.
- [ ] **`duneos_loader/src/loader.c`**: 3 sites of `heap_caps_malloc(MALLOC_CAP_SPIRAM \| MALLOC_CAP_8BIT)` / `MALLOC_CAP_INTERNAL` (allocation of the app's sections: code, data, heap) → `osal_mem_alloc(size, OSAL_MEM_EXTERNAL)` / `osal_mem_alloc(size, 0)`. `esp_rom_printf` → `osal_panic_print()` (the same sites as `supervisor.c`).
- [ ] **`loader.c` — `soc/soc.h`**: memory map constants (used by the internal range checks). Decision: (a) expose the ranges through a new `duneos_memmap_t` struct provided by arch.cmake (preferred), or (b) an arch-specific mini-header `hal_memmap.h`. To be settled at the start of the phase. Concerns only `loader.c` today.
- [ ] **Reword the `task.h:8-10` comment** "FreeRTOS abstraction" → "OSAL abstraction".

**Libc prerequisite — PicoLibc in `third_party/`**:

Up to this phase it is `esp_libc` (an ESP-IDF component) that provides PicoLibc and its syscall stubs. As soon as `pthread_osal.c` is implemented for the Linux simulator, there is no ESP-IDF left to provide the libc. That is the **mandatory trigger point** for bundling PicoLibc:

- [ ] **`third_party/picolibc`**: git submodule (the same strategy as cJSON/LittleFS). Compiled for the current target by the toolchain plugin.
- [ ] **`tools/dbt/toolchain/esp_idf.py`**: remove the implicit dependency on `esp_libc` — pass the PicoLibc include path and linker script explicitly through the plugin.
- [ ] **Multi-SDK coherence**: the pico-sdk provides newlib (not PicoLibc) — slightly different `stdio`/`errno` behaviours. Use the bundled PicoLibc on **every** target (decision recorded by this phase, validated by Phase 29).

---

### Phase 27 — VFS rework and network stabilisation

**Swapped with the former Phase 27 (OSAL).** Reason: see Phase 26. The native VFS needs the OSAL primitives (a mutex for the VFS global structures, a sem for the `poll`/`select` wait queues), so it cannot be written before.

Prepare the network stack before facing the WiFi decoupling. **API frozen by ADR 001 and 005 — -errno codes, documented path conventions.**

**Remaining hardware DHI migrations** (the only ones still on `driver/spi_master.h` after Phase 24a):

- [ ] **`vfs.c` SD init**: replace `driver/spi_master.h` + `driver/gpio.h` (the SD card's SPI bus init) with `hal_spi.h` + `hal_gpio.h` — the implementations already exist, migration only.
- [ ] **`display/st7789_hw.c/.h`**: replace `driver/spi_master.h` with `hal_spi.h` — `spi_device_handle_t` types encapsulated in the implementation, the public header purged of `esp_err.h`.

**Network architecture** — frozen by [ADR 013](docs/adr/013-network-architecture.md): POSIX sockets + vendored lwIP + a per-medium HAL (`hal_wifi.h` ≠ `hal_eth.h`) + lwIP's `struct netif` directly as the registry. No `duneos_netif_t` wrapper. No `esp_netif`.

**Vendoring lwIP**:

- [ ] **`third_party/lwip` git submodule**: stop using the lwIP shipped by ESP-IDF. Compiled for the current target by the toolchain plugin. Decision recorded by ADR 013; the same stack everywhere = the same behaviour, the same DNS resolver, the same API. A prerequisite for Phase 29 (the RP2040 has its own lwIP through the pico-sdk, to be substituted).
- [ ] **`tools/dbt/toolchain/esp_idf.py`**: remove the implicit dependency on the ESP-IDF `lwip` component; link the compiled `third_party/lwip` explicitly.

**Network HAL (split per medium, ADR 013)**:

- [ ] **`kernel/duneos_kernel/include/duneos/hal_wifi.h`**: pure C interface — `hal_wifi_init/scan/connect_sta/disconnect/start_ap/get_rssi/set_event_cb` + `hal_wifi_tx_packet` + an RX callback. No esp_wifi / cyw43 type.
- [x] **`kernel/duneos_kernel/include/duneos/hal_eth.h`**: MAC control — `hal_eth_init/tx/set_rx_cb/get_stats`. **Exists**, implemented in `arch/xtensa_esp32/hal/hal_eth.c` by the kincony-A16 board work — see the RMII bullet below, which already said so. Its backend is still `esp_netif` + esp_eth; **what remains in this phase is the rewrite onto vendored lwIP + `struct netif`, not the header.**
- [x] **`kernel/duneos_kernel/include/duneos/hal_phy.h`**: MDIO abstraction — `hal_phy_read/write/get_link`. **Exists**, implemented in `arch/xtensa_esp32/hal/hal_phy.c`, dispatching across LAN8720 / KSZ8081 / RTL8201 / IP101 at runtime (`kernel/duneos_kernel/idf_component.yml:20-35` gates the four PHY components on `target == esp32`). Same caveat as `hal_eth.h`: delivered, pre-Phase-27 backend.
- [ ] **`arch/xtensa_esp32s3/hal/hal_wifi.c`**: wraps esp_wifi + esp_event as the ESP-IDF backend.

**Link-layer drivers**:

- [ ] **`kernel/duneos_kernel/src/drivers/net/wifi_esp.c`**: rewrite the current `drv_wifi.c` — uses `hal_wifi.h` (no more direct `esp_wifi`), registers an lwIP `struct netif` with a `linkoutput` callback, bridges packet rx_cb → `netif->input()`.
- [ ] **`drv_raw80211.c`**: stays functionally identical (802.11 injection bypassing the IP stack). Migrated to `hal_wifi.h` only for the shared initialisation config bits.
- [ ] **Loopback `lo`**: created at kernel init through native lwIP. Useful for tests without hardware ([[ADR-012]]).
**Network configuration API** (replacing the Linux ioctl/netlink mess):

- [ ] **`duneos_netif_set_ip/set_dns/get_status/list`**: header `kernel/duneos_kernel/include/duneos/netif.h`. Consumed by apps + `apps/system/bin/ifconfig`.
- [ ] **`duneos_netif_wait_ip()`**: reimplemented on top of `struct netif` flags + `osal_sem`. Same public symbol.

**Native VFS**:

- [ ] **`vfs.h`**: migrate the 6 public `esp_err_t` signatures → `int` (-errno). Debt inherited from Phase 24.
- [ ] **Native `duneos_vfs`**: replace `esp_vfs.h` in `vfs.c`, `vfs_dev.c`, `vfs_tmp.c` — natively handles `poll()`/`select()` and sockets through wait queues built on `osal_sem`. **Interim delivered 2026-06-06** (Phase 24.12): `select()` on `/dev` fds through the `esp_vfs` bridge (`start_select`/`end_select` in `vfs_dev.c` + a per-driver `readable` callback). The native rewrite will replace that bridge; `poll()` on `/dev` is still to be done (esp_vfs has no `poll`).
- [ ] **VFS sockets**: route the BSD calls to lwIP through the new internal stack.
- [x] **Userspace GPIO IRQ**: `GPIOCHIP_SET_IRQ` **delivered 2026-06-06** (contest sprint) — arms a line for edge detection, `gpio_event_t` events read through `read()` on `/dev/gpiochip0`, `select()`-able. Linux gpio chardev line-events model ([ADR 019](docs/adr/019-linux-device-interface-semantics.md)). Did not need to wait for the native VFS.

**RMII Ethernet** (optional, depends on the availability of a target board):

- [x] **ESP32 MAC (not the S3)**: the **kincony-A16** reference board delivered (`feature/kincony-a16`). Arch `xtensa_esp32`, LAN8720 PHY, `arch/xtensa_esp32/hal/hal_eth.c` + `hal_phy.c`, kernel driver `/dev/eth0` (`drv_eth.c`), `duneos_eth_get_info` + `ifconfig` eth0. Hardware-validated: 100M FD link → DHCP → IP in ~3 s. **Pre-Phase-27 backend = `esp_netif` + esp_eth** (will be rewritten on vendored lwIP + `struct netif` here).
- [ ] **Gap: `duneos_netif_wait_ip()` does not cover Ethernet.** The current implementation (`drv_wifi.c`) only listens to the WiFi event group; an `IP_EVENT_ETH_GOT_IP` does not wake the waiters. An app blocking on "network up" through that API therefore does not see the eth (workaround: query `/dev/eth0` / `duneos_eth_get_info`). The clean fix is the `wait_ip` reimplementation on top of the `struct netif` flags above; in the interim, have `hal_eth.c`'s `IP_EVENT` handler signal the same event group.

---

### Phase 28 — Espressif RISC-V ports: ESP32-C6 + ESP32-P4

**Goal: validate that `arch/*/arch.cmake` really works on a second ISA.** The ESP32-C6 and ESP32-P4 are RISC-V but stay under ESP-IDF — same build system, new arch. If something breaks, it is a gap in the abstraction, not in the tooling.

**Unifying the `arch.cmake` guards** — foundations laid in Phase 24, what remains is the non-ESP-IDF use:

- [x] **`DUNEOS_ARCH` variable introduced**: the triple guard (`CONFIG_IDF_TARGET_ARCH_XTENSA` + `IDF_TARGET_ARCH` + `DUNEOS_ARCH`) is already in place in `arch/xtensa_esp32s3/arch.cmake`. The CLAUDE.md documentation describes the pattern to reproduce for new arches.
- [ ] **Check the guard outside ESP-IDF**: test that a non-ESP-IDF toolchain plugin (Phase 29) can indeed activate an arch by setting only `DUNEOS_ARCH=<value>` before the configure step.

**Prerequisite: modularise the loader AND the exception handler (debt inherited from Phase 24)** — blocking before adding a second arch, otherwise all the ISA-specific code gets duplicated:

- [ ] **`duneos_arch_ops_t` interface**: define in `kernel/duneos_loader/include/duneos/arch_ops.h` a struct `{ int (*apply_reloc)(void *base, const Elf32_Rela *r, ...); const char *arch_name; }` that the arch registers through `duneos_loader_register_arch_ops(&ops)`. loader.c becomes arch-agnostic.
- [ ] **Extract the Xtensa logic from `loader.c`** into `arch/xtensa_esp32s3/reloc/loader_reloc_xtensa.c` (the stub has existed since Phase 24 but was never filled in — around 150 lines to move: R_XTENSA_32, R_XTENSA_SLOT0_OP, R_XTENSA_ASM_EXPAND, R_XTENSA_DIFF8/16/32 + the `apply_slot0_op` helper).
- [ ] **`duneos_arch_exception_t` interface**: define in `kernel/duneos_kernel/include/duneos/arch_exception.h` a struct `{ void (*install_handler)(int core_id, void (*on_crash)(int slot_id, uint32_t cause, uint32_t pc, uint32_t sp)); }`. The supervisor registers its `on_crash` callback once; the arch takes care of installing the real ISA-specific handler that unpicks the XtExcFrame / `mcause` / Cortex-M Fault status.
- [ ] **Extract the Xtensa exception handler from `supervisor.c`** into `arch/xtensa_esp32s3/exception/exc_xtensa.c`: `xt_set_exception_handler`, `xt_exc_handler`, `XtExcFrame`, `EXCCAUSE_*`, `XCHAL_EXCCAUSE_NUM`, the `app_exception_handler` helper and its dispatch through PS.INTLEVEL. supervisor.c keeps only the generic `on_crash` callback, which talks about slots (not CPU frames).
- [ ] **Update `arch/xtensa_esp32s3/arch.cmake`**: add `loader_reloc_xtensa.c` + `exception/exc_xtensa.c` to `DUNEOS_KERNEL_SRCS`. Likewise for `arch/riscv32/arch.cmake` when it is filled in (`reloc/loader_reloc_riscv.c` + `exception/exc_riscv.c`).
- [ ] **Validation**: `dbt flash kernel --build-only` on the CardPuter passes with the reloc + exception code in the arch files, not in loader.c / supervisor.c. Crash test (Phase 20 `test_hardening`): the crashing app is still intercepted and the slot killed cleanly.

**ESP32-C6 (RISC-V, RV32IMC)** — the RISC-V arch is then trivial to add because loader.c has been neutralised:

- [ ] **`arch/riscv32/arch.cmake`**: fill it in — `DUNEOS_KERNEL_SRCS` (HAL + loader_reloc_riscv) + ESP-IDF RISC-V `DUNEOS_KERNEL_REQUIRES`. Guard: `DUNEOS_ARCH STREQUAL "riscv32"`.
- [ ] **`arch/riscv32/hal/hal_*.c`**: 6 ESP32-C6 HAL files through ESP-IDF. APIs often identical to Xtensa except the ADC (different unit/channel on the C6).
- [ ] **`arch/riscv32/reloc/loader_reloc_riscv.c`**: **extract**, do not implement — the RISC-V relocations already work. `apply_riscv_reloc()` lives in `kernel/duneos_loader/src/loader.c:735` and covers `R_RISCV_32`, `CALL`/`CALL_PLT`, `PCREL_HI20`/`PCREL_LO12_I`/`_S`, `HI20`/`LO12_I`/`_S`, with the full constant table at `kernel/duneos_loader/include/duneos/elf.h:89-108`, plus `NONE`/`RELAX`/`ALIGN` as no-ops.
  (`R_RISCV_BRANCH` and `R_RISCV_JAL` are named in `elf.h` but not in the switch — they are
  intra-section in ET_REL, so `ld -r` resolves them; if one ever reaches the loader it hits the
  `default:` reject, which is the correct outcome.) What this phase owes is the same move as the Xtensa one above: lift them out of `loader.c` into `arch/riscv32/reloc/loader_reloc_riscv.c` behind `duneos_arch_ops_t.apply_reloc`, registered through `duneos_loader_register_arch_ops`. **`arch/riscv32/` today holds only a 24-line comment stub at that path — no `arch.cmake`, no `hal/`** (LEG-24).
- [ ] **`tools/dbt/toolchain/esp_idf.py`**: handle `arch: riscv32` — `riscv32-esp-elf-gcc` compiler prefix, CFLAGS (`-march=rv32imc_zicsr_zifencei`, `-mabi=ilp32`).
- [ ] **`boards/esp32c6-devkitc/board.yaml`** + `bspgen`: the RISC-V reference board.

**ESP32-P4 (RISC-V RV32IMA, dual-core HP + LP)** — added without a new architecture:

- [ ] **`boards/esp32p4-devkitm/board.yaml`**: the board already exists. Validate that `arch: riscv32` + the same toolchain plugin cover the ESP32-P4. Adjust the HAL if ESP-IDF exposes P4 differences (LP core, new peripherals).
- [ ] **Smoke test**: a RISC-V `hello_world.dap` on the C6 and the P4. The loader must reject an Xtensa `.dap` with a clear message.

> **What this phase proves**: an Espressif RISC-V chip = only a new `board.yaml`. `arch/riscv32/` covers the C3, C6, H2 and P4 — no duplication.

---

### Phase 28.5 — ESP-IDF decoupling of the entry point, the build root, and dbt

**Why this phase:** Phase 21 moved `build_kernel`/`flash_kernel`/`monitor` into the toolchain plugin, but five residual ESP-IDF fronts remain on the kernel and dbt side. None of them blocks Phase 28 (which stays fully ESP-IDF, just another ISA). All of them block Phase 29 (the first port outside ESP-IDF). Audit done 2026-05-19.

**Front 1 — Entry point split (`main/` → `kernel/` + `arch/`):**

- [ ] **Extract the SDK-agnostic logic from `main/main.c`** into `kernel/duneos_kernel/src/duneos_main.c`, exposing `void duneos_main(void)` which does: `vfs_init` → `loader_init` → `supervisor_init` → `launch_from_init_yaml` → `wait_all` → `kernel_idle`. No `app_main`, no USB JTAG `console_init`.
- [ ] **`arch/xtensa_esp32s3/entry/entry_esp_idf.c`**: implements `void app_main(void)`, which calls `duneos_main()`. Includes the current USB JTAG `console_init()` (`driver/usb_serial_jtag.h`) — under `#ifndef CONFIG_ESP_CONSOLE_USB_CDC`.
- [ ] **`arch/<arch>/entry/entry_<sdk>.c`** for future ports: pico_sdk → `int main(void) { duneos_main(); return 0; }`; stm32 → likewise.
- [ ] **Remove `main/`**: its `main.c` is empty after the extraction, and its `CMakeLists.txt` (`idf_component_register` + REQUIRES `esp_driver_usb_serial_jtag`) moves to `arch/xtensa_esp32s3/entry/`.

**Front 2 — SDK-agnostic build root:**

- [ ] **Root `CMakeLists.txt`**: today it directly calls `include($ENV{IDF_PATH}/tools/cmake/project.cmake)`. It must dispatch on `board.yaml`'s `sdk:` field. Pattern: read `.duneos_board` → read `boards/<board>/board.yaml` → `include(tools/dbt/toolchain/<sdk>/setup.cmake)`, which takes over the SDK-specific orchestration (project.cmake for ESP-IDF, pico_sdk_init for the pico-sdk, etc.).
- [ ] **Root `sdkconfig.defaults`**: stays, but documented at the top as "ESP-IDF Kconfig fragment — only consumed when sdk: esp-idf in board.yaml". Other SDKs get their equivalent in `tools/dbt/toolchain/<sdk>/defaults.{cmake,h,...}` depending on what they accept.

**Front 3 — dbt audit findings:**

- [ ] **`tools/dbt/setup.py`** (61 ESP-IDF references): it is an IDF install wizard (clone esp-idf, install.sh, export.sh, find_idf_root, build_idf_env, etc.). All the IDF-specific logic moves into `plugin.setup_wizard()`, `plugin.find_toolchain_root()`, `plugin.build_env()` methods. `setup.py` becomes a dispatcher calling the active plugin. The generic logic (`_BOARD_FILE`, `_PORT_FILE`, board/port pickers) stays.
- [ ] **`tools/dbt/tui.py`** (51 references): hardcoded labels `"ESP-IDF v6.0.x"`, `"IDF v6"`, `"echo ~/esp/esp-idf > .duneos_idf"`. Replace with `f"{plugin.SDK} {plugin.version()}"`, `f"echo … > .duneos_{plugin.SDK_SLUG}"`. Every `find_idf_root()` → `plugin.find_toolchain_root()`. The private `_idf_env()` / `_idf_cmd()` functions become `_sdk_env()` / `_sdk_cmd()`, delegating to the plugin.
- [ ] **`tools/dbt/flashimg.py`** (12 references): `_find_esptool()` + a direct esptool call to flash the sysbin partition. On the RP2040 it would be `picotool`; the pattern is identical (binary image → partition offset → flash tool). Delegate: `plugin.flash_partition(image_path, partition_offset, partition_name, port)`. The esp-idf plugin implements it with esptool, the pico-sdk plugin with picotool. `flashimg.py` stays generic: builds the LittleFS image, reads the offset from `partitions.csv`, calls `plugin.flash_partition()`.
- [ ] **`tools/dbt/kernel.py`** (4 references): clean usage through `plugin.find_toolchain_root()` but writes `.duneos_idf` hardcoded (`_IDF_FILE.write_text(...)`). Replace with `plugin.write_toolchain_path_marker(root)`, which knows which per-SDK file to use (`.duneos_idf` for ESP-IDF, `.duneos_pico_sdk` for the pico-sdk).
- [ ] **`tools/dbt/cli.py`** (2 references): just help texts mentioning `idf.py` and `idf_target.txt`. Minor; to be reworded in SDK-agnostic terms.

**Front 4 — Extended plugin interface**:

What the toolchain plugin must expose after this phase (on top of what already exists since Phase 21):

```python
# New methods required by fronts 1-3
SDK_SLUG: str                                  # "esp_idf", "pico_sdk" — for the .duneos_<slug> files
def version() -> str: ...                      # "v6.0.1" — for the TUI display
def setup_wizard(console) -> int: ...          # partial in Phase 21; generalise
def build_env() -> dict[str, str]: ...         # env vars for calling the build/flash subprocess
def write_toolchain_path_marker(root): ...     # writes .duneos_<slug>
def flash_partition(image, offset, name, port) -> int: ...  # what flashimg.py delegates
def setup_cmake() -> Path: ...                 # the file the root CMakeLists.txt include()s
```

**Front 5 — Smoke test**:

- [ ] **`dbt flash kernel --build-only` stays green** after fronts 1-3 on the 5 current boards (CardPuter, T-Embed, DevKitC, ESP32-C3, ESP32-P4). No regression.
- [ ] **The Phase 29 RP2040 rebuild can start** without touching the kernel core code or dbt — only adding `tools/dbt/toolchain/pico_sdk.py` + `arch/arm_cortex_m/`.

> **Estimated cost**: one week of focused work. Much mechanical refactoring (sed + grep), little design. The real complexity is in the root CMakeLists.txt dispatch (front 2), which mixes two incompatible build systems.

---

### Phase 29 — First non-ESP-IDF port: ARM Cortex-M (RP2040, Pico W)

**Goal: validate that the kernel builds without ESP-IDF.** This is the real portability test of the build system. Requires Phase 26 (OSAL + PicoLibc in `third_party/`) and Phase 27 (native VFS if the target app uses sockets; otherwise the ESP-IDF socket layer is absent and that is fine for a first hello_world).

**ARM Cortex-M architecture decision** — to be taken before coding:

The M0+ (RP2040), M4F (SAMD51, nRF52) and M7 (STM32H750) share the **same Thumb-2 ELF relocations**. The difference is the SoC HAL. Chosen structure:

```
arch/arm_cortex_m/
    arch.cmake                   # guard: DUNEOS_ARCH matches "arm_cortex_m*"
    reloc/loader_reloc_arm.c     # single file for all ARM Thumb-2
    hal/
        rp2040/                  # HAL through the pico-sdk
        stm32h7/                 # HAL through STM32CubeHAL (Phase 30+)
        nrf52/                   # HAL through the nRF5 SDK (Phase 30+)
        samd51/                  # HAL through CMSIS/ASF4 (Phase 30+)
```

`arch.cmake` includes `hal/${DUNEOS_BOARD_SOC}/hal_sources.cmake` to select the right HAL subdirectory.

- [ ] **`duneos_kernel/CMakeLists.txt` — standalone mode**: guard `if(DEFINED IDF_TARGET)` → `idf_component_register()` (existing). Otherwise: `add_library(duneos_kernel STATIC ...)` + `target_sources()` + `target_include_directories()`. The already-computed `DUNEOS_KERNEL_SRCS` stay valid in both modes.
- [ ] **`tools/dbt/toolchain/pico_sdk.py`**: plugin — `SDK = "pico-sdk"`, `ARCH = "arm_cortex_m"`, `SOC = "rp2040"`. `build_kernel()` through standalone CMake, `flash_kernel()` through `picotool`, `DUNEOS_ARCH = arm_cortex_m`, `DUNEOS_BOARD_SOC = rp2040`.
- [ ] **`arch/arm_cortex_m/arch.cmake`**: guard `DUNEOS_ARCH STREQUAL "arm_cortex_m"`. Includes `loader_reloc_arm.c`. Includes `hal/${DUNEOS_BOARD_SOC}/hal_sources.cmake`.
- [ ] **`arch/arm_cortex_m/reloc/loader_reloc_arm.c`**: ARM Thumb-2 ELF relocations (`R_ARM_THM_CALL`, `R_ARM_ABS32`, `R_ARM_THM_MOVW_ABS_NC`, `R_ARM_THM_MOVT_ABS`). Shared by M0+/M4/M7.
- [ ] **`arch/arm_cortex_m/hal/rp2040/hal_*.c`**: 6 RP2040 HAL files through the pico-sdk.
- [ ] **`boards/rp2040-pico/board.yaml`**: `arch: arm_cortex_m`, `sdk: pico-sdk`, `soc: rp2040`.
- [ ] **`boards/rp2040-pico-w/board.yaml`**: identical + a WiFi section (CYW43439 through pico_cyw43_arch in userspace). WiFi = the app opens `/dev/spi-1` and drives the CYW43 in userspace, not in the kernel.
- [ ] **Bundled PicoLibc**: check that the PicoLibc from `third_party/` compiles for ARM Cortex-M0+ and provides coherent syscall stubs.
- [ ] **`dbt sim`**: compile the kernel for Linux x86_64 through `pthread_osal.c` + x86 PicoLibc. Smoke test: an x86 `hello_world.dap` in the simulator.

> **What this phase proves**: DuneOS is a portable OS. The following phases (STM32H750, nRF52-DK, Feather M4) only add `hal/<soc>/` + `board.yaml` — the pattern is established.

**Future ARM targets (post Phase 29, same pattern)**:
- *STM32H750*: `stm32_hal.py` plugin, `arch/arm_cortex_m/hal/stm32h7/`, STM32CubeH7 HAL, `arm-none-eabi-gcc -mcpu=cortex-m7`.
- *nRF52-DK (nRF52840)*: `nrf5_sdk.py` plugin, `arch/arm_cortex_m/hal/nrf52/`. ⚠️ The nRF52832 has 64 KB of SRAM — too tight for the dynamic ELF loader; the nRF52840 (256 KB) is the target.
- *Feather M4 (SAMD51)*: no dominant SDK with a clean CMake — ASF4 or bare CMSIS. The hardest one: it needs a `samd51_cmsis.py` plugin with manual startup/linker scripts.

---

### Phase 30 — Audio and multimedia

- [ ] **`/sd/board.info`** extended with the audio capabilities (I2S presence, codec).
- [ ] **`/dev/pcm`**: an ALSA-lite driver.
- [ ] **Mixing daemon**: a system service to play several sounds simultaneously.
- [ ] **SDK**: `libwav.a`, `libsynth.a`.

---

### Phase 31 — Power and optimisation

- [ ] **Wake locks**: an application API (`PM_LOCK_CPU`, `PM_LOCK_DISPLAY`). The kernel drops into deep sleep when every lock has been released.
- [ ] **Input ISR**: rewrite the keyboard and encoder scanning to use hardware interrupts instead of polling.

---

### Phase 32 — Security and ecosystem

- [ ] **`/dev/crypto`**: expose the AES/SHA/TRNG accelerators through syscalls.
- [ ] **Ed25519 signatures**: asymmetric verification of `.dap` files by the loader before execution.
- [ ] **App store CLI**: `dbt system install author/repo` — download community apps and fold them into a `system.yaml`.

---

### Phase 33 — Future research

- [ ] **Shared libraries (`.dsl`)**: feasibility study of shared libraries (PIC/GOT) if the need for pooled RAM becomes critical against the simplicity of static linking.
- [ ] **Multi-core support (SMP)**: exploiting the second core on the ESP32-S3 and P4.
