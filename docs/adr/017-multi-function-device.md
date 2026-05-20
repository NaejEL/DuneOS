# ADR 017 — Multi-Function Device (MFD) handling

**Status:** Accepted · 2026-05-20

## Context

Some single I2C/SPI chips expose several logically independent sub-functions
that, in DuneOS terms, belong to different subsystems. Examples:

- **Semtech SX1509** — 16 GPIO + 16-channel PWM/LED breathing + keyboard matrix
  scanner + hardware debouncer + INT pin + reset pin. ([[ADR-009]] places GPIO and
  PWM in the kernel because both are shared resources; keyboard matrix lands in
  `/dev/input/event*` whose userspace ownership is still being migrated under
  Phase 24-debt #5.)
- **TI TPS65217** (future PMIC) — battery charger + voltage regulators +
  LED driver + GPIO + ADC.
- A future "all-in-one" sensor — IMU + magnetometer + temperature, each
  belonging to a different sensor subsystem.

Without a convention each contributor invents their own structure:

- Monolithic file with all sub-functions in one place (hard to read, hard to opt
  in/out, mixes subsystems).
- One file per sub-function but no shared init (each sub-driver re-resets the
  chip → race / wasted I2C traffic).
- Implicit "first sub-driver wins" init (silent ordering bugs across boards).

The kernel/userspace boundary itself is settled by [[ADR-009]]; this ADR is
strictly about *internal structure* once a chip's placement is known.

## Decision

**Adopt the "chip-core + sub-drivers" pattern, formalised once and applied
lazily.**

### Pattern

For any chip that exposes ≥ 2 sub-functions used by DuneOS:

```
src/drivers/<chip-family>/
  <chip>_core.c        # private — chip init/reset/clock/bus handle,
                       # ref-counted across sub-drivers
  <chip>_core.h        # private — internal API for sub-drivers only
  drv_<subsystem>_<chip>.c   # one per active sub-function:
                             #   drv_gpio_sx1509.c    → /dev/gpiochipN
                             #   drv_pwm_sx1509.c     → /dev/pwmN
                             #   drv_input_sx1509.c   → /dev/input/eventN
```

### Conventions

1. **`<chip>_core.c` owns the chip.** All sub-drivers go through it for bus
   access. The core is responsible for:
   - First-time chip reset (REG_RESET write, then optional REG_DEV_ID check)
   - Holding the shared I2C/SPI bus handle and per-chip mutex
   - Reference-counting init: first sub-driver to call `<chip>_core_init()`
     triggers the reset; subsequent calls just bump the refcount

2. **One Kconfig per sub-feature, `select`-ing the core.**
   ```kconfig
   config DUNEOS_DRV_SX1509_CORE
       bool

   config DUNEOS_DRV_GPIO_SX1509
       bool "..."
       select DUNEOS_DRV_SX1509_CORE
       depends on DUNEOS_DRV_I2C

   config DUNEOS_DRV_PWM_SX1509
       bool "..."
       select DUNEOS_DRV_SX1509_CORE
       depends on DUNEOS_DRV_I2C
   ```
   Sub-features compose at build time; `<chip>_core.c` is compiled iff at least
   one sub-feature is enabled.

3. **board.yaml declares features explicitly.** The chip block lists the
   sub-functions it actually uses on this board:
   ```yaml
   gpio_expanders:
     - type: sx1509
       i2c_addr: 0x3E
       features: [gpio]                # could be: [gpio, pwm]
       # PWM-specific knobs (only read when "pwm" in features):
       # pwm_pins: [0, 1, 2, 3]
   ```
   `bspgen.py` maps the declared features → matching `CONFIG_*=y` lines and
   per-feature defines in `board_config.h`. Unused features cost nothing in
   the binary.

4. **Trigger rule: extract `<chip>_core.c` at the second sub-feature, not
   before.** Single-feature chips stay as one file (`drv_<subsystem>_<chip>.c`)
   with init folded in. Pre-emptive extraction is YAGNI — the SX1509 today is
   GPIO-only and the existing `drv_gpiochip_sx1509.c` is fine. When a PR adds
   a second sub-function (PWM, keyboard scanner) the contributor extracts the
   core *in the same PR* as adding their feature.

### Documentation contract

The "Adding a new kernel driver" recipe in `CLAUDE.md` gains a paragraph
referencing this ADR when the chip is MFD. Single-feature chips follow the
existing 6-step recipe unchanged.

## Consequences

- **Novice ergonomics.** Adding a new MFD chip = follow the pattern: write
  `<chip>_core.c` (~50 LoC: reset + bus + refcount) + one `drv_<feature>_<chip>.c`
  per feature you implement. Subsequent features added by other contributors
  drop in beside the core without touching it.
- **No code mass tax today.** SX1509 stays as one file; the ADR is dormant
  until a real MFD pressure point appears (likely PWM-SX1509 or the first PMIC
  added to a board).
- **Sub-driver isolation.** A bug in `drv_pwm_sx1509.c` cannot corrupt the
  shadow state of `drv_gpio_sx1509.c` — each sub-driver owns its registers,
  the core owns only chip-wide state (reset, clock, refcount).
- **Refcounted init handles boot order naturally.** `vfs_dev.c` registers
  sub-drivers in any order; the first to call `<chip>_core_init()` triggers
  the hardware reset, the rest no-op.
- **Cost of being wrong.** If the trigger rule ("extract at the 2nd feature")
  is missed, the 2nd feature ends up with duplicate init code → silent races.
  Reviewers check for this when a PR touches `drv_<*>_<chip>.c` for a chip
  that's already in tree.

## Alternatives

- **Linux MFD subsystem (`mfd_cell` parent/child registry).** Powerful but
  ~500 LoC of registry plumbing for benefits we don't need (hot-plug, kernel
  module loading). Rejected: too heavy for a no-MMU embedded kernel.
- **Monolithic `drv_<chip>.c` per chip.** Rejected: violates "one file = one
  responsibility"; a 600-line file mixing GPIO + PWM + keyboard scan is
  unfriendly to the novice contributor that this project explicitly targets.
- **No convention, decide per-chip.** Rejected: each MFD chip would reinvent
  its structure; future maintainers face N divergent patterns. The ADR exists
  to prevent that drift.
- **Userspace per-feature daemons.** Rejected for kernel-side MFDs: GPIO and
  PWM are shared resources ([[ADR-009]] criterion 1), can't reasonably live
  per-app. (Userspace MFDs — e.g. a future TPS65217 fuel-gauge view through
  `apps/system/battery_daemon` — follow [[ADR-014]] capability resolution with
  the same chip-core pattern in `sdk/<category>/`.)
