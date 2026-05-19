#pragma once

/*
 * DuneOS SDK — libgfx display abstraction.
 *
 * gfx_open() selects the best available display backend at runtime:
 *   - Tier B: /dev/fb0  — kernel PSRAM framebuffer (CONFIG_DUNEOS_DRV_FB=y boards)
 *   - Tier A: /dev/disp0 — kernel streaming driver via libdisp (CONFIG_DUNEOS_DRV_DISP=y)
 *
 * All drawing goes into a userspace back-buffer; gfx_flush() pushes it to
 * the display in one shot. This decouples drawing speed from SPI bandwidth.
 *
 * MEMORY COST — read this before deciding to use libgfx:
 *   The back-buffer is width * height * 2 bytes (RGB565). On the CardPuter
 *   (240x135) that's 63 KiB; on a 320x240 board, 150 KiB. The buffer lives
 *   in the app's userspace heap, so the app's duneos.yaml MUST declare a
 *   `heap_size:` at least that large plus ~16 KiB overhead.
 *
 *   Example for a 240x135 board:
 *     stack_size: 8192
 *     heap_size:  81920    # 64 KiB back-buffer + slack
 *
 *   If the back-buffer is too expensive for your app, skip libgfx and call
 *   <duneos/disp.h> directly with a single-line buffer (see apps/user/g_shell
 *   for an example — it draws line-by-line and uses only ~3 KiB total).
 *
 * Pixel format: RGB565, host byte order (little-endian on ESP32).
 * Convenience colour macros follow the same packing.
 *
 * Example:
 *   gfx_ctx_t *ctx = gfx_open();
 *   if (!ctx) { / no display, or heap_size too small / return; }
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
