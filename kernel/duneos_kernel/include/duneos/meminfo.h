#pragma once

/*
 * DuneOS memory diagnostics — ADR 008 / ADR 025.
 *
 * A full RAM map for the `free` tool. On a no-PSRAM board, allocation failures
 * are about the largest *contiguous* block, not the total — so every region
 * reports free AND largest. `internal_total` minus `internal_free` is what the
 * kernel + system + WiFi + apps consume right now; `internal_min_free` is the
 * worst-case low-water since boot.
 *
 * Regions reported (they overlap on the S3's unified SRAM — INTERNAL and EXEC
 * both draw from D/IRAM, DMA is a DRAM subset):
 *   - INTERNAL : MALLOC_CAP_INTERNAL|8BIT — app data/stack/heap, kernel, WiFi.
 *   - EXEC     : MALLOC_CAP_EXEC — IRAM-capable; the loader exec pool lives here.
 *   - DMA      : MALLOC_CAP_DMA  — DMA-capable DRAM.
 * Plus the two DuneOS-reserved pools (exec pool, app arena) and their use.
 */

#include <stdint.h>

typedef struct {
    uint32_t internal_total;     /* INTERNAL|8BIT — free + allocated            */
    uint32_t internal_free;      /* free now                                    */
    uint32_t internal_largest;   /* largest free contiguous block               */
    uint32_t internal_min_free;  /* lowest free ever since boot (low-water)      */
    uint32_t exec_free;          /* MALLOC_CAP_EXEC free                         */
    uint32_t exec_largest;       /* largest free EXEC block                      */
    uint32_t dma_free;           /* MALLOC_CAP_DMA free                          */
    uint32_t dma_largest;        /* largest free DMA block                       */
    uint32_t exec_pool_size;     /* loader app exec pool, bytes (0 if no loader) */
    uint32_t exec_pool_used;     /* of which used by loaded apps' .text          */
    uint32_t arena_size;         /* app memory arena, bytes (0 if disabled/PSRAM) */
    uint32_t arena_free;         /* free bytes in the arena                       */
    uint32_t arena_largest;      /* largest free contiguous block in the arena    */
    /* Boot baselines for the consumption breakdown (0 until marked):
     *   entry_free  = INTERNAL free at app_main entry → IDF/FreeRTOS startup
     *                 baseline; total − entry_free is what ESP-IDF consumed
     *                 before DuneOS ran.
     *   kernel_free = INTERNAL free after the DuneOS kernel finished init
     *                 (VFS, drivers, exec pool, supervisor) but before any
     *                 service launched; entry_free − kernel_free is the DuneOS
     *                 kernel's own cost (includes the exec pool), and
     *                 kernel_free − internal_free is the running services
     *                 (apps + WiFi). */
    uint32_t internal_entry_free;
    uint32_t internal_kernel_free;
} duneos_meminfo_t;

/* Fill *out with current memory stats. Returns 0 on success, -1 on NULL arg. */
int duneos_meminfo(duneos_meminfo_t *out);

/* Record a boot baseline for the consumption breakdown.
 *   phase 0 = app_main entry (IDF startup baseline)
 *   phase 1 = kernel ready, before services launch */
void duneos_meminfo_mark(int phase);
