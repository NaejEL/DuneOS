# ADR 030 — Frugal WiFi memory budget on no-PSRAM boards

**Status:** Accepted · 2026-06-08

## Context

The `free` consumption breakdown (ADR 008 tooling) measured where the CardPuter's
~288 KiB of heap-managed DRAM goes once WiFi is associated:

```
IDF/startup     ~18 KiB
DuneOS kernel  ~46 KiB   (+ 64 KiB exec pool reserved)
services      ~108 KiB   = apps (~39 KiB) + WiFi/lwIP (~70 KiB)
```

**WiFi + lwIP alone is ~70 KiB — the single biggest consumer.** On a 320 KiB
board that competes directly with the app exec pool, per-app memory, and the
contiguous blocks the launcher needs. The default ESP-IDF WiFi/lwIP config is
tuned for throughput, which DuneOS does not need for contest connectivity
(scan / join / light HTTP / SNTP).

Separately, the WiFi library links ~25-30 KiB of hot code into IRAM. On the
ESP32-S3 that IRAM is **D/IRAM SRAM** — the same SRAM the general heap draws
INTERNAL DRAM from. So that code isn't just "flash usage": it permanently
occupies SRAM that could otherwise be heap.

## Decision

**Cut the WiFi/lwIP footprint hard, project-wide (`sdkconfig.defaults`), trading
throughput for RAM.** Two levers:

1. **Move WiFi code IRAM→flash** (`CONFIG_ESP_WIFI_IRAM_OPT=n`,
   `CONFIG_ESP_WIFI_RX_IRAM_OPT=n`). Returns ~25-30 KiB of D/IRAM SRAM to the
   general heap as INTERNAL DRAM — the biggest single lever for the launcher's
   contiguous-block problem ([[ADR-008]]). Cost: WiFi runs from cached flash,
   lower throughput.
2. **Trim runtime buffers + lwIP**: static/dynamic RX/TX counts down, AMPDU off,
   cache-TX and mgmt short-buffer counts down; lwIP loses IPv6, runs fewer
   sockets, smaller TCPIP/TCP/UDP mailboxes, and a 2-MSS TCP window
   (`TCP_WND_DEFAULT` / `TCP_SND_BUF_DEFAULT` = 2880).

PSRAM boards can override these upward in their board sdkconfig if they want
throughput; on no-PSRAM this is the default.

## Consequences

- Frees both **SRAM** (IRAM→flash, ~25-30 KiB, contiguous → helps fragmentation)
  and **runtime DRAM** (smaller buffers/lwIP, off the ~70 KiB) — quantify the win
  with the `free` breakdown after a fullclean.
- **sdkconfig change ⇒ requires `idf.py fullclean`** before the values apply.
- Lower WiFi throughput and no IPv6. Acceptable for STA scan/join/HTTP/SNTP;
  revisit per-board if a use case needs bulk transfer.
- A few symbol names are version-sensitive; an unknown CONFIG is ignored by
  kconfgen (warning, not fatal). Verify the generated `build/sdkconfig` actually
  carries them after the fullclean.
- This is a budget cut, not an isolation strategy — it complements the
  measure-don't-reserve principle ([[ADR-008]], [[ADR-029]]): the WiFi stack
  still churns the general heap, but it churns a much smaller share of it.

## Alternatives considered

- **Keep defaults, lean harder on the exec pool / arena.** Rejected: WiFi is the
  biggest single block; not cutting it forces unreasonable cuts elsewhere.
- **On-demand WiFi only** (start from the wifi app, no boot daemon). Still on the
  table as a *complementary* choice (contest roadmap item 4) — the user wants
  WiFi available at boot too, so the footprint cut is what makes coexistence fit.
- **Tune at runtime** via `esp_wifi_set_*`. The buffer pools are init-time; the
  Kconfig is the right surface.

## Cross-references

- [[ADR-008]] — memory fragmentation strategy + the `free`/`ps` visibility that
  quantified the 70 KiB.
- [[ADR-029]] — computed app stacks (the other half of the RAM reclamation).
- [[ADR-013]] — network architecture (lwIP vendoring; Phase 27 rewrite supersedes
  this Kconfig surface).
- [[ADR-025]] — RAM-limited concurrency (why every KiB back to the heap matters).
