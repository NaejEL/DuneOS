#include "duneos/gfx.h"
#include "duneos/font8x8.h"
#include "duneos/fb_ioctl.h"
#include "duneos/disp.h"

#include <fcntl.h>
#include <unistd.h>
#include <stdlib.h>
#include <string.h>

typedef enum { GFX_BACKEND_DISP, GFX_BACKEND_FB0 } gfx_backend_t;

struct gfx_ctx {
    gfx_backend_t  backend;
    uint16_t       width;
    uint16_t       height;
    uint16_t      *buf;      /* back-buffer: width * height pixels, host-endian RGB565 */
    union {
        int          fd;    /* GFX_BACKEND_FB0 */
        disp_ctx_t  *disp;  /* GFX_BACKEND_DISP — chip-agnostic SDK display */
    };
};

/* ---- open / close -------------------------------------------------------- */

/* Failure-step diagnostic — set whenever gfx_open returns NULL.
 * Apps read it via gfx_last_error() and use distinct duneos_exit() codes
 * to identify which step broke without needing kernel-side logs. */
static int s_gfx_last_error = 0;

int gfx_last_error(void) { return s_gfx_last_error; }

gfx_ctx_t *gfx_open(void)
{
    uint16_t w, h;
    gfx_backend_t backend;

    s_gfx_last_error = 0;

    /* Tier B: kernel PSRAM framebuffer */
    int fd = open("/dev/fb0", O_RDWR);
    if (fd >= 0) {
        fb_info_t info;
        if (ioctl(fd, FB_GET_INFO, &info) == 0) {
            w       = info.width;
            h       = info.height;
            backend = GFX_BACKEND_FB0;

            gfx_ctx_t *ctx = malloc(sizeof(*ctx));
            if (!ctx) { s_gfx_last_error = 2; close(fd); return NULL; }
            ctx->buf = malloc((size_t)w * h * sizeof(uint16_t));
            if (!ctx->buf) { s_gfx_last_error = 3; free(ctx); close(fd); return NULL; }
            ctx->fd      = fd;
            ctx->width   = w;
            ctx->height  = h;
            ctx->backend = backend;
            memset(ctx->buf, 0, (size_t)w * h * sizeof(uint16_t));
            return ctx;
        }
        s_gfx_last_error = 1;
        close(fd);
    }

    /* Tier A: chip-agnostic SDK display — backend selected from /flash/board.info */
    disp_ctx_t *disp = disp_open();
    if (disp) {
        w = disp_width(disp);
        h = disp_height(disp);
        gfx_ctx_t *ctx = malloc(sizeof(*ctx));
        if (!ctx) { s_gfx_last_error = 5; disp_close(disp); return NULL; }
        ctx->buf = malloc((size_t)w * h * sizeof(uint16_t));
        if (!ctx->buf) { s_gfx_last_error = 6; free(ctx); disp_close(disp); return NULL; }
        ctx->disp    = disp;
        ctx->width   = w;
        ctx->height  = h;
        ctx->backend = GFX_BACKEND_DISP;
        memset(ctx->buf, 0, (size_t)w * h * sizeof(uint16_t));
        return ctx;
    }
    if (s_gfx_last_error == 0) s_gfx_last_error = 4;

    return NULL;
}

void gfx_close(gfx_ctx_t *ctx)
{
    if (!ctx) return;
    if (ctx->backend == GFX_BACKEND_FB0)
        close(ctx->fd);
    else
        disp_close(ctx->disp);
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
        /* Tier A: chip-agnostic full-frame blit via libdisp */
        disp_write_area(ctx->disp, 0, 0, ctx->width, ctx->height, ctx->buf);
    }
}
