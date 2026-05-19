#include "duneos/st7789.h"

#include "driver/spi_master.h"
#include "driver/gpio.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"

#include <fcntl.h>
#include <unistd.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

static const char *TAG = "libst7789";

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

struct st7789_handle {
    spi_device_handle_t spi;
    spi_host_device_t   host;
    int                 dc_pin;
    int                 bl_pin;
    uint16_t            width;
    uint16_t            height;
    bool                bus_owned; /* true → we called spi_bus_initialize */
};

/* Line buffer for row-by-row pixel transfer — avoids heap alloc per call.
 * 320 covers the widest ST7789 variant.  Not thread-safe (single-app model). */
static uint16_t s_line[320];

/* ---- low-level SPI helpers ----------------------------------------------- */

static inline void send_cmd(st7789_handle_t *h, uint8_t c)
{
    gpio_set_level(h->dc_pin, 0);
    spi_transaction_t t = {
        .length  = 8,
        .flags   = SPI_TRANS_USE_TXDATA,
        .tx_data = { c },
    };
    spi_device_transmit(h->spi, &t);
}

static inline void send_data(st7789_handle_t *h, const void *buf, size_t len)
{
    if (!len) return;
    gpio_set_level(h->dc_pin, 1);
    spi_transaction_t t = {
        .length    = len * 8,
        .tx_buffer = buf,
    };
    spi_device_transmit(h->spi, &t);
}

static inline void send_u8(st7789_handle_t *h, uint8_t b)
{
    send_data(h, &b, 1);
}

/* ---- window / pixel helpers ---------------------------------------------- */

static void set_window(st7789_handle_t *h,
                       uint16_t x, uint16_t y, uint16_t w, uint16_t ht)
{
    uint8_t buf[4];
    uint16_t xe = x + w - 1;
    uint16_t ye = y + ht - 1;

    buf[0] = x  >> 8; buf[1] = x  & 0xFF;
    buf[2] = xe >> 8; buf[3] = xe & 0xFF;
    send_cmd(h, ST7789_CASET);
    send_data(h, buf, 4);

    buf[0] = y  >> 8; buf[1] = y  & 0xFF;
    buf[2] = ye >> 8; buf[3] = ye & 0xFF;
    send_cmd(h, ST7789_RASET);
    send_data(h, buf, 4);

    send_cmd(h, ST7789_RAMWR);
}

/* Send one row of pre-swapped pixels (big-endian) already in s_line[]. */
static void flush_line(st7789_handle_t *h, uint16_t w)
{
    gpio_set_level(h->dc_pin, 1);
    spi_transaction_t t = {
        .length    = (size_t)w * 16,
        .tx_buffer = s_line,
    };
    spi_device_transmit(h->spi, &t);
}

/* ---- public API ----------------------------------------------------------- */

static const uint8_t s_madctl_table[4] = {
    ST7789_MADCTL_ROT0,
    ST7789_MADCTL_ROT1,
    ST7789_MADCTL_ROT2,
    ST7789_MADCTL_ROT3,
};

st7789_handle_t *st7789_open(const st7789_config_t *cfg)
{
    if (!cfg) return NULL;

    st7789_handle_t *h = malloc(sizeof(*h));
    if (!h) return NULL;

    h->dc_pin    = cfg->dc_pin;
    h->bl_pin    = cfg->bl_pin;
    h->width     = cfg->width;
    h->height    = cfg->height;
    h->host      = cfg->spi_host;
    h->bus_owned = false;
    h->spi       = NULL;

    /* Configure output GPIOs: DC, RST (optional), BL (optional) */
    uint64_t pin_mask = (1ULL << cfg->dc_pin);
    if (cfg->rst_pin >= 0) pin_mask |= (1ULL << cfg->rst_pin);
    if (cfg->bl_pin  >= 0) pin_mask |= (1ULL << cfg->bl_pin);

    gpio_config_t gcfg = {
        .pin_bit_mask = pin_mask,
        .mode         = GPIO_MODE_OUTPUT,
        .pull_up_en   = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type    = GPIO_INTR_DISABLE,
    };
    if (gpio_config(&gcfg) != ESP_OK) {
        ESP_LOGE(TAG, "gpio_config failed");
        free(h);
        return NULL;
    }

    /* SPI bus — skip init when the bus was already brought up (e.g. for SD) */
    if (!cfg->bus_already_init) {
        spi_bus_config_t bus = {
            .mosi_io_num     = cfg->mosi_pin,
            .miso_io_num     = -1,
            .sclk_io_num     = cfg->clk_pin,
            .quadwp_io_num   = -1,
            .quadhd_io_num   = -1,
            /* Pre-allocate for a full framebuffer DMA transfer */
            .max_transfer_sz = (int)cfg->width * cfg->height * 2,
        };
        esp_err_t err = spi_bus_initialize(cfg->spi_host, &bus, SPI_DMA_CH_AUTO);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "spi_bus_initialize: %s", esp_err_to_name(err));
            free(h);
            return NULL;
        }
        h->bus_owned = true;
    }

    spi_device_interface_config_t dev = {
        .mode           = 0,
        .clock_speed_hz = (int)cfg->freq_hz,
        .spics_io_num   = cfg->cs_pin,
        .queue_size     = 1,
    };
    if (spi_bus_add_device(cfg->spi_host, &dev, &h->spi) != ESP_OK) {
        ESP_LOGE(TAG, "spi_bus_add_device failed");
        if (h->bus_owned) spi_bus_free(cfg->spi_host);
        free(h);
        return NULL;
    }

    /* Hardware reset */
    if (cfg->rst_pin >= 0) {
        gpio_set_level(cfg->rst_pin, 0);
        vTaskDelay(pdMS_TO_TICKS(10));
        gpio_set_level(cfg->rst_pin, 1);
        vTaskDelay(pdMS_TO_TICKS(120));
    }

    /* Init sequence */
    send_cmd(h, ST7789_SWRESET); vTaskDelay(pdMS_TO_TICKS(120));
    send_cmd(h, ST7789_SLPOUT);  vTaskDelay(pdMS_TO_TICKS(120));
    send_cmd(h, ST7789_COLMOD);  send_u8(h, 0x55); /* 16-bit RGB565 */
    send_cmd(h, ST7789_MADCTL);
    send_u8(h, s_madctl_table[cfg->rotation & 0x3]);
    send_cmd(h, ST7789_INVON);                      /* most panels need inversion */
    send_cmd(h, ST7789_NORON);   vTaskDelay(pdMS_TO_TICKS(10));
    send_cmd(h, ST7789_DISPON);  vTaskDelay(pdMS_TO_TICKS(10));

    if (cfg->bl_pin >= 0)
        gpio_set_level(cfg->bl_pin, 1);

    ESP_LOGI(TAG, "ST7789 %ux%u ready", cfg->width, cfg->height);
    return h;
}

void st7789_close(st7789_handle_t *h)
{
    if (!h) return;
    if (h->bl_pin >= 0) gpio_set_level(h->bl_pin, 0);
    if (h->spi)  spi_bus_remove_device(h->spi);
    if (h->bus_owned) spi_bus_free(h->host);
    free(h);
}

void st7789_fill(st7789_handle_t *h, uint16_t color)
{
    if (!h) return;
    /* Pre-fill the line buffer with the byte-swapped colour */
    uint16_t swapped = (color >> 8) | (color << 8);
    for (int i = 0; i < h->width && i < (int)(sizeof(s_line) / sizeof(s_line[0])); i++)
        s_line[i] = swapped;

    set_window(h, 0, 0, h->width, h->height);
    for (uint16_t row = 0; row < h->height; row++)
        flush_line(h, h->width);
}

void st7789_write_area(st7789_handle_t *h,
                       uint16_t x, uint16_t y,
                       uint16_t w, uint16_t height_px,
                       const uint16_t *pixels)
{
    if (!h || !pixels || !w || !height_px) return;

    uint16_t clamped_w = w;
    if (clamped_w > (uint16_t)(sizeof(s_line) / sizeof(s_line[0])))
        clamped_w = (uint16_t)(sizeof(s_line) / sizeof(s_line[0]));

    set_window(h, x, y, clamped_w, height_px);
    for (uint16_t row = 0; row < height_px; row++) {
        /* Byte-swap each pixel from host-endian → big-endian for ST7789 */
        for (uint16_t col = 0; col < clamped_w; col++) {
            uint16_t px = pixels[(uint32_t)row * w + col];
            s_line[col] = (px >> 8) | (px << 8);
        }
        flush_line(h, clamped_w);
    }
}

void st7789_set_backlight(st7789_handle_t *h, bool on)
{
    if (!h || h->bl_pin < 0) return;
    gpio_set_level(h->bl_pin, on ? 1 : 0);
}

/* ---- board.info-aware open ---------------------------------------------- */

/*
 * Parse a single integer field from a board.info text buffer.
 * Returns def if the key is not found.
 */
static int bi_int(const char *buf, size_t len, const char *key, int def)
{
    char pattern[64];
    int plen = snprintf(pattern, sizeof(pattern), "%s: ", key);
    const char *p = buf;
    const char *end = buf + len;
    while (p < end) {
        if ((size_t)(end - p) > (size_t)plen && strncmp(p, pattern, plen) == 0)
            return atoi(p + plen);
        p = memchr(p, '\n', (size_t)(end - p));
        if (!p) break;
        p++;
    }
    return def;
}

st7789_handle_t *st7789_open_from_board_info(uint16_t *out_width, uint16_t *out_height)
{
    int fd = open("/flash/board.info", O_RDONLY);
    if (fd < 0) return NULL;

    char buf[512];
    ssize_t n = read(fd, buf, sizeof(buf) - 1);
    close(fd);
    if (n <= 0) return NULL;
    buf[n] = '\0';

    st7789_config_t cfg = {
        .spi_host         = (spi_host_device_t)bi_int(buf, (size_t)n, "display_spi_host", -1),
        .mosi_pin         = bi_int(buf, (size_t)n, "display_mosi",       -1),
        .clk_pin          = bi_int(buf, (size_t)n, "display_clk",        -1),
        .cs_pin           = bi_int(buf, (size_t)n, "display_cs",         -1),
        .dc_pin           = bi_int(buf, (size_t)n, "display_dc",         -1),
        .rst_pin          = bi_int(buf, (size_t)n, "display_rst",        -1),
        .bl_pin           = bi_int(buf, (size_t)n, "display_bl",         -1),
        .freq_hz          = (uint32_t)bi_int(buf, (size_t)n, "display_freq_hz",   40000000),
        .width            = (uint16_t)bi_int(buf, (size_t)n, "width",             240),
        .height           = (uint16_t)bi_int(buf, (size_t)n, "height",            135),
        .rotation         = (uint8_t)bi_int(buf, (size_t)n, "display_rotation",    0),
        .bus_already_init = (bi_int(buf, (size_t)n, "display_bus_shared",  0) != 0),
    };

    if (cfg.spi_host < 0 || cfg.mosi_pin < 0 || cfg.clk_pin < 0
        || cfg.cs_pin < 0 || cfg.dc_pin < 0) {
        ESP_LOGE(TAG, "board.info missing required display fields");
        return NULL;
    }

    st7789_handle_t *h = st7789_open(&cfg);
    if (h && out_width)  *out_width  = cfg.width;
    if (h && out_height) *out_height = cfg.height;
    return h;
}

/* ---- libdisp ops table ---------------------------------------------------
 *
 * This block makes libst7789.c a self-contained libdisp backend.  Link it
 * alongside libdisp.c and the linker resolves duneos_disp_ops here.
 * A board using a different chip links a different lib that defines the same
 * symbol — libdisp.c and every app remain untouched.
 * -------------------------------------------------------------------------- */

#include "duneos/disp.h"
#include <stdlib.h>

struct disp_ctx_s {
    st7789_handle_t *hw;
    uint16_t         width;
    uint16_t         height;
};

static disp_ctx_t *_st7789_open(void)
{
    uint16_t w, h;
    st7789_handle_t *hw = st7789_open_from_board_info(&w, &h);
    if (!hw) return NULL;
    disp_ctx_t *ctx = malloc(sizeof(*ctx));
    if (!ctx) { st7789_close(hw); return NULL; }
    ctx->hw = hw; ctx->width = w; ctx->height = h;
    return ctx;
}

static uint16_t    _st7789_width(const disp_ctx_t *ctx)  { return ctx->width; }
static uint16_t    _st7789_height(const disp_ctx_t *ctx) { return ctx->height; }

static void _st7789_write_area(disp_ctx_t *ctx,
                               uint16_t x, uint16_t y,
                               uint16_t w, uint16_t h,
                               const uint16_t *pixels)
{
    st7789_write_area(ctx->hw, x, y, w, h, pixels);
}

static void _st7789_close(disp_ctx_t *ctx)
{
    if (!ctx) return;
    st7789_close(ctx->hw);
    free(ctx);
}

const disp_backend_ops_t duneos_disp_ops = {
    .open       = _st7789_open,
    .width      = _st7789_width,
    .height     = _st7789_height,
    .write_area = _st7789_write_area,
    .close      = _st7789_close,
};
