# ADR 020 — Logic-capture device (`/dev/logic0`)

**Status:** Accepted · 2026-06-06

## Context

The `i2cscope` sniffer first tried to capture I²C traffic with per-edge GPIO
interrupts (`/dev/gpiochip0` line-events). On real buses this fails: at
100–400 kHz the kernel ISR (critical section + timer read + semaphore +
select-notify) cannot keep pace, and — worse — SCL and SDA edges that fall
within ~1 µs of each other are serialised by the interrupt controller and have
their level **sampled after the fact**, so the recorded bit values are wrong
even when no edge is dropped. A decoded capture is a hash of spurious
START/STOP markers and incorrect addresses; the VCD shows wildly irregular clock
periods (6 µs … 366 µs half-cycles) — the signature of merged/dropped edges.

Edge-interrupt sniffing cannot capture a clock-rate serial bus. Reliable capture
requires **synchronous sampling** — reading the lines at a fixed cadence,
faster than the bus — not reacting to edges.

## Decision

Introduce a dedicated **logic-capture device, `/dev/logic0`**, separate from
`/dev/gpiochip0`. GPIO chardev stays the line-event / set-value interface;
logic0 is the "sample N lines synchronously into a buffer" interface.

Linux has no canonical kernel logic-analyzer node (sigrok drives capture
hardware from userspace), so per [[ADR-019]] this is a DuneOS-native interface —
but kept POSIX-shaped: `open` → `ioctl(LOGIC_SET_CONFIG)` → `read()` returns
packed transition records.

### Interface (`<duneos/logic_ioctl.h>`)

- `ioctl(fd, LOGIC_SET_CONFIG, &logic_config_t)` — pick the channels (a GPIO per
  bit position, channel 0 = bit 0…), the post-trigger idle timeout, an absolute
  capture ceiling, and a trigger timeout.
- `read(fd, buf, n*sizeof(logic_sample_t))` — perform **one capture window**:
  busy-wait (abortable only by `trigger_timeout_us`) for any channel to change,
  then capture transitions until the buffer fills, the bus idles for `idle_us`,
  or `hard_cap_us` elapses. Returns the byte count; **0 means no activity within
  the trigger timeout** (the caller loops, checking its own UI/abort between
  calls). Each record is `{ uint32_t t_us; uint32_t levels; }` — one entry per
  *transition* of the channel mask, `t_us` relative to the window start.

The record is RLE-by-transition (not uniform samples) so it stays compact on a
no-PSRAM board and maps 1:1 to a VCD value-change. Because the level is sampled
*synchronously by the poll loop* (not in an ISR after the edge), the recorded
bit values are correct — the root cause of the old garbage.

### Capture backend is swappable behind the HAL

`duneos/hal_logic.h` defines `duneos_hal_logic_capture()`. Two backends, same
interface:

| Backend | State | Notes |
| --- | --- | --- |
| **Polled** (`hal_logic.c`) | ✅ this ADR | Tight register-poll loop, interrupts disabled on the calling core, CPU-cycle-counter timestamps. Reliable for I²C ≤ 400 kHz. Capture window bounded by `hard_cap_us` (kept < `ESP_INT_WDT_TIMEOUT_MS`). Only the sampled GPIOs are touched — no extra pins. |
| **DMA** (DVP/LCD_CAM, future) | ⏳ planned | ESP32-S3 LCD_CAM camera controller + GDMA for continuous fixed-rate parallel sampling. Higher rates, continuous capture; costs a synthesised PCLK + VSYNC + DE and 3 spare GPIOs. Drops in behind the same `/dev/logic0` interface — decode and VCD are unchanged. |

The polled backend ships first because it needs only the two sniff pins and no
peripheral bring-up, so the whole chain (device → decode → VCD → PulseView) is
validatable immediately; the DMA backend is a performance upgrade, not a
re-architecture.

### Why interrupts are disabled during the burst (and why that's safe)

Without an active `select()`/ISR in the hot path the sampler is at its lightest,
but a FreeRTOS tick or other ISR preempting the loop for even ~1 µs can miss a
400 kHz half-bit. `portENTER_CRITICAL` disables interrupts on the **calling core
only** (the other core and its watchdog keep running). The burst is bounded by
`hard_cap_us` (default 50 ms ≪ the 300 ms interrupt watchdog), so the critical
section can never trip `INT_WDT`. The *trigger wait* runs with interrupts on
(preemptible, bounded by `trigger_timeout_us`); interrupts are only cut once
activity is seen.

## Consequences

- `i2cscope` sniffer opens `/dev/logic0` instead of arming `/dev/gpiochip0`
  edges; the edge-based decoder is unchanged (it already keys off mask
  transitions, so synchronous samples feed straight in).
- `/dev/gpiochip0` keeps its line-event API for low-rate / bit-bang use; ADR 019
  is unchanged. The two devices are complementary, not redundant.
- The capture window is bounded (no PSRAM) — the sniffer accumulates across
  successive `read()` windows, which also gives a natural abort/UI checkpoint.
- A future DMA backend is an additive change behind `hal_logic.h`; no app or
  decode change.

## Cross-references

- [[ADR-019]] — Linux-faithful device semantics (logic0 is a documented native
  deviation: no Linux equivalent exists).
- [[ADR-009]] — driver boundary (register access + cycle counter stay in the
  arch HAL; the device/core layer is SDK-agnostic).
