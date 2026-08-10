# ADR 019 — Linux-faithful device interface semantics

**Status:** Accepted · 2026-06-06

## Context

DuneOS exposes hardware to apps through POSIX device nodes (`/dev/i2c-0`,
`/dev/gpiochip0`, `/dev/spi-1`, `/dev/input/event0`, …) driven by `open` /
`read` / `write` / `ioctl`. The *shape* of each interface — ioctl numbers,
argument structs, transfer models — was historically invented per-driver, and
some of it leaked ESP-IDF vocabulary into the app-facing ABI:

- I²C used `ioctl(I2C_SET_ADDR, &addr)` + a bespoke `i2c_rdwr_t { tx, txlen, rx,
  rxlen }`, and the kernel core was named `i2c_bus_write_read()` /
  `i2c_bus_probe()` — clearly modelled on `i2c_master_bus` from ESP-IDF.

This matters because the device ABI is the surface app authors learn and code
against. If it mirrors Linux, then knowledge, muscle memory, and large amounts
of existing app/driver code transfer directly; if it mirrors ESP-IDF, DuneOS
inherits a vocabulary that is unfamiliar outside the Espressif ecosystem and
contradicts the project's "partial POSIX layer" goal.

## Decision

**DuneOS userspace device interfaces mirror Linux semantics and naming.** When
a Linux equivalent exists (i2c-dev, gpio chardev, spidev, termios, evdev), the
DuneOS interface copies its structs, ioctl numbers, and transfer model as
closely as the architecture allows. New invention is reserved for things Linux
has no equivalent for.

Concretely, the contract per subsystem:

- **I²C** → Linux `i2c-dev`: `struct i2c_msg { addr, flags, len, buf }` with
  `I2C_M_RD`; `ioctl(I2C_SLAVE, addr)` and `ioctl(I2C_RDWR,
  struct i2c_rdwr_ioctl_data*)`; kernel core primitive named `i2c_transfer()`.
  A zero-length write message is an address probe (i2cdetect-style). **Done.**
- **GPIO** → Linux GPIO chardev line-events: arm a line with edge detection,
  then `read()` timestamped edge events. **Done** (`GPIOCHIP_SET_IRQ` +
  `gpio_event_t`); the broader `gpiod` line-request model is a later refinement.
- **SPI** → Linux `spidev`: `struct spi_ioc_transfer`, `SPI_IOC_MESSAGE(n)`,
  `SPI_IOC_WR_MODE` / `WR_MAX_SPEED_HZ` / `WR_BITS_PER_WORD`. **Pending.**
- **UART/serial** → POSIX `termios` (`tcgetattr`/`tcsetattr`, `cfsetspeed`).
  **Pending.**

### Where the line sits

Linux semantics apply at the **device node + kernel-core layer** — the part
apps and in-kernel consumers see. The **HAL** (`arch/<arch>/hal/hal_*.c`)
remains the SDK bridge and keeps its `duneos_hal_*` names and the underlying
SDK's idioms ([[ADR-009]] driver boundary). The kernel core translates between
the Linux-shaped device interface and the HAL. So `i2c_transfer()` (core) calls
`duneos_hal_i2c_write_read()` / `_probe()` (HAL → ESP-IDF `i2c_master_*`); the
ESP-IDF vocabulary stops at the HAL and never reaches the app ABI.

### Deviations are allowed, but named

Exact Linux fidelity is not always possible (no MMU, no `anon_inode` fd from
ioctl, different DMA constraints). Where DuneOS must deviate (e.g. GPIO events
delivered on the chip fd rather than a per-request event fd), the deviation is
documented in the interface header and kept as close to the Linux model as the
constraint permits.

## Consequences

- App authors can lean on Linux man pages and port Linux userspace device code
  with minimal change.
- Each subsystem migration is an ABI change for that device interface (old
  bespoke structs disappear). In-tree consumers are migrated in the same change;
  out-of-tree `.dap`s built against the old interface must be rebuilt. This does
  not necessarily bump `DUNEOS_ABI_VERSION` (the function table is unchanged) —
  it is a device-protocol change, versioned per interface header.
- A systematic pass is required to audit and migrate the remaining interfaces
  (SPI, UART, and a review of input/fb). Tracked on the roadmap as
  "Linux device-interface alignment".

## Status of the rollout

| Interface | Linux model | State |
| --- | --- | --- |
| I²C | i2c-dev (`i2c_msg`, `I2C_SLAVE`/`I2C_RDWR`) | ✅ done |
| GPIO events | gpio chardev line-events | ✅ done (chip-fd delivery) |
| I/O multiplexing | POSIX `select()` | ✅ `select()` on `/dev` (esp_vfs bridge, per-driver `readable`); `poll()` pending (no esp_vfs poll), native impl is Phase 27 |
| SPI | spidev (`spi_ioc_transfer`, `SPI_IOC_MESSAGE`) | ⏳ pending |
| UART | termios | ⏳ pending |
| input / fb | evdev / fbdev | 🔍 review for drift |

> Note: the exported `select` is now esp_vfs's (works on `/dev` fds **and**
> sockets), replacing the previous NET-gated `lwip_select` which only handled
> lwIP sockets. ESP-IDF/FreeRTOS primitives used by the event drivers
> (drv_gpio, drv_input: `xSemaphore`, `portMUX`) stay until Phase 26 (OSAL)
> abstracts them — they're inside the kernel, not the app ABI.

## Cross-references

- [[ADR-009]] — driver boundary (HAL keeps SDK vocabulary; this ADR governs the
  layer above it).
- [[ADR-006]] — manifest extensibility (additive-change rule).
- Linux references: `<linux/i2c.h>`, `<linux/i2c-dev.h>`, `<linux/gpio.h>`,
  `<linux/spi/spidev.h>`, `<termios.h>`.
