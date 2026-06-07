# Architecture Decision Records

This directory holds DuneOS' Architecture Decision Records (ADRs) — short, dated documents recording the architectural choices that shape the kernel and tooling. See [ADR 000](000-process.md) for the format and conventions.

## Index

| # | Title | Status | Consumes / consumed by |
|---|---|---|---|
| [000](000-process.md) | ADR process | Accepted 2026-05-19 | — |
| [001](001-error-model.md) | Kernel error model: `int` with POSIX `-errno` | Accepted 2026-05-19 | Phases 26-27 (`task.h`/`supervisor.h`/`vfs.h` migrations), all OSAL/VFS APIs |
| [002](002-osal-api.md) | OSAL API: static-storage handles, minimal surface | Accepted 2026-05-19 | Phase 26 (OSAL implementation), reinforces [008](008-memory-fragmentation.md) |
| [003](003-memory-caps.md) | Memory caps: placement hints with silent DRAM fallback | Accepted 2026-05-19 | Phase 26 (`osal_mem_alloc`), enables [008](008-memory-fragmentation.md) Pillar 4 |
| [004](004-task-priorities.md) | Task priorities: 5 symbolic levels, no runtime change | Accepted 2026-05-19 | Phase 26 (OSAL task creation), Phase 25 (`dbt system` validates `priority` in manifest) |
| [005](005-path-conventions.md) | Path conventions: `/flash` mandatory, `/sd` optional, `/proc` reserved | Accepted 2026-05-19 | Kernel boot, app portability across boards |
| [006](006-manifest-extensibility.md) | Manifest extensibility: unknown fields ignored | Accepted 2026-05-19 | Loader, `dbt build` validation, Phase 25 (`dbt system check`) |
| [007](007-multi-arch-smoke-test.md) | Multi-arch smoke test before Phase 29 | Accepted 2026-05-19 | CI infrastructure, Phases 26-28 portability discipline |
| [008](008-memory-fragmentation.md) | Memory fragmentation strategy: design layer, not allocator | Accepted 2026-05-19 | Phase 20 (TLSF TODO), kernel PR review checklist, future allocation policy |
| [009](009-driver-boundary.md) | Kernel/userspace boundary for drivers | Accepted 2026-05-19 | Driver placement decisions across all phases; 3 existing drifts tracked as Phase 24 debt |
| [010](010-arch-accelerators.md) | Architecture-specific accelerators (PCNT, PIO, ULP, …) | Accepted 2026-05-19 | Phase 24 debt (hal_encoder for PCNT/PIO), Phase 29 (rp2040 PIO sdk lib) |
| [011](011-threat-model.md) | Threat model: permissions are advisory, not enforced | Accepted 2026-05-19 | Phase 32 signing strategy; README security disclosure; any future "sandbox" claim |
| [012](012-test-strategy.md) | Test strategy: host-side first, on-device smoke | Accepted 2026-05-19 | Phase 26+ refactor safety net; CI gating; `dbt test` subcommand |
| [013](013-network-architecture.md) | Network architecture: POSIX sockets + vendored lwIP + per-medium HAL | Accepted 2026-05-19 | Phase 27 (VFS native + net rewrite), Phase 29 (RP2040 WiFi via CYW43); board.yaml `network:` section |
| [014](014-capability-resolution.md) | Capability-based source resolution (dbt resolves chip backends) | Accepted 2026-05-20 | App manifests declare `capabilities: [display]`; dbt picks `lib${board.display.driver}.c` automatically. Extension to input/audio/sensor planned. |
| [015](015-declarative-driven-architecture.md) | Declarative-driven kernel/userspace (driver self-registration + userspace board.h + drivers-as-apps north star) | Accepted 2026-05-20 | Phase 24.8 (auto-generated `<duneos/board.h>` from board.yaml), Phase 24.9 (kernel driver self-registration via ELF section), long-term drivers-as-userspace-apps. Eliminates hardcoded device paths + hand-maintained driver registry. |
| [016](016-captured-app-exit.md) | Captured-app exit semantics (`duneos_exit` longjmps to loader, doesn't kill shell) | Accepted 2026-05-20 | Phase 24.9.5 (implementation). Fixes the gfx_demo footgun: captured-app `duneos_exit` killing the parent shell. setjmp/longjmp + app-author contract (no pthread from captured, free fds/heap). |
| [017](017-multi-function-device.md) | Multi-Function Device handling (chip-core + per-feature sub-drivers, extracted on 2nd sub-feature) | Accepted 2026-05-20 | Future MFD chips (SX1509 PWM/keyboard scanner, TPS65217 PMIC, all-in-one sensors). Today dormant — SX1509 stays single-feature; ADR formalises the pattern before drift sets in. |
| [018](018-peripheral-instance-tables.md) | Board-declared peripheral instances as generated X-macro tables (`DUNEOS_GPIOCHIP_LIST`) | Accepted 2026-06-05 | GPIO expander drivers (PCF8574, SX1509) iterate one bspgen-emitted table; scales to any count across any I2C bus, zero per-instance `#ifdef`. Extends [015](015-declarative-driven-architecture.md) into the kernel driver layer; template for future multi-instance I2C sensors/gauges. |
| [019](019-linux-device-interface-semantics.md) | Linux-faithful device interface semantics (i2c-dev, gpio chardev, spidev, termios) | Accepted 2026-06-06 | App-facing `/dev` interfaces mirror Linux structs/ioctls; ESP-IDF vocabulary stops at the HAL. I²C (`i2c_transfer`/`i2c_msg`/`I2C_SLAVE`) + GPIO line-events done; SPI (`spidev`) + UART (`termios`) pending — roadmap "Linux device-interface alignment". |
| [020](020-logic-capture-device.md) | Logic-capture device (`/dev/logic0`) — synchronous sampling, swappable backend | Accepted 2026-06-06 | `i2cscope` sniffer: per-edge GPIO interrupts can't capture I²C (dropped/scrambled edges). `/dev/logic0` samples synchronously into transition records. Polled backend (`hal_logic.c`) done; DMA backend (LCD_CAM/DVP) planned behind the same interface. Native deviation under [019](019-linux-device-interface-semantics.md). |
| [021](021-pwm-device-interface.md) | PWM device interface (`/dev/pwmchip0`) — mirror Linux kernel `pwm_state`, LEDC in HAL | Proposed 2026-06-06 | No PWM interface today. App-facing `/dev/pwmchip0` mirrors Linux's kernel `pwm_state` (period/duty ns, polarity, atomic apply); ESP32 LEDC vocabulary stops at `hal_pwm`. Native deviation under [019](019-linux-device-interface-semantics.md) (Linux userspace PWM is sysfs). Not implemented — decision shape only. |
| [022](022-app-elf-section-optimization.md) | App ELF section reduction at link time (count + dead-code in exec pool) | Proposed 2026-06-06 | Multi-file apps inflate section count (`i2cscope` hit 584 > old 512 cap, bumped to 1024) and `ld -r` keeps dead lib code that the loader maps into the scarce 64 KB exec pool. Levers: drop `-ffunction-sections` (A), real function GC (B), or merge sections (C) — measure first. Protects [008](008-memory-fragmentation.md) budget. |
| [023](023-app-icon-assets.md) | App icon assets — freedesktop-style shared theme dir, not embedded | Proposed 2026-06-06 | Manifest `icon: <name>` (a name, not a blob); the `.dr` file lives in `/flash/share/icons/<name>.dr` installed at flash time (or adjacent to the `.dap` on SD), resolved by the launcher via a search path. Keeps the `.dap` lean (no exec-pool cost, [008](008-memory-fragmentation.md)); XDG model ([019](019-linux-device-interface-semantics.md)). Rejects the Flipper-style built-in glyph atlas. |
| [024](024-board-driven-responsive-views.md) | Board-driven responsive app views — layout from screen size, never hardcoded | Accepted 2026-06-06 | The same `.dap` must run on any panel. Screen geometry comes from the board (`ui_size()` runtime + `/flash/board.info` `width`/`height` declarative); apps derive layout from it and never hardcode pixels. Audit pass pending (i2cscope value column, etc.). Extends [015](015-declarative-driven-architecture.md) portability contract. |
| [025](025-app-concurrency-ram-limited.md) | App concurrency is RAM-limited (dynamic grow-only slot pool) | Accepted 2026-06-07 | The supervisor's fixed 4-slot array capped concurrent apps below what RAM allows. Slots are now a grow-only pool: malloc on demand, never freed (stable addresses keep the lock-free crash-handler lookup safe), inactive slots reused. `DUNEOS_MAX_RUNNING_APPS` becomes a soft buffer hint. Defers to [008](008-memory-fragmentation.md); OSAL-abstracted in Phase 26 ([002](002-osal-api.md)). |

## Reading order for new contributors

1. [000](000-process.md) — how to read and write these docs.
2. [001](001-error-model.md), [005](005-path-conventions.md), [006](006-manifest-extensibility.md) — surface contracts (errors, paths, manifest).
3. [002](002-osal-api.md), [003](003-memory-caps.md), [004](004-task-priorities.md) — kernel-internal contracts (OSAL).
4. [008](008-memory-fragmentation.md) — the strategy that ties allocation decisions together.
5. [009](009-driver-boundary.md), [010](010-arch-accelerators.md), [014](014-capability-resolution.md), [015](015-declarative-driven-architecture.md), [016](016-captured-app-exit.md), [017](017-multi-function-device.md) — where drivers live, how arch-specific hardware is exposed, how userspace apps stay board-portable, how the whole pipeline becomes declarative-driven, how captured apps exit safely, and how multi-function chips are structured.
6. [011](011-threat-model.md) — what DuneOS does and does not promise about security.
7. [012](012-test-strategy.md), [007](007-multi-arch-smoke-test.md) — how we guard against regressions: tests and portability builds.
8. [013](013-network-architecture.md) — how the network stack is layered and how new media (WiFi chips, Ethernet variants) plug in.

## Writing a new ADR

1. Pick the next free number (`NNN`).
2. Copy the structure from [000](000-process.md) — Context / Decision / Consequences / Alternatives.
3. One page max; cross-reference existing ADRs with `[NNN](NNN-slug.md)`.
4. Add a row to the index above.
5. Submit as a PR with status `Proposed`; flip to `Accepted · YYYY-MM-DD` once merged.

## Related docs

- [`../backlog.md`](../backlog.md) — unscheduled ideas and gaps surfaced during planning. Items get promoted to a real ADR or a ROADMAP_v2 entry as they earn scheduling.
