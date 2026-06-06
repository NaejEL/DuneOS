/*
 * libimage — DuneOS SDK raster loader.
 *
 * Single-file userspace library. Apps link this in by listing it under
 * `sources:` in their duneos.yaml:
 *
 *   sources:
 *     - $SDK/image/libimage.c
 *
 * No kernel calls beyond POSIX (open/read/close) and malloc.
 */

#include "duneos/image.h"

#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

typedef struct __attribute__((packed)) {
    uint16_t magic;
    uint16_t width;
    uint16_t height;
    uint16_t format;
} dr_header_t;

int duneos_image_load_dr(const char *path, duneos_image_t *out)
{
    if (!path || !out) return -2;
    memset(out, 0, sizeof(*out));

    int fd = open(path, O_RDONLY);
    if (fd < 0) return -1;

    dr_header_t hdr;
    ssize_t hn = read(fd, &hdr, sizeof(hdr));
    if (hn != (ssize_t)sizeof(hdr)) { close(fd); return -2; }
    if (hdr.magic != DUNEOS_IMAGE_MAGIC) { close(fd); return -2; }
    if (hdr.format != DUNEOS_IMAGE_FMT_RGB565) { close(fd); return -2; }
    if (hdr.width == 0 || hdr.height == 0) { close(fd); return -2; }

    size_t npix = (size_t)hdr.width * (size_t)hdr.height;
    /* Guard against an overflow-crafted file: 2*npix must fit in size_t.
     * For RGB565 a width or height of 65535 is already > display-realistic. */
    if (npix > (SIZE_MAX / 2)) { close(fd); return -2; }
    size_t nbytes = npix * 2;

    uint16_t *pix = malloc(nbytes);
    if (!pix) { close(fd); return -3; }

    ssize_t pn = read(fd, pix, nbytes);
    close(fd);
    if (pn != (ssize_t)nbytes) {
        free(pix);
        return -4;
    }

    out->width  = hdr.width;
    out->height = hdr.height;
    out->format = hdr.format;
    out->pixels = pix;
    return 0;
}

void duneos_image_free(duneos_image_t *img)
{
    if (!img) return;
    free(img->pixels);
    memset(img, 0, sizeof(*img));
}

/* read() can return short; loop until n bytes or EOF/error. */
static ssize_t read_full(int fd, void *buf, size_t n)
{
    size_t got = 0;
    uint8_t *p = buf;
    while (got < n) {
        ssize_t r = read(fd, p + got, n - got);
        if (r <= 0) break;
        got += (size_t)r;
    }
    return (ssize_t)got;
}

int duneos_image_info_dr(const char *path, uint16_t *w, uint16_t *h)
{
    if (!path) return -2;
    int fd = open(path, O_RDONLY);
    if (fd < 0) return -1;
    dr_header_t hdr;
    ssize_t hn = read_full(fd, &hdr, sizeof(hdr));
    close(fd);
    if (hn != (ssize_t)sizeof(hdr) || hdr.magic != DUNEOS_IMAGE_MAGIC ||
        hdr.format != DUNEOS_IMAGE_FMT_RGB565 || !hdr.width || !hdr.height)
        return -2;
    if (w) *w = hdr.width;
    if (h) *h = hdr.height;
    return 0;
}

#define DR_BLIT_MAX_W 480   /* widest supported row; row buffer is on the stack */

int duneos_image_blit_dr(gfx_ctx_t *gfx, int x, int y, const char *path)
{
    if (!gfx || !path) return -2;
    int fd = open(path, O_RDONLY);
    if (fd < 0) return -1;

    dr_header_t hdr;
    if (read_full(fd, &hdr, sizeof(hdr)) != (ssize_t)sizeof(hdr) ||
        hdr.magic != DUNEOS_IMAGE_MAGIC || hdr.format != DUNEOS_IMAGE_FMT_RGB565 ||
        !hdr.width || !hdr.height || hdr.width > DR_BLIT_MAX_W) {
        close(fd);
        return -2;
    }

    uint16_t row[DR_BLIT_MAX_W];
    size_t rb = (size_t)hdr.width * 2;
    for (uint16_t ry = 0; ry < hdr.height; ry++) {
        if (read_full(fd, row, rb) != (ssize_t)rb) { close(fd); return -4; }
        gfx_blit(gfx, x, y + ry, hdr.width, 1, row);
    }
    close(fd);
    return 0;
}
