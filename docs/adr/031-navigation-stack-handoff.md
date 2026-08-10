# ADR 031 — Navigation-stack handoff (parent exits while child runs)

**Status:** Accepted · 2026-06-09

## Context

The launcher launched a child app and stayed resident, blocked on
`wait_for_completion`. So while a game ran, the device held **launcher + game +
daemons + WiFi** at once. On the no-PSRAM CardPuter that is unaffordable:

- Measured: with the launcher resident, the largest contiguous free block is
  ~10-18 KiB. A RAM-hungry app can't get its block — `tetris`' 32 KiB canvas
  heap pool failed to allocate (ran on the global heap, fragile and prone to
  freeze under pressure); `waves` failed to even load (`data pool alloc failed
  (18320 B)`).
- The launcher itself is dead weight while the child runs — it only waits.

Freeing the launcher's ~26 KiB by hand isn't enough either: its data pool,
stack, and heap pool are three separate allocations, so freeing them leaves
three holes, not one contiguous block. The fix has to remove the launcher
entirely while the child runs.

## Decision

**A parent app hands the device — and its RAM — to a child, then exits. The
supervisor relaunches the parent when the child exits.** A navigation stack of
full-screen apps where only the top is resident.

```c
int duneos_supervisor_chain(const char *child_path);   /* then duneos_exit() */
```

Flow (launcher → game → launcher):

1. The launcher calls `duneos_supervisor_chain(game)` (records `chain_child` on
   its slot), releases the display/input, and `duneos_exit()`s.
2. The supervisor frees the launcher's slot — **its RAM is gone before the child
   loads**. Seeing `chain_child` set, it suppresses the restart policy (the
   launcher has `restart: always`; a handoff exit must not auto-restart it),
   launches the child in the freed space, and arms a watch `{child → launcher}`.
3. When the child exits — cleanly or by crash — the watch fires and the
   supervisor relaunches the launcher (with `restart: always`, fresh).

The launcher persists its highlighted app to `/tmp/.launcher_sel` before handing
off and restores it on relaunch, so the round trip feels seamless.

### Scope / limits

- **Depth-1** watch (one `s_chain_watch_name`/`s_chain_launch_path`). Nesting a
  chain inside a chain overwrites it — fine for launcher/shell → app, which is
  the only use. A deeper stack can be added if a child ever needs to chain too.
- Robust to child crashes: any exit of the watched child (clean, WDT, stack
  overflow) triggers the relaunch, so the user always returns to the launcher.
- The parent is relaunched with `restart: always` so it stays crash-resilient.

## Consequences

- Peak memory drops from `daemons + launcher + child` to `daemons +
  max(launcher, child)`. The child gets the **contiguous** block it needs
  (tetris' 32 KiB canvas, waves' 18 KiB data pool) because the launcher's
  footprint is gone, not just freed-in-pieces.
- The launcher is no longer a `wait_for_completion` blocker; its own code
  shrank (its stack auto-sized down).
- Makes a **small, right-sized app arena** viable next ([[ADR-008]]/[[ADR-025]]):
  with only one foreground app at a time, an arena sized for that one app's
  heap/data is enough to guarantee its contiguous block — the 96 KiB arena
  failed precisely because apps stacked.
- A handoff exit is intentional: excluded from the restart policy and the
  circuit breaker.
- Display/SPI must be fully released by the parent before exit (it is) so the
  child can claim `/dev/disp0`.

## Alternatives considered

- **Keep the launcher resident (status quo).** Rejected: forces launcher+child
  coexistence, which doesn't fit.
- **Pure userspace** (launcher forks/waits). No fork, no MMU; and the launcher
  must be *gone* (not blocked) to free RAM — only the supervisor can relaunch it
  after the child. Hence the kernel `chain` primitive.
- **Thread the return path through the launch call.** A name-matched watch (like
  the `after:` observer) is simpler and needs no new launch signature.

## Cross-references

- ADR 016 — captured-app exit (spawned vs captured; the handoff child is spawned).
- [[ADR-008]] / [[ADR-025]] — memory fragmentation + RAM-limited concurrency: why
  removing the resident parent matters, and the small-arena follow-up.
- [[ADR-029]] — computed stacks (the launcher's stack shrank further here).
