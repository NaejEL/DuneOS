/*
 * splash — one-shot boot logo.
 *
 * If /etc/splash/config.yaml exists and points to a `.dr` raster, blit that
 * logo centred on the display. Otherwise fall back to a procedural sand
 * gradient + dune silhouette + "DuneOS" wordmark.
 *
 * Holds for ~1.5 s, then exits. Combined with `after: splash` in init.yaml,
 * dependent services (g_shell, app launcher, …) start once it has cleared.
 *
 * STREAM mode (no userspace back-buffer) — CardPuter's 320 KiB DRAM can't
 * afford a 64 KiB framebuffer just for a static image.
 */

#include "duneos/gfx.h"
#include "duneos/image.h"
#include "duneos/libdune.h"

#include <ctype.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

extern void duneos_exit(int code);
extern int  usleep(unsigned int usec);

/*
 * Look up `logo: <path>` in /etc/splash/config.yaml. Returns 0 on success
 * and fills out_path; returns -1 if the file is absent or the key missing.
 */
static int read_logo_path(char *out_path, size_t outsz)
{
    char conf_path[64];
    if (duneos_config_path("splash", conf_path, sizeof(conf_path)) != 0)
        return -1;

    int fd = open(conf_path, O_RDONLY);
    if (fd < 0) return -1;

    char buf[256];
    ssize_t n = read(fd, buf, sizeof(buf) - 1);
    close(fd);
    if (n <= 0) return -1;
    buf[n] = '\0';

    /* Minimal one-shot YAML reader: scan for `logo:` at line start. */
    char *line = buf;
    while (line && *line) {
        char *nl = strchr(line, '\n');
        if (nl) *nl = '\0';

        char *p = line;
        while (*p && isspace((unsigned char)*p)) p++;
        if (*p == '#' || *p == '\0') { line = nl ? nl + 1 : NULL; continue; }

        if (strncmp(p, "logo", 4) == 0) {
            char *sep = p + 4;
            while (*sep && isspace((unsigned char)*sep)) sep++;
            if (*sep == ':') {
                sep++;
                while (*sep && isspace((unsigned char)*sep)) sep++;
                /* strip trailing whitespace / \r */
                size_t len = strlen(sep);
                while (len > 0 && isspace((unsigned char)sep[len - 1]))
                    sep[--len] = '\0';
                if (*sep) {
                    snprintf(out_path, outsz, "%s", sep);
                    return 0;
                }
            }
        }
        line = nl ? nl + 1 : NULL;
    }
    return -1;
}

void app_main(void)
{
    gfx_ctx_t *ctx = gfx_open_mode(GFX_MODE_STREAM);
    if (!ctx) {
        /* Encode the gfx_open failure step in the exit code (10..16) so the
         * kernel log message identifies which step broke. */
        duneos_exit(10 + gfx_last_error());
        return;
    }

    uint16_t w, h;
    gfx_get_info(ctx, &w, &h);

    /* Background: vertical desert-sand gradient — composed row by row on
     * the stack and pushed via gfx_blit, so it's H SPI transactions
     * instead of W*H individual pixel writes. */
    {
        uint16_t row[320];   /* widest supported board row */
        for (int y = 0; y < h; y++) {
            uint8_t r = (uint8_t)( 60 + (y * 160) / h);  /*  60..220 */
            uint8_t g = (uint8_t)( 40 + (y * 110) / h);  /*  40..150 */
            uint8_t b = (uint8_t)( 20 + (y *  45) / h);  /*  20..65  */
            uint16_t c = GFX_RGB(r, g, b);
            for (int x = 0; x < w && x < (int)(sizeof(row) / sizeof(row[0])); x++)
                row[x] = c;
            gfx_blit(ctx, 0, y, w, 1, row);
        }
    }

    /* Try the configured logo first. If /etc/splash/config.yaml exists and
     * the referenced .dr file loads cleanly, blit it centred and skip the
     * procedural dune art — the user's custom logo is the focal point. */
    char logo_path[96];
    duneos_image_t logo = {0};
    int have_logo = (read_logo_path(logo_path, sizeof(logo_path)) == 0 &&
                     duneos_image_load_dr(logo_path, &logo) == 0);
    if (have_logo) {
        int x = (int)(w  - logo.width)  / 2;
        int y = (int)(h - logo.height) / 2;
        if (x < 0) x = 0;
        if (y < 0) y = 0;
        gfx_blit(ctx, x, y, logo.width, logo.height, logo.pixels);
        duneos_image_free(&logo);
        gfx_flush(ctx);
        usleep(1500 * 1000);
        gfx_close(ctx);
        duneos_exit(0);
        return;
    }

    /* Three dune silhouettes near the lower third. gfx_rect with h=1 is a
     * cheap horizontal-line primitive in STREAM mode. */
    int baseline = (h * 7) / 10;
    uint16_t dune_color = GFX_RGB(50, 30, 15);
    int cx = w / 2;

    /* Left, centre, right peaks — triangular, each a stack of 1px rects. */
    for (int dy = 0; dy < 22; dy++) {
        int hw = 22 - dy;                       /* half-width */
        gfx_rect(ctx, cx - 60 - hw, baseline - dy, hw * 2, 1, dune_color);
        gfx_rect(ctx, cx + 60 - hw, baseline - dy, hw * 2, 1, dune_color);
    }
    for (int dy = 0; dy < 32; dy++) {
        int hw = 32 - dy;
        gfx_rect(ctx, cx - hw, baseline - dy - 6, hw * 2, 1, dune_color);
    }

    /* Wordmark — 8×8 font; "DuneOS" is 6 chars = 48 px. */
    const char *title = "DuneOS";
    int title_w = 8 * 6;
    int title_x = cx - title_w / 2;
    int title_y = 14;
    /* Faux drop-shadow: same text 1px down-right in darker tone. */
    gfx_text(ctx, title_x + 1, title_y + 1, title,
             GFX_RGB(30, 20, 10), GFX_RGB(60, 40, 20));
    gfx_text(ctx, title_x, title_y, title,
             GFX_WHITE, GFX_RGB(60, 40, 20));

    const char *tag = "boot";
    int tag_w = 8 * 4;
    gfx_text(ctx, cx - tag_w / 2, title_y + 14, tag,
             GFX_RGB(255, 230, 180), GFX_RGB(60, 40, 20));

    gfx_flush(ctx);

    /* Long enough to read, short enough not to annoy. */
    usleep(4500 * 1000);

    gfx_close(ctx);
    duneos_exit(0);
}
