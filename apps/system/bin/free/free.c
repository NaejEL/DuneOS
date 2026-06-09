#include <unistd.h>
#include <stdio.h>

#include "duneos/meminfo.h"

extern int esp_get_free_heap_size(void);

static void put(const char *s, int n) { write(STDOUT_FILENO, s, n); }

void app_main(void)
{
    char b[128];
    int  n;

    duneos_meminfo_t mi;
    if (duneos_meminfo(&mi) != 0) {
        n = snprintf(b, sizeof(b), "free heap: %d bytes\r\n", esp_get_free_heap_size());
        put(b, n);
        return;
    }

    /* INTERNAL is the binding region on a no-PSRAM board: used = total − free,
     * and `largest` is what actually caps a single allocation. */
    n = snprintf(b, sizeof(b),
        "INTERNAL  total %6u  used %6u  free %6u\r\n",
        (unsigned)mi.internal_total,
        (unsigned)(mi.internal_total - mi.internal_free),
        (unsigned)mi.internal_free);
    put(b, n);
    n = snprintf(b, sizeof(b),
        "          largest %6u  low-water %6u\r\n",
        (unsigned)mi.internal_largest, (unsigned)mi.internal_min_free);
    put(b, n);
    n = snprintf(b, sizeof(b),
        "EXEC      free %6u  largest %6u\r\n",
        (unsigned)mi.exec_free, (unsigned)mi.exec_largest);
    put(b, n);
    n = snprintf(b, sizeof(b),
        "DMA       free %6u  largest %6u\r\n",
        (unsigned)mi.dma_free, (unsigned)mi.dma_largest);
    put(b, n);
    n = snprintf(b, sizeof(b),
        "exec pool %u / %u used\r\n",
        (unsigned)mi.exec_pool_used, (unsigned)mi.exec_pool_size);
    put(b, n);

    /* Consumption breakdown from the boot baselines (if marked):
     *   IDF/startup   = total − entry      (ESP-IDF + FreeRTOS before DuneOS)
     *   DuneOS kernel = entry − kernel      (VFS/drivers/supervisor + exec pool)
     *   services      = kernel − free       (running apps + WiFi) */
    if (mi.internal_entry_free && mi.internal_kernel_free) {
        n = snprintf(b, sizeof(b),
            "--- consumed by ---\r\n"
            "IDF/startup   %6u\r\n"
            "DuneOS kernel %6u  (incl %u exec pool)\r\n"
            "services      %6u  (apps + WiFi)\r\n",
            (unsigned)(mi.internal_total       - mi.internal_entry_free),
            (unsigned)(mi.internal_entry_free  - mi.internal_kernel_free),
            (unsigned)mi.exec_pool_size,
            (unsigned)(mi.internal_kernel_free - mi.internal_free));
        put(b, n);
    }
    if (mi.arena_size) {
        n = snprintf(b, sizeof(b),
            "arena     free %6u  largest %6u  (size %u)\r\n",
            (unsigned)mi.arena_free, (unsigned)mi.arena_largest,
            (unsigned)mi.arena_size);
        put(b, n);
    }
}
