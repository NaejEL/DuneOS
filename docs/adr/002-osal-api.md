# ADR 002 — OSAL API: static-storage handles, minimal surface

**Status:** Accepted · 2026-05-19

## Context

Phase 26 introduces `duneos_osal.h` — the abstraction layer between the kernel and whatever scheduler / synchronisation primitives the host platform offers (FreeRTOS today, pthread for the Linux simulator, future bare-metal ports). The risk is well-known: an OSAL that mirrors FreeRTOS verbatim re-creates FreeRTOS; one that mirrors POSIX is hard to implement bare-metal. We want the kernel-side abstraction, not a portable userland.

Two further axes need locking before code is written:

1. **Allocation model for handles.** Tasks, mutexes, semaphores, queues — does the OSAL allocate them internally (`osal_task_t *t = osal_task_create(...)`) or does the caller provide static storage (`osal_task_storage_t mem; osal_task_t *t = osal_task_create_static(&mem, ...)`)? The first is ergonomic but forces every implementation to bring an allocator. The second matches FreeRTOS' `xTaskCreateStatic` family already used by the supervisor (see [Hard-Won Lessons on stack alignment]), and works on no-malloc targets.
2. **Software timers.** Useful (heartbeat, WDT feed) but they're built on tasks + sleep, and every platform implements them differently. Including them in the first OSAL cut multiplies the surface.

## Decision

`duneos_osal.h` exposes the following primitives, **all static-storage for handles** (no internal allocation) and **all returning `int` with `-errno`** per [[ADR-001]]:

| Domain | Functions | Storage |
|---|---|---|
| Task | `osal_task_create_static`, `osal_task_yield`, `osal_task_sleep_ms`, `osal_task_self`, `osal_task_join` | `osal_task_storage_t` (caller-provided, sized for TCB + stack) |
| Mutex | `osal_mutex_init_static`, `osal_mutex_lock`, `osal_mutex_trylock`, `osal_mutex_unlock`, `osal_mutex_destroy` | `osal_mutex_storage_t` |
| Semaphore | `osal_sem_init_static`, `osal_sem_take`, `osal_sem_give`, `osal_sem_destroy` | `osal_sem_storage_t` |
| Queue | `osal_queue_init_static`, `osal_queue_send`, `osal_queue_recv` | `osal_queue_storage_t` + caller-provided item buffer |
| Memory | `osal_mem_alloc`, `osal_mem_free` | — (the only place the OSAL touches the heap) |
| Time | `osal_monotonic_us` (u64), `osal_task_sleep_ms` | — |
| Diagnostics | `osal_panic_print(const char *fmt, ...)` | — |

No software timers in v1. Callers needing timers build them from a task + `osal_task_sleep_ms` + queue. A separate ADR may add `osal_timer_*` later, with measurable need.

No `_dynamic` variants. If a port truly needs malloc-allocated handles, it can wrap `osal_*_create_static` with a heap-allocated storage block — this is a library decision, not an OSAL one.

`osal_panic_print` is a last-resort output usable from exception/WDT/stack-overflow handlers. Implemented by `esp_rom_printf` on ESP32, `fprintf(stderr,...)` on the Linux simulator. Never call `klog` from a crash context.

## Consequences

- The supervisor's `xTaskCreateStaticPinnedToCore` migrates directly to `osal_task_create_static` — same model, different vocabulary. No new alignment/lifetime hazards.
- Every `_static` call requires the caller to size and align the storage buffer. We provide `OSAL_TASK_STORAGE_SIZE(stack_bytes)` macros to compute it, hiding the per-platform TCB size.
- No allocation in OSAL hot paths means [[ADR-008]] (memory fragmentation strategy) gets its "static-first" pillar essentially for free.
- The pthread simulator needs a slightly more verbose implementation (caller-storage that internally wraps a `pthread_t`+`pthread_attr_t`+stack — pthreads don't natively expose static-storage creation), but this is a one-time cost.
- ABI: OSAL types live in `kernel/duneos_kernel/include/duneos/osal.h`. Apps do not see this header — the kernel calls OSAL, apps use POSIX wrappers in `libdune.a`. So an OSAL change does not bump the app ABI.

## Alternatives

- **Opaque pointer with internal allocation** — rejected. Forces every backend to bring an allocator (problematic on no-malloc bare-metal); creates hidden runtime allocation in supervisor/init paths, against [[ADR-008]].
- **Hybrid (both static and dynamic variants)** — rejected. Doubles the API surface to maintain and test; adds no capability we can't already build by composing static + a caller-side allocator wrapper.
- **POSIX subset (`pthread_*`, `sem_*`)** — rejected for kernel use. POSIX semantics force memory allocations behind the scenes (`pthread_create` mallocs the stack on most implementations); also blocks bare-metal ports.
- **Copy FreeRTOS verbatim (`x*`/`v*` naming, tick types)** — rejected. Re-creates FreeRTOS; defeats the abstraction.
