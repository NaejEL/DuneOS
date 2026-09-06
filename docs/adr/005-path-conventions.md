# ADR 005 — Path conventions: `/flash` mandatory, `/sd` optional, `/proc` reserved

**Status:** Accepted · 2026-05-19

> **Note added 2026-09-06 — the literal paths below have changed; the decision has not.**
> The flash filesystem mounts at the **root**, not at `/flash` (`vfs.c`: `FLASH_MOUNT_POINT ""`),
> so the real paths are `/bin`, `/sd/bin`, `/sd/apps` and `/init.yaml` → `/sd/init.yaml`.
> `/flash` survives as the mount's registry name (`FLASH_MOUNT_NAME "/"`). The Context below is
> left as written: it records what was true when the decision was taken.

## Context

DuneOS today mounts `/flash` (LittleFS in the `sysbin` partition), `/sd` (FatFS on a hardware SD slot), `/tmp` (heap-backed tmpfs), and `/dev` (devfs). The kernel boots from flash even without an SD card (Phase 19). Apps are loaded from `/flash/bin/` → `/sd/bin/` → `/sd/apps/`, init.yaml from `/flash/init.yaml` → `/sd/init.yaml`.

Future ports raise questions:

- RP2040 (Phase 29) typically has no SD slot. What happens to `/sd`?
- Linux simulator (Phase 26) has none of these as physical devices; it fakes them with directories.
- An app that hardcodes `fopen("/sd/config.txt", ...)` will silently fail on a board where SD is absent.
- Future subsystems (process info, runtime stats) may want a `/proc`-like surface. Reserving the prefix now prevents apps from squatting on it.

## Decision

**Mandatory mount points** — kernel boot fails if any of these is unavailable:

- `/flash` — read/write LittleFS in the `sysbin` partition. Source of truth for boot services and embedded apps.

**Optional mount points** — present only if the board declares the corresponding capability in `board.yaml`:

| Path | Capability | Behaviour when absent |
|---|---|---|
| `/sd` | `has_sd: true` in `board.yaml` (default `true` on boards with a slot, `false` otherwise) | `stat("/sd", ...)` returns `-ENOENT` |
| `/dev` | always present in current kernels; reserved as optional for ultra-minimal future ports | unlikely in practice |
| `/tmp` | always present (RAM tmpfs); cheap enough to keep universal | unlikely in practice |

**Reserved prefixes** — apps must not create files or mounts under these:

- `/proc` — reserved for a future kernel-introspection surface (process list, memory map, sched stats).
- `/sys` — reserved for runtime configuration knobs (Linux-style `sysfs`).
- `/dev` — kernel-managed devfs; apps can `open()`, never `mkdir()` or `creat()` directly.

Apps that want to be portable across boards **must feature-detect optional mounts**:

```c
struct stat st;
const char *cfg = (stat("/sd/config.yaml", &st) == 0) ? "/sd/config.yaml"
                                                      : "/flash/config.yaml";
```

Apps must not hardcode `/sd` in their `duneos.yaml` `sources:` or runtime paths without a fallback. The kernel logs a warning at first app launch when an app's manifest references `/sd` paths but the board has `has_sd: false`.

## Consequences

- Apps gain a stable contract: `/flash` is always there, anything else is conditional.
- The supervisor's launch logic stays simple — it tries `/flash/bin/<name>.dap` first, then `/sd/bin/`, then `/sd/apps/`, in that order. Missing intermediate paths are not errors.
- The Linux simulator implements `/flash` as a directory under the build tree (e.g. `build/sim/flash/`). `/sd` is implemented if the user provides `--sim-sd <dir>`, otherwise absent. Apps written portably work on the simulator unchanged.
- Reserving `/proc` and `/sys` costs nothing today and prevents a painful rename in Phase 30+.
- Mount-point semantics are independent of the underlying filesystem implementation (Phase 27 native VFS rewrite). The conventions documented here outlive any specific VFS backend.

## Alternatives

- **Mandate `/sd` always present (require SD slot on every board)** — rejected. RP2040 Pico has no SD; CardPuter sometimes runs sans SD; the kernel already supports flash-only boot (Phase 19).
- **No reserved prefixes — let apps use what they want** — rejected. Costs nothing to reserve now, painful to reclaim later. Linux's `/proc`/`/sys` precedent is well-known.
- **Per-app namespaces (`/app/<name>/...`)** — rejected as primary. Adds complexity; can be layered on later if apps need private writable space (today they use `/tmp/<name>/`).
