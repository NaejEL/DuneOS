/*
 * Kernel ring-buffer logger.
 *
 * Messages are formatted as "[L][tag] message\n" and written into a circular
 * buffer in DRAM. The buffer is exposed read-only via /dev/klog so that a
 * shell can dump it with `cat /dev/klog` without polluting app stdout.
 *
 * In non-silent builds each message is also forwarded to ESP_LOGx so the
 * USB/UART console shows live output during development.
 *
 * Ring semantics: write always succeeds (overwrites oldest data on wrap).
 * Readers track their own absolute position; if they fall too far behind
 * they start from the oldest available byte.
 *
 * Thread / ISR safety: a portMUX spinlock guards the ring so klog_write can
 * be called from any task context without initialization ceremony.
 */

#include "duneos/klog.h"

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/portmacro.h"

#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <stddef.h>

#define KLOG_RING_SIZE  16384
#define KLOG_RING_MASK  (KLOG_RING_SIZE - 1)

static char         s_ring[KLOG_RING_SIZE];
static size_t       s_write_abs = 0;
static portMUX_TYPE s_mux       = portMUX_INITIALIZER_UNLOCKED;

/* ----- ring helpers ------------------------------------------------------ */

static void ring_append(const char *data, size_t len)
{
    for (size_t i = 0; i < len; i++) {
        s_ring[s_write_abs & KLOG_RING_MASK] = data[i];
        s_write_abs++;
    }
}

/* ----- public API -------------------------------------------------------- */

void klog_write(char level, const char *tag, const char *fmt, ...)
{
    char line[256];
    int prefix_len = snprintf(line, sizeof(line), "[%c][%s] ", level, tag);
    if (prefix_len < 0) prefix_len = 0;
    if (prefix_len >= (int)sizeof(line)) prefix_len = (int)sizeof(line) - 1;

    va_list ap;
    va_start(ap, fmt);
    int msg_len = vsnprintf(line + prefix_len,
                            sizeof(line) - (size_t)prefix_len,
                            fmt, ap);
    va_end(ap);
    if (msg_len < 0) msg_len = 0;

    size_t total = (size_t)prefix_len + (size_t)msg_len;
    if (total >= sizeof(line)) total = sizeof(line) - 1;
    if (total == 0 || line[total - 1] != '\n') {
        if (total < sizeof(line) - 1) line[total++] = '\n';
    }
    line[total] = '\0';

    /* Keep DEBUG out of the persistent ring: the loader emits one line per
     * relocation section (~180 per app load), which otherwise floods the
     * 16 KiB ring and evicts the boot sequence before it can be read back.
     * DEBUG still reaches the live console below when explicitly enabled. */
    if (level != 'D') {
        portENTER_CRITICAL(&s_mux);
        ring_append(line, total);
        portEXIT_CRITICAL(&s_mux);
    }

    /* Forward only errors to ESP_LOG — I/W stay in the ring buffer only. */
    if (level == 'E') ESP_LOGE(tag, "%s", line + prefix_len);
}

/* ----- ring read API (used by /dev/klog in vfs_dev.c) -------------------- */

size_t klog_ring_write_pos(void)
{
    return s_write_abs;
}

size_t klog_ring_oldest(void)
{
    return (s_write_abs > KLOG_RING_SIZE) ? (s_write_abs - KLOG_RING_SIZE) : 0;
}

size_t klog_ring_read(size_t *abs_pos, char *buf, size_t max_len)
{
    if (!abs_pos || !buf || max_len == 0) return 0;

    portENTER_CRITICAL(&s_mux);

    size_t oldest = klog_ring_oldest();
    size_t newest = s_write_abs;

    if (*abs_pos < oldest) *abs_pos = oldest;

    size_t avail = (newest > *abs_pos) ? (newest - *abs_pos) : 0;
    size_t n     = (avail < max_len) ? avail : max_len;

    if (n > 0) {
        size_t start = *abs_pos & KLOG_RING_MASK;
        size_t first = KLOG_RING_SIZE - start;
        if (first > n) first = n;
        memcpy(buf, s_ring + start, first);
        if (first < n) memcpy(buf + first, s_ring, n - first);
        *abs_pos += n;
    }

    portEXIT_CRITICAL(&s_mux);
    return n;
}
