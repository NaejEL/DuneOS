#include "duneos/dev_driver.h"
#include "duneos/disp_ioctl.h"
#include "duneos/klog.h"
#include "board_config.h"
#include "st7789_hw.h"

#include <string.h>
#include <errno.h>

static const char *TAG = "duneos/disp0";

/* Chunk buffer in internal DRAM — must be DMA-accessible.
 * Receives byte-swapped pixels before each SPI DMA transfer. */
#define CHUNK_PIXELS  512
static uint16_t s_chunk[CHUNK_PIXELS];

/* ---- driver callbacks ----------------------------------------------------- */

static int disp_init(void)
{
    esp_err_t err = st7789_hw_init();
    if (err == ESP_OK)
        klog_i(TAG, "/dev/disp0 ready: %ux%u streaming",
               st7789_hw_width(), st7789_hw_height());
    return (err == ESP_OK) ? 0 : -1;
}

static ssize_t disp_write(duneos_devfd_t *fd, const void *buf, size_t len)
{
    (void)fd;
    if (len & 1) { errno = EINVAL; return -1; }

    const uint8_t *src = (const uint8_t *)buf;
    size_t done = 0;

    while (done < len) {
        size_t batch = (len - done) / 2;
        if (batch > CHUNK_PIXELS) batch = CHUNK_PIXELS;

        /* Byte-swap each host-endian uint16_t → big-endian for ST7789 SPI */
        for (size_t i = 0; i < batch; i++) {
            uint16_t px = (uint16_t)src[done + i*2] |
                          ((uint16_t)src[done + i*2 + 1] << 8);
            s_chunk[i] = (px >> 8) | (px << 8);
        }
        st7789_hw_data(s_chunk, batch * 2);
        done += batch * 2;
    }
    return (ssize_t)len;
}

static int disp_ioctl(duneos_devfd_t *fd, int cmd, void *arg)
{
    (void)fd;
    switch (cmd) {
    case DISP_GET_INFO: {
        disp_info_t *info = arg;
        info->width    = st7789_hw_width();
        info->height   = st7789_hw_height();
        info->rotation = st7789_hw_rotation();
        return 0;
    }
    case DISP_SET_WINDOW: {
        const disp_window_t *win = arg;
        if (!win || win->w == 0 || win->h == 0 ||
            (uint32_t)win->x + win->w > st7789_hw_width() ||
            (uint32_t)win->y + win->h > st7789_hw_height()) {
            errno = EINVAL;
            return -1;
        }
        st7789_hw_set_window(win->x, win->y, win->w, win->h);
        return 0;
    }
    default:
        errno = ENOTTY;
        return -1;
    }
}

/* ---- registration --------------------------------------------------------- */

static const duneos_dev_driver_t s_drv_disp = {
    .name  = "disp0",
    .init  = disp_init,
    .write = disp_write,
    .ioctl = disp_ioctl,
};

void drv_disp_st7789_register(void)
{
    duneos_dev_register(&s_drv_disp);
}
