#pragma once

/*
 * DuneOS SDK — ST7789 display driver (Tier A userspace library).
 *
 * Apps link libst7789.c directly; the library initializes the SPI bus and
 * drives the display without any kernel involvement.  This is correct for
 * boards where the display SPI bus is not owned by the kernel (/dev/fb0
 * absent or CONFIG_DUNEOS_DRV_FB not set).
 *
 * Usage:
 *   #include "board_config.h"
 *   #include <duneos/st7789.h>
 *
 *   st7789_config_t cfg = ST7789_CONFIG_FROM_BOARD();
 *   st7789_handle_t *h = st7789_open(&cfg);
 *   st7789_fill(h, 0x001F);       // fill blue (RGB565, host-endian)
 *   st7789_close(h);
 *
 * Pixel format: RGB565, host byte order (little-endian on ESP32).
 * The library byte-swaps internally before transmission.
 *
 * On boards where CONFIG_DUNEOS_DRV_FB=y the kernel owns the display SPI —
 * do not use this library simultaneously.  Use /dev/fb0 instead.
 */

#include <stdint.h>
#include <stdbool.h>
#include "driver/spi_master.h"

typedef struct st7789_handle st7789_handle_t;

typedef struct {
    spi_host_device_t spi_host;
    int       mosi_pin;
    int       clk_pin;
    int       cs_pin;
    int       dc_pin;
    int       rst_pin;
    int       bl_pin;         /* -1 if no backlight GPIO */
    uint32_t  freq_hz;
    uint16_t  width;
    uint16_t  height;
    uint8_t   rotation;       /* 0-3 (90° increments); maps to MADCTL */
    bool      bus_already_init; /* true when the SPI bus was already init'd
                                   (e.g. shared with SD card in vfs.c) */
} st7789_config_t;

/* Convenience: default MADCTL for rotation 0 means no byte-swap needed in
 * MADCTL itself; apps may override by writing register 0x36 directly. */
#define ST7789_MADCTL_ROT0   0x00u
#define ST7789_MADCTL_ROT1   0x60u   /* MV=1, MX=1  — 90° CW */
#define ST7789_MADCTL_ROT2   0xC0u   /* MY=1, MX=1  — 180°   */
#define ST7789_MADCTL_ROT3   0xA0u   /* MY=1, MV=1  — 270° CW */

/*
 * BSP-aware initializer: fills st7789_config_t from board_config.h constants.
 * Requires board_config.h to be included first.
 *
 * DUNEOS_DISPLAY_BUS_SHARED defaults to 0 (dedicated bus) if not defined —
 * safe for boards where the display is on its own SPI host.
 */
#ifndef DUNEOS_DISPLAY_BUS_SHARED
#define DUNEOS_DISPLAY_BUS_SHARED 0
#endif
#ifndef DUNEOS_DISPLAY_ROTATION
#define DUNEOS_DISPLAY_ROTATION   0
#endif

#ifdef DUNEOS_DISPLAY_SPI_HOST
#define ST7789_CONFIG_FROM_BOARD() ((st7789_config_t){      \
    .spi_host         = DUNEOS_DISPLAY_SPI_HOST,            \
    .mosi_pin         = DUNEOS_DISPLAY_MOSI_PIN,            \
    .clk_pin          = DUNEOS_DISPLAY_CLK_PIN,             \
    .cs_pin           = DUNEOS_DISPLAY_CS_PIN,              \
    .dc_pin           = DUNEOS_DISPLAY_DC_PIN,              \
    .rst_pin          = DUNEOS_DISPLAY_RST_PIN,             \
    .bl_pin           = DUNEOS_DISPLAY_BL_PIN,              \
    .freq_hz          = DUNEOS_DISPLAY_FREQ_HZ,             \
    .width            = DUNEOS_DISPLAY_WIDTH,               \
    .height           = DUNEOS_DISPLAY_HEIGHT,              \
    .rotation         = DUNEOS_DISPLAY_ROTATION,            \
    .bus_already_init = DUNEOS_DISPLAY_BUS_SHARED,          \
})
#endif

/*
 * Open / close
 * st7789_open() initialises the SPI bus (unless bus_already_init=true),
 * adds the device, performs hardware reset, and runs the init sequence.
 * Returns NULL on failure.
 */
st7789_handle_t *st7789_open(const st7789_config_t *cfg);
void             st7789_close(st7789_handle_t *h);

/*
 * Drawing — all accept host-endian RGB565 pixels.
 */

/* Fill the entire display with a single colour. */
void st7789_fill(st7789_handle_t *h, uint16_t color_rgb565);

/*
 * Write a rectangular region.
 * pixels[] must contain w*height_px host-endian RGB565 values, row-major.
 */
void st7789_write_area(st7789_handle_t *h,
                       uint16_t x, uint16_t y,
                       uint16_t w, uint16_t height_px,
                       const uint16_t *pixels);

/* Turn backlight on or off (no-op if bl_pin == -1). */
void st7789_set_backlight(st7789_handle_t *h, bool on);

/*
 * Discover display config from /flash/board.info and open the display.
 * Equivalent to: read board.info → fill st7789_config_t → st7789_open().
 * Returns NULL if board.info is absent or missing required display fields.
 * If out_width / out_height are non-NULL they are filled with the display
 * dimensions read from board.info.
 * Do not call on boards where CONFIG_DUNEOS_DRV_FB=y — the kernel already
 * owns the display SPI bus in that case.
 */
st7789_handle_t *st7789_open_from_board_info(uint16_t *out_width, uint16_t *out_height);
