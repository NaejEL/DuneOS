# ADR 027 — Ambient system state via tmpfs (`/tmp/state/*`)

**Status:** Proposed · 2026-06-07

## Context

Several pieces of UI want to display *ambient system state* that they do not
own: a status bar shows battery %, WiFi SSID/RSSI, and the clock; a keyboard
modifier indicator shows whether Fn/Shift/Opt are locked. The state is produced
by one component (battery backend, `wifi` app, `kb_iomatrix`, an RTC) and read by
many (`launcher`, `g_shell`, `i2cscope` — but *not* the games, which want the
full panel).

Two structural questions:

1. **Who composites the bar?** DuneOS draws to a single SPI panel with no layers.
   Two processes writing `/dev/disp0` concurrently race on the SPI CS (already a
   documented footgun — see CLAUDE.md "Hard-Won Lessons"). A dedicated status-bar
   *daemon* that owns the top rows would need a real compositor, which contradicts
   the "UI lives in userspace, kernel stays minimal" line ([[ADR-009]]).
2. **Where does the state live** so a reader gets it cheaply without coupling to
   the producer's internals or paying for a second always-running daemon (RAM is
   the binding constraint on the no-PSRAM CardPuter — [[ADR-025]], [[ADR-008]])?

## Decision (proposed)

**Producers publish their state as small files under `/tmp/state/`; consumers
read those files. No status-bar daemon, no shared framebuffer.** Rendering stays
with whichever app is foreground: a libui widget (`ui_statusbar_top`) reads the
files and draws the bar, and each app *opts in* by calling it. Only one app draws
at a time, so there is no display race.

This is the Linux `/run` + `/sys` model: ephemeral, well-known paths that name
live system state, readable by anyone, owned by the producer. It is the same
decentralised, declarative shape already used for capabilities, icons, and file
associations ([[ADR-015]], [[ADR-023]], [[ADR-026]]).

### The convention

```
/tmp/battery         battery_info_t   (grandfathered — see below)
/tmp/state/wifi      ambient_wifi_t   state + RSSI dBm + SSID
/tmp/state/kbd       ambient_kbd_t    fn/shift/opt lock bitmask
```

- `/tmp` is tmpfs ([[ADR-005]] path conventions) — writes never touch flash, so a
  producer updating RSSI every few seconds costs nothing and does not survive a
  power cycle (correct: ambient state is not persistent config).
- **Format is a small fixed binary struct, not text.** A single `write()` of a
  struct this size is effectively atomic on tmpfs, so readers (single
  `open`/`read`/`close`) never need locking and a torn read can't happen. The
  shared structs + paths live in one header, `<duneos/ambient.h>` (in the SDK's
  always-on include dir, next to the device ABIs like `battery_ioctl.h`), so a
  producer and a consumer can never disagree on layout. Text was considered (the
  `/sys` aesthetic) but binary matches the existing battery snapshot and needs no
  parser.
- A reader that finds no file renders that slot as absent (e.g. no WiFi glyph
  when `wifi` was never started). Missing file = feature off.
- **Battery is grandfathered at `/tmp/battery`** as a `battery_info_t`: the
  snapshot and its readers predate this ADR. It keeps that path (not
  `/tmp/state/battery`) rather than break existing readers; new signals live
  under `/tmp/state/`.

### Producers

- **battery**: `battery_daemon` (I2C gauges) or `battery_pub` (kernel-served ADC,
  e.g. CardPuter — bridges `/dev/battery0` → `/tmp/battery`). Exactly one runs per
  board; the consumer never knows which backend produced the snapshot.
- **wifi**: the `wifi` app ([[ADR-026]] companion, contest roadmap) writes
  `ambient_wifi_t` on association / RSSI poll / disconnect.
- **kbd**: `kb_iomatrix` writes `ambient_kbd_t` whenever a modifier lock toggles.
  The lock *state itself* also lives in `kb_iomatrix` (it is what rewrites the
  keymap when Fn is latched); the file is just its published view.

### Consumers

- `ui_statusbar_top(ui, title)` in libui (`sdk/ui/ui_statusbar.c`) reads each file
  every draw and paints a one-row top bar: title left, WiFi/battery/locks/clock
  right. Cheap — a handful of small struct reads, no allocation. The clock comes
  straight from `clock_gettime(CLOCK_REALTIME)` and shows only once that clock is
  set (needs SNTP/RTC, which arrives with the WiFi work).
- Any app can read a single file directly (e.g. an app that only cares about
  battery) without the widget.

## Consequences

- **No extra always-on daemon** for the bar — it is a library linked only by apps
  that want it. Matters directly for the RAM budget ([[ADR-025]]).
- **No display compositor / no CS race** — exactly one app draws the panel.
- Apps opt in per-screen: the launcher and tools show the bar; games call
  `game_open` and never invoke it, keeping the full panel.
- No locking: a single-`write()` binary struct is atomic at these sizes, so a
  torn read can't occur. Documented so nobody adds a mutex the design doesn't need.
- The `/tmp/state/` namespace is a new, soft contract. New ambient signals add a
  file; they do not touch the kernel or any existing producer/consumer.
- Not persistent by construction. Anything that must survive reboot (known WiFi
  networks, user prefs) is *config*, not state, and lives in `/flash/etc/...`
  ([[ADR-005]]) — a deliberate split.

## Alternatives considered

- **Status-bar daemon owning the top rows (compositor).** Rejected: needs display
  layering/clipping the kernel doesn't have; concurrent `/dev/disp0` writers race
  on SPI CS; adds a permanent RAM resident. Re-examine only if a true compositor
  lands (post-contest, not planned).
- **Kernel-drawn status bar.** Rejected: pushes UI and device-state polling into
  the kernel, against [[ADR-009]].
- **A `/dev/state` or sysfs-like kernel node.** Heavier than files for no gain at
  this scale; revisit if a producer needs `poll()` wakeups instead of the widget
  re-reading each frame.
- **IPC (mailbox) push to subscribers.** Couples producers to consumers and needs
  every reader to run a task with a mailbox. Files decouple them entirely.

## Cross-references

- [[ADR-005]] — path conventions (`/tmp` tmpfs vs `/flash/etc` config split).
- [[ADR-009]] — kernel/userspace boundary (UI and polling stay in userspace).
- [[ADR-015]] — declarative, decentralised metadata (same shape).
- [[ADR-019]] — Linux-faithful semantics (the `/run`+`/sys` model).
- [[ADR-023]] / [[ADR-026]] — icons / file associations (declare-and-resolve peers).
- [[ADR-025]] / [[ADR-008]] — RAM-limited concurrency: why "no extra daemon" matters.
