# DuneOS Roadmap V2

## 1. The Manifesto

NuttX and Zephyr offer incredible POSIX conformance, but at the price of a brutal learning curve (Kconfig hell, complex Device Trees). DuneOS aims for the Sweet Spot: the power of a POSIX RTOS with the smooth developer experience (DX) of a console like the Playdate or the Flipper Zero.

The user must never have to touch CMake or the kernel internals. A single YAML file and some C code must be enough to compile, link and dynamically deploy an application on any supported hardware.

---

## 2. POC legacy (Phases 1 to 24 validated, residual debt addressed by 26-27)

DuneOS is no longer just an idea — the foundations are proven on ESP32-S3 (M5Stack CardPuter, LilyGo T-Embed CC1101, ESP32-S3-DevKitC):

- **Execution and ABI**: working Xtensa ELF loader (ET_REL) (load/run/unload cycle, complete Xtensa relocations). ABI v3: typed `duneos_api_t` dispatch table injected into `__duneos_api_ptr` by the loader — O(1) resolution.
- **VFS and devices**: `/flash` (LittleFS, sysbin), `/sd` (optional FatFS), `/tmp` (tmpfs), `/dev/uart0`, `/dev/klog`, `/dev/gpiochip0`, `/dev/i2c-0`, `/dev/spi-1`, `/dev/disp0`, `/dev/fb0` (PSRAM boards), `/dev/input/event0`, `/dev/raw80211`, `/dev/ttyUSB0`, `/dev/battery0`.
- **Display**: `/dev/disp0` streaming driver (all boards) + `/dev/fb0` PSRAM back-buffer (T-Embed). `libgfx` (`sdk/display/gfx.c`) selects the backend dynamically at runtime (`/dev/fb0` if available, `/dev/disp0` fallback).
- **USB**: composite TinyUSB MSC + CDC on OTG boards; `/dev/ttyUSB0` exposed through `drv_usb_cdc.c`; klog redirected to CDC at runtime.
- **Userspace**: `libdune.a` (6 sources: ptr, fs, mem, thread, time, sys) — PicoLibc + ABI v3 dispatch. Lets apps write standard POSIX C.
- **Ecosystem**: modular shell (`apps/system/usb_shell/` + `apps/system/shell_core/`, builtins + 14 commands in `apps/system/bin/`), tooling (`dbt` package + Textual TUI + toolchain plugins), init system (`boards/<board>/init.yaml` flashed into sysbin + optional `/sd/init.yaml`), restart policies.
- **Networking**: WiFi daemon, `duneos_netif_wait_ip()`, raw 802.11 frame injection (`/dev/raw80211`), complete BSD socket exports, `apps/system/bin/ifconfig` + `ping`.
- **Hardening (Phase 20 partial)**: per-app heap (heap_caps_malloc), per-slot Task WDT, per-app Xtensa exception handler (clean kill without a kernel reboot), syscall pointer validation (`check_user_ptr` + `check_app_writable_ptr`). Remaining: stack canary, userspace TLSF.
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

## 4. Detailed roadmap

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

- [x] **`/flash/board.info`** (+ `/sd/board.info`): YAML file written by the kernel at boot from `board_config.h` (fields: `board`, `display`, `width`, `height`, `fb`).
- [x] **`<duneos/gfx.h>`**: public API — `gfx_open/close/fill/pixel/text/flush/get_info`. RGB565 pixel format, host byte order.
- [x] **`sdk/display/gfx.c`**: a single implementation, **runtime** backend selection in `gfx_open()` — Tier B (`/dev/fb0`, kernel PSRAM framebuffer) if available, otherwise Tier A (`/dev/disp0`, SPI streaming). Drawing goes to a userspace back-buffer; `gfx_flush()` pushes it in one shot.
- [x] **Demo app** `gfx_demo.dap` — draws shapes + text, runs on every board with a display without recompiling the source.

> **Note vs the original design**: the roadmap initially mentioned two separate backend files (`gfx_st7789.c` + `gfx_fb.c`) selected at build time through `.duneos_board`. The implementation chosen is simpler: a single library that sniffs `/dev/fb0` at runtime. Same result (source-level portability), less dbt code.

---

### Phase 19 — Flash storage (boot without SD) ✅

DuneOS must boot and be usable even with no SD card inserted.

- [x] **LittleFS `sysbin` partition** in `partitions.csv` (~1 MB); mounted on `/flash` in `vfs.c`.
- [x] **Embedding the vital apps** (`shell.dap`, `apps/system/bin/` commands) as firmware blobs through `COMPONENT_EMBED_FILES`.
- [x] **First-boot provisioning**: the kernel copies the missing blobs to `/flash/bin/` on first start.
- [x] **Loader cascade**: search `/flash/bin/` → `/sd/bin/` → `/sd/apps/`.
- [x] **BSP YAML**: `has_sd: false` field; `vfs.c` skips the SD mount and reads `init.yaml` from `/flash`.
- [x] **`dbt.py flashimg`**: produces a LittleFS image directly flashable through `esptool` (port from `.duneos_port` / `--port` / `DUNEOS_PORT`).
- [x] **`bspgen.py`**: generates a per-board `partitions.csv` from `flash_size_mb`; `sdkconfig.board` replaces the handwritten `sdkconfig.defaults`.
- [x] **Init dedup by app name**: `init.c` deduplicates services by name (basename without `.dap`) — avoids double-launching when the same service appears in both `/flash/init.yaml` and `/sd/init.yaml`. The flash entry wins (loaded first).
- [x] **`boards/<board>/init.yaml`**: a per-board file versioned in the repo, controlled by the user. `dbt flashimg` copies it as-is (no more hardcoded `_BOOT_SERVICES` list). Editable through the TUI (`i` → Init Config): app selection + restart policy (`always`/`on-failure`/`no`).

---

### Phase 20 — Memory hardening + technical debt 🟡 PARTIAL

Guarantee that an application cannot crash the system. Purge the accumulated technical debt.

- [x] **Per-app exception handler**: intercept the crash → log to `/dev/klog` → clean unload without a kernel reboot. (`supervisor.c` → `app_exception_handler`).
- [x] **Task WDT**: the supervisor feeds the WDT for the app; kicks the app on timeout.
- [x] **Per-app heap**: dedicated DRAM pool per app through `heap_caps_malloc` (contiguous Code+Data+Heap block). Monolithic TLSF block deferred.
- [x] **Syscall validation**: `duneos_supervisor_check_user_ptr` (permissive, source buffers) + `duneos_supervisor_check_app_writable_ptr` (strict, target buffers). Used by `api.c` (read/write) and `symbols.c`. Rejects the peripheral space and IRAM.
- [ ] **Stack canary** per application task.
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
    - `boards/lilygo-t-embed-cc1101/init.yaml` adds `/flash/bin/battery_daemon.dap restart: always`
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

- [x] **#5 — uinput + userspace input drivers** (validated on device 2026-05-20)
  - **Status 2026-05-20**: implementation done; CardPuter + T-Embed kernels build OK with the daemons enabled; 29/29 apps OK on both boards. **Keyboard tested on the CardPuter device — kb_iomatrix.dap injects the events, the shell and g_shell still receive keystrokes.**
  - **Done**:
    - Kernel: `kb_iomatrix.c` + `btn_gpio.c` removed. `drv_input.c` gains the `INPUT_INJECT_EVENT` ioctl (single handler for userspace injection; internal `drv_input_push_event()` remains for the encoder ISR path). Kconfig purged of `DUNEOS_DRV_INPUT_IOMATRIX/BTNGPIO` (the encoder stays).
    - Userspace: `apps/system/kb_iomatrix/` + `apps/system/btn_gpio/` (10 ms polling daemons, opening `/dev/gpiochip0` + `/dev/input/event0`, `#ifdef` guard to build as a stub on boards without the feature)
    - `boardgen.py`: a new `_input_block` emits `DUNEOS_KB_*` + `DUNEOS_BTN_GPIO_*` + `DUNEOS_INPUT_DEV` into `<duneos/board.h>` (per-app); `duneos-bspgen.py` no longer emits them into `board_config.h` (kernel) since the kernel no longer consumes them
    - CardPuter `init.yaml`: adds `/flash/bin/kb_iomatrix.dap restart: always`. T-Embed: adds `/flash/bin/btn_gpio.dap`.
  - **Left to close**: validate on device (CardPuter) that `g_shell` still receives its events after boot. Strict process — `[ ]` pending hardware observation.
  - **Architecture note**: scan latency increases (each ioctl is a function call, but 8 row selects × 7 col reads = ~56 ioctls per scan). On the CardPuter without an MMU and with the API dispatch table, this is µs-level — it should stay << 1 ms per scan. To be measured if fast typing feels less fluid.

> No item is marked `[x]` until **the kernel no longer holds the obsolete code** + **the migrated apps pass the tests**. No "MVP/PARTIAL" mid-implementation — every item is either `[ ]` or `[x]`, never in between. This discipline enacts the **100 % closure process** established 2026-05-20.

---

### Phase 24.5 — Design decisions / ADR ✅

11 short ADRs in [`docs/adr/`](docs/adr/) (see the [README](docs/adr/README.md) for the index). Michael Nygard format, 1 page maximum each, status `Accepted · 2026-05-19`. All the choices consumed by phase 24 (debt) and 25-29.

- [x] **ADR 000** — ADR process
- [x] **ADR 001** — `int`/-errno error model (consumed by 26-27)
- [x] **ADR 002** — OSAL API, static-storage handles only (consumed by 26)
- [x] **ADR 003** — memory caps, silent fallback to DRAM (consumed by 26)
- [x] **ADR 004** — task priorities, 5 symbolic levels (consumed by 26)
- [x] **ADR 005** — path conventions, `/flash` mandatory (consumed by 25, kernel boot)
- [x] **ADR 006** — manifest extensibility, unknown fields ignored (consumed by 25, loader)
- [x] **ADR 007** — multi-arch smoke test (consumed by 26-28, prerequisite for 29)
- [x] **ADR 008** — memory fragmentation strategy (consumed by 20 TLSF, kernel review policy)
- [x] **ADR 009** — kernel/userspace boundary for drivers (consumed by the Phase 24 debt, every new driver PR)
- [x] **ADR 010** — architecture-specific accelerators (consumed by the Phase 24 debt hal_encoder, Phase 29 PIO)
- [x] **ADR 011** — threat model: permissions are advisory (consumed by Phase 32 signing, README warning, future security claims)
- [x] **ADR 012** — test strategy: host-side first (consumed by Phase 26+, prerequisite of the OSAL refactor)

---

### Phase 24.7 — Safe boot & recovery (not finished — see the hold-key item)

**Why here (before Phase 25):** an `init.yaml` that launches a `restart: always` service which crashes at startup puts the board into a restart loop. On the CardPuter, with no dedicated boot button and no accessible UART, the only way out is `dbt flash sysbin` — which assumes USB MSC mounts BEFORE the crash. That is not guaranteed. Phase 25 (`dbt system deploy`) will automate deployments that can introduce exactly this bug → we must be able to recover before then.

**Minimal scope**:

- [x] **Circuit breaker in the supervisor**: by default 3 crashes in 30 s → `breaker_tripped`, restart skipped, klog "circuit breaker tripped, restart disabled". `breaker_max_crashes` and `breaker_window_ms` fields in the slot, defaults used when 0. A per-service configurable `init.yaml` field is deferred (the defaults are enough for development).
- [ ] **Hold-key-at-boot fallback**: `board.yaml` gains an optional `recovery_pin: { gpio: 0, level: low }` field. Requires hardware testing — moved to a later session when a board is connected.
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
- [x] **`libst7789.c` migration**: delivered within the Phase 24 debt #6+#2 (commit `109d53c`). Reads `DUNEOS_DISPLAY_DEV_INDEX` + device pins from `<duneos/board.h>` — opens `/dev/spi-N` according to the board, no more `/flash/board.info` parsing.
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

### Phase 24.13 — Reducing the app ELF section count (ADR 022) 🟡 STOPGAP DELIVERED

**Why**: apps are ET_REL files produced by `ld -r` with `-ffunction-sections`/`-fdata-sections`, hence one section per function/object (+ its `.rela` twin). The relocatable link GCs nothing, and the loader maps **every** `SHF_ALLOC` section (no GC there either). Two costs: (1) the **section count** explodes — the modular `i2cscope` reached 584 and exceeded `MAX_SECTIONS=512` on 2026-06-06; (2) the **dead code of the libs** (uncalled `ui_*`/`gfx_*` functions) is loaded into the 64 KB IRAM exec pool — the scarcest resource on the CardPuter without PSRAM. `-ffunction-sections` only makes sense with `--gc-sections`, which the app link does not do → today the flag only inflates the count without returning anything. Decision recorded in [ADR 022](docs/adr/022-app-elf-section-optimization.md).

**Status**: 🟡 stopgap delivered (cap raised), the underlying optimisation to be measured then decided.

- [x] **Stopgap 2026-06-06**: `MAX_SECTIONS` 512→1024 in `loader.c` (the `section_bases[]` array is `calloc`ed per app and freed on unload — ~4 B/entry, transient). Unblocks multi-file apps; does not address the cause.
- [ ] **Measure**: `size`/`readelf` on a representative app — what fraction of the exec pool is dead library code?
- [ ] **Choose the lever** (ADR 022): (A) drop `-ffunction-sections`/`-fdata-sections` from the app CFLAGS (free, kills the inflation, loses the granularity of a future GC); (B) real function-level GC (partial link with an entry root — pays the most, riskier); (C) merge the sections through a linker script (`*(.text .text.*)` — fixes the count, keeps all the code).
- [ ] **Implement** in `tools/dbt/constants.py` (`CFLAGS_COMMON`/`LDFLAGS`) and/or an app linker script — not in the apps' code.

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

> ### ⚡ Contest sprint 2026 — M5Stack Global Innovation Contest
>
> **Full plan: [`docs/contest-2026.md`](docs/contest-2026.md).**
>
> If we enter the [M5Stack 2026 contest](https://m5stack.com/global-innovation-contest-2026) (deadline 7 August 2026), the roadmap **freezes after Phase 25 (minimum viable)** until 31 August. Phases 26-29 resume on 1 September. The sprint absorbs Phases 24.7 + 25-minimal and delivers a killer-app demo: `i2cscope` (✅) + a **graphical file explorer** + a `lua` REPL + `snake` + `tetris` + a graphical launcher with **per-app icons in the freedesktop style** ([ADR 023](docs/adr/023-app-icon-assets.md) — `icon:` = a name, the `.dr` lives in `/flash/share/icons/`, not in the `.dap`). Sprint detail: **Phase 25.6**.
>
> **Non-blocking for the main roadmap** if we do not enter — in that case, Phase 25 starts directly after Phase 24.7.

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
  - `dbt system size`: reports kernel + sysbin + SD with `[████░░] X KB / Y KB (pp%)` bars. Parses `boards/<board>/partitions.csv` for `factory` (kernel) and `sysbin` (flash). Lists the top-5 apps by size for /flash and /sd. Non-zero exit when an overflow is detected.
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
  - Backlog: `libimage_png` (a full PNG decoder when the need arises — picopng or the ESP-IDF jpeg_decoder), writable state `/flash/var/<app>/state.yaml`.
- [x] **25.4 — Branding + sequenced boot** (shipped 2026-05-22)
  - Kernel: an `after:` field in `init.yaml` (`duneos_service_desc_t.after`), an exit observer in `supervisor.c` (`duneos_supervisor_set_exit_observer`), `duneos_init_run()` orchestrating immediate vs deferred launch with a pending-queue mutex. Refuses dependencies on `restart: always` services (warns but launches anyway). `main.c` delegates to `duneos_init_run`.
  - `apps/user/splash`: a one-shot libgfx STREAM mode — desert gradient + dune silhouette + centred "DuneOS" wordmark, ~1.5 s then exit. The CardPuter `init.yaml` launches `splash` then defers `kb_iomatrix` through `after: splash` (a demo).
  - `duneos.yaml`: the `icon:` field is recognised (string ≤ 64 chars), with parse validation and a warning on unknown keys (typo guard). Embedded as-is in the JSON manifest; no ABI bump (consumed by the future launcher).
  - `dbt` TUI: a `SplashScreen` with an ASCII "DuneOS" wordmark + desert tagline, 1 s at startup, dismissable by any key. Can be suppressed through `DUNEOS_TUI_NO_SPLASH=1` (CI).

---

### Phase 25.6 — Contest demo apps + launcher icons 🟡 IN PROGRESS

**Why**: the contest demo needs a visual identity (launcher icons) and apps showing that DuneOS is a real OS. `i2cscope` is delivered (✅, 2026-06-06 — scan/xfer/sniff/scenarios, `/dev/logic0`, libsmbus). What remains is the identity + the remaining apps.

**Recommended order** (from the strongest lever to the weakest): **icons → file explorer → games**. Icons are a multiplier (every app added afterwards inherits them, and the launcher is the demo's front door) and they freeze an architectural decision; the explorer is the best "real OS" showcase (VFS, /flash + /sd, side-loading); the games are the fun cherry on top, the weakest lever.

- [x] **i2cscope** — the I²C Swiss army knife (scan / xfer / Pulseable VCD sniff / SD scenarios). Modular structure (1 file per screen) = the reference layout for multi-screen apps.
- [ ] **Per-app icons (ADR 023, freedesktop model)** — standard size **32×32 RGB565**:
  - [x] `dbt build`: build-time conversion of `icon.png` → `build/icon.dr` (`build_app_icon`, non-fatal if Pillow is absent). The developer drops in a PNG, that's all. (2026-06-06)
  - [ ] `dbt`: an icon install step at image build — copies `build/icon.dr` (or a hand-authored `apps/<app>/icon.dr`) to `/flash/share/icons/<name>.dr`; `dbt deploy` copies the `.dr` next to the `.dap` on the SD card.
  - [ ] launcher: name→file resolution through a search path (SD neighbour → `/sd/share/icons` → `/flash/share/icons` → generic fallback), `duneos_image_load_dr` + blit, placeholder if absent. Target UI: a **coverflow carousel** (32×32 centre icon, shrunken neighbours on either side).
  - [ ] a generic fallback set shipped in the image (`application.dr`, `folder.dr`, …) — freedesktop's `hicolor`.
- [ ] **Responsive views (ADR 024)** — the demo runs on the CardPuter **and** the T-Embed without changing the apps. The layout derives from `ui_size()` / `board.info` (`width`/`height`), never from hardcoded pixels. Launcher: icon size derived (48 px focus, scaled previews). ✅; audit/fix of the rest (the `i2cscope` value column at `x=150`, review of `g_shell`/`gfx_demo`/`splash`). ⏳
- [ ] **Graphical file explorer**: tree navigation of `/flash` + `/sd` (`opendir`/`readdir`/`stat`), libui `list` + `textview`, hexview for binaries / textview for text, `.dap` launching (loader/launcher tie-in). Reuses the i2cscope modular pattern.
- [ ] **Games**: `snake` + `tetris` (libgfx STREAM + input), `lua` REPL. The weakest architectural lever — demo fun, doable at any time.

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
- [ ] **`kernel/duneos_kernel/include/duneos/hal_eth.h`**: MAC control — `hal_eth_init/tx/set_rx_cb/get_stats`.
- [ ] **`kernel/duneos_kernel/include/duneos/hal_phy.h`**: MDIO abstraction — `hal_phy_read/write/get_link`. Allows support for several PHYs (LAN8720, KSZ8081, RTL8201, …) with a mini-file per chip under `arch/<arch>/hal/phy/`.
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
- [ ] **`arch/riscv32/reloc/loader_reloc_riscv.c`**: RISC-V ELF relocations (`R_RISCV_32`, `R_RISCV_HI20`/`LO12`, `R_RISCV_CALL`, `R_RISCV_BRANCH`, `R_RISCV_JAL`). Implements `duneos_arch_ops_t.apply_reloc`. Registered through `duneos_loader_register_arch_ops` at startup.
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
