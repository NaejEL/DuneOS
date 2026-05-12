#include "duneos/dev_driver.h"
#include "duneos/fb_ioctl.h"
#include "duneos/klog.h"
#include "board_config.h"
#include "st7789_hw.h"

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "esp_heap_caps.h"

#include <string.h>
#include <errno.h>

static const char *TAG = "duneos/fb0";

/* Maximum bytes per DMA transfer — 4096 is safe on shared and dedicated buses. */
#define FB_DMA_CHUNK   4096u

/* Per-fd write cursor (byte offset into the back-buffer). */
typedef struct {
    uint32_t pos;
    uint8_t  _pad[12];
} fb_priv_t;

_Static_assert(sizeof(fb_priv_t) <= DUNEOS_DEV_PRIV_SIZE, "fb_priv_t too large");

/* Global driver state — one framebuffer per kernel. */
static uint16_t         *s_fb    = NULL;
static uint16_t          s_width = 0;
static uint16_t          s_height = 0;
static SemaphoreHandle_t s_lock  = NULL;

/* Chunk buffer in internal DRAM for byte-swap during flush. */
static uint16_t s_chunk[FB_DMA_CHUNK / sizeof(uint16_t)];

/* ---- flush helper --------------------------------------------------------- */

static void flush_rect(uint16_t rx, uint16_t ry, uint16_t rw, uint16_t rh)
{
    /* set_window issues RAMWR (command mode); data calls below set DC=1 each time */
    st7789_hw_set_window(rx, ry, rw, rh);

    size_t pixels_total = (size_t)rw * rh;
    size_t pixels_done  = 0;
    size_t chunk_pixels = FB_DMA_CHUNK / sizeof(uint16_t);

    while (pixels_done < pixels_total) {
        size_t batch = pixels_total - pixels_done;
        if (batch > chunk_pixels) batch = chunk_pixels;

        for (size_t i = 0; i < batch; i++) {
            size_t   p   = pixels_done + i;
            uint16_t py  = (uint16_t)(p / rw);
            uint16_t px  = (uint16_t)(p % rw);
            uint16_t src = s_fb[(size_t)(ry + py) * s_width + (rx + px)];
            s_chunk[i]   = (src >> 8) | (src << 8);
        }
        st7789_hw_data(s_chunk, batch * 2);
        pixels_done += batch;
    }
}

/* ---- driver callbacks ----------------------------------------------------- */

static esp_err_t fb_init(void)
{
    size_t fb_bytes = (size_t)DUNEOS_DISPLAY_WIDTH * DUNEOS_DISPLAY_HEIGHT * 2;
    s_fb = heap_caps_malloc(fb_bytes, MALLOC_CAP_SPIRAM);
    if (!s_fb) {
        klog_e(TAG, "PSRAM alloc %u bytes failed", (unsigned)fb_bytes);
        return ESP_ERR_NO_MEM;
    }
    memset(s_fb, 0, fb_bytes);

    s_width  = DUNEOS_DISPLAY_WIDTH;
    s_height = DUNEOS_DISPLAY_HEIGHT;

    s_lock = xSemaphoreCreateMutex();
    if (!s_lock) {
        heap_caps_free(s_fb); s_fb = NULL;
        return ESP_ERR_NO_MEM;
    }

    esp_err_t err = st7789_hw_init();
    if (err != ESP_OK) {
        heap_caps_free(s_fb); s_fb = NULL;
        vSemaphoreDelete(s_lock); s_lock = NULL;
        return err;
    }

    klog_i(TAG, "/dev/fb0 ready: %ux%u PSRAM back-buffer (%u bytes)",
           s_width, s_height, (unsigned)fb_bytes);
    return ESP_OK;
}

static int fb_open(duneos_devfd_t *fd, int flags)
{
    (void)flags;
    ((fb_priv_t *)fd->priv)->pos = 0;
    return 0;
}

static ssize_t fb_write(duneos_devfd_t *fd, const void *buf, size_t len)
{
    fb_priv_t *p       = (fb_priv_t *)fd->priv;
    size_t     fb_bytes = (size_t)s_width * s_height * sizeof(uint16_t);

    if (p->pos >= fb_bytes) { errno = ENOSPC; return -1; }
    size_t avail = fb_bytes - p->pos;
    if (len > avail) len = avail;

    memcpy((uint8_t *)s_fb + p->pos, buf, len);
    p->pos += (uint32_t)len;
    return (ssize_t)len;
}

static int fb_ioctl(duneos_devfd_t *fd, int cmd, void *arg)
{
    fb_priv_t *p = (fb_priv_t *)fd->priv;

    switch (cmd) {
    case FB_GET_INFO: {
        fb_info_t *info = arg;
        info->width  = s_width;
        info->height = s_height;
        info->bpp    = 16;
        info->stride = s_width * 2;
        return 0;
    }
    case FB_SET_POS: {
        const fb_pos_t *pos = arg;
        if (pos->x >= s_width || pos->y >= s_height) { errno = EINVAL; return -1; }
        p->pos = (uint32_t)pos->y * s_width * 2 + pos->x * 2;
        return 0;
    }
    case FB_FLUSH:
        xSemaphoreTake(s_lock, portMAX_DELAY);
        flush_rect(0, 0, s_width, s_height);
        xSemaphoreGive(s_lock);
        return 0;
    case FB_FLUSH_RECT: {
        const fb_rect_t *r = arg;
        if (!r || r->x + r->w > s_width || r->y + r->h > s_height) {
            errno = EINVAL; return -1;
        }
        xSemaphoreTake(s_lock, portMAX_DELAY);
        flush_rect(r->x, r->y, r->w, r->h);
        xSemaphoreGive(s_lock);
        return 0;
    }
    default:
        errno = ENOTTY;
        return -1;
    }
}

/* ---- registration --------------------------------------------------------- */

static const duneos_dev_driver_t s_drv_fb = {
    .name  = "fb0",
    .init  = fb_init,
    .open  = fb_open,
    .write = fb_write,
    .ioctl = fb_ioctl,
};

void drv_fb_st7789_register(void)
{
    duneos_dev_register(&s_drv_fb);
}
