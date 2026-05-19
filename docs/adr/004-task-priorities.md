# ADR 004 — Task priorities: 5 symbolic levels, no runtime change

**Status:** Accepted · 2026-05-19

## Context

Schedulers number priorities differently. FreeRTOS uses 0..`configMAX_PRIORITIES-1` ascending (higher number = more urgent), typically 25 levels but configurable. POSIX `pthread` separates scheduling policies (`SCHED_OTHER`/`SCHED_FIFO`/`SCHED_RR`) with disjoint priority ranges; on Linux only privileged processes can use `SCHED_FIFO`. Some real-time OS use inverted scales.

If DuneOS apps specify priorities as platform-native integers, every port needs a translation, and a manifest declaring `priority: 12` becomes ambiguous (was that intended as "high" or "below the WiFi daemon"?). Worse, an app written assuming FreeRTOS values would silently misbehave on the simulator.

There's also a question of runtime mutability. Apps could theoretically request priority changes mid-execution (`pthread_setschedparam`) — but that adds API surface, raises permission questions (who can promote themselves?), and is rarely needed in the kinds of workloads DuneOS targets.

## Decision

DuneOS exposes a **5-level symbolic priority scale** in `osal.h`:

```c
typedef enum {
    OSAL_PRIO_IDLE,    // background work — yields to everything else
    OSAL_PRIO_LOW,     // non-time-sensitive (logging, telemetry)
    OSAL_PRIO_NORMAL,  // default for apps
    OSAL_PRIO_HIGH,    // input handling, UI redraws
    OSAL_PRIO_RT,      // hard timing constraints (audio, radio)
} osal_priority_t;
```

Each OSAL backend defines the mapping:

| Level | FreeRTOS (configMAX_PRIORITIES=25) | pthread (Linux simulator) |
|---|---|---|
| `IDLE`   | 0 (tskIDLE_PRIORITY)              | `SCHED_OTHER` + `nice 19` |
| `LOW`    | 5                                  | `SCHED_OTHER` + `nice 10` |
| `NORMAL` | 12                                 | `SCHED_OTHER` + `nice 0`  |
| `HIGH`   | 18                                 | `SCHED_RR` priority 10 (best-effort if non-root) |
| `RT`     | 23                                 | `SCHED_RR` priority 50 (best-effort if non-root) |

The mapping is the implementation's choice — apps must never depend on the underlying integer. Two consecutive symbolic levels are guaranteed to be schedulable distinctly (no two map to the same backend value).

**Apps declare their priority in `duneos.yaml`:**

```yaml
name: wifi_daemon
priority: high          # default: normal
```

The supervisor reads this at launch and passes it to `osal_task_create_static`. **There is no runtime API to change priority.** If an app needs different priority for different threads, it creates each `osal_task` with the right level.

`RT` requires the `DUNEOS_PERM_RT` permission in the manifest (added to the permission bitmask). Apps without it are clamped to `HIGH`. Reason: an RT task that busy-loops can starve the kernel.

## Consequences

- Apps are portable across FreeRTOS and the simulator without source changes; the priority number a developer writes never appears in code.
- The supervisor itself runs at a priority higher than any app can declare (defined per-port; on FreeRTOS = `configMAX_PRIORITIES-1`, above `RT`). This prevents apps from blocking the supervisor.
- No runtime priority changes means the API has fewer corner cases (priority inversion handling is left to the platform's mutex implementation, which is the right place).
- 5 levels is enough for the kinds of workloads DuneOS targets (one or two RT tasks, a few HIGH for input/UI, the rest NORMAL). Adding more later is non-breaking (`OSAL_PRIO_VERY_HIGH` between HIGH and RT).
- On the Linux simulator, `SCHED_RR` requires `CAP_SYS_NICE` (root or capability). Without it, `HIGH` and `RT` fall back to `SCHED_OTHER` and the test runner is informed via a klog warning at startup. Accept that simulator timing is not RT-accurate.

## Alternatives

- **Raw integer (FreeRTOS-style 0..N)** — rejected. Couples app manifests to FreeRTOS' configuration; not portable.
- **More levels (8-10)** — rejected. Hard to remember when to use what; granularity buys nothing for the kinds of apps we ship.
- **Runtime priority mutability** — rejected. Adds API; introduces permission/inversion concerns. Re-add if a real need appears, behind a permission bit.
- **POSIX scheduling policies exposed verbatim** — rejected. Couples app code to UNIX semantics that bare-metal targets don't have.
