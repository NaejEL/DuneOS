/*
 * libui — coverflow carousel widget. See <duneos/ui_carousel.h>.
 */

#include "duneos/ui_carousel.h"
#include "duneos/image.h"
#include "duneos/input_ioctl.h"

#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <fcntl.h>
#include <unistd.h>

static int clampi(int v, int lo, int hi)
{
    return v < lo ? lo : (v > hi ? hi : v);
}

/* Nearest-neighbour scale (up or down) of a .dr into a size×size square, blitted
 * one row at a time so no large buffer hits the stack (per gfx.h guidance). */
static void blit_scaled(gfx_ctx_t *g, int x, int y, int size, const duneos_image_t *img)
{
    uint16_t row[256];
    if (size > 256) size = 256;
    for (int ty = 0; ty < size; ty++) {
        const uint16_t *src = img->pixels + (ty * img->height / size) * img->width;
        for (int tx = 0; tx < size; tx++)
            row[tx] = src[tx * img->width / size];
        gfx_blit(g, x, y + ty, size, 1, row);
    }
}

/* Draw item idx (icon via the callback, placeholder on miss) centred on
 * (cx, cy) at size×size. */
static void draw_at(gfx_ctx_t *g, const ui_carousel_t *c, int idx,
                    int cx, int cy, int size)
{
    int x = cx - size / 2, y = cy - size / 2;
    const char *path = c->icon_fn ? c->icon_fn(idx, c->icon_ctx) : NULL;
    duneos_image_t img;
    if (path && duneos_image_load_dr(path, &img) == 0) {
        blit_scaled(g, x, y, size, &img);
        duneos_image_free(&img);
        return;
    }
    uint16_t bg = GFX_RGB(60, 70, 90), fg = GFX_RGB(200, 210, 230);
    gfx_rect(g, x, y, size, size, bg);
    char l[2] = { c->labels[idx][0], '\0' };
    gfx_text(g, x + size / 2 - 4, y + size / 2 - 4, l, fg, bg);
}

void ui_carousel_init(ui_carousel_t *c, int x, int y, int w, int h)
{
    memset(c, 0, sizeof(*c));
    c->x = x; c->y = y; c->w = w; c->h = h;
}

void ui_carousel_set_items(ui_carousel_t *c, const char *const *labels, int n,
                           ui_carousel_icon_fn icon_fn, void *icon_ctx)
{
    c->labels   = labels;
    c->icon_fn  = icon_fn;
    c->icon_ctx = icon_ctx;
    c->n        = n;
    if (c->sel >= n) c->sel = n > 0 ? n - 1 : 0;
}

void ui_carousel_draw(ui_t *ui, const ui_carousel_t *c)
{
    gfx_ctx_t        *g  = ui_gfx(ui);
    const ui_theme_t *th = ui_theme(ui);
    uint16_t bg = th->bg;

    gfx_rect(g, c->x, c->y, c->w, c->h, bg);
    if (c->n <= 0) return;

    int big    = c->big    ? c->big    : clampi(c->h * 52 / 100, 40, 64);
    int radius = c->radius ? c->radius : 2;
    int gap    = big / 12 + 2;
    int cx     = c->x + c->w / 2;
    int cy     = c->y + c->h * 42 / 100;   /* icon vertical centre */

    /* Sides first (so the larger focused icon overlaps them), each depth
     * smaller. Skip a depth that would wrap onto an already-shown item. */
    int sz = big, xl = cx - big / 2 - gap, xr = cx + big / 2 + gap;
    for (int d = 1; d <= radius && 2 * d < c->n; d++) {
        sz = sz * 66 / 100;
        if (sz < 14) sz = 14;
        int li = ((c->sel - d) % c->n + c->n) % c->n;
        int ri = (c->sel + d) % c->n;
        draw_at(g, c, ri, xr + sz / 2, cy, sz); xr += sz + gap;
        draw_at(g, c, li, xl - sz / 2, cy, sz); xl -= sz + gap;
    }
    draw_at(g, c, c->sel, cx, cy, big);

    ui_label(ui, c->x, cy + big / 2 + 6, c->w, c->labels[c->sel],
             UI_ALIGN_CENTER, th->fg, bg);
}

ui_carousel_event_t ui_carousel_key(ui_carousel_t *c, uint16_t key)
{
    if (c->n <= 0) return UI_CAROUSEL_NONE;
    switch (key) {
    case KEY_LEFT:
    case KEY_UP:
        c->sel = (c->sel - 1 + c->n) % c->n;
        return UI_CAROUSEL_MOVED;
    case KEY_RIGHT:
    case KEY_DOWN:
        c->sel = (c->sel + 1) % c->n;
        return UI_CAROUSEL_MOVED;
    case KEY_ENTER:
        return UI_CAROUSEL_SELECT;
    case KEY_ESC:
        return UI_CAROUSEL_CANCEL;
    }
    return UI_CAROUSEL_NONE;
}

/* ----- icon resolution (ADR 023 search path) ----------------------------- */

static bool file_exists(const char *p)
{
    int fd = open(p, O_RDONLY);
    if (fd < 0) return false;
    close(fd);
    return true;
}

bool ui_app_icon_path(const char *dap_path, const char *icon_name,
                      char *out, int outsz)
{
    /* 1. adjacent to the .dap: "...x.dap" → "...x.dr" */
    snprintf(out, outsz, "%s", dap_path);
    int L = (int)strlen(out);
    if (L >= 4 && out[L - 4] == '.') {
        out[L - 3] = 'd'; out[L - 2] = 'r'; out[L - 1] = '\0';
        if (file_exists(out)) return true;
    }
    /* 2. flashed shared theme dir, by manifest icon name */
    if (icon_name && icon_name[0]) {
        snprintf(out, outsz, "/flash/share/icons/%s.dr", icon_name);
        if (file_exists(out)) return true;
    }
    /* 3. OS generic fallback */
    snprintf(out, outsz, "/flash/share/icons/application.dr");
    return file_exists(out);
}
