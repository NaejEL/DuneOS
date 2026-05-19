#pragma once

/*
 * DuneOS framebuffer ioctl API — /dev/fb0
 *
 * /dev/fb0 is a double-buffered kernel framebuffer backed by PSRAM.
 * Only available on boards where CONFIG_DUNEOS_DRV_FB=y (requires SPIRAM).
 *
 * Typical usage:
 *
 *   int fb = open("/dev/fb0", O_RDWR);
 *
 *   fb_info_t info;
 *   ioctl(fb, FB_GET_INFO, &info);  // width, height, bpp, stride
 *
 *   // Write pixels (host-endian RGB565) at (x, y):
 *   fb_pos_t pos = { .x = x, .y = y };
 *   ioctl(fb, FB_SET_POS, &pos);
 *   write(fb, pixels, w * sizeof(uint16_t)); // one row
 *
 *   ioctl(fb, FB_FLUSH, NULL);   // DMA entire back-buffer to display
 *   close(fb);
 *
 * Pixel format: RGB565, host byte order (little-endian on ESP32-S3).
 * The kernel driver byte-swaps during FB_FLUSH — apps always write
 * native uint16_t values.
 *
 * write() advances the internal write cursor by the number of bytes written.
 * FB_SET_POS resets the cursor to the given pixel coordinate.
 * write() wraps at the end of the buffer (returns ENOSPC if already full).
 */

#include <stdint.h>

/* ioctl() is exported by the kernel ABI; declare it here for -nostdlib apps. */
extern int ioctl(int fd, unsigned long request, ...);

/* ioctl commands */
#define FB_GET_INFO    0x01  /* arg → fb_info_t *                          */
#define FB_SET_POS     0x02  /* arg ← fb_pos_t  * (pixel coordinates)      */
#define FB_FLUSH       0x03  /* arg = NULL — flush entire back-buffer       */
#define FB_FLUSH_RECT  0x04  /* arg ← fb_rect_t * — flush a sub-region     */

typedef struct {
    uint16_t width;
    uint16_t height;
    uint8_t  bpp;    /* always 16 (RGB565) */
    uint16_t stride; /* bytes per row = width * 2 */
} fb_info_t;

typedef struct {
    uint16_t x;
    uint16_t y;
} fb_pos_t;

typedef struct {
    uint16_t x;
    uint16_t y;
    uint16_t w;
    uint16_t h;
} fb_rect_t;
