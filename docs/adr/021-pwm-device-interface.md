# ADR 021 — PWM device interface (`/dev/pwmchip0`)

**Status:** Proposed · 2026-06-06

## Context

DuneOS has no PWM interface yet. The need surfaced while discussing the logic
sniffer ([[ADR-020]]): the ESP32 PWM peripheral is **LEDC**, and the future DMA
capture backend would use LEDC to synthesise a sample clock — but apps also want
PWM directly (servo, buzzer, LED dimming, motor speed). Per [[ADR-019]] the
app-facing interface should mirror Linux, with ESP-IDF (`ledc_*`) vocabulary
stopping at the HAL.

Linux exposes PWM to userspace through **sysfs** (`/sys/class/pwm/pwmchipN/`:
`export`, `pwmX/period`, `pwmX/duty_cycle`, `pwmX/polarity`, `pwmX/enable`).
DuneOS has no sysfs — it is a `/dev` + `ioctl` system — so a literal copy is
impossible. The Linux *kernel-internal* PWM model, however, is clean and maps
directly: `struct pwm_state { period, duty_cycle, polarity, enabled }` applied
atomically by `pwm_apply_state()`.

## Decision (proposed)

Expose PWM as a **`/dev/pwmchip0`** character device. Mirror the Linux *kernel*
PWM model (`pwm_state` + atomic apply), not the sysfs string-poking layer —
sysfs has no DuneOS equivalent, so this is a documented native deviation under
[[ADR-019]].

### Interface (`<duneos/pwm_ioctl.h>`)

```c
typedef struct {
    uint8_t  channel;     /* PWM channel on this chip                    */
    uint8_t  polarity;    /* PWM_POLARITY_NORMAL / _INVERSED             */
    uint8_t  enabled;     /* 0 = off (output idle), 1 = running          */
    uint8_t  _pad;
    uint32_t period_ns;   /* full cycle, nanoseconds (Linux unit)        */
    uint32_t duty_ns;     /* high time, nanoseconds; 0..period_ns        */
} pwm_state_t;

#define PWMCHIP_GET_INFO  0x01  /* → pwmchip_info_t { npwm }             */
#define PWM_APPLY         0x02  /* ← pwm_state_t  (atomic set)           */
#define PWM_GET           0x03  /* ↔ pwm_state_t  (fill from .channel)   */
```

- `period_ns` / `duty_ns` in **nanoseconds** — Linux's unit; keeps app code and
  reasoning portable. The HAL converts to LEDC frequency (`1e9 / period_ns`) and
  duty fraction (`duty_ns / period_ns`), picking a timer + resolution.
- `PWM_APPLY` is **atomic** (period + duty + polarity + enable in one call), like
  `pwm_apply_state()` — avoids the glitchy intermediate states the sysfs
  attribute-at-a-time model suffers from.

### Layering ([[ADR-009]])

- Device/core (`drv_pwm.c`, `/dev/pwmchip0`) speaks `pwm_state_t` — Linux-shaped,
  SDK-agnostic.
- HAL (`arch/<arch>/hal/hal_pwm.c`) keeps SDK vocabulary: ESP32 → LEDC
  (`ledc_timer_config` / `ledc_channel_config` / `ledc_set_duty`). The ns→LEDC
  arithmetic, timer allocation, and resolution/frequency trade-off live here.
- Board declaration: `board.yaml` `pwm:` section lists channels (gpio, optional
  default frequency); bspgen emits the pin table + `CONFIG_DUNEOS_DRV_PWM=y`,
  following the instance-table pattern of [[ADR-018]].

### Permissions

A new capability bit (`PERM_PWM`) gates `/dev/pwmchip0`, resolved by
`capability_map.py` to `CONFIG_DUNEOS_DRV_PWM`.

## Consequences

- App authors get a familiar period/duty/polarity/enable model; LEDC's
  timer/channel/resolution complexity stays in the HAL.
- LEDC timer/channel exhaustion (the S3 has a limited count, some already used
  for backlight/buzzer) must be arbitrated by the HAL — a board can't expose more
  PWM channels than LEDC can back. The `board.yaml` `pwm:` table makes the budget
  explicit at build time.
- A future DMA logic backend ([[ADR-020]]) that needs a synthesised PCLK can
  reuse the same `hal_pwm`/LEDC path internally rather than poking LEDC directly.

## Status of the rollout

Not implemented. This ADR records the **decision shape** so the first PWM driver
lands Linux-faithful instead of ESP-IDF-flavoured. Scheduling TBD (post-contest).

## Cross-references

- [[ADR-019]] — Linux-faithful device semantics (PWM is a native deviation:
  Linux's userspace PWM is sysfs, which DuneOS lacks; we mirror the kernel model).
- [[ADR-009]] — driver boundary (LEDC vocabulary stops at the HAL).
- [[ADR-018]] — board-declared peripheral instance tables (PWM channel table).
- [[ADR-020]] — logic capture (the LEDC/PCLK link that surfaced this need).
