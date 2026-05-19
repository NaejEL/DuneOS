#pragma once

/*
 * /dev/disp0 — streaming pixel display (ST7789).
 *
 * Usage:
 *   1. open("/dev/disp0", O_WRONLY)
 *   2. ioctl(fd, DISP_GET_INFO, &info)       // get width/height/rotation
 *   3. ioctl(fd, DISP_SET_WINDOW, &win)      // set hardware pixel window
 *   4. write(fd, pixels, win.w * win.h * 2)  // stream host-endian RGB565 pixels
 *      (kernel byte-swaps to big-endian for SPI before DMA)
 *   5. Repeat 3-4 for each dirty region.
 *   6. close(fd)
 *
 * The window stays set until the next DISP_SET_WINDOW or close().
 * write() must receive an even number of bytes (whole pixels only).
 */

#include <stdint.h>

/* ioctl() is exported by the kernel ABI; declare it here for -nostdlib apps. */
extern int ioctl(int fd, unsigned long request, ...);

/* ioctl command codes */
#define DISP_GET_INFO    0x10   /* arg: disp_info_t *   (out) */
#define DISP_SET_WINDOW  0x11   /* arg: disp_window_t * (in)  */

/* Display properties returned by DISP_GET_INFO */
typedef struct {
    uint16_t width;     /* pixels */
    uint16_t height;    /* pixels */
    uint8_t  rotation;  /* 0=0°, 1=90°CW, 2=180°, 3=270°CW */
} disp_info_t;

/* Window for subsequent write() calls */
typedef struct {
    uint16_t x;   /* left column, 0-based */
    uint16_t y;   /* top row, 0-based */
    uint16_t w;   /* width in pixels */
    uint16_t h;   /* height in pixels */
} disp_window_t;
