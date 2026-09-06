# ADR 010 — Architecture-specific accelerators

**Status:** Accepted · 2026-05-19

## Context

Microcontroller architectures expose specialised hardware peripherals that accelerate operations the CPU would otherwise do in software. Three classes exist:

**Class A — Equivalent goals, different HW.** ESP32 has PCNT (pulse counter) for quadrature decoding; RP2040 has PIO programs that can implement the same; STM32 has TIM in encoder mode. They all answer "decode a quadrature signal" with zero CPU after setup. Today DuneOS has `kernel/duneos_kernel/src/drivers/input/enc_quadrature.c` doing GPIO polling in software — works everywhere, wastes ESP32's PCNT.

**Class B — Unique to one architecture.** RP2040's PIO is a programmable I/O state machine — there's nothing equivalent on ESP32 or STM32 (RMT is partially comparable but limited). ESP32's ULP RISC-V coprocessor is similarly unique. Exposing these as portable kernel APIs is impossible.

**Class C — Partially equivalent.** ESP32 RMT (Remote Control) generates arbitrary timing patterns; RP2040 PIO can do the same and more. The intersection is "emit a configurable pulse train". The union is "do arbitrary signal processing".

Without a rule, the project drifts in one of two ways: (i) software-only implementations everywhere, leaving accelerators idle; (ii) ESP32-specific kernel APIs that don't port.

## Decision

Three patterns matching the three classes:

### Pattern A — Capability HAL with arch-specific backends

For operations where multiple architectures have *some* hardware acceleration for the *same logical task*, define a kernel HAL header that expresses the **capability**, not the peripheral.

```text
kernel/duneos_kernel/include/duneos/hal_encoder.h     # API: open/read/close a quadrature encoder
arch/xtensa_esp32s3/hal/hal_encoder.c                  # backend: PCNT
arch/riscv32/hal/hal_encoder.c                         # backend: PCNT (ESP32-C6 also has PCNT)
arch/arm_cortex_m/hal/rp2040/hal_encoder.c             # backend: PIO program
arch/arm_cortex_m/hal/stm32h7/hal_encoder.c            # backend: TIM encoder mode
```

If a backend cannot be implemented on a target, the HAL provides a **portable software fallback** in `kernel/duneos_kernel/src/drivers/<category>/hal_fallback_*.c` (GPIO interrupt + soft state machine). Apps written against `hal_encoder.h` work everywhere; performance varies by arch.

**Apply this pattern to**: quadrature encoders, hardware PWM, hardware capture/compare, hardware UART/SPI/I2C (already done), DMA-driven memcpy.

### Pattern B — SDK lib under `sdk/<arch>/`, app accepts non-portability

For peripherals truly unique to one architecture (RP2040 PIO, ESP32 ULP, STM32 TIM advanced modes that have no equivalent), expose them as **userspace libraries** scoped to that architecture. They never appear in `kernel/`.

```text
sdk/rp2040/pio.h, pio.c                                # only compiles on rp2040
sdk/esp32/ulp.h, ulp.c                                 # only compiles on xtensa_esp32s3
```

The app's `duneos.yaml` `arch:` field acts as a portability contract: if the app uses `sdk/rp2040/`, the kernel loader will refuse it on non-rp2040 boards (cross-ISA rejection already handles this — [[Phase 24]]).

`dbt build` enforces: linking `sdk/<arch>/` from an app whose manifest `arch:` does not match is a build error, not a runtime one. This makes the non-portability explicit at compile time.

### Pattern C — Capability HAL with degraded mode

For operations where the architectures overlap partially (ESP32 RMT vs RP2040 PIO for signal generation), define the HAL on the **intersection** of capabilities. The full power of the more capable peripheral becomes inaccessible through the HAL; apps that need it use Pattern B.

Example: `hal_signal_gen` covers "emit N pulses of width W with delay D" (RMT-style, achievable on both RMT and PIO). For RP2040-specific dynamic state machines, the app falls back to Pattern B (`sdk/rp2040/pio.h`).

## Decision rules

1. **Default position: software fallback.** Every HAL must have a portable software implementation, even if slower. This guarantees a board running an unsupported arch can still ship.
2. **Add Pattern A only when at least two archs would benefit.** Adding `hal_encoder` is justified by ESP32 + RP2040 + STM32; adding `hal_some_thing` for a single SoC's accelerator is Pattern B.
3. **Document the loss.** Each Pattern A header lists in its top comment: "On platforms without HW acceleration, this falls back to software at CPU cost X."
4. **Apps never `#include` arch-specific HAL headers directly.** Even Pattern A headers expose only the capability, not the backend. Apps that need backend specifics use Pattern B.

## Consequences

- The current `enc_quadrature.c` (software polling, kernel-side) is the **fallback**; ESP32 gains `hal_encoder` backed by PCNT in Phase 24 debt (see ROADMAP.md). This is the first concrete Pattern A migration.
- RP2040 PIO becomes a userspace SDK lib in Phase 29. No kernel API exposes it; apps that use it are non-portable by design and the manifest reflects this.
- ESP32 ULP (currently unused) follows the same pattern when needed: userspace lib in `sdk/esp32/ulp.h`, app declares ESP32-only.
- New HAL additions go through the [[ADR-002]] OSAL-style review: "is this on the intersection of capabilities, or only one arch?" Pattern A or B, not both.
- The kernel binary doesn't grow unboundedly with arch-specific code: Pattern B keeps it in app `.dap`s, Pattern A keeps only the capability API plus the active arch's backend in the kernel.

## Alternatives

- **Always use software fallback (Class A becomes "ignore PCNT/PIO/TIM")** — rejected. Wastes hardware purchased for the chip; degrades user-visible responsiveness (encoder polling at 1 kHz vs PCNT real-time).
- **Expose every peripheral as a kernel API** — rejected. Pollutes the kernel surface with arch-leaking names (`/dev/pcnt0`, `/dev/pio0`, `/dev/ulp0`); apps written against them are non-portable but the non-portability isn't surfaced explicitly.
- **Userspace-only HAL (no kernel hal_encoder)** — rejected for resources that need ISR or DMA setup (PCNT requires interrupt allocation). Some accelerator setups can't be done from userspace on a non-MMU system.
- **Pattern A only, with no escape hatch** — rejected. Forces the intersection-of-capabilities discipline even when an app legitimately wants the full power of PIO. Pattern B exists as the explicit, well-named exit.
