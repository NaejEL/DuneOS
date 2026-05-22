# DuneOS — backlog

A holding pen for ideas and gaps that surfaced during planning but aren't on the roadmap yet. Not a commitment; a memory aid. Move an item to `ROADMAP_v2.md` or a new ADR once you decide to do it.

Items are grouped by impact, not by phase. Each line is one sentence — if it grows, it's probably ready to become a roadmap entry.

---

## High impact (likely to be scheduled later)

- **Kernel resilience under app-crash storms** — observed 2026-05-22 on CardPuter with a misconfigured init.yaml (test_crash + g_shell + splash running concurrently): the per-app Xtensa exception handler catches individual crashes, but a tight loop of restart→crash→restart eventually takes the device down (kernel reboot). Per-app circuit breaker exists (3 crashes / 30 s → restart disabled) but doesn't help when *different* apps each crash once and the supervisor task is starved processing exits. Hypotheses to investigate when we hit this again: (1) FreeRTOS ready-list races on Core 0 between supervisor task + dying app, (2) heap fragmentation as crashed-slot heaps get re-allocated faster than they're freed, (3) SPI device handle refcount edge case when an app crashes mid-transaction. Needs ADR + Phase 20 follow-up. **Userspace must not be able to hard-crash the kernel — that's an MMU-less OS's only safety promise.**
- **OTA / kernel update story** — today the kernel is updated via `dbt flash kernel` (USB UART/JTAG); no A/B partitions, no in-system updater, no WiFi push. Blocks non-dev adoption. Likely a phase of its own once Phase 25 (`dbt system`) ships, because `dbt system deploy` is the natural carrier.
- **Persistent coredumps in `/flash/crash/`** — today `klog` is a 4 KiB RAM ring buffer lost at reboot, and the supervisor exception handler dumps via `esp_rom_printf` (visible only if console is connected). ESP-IDF has `esp_core_dump` that targets a dedicated partition; could be wired in. Could absorb into Phase 20 (Memory hardening), which is already 🟡 PARTIAL.
- **App debugging story** — JTAG via VS Code ESP-IDF works for kernel devs, but app authors get hex register dumps when their `.dap` crashes. Symbolic stack trace (using `addr2line` against the `.elf` left on disk by `dbt build`) would be a big DX win. Could be a `dbt trace <coredump.bin>` subcommand. Depends on persistent coredumps existing first.

## Moderate impact

- **libimage_png — full PNG decoder** — Phase 25.5 ships only the `.dr` raw RGB565 format (8-byte header + pixels), generated host-side by `dbt img convert`. That's enough for splash and app icons but doesn't help an app that wants to display arbitrary PNG/JPEG content from the SD card. If the need emerges, add `sdk/image/libimage_png.c` linking a minimal PNG decoder (e.g. `picopng`, ~3 KB, no zlib) for grayscale + RGB; full RGBA+IDAT needs zlib. JPEG: ESP-IDF already ships `jpeg_decoder` — could be re-exported as a kernel symbol. Userspace lib-per-format is the goal — libgfx stays mince.
- **App writable state** — Phase 25.5 standardised read-only config at `/etc/<app>/config.yaml` (provisioned by `dbt flashimg`). For state that the app *itself* writes (last brightness, recent files, prefs), we still have no API. Options: `/flash/var/<app>/state.yaml` convention with a libdune helper; or `/dev/nvs0` for fast key/value. The first is portable, the second is faster.
- **Event bus / pub-sub** — current IPC is point-to-point via supervisor mailbox queues. Broadcast events (`battery_low`, `wifi_disconnected`, `usb_plugged`) would need a different mechanism. Defer until a concrete need emerges; not worth designing in the abstract.
- **Time-of-day / NTP-SNTP** — `clock_gettime(CLOCK_MONOTONIC)` works (it's the hardware timer); `CLOCK_REALTIME` returns whatever happens to be in the kernel's notion of wall clock (probably epoch). `wifi_daemon` doesn't do SNTP. Trivial to add once it matters; not blocking anything.
- **Kernel version exposure** — `DUNEOS_VERSION_STRING` is printed in klog at boot but isn't queryable at runtime. A `dbt info-device` that talks to the kernel over `/dev/ttyUSB0` would be useful for support ("what kernel are you running?"). Tiny scope; opportunistic.
- **`cd apps/user/<name> && python ../../../tools/dbt.py build`** is awkward after the `apps/system,user` split (one extra `../`). Either `dbt build <app_path>` from the repo root, or a `dbt` wrapper that walks up to find the repo root, would be cleaner. Tiny DX win.
- **Shell default cwd `/sd` is wrong when no SD is mounted** — `apps/system/shell_core/shell_core.c:31`, `shell_cmds.c:81` (`cd` no-arg), and `g_shell.c:56` all hardcode `/sd`. With SD absent, the user lands in a non-existent dir and `ls` returns ENOENT. Fix: at shell startup, `stat()` the mount points and fall back to `/flash` if `/sd` is missing — same logic for `cd` no-arg. Touches 3 files + a small helper.

## Low impact / niche

- **Atomics, rw-locks, barriers** in `libdune.a` — POSIX `<stdatomic.h>` would be cheap to add (PicoLibc has it ; libdune just needs to expose). `pthread_rwlock_*` and `pthread_barrier_*` similar. Add when a real caller asks.
- **Multi-file apps / dependencies** — every `.dap` is self-contained today. If a user wants to share `libfoo.c` across three apps, they `#include` it in each manifest's `sources:`. Linkage with shared libraries (Phase 33 "Shared Libraries `.dsl`" study) is the answer but the study hasn't started.
- **Filesystem features** — `fsync()` isn't exposed (data-integrity risk if a power-cut interrupts a write); no per-app quota (a buggy app can fill `/sd`); no symlinks even on LittleFS (it supports them, we don't expose). Each is a small standalone task.
- **i18n / keyboard layouts** — the keymap is hardcoded per board (CardPuter has French AZERTY in code today). Multiple layouts would mean a runtime-selectable keymap; not worth it until there are multiple users on different layouts.
- **ABI churn policy** — we've bumped to v3; every bump forces user apps to rebuild. No "stable ABI promise" beyond CLAUDE.md prose. Could formalise: "ABI is unstable until v1.0; until then expect a bump per phase that touches `duneos_api_t` layout." Tiny doc, not a phase.

## Out of scope / probably never

- **Video output (HDMI, MIPI-DSI)** — only ESP32-P4 has the hardware among current targets; Phase 30 (Audio) doesn't extend to video. Wait until a board demands it.
- **Hot reload / live code patching** — Linux doesn't do it ; DuneOS won't either. Apps that need to upgrade themselves call `duneos_restart()` after writing their new `.dap`.
- **Privilege separation via software MPU** — see [ADR 011](adr/011-threat-model.md). Cost/benefit doesn't work on these chips.
- **Container-like sandboxing** — same answer as above plus no `chroot` plumbing exists.
- **Distributed compute / mesh networking primitives** — well outside the manifesto's "Flipper / Playdate sweet spot". Apps that want it use the existing socket layer over WiFi.

---

## How to use this file

- **Adding an item:** write one sentence. If you can't say it in one sentence, it's two items in disguise.
- **Promoting an item:** when an item earns scheduling, move it out (to ROADMAP_v2.md or a new ADR) and delete the line here. The git history keeps the discussion.
- **Removing an item:** if it stops being relevant (e.g. solved by a different design choice), delete the line and add a one-line note in the commit message explaining why.
- **Linking to context:** if a backlog item connects to an existing ADR/Phase/file, link it inline. Don't expand here.
