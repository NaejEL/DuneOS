# ADR 009 — Kernel/userspace boundary for drivers

**Status:** Accepted · 2026-05-19

## Context

DuneOS has no MMU. The "kernel vs userspace" distinction does not carry the Linux meaning of memory isolation — it's a *placement* decision:

- **Kernel** = code linked into the kernel binary (currently the `factory` partition, 1.5 MiB on 8 MiB boards). Resident at all times, costs DRAM/IRAM budget continuously, shared by every running app.
- **Userspace** = code linked into a `.dap`, loaded into the app's monolithic pool (Phase 20), freed when the app exits.

The question "where does this driver go?" has been answered ad-hoc per phase, with three visible drifts today (see [Consequences](#consequences)). Without a rule, every new chip request reopens the same debate, and the kernel binary grows unchecked.

The kernel also pays a portability cost for each driver: kernel-side code must work across every supported arch/SDK, while a userspace lib only needs to compile for the active target.

## Decision

**Four-criterion test.** A driver goes in the kernel only if it meets at least one of these:

1. **Concurrent access** — multiple apps need to use the device simultaneously (network, audio mix, framebuffer compositor).
2. **Hard real-time** — sub-millisecond ISR latency or continuous DMA is required (WiFi MAC, audio I2S, camera capture).
3. **Shared resource arbitration** — the driver IS the arbiter of a shared bus (I2C, SPI, UART). Per-chip protocol on that bus stays userspace.
4. **Lifecycle independence** — the device must outlive any single app exit (WiFi connection, RTC, persistent storage).

Default if none apply: **userspace** — driver lives as a library in `sdk/<category>/` and is linked into the apps that need it.

**Bus controllers / per-chip drivers split.** I2C/SPI/UART bus controllers are kernel (criterion 3); the chips that talk on those buses are userspace libraries. This is the established Linux pattern (`/dev/i2c-N` + libi2c, `/dev/spi-N` + libspi). The kernel exposes the bus; apps speak the chip protocol.

**Two exceptions to the userspace default:**

- **Board-declared shared peripherals.** If a peripheral is declared in `board.yaml` (e.g. an SX1509 expander wired to the board's button matrix, or a BQ27220 fuel gauge whose state apps query via a daemon), it becomes a kernel resource (`/dev/gpiochip1`, `/dev/battery0`). Apps using the same chip in a project-specific wiring (not in `board.yaml`) stay userspace — they `open("/dev/i2c-0")` and speak the protocol.
- **Network stacks.** WiFi/BLE drivers stay kernel even though they look like "one device used by one app at a time" today, because (a) the underlying SoC blob requires kernel-resident FreeRTOS callbacks (ESP32 WiFi), (b) the lifecycle is system-scope (`wifi_daemon.dap` keeps the connection alive across other app exits).

**Driver placement matrix** (one-line per category — full reasoning in commit history):

| Category | Placement | Rationale |
|---|---|---|
| Bus controllers (I2C, SPI, UART) | Kernel | Criterion 3 |
| WiFi, Bluetooth, ESP-NOW | Kernel | Criteria 2, 4 |
| LoRa (SX1262, CC1101), NFC/RFID | Userspace | Transactional on bus, no stack |
| Thread/Zigbee | Kernel if stack embedded | Case by case (OpenThread maturity) |
| LCD/OLED/EPD displays | Userspace | One drawing app at a time |
| Framebuffer `/dev/fb0` (PSRAM compositor) | Kernel, optional | Criterion 1 — only useful with compositor (not in roadmap today) |
| Audio I2S DMA | Kernel | Criterion 2 |
| Audio codec config (PCM5102, ES8388) | Userspace | One-shot I2C config |
| Sensors (BMP280, SHT31, MPU6050) | Userspace | Transactional, app-owned |
| Battery fuel gauge | Userspace lib + optional system daemon | Lifecycle = app-scope |
| GPIO expanders (SX1509, PCF8574) | Kernel if in `board.yaml`, else userspace | Criterion 3 conditional |
| RTC | Kernel | Criterion 4 (`clock_gettime`) |
| Crypto accelerator | Kernel | Criterion 1 |
| Camera, CAN, USB host | Kernel | Criterion 2 |
| Touchscreen | Kernel if `/dev/input/event*` exposes events, else userspace | Criterion 1 |
| PWM (buzzer, servo) | Userspace | Trivial GPIO/timer use |
| Stepper with DMA timing | Kernel | Criterion 2 |
| Modem AT (SIM7600) | Userspace + PPP lib | One owner, transactional |

## Consequences

**Three existing drifts to correct** (tracked as Phase 24 "Driver placement debt" in ROADMAP_v2):

1. **`drv_battery_bq27220.c` is in the kernel** but fails criteria 1-4. It's a periodic I2C read. Move to `sdk/sensor/libbq27220.c` (userspace) + a `apps/system/bin/battery.dap` daemon if board.yaml declares `battery: bq27220`. The `/dev/battery0` device node remains for compatibility, served by the daemon.

2. **ST7789 has three codepaths** (`drv_disp_st7789.c` streaming `/dev/disp0` in kernel, `drv_fb_st7789.c` framebuffer `/dev/fb0` in kernel, `sdk/display/libst7789.c` userspace). Per the matrix, this should be one userspace lib. The framebuffer kernel path stays only as an optional compositor on PSRAM boards (no caller today; deferrable until a real compositor is built). `/dev/disp0` should be retired.

3. **GPIO expander policy was implicit.** Make it explicit: declared in `board.yaml` ⇒ kernel `/dev/gpiochipN`; otherwise userspace via `/dev/i2c-N` + chip lib. Update `bspgen.py` to emit the chip's I2C address and pin count into `board_config.h` when declared.

**New driver requests must justify in their PR.** A PR adding a `kernel/duneos_kernel/src/drivers/drv_*.c` must cite which of the four criteria it satisfies. If none apply, the contributor is redirected to write a `sdk/<category>/lib*.c` instead. This is the kernel-side analogue of the [[ADR-008]] "no new hot-path allocation without justification" rule.

**Memory budget benefit.** Moving misplaced drivers out of the kernel reduces the factory partition footprint. Today's binary is 0xFBA90 (1003 KiB) of 0x180000 (1536 KiB) — 34% headroom. Without discipline, this shrinks fast; with discipline, board.yaml-declared peripherals are the only growth vector.

**Driver portability becomes app concern.** A userspace driver lib (libst7789, libbq27220, …) is ISA-specific in compiled form but portable in source. Apps that want to be portable in *binary* form must use abstraction layers (`libgfx` for displays — [[Phase 18]]). This is consistent with [[ADR-006]] manifest extensibility — apps declare what they need, the system honours or refuses.

## Alternatives

- **Everything in kernel** — rejected. Kernel binary balloons; portability cost multiplies; per-board kernel rebuilds get heavier. Linux discarded this model in the 90s for good reason.
- **Everything in userspace, kernel is just bus controllers** — rejected. Real-time peripherals (WiFi, audio, camera) literally can't work in userspace on a non-MMU non-microkernel system. The kernel must own ISRs for these.
- **Kernel/userspace by complexity rather than criteria** — rejected. "Complexity" is subjective; the four criteria are testable.
- **Per-driver vote** — rejected. Reopens the debate every time; this ADR is the standing answer.
