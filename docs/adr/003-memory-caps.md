# ADR 003 — Memory caps: placement hints with silent DRAM fallback

**Status:** Accepted · 2026-05-19

## Context

ESP32-S3 exposes heterogeneous memory: internal DRAM (fast, small, ~320 KiB), external PSRAM (large, slower, optional), IRAM (instruction-side, scarce), DMA-capable regions. ESP-IDF surfaces these via `heap_caps_malloc(size, MALLOC_CAP_*)` flags. RP2040 has none of this — one flat SRAM. STM32H7 has TCM, DTCM, and external SDRAM. The simulator Linux has only whatever the kernel hands out.

The OSAL needs an allocator API that:

- Lets callers express *intent* (large block → prefer external; DMA buffer → must be DMA-capable; small frequent alloc → keep close).
- Never fails silently because of capability mismatch on platforms that lack the hierarchy (the simulator should not reject a `MALLOC_CAP_DMA` request — it should just allocate from the only memory it has).
- Doesn't multiply per-caller fallback logic (every caller writing `try DMA, if NULL try DEFAULT` is code duplication and a fragmentation source — see [[ADR-008]]).

## Decision

```c
void *osal_mem_alloc(size_t size, uint32_t flags);
void  osal_mem_free(void *ptr);
bool  osal_mem_caps_available(uint32_t flags);
```

Flags are a bitmask. `flags == 0` means `OSAL_MEM_DEFAULT`. Other flags:

| Flag | Intent | Backend mapping (ESP32-S3) | Mapping when unsupported |
|---|---|---|---|
| `OSAL_MEM_DEFAULT` (0) | Caller has no preference | DRAM internal | DRAM (always available) |
| `OSAL_MEM_EXTERNAL` | Prefer large/slow memory; usable for >8 KiB | PSRAM if board has it | DRAM (silent fallback) |
| `OSAL_MEM_DMA` | Must be DMA-capable | DRAM internal (DMA-capable region) | DRAM (silent fallback — caller must validate alignment separately) |
| `OSAL_MEM_FAST` | Hot code/data; latency-critical | IRAM if free | DRAM (silent fallback) |

**Fallback policy: silent fallback to DRAM whenever the requested capability is not available on the target.** Allocation only fails (returns NULL) when there is genuinely not enough memory in any pool the OSAL can reach.

**Callers who must distinguish "got DMA-capable" vs "got fallback DRAM" use `osal_mem_caps_available(flags)` before allocating.** Example: a DMA-driven SPI driver checks once at init; if `false`, it falls back to PIO mode instead of triggering a DMA transfer on non-DMA memory. The check is platform-static (no per-call overhead).

Alignment is *not* a flag — `osal_mem_alloc_aligned(size, align, flags)` is a separate function if needed (deferred until a real caller demands it).

## Consequences

- The kernel and apps that use OSAL allocations get a predictable interface: any allocation succeeds as long as there is enough memory total, regardless of platform capability variations. This eliminates a class of "works on T-Embed (PSRAM) but crashes on CardPuter" bugs.
- DMA-capable validation moves from the allocator to the caller: drivers needing DMA must verify with `osal_mem_caps_available` and refuse the operation up-front if not satisfied. This is the right level — the allocator can't know what the caller intends to do with the buffer.
- The "fragmentation by placement" benefit of [[ADR-008]] is achievable: large allocs ask for `EXTERNAL`, small ones use `DEFAULT`, naturally reducing DRAM pressure on PSRAM boards.
- ESP-IDF backend implementation is a near-direct mapping to `heap_caps_malloc` flags; the Linux simulator backend is one `malloc`.
- `osal_mem_free` takes only the pointer — no size, no flags. The allocator tracks the source pool internally (heap_caps does this naturally; the simulator just calls `free`).

## Alternatives

- **Strict (NULL on cap mismatch)** — rejected. Forces every caller to write fallback logic, duplicating code and introducing inconsistent retry patterns. Worse for fragmentation, not better.
- **Enum instead of bitmask** — rejected. `EXTERNAL | DMA` (large-but-DMA-capable) is a real combination on some platforms; bitmasks express it naturally.
- **Per-pool API (`osal_mem_alloc_external`, `osal_mem_alloc_dma`)** — rejected. Adds functions without expressive power; combining hints is awkward.
- **Carry alignment in flags** — rejected. Conflates two concerns; alignment is a property of the buffer's use (DMA controller requirement, SIMD lane), not of the memory pool.
