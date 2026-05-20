#include "duneos/gfx.h"

extern void duneos_exit(int code);
extern int  usleep(unsigned int usec);

void app_main(void)
{
    /* STREAM mode (Phase 24.10): no 64 KiB back-buffer — each draw call
     * writes directly through libdisp. Required so gfx_demo can coexist
     * with g_shell + other apps on CardPuter's 320 KiB DRAM. */
    gfx_ctx_t *ctx = gfx_open_mode(GFX_MODE_STREAM);
    if (!ctx) {
        /* Encode the gfx_open failure step in the exit code (10..16) so the
         * kernel's `app exited (code N)` message tells us which step broke
         * without needing extra klog output. */
        duneos_exit(10 + gfx_last_error());
        return;
    }

    uint16_t w, h;
    gfx_get_info(ctx, &w, &h);

    /* --- frame 1: coloured bars --- */
    uint16_t bar_h = h / 7;
    uint16_t colors[7] = {
        GFX_RED, GFX_GREEN, GFX_BLUE,
        GFX_YELLOW, GFX_CYAN,
        GFX_RGB(255, 0, 255),   /* magenta */
        GFX_WHITE,
    };
    for (int i = 0; i < 7; i++)
        gfx_rect(ctx, 0, i * bar_h, w, bar_h + 1, colors[i]);
    gfx_text(ctx, 4, 4, "DuneOS libgfx", GFX_BLACK, GFX_RED);
    gfx_flush(ctx);
    usleep(2000000);

    /* --- frame 2: checkerboard + text --- */
    gfx_fill(ctx, GFX_BLACK);
    for (int y = 0; y < h; y += 16)
        for (int x = 0; x < w; x += 16)
            if (((x / 16) + (y / 16)) & 1)
                gfx_rect(ctx, x, y, 16, 16, GFX_RGB(40, 40, 40));

    gfx_text(ctx, 4, 4,  "Board:", GFX_WHITE, GFX_BLACK);
    gfx_text(ctx, 4, 14, "libgfx Tier A/B", GFX_CYAN,  GFX_BLACK);
    gfx_text(ctx, 4, 24, "gfx_flush() OK", GFX_GREEN,  GFX_BLACK);
    gfx_flush(ctx);
    usleep(2000000);

    /* --- frame 3: pixel art gradient ---
     * Compose one row at a time on the stack and gfx_blit it. In STREAM
     * mode this is H SPI transactions instead of W*H gfx_pixel calls
     * (each of which would be its own SPI transaction). */
    {
        uint16_t row[240];   /* widest CardPuter row; bump if needed */
        for (int y = 0; y < h; y++) {
            uint8_t g = (uint8_t)(y * 255 / h);
            uint8_t b = 128;
            for (int x = 0; x < w; x++) {
                uint8_t r = (uint8_t)(x * 255 / w);
                row[x] = GFX_RGB(r, g, b);
            }
            gfx_blit(ctx, 0, y, w, 1, row);
        }
    }
    gfx_text(ctx, 4, 4, "RGB gradient", GFX_WHITE, GFX_BLACK);
    gfx_flush(ctx);
    usleep(2000000);

    gfx_fill(ctx, GFX_BLACK);
    gfx_text(ctx, 4, 4, "Done.", GFX_WHITE, GFX_BLACK);
    gfx_flush(ctx);

    gfx_close(ctx);
    duneos_exit(0);
}
