#include "duneos/gfx.h"
#include "duneos/font8x8.h"
#include "duneos/fb_ioctl.h"
#include "duneos/disp.h"

#include <fcntl.h>
#include <unistd.h>
#include <stdlib.h>
#include <string.h>

typedef enum { GFX_BACKEND_DISP, GFX_BACKEND_FB0, GFX_BACKEND_NONE } gfx_backend_t;

struct gfx_ctx {
    gfx_backend_t  backend;
    gfx_mode_t     mode;
    uint16_t       width;
    uint16_t       height;
    uint16_t      *buf;      /* BUFFERED only; NULL in STREAM */
    union {
        int          fd;     /* GFX_BACKEND_FB0 */
        disp_ctx_t  *disp;   /* GFX_BACKEND_DISP */
    };
};

/* ---- open / close -------------------------------------------------------- */

/* Failure-step diagnostic — set whenever gfx_open returns NULL.
 * Apps read it via gfx_last_error() and use distinct duneos_exit() codes
 * to identify which step broke without needing kernel-side logs. */
static int s_gfx_last_error = 0;

int gfx_last_error(void) { return s_gfx_last_error; }

gfx_ctx_t *gfx_open_mode(gfx_mode_t mode)
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
            ctx->buf = NULL;
            if (mode == GFX_MODE_BUFFERED) {
                ctx->buf = malloc((size_t)w * h * sizeof(uint16_t));
                if (!ctx->buf) { s_gfx_last_error = 3; free(ctx); close(fd); return NULL; }
                memset(ctx->buf, 0, (size_t)w * h * sizeof(uint16_t));
            }
            ctx->fd      = fd;
            ctx->width   = w;
            ctx->height  = h;
            ctx->backend = backend;
            ctx->mode    = mode;
            return ctx;
        }
        s_gfx_last_error = 1;
        close(fd);
    }

    /* Tier A: chip-agnostic SDK display via libdisp */
    disp_ctx_t *disp = disp_open();
    if (disp) {
        w = disp_width(disp);
        h = disp_height(disp);
        gfx_ctx_t *ctx = malloc(sizeof(*ctx));
        if (!ctx) { s_gfx_last_error = 5; disp_close(disp); return NULL; }
        ctx->buf = NULL;
        if (mode == GFX_MODE_BUFFERED) {
            ctx->buf = malloc((size_t)w * h * sizeof(uint16_t));
            if (!ctx->buf) { s_gfx_last_error = 6; free(ctx); disp_close(disp); return NULL; }
            memset(ctx->buf, 0, (size_t)w * h * sizeof(uint16_t));
        }
        ctx->disp    = disp;
        ctx->width   = w;
        ctx->height  = h;
        ctx->backend = GFX_BACKEND_DISP;
        ctx->mode    = mode;
        return ctx;
    }
    if (s_gfx_last_error == 0) s_gfx_last_error = 4;

    return NULL;
}

gfx_ctx_t *gfx_open(void) { return gfx_open_mode(GFX_MODE_BUFFERED); }

void gfx_close(gfx_ctx_t *ctx)
{
    if (!ctx) return;
    if (ctx->backend == GFX_BACKEND_FB0)
        close(ctx->fd);
    else if (ctx->backend == GFX_BACKEND_DISP)
        disp_close(ctx->disp);
    /* GFX_BACKEND_NONE (offscreen canvas): no device to close. */
    if (ctx->buf) free(ctx->buf);
    free(ctx);
}

/* ---- offscreen canvas (partial rendering) ------------------------------- */

/* An offscreen canvas is just a BUFFERED gfx_ctx with no backing device: all
 * the gfx_* primitives already draw into ctx->buf with ctx->width as stride,
 * so a canvas reuses every drawing op unchanged. The app draws a moving region
 * into the canvas in RAM, then pushes it to the live display in ONE blit — the
 * TFT_eSPI/LVGL sprite model. No full-screen clear on the panel ⇒ no flicker.
 * RAM cost is w*h*2 bytes, so size the canvas to the region that moves, not the
 * whole screen. */
gfx_ctx_t *gfx_canvas_new(int w, int h)
{
    if (w <= 0 || h <= 0) return NULL;
    gfx_ctx_t *ctx = malloc(sizeof(*ctx));
    if (!ctx) return NULL;
    ctx->buf = malloc((size_t)w * h * sizeof(uint16_t));
    if (!ctx->buf) { free(ctx); return NULL; }
    memset(ctx->buf, 0, (size_t)w * h * sizeof(uint16_t));
    ctx->width   = (uint16_t)w;
    ctx->height  = (uint16_t)h;
    ctx->backend = GFX_BACKEND_NONE;
    ctx->mode    = GFX_MODE_BUFFERED;
    return ctx;
}

void gfx_canvas_present(gfx_ctx_t *display, const gfx_ctx_t *canvas, int x, int y)
{
    if (!display || !canvas) return;
    gfx_blit(display, x, y, canvas->width, canvas->height, canvas->buf);
}

void gfx_canvas_free(gfx_ctx_t *canvas)
{
    if (!canvas) return;
    if (canvas->buf) free(canvas->buf);
    free(canvas);
}

void gfx_get_info(const gfx_ctx_t *ctx, uint16_t *w, uint16_t *h)
{
    if (!ctx) return;
    if (w) *w = ctx->width;
    if (h) *h = ctx->height;
}

/* ---- STREAM helpers ----------------------------------------------------- */

/* Write `w*h` host-endian RGB565 pixels at (x,y) on whichever backend is
 * active. The buffer must be width*height*2 bytes. Used by STREAM draw ops. */
static void stream_write_area(gfx_ctx_t *c, int x, int y, int w, int h,
                              const uint16_t *pixels)
{
    if (c->backend == GFX_BACKEND_DISP) {
        disp_write_area(c->disp, (uint16_t)x, (uint16_t)y,
                                  (uint16_t)w, (uint16_t)h, pixels);
    } else {
        /* FB0: position, write pixels row by row. FB_FLUSH happens at
         * gfx_flush(). */
        fb_pos_t pos = { .x = (uint16_t)x, .y = (uint16_t)y };
        ioctl(c->fd, FB_SET_POS, &pos);
        write(c->fd, pixels, (size_t)w * h * sizeof(uint16_t));
    }
}

/* Max display width DuneOS supports today. Used for stack-allocated row
 * buffers in STREAM mode. 320 covers ST7789 + most parallel TFTs. */
#define GFX_STREAM_MAX_ROW_PX  320

/* ---- drawing ------------------------------------------------------------- */

void gfx_fill(gfx_ctx_t *ctx, uint16_t color)
{
    if (!ctx) return;
    if (ctx->mode == GFX_MODE_BUFFERED) {
        size_t n = (size_t)ctx->width * ctx->height;
        for (size_t i = 0; i < n; i++) ctx->buf[i] = color;
        return;
    }
    /* STREAM: compose one row of the colour, write H times. */
    uint16_t row[GFX_STREAM_MAX_ROW_PX];
    if (ctx->width > GFX_STREAM_MAX_ROW_PX) return;
    for (int i = 0; i < ctx->width; i++) row[i] = color;
    for (int y = 0; y < ctx->height; y++)
        stream_write_area(ctx, 0, y, ctx->width, 1, row);
}

void gfx_pixel(gfx_ctx_t *ctx, int x, int y, uint16_t color)
{
    if (!ctx || x < 0 || y < 0 || x >= ctx->width || y >= ctx->height) return;
    if (ctx->mode == GFX_MODE_BUFFERED) {
        ctx->buf[(size_t)y * ctx->width + x] = color;
        return;
    }
    /* STREAM: single-pixel disp write. Slow — apps doing many gfx_pixel calls
     * in STREAM should refactor to gfx_rect / a buffered row instead. */
    stream_write_area(ctx, x, y, 1, 1, &color);
}

void gfx_rect(gfx_ctx_t *ctx, int x, int y, int w, int h, uint16_t color)
{
    if (!ctx || w <= 0 || h <= 0) return;
    int x1 = x < 0 ? 0 : x;
    int y1 = y < 0 ? 0 : y;
    int x2 = x + w > ctx->width  ? ctx->width  : x + w;
    int y2 = y + h > ctx->height ? ctx->height : y + h;
    if (x1 >= x2 || y1 >= y2) return;
    int rw = x2 - x1;

    if (ctx->mode == GFX_MODE_BUFFERED) {
        for (int row = y1; row < y2; row++)
            for (int col = x1; col < x2; col++)
                ctx->buf[(size_t)row * ctx->width + col] = color;
        return;
    }
    /* STREAM: compose one row of the colour (rw pixels), write rh times. */
    uint16_t row[GFX_STREAM_MAX_ROW_PX];
    if (rw > GFX_STREAM_MAX_ROW_PX) return;
    for (int i = 0; i < rw; i++) row[i] = color;
    for (int yy = y1; yy < y2; yy++)
        stream_write_area(ctx, x1, yy, rw, 1, row);
}

/* Render one 8×8 glyph into a stack buffer; written by gfx_text in STREAM
 * mode. Returns the buffer ready for stream_write_area. */
static void rasterize_glyph(const uint8_t *glyph, uint16_t fg, uint16_t bg,
                            uint16_t out[8 * 8])
{
    for (int row = 0; row < 8; row++)
        for (int col = 0; col < 8; col++)
            out[row * 8 + col] = ((glyph[row] >> col) & 1) ? fg : bg;
}

void gfx_text(gfx_ctx_t *ctx, int x, int y,
              const char *s, uint16_t fg, uint16_t bg)
{
    if (!ctx || !s) return;
    int cx = x, cy = y;

    if (ctx->mode == GFX_MODE_BUFFERED) {
        for (; *s; s++) {
            unsigned char c = (unsigned char)*s;
            if (c == '\n') { cx = x; cy += 8; continue; }
            if (c < 32 || c > 127) c = '?';
            const uint8_t *glyph = font8x8_basic[c - 32];
            for (int row = 0; row < 8; row++) {
                for (int col = 0; col < 8; col++) {
                    int px = cx + col, py = cy + row;
                    if (px < 0 || py < 0 || px >= ctx->width || py >= ctx->height) continue;
                    uint16_t color = (glyph[row] >> col) & 1 ? fg : bg;
                    ctx->buf[(size_t)py * ctx->width + px] = color;
                }
            }
            cx += 8;
            if (cx + 8 > ctx->width) { cx = x; cy += 8; }
        }
        return;
    }

    /* STREAM: rasterize one glyph into a stack buffer, stream_write_area
     * per char. Skips chars fully off-screen; partial-clip chars still
     * write their full 8×8 — the backend's set_window clips for us when
     * possible, otherwise we just spill harmlessly outside the panel. */
    uint16_t cbuf[8 * 8];
    for (; *s; s++) {
        unsigned char c = (unsigned char)*s;
        if (c == '\n') { cx = x; cy += 8; continue; }
        if (c < 32 || c > 127) c = '?';
        if (cx + 8 > 0 && cy + 8 > 0 && cx < ctx->width && cy < ctx->height) {
            rasterize_glyph(font8x8_basic[c - 32], fg, bg, cbuf);
            int dx = cx < 0 ? 0 : cx;
            int dy = cy < 0 ? 0 : cy;
            int dw = (cx + 8 > ctx->width)  ? (ctx->width  - dx) : (8 - (dx - cx));
            int dh = (cy + 8 > ctx->height) ? (ctx->height - dy) : (8 - (dy - cy));
            if (dw == 8 && dh == 8) {
                stream_write_area(ctx, dx, dy, 8, 8, cbuf);
            } else {
                /* Partial clip — emit clipped rect from the right offset. */
                for (int row = 0; row < dh; row++) {
                    stream_write_area(ctx, dx, dy + row, dw, 1,
                                      &cbuf[(row + (dy - cy)) * 8 + (dx - cx)]);
                }
            }
        }
        cx += 8;
        if (cx + 8 > ctx->width) { cx = x; cy += 8; }
    }
}

void gfx_blit(gfx_ctx_t *ctx, int x, int y, int w, int h,
              const uint16_t *pixels)
{
    if (!ctx || !pixels || w <= 0 || h <= 0) return;
    /* Clip to display bounds. */
    int x1 = x < 0 ? 0 : x;
    int y1 = y < 0 ? 0 : y;
    int x2 = x + w > ctx->width  ? ctx->width  : x + w;
    int y2 = y + h > ctx->height ? ctx->height : y + h;
    if (x1 >= x2 || y1 >= y2) return;
    int cw = x2 - x1, ch = y2 - y1;
    int src_off_x = x1 - x;
    int src_off_y = y1 - y;

    if (ctx->mode == GFX_MODE_BUFFERED) {
        for (int row = 0; row < ch; row++) {
            const uint16_t *src = pixels + (src_off_y + row) * w + src_off_x;
            uint16_t *dst = ctx->buf + (size_t)(y1 + row) * ctx->width + x1;
            memcpy(dst, src, (size_t)cw * sizeof(uint16_t));
        }
        return;
    }
    /* STREAM: one SPI transaction for the whole clipped rect. If the source
     * is unclipped (cw == w && ch == h), pass the buffer through directly.
     * Otherwise we'd have to allocate a contiguous clipped copy — instead
     * write row by row to avoid heap allocation in STREAM. */
    if (cw == w && ch == h) {
        stream_write_area(ctx, x1, y1, cw, ch, pixels);
    } else {
        for (int row = 0; row < ch; row++) {
            const uint16_t *src = pixels + (src_off_y + row) * w + src_off_x;
            stream_write_area(ctx, x1, y1 + row, cw, 1, src);
        }
    }
}

/* ---- flush --------------------------------------------------------------- */

void gfx_flush(gfx_ctx_t *ctx)
{
    if (!ctx) return;
    if (ctx->mode == GFX_MODE_STREAM) {
        /* On FB0, a flush is still needed to push the kernel buffer to SPI.
         * On DISP, draws went out at draw time — flush is a no-op. */
        if (ctx->backend == GFX_BACKEND_FB0)
            ioctl(ctx->fd, FB_FLUSH, NULL);
        return;
    }
    /* BUFFERED: push the whole back-buffer. */
    size_t total = (size_t)ctx->width * ctx->height * sizeof(uint16_t);
    if (ctx->backend == GFX_BACKEND_FB0) {
        fb_pos_t pos = { .x = 0, .y = 0 };
        ioctl(ctx->fd, FB_SET_POS, &pos);
        write(ctx->fd, ctx->buf, total);
        ioctl(ctx->fd, FB_FLUSH, NULL);
    } else {
        disp_write_area(ctx->disp, 0, 0, ctx->width, ctx->height, ctx->buf);
    }
}
