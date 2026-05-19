#pragma once

/*
 * DuneOS SDK — ST7789 chip library (userspace).
 *
 * Public API: this file is intentionally tiny. Apps don't talk to libst7789
 * directly — they use the chip-agnostic `<duneos/disp.h>` front-end and
 * libdisp.c, which dispatches through the `duneos_disp_ops` symbol defined
 * in libst7789.c.
 *
 * The library auto-configures from /flash/board.info (written by the kernel
 * at boot): width, height, MOSI/CLK/CS/DC/RST/BL pins, SPI frequency,
 * MADCTL, swap_xy, column/row offsets. No board_config.h is consumed in
 * userspace.
 *
 * Usage from an app:
 *
 *   #include <duneos/disp.h>      // not <duneos/st7789.h>
 *
 *   disp_ctx_t *d = disp_open();  // resolves to libst7789's backend_open
 *   if (!d) return -1;
 *   uint16_t pixels[240 * 8];     // a strip of pixels
 *   disp_write_area(d, 0, 0, 240, 8, pixels);
 *   disp_close(d);
 *
 * Build:
 *   The app's duneos.yaml must list both files in `sources:`:
 *     - $SDK/display/libdisp.c
 *     - $SDK/display/libst7789.c
 *
 * Architecture: see ADR 009 (kernel/userspace boundary for drivers). The
 * ST7789 used to live in the kernel as drv_disp_st7789.c (/dev/disp0);
 * that driver was retired in the Phase 24 driver-placement cleanup. The
 * kernel still owns /dev/fb0 on PSRAM boards via drv_fb_st7789.c — apps
 * on those boards should not link libst7789 (libgfx tier B picks /dev/fb0).
 */
