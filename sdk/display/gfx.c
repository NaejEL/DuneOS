#include "duneos/gfx.h"
#include "duneos/font8x8.h"
#include "duneos/disp_ioctl.h"
#include "duneos/fb_ioctl.h"

#include <fcntl.h>
#include <unistd.h>
#include <stdlib.h>
#include <string.h>

typedef enum { GFX_BACKEND_DISP0, GFX_BACKEND_FB0 } gfx_backend_t;

struct gfx_ctx {
    int           fd;
    uint16_t      width;
    uint16_t      height;
    uint16_t     *buf;      /* back-buffer: width * height pixels, host-endian RGB565 */
    gfx_backend_t backend;
};

/* ---- open / close -------------------------------------------------------- */

gfx_ctx_t *gfx_open(void)
{
    int fd;
    uint16_t w, h;
    gfx_backend_t backend;

    /* Tier B: kernel PSRAM framebuffer */
    fd = open("/dev/fb0", O_RDWR);
    if (fd >= 0) {
        fb_info_t info;
        if (ioctl(fd, FB_GET_INFO, &info) == 0) {
            w = info.width;
            h = info.height;
            backend = GFX_BACKEND_FB0;
            goto alloc;
        }
        close(fd);
    }

    /* Tier A: streaming display driver */
    fd = open("/dev/disp0", O_WRONLY);
    if (fd >= 0) {
        disp_info_t info;
        if (ioctl(fd, DISP_GET_INFO, &info) == 0) {
            w = info.width;
            h = info.height;
            backend = GFX_BACKEND_DISP0;
            goto alloc;
        }
        close(fd);
    }

    return NULL;

alloc:;
    gfx_ctx_t *ctx = malloc(sizeof(*ctx));
    if (!ctx) { close(fd); return NULL; }

    ctx->buf = malloc((size_t)w * h * sizeof(uint16_t));
    if (!ctx->buf) { free(ctx); close(fd); return NULL; }

    ctx->fd      = fd;
    ctx->width   = w;
    ctx->height  = h;
    ctx->backend = backend;
    memset(ctx->buf, 0, (size_t)w * h * sizeof(uint16_t));
    return ctx;
}

void gfx_close(gfx_ctx_t *ctx)
{
    if (!ctx) return;
    close(ctx->fd);
    free(ctx->buf);
    free(ctx);
}

void gfx_get_info(const gfx_ctx_t *ctx, uint16_t *w, uint16_t *h)
{
    if (!ctx) return;
    if (w) *w = ctx->width;
    if (h) *h = ctx->height;
}

/* ---- drawing ------------------------------------------------------------- */

void gfx_fill(gfx_ctx_t *ctx, uint16_t color)
{
    if (!ctx) return;
    size_t n = (size_t)ctx->width * ctx->height;
    for (size_t i = 0; i < n; i++)
        ctx->buf[i] = color;
}

void gfx_pixel(gfx_ctx_t *ctx, int x, int y, uint16_t color)
{
    if (!ctx || x < 0 || y < 0 || x >= ctx->width || y >= ctx->height) return;
    ctx->buf[(size_t)y * ctx->width + x] = color;
}

void gfx_rect(gfx_ctx_t *ctx, int x, int y, int w, int h, uint16_t color)
{
    if (!ctx || w <= 0 || h <= 0) return;
    int x1 = x < 0 ? 0 : x;
    int y1 = y < 0 ? 0 : y;
    int x2 = x + w > ctx->width  ? ctx->width  : x + w;
    int y2 = y + h > ctx->height ? ctx->height : y + h;
    for (int row = y1; row < y2; row++)
        for (int col = x1; col < x2; col++)
            ctx->buf[(size_t)row * ctx->width + col] = color;
}

void gfx_text(gfx_ctx_t *ctx, int x, int y,
              const char *s, uint16_t fg, uint16_t bg)
{
    if (!ctx || !s) return;
    int cx = x, cy = y;
    for (; *s; s++) {
        unsigned char c = (unsigned char)*s;
        if (c == '\n') {
            cx = x;
            cy += 8;
            continue;
        }
        if (c < 32 || c > 127) c = '?';
        const uint8_t *glyph = font8x8_basic[c - 32];
        for (int row = 0; row < 8; row++) {
            for (int col = 0; col < 8; col++) {
                int px = cx + col, py = cy + row;
                if (px < 0 || py < 0 || px >= ctx->width || py >= ctx->height)
                    continue;
                uint16_t color = (glyph[row] >> col) & 1 ? fg : bg;
                ctx->buf[(size_t)py * ctx->width + px] = color;
            }
        }
        cx += 8;
        if (cx + 8 > ctx->width) {
            cx = x;
            cy += 8;
        }
    }
}

/* ---- flush --------------------------------------------------------------- */

void gfx_flush(gfx_ctx_t *ctx)
{
    if (!ctx) return;
    size_t total = (size_t)ctx->width * ctx->height * sizeof(uint16_t);

    if (ctx->backend == GFX_BACKEND_FB0) {
        /* Tier B: write entire buffer into kernel PSRAM buffer, then flush to SPI */
        fb_pos_t pos = { .x = 0, .y = 0 };
        ioctl(ctx->fd, FB_SET_POS, &pos);
        write(ctx->fd, ctx->buf, total);
        ioctl(ctx->fd, FB_FLUSH, NULL);
    } else {
        /* Tier A: set full-screen window and stream pixel data */
        disp_window_t win = {
            .x = 0, .y = 0,
            .w = ctx->width, .h = ctx->height,
        };
        ioctl(ctx->fd, DISP_SET_WINDOW, &win);
        write(ctx->fd, ctx->buf, total);
    }
}
