/*
 * libdisp — chip-agnostic display dispatch.
 *
 * Every call forwards to `duneos_disp_ops`, the vtable defined by whichever
 * chip library is linked alongside this file (libst7789.c, libssd1306.c,
 * libili9341.c, …). Adding a new display chip requires zero changes to
 * libdisp.c or to apps — just a new sdk/display/lib<chip>.c that exports
 * `const disp_backend_ops_t duneos_disp_ops`, plus a board.yaml
 * `display.driver: <chip>` so the capability resolver pulls the right
 * source via `capabilities: [display]`.
 *
 * Apps include <duneos/disp.h>; they never see the chip name.
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
