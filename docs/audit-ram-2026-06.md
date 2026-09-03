# Kernel RAM audit — DRAM / IRAM (CardPuter, June 2026)

Audit carried out on 2026-06-09 on the `feature/contest-2026` branch (post-c296ec9),
that day's build (`build/duneos.map`), board `m5stack-cardputer` (ESP32-S3FN8,
512 KiB SRAM, no PSRAM). No modification applied — this document lists the
findings and the recommended actions, prioritised by gain/risk ratio.

S3 reminder: IRAM and DRAM are the same physical SRAM (D/IRAM) seen through two
buses. Every KiB of static IRAM saved becomes heap DRAM again. That is the common
thread of the whole audit.

---

## 1. Measured state

### 1.1 Static image (esp_idf_size on `build/duneos.map`, 2026-06-09)

| Region | Used | Detail | Left |
| --- | ---: | --- | ---: |
| **DIRAM** (334 KiB) | **143,644 B (42 %)** | .text **60,451** + .bss **60,128** + .data **23,065** | **198,116 B** |
| Dedicated IRAM (16 KiB) | 16,384 B (100 %) | vectors 1,028 + .text 15,356 | 0 |
| Flash code | 753,742 B | | |
| Flash rodata | 170,996 B | | |

Reading: ~**193 KiB** of heap are left at boot, and they must hold the arena
(64 K) + exec pool (64 K) + WiFi/lwIP (~70 K post-ADR 030) ≈ **198 K**. The budget
is exactly at break-even — that is why everything passes or breaks within a few
KiB. The two compressible items of the static image:

- **DIRAM `.text` = 60.4 KiB**: kernel/IDF code placed in IRAM, compiled with
  `-Og`. That is the target of the P1 actions (§3.1).
- **`.bss` = 60.1 KiB**: of which klog 16 K (`klog.c:30`), GPIO ring 4 K
  (`drv_gpio.c:22`), DMA FB chunk 4 K (`drv_fb_st7789.c` — **probably absent on
  the CardPuter: `DUNEOS_DRV_FB` depends on `SPIRAM`, off without PSRAM; to be
  confirmed on the `.map`**), tmpfs/devfs/vfs tables ~5 K. Target of the P2
  actions (§3.2).

### 1.2 Kernel runtime reservations

| Item | Size | Source |
| --- | ---: | --- |
| App arena | 64 KiB (`CONFIG_DUNEOS_APP_ARENA_KB=64`) | `supervisor.c:78-98`, dedicated multi_heap, reserved pre-WiFi, binary fallback down to 16 K |
| Exec pool (app .text) | 64 KiB (`CONFIG_DUNEOS_EXEC_POOL_KB=64`) | `loader.c:1043-1070`, `MALLOC_CAP_EXEC`, bump allocator, LIFO reclaim only (`loader.c:1591`) |
| Supervisor stack | 24 KiB | `supervisor.c` (~755) |
| WiFi + lwIP | ~70 KiB on association | measured through `free` (ADR 030 already applied) |

### 1.3 Cost per resident app (daemons included)

| Item | Size | Note |
| --- | ---: | --- |
| `duneos_app_t` | **~4.2 KiB** | of which **4 KiB** of `section_bases[MAX_SECTIONS=1024]` (`loader.c:101,108`) — see P2.1 |
| Mailbox queue | ~1 KiB | 8 × `duneos_msg_t`, created at every launch even without IPC |
| Slot + TCB | ~600 B | slot ~360 B (grow-only, ADR 025) + `StaticTask_t` 232 B |
| Stack | automatic (ADR 029) | 3-8 KiB, watermarks 26-65 % — well sized now |
| Data pool | variable | the app's .data/.bss/.rodata, inside the arena |

---

## 2. Opinion on the arena

**The approach is the right one, keep it.** Four reasons:

1. **Pre-WiFi reservation** = the foreground gets its large contiguous blocks
   (tetris canvas 20 K, waves data pool 18 K) whatever the fragmentation state of
   the general heap. It is the only guarantee possible without an MMU.
2. **ESP-IDF's `multi_heap` is a TLSF** (since IDF 4.3). Pillar 3 of ADR 008
   ("TLSF in the app pool") is therefore **already covered de facto** — the
   allocator of the arena and of the per-app heap pools is already O(1) with low
   fragmentation. Recommendation: update ADR 008 to record pillar 3 as closed,
   rather than embedding a second TLSF in `libdune.a`.
3. **Free-on-exit = implicit defrag** (pillar 2), reinforced by the ADR 031
   handoff which bounds occupancy to daemons + 1 foreground.
4. The binary fallback at boot (64→32→16 K) degrades cleanly instead of failing.

**But the arena is blocked today — not by its design, by the loader.** Re-enabled
at 64 K, the launcher fails to load (`ESP_ERR_NO_MEM`, measured 2026-06-09): not
for want of room for its pools (data 12.8 K < 38 K free in the arena), but because
**reading the `.dap` headers requires ~28 K contiguous in the general heap** which
the arena has just reduced (`largest` down to 27.6 K). That is the **P0** lock
(§3); once lifted, the four reasons above hold and the arena becomes the default
strategy again.

Two weaknesses to fix (little code, see P2.5):

- **Silent fallback to the general heap** (`supervisor.c:111-113`): an arena
  overflow re-fragments the general heap *with no trace at all*, contradicting the
  tripwire policy of ADR 008 ("every kernel allocation that fails or overflows is
  logged as potential fragmentation"). A `klog_w` at that spot is enough.
- **No visibility of the occupancy peak**: `multi_heap_get_info()` exposes
  `minimum_free_bytes`, but `duneos_meminfo()` does not report it for the arena
  (`supervisor.c:163-171`). Exposing `arena_min_free` in `meminfo.h` + `free`
  would allow sizing the 64 K from real data (cf. P3.2).

---

## 3. Recommended actions, prioritised

### P0 — keystone: the load transient (unblocks the arena AND speed)

**Finding (added 2026-06-09, not seen in v1 of the audit).** On *every* `.dap`
load, the loader allocates four buffers from the **general heap**, all alive
simultaneously during relocation and freed afterwards (`loader.c:1128-1166`):

| Buffer | Size (launcher, ~700 sections) | Line |
| --- | ---: | --- |
| `shdrs` (section header table) | `e_shnum × 40` ≈ **28 K** | `loader.c:1128` |
| `shstrtab` (section names) | ~8 K | `loader.c:1138` |
| `symtab` | ~15 K | `loader.c:1152` |
| `strtab` (symbol names) | ~8 K | `loader.c:1160` |

≈ **60 K of transient per load**, including one contiguous 28 K block.
Consequences: (1) **breaks the arena** — the general heap can no longer supply
28 K contiguous; (2) **slows down every launch / boot / handoff-return** — 60 K to
read from the SD card (FatFS/SPI) + malloc/free; (3) it is paid on *every* load,
not once. To be distinguished from the `section_bases[1024]` of P2.1: that one is
the *runtime* cost (4 K resident); the transient above is the *load* cost (~60 K
ephemeral) — that is the real lock.

**Cause.** The `.dap` is ET_REL (`LDFLAGS = -r`, `constants.py:44`), and `ld -r`
**does not do gc-sections**. Yet apps compile with `-ffunction-sections
-fdata-sections` (`constants.py:17-19`) → ~1 section per function. Without gc,
these options eliminate **nothing**: they only **multiply the sections** (~700 for
the launcher), inflating the four buffers, for zero benefit.

**Action.** Drop `-ffunction-sections -fdata-sections`, or merge the sections
through a linker script in `ld -r` (`.text : { *(.text* .literal*) }`, etc. —
ADR 022 option C). Result: ~40 sections instead of ~700, **the same code bytes
loaded** (no gc to lose), transient **~60 K → ~12 K**, faster loading, and **the
arena becomes viable again** (the general heap no longer needs the 28 K block).

**Validation.** `dbt info` (count the sections before/after), size of `shdrs` at
boot, launch timing (boot → launcher visible); then re-enable
`CONFIG_DUNEOS_APP_ARENA_KB=64` and confirm the launcher loads. Low risk (ET_REL =
no gc to lose; the loader already classifies by name prefix, § "Section
classification", so merged `.text`/`.rodata` sections load identically). **Best
ratio of the whole audit, to be done before P1/P2.**

### P1 — sdkconfig, strong gain, low risk, zero code

Target: the 60.4 KiB of DIRAM `.text`. Each action is validated by `idf.py size`
before/after (and a `fullclean` is mandatory after a change).

| # | Action | Estimated gain | Risk / condition |
| --- | --- | ---: | --- |
| 1 | **`CONFIG_COMPILER_OPTIMIZATION_SIZE=y`** (the kernel is at `-Og`, `sdkconfig:979` — decision already validated) | 10-30 K (IRAM + flash) | Less comfortable debugging (optimised-out variables). The `.dap` apps are already at `-Os` (`tools/dbt/constants.py:24`) |
| 2 | **`CONFIG_SPI_FLASH_ROM_IMPL=y`** (`sdkconfig:3281`, not set) | ~7-10 K IRAM | ROM flash driver ≠ IDF: validate LittleFS + any OTA on device |
| 3 | `CONFIG_FREERTOS_PLACE_FUNCTIONS_INTO_FLASH=y` | ~8 K IRAM | Check the exact name on IDF v6 (absent from the generated sdkconfig — may have been renamed/absorbed) |
| 4 | `CONFIG_HEAP_PLACE_FUNCTION_INTO_FLASH=y` + `CONFIG_RINGBUF_PLACE_ISR_FUNCTIONS_INTO_FLASH=y` (`sdkconfig:2433,1829`) | 2-5 K IRAM | Forbids malloc/ringbuf in an ISR with the flash cache off — DuneOS does neither (CDC RX goes through a StreamBuffer) |
| 5 | Newlib **nano format** — the S3 ROM has it (`CONFIG_ESP_ROM_HAS_NEWLIB_NANO_FORMAT=y`, `sdkconfig:862`); find the IDF v6 option again (renamed, probably `CONFIG_LIBC_*`) | a few K .data/flash | Breaks `%f`/`%lld` in printf — audit klog and the apps first |
| 6 | **Dcache 32 K → 16 K** (`CONFIG_ESP32S3_DATA_CACHE_16KB`, `sdkconfig:1898`) | **16 K SRAM** | **To be measured, not done by default**: it penalises rodata-in-flash accesses (libgfx fonts, launcher icons). Test game rendering before adopting. Icache is already at its minimum (16 K) |
| 7 | System stacks: main 3584, timer 3584, event 2304, TCPIP 3072 | 2-4 K in total | Only with measured watermarks. Low priority, last on the list |

Recommended order: one change at a time, `idf.py size` + a full boot (launcher →
game → return) between each, so gains can be attributed and any regression
isolated.

### P2 — kernel code, strong gain, low effort

| # | Action | Estimated gain | Where |
| --- | --- | ---: | --- |
| 1 | **`section_bases[1024]` → allocation at the real `e_shnum`** (typically <100 sections, i.e. <400 B instead of 4 K). Best gain/effort ratio of the whole audit: the array is paid by **every resident app**, daemons included | **~3.9 K × N apps ≈ 15-20 K** | `loader.c:101,108`; bounds check already in place (`loader.c:1119,1334`) |
| 2 | `KLOG_RING_SIZE` 16 K → a Kconfig option, 8 K on the CardPuter | 8 K .bss | `klog.c:30` (keep a power of 2, the code uses `KLOG_RING_MASK`) |
| 3 | `GPIO_EV_RING` 512 → Kconfig or 128 entries | 3 K .bss | `drv_gpio.c:22` — 512 was sized to absorb `select()` latency; 128 is still ample for a keyboard |
| 4 | **Lazy mailbox**: create the queue on the first `duneos_send`/`recv` instead of at every launch — not all daemons use IPC | 3-5 K | `supervisor.c` (~881) |
| 5 | Arena tripwire (`klog_w` on fallback) + `arena_min_free` in `meminfo`/`free` (cf. §2) | visibility | `supervisor.c:111-113,163-171`, `meminfo.h`, `free.c` |

### P3 — structural work (post-contest, unless blocking)

1. **Two-zone exec pool**: daemons allocated bottom-up, foreground top-down. This
   is the only real fragmentation risk left: the bump allocator only reclaims in
   LIFO order (`loader.c:1591`) — a daemon unloaded in the middle leaves an
   unrecoverable hole until reboot. Compaction is impossible (relocations already
   applied, the code cannot move); two-zone is the simple and sufficient answer.
   With the ADR 031 handoff the foreground is already LIFO by construction, so
   this is not blocking for the contest.
2. **Re-size the exec pool and arena from real data**: once P2.5 is in place,
   record max `exec_pool_used` and `arena_min_free` with the 5 contest apps. If
   the exec pool tops out around ~40 K, dropping `CONFIG_DUNEOS_EXEC_POOL_KB` to
   48 returns 16 K to the general heap.
3. **24 K supervisor stack**: re-measure the watermark now that the ADR 031
   handoff has replaced the deep observation chains; potentially 8-12 K to take
   back.
4. **`/dev` fd leak on app crash** (backlog, confirmed 2026-06-05): not a direct
   RAM gain, but `DEVFS_MAX_FDS=16` is exhausted in ~2 crash loops — it governs
   restart stability and therefore the demo. To be kept at the top of the backlog.

---

## 4. Tracking table

To be filled in as implementation proceeds; each gain is measured by `idf.py size`
(static) and `free` on device (runtime, 3 baselines entry/kernel/services).

| Action | Estimated gain | Measured gain | Risk | Status |
| --- | ---: | ---: | --- | --- |
| **P0 merge sections** (drop `-ffunction-sections`) | transient 60→12 K/load + unblocks the arena + speed | | low (ET_REL = no gc) | **keystone, to be done first** |
| P1.1 `-Os` | 10-30 K | 8.8 K IRAM (+90 K flash) | **medium (revised)** | **REVERTED 2026-06-10** — measured gain of -Os vs -Og on the same code (`.iram0.text` 75831→67039), but the kernel **no longer boots** at `-Os`. Probable latent UB exposed by the optimisation in low-level code (the loader writes IRAM through the DRAM alias, exec-install, memory barriers). Do NOT re-enable before the UB has been isolated (missing `volatile`/barrier). `-Os` is therefore a conditional gain, not an acquired one. |
| P1.2 `SPI_FLASH_ROM_IMPL` | 7-10 K | | medium (LittleFS) | |
| P1.3 FreeRTOS→flash | ~8 K | | low | IDF v6 name to be checked |
| P1.4 heap/ringbuf→flash | 2-5 K | | low | |
| P1.5 newlib nano | a few K | | medium (%f) | |
| P1.6 dcache 16 K | 16 K | | **rendering perf** | to be measured |
| P2.1 `section_bases` | 15-20 K | **24.6 K** (measured with `free`) | low | **done + validated 2026-06-10** — `free` 42296→66952, low-water 17K→38K. Better than the estimate (5 resident apps × ~4K + reload). |
| P2.2 klog 8 K | 8 K | | none | |
| P2.3 GPIO ring | 3 K | | low | |
| P2.4 lazy mailbox | 3-5 K | | low | |
| P2.5 tripwire + min_free | visibility | | none | |

Cumulative P1+P2 potential (excluding dcache): **~50-90 KiB**, enough to move from
a budget "at exact break-even" (§1.1) to ~25 % of margin — or to re-enable some
comfort (resident WiFi daemon, klog 16 K) if the margin allows.

**P0 stands apart and comes before everything**: it frees no resident RAM but
**removes the ~60 K peak on every load** (thus unblocking the arena) and **speeds
up launches**. Revised conclusion of the audit: the arena strategy is the right one
(§2); its only blocker is the load transient (P0), caused by the section explosion
of an ET_REL without gc. Order of attack: **P0 → re-enable the arena → P1 → P2**.
