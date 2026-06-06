/*
 * splash — one-shot boot logo.
 *
 * Reads /flash/etc/splash/config.yaml:
 *   logo:        path to a .dr raster to blit centred (optional)
 *   duration_ms: how long to hold before exiting (optional, default 2000)
 * Drop a logo.png next to config.yaml and `dbt flashimg` converts it to
 * logo.dr automatically — a non-developer can change the splash with an image
 * editor, no toolchain. If no logo loads, falls back to procedural desert art.
 *
 * Combined with `after: splash` in init.yaml, dependent services (g_shell, app
 * launcher, …) start once it has cleared.
 *
 * STREAM mode (no userspace back-buffer) — CardPuter's DRAM can't afford a
 * 64 KiB framebuffer just for a static image.
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

#define DEFAULT_DURATION_MS 2000

/* Parse /etc/splash/config.yaml for `logo:` (→ out_logo) and `duration_ms:`
 * (→ out_ms). Missing keys leave the outputs untouched. Returns 0 if the file
 * was read, -1 if absent. */
static int read_config(char *out_logo, size_t logosz, int *out_ms)
{
    char conf_path[64];
    if (duneos_config_path("splash", conf_path, sizeof(conf_path)) != 0)
        return -1;
    int fd = open(conf_path, O_RDONLY);
    if (fd < 0) return -1;

    /* Read the whole file: the keys (logo:/duration_ms:) sit after a long
     * comment header, so a short buffer would miss them. */
    char   buf[1024];
    size_t total = 0;
    ssize_t r;
    while (total < sizeof(buf) - 1 &&
           (r = read(fd, buf + total, sizeof(buf) - 1 - total)) > 0)
        total += (size_t)r;
    close(fd);
    if (total == 0) return -1;
    buf[total] = '\0';

    char *line = buf;
    while (line && *line) {
        char *nl = strchr(line, '\n');
        if (nl) *nl = '\0';

        char *p = line;
        while (*p && isspace((unsigned char)*p)) p++;
        if (*p == '#' || *p == '\0') { line = nl ? nl + 1 : NULL; continue; }

        char *sep = strchr(p, ':');
        if (sep) {
            char *v = sep + 1;
            while (*v && isspace((unsigned char)*v)) v++;
            size_t len = strlen(v);
            while (len > 0 && isspace((unsigned char)v[len - 1])) v[--len] = '\0';

            if (strncmp(p, "logo", 4) == 0 && *v) {
                snprintf(out_logo, logosz, "%s", v);
            } else if (strncmp(p, "duration_ms", 11) == 0) {
                int val = 0, d = 0;
                for (char *q = v; *q >= '0' && *q <= '9'; q++) { val = val * 10 + (*q - '0'); d++; }
                if (d) *out_ms = val;
            }
        }
        line = nl ? nl + 1 : NULL;
    }
    return 0;
}

/* Fill the screen with a vertical gradient, composed one row at a time on the
 * stack and pushed via gfx_blit (H transactions, not W*H pixel writes). */
static void fill_gradient(gfx_ctx_t *ctx, int w, int h,
                          uint8_t r0, uint8_t g0, uint8_t b0,
                          uint8_t r1, uint8_t g1, uint8_t b1)
{
    uint16_t row[320];
    for (int y = 0; y < h; y++) {
        uint8_t r = (uint8_t)(r0 + (y * (r1 - r0)) / h);
        uint8_t g = (uint8_t)(g0 + (y * (g1 - g0)) / h);
        uint8_t b = (uint8_t)(b0 + (y * (b1 - b0)) / h);
        uint16_t c = GFX_RGB(r, g, b);
        for (int x = 0; x < w && x < 320; x++) row[x] = c;
        gfx_blit(ctx, 0, y, w, 1, row);
    }
}

/* Procedural fallback: desert sand + three dune silhouettes + wordmark. */
static void draw_procedural(gfx_ctx_t *ctx, int w, int h)
{
    fill_gradient(ctx, w, h, 60, 40, 20, 220, 150, 65);

    int baseline = (h * 7) / 10;
    uint16_t dune = GFX_RGB(50, 30, 15);
    int cx = w / 2;
    for (int dy = 0; dy < 22; dy++) {
        int hw = 22 - dy;
        gfx_rect(ctx, cx - 60 - hw, baseline - dy, hw * 2, 1, dune);
        gfx_rect(ctx, cx + 60 - hw, baseline - dy, hw * 2, 1, dune);
    }
    for (int dy = 0; dy < 32; dy++) {
        int hw = 32 - dy;
        gfx_rect(ctx, cx - hw, baseline - dy - 6, hw * 2, 1, dune);
    }

    const char *title = "DuneOS";
    int title_x = cx - (8 * 6) / 2, title_y = 14;
    gfx_text(ctx, title_x + 1, title_y + 1, title, GFX_RGB(30, 20, 10), GFX_RGB(60, 40, 20));
    gfx_text(ctx, title_x, title_y, title, GFX_WHITE, GFX_RGB(60, 40, 20));
}

void app_main(void)
{
    gfx_ctx_t *ctx = gfx_open_mode(GFX_MODE_STREAM);
    if (!ctx) {
        /* Encode the gfx_open failure step in the exit code (10..16). */
        duneos_exit(10 + gfx_last_error());
        return;
    }

    uint16_t w, h;
    gfx_get_info(ctx, &w, &h);

    char logo_path[96] = { 0 };
    int  duration_ms   = DEFAULT_DURATION_MS;
    read_config(logo_path, sizeof(logo_path), &duration_ms);

    uint16_t lw, lh;
    int have_logo = (logo_path[0] && duneos_image_info_dr(logo_path, &lw, &lh) == 0);

    if (have_logo) {
        /* Streamed row by row (duneos_image_blit_dr) — no full-image heap, so a
         * full-screen logo is fine on a low-RAM board. Fill behind it only when
         * the logo doesn't cover the screen, so its border blends in. */
        if (lw < w || lh < h)
            fill_gradient(ctx, w, h, 12, 16, 30, 22, 28, 48);
        int x = ((int)w - (int)lw) / 2;
        int y = ((int)h - (int)lh) / 2;
        if (x < 0) x = 0;
        if (y < 0) y = 0;
        duneos_image_blit_dr(ctx, x, y, logo_path);
    } else {
        draw_procedural(ctx, w, h);
    }

    gfx_flush(ctx);
    usleep((unsigned)duration_ms * 1000);
    gfx_close(ctx);
    duneos_exit(0);
}
