/*
 * libdisp dispatch layer — stable, never changes.
 *
 * All calls forward to duneos_disp_ops, which is defined by the chip library
 * linked alongside this file (libst7789.c, libssd1306.c, …).  Adding a new
 * display chip does not require touching this file.
 */

#include "duneos/disp.h"

disp_ctx_t *disp_open(void)
{
    return duneos_disp_ops.open();
}

uint16_t disp_width(const disp_ctx_t *ctx)
{
    return duneos_disp_ops.width(ctx);
}

uint16_t disp_height(const disp_ctx_t *ctx)
{
    return duneos_disp_ops.height(ctx);
}

void disp_write_area(disp_ctx_t *ctx,
                     uint16_t x, uint16_t y,
                     uint16_t w, uint16_t h,
                     const uint16_t *pixels)
{
    duneos_disp_ops.write_area(ctx, x, y, w, h, pixels);
}

void disp_close(disp_ctx_t *ctx)
{
    duneos_disp_ops.close(ctx);
}
