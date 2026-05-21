#pragma once

/*
 * DuneOS SDK — libimage: minimal raster image loader.
 *
 * Format: ".dr" (Dune Raster) — 8-byte header + raw RGB565 pixel data.
 *   offset 0  uint16  magic   = 0xD12E  ("Dune Image")
 *   offset 2  uint16  width   in pixels
 *   offset 4  uint16  height  in pixels
 *   offset 6  uint16  format  = 0  (RGB565 little-endian)
 *   offset 8  width*height*2 bytes of RGB565 pixels (host-endian on ESP32)
 *
 * Why this and not PNG: a PNG decoder pulls in zlib + ~50-100 KiB of libpng,
 * which is overkill for an embedded app whose only job is to blit a logo.
 * The `.dr` format is what `gfx_blit()` already expects — load is just a
 * malloc + read with header validation. If full PNG support is needed
 * later, see docs/backlog.md → libimage_png.
 *
 * Generation: `python tools/dbt.py img convert in.png out.dr [--resize WxH]`
 *
 * Memory cost: width*height*2 bytes on the app's heap. The app's
 * duneos.yaml must declare a heap_size: large enough to hold the largest
 * image it loads, plus ~4 KiB overhead.
 *
 * Example:
 *   duneos_image_t logo;
 *   if (duneos_image_load_dr("/etc/splash/logo.dr", &logo) == 0) {
 *       gfx_blit(ctx, x, y, logo.width, logo.height, logo.pixels);
 *       duneos_image_free(&logo);
 *   }
 */

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

#define DUNEOS_IMAGE_MAGIC   0xD12Eu
#define DUNEOS_IMAGE_FMT_RGB565   0u

typedef struct {
    uint16_t  width;
    uint16_t  height;
    uint16_t  format;       /* echoes header — currently always RGB565 */
    uint16_t *pixels;       /* malloc'd; free via duneos_image_free */
} duneos_image_t;

/*
 * Load `path` into out. Returns:
 *    0  success — caller owns out->pixels, must duneos_image_free(&out).
 *   -1  open() failed (file missing, permission, etc.)
 *   -2  header malformed (truncated read, bad magic, unsupported format)
 *   -3  malloc failed
 *   -4  pixel data short read
 *
 * On non-zero return, out is zeroed and pixels is NULL — no free needed.
 */
int  duneos_image_load_dr(const char *path, duneos_image_t *out);

/*
 * Free img->pixels and zero the struct. Safe to call on a zeroed/failed
 * image (no-op).
 */
void duneos_image_free(duneos_image_t *img);

#ifdef __cplusplus
}
#endif
