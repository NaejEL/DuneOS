# ADR 001 — Kernel error model: `int` with POSIX `-errno`

**Status:** Accepted · 2026-05-19

## Context

The kernel was born with `esp_err_t` everywhere — natural choice when ESP-IDF was the only target, but a hard coupling for portability. By 2026-05, four core public headers (`init.h`, `task.h`, `vfs.h`, `supervisor.h`) still leak `esp_err_t` in 11 signatures; the userspace library `libdune.a` already uses POSIX `int`/-errno (PicoLibc convention); the loader, drivers, and HAL have been migrated to `int` in Phase 24.

Closing this divergence requires choosing one error convention that works on (a) ESP-IDF as the current backend, (b) the simulator Linux pthread port, (c) future bare-metal ports (RP2040, STM32, …). Two contenders:

- A dedicated enum `duneos_status_t { DUNEOS_OK, DUNEOS_ERR_NOT_FOUND, … }` — clean DuneOS namespace but ergonomically annoying (`if (err == DUNEOS_ERR_NOT_FOUND)` instead of `if (err == -ENOENT)`), and the userspace library already speaks `-errno` (PicoLibc), so the kernel/libdune boundary would need translation everywhere.
- Plain `int` returning **0 on success, negative `-errno` on failure** — the POSIX convention. Already what `libdune.a` and the loader use. Free interop with PicoLibc's `errno`. No new vocabulary to learn.

## Decision

**Kernel public APIs and `libdune` return `int`. 0 means success. Negative values are `-errno` constants from `<errno.h>` (`-ENOENT`, `-EIO`, `-ENOMEM`, `-EINVAL`, `-EFAULT`, `-ETIMEDOUT`, `-EBUSY`, …).**

- Internal `.c` files may keep `esp_err_t` for ESP-IDF call results, but must convert to `-errno` before crossing any public boundary. A private helper `esp_to_errno(esp_err_t)` is allowed during the Phase 26/27 transition; it disappears when the underlying ESP-IDF call disappears.
- No `duneos_status_t`, `duneos_err_t`, or similar wrapping enum.
- `errno` itself remains thread-local and is the userspace-facing channel for syscall errors (POSIX standard). Kernel returns the same code negated. Callers in C apps see `read() == -1` and consult `errno`; kernel callers compare directly against `-EIO` etc.
- Error logging via `klog_w("op failed: %s", strerror(-rc))` — `strerror` is in PicoLibc, works everywhere.

## Consequences

- The 4 leaking headers (`init.h`, `task.h`, `vfs.h`, `supervisor.h`) get migrated incrementally, each in the phase that touches the underlying implementation: `init.h` standalone (already done 2026-05-19 in commit `91b3175`), `task.h`+`supervisor.h` in Phase 26 alongside the OSAL migration, `vfs.h` in Phase 27 alongside the native VFS rewrite. No throwaway "esp_err_t → enum → int" double translation.
- The kernel and userspace speak the same dialect — no translation layer at the ABI boundary. `libdune.a` can simply forward kernel return values.
- ESP-IDF's `esp_err_to_name()` is replaced by `strerror(-rc)` everywhere in DuneOS code. Slightly less specific (no `ESP_ERR_WIFI_NOT_INIT` granularity) but portable and standard.
- ADR is binding on Phase 26 (OSAL: `osal_*` functions also return `int`/-errno) and Phase 27 (native VFS).

## Alternatives

- **`duneos_status_t` enum** — rejected. Forces translation at every kernel↔libdune boundary; ergonomics worse than `-errno`; no precedent in similar projects (Zephyr, NuttX, FreeRTOS-Linux all use POSIX-style integers).
- **`bool` + thread-local errno** — rejected. Hides the failure mode from the caller; harder to log; no way to express "partial success" (returning the number of bytes read).
- **Keep `esp_err_t`** — rejected. Hardcodes ESP-IDF as the only backend; would force the simulator and RP2040 ports to fake `esp_err_t` constants. Already the dette this ADR cleans up.
