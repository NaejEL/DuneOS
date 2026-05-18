/*
 * drv_usb_cdc.c — USB CDC-ACM VFS device (/dev/ttyUSB0)
 *
 * TX path: mutex-serialized write_queue + single non-blocking flush.
 * Mirrors vfs_tinyusb.c's tusb_write design — eliminates concurrent
 * write_flush collisions that caused echo chars to appear only on Enter.
 *
 * RX path: TinyUSB RX callback enqueues into a FreeRTOS ring buffer;
 * cdc_read blocks on a counting semaphore with 100ms timeout — compatible
 * with shell_core.c's idle-reprinting (50 × 100ms = 5s).
 */

#include "duneos/klog.h"
#include "duneos/dev_driver.h"

#include "tinyusb_cdc_acm.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/ringbuf.h"

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
 * tinyusb_cdcacm_write_flush(itf, 0) is non-blocking (one tud_cdc_n_write_flush
 * call, no vTaskDelay) — safe to call from any task including TinyUSB's own.
 * ---------------------------------------------------------------------- */

static SemaphoreHandle_t s_write_mutex = NULL;

/* -------------------------------------------------------------------------
 * RX state
 * ---------------------------------------------------------------------- */

static RingbufHandle_t   s_rx_ring   = NULL;
static SemaphoreHandle_t s_rx_sem    = NULL;
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

    xRingbufferSend(s_rx_ring, buf, rx_size, 0);
    xSemaphoreGive(s_rx_sem);
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
    if (!s_rx_ring) { errno = EIO; return -1; }

    uint8_t *out   = (uint8_t *)dst;
    size_t   total = 0;

    while (total < size) {
        size_t item_size = 0;
        if (xSemaphoreTake(s_rx_sem,
                           pdMS_TO_TICKS(CDC_READ_TIMEOUT_MS)) != pdTRUE) {
            break;
        }

        uint8_t *item = xRingbufferReceiveUpTo(s_rx_ring, &item_size,
                                                0, size - total);
        if (!item || item_size == 0) break;

        memcpy(out + total, item, item_size);
        vRingbufferReturnItem(s_rx_ring, item);
        total += item_size;

        while (total < size) {
            item = xRingbufferReceiveUpTo(s_rx_ring, &item_size, 0,
                                          size - total);
            if (!item) break;
            memcpy(out + total, item, item_size);
            vRingbufferReturnItem(s_rx_ring, item);
            total += item_size;
        }
        break;
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
 *
 * Uses the same write_mutex as cdc_write — no concurrent flush collisions.
 * write_flush(0) is non-blocking so this is safe from any task context.
 * Uses a short timeout (10ms) so a slow host never stalls ESP_LOG callers.
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
    s_rx_ring     = xRingbufferCreate(CDC_RX_BUF_SIZE, RINGBUF_TYPE_BYTEBUF);
    s_rx_sem      = xSemaphoreCreateCounting(CDC_RX_BUF_SIZE, 0);

    if (!s_write_mutex || !s_rx_ring || !s_rx_sem) {
        klog_e(TAG, "mutex / ring buffer / semaphore alloc failed");
        return;
    }
    klog_d(TAG, "CDC RX ring allocated (%u B)", CDC_RX_BUF_SIZE);
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
