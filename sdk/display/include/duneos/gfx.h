#pragma once

/*
 * DuneOS SDK — libgfx display abstraction.
 *
 * gfx_open() selects the best available display backend at runtime:
 *   - Tier B: /dev/fb0  — kernel PSRAM framebuffer (CONFIG_DUNEOS_DRV_FB=y boards)
 *   - Tier A: libst7789 via /flash/board.info — direct SPI (no kernel driver, all boards)
 *
 * All drawing goes into a userspace back-buffer; gfx_flush() pushes it to
 * the display in one shot.  This decouples drawing speed from SPI bandwidth.
 *
 * Pixel format: RGB565, host byte order (little-endian on ESP32).
 * Convenience colour macros follow the same packing.
 *
 * Example:
 *   gfx_ctx_t *ctx = gfx_open();
 *   if (!ctx) { / no display / return; }
 *
 *   uint16_t w, h;
 *   gfx_get_info(ctx, &w, &h);
 *
 *   gfx_fill(ctx, GFX_RGB(0, 0, 0));
 *   gfx_text(ctx, 10, 10, "Hello!", GFX_RGB(255, 255, 255), GFX_RGB(0, 0, 0));
 *   gfx_flush(ctx);
 *   gfx_close(ctx);
 */

#include <stdint.h>
#include <stdbool.h>

/* Pack r,g,b (0-255 each) → RGB565 host-endian */
#define GFX_RGB(r, g, b) \
    ((uint16_t)(((r) & 0xF8u) << 8) | (((g) & 0xFCu) << 3) | (((b) & 0xF8u) >> 3))

#define GFX_BLACK   GFX_RGB(  0,   0,   0)
#define GFX_WHITE   GFX_RGB(255, 255, 255)
#define GFX_RED     GFX_RGB(255,   0,   0)
#define GFX_GREEN   GFX_RGB(  0, 255,   0)
#define GFX_BLUE    GFX_RGB(  0,   0, 255)
#define GFX_YELLOW  GFX_RGB(255, 255,   0)
#define GFX_CYAN    GFX_RGB(  0, 255, 255)

typedef struct gfx_ctx gfx_ctx_t;

/*
 * Open the best available display backend.
 * Returns NULL if no display device is found (/dev/fb0 absent and board.info has no display).
 * Allocates a width×height RGB565 back-buffer with malloc().
 */
gfx_ctx_t *gfx_open(void);

/* Close the display and free the back-buffer. */
void gfx_close(gfx_ctx_t *ctx);

/* Return display dimensions. */
void gfx_get_info(const gfx_ctx_t *ctx, uint16_t *w, uint16_t *h);

/* Fill the entire back-buffer with a single colour. */
void gfx_fill(gfx_ctx_t *ctx, uint16_t color);

/* Set one pixel in the back-buffer.  Out-of-bounds writes are silently ignored. */
void gfx_pixel(gfx_ctx_t *ctx, int x, int y, uint16_t color);

/* Draw a filled rectangle. Clips to display bounds. */
void gfx_rect(gfx_ctx_t *ctx, int x, int y, int w, int h, uint16_t color);

/*
 * Draw a NUL-terminated string using the built-in 8×8 bitmap font.
 * fg = foreground colour, bg = background colour.
 * Characters outside the display bounds are silently clipped.
 * Newline (\n) advances to the next line.
 */
void gfx_text(gfx_ctx_t *ctx, int x, int y,
              const char *s, uint16_t fg, uint16_t bg);

/*
 * Push the back-buffer to the display hardware.
 *
 * For libst7789 (Tier A): calls st7789_write_area() with the full buffer.
 * For /dev/fb0   (Tier B): writes the buffer then calls ioctl(FB_FLUSH).
 *
 * Must be called after drawing to make changes visible.
 */
void gfx_flush(gfx_ctx_t *ctx);
