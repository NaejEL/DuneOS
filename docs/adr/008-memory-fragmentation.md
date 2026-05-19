# ADR 008 — Memory fragmentation strategy: design layer, not allocator

**Status:** Accepted · 2026-05-19

## Context

DuneOS targets RAM-constrained MCUs. The smallest validated board (M5Stack CardPuter, ESP32-S3FN8) has 320 KiB of internal DRAM and no PSRAM. With four concurrent app slots, several kernel daemons, FreeRTOS overhead, and driver state, the working set is tight. The classic failure mode on such systems is not running out of memory — it's fragmentation: `malloc(8192)` fails not because there's no memory but because no contiguous block of 8 KiB exists.

The roadmap had silently relied on three anti-fragmentation mechanisms that were never named as a strategy:

1. **Per-app monolithic pool** (Phase 20, shipped): one `heap_caps_malloc` per slot for code+data+heap. When the app exits, the entire pool returns to the kernel in one piece — implicit "compaction on exit" for free.
2. **TLSF userspace allocator** (Phase 20 TODO): would handle the in-pool `malloc`/`free` with O(1) best-fit, minimising internal fragmentation.
3. **Static-storage handles** (now [[ADR-002]]): kernel tasks/mutexes/semaphores/queues never `malloc`, so the bulk of kernel-side allocations is gone.

Without a centralised statement, every future code change risks re-introducing fragmentation through a careless `heap_caps_malloc` in a hot path. There's also the question of compaction: without an MMU, pointers are absolute, so live objects can't be moved. This needs to be made explicit so nobody wastes a phase trying to "add a defrag pass".

## Decision

DuneOS treats memory fragmentation as a **design-layer concern**, addressed through four pillars and one explicit non-goal:

**Pillar 1 — Static-first for kernel handles.** Per [[ADR-002]], OSAL primitives use caller-provided storage. No malloc-allocated tasks, mutexes, semaphores, or queues in the kernel. This eliminates the bulk of per-operation allocations.

**Pillar 2 — Per-app monolithic pool.** The supervisor allocates one contiguous block per slot covering code, data, and heap (Phase 20). When the app exits, `heap_caps_free(slot->heap_pool_buf)` returns the entire block to the kernel atomically. **This is the closest thing DuneOS has to "defragmentation on app exit" — and it's already in place.** No additional defrag pass is needed or possible.

**Pillar 3 — TLSF inside the app pool.** Phase 20 TODO. `libdune.a` will ship a TLSF (Two-Level Segregated Fit) allocator that handles in-pool `malloc`/`free` for the app. TLSF is O(1) for both allocate and free, and has well-known low fragmentation properties under real-world workloads. The kernel never sees these allocations.

**Pillar 4 — Caps as placement strategy.** Per [[ADR-003]], large allocations (>8 KiB — framebuffers, app pools, network buffers) request `OSAL_MEM_EXTERNAL` to land in PSRAM on boards that have it, reducing DRAM pressure. Small frequent allocations stay in `OSAL_MEM_DEFAULT` (DRAM). The 8 KiB threshold is heuristic, not a hard rule — callers use judgment.

**Non-goal: compaction.** DuneOS has no MMU. Pointers are absolute. Moving a live object is impossible without a recompile. **No phase will attempt compaction.** The only "free defrag" event is app exit, and Pillar 2 already captures it.

**Operational policy — no new runtime allocations in kernel hot paths.** Code review for every kernel-side PR asks: "does this introduce a `heap_caps_malloc` / `osal_mem_alloc` that runs after kernel init?" If yes, the contributor must justify in the PR description why pre-allocation at init or static storage doesn't suffice. Drivers may allocate at init; nothing should allocate in `read()`, `write()`, ISR paths, or supervisor scheduling decisions.

**Tripwire — log allocation failures distinguishably.** When `osal_mem_alloc` returns NULL on a path that isn't a one-shot init, log via `klog_w("memory: alloc %u B failed (possibly fragmenting)", size)`. If kernel-side allocation failures start appearing in `klog` on long-running boards, fragmentation is winning and a code review is overdue.

## Consequences

- Phase 20's TLSF TODO becomes load-bearing — it's the only allocator-level anti-fragmentation mechanism, and the rest of the strategy assumes it's there.
- New phases (especially 26-27) inherit a clear rule: prefer pre-allocation, prefer static storage, prefer per-app pools. PR review gets a concrete checklist.
- The kernel's existing `vfs_tmp.c` (heap-backed RAM filesystem) and `klog` ring buffer (4 KiB) are already pre-allocated at init — they conform. Future kernel features must follow the same model.
- An eventual MMU-equipped target (deep future, not in current roadmap) could revisit Pillar 4 (compaction becomes possible), but the other three pillars remain valid regardless.
- No defrag/compaction work appears in the roadmap. If a contributor proposes one, this ADR is the answer: it's impossible by construction (no MMU), and the existing pillars already capture every defrag opportunity that exists.

## Alternatives

- **Add a compaction pass at idle** — rejected, impossible without MMU. Documented here so the discussion doesn't reopen.
- **Single global heap + manual reference counting** — rejected. Fragments globally with no per-app cleanup boundary; loses the implicit defrag-on-exit benefit.
- **Pool-per-object-type (slab allocator)** — considered, deferred. Could supplement Pillar 1 if profiling shows fragmentation from small irregular allocations. Adds complexity; not justified until measured pressure appears.
- **Larger pre-allocation buckets in supervisor** — rejected. Would waste memory on small apps to benefit large ones. The per-app declared `heap_size` already captures this — apps that need more ask for more.
