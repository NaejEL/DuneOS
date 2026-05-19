/*
 * libdisp — chip-agnostic SDK display API.
 *
 * Apps and libgfx include this header only.  The chip library (libst7789.c,
 * libssd1306.c, …) defines `duneos_disp_ops` and `struct disp_ctx_s`.
 * libdisp.c dispatches through that symbol — it never changes regardless of
 * how many chips are added.
 *
 * Adding a new display chip:
 *   1. Create sdk/display/lib<chip>.c that defines `duneos_disp_ops`
 *      and `struct disp_ctx_s` (see libst7789.c for the pattern)
 *   2. Add the chip lib to duneos.yaml sources alongside libdisp.c
 *   3. Zero changes to disp.h, libdisp.c, gfx.c, g_shell.c, or any app.
 */

#pragma once
#include <stdint.h>
#include <stddef.h>

typedef struct disp_ctx_s disp_ctx_t;

/*
 * Backend ops table.  Defined by the chip library (libst7789.c, …).
 * libdisp.c dispatches through this symbol — one indirection per call,
 * irrelevant versus the SPI transfer time.
 */
typedef struct {
    disp_ctx_t *(*open)(void);
    uint16_t    (*width)(const disp_ctx_t *);
    uint16_t    (*height)(const disp_ctx_t *);
    void        (*write_area)(disp_ctx_t *, uint16_t x, uint16_t y,
                              uint16_t w, uint16_t h, const uint16_t *pixels);
    void        (*close)(disp_ctx_t *);
} disp_backend_ops_t;

extern const disp_backend_ops_t duneos_disp_ops;

/*
 * Open the board's display using config from /flash/board.info.
 * Returns NULL if board.info is absent or has no usable display config.
 * Do NOT call on boards where CONFIG_DUNEOS_DRV_FB=y — the kernel already
 * owns the display SPI bus for the PSRAM framebuffer.
 */
disp_ctx_t *disp_open(void);

/* Display dimensions in pixels. */
uint16_t disp_width(const disp_ctx_t *ctx);
uint16_t disp_height(const disp_ctx_t *ctx);

/*
 * Write a rectangular block of RGB565 pixels to the display.
 * pixels must contain w * h uint16_t values in host-endian RGB565.
 */
void disp_write_area(disp_ctx_t *ctx,
                     uint16_t x, uint16_t y,
                     uint16_t w, uint16_t h,
                     const uint16_t *pixels);

/* Close and free the display context. */
void disp_close(disp_ctx_t *ctx);
