# SPEC — "radar" app (HLK-LD2450 sensor)

Status: APPROVED

## Context

The user bought an HLK-LD2450 mmWave sensor (24 GHz radar, UART 256000 baud 8N1) and wants a DuneOS `.dap` "radar" app for the M5Stack CardPuter: real-time visualisation of the moving targets detected (up to 3), with the maximum of information the sensor provides — X/Y position (mm), hence derivable distance and angle, and radial speed (cm/s) — in a "military radar scope" GUI (PPI: range arcs, animated sweep, blips).

**Sensor protocol (HLK-LD2450 datasheet reference).** Periodic report frame (~10 Hz, ~30 bytes): header `AA FF 03 00`, 3 target blocks of 8 bytes (X int16 mm, Y int16 mm, speed int16 cm/s, distance resolution uint16 — the specific sign encoding is to be checked against the datasheet by the Builder), tail `55 CC`. The sensor also accepts configuration commands; only the "multi-target tracking" command is in scope (PO decision Q6).

**Product Owner decisions (2026-08-09):**

| # | Question | Decision |
|---|---|---|
| Q1 | Wiring | Grove port (GPIO1/GPIO2) reassigned to the sensor UART at 256000 baud. The loss of `/dev/i2c-0` (i2cscope) on this board is **accepted** for as long as the radar is wired. |
| Q2 | UART variant | **B**: new `uart: id 1` entry in `board.yaml` + generalisation of `drv_uart.c` → dedicated `/dev/uart1`, without `use_as_console`. |
| Q3 | Sweep | **Yes**: rotating line + blip persistence, bounded to the scope area. |
| Q4 | Range | Keyboard zoom **2/4/6 m**. |
| Q5 | Demo mode | **Yes**: simulated targets / replayed reference frame, usable without the sensor. |
| Q6 | Sensor mode | The app **sends the multi-target tracking command** at startup. |
| Q7 | Units | Distance in **m (1 decimal)**, speed in **km/h**, angle in **signed degrees** relative to the sensor axis. |
| Q8 | Parser | **`sdk/sensor/libld2450.c`** (reusable SDK library, `libbq27220.c` pattern). |

**Current state of the code concerned:**

- **Kernel UART**: `kernel/duneos_kernel/src/drivers/drv_uart.c` exposes only `/dev/uart0`, hardwired to `DUNEOS_UART0_TX_PIN/RX_PIN/BAUD` from `board_config.h`, with `use_as_console = true`. No `ioctl` or `readable` callback (`dev_driver.h`): `select()` on that fd considers it "always readable". The HAL read has a 100 ms timeout and returns 0 bytes when RX is idle (`arch/xtensa_esp32s3/hal/hal_uart.c`).
- **CardPuter pinout** (`boards/m5stack-cardputer/board.yaml`): UART0 declared on GPIO43/44 — pins physically inaccessible (Hard-Won Lesson). Grove HY2.0 port = GPIO1/GPIO2, currently I2C0.
- **bspgen** (`tools/duneos-bspgen.py`, l.190-201) already emits `DUNEOS_UART<id>_*` for several `uart:` entries in the YAML; only `drv_uart.c` consumes entry 0 alone.
- **App display/UI**: `sdk/display/gfx.h` (libgfx, `GFX_MODE_STREAM` mode without back-buffer + `gfx_canvas_new()` for anti-flicker compositing), `sdk/ui/ui.h` (libui), `sdk/game/game.h` (`game_wait()`: paced `select()` loop on `/dev/input/event0`). Model apps: `apps/user/snake`, `apps/user/waves`, `apps/user/i2cscope`.
- **Manifest/dispatch**: `duneos.yaml` with `heap_size > 0` ⇒ spawned mode (dedicated task + slot). BUFFERED mode = 63 KiB of back-buffer, not viable (no PSRAM, ~50-80 KiB kernel heap free).
- **Launcher**: scans `/sd/apps`, reads the embedded manifest + `icon.dr` (ADR 023). No modification required.
- **Capabilities**: `tools/dbt/capabilities.py` — `capabilities: [display]` resolves `libdisp.c` + `libst7789.c` (ADR 014).

## Scope

1. **`boards/m5stack-cardputer/board.yaml`**: remove/neutralise the I2C0 Grove section, add a `uart: id 1` entry on GPIO1/GPIO2 at `default_baud: 256000` (TX/RX direction: CardPuter TX → sensor RX, CardPuter RX ← sensor TX; the Builder documents the chosen Grove pinout in the YAML). Regeneration through `python tools/duneos-bspgen.py boards/m5stack-cardputer/board.yaml` — never hand-edit the generated files. Check that the `cardputer-net` profile (`profiles/`) is not impacted.
2. **`kernel/duneos_kernel/src/drivers/drv_uart.c`**: generalise it to register `/dev/uart1` when `DUNEOS_UART1_*` is defined in `board_config.h`, without `use_as_console`. Existing driver framework (`DUNEOS_DRIVER_REGISTER`), no new ABI symbol.
3. **`sdk/sensor/libld2450.c` + `sdk/sensor/include/duneos/ld2450.h`**: SDK library — opening the serial device, sending the multi-target tracking command at startup, frame synchronisation (resync on a partial/misaligned stream), decoding of the 3 targets (x mm, y mm, speed cm/s, resolution), "target absent" state. The decoding logic is a **pure function** (bytes → struct), separated from I/O, exercisable on a test vector.
4. **`apps/user/radar/`**: `radar.c`, `duneos.yaml` (name `radar`, `required_abi_version: 1`, `heap_size` > 0 ⇒ spawned, `capabilities: [display]`, minimal permissions, `icon: radar`), `icon.png`. GUI:
   - PPI "scope" view (half-plane in front of the sensor): targets positioned from X/Y, range arcs/graduations, animated sweep with blip persistence bounded to the scope area;
   - keyboard zoom 2/4/6 m;
   - per-target information panel: distance (m, 1 decimal), angle (signed °), speed (km/h);
   - demo mode usable without the sensor (simulated targets or replayed reference frame);
   - refresh at frame rate (~10 Hz) without full-screen flicker (STREAM and/or partial canvas, the Builder's choice within the memory budget);
   - exit key (convention of the existing apps) with clean fd closing and return to the launcher.
5. **No modification** to: launcher, loader, ABI symbol table, libgfx/libui (unless a blocking gap is discovered — report it, do not work around it).

## Acceptance criteria

1. **App build**: `python ../../../tools/dbt.py build` in `apps/user/radar/` (board `m5stack-cardputer`) completes successfully and produces `radar.dap` + `icon.dr`. *Check: run it, return code 0.*
2. **Manifest**: `python ../../../tools/dbt.py info` shows `name: radar`, `required_abi_version: 1`, `heap_size > 0`, arch `xtensa`. *Check: command output / `.duneos_manifest` section.*
3. **Kernel build**: `python tools/duneos-bspgen.py boards/m5stack-cardputer/board.yaml` then `idf.py build` pass with no error and no new warning. *Check: return codes + build log.*
4. **Device present**: after flashing, `/dev/uart1` appears and opens with no klog error; the console stays on its current routing (no console byte reaches the sensor). *Check: USB CDC shell session.*
5. **Decoding on a test vector**: the pure parsing function, fed a documented reference frame from the datasheet preceded by garbage bytes (proof of resynchronisation), produces the expected X/Y/speed values. Vector and expected values cited in a comment with the datasheet reference. *Check: observable execution path (demo mode or klog/dlog test trace).*
6. **Working on hardware**: LD2450 wired to Grove, `radar` launched from the launcher: a person moving in front of the sensor appears as a moving target on screen in under 1 s, with distance/angle/speed displayed and updated; plausible distances (±20 % at 1 m and 3 m). *Check: scripted manual test (procedure described in the PR).*
7. **3 targets**: several people in the field → up to 3 distinct blips; slots with no target show no ghost blip. *Check: manual test.*
8. **Zoom**: the zoom key switches the scale between 2/4/6 m, graduations updated, targets repositioned accordingly. *Check: manual test (demo mode accepted).*
9. **Sweep**: the sweep rotates continuously over the scope area with blip persistence, with no full-screen flicker and no visible drop in keyboard responsiveness. *Check: visual test.*
10. **Demo mode**: with no sensor connected, enabling demo mode displays animated simulated targets with a coherent info panel. *Check: manual test without hardware.*
11. **Clean exit**: the exit key returns to the launcher; 5 launch→exit cycles with no observable leak and no crash. *Check: manual test + klog monitor.*
12. **Launcher**: after `dbt deploy`, the app appears in the carousel with its icon. *Check: visual + scan count.*
13. **Memory budget**: spawned mode with the declared `heap_size`, without a 63 KiB full-screen back-buffer; no `gfx_open` failure. *Check: `dbt info` + `gfx_last_error` on target.*
14. **i2cscope**: the unavailability of `/dev/i2c-0` on the CardPuter is accepted (PO decision Q1) and documented in `board.yaml` (comment); i2cscope shows a clean error, not a crash. *Check: run i2cscope on target.*

## Out of scope

- Hand-editing bspgen-generated files (`sdkconfig.board`, `board_config.h`, `partitions.csv`, `idf_target.txt`).
- Phases 26-29: no OSAL, no native VFS, no network rework. (The constraint stands on its own — those phases are simply not done. The contest freeze that used to justify it lapsed on 2026-08-31.)
- Bumping `DUNEOS_ABI_VERSION`: no new exported symbol and no ABI struct change expected.
- LD2450 configuration commands other than multi-target tracking (filter zones, sensor baud rate, Bluetooth, firmware).
- Recording/exporting tracks, persistent history on SD.
- Support for boards other than the CardPuter (use the responsive helpers `ui_pct_*`/`gfx_get_info` all the same, ADR 024).
- Any modification of the launcher, the loader, or `vfs_dev.c` beyond standard driver registration.

## Risks

- **`use_as_console`**: `/dev/uart1` must never be a console candidate; check the behaviour with `console: none`.
- **64 KiB exec pool (IRAM)**: radar.c + gfx + ui + libld2450 + libst7789 must fit in the shared pool (the reason LVGL was rejected). Watch `.text` at `dbt build`.
- **Heap without PSRAM** (~50-80 KiB kernel free): bounded partial canvas (scope area only); the spawned `heap_size` is taken from the kernel heap.
- **Applicable Hard-Won Lessons traps**: no busy-poll (`while + usleep`); no bare `select()` on the UART fd (no `readable` callback → always "ready" → spin); read buffers on the stack, not `static`; timestamps through `duneos_hal_monotonic_us()`; UART0 GPIO43/44 inaccessible.
- **256000 baud stream**: fragmented/misaligned frames (100 ms HAL read timeout) — the parser must resynchronise on the header without blocking the UI.
- **`cardputer-net` profile**: check that a UART change in `board.yaml` does not impact this profile (currently modified in the working tree).

## Open questions

None — the 8 questions of the draft were settled by the PO (decision table in Context).

---

**Key files for the Builder**: `boards/m5stack-cardputer/board.yaml`, `kernel/duneos_kernel/src/drivers/drv_uart.c`, `kernel/duneos_kernel/include/duneos/dev_driver.h`, `sdk/display/include/duneos/gfx.h`, `sdk/ui/include/duneos/ui.h`, `sdk/game/include/duneos/game.h`, `apps/user/snake/` and `apps/user/i2cscope/` (models), `tools/dbt/capabilities.py`, `tools/duneos-bspgen.py` (l.190-201, `DUNEOS_UART<id>_*` emission).
