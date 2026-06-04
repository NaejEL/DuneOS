# ADR 018 — Board-declared peripheral instances as generated tables

**Status:** Accepted · 2026-06-05

## Context

Homogeneous I2C peripherals appear in variable counts per board: GPIO expanders
today (PCF8574, SX1509), I2C fuel gauges and sensors tomorrow. The first cut
enumerated each instance with numbered macros — `bspgen` emitted
`DUNEOS_GPIOCHIP1_TYPE … DUNEOS_GPIOCHIP4_*`, and every driver consumed them
with hand-unrolled `#ifdef DUNEOS_GPIOCHIP{1..4}_TYPE` blocks, a fixed
`MAX_*_INSTANCES = 4` slot ceiling, and a hardcoded I2C bus 0.

This breaks down immediately:

- **No scale.** A relay board with 64 expanders needs 64 `#ifdef` blocks and a
  bigger ceiling — a driver edit per board. A PCF8574 has only 8 addresses per
  bus, so 64 chips *must* span several buses; bus 0 in the driver can't express
  that.
- **Duplicated wart.** `drv_gpiochip_pcf8574.c` and `drv_gpiochip_sx1509.c`
  each carried the same four-way unroll — the pattern multiplies per chip type.
- **Contradicts [[ADR-015]].** `board.yaml` is the source of truth on paper, but
  the driver re-encodes the instance count by hand. The declarative pipeline
  stops at the kernel door.

## Decision

**A board-declared set of homogeneous peripheral instances is emitted by bspgen
as one X-macro table and iterated by drivers — never enumerated per-instance.**

`bspgen` emits a single table in `board_config.h`, one row per `board.yaml`
entry, carrying everything an instance needs (including its bus):

```c
#define DUNEOS_GPIOCHIP_LIST(X) \
    X(1, "pcf8574", 0, 0x21, 8, DUNEOS_GPIO_DIR_INPUT) \
    X(3, "pcf8574", 1, 0x24, 8, DUNEOS_GPIO_DIR_OUTPUT)
```

A shared `gpiochip_table.h` provides the descriptor struct and a `ROW` emitter.
Each chip-type driver builds its array from the table, **sizes its slot pool to
the table itself** (`sizeof(descs)/sizeof(descs[0])` — no magic ceiling), and
registers only rows whose `type` matches:

```c
static const duneos_gpiochip_desc_t s_descs[] = { DUNEOS_GPIOCHIP_LIST(DUNEOS_GPIOCHIP_ROW) };
for (size_t i = 0; i < N; i++) if (!strcmp(s_descs[i].type, "pcf8574")) register_instance(&s_descs[i]);
```

Field validation (e.g. `direction` ∈ {input, output}) happens at generation
time: bad declarations fail the build loudly, never mis-encode silently.

## Consequences

- **Boards scale for free.** Adding/moving/re-bus-ing an expander is a
  `board.yaml` edit + bspgen — zero driver change, any count across any bus.
- **One table, many consumers.** A new chip type is a new driver iterating the
  same table filtered by `type`; the table is shared, the protocol code isn't.
- **Counts live in one generated place.** Drivers carry no instance ceiling and
  no per-instance `#ifdef`; the kernel no longer "knows" how many chips a board
  has — it asks the table.
- **Errors caught at build.** Typo'd `direction` aborts bspgen instead of
  flipping a relay bank to output at boot.
- **Cost: indirection.** The X-macro is less obvious than a literal array to a
  newcomer; mitigated by documenting it once in `gpiochip_table.h`.
- **Compile-time, not hot-plug.** The table is fixed at build — correct for
  board-soldered peripherals; dynamic discovery is a non-goal on a no-MMU
  target (consistent with [[ADR-015]]).

## Alternatives

- **Numbered macros + unrolled `#ifdef` (status quo).** Rejected: doesn't scale,
  duplicated per driver, fixed ceiling, bus not expressible.
- **Runtime parse of a board descriptor blob** (à la `libst7789` reading
  `board.info`). Rejected by [[ADR-015]]: rediscovers build-time knowledge at
  runtime, badly.
- **One generic gpiochip driver dispatching by `type`.** Rejected: PCF8574
  quasi-bidirectional pins and the SX1509 register map differ enough that
  per-type drivers read clearer — they share the table, not the logic.
- **Kconfig-generated per-driver arrays.** Rejected: Kconfig can't express a
  variable-length list of structured records cleanly.
