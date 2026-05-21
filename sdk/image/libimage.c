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
