# ADR 038 — Userspace logging: `dlog` SDK + `logd` daemon

**Status:** Accepted · 2026-06-14

## Context

DuneOS already has `klog` — a kernel-side ring buffer, the equivalent of
`dmesg`, read with the `klog` tool. It is deliberately kernel-only: driver
init, VFS mounts, the supervisor, panics.

Userspace had nothing shared. Apps that needed to record anything poked ad-hoc
files — `telnet_shell` wrote start-up errors to `/tmp/telnet.err` so a user
could `cat` them after a failed boot. That worked, but it doesn't generalise:
no convention for *where* logs live, no timestamps/levels, nothing bounding
growth, and `/tmp` is RAM-backed and lost on reboot.

We want the userspace counterpart of `klog`: a shared, file-backed log every
app can write to, that survives a reboot when possible, and that can never fill
the storage. The CardPuter has no PSRAM, so a RAM-resident log buffer is
expensive; and the internal flash (the `sysbin` LittleFS) is small and shared
with the app partition, so unbounded writes there are unacceptable.

## Decision

Three pieces, none of which touch the kernel ABI.

### 1. `dlog` — the SDK logger (`sdk/log/dlog.c`, `<duneos/dlog.h>`)

Pulled per-app like the regex engine ([036](036-regex-engine.md)):

```yaml
sources:
  - $SDK/log/dlog.c
permissions: 96          # FS_READ (32) | FS_WRITE (64)
```

API: `dlog_open(app_name)`, `dlog(level, fmt, …)` with `DLOGE/W/I/D` macros,
`dlog_close()`. Each line is `[<sec>.<ms>] <L> <app>: <message>` (monotonic
clock, level `E/W/I/D`).

Two design choices matter:

- **Backend by `stat("/sd")`.** The VFS only registers `/sd` when FatFS mounts,
  so a successful `stat("/sd")` means an SD card is present. `dlog` logs to
  `/sd/log/<app>.log` when it is (space, and spares the internal flash from
  wear), and falls back to `/log/<app>.log` on the LittleFS root otherwise. No
  kernel query, no new VFS — it rides the root-FS layout
  (`/`, `/sd`, `/tmp`, `/dev`).
- **Open-append-close per line.** `dlog` holds no fd across calls, so `logd`
  can rename/rotate the file underneath it at any moment without a race. The
  cost (an `open`/`close` per line) is irrelevant at log volumes. Buffers are
  on the stack — a captured app handing a static-BSS pointer to `write()` would
  trip the kernel's pointer validation.

### 2. `logd` — the housekeeping daemon (`apps/system/logd`)

Owns the active log dir and keeps it bounded, out of band so writers never
block:

- **per-file cap**: rotate `<app>.log` → `<app>.log.1`, one generation kept;
  the writer recreates `<app>.log` on its next line.
- **directory cap**: drop `.1` rotations oldest-first until under budget.

Caps are backend-aware: the tiny shared `sysbin` flash gets small budgets
(32 KiB file / 64 KiB dir), an SD card gets the MB range (128 KiB file / 4 MiB
dir) so logs keep real history. It scans on an interval (default 30 s) and
snapshots the directory listing before mutating it (never renames/unlinks while
a `readdir` iterator is live). Overridable via `/etc/logd/config.yaml`
(`file_cap_kb`, `dir_cap_kb`, `interval_s`); omit a key to keep the backend
default. Runs as an init service, `restart: always`.

**Kernel-log persistence.** When (and only when) an SD card is mounted, `logd`
also tails `/dev/klog` into `/sd/log/kernel.log`. `/dev/klog` is a cursor — each
`read()` returns only what was appended since the last — so holding the fd open
and draining each tick mirrors the kernel's 16 KiB dmesg ring into a real file
that survives reboot and grows well past the ring. It is just another managed
`*.log` (rotated, quota-bounded, readable via `logs kernel`). Persisting to the
small internal flash isn't worth the wear, so this is SD-only; the first drain
captures the boot history (the cursor opens at the oldest entry).

### 3. `logs` — the reader (`apps/system/bin/logs`)

The `dlog` counterpart of `klog`: `logs` lists the log files with sizes,
`logs <app>` prints one (`.log` optional). Resolves the same backend dir.

## Consequences

- Clean split: `klog` = kernel dmesg, `dlog`/`logs` = userspace syslog. No
  shared buffer, no kernel coupling.
- Logs persist across reboot whenever storage allows (SD, or flash) — crash
  post-mortems survive, unlike the old `/tmp` approach.
- Growth is bounded by `logd` regardless of how chatty apps are; the writer is
  decoupled from rotation, so a slow/dead `logd` only stops *housekeeping*, not
  logging (the per-file cap is then the only limit until it returns).
- `telnet_shell` is the first consumer — `/tmp/telnet.err` is gone, replaced by
  `dlog`/`logs telnet`.
- No `DUNEOS_ABI_VERSION` bump: everything is userspace + existing exports.

## Alternatives considered

- **Dedicated RAM `logfs` VFS (dmesg-for-userspace).** Zero flash wear, but
  costs the scarce no-PSRAM RAM and is lost on reboot, and is a new kernel VFS
  with subdir support to write. Rejected for an MCU that wants persistence.
- **A `/var/log` redirect VFS** for a literal POSIX path independent of the
  SD/flash choice. A passthrough VFS must track fd mappings — non-trivial and
  fiddly — for cosmetics. Rejected: a real browsable dir (`/sd/log` or `/log`)
  plus the `logs` tool is honest and lighter.
- **Daemon in the write path (apps send lines to `logd` over IPC).** More
  central control, but makes `logd` a logging bottleneck and a single point of
  failure. Rejected: housekeeping belongs out of band (like `logrotate`), not
  in every `write`.
