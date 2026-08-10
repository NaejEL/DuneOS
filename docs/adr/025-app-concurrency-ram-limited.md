# ADR 025 — App concurrency is RAM-limited (dynamic grow-only slot pool)

**Status:** Accepted · 2026-06-07

## Context

The supervisor tracked running apps in a fixed static array of
`DUNEOS_MAX_RUNNING_APPS` (4) `app_slot_t` entries. Launching a 5th app failed
with "no free app slots" **regardless of free memory**.

Four is arbitrary and too low for real use: the contest image alone runs two
input/console daemons (`kb_iomatrix`, `usb_shell`) plus the launcher, which is
3 slots before a single app is launched; launching an app makes 4; that app
launching a child (e.g. `g_shell` running a command) needs a 5th — denied. With
more daemons coming (WiFi, Bluetooth, …) the ceiling is hit immediately. The
limiting resource on a no-PSRAM board is RAM, and it should be the *only* limit,
not a hand-picked count.

A slot's `app_slot_t` struct is small (~400 B). The real per-app cost — the
exec-pool `.text`, data pool, heap pool, and stack — is allocated at launch and
already fails gracefully on OOM. So the fixed array capped concurrency far below
what memory actually allows.

## Decision

**App slots are a dynamic, grow-only pool; concurrency is limited only by RAM.**

- `slot_alloc()` reuses an inactive slot, or `malloc()`s a new one when none is
  free. The only failure is out-of-memory (the slot struct itself, or — far more
  likely first — the app's heap/exec/data allocation).
- **Slots are never freed.** An exited app's slot is marked inactive and reused;
  the pool only grows to the high-water mark of concurrent apps. This is
  deliberate, not laziness — see below.
- `DUNEOS_MAX_RUNNING_APPS` (now 16) is **not** a hard cap. It only sizes the
  exit queue/semaphore and the default `list_slots` buffer — a soft hint.

### Why grow-only / never-freed

The crash path matters. When an app faults, the exception handler runs **in the
faulting app's context** and must find its slot to record the crash and tear it
down. That lookup is **lock-free** (you cannot take a mutex from an exception
handler). With a static array, a lock-free walk is always safe — entries never
move or disappear. A linked list that `free()`s slots would let the supervisor
free a node on one core while the exception handler walks the list on another →
a dangling `next` → a second fault inside the fault handler.

Keeping slots allocated forever preserves the static array's safety property
(stable addresses, nothing disappears) while lifting the count limit. New nodes
are linked at the head with a **single atomic pointer write**, so a concurrent
lock-free walker observes either the old list or the fully-initialised new node
— never a half-linked one.

## Consequences

- The supervisor scales with RAM: daemons + launcher + nested launches work
  until memory genuinely runs out, at which point the launch fails cleanly
  (`-ENOMEM`) instead of hitting an arbitrary wall.
- Pool memory is bounded by peak concurrency, not by every-launch churn (~400 B
  per high-water slot, never reclaimed — negligible).
- No DoS hardening regression: a runaway spawner is bounded by OOM, consistent
  with the advisory-permission model ([[ADR-011]]) — slots were never a security
  boundary.
- Phase 26 (OSAL, [[ADR-002]]) will move the `malloc`/atomic-link/`xQueue` here
  behind `osal_*`; the grow-only-pool *shape* stays, only the primitives change.

## Alternatives considered

- **Bigger static array (e.g. 16/32):** still an arbitrary cap, and wastes the
  struct memory whether or not the apps ever run. Rejected — doesn't address the
  principle.
- **Linked list with free-on-exit:** smallest footprint, but a freed node can
  dangle the lock-free exception-context walk. Rejected as unsafe.
- **Static array of pointers to malloc'd slots:** the pointer array is still a
  fixed cap. Rejected.

## Cross-references

- [[ADR-008]] — memory strategy (RAM is the real budget; this makes the slot
  layer defer to it).
- [[ADR-002]] — OSAL (Phase 26 abstracts the supervisor's FreeRTOS/heap calls).
- [[ADR-011]] — threat model (permissions are advisory; OOM, not a slot count,
  bounds a misbehaving spawner).
