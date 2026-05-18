/*
 * drv_usb_cdc.c — USB CDC-ACM VFS device (/dev/ttyUSB0)
 *
 * TX path: mutex-serialized write_queue + single non-blocking flush.
 *
 * RX path: TinyUSB RX callback stores bytes in a plain C circular buffer;
 * a counting semaphore carries exactly one token per byte so that
 * cdc_read always advances the tail by exactly the number of bytes it
 * actually consumed.
 *
 * We deliberately avoid xRingbufferReceiveUpTo (BYTEBUF type) because
 * vRingbufferReturnItem advances the read pointer by the full contiguous
 * block, not by the caller's maxSize, silently dropping bytes when a USB
 * packet carries more than 1 character.  We also avoid xStreamBufferReceive
 * because it uses task notifications, which can conflict with other
 * FreeRTOS mechanisms active on the shell task.
 */

#include "duneos/klog.h"
#include "duneos/dev_driver.h"

#include "tinyusb_cdc_acm.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>

#include "drv_usb_cdc_priv.h"

static const char *TAG = "duneos/usb_cdc";

#define CDC_RX_BUF_SIZE     512
#define CDC_READ_TIMEOUT_MS 100

/* -------------------------------------------------------------------------
 * TX — single mutex shared by cdc_write and s_cdc_log_vprintf.
 * ---------------------------------------------------------------------- */

static SemaphoreHandle_t s_write_mutex = NULL;

/* -------------------------------------------------------------------------
 * RX — plain circular byte buffer + counting semaphore.
 *
 * s_rx_sem: one token per byte in the buffer.  s_rx_head/tail protected by
 * s_rx_mux (portMUX, safe to take inside and outside ISR context).
 *
 * Invariant: semaphore count == (s_rx_head - s_rx_tail) % CDC_RX_BUF_SIZE
 * ---------------------------------------------------------------------- */

static uint8_t           s_rx_buf[CDC_RX_BUF_SIZE];
static volatile uint16_t s_rx_head   = 0;   /* write index (RX callback) */
static volatile uint16_t s_rx_tail   = 0;   /* read  index (cdc_read)   */
static SemaphoreHandle_t s_rx_sem    = NULL;
static portMUX_TYPE      s_rx_mux    = portMUX_INITIALIZER_UNLOCKED;
static bool              s_connected = false;

/* -------------------------------------------------------------------------
 * TinyUSB CDC callbacks
 * ---------------------------------------------------------------------- */

void drv_usb_cdc_rx_cb(int itf, cdcacm_event_t *event)
{
    (void)itf; (void)event;

    uint8_t buf[CONFIG_TINYUSB_CDC_RX_BUFSIZE];
    size_t  rx_size = 0;

    esp_err_t ret = tinyusb_cdcacm_read(TINYUSB_CDC_ACM_0, buf,
                                         sizeof(buf), &rx_size);
    if (ret != ESP_OK || rx_size == 0) return;

    /* Write bytes into the circular buffer, counting only stored bytes.
     * Give exactly one semaphore token per byte actually stored so the
     * semaphore count stays equal to (s_rx_head - s_rx_tail) % BUF_SIZE. */
    size_t stored = 0;
    portENTER_CRITICAL(&s_rx_mux);
    for (size_t i = 0; i < rx_size; i++) {
        uint16_t next = (s_rx_head + 1u) % CDC_RX_BUF_SIZE;
        if (next == s_rx_tail) break;   /* full — drop remaining bytes */
        s_rx_buf[s_rx_head] = buf[i];
        s_rx_head = next;
        stored++;
    }
    portEXIT_CRITICAL(&s_rx_mux);

    /* Give tokens AFTER the critical section to avoid priority inversion. */
    for (size_t i = 0; i < stored; i++) xSemaphoreGive(s_rx_sem);
}

void drv_usb_cdc_line_state_cb(int itf, cdcacm_event_t *event)
{
    (void)itf;
    s_connected = event->line_state_changed_data.dtr;
    klog_d(TAG, "CDC line state: DTR=%d RTS=%d",
           event->line_state_changed_data.dtr,
           event->line_state_changed_data.rts);
}

/* -------------------------------------------------------------------------
 * duneos_dev_driver_t callbacks — routes through devfs (/dev/ttyUSB0)
 * ---------------------------------------------------------------------- */

static int cdc_open(duneos_devfd_t *fd, int flags)
{
    (void)fd; (void)flags;
    return 0;
}

static int cdc_close(duneos_devfd_t *fd)
{
    (void)fd;
    return 0;
}

static ssize_t cdc_read(duneos_devfd_t *fd, void *dst, size_t size)
{
    (void)fd;
    if (!s_rx_sem) { errno = EIO; return -1; }
    if (size == 0) return 0;

    uint8_t *out   = (uint8_t *)dst;
    size_t   total = 0;

    /* Block until the first byte arrives (or 100 ms timeout). */
    if (xSemaphoreTake(s_rx_sem, pdMS_TO_TICKS(CDC_READ_TIMEOUT_MS)) != pdTRUE)
        return 0;  /* timeout — shell_core treats 0 as idle */

    portENTER_CRITICAL(&s_rx_mux);
    out[total++] = s_rx_buf[s_rx_tail];
    s_rx_tail = (s_rx_tail + 1u) % CDC_RX_BUF_SIZE;
    portEXIT_CRITICAL(&s_rx_mux);

    /* Greedily drain any additional bytes the caller requested. */
    while (total < size) {
        if (xSemaphoreTake(s_rx_sem, 0) != pdTRUE) break;  /* non-blocking */
        portENTER_CRITICAL(&s_rx_mux);
        out[total++] = s_rx_buf[s_rx_tail];
        s_rx_tail = (s_rx_tail + 1u) % CDC_RX_BUF_SIZE;
        portEXIT_CRITICAL(&s_rx_mux);
    }

    return (ssize_t)total;
}

static ssize_t cdc_write(duneos_devfd_t *fd, const void *src, size_t size)
{
    (void)fd;
    if (!s_write_mutex) return (ssize_t)size;
    xSemaphoreTake(s_write_mutex, portMAX_DELAY);
    tinyusb_cdcacm_write_queue(TINYUSB_CDC_ACM_0, (const uint8_t *)src, size);
    tinyusb_cdcacm_write_flush(TINYUSB_CDC_ACM_0, 0);
    xSemaphoreGive(s_write_mutex);
    return (ssize_t)size;
}

/* -------------------------------------------------------------------------
 * klog → CDC redirect
 * ---------------------------------------------------------------------- */

static int s_cdc_log_vprintf(const char *fmt, va_list ap)
{
    char buf[256];
    int n = vsnprintf(buf, sizeof(buf), fmt, ap);
    int len = (n > 0 && n < (int)sizeof(buf)) ? n : (int)sizeof(buf) - 1;
    if (!s_write_mutex) return n;
    if (xSemaphoreTake(s_write_mutex, pdMS_TO_TICKS(10)) == pdTRUE) {
        tinyusb_cdcacm_write_queue(TINYUSB_CDC_ACM_0, (uint8_t *)buf, len);
        tinyusb_cdcacm_write_flush(TINYUSB_CDC_ACM_0, 0);
        xSemaphoreGive(s_write_mutex);
    }
    return n;
}

void drv_usb_cdc_attach_log(void)
{
    esp_log_set_vprintf(s_cdc_log_vprintf);
}

/* -------------------------------------------------------------------------
 * Initialisation
 * ---------------------------------------------------------------------- */

void drv_usb_cdc_preinit(void)
{
    s_write_mutex = xSemaphoreCreateMutex();
    s_rx_sem      = xSemaphoreCreateCounting(CDC_RX_BUF_SIZE, 0);

    if (!s_write_mutex || !s_rx_sem) {
        klog_e(TAG, "mutex / semaphore alloc failed");
        return;
    }
    klog_d(TAG, "CDC RX circular buffer ready (%u B)", CDC_RX_BUF_SIZE);
}

void drv_usb_cdc_register(void)
{
    static const duneos_dev_driver_t drv = {
        .name  = "ttyUSB0",
        .init  = NULL,
        .open  = cdc_open,
        .close = cdc_close,
        .read  = cdc_read,
        .write = cdc_write,
    };
    duneos_dev_register(&drv);
}
