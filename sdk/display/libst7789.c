/*
 * libst7789 — ST7789 userspace display library for DuneOS.
 *
 * Talks to the kernel-owned SPI and GPIO buses through their public ioctl
 * interfaces (/dev/spi-N, /dev/gpiochip0). Contains NO ESP-IDF or FreeRTOS
 * dependencies — it is a pure-C userspace library, suitable for any DuneOS
 * arch where libst7789 is linked into a .dap.
 *
 * Configuration is read at runtime from /flash/board.info (written by the
 * kernel at boot). The kernel BSP knows the pins, frequency, MADCTL, and
 * column/row offsets for the active board; the library never #includes
 * board_config.h.
 *
 * This library implements duneos_disp_ops (see duneos/disp.h), so apps
 * link libdisp.c + libst7789.c and call disp_open() / disp_write_area().
 */

#include "duneos/disp.h"
#include "duneos/spi_ioctl.h"
#include "duneos/gpio_ioctl.h"

#include <fcntl.h>
#include <unistd.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <errno.h>

/* PicoLibc hides usleep() by default; the kernel exports it. */
extern int usleep(unsigned int useconds);

/* ST7789 command bytes */
#define ST7789_SWRESET  0x01
#define ST7789_SLPOUT   0x11
#define ST7789_NORON    0x13
#define ST7789_INVON    0x21
#define ST7789_DISPON   0x29
#define ST7789_CASET    0x2A
#define ST7789_RASET    0x2B
#define ST7789_RAMWR    0x2C
#define ST7789_MADCTL   0x36
#define ST7789_COLMOD   0x3A

/* Display config parsed from /flash/board.info — all fields needed by the
 * driver, no kernel-side board_config.h ever included in userspace. */
typedef struct {
    int      spi_host;        /* informational; we use the global /dev/spi-N */
    int      mosi, clk, cs, dc, rst, bl;
    uint32_t freq_hz;
    uint16_t width, height;
    uint8_t  madctl;
    uint8_t  swap_xy;
    uint16_t col_offset, row_offset;
    uint8_t  bus_shared;
} st7789_cfg_t;

struct disp_ctx_s {
    int          spi_fd;
    int          gpio_fd;
    st7789_cfg_t cfg;
};

/* ---- /flash/board.info parser ------------------------------------------- */

static int read_board_info(char *buf, size_t bufsize)
{
    int fd = open("/flash/board.info", O_RDONLY);
    if (fd < 0) {
        fd = open("/sd/board.info", O_RDONLY);
        if (fd < 0) return -ENOENT;
    }
    int n = (int)read(fd, buf, bufsize - 1);
    close(fd);
    if (n <= 0) return -EIO;
    buf[n] = '\0';
    return n;
}

/* Find "key: value" in board.info and parse value as integer (decimal).
 * Returns 0 on success, -ENOENT if the key is missing. */
static int kv_int(const char *src, const char *key, long *out)
{
    size_t klen = strlen(key);
    const char *p = src;
    while (*p) {
        if (!strncmp(p, key, klen) && p[klen] == ':') {
            p += klen + 1;
            while (*p == ' ' || *p == '\t') p++;
            *out = strtol(p, NULL, 0);
            return 0;
        }
        const char *nl = strchr(p, '\n');
        if (!nl) break;
        p = nl + 1;
    }
    return -ENOENT;
}

static int parse_board_info(st7789_cfg_t *cfg)
{
    char buf[768];
    if (read_board_info(buf, sizeof(buf)) < 0) return -ENOENT;

    long v;
    if (kv_int(buf, "width",  &v) < 0) return -EINVAL;
    cfg->width  = (uint16_t)v;
    if (kv_int(buf, "height", &v) < 0) return -EINVAL;
    cfg->height = (uint16_t)v;
    if (kv_int(buf, "display_dc", &v) < 0) return -EINVAL;
    cfg->dc = (int)v;

    if (kv_int(buf, "display_spi_host",   &v) == 0) cfg->spi_host = (int)v;
    if (kv_int(buf, "display_mosi",       &v) == 0) cfg->mosi     = (int)v;
    if (kv_int(buf, "display_clk",        &v) == 0) cfg->clk      = (int)v;
    if (kv_int(buf, "display_cs",         &v) == 0) cfg->cs       = (int)v;
    if (kv_int(buf, "display_rst",        &v) == 0) cfg->rst      = (int)v; else cfg->rst = -1;
    if (kv_int(buf, "display_bl",         &v) == 0) cfg->bl       = (int)v; else cfg->bl  = -1;
    if (kv_int(buf, "display_freq_hz",    &v) == 0) cfg->freq_hz  = (uint32_t)v; else cfg->freq_hz = 10000000;
    if (kv_int(buf, "display_madctl",     &v) == 0) cfg->madctl     = (uint8_t)v;
    if (kv_int(buf, "display_swap_xy",    &v) == 0) cfg->swap_xy    = (uint8_t)v;
    if (kv_int(buf, "display_col_offset", &v) == 0) cfg->col_offset = (uint16_t)v;
    if (kv_int(buf, "display_row_offset", &v) == 0) cfg->row_offset = (uint16_t)v;

    /* If no display fields were found, /flash/board.info doesn't describe
     * a display at all — refuse to open. */
    if (cfg->width == 0 || cfg->height == 0) return -ENODEV;
    return 0;
}

/* ---- SPI / GPIO helpers ------------------------------------------------- */

/* Build the /dev/spi-N path from the BSP host id. */
static int open_spi(int host_id, uint32_t freq_hz, int cs_pin)
{
    char path[16];
    snprintf(path, sizeof(path), "/dev/spi-%d", host_id);
    int fd = open(path, O_RDWR);
    if (fd < 0) {
        /* The BSP may declare host=2 but the kernel exposes the bus as
         * /dev/spi-1. Try a small set of common indices as a fallback. */
        for (int i = 0; i <= 3 && fd < 0; i++) {
            snprintf(path, sizeof(path), "/dev/spi-%d", i);
            fd = open(path, O_RDWR);
        }
        if (fd < 0) return -1;
    }
    uint8_t mode = 0;                         ioctl(fd, SPI_SET_MODE,  &mode);
    uint32_t hz  = freq_hz;                   ioctl(fd, SPI_SET_SPEED, &hz);
    int cs       = cs_pin;                    ioctl(fd, SPI_SET_CS,    &cs);
    return fd;
}

static void gpio_set_out(int fd, int line, int val)
{
    gpio_req_t req = { .line = (uint8_t)line, .dir = 1 /* OUTPUT */, .val = (uint8_t)val };
    ioctl(fd, GPIOCHIP_SET_DIR,   &req);
    ioctl(fd, GPIOCHIP_SET_VALUE, &req);
}

static void gpio_write(int fd, int line, int val)
{
    gpio_req_t req = { .line = (uint8_t)line, .val = (uint8_t)val };
    ioctl(fd, GPIOCHIP_SET_VALUE, &req);
}

/* ---- SPI command / data primitives -------------------------------------- */

static void st_cmd(disp_ctx_t *c, uint8_t cmd)
{
    gpio_write(c->gpio_fd, c->cfg.dc, 0);   /* DC low = command */
    write(c->spi_fd, &cmd, 1);
}

static void st_data(disp_ctx_t *c, const void *data, size_t len)
{
    if (!len) return;
    gpio_write(c->gpio_fd, c->cfg.dc, 1);   /* DC high = data */
    /* Chunk large transfers — many kernel SPI drivers cap a single ioctl
     * transfer (typically 4 KiB). 2048 bytes is a safe ceiling. */
    const uint8_t *p = data;
    while (len) {
        size_t chunk = len > 2048 ? 2048 : len;
        write(c->spi_fd, p, chunk);
        p   += chunk;
        len -= chunk;
    }
}

static void st_set_window(disp_ctx_t *c, uint16_t x, uint16_t y, uint16_t w, uint16_t h)
{
    uint16_t cs, ce, rs, re;
    if (c->cfg.swap_xy) {
        cs = x + c->cfg.row_offset; ce = cs + w - 1;
        rs = y + c->cfg.col_offset; re = rs + h - 1;
    } else {
        cs = x + c->cfg.col_offset; ce = cs + w - 1;
        rs = y + c->cfg.row_offset; re = rs + h - 1;
    }
    uint8_t buf[4];

    buf[0] = cs >> 8; buf[1] = cs & 0xFF;
    buf[2] = ce >> 8; buf[3] = ce & 0xFF;
    st_cmd(c, ST7789_CASET);
    st_data(c, buf, 4);

    buf[0] = rs >> 8; buf[1] = rs & 0xFF;
    buf[2] = re >> 8; buf[3] = re & 0xFF;
    st_cmd(c, ST7789_RASET);
    st_data(c, buf, 4);

    st_cmd(c, ST7789_RAMWR);
}

/* ---- backend ops (disp_backend_ops_t) ---------------------------------- */

static disp_ctx_t *st7789_backend_open(void)
{
    disp_ctx_t *c = calloc(1, sizeof(*c));
    if (!c) return NULL;
    c->spi_fd = c->gpio_fd = -1;
    c->cfg.rst = c->cfg.bl = -1;

    if (parse_board_info(&c->cfg) < 0) { free(c); return NULL; }

    /* SPI bus: try declared host first, fall back to /dev/spi-0..3. */
    c->spi_fd = open_spi(c->cfg.spi_host, c->cfg.freq_hz, c->cfg.cs);
    if (c->spi_fd < 0) { free(c); return NULL; }

    /* GPIO chip 0 — DC mandatory, RST + BL optional. */
    c->gpio_fd = open("/dev/gpiochip0", O_RDWR);
    if (c->gpio_fd < 0) { close(c->spi_fd); free(c); return NULL; }
    gpio_set_out(c->gpio_fd, c->cfg.dc, 1);
    if (c->cfg.rst >= 0) gpio_set_out(c->gpio_fd, c->cfg.rst, 1);
    if (c->cfg.bl  >= 0) gpio_set_out(c->gpio_fd, c->cfg.bl,  0);

    /* Hardware reset: drive RST low ~10 ms, release, wait ~120 ms. */
    if (c->cfg.rst >= 0) {
        gpio_write(c->gpio_fd, c->cfg.rst, 0);
        usleep(10 * 1000);
        gpio_write(c->gpio_fd, c->cfg.rst, 1);
        usleep(120 * 1000);
    }

    /* ST7789 init sequence (mirrors kernel st7789_hw.c, derived from datasheet). */
    st_cmd(c, ST7789_SWRESET); usleep(120 * 1000);
    st_cmd(c, ST7789_SLPOUT);  usleep(120 * 1000);
    st_cmd(c, ST7789_COLMOD);  { uint8_t v = 0x55;            st_data(c, &v, 1); }
    st_cmd(c, ST7789_MADCTL);  { uint8_t v = c->cfg.madctl;   st_data(c, &v, 1); }
    st_cmd(c, ST7789_INVON);
    st_cmd(c, ST7789_NORON);   usleep(10 * 1000);
    st_cmd(c, ST7789_DISPON);  usleep(10 * 1000);

    /* Backlight on. */
    if (c->cfg.bl >= 0) gpio_write(c->gpio_fd, c->cfg.bl, 1);

    return c;
}

static uint16_t st7789_backend_width(const disp_ctx_t *c)  { return c->cfg.width;  }
static uint16_t st7789_backend_height(const disp_ctx_t *c) { return c->cfg.height; }

/* ST7789 expects big-endian RGB565 over the wire. We byte-swap into a small
 * line buffer to keep the userspace caller free to pass native-endian data. */
static void st7789_backend_write_area(disp_ctx_t *c,
                                       uint16_t x, uint16_t y,
                                       uint16_t w, uint16_t h,
                                       const uint16_t *pixels)
{
    if (!c || !pixels || !w || !h) return;
    st_set_window(c, x, y, w, h);

    uint16_t line[320];   /* widest ST7789 variant */
    if (w > 320) return;
    for (uint16_t row = 0; row < h; row++) {
        for (uint16_t col = 0; col < w; col++) {
            uint16_t p = pixels[row * w + col];
            line[col] = (uint16_t)((p << 8) | (p >> 8));   /* host → big-endian */
        }
        st_data(c, line, (size_t)w * 2);
    }
}

static void st7789_backend_close(disp_ctx_t *c)
{
    if (!c) return;
    if (c->cfg.bl >= 0) gpio_write(c->gpio_fd, c->cfg.bl, 0);   /* backlight off */
    if (c->spi_fd  >= 0) close(c->spi_fd);
    if (c->gpio_fd >= 0) close(c->gpio_fd);
    free(c);
}

/* Backend ops table consumed by libdisp.c — strong symbol; multiple chip
 * libraries linked in the same .dap is not supported (one chip per app). */
const disp_backend_ops_t duneos_disp_ops = {
    .open       = st7789_backend_open,
    .width      = st7789_backend_width,
    .height     = st7789_backend_height,
    .write_area = st7789_backend_write_area,
    .close      = st7789_backend_close,
};
