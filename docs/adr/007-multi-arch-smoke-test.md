# ADR 007 — Multi-arch smoke test before Phase 29

**Status:** Accepted · 2026-05-19

## Context

Phase 29 ports DuneOS to RP2040, the first non-ESP-IDF target. Phase 28 ports it to ESP32-C6 (RISC-V, still ESP-IDF). Between today and Phase 29, several layers will change in ways that *should* be portability-preserving but where regressions are hard to spot from a single-target build:

- OSAL implementation (Phase 26) — easy to inadvertently `#include "freertos/queue.h"` in a "portable" file.
- Native VFS (Phase 27) — easy to call `esp_vfs_register` in a `.c` that was supposed to be backend-agnostic.
- Loader arch_ops extraction (Phase 28 preamble) — easy to leave an `R_XTENSA_*` reference outside `arch/xtensa_esp32s3/`.

A regression slipped in during the OSAL phase only becomes visible when RP2040 fails to compile, possibly months later. Debugging "why doesn't this build for RP2040?" while also discovering "and also we have 6 unrelated portability bugs" is exhausting and demotivating — it's the failure mode that kills portability projects.

## Decision

Before any Phase 29 work begins, a CI smoke test must build the kernel for **three targets** on every push:

| Target | Build command | Validates |
|---|---|---|
| ESP32-S3 (`m5stack-cardputer` board) | `dbt flash kernel --build-only` | Xtensa + ESP-IDF — current default |
| ESP32-C6 (`esp32c6-devkitc` board) | `dbt flash kernel --build-only` (after switching `.duneos_board`) | RISC-V + ESP-IDF — proves arch.cmake works for a 2nd ISA on the same SDK |
| Linux x86_64 simulator | `dbt build --sim` (Phase 26+ deliverable) | OSAL via pthread + PicoLibc, no ESP-IDF — proves the kernel can be built outside Espressif's toolchain |

**Trigger:** every push to `main`, every PR. Fast feedback (< 5 minutes for all three) is more important than coverage breadth — slow CI is ignored.

**Failure policy:** a PR that breaks any of the three is not mergeable. No "we'll fix RP2040 later" — that's exactly what this ADR exists to prevent.

**Infrastructure:**

- The matrix runs on GitHub Actions (or equivalent) using Docker images with ESP-IDF v6.x preinstalled.
- The Linux simulator target is added as a build-only target in Phase 26 (alongside the pthread OSAL); it does not need to *run* anything yet — successful link is the smoke test.
- Caching: ESP-IDF toolchain and PicoLibc build artefacts are cached aggressively. The build itself is incremental within a job.

**Exemption:** documentation-only changes (`*.md`, `docs/**`) bypass the smoke test via path filter. Everything else runs the matrix.

## Consequences

- Phase 29 starts on a known-portable foundation. Whatever fails on RP2040 is RP2040-specific, not "accumulated portability rot since Phase 26".
- The simulator becomes a first-class target from Phase 26 onwards — incentive to keep it working, no longer optional infrastructure.
- An ESP32-C6 board (`esp32c6-devkitc`) must be added to the supported list in Phase 28 even if it doesn't have full driver coverage — a build-only smoke is enough.
- Cost: ~5 min of CI per push. Worth it for the portability insurance.
- This ADR doesn't specify *which* CI system. GitHub Actions is the default given the repo's home, but the matrix definition is portable.

## Alternatives

- **Single-target CI** — rejected. Reproduces the current state, where portability rot only surfaces during a port attempt.
- **Test runs for each target (vs build-only)** — rejected for the smoke test specifically. Real tests come from QEMU/board-in-the-loop and are a separate effort; build-only catches the bulk of portability regressions cheaply.
- **Defer until Phase 29 starts** — rejected. The whole point is to catch regressions *during* Phases 26-28, not after.
