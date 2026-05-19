#pragma once

/*
 * st7789_hw — shared low-level ST7789 hardware module.
 *
 * Internal to duneos_kernel. Used by drv_fb_st7789.c (PSRAM back-buffer
 * exposed as /dev/fb0). The streaming `/dev/disp0` driver was retired in
 * the Phase 24 driver-placement cleanup (ADR 009 drift #2): non-PSRAM
 * boards now use the userspace libst7789 library, driven from
 * /flash/board.info, instead of a kernel driver.
 *
 * Reads pin assignments from board_config.h at compile time.
 * st7789_hw_init() is idempotent — safe to call multiple times.
 */

#include "esp_err.h"
#include <stdint.h>
#include <stddef.h>

/* One-time hardware init: GPIO config, SPI bus + device, display init sequence. */
esp_err_t st7789_hw_init(void);

/* Send a command byte (DC pin = 0). */
void st7789_hw_cmd(uint8_t c);

/* Send data bytes (DC pin = 1).  buf must be DMA-accessible (internal DRAM). */
void st7789_hw_data(const void *buf, size_t len);

/* Issue CASET/RASET/RAMWR to set the pixel write window. */
void st7789_hw_set_window(uint16_t x, uint16_t y, uint16_t w, uint16_t h);

/* Fill the full active display area with a big-endian RGB565 color (e.g. 0x0000 = black). */
void st7789_hw_clear(uint16_t color_be);

/* Display dimensions read from board_config.h constants. */
uint16_t st7789_hw_width(void);
uint16_t st7789_hw_height(void);
uint8_t  st7789_hw_rotation(void);
