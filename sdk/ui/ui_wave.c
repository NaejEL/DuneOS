/*
 * libui — wave (chronogram) widget. See <duneos/ui_wave.h>.
 */

#include "duneos/ui_wave.h"
#include "duneos/input_ioctl.h"

#include <stddef.h>

#define LABEL_W   28          /* left gutter for channel names */
#define WAVE_COL  GFX_RGB(110, 220, 170)
#define EDGE_COL  GFX_RGB( 70, 150, 120)

static int plot_w(const ui_wave_t *w) { return w->w - LABEL_W; }

void ui_wave_init(ui_wave_t *w, int x, int y, int wd, int ht)
{
    w->x = x; w->y = y; w->w = wd; w->h = ht;
    w->samples = NULL; w->n = 0; w->nch = 0; w->labels = NULL;
    w->t0 = 0; w->us_per_px = 1; w->span_us = 0;
}

void ui_wave_set(ui_wave_t *w, const ui_wave_sample_t *s, int n, int nch,
                 const char *const *labels)
{
    w->samples = s; w->n = n; w->nch = nch; w->labels = labels;
    w->t0 = (n > 0) ? s[0].t_us : 0;
    w->span_us = (n > 1) ? (s[n - 1].t_us - s[0].t_us) : 0;
    int pw = plot_w(w);
    uint32_t upp = (pw > 0 && w->span_us > 0) ? w->span_us / (uint32_t)pw : 1;
    w->us_per_px = upp ? upp : 1;
}

int ui_wave_time_x(const ui_wave_t *w, uint32_t t_us)
{
    int x0 = w->x + LABEL_W;
    if (t_us <= w->t0) return x0;
    uint32_t dx = (t_us - w->t0) / w->us_per_px;
    int pw = plot_w(w);
    return (dx >= (uint32_t)pw) ? x0 + pw : x0 + (int)dx;
}

void ui_wave_draw(ui_t *ui, const ui_wave_t *w)
{
    gfx_ctx_t        *g  = ui_gfx(ui);
    const ui_theme_t *th = ui_theme(ui);
    uint16_t bg = th->bg, fg = th->fg, grid = th->border;

    gfx_rect(g, w->x, w->y, w->w, w->h, bg);
    if (w->n <= 0 || w->nch <= 0) return;

    int x0 = w->x + LABEL_W, pw = plot_w(w);
    int lane_h = w->h / w->nch;

    for (int ch = 0; ch < w->nch; ch++) {
        int lane_y = w->y + ch * lane_h;
        int yhi = lane_y + 3, ylo = lane_y + lane_h - 4;
        if (ylo <= yhi) ylo = yhi + 2;

        gfx_rect(g, w->x, lane_y, w->w, 1, grid);           /* lane separator */
        if (w->labels && w->labels[ch])
            gfx_text(g, w->x, lane_y + (lane_h - 8) / 2, w->labels[ch], fg, bg);

        /* Walk the transition stream for this channel and draw segments. */
        int level = (int)((w->samples[0].levels >> ch) & 1);
        int last_x = x0;
        for (int i = 0; i < w->n; i++) {
            int nl = (int)((w->samples[i].levels >> ch) & 1);
            if (nl == level) continue;
            int xi = ui_wave_time_x(w, w->samples[i].t_us);
            int yseg = level ? yhi : ylo;
            if (xi > last_x) gfx_rect(g, last_x, yseg, xi - last_x, 1, WAVE_COL);
            gfx_rect(g, xi, yhi, 1, ylo - yhi + 1, EDGE_COL);   /* vertical edge */
            last_x = xi;
            level = nl;
        }
        int yseg = level ? yhi : ylo;
        if (x0 + pw > last_x) gfx_rect(g, last_x, yseg, x0 + pw - last_x, 1, WAVE_COL);
    }
}

int ui_wave_key(ui_wave_t *w, uint16_t key)
{
    int pw = plot_w(w);
    uint32_t step = (uint32_t)pw / 4 * w->us_per_px;   /* pan a quarter-screen */
    if (step == 0) step = w->us_per_px;
    uint32_t end = w->t0 + w->span_us;

    switch (key) {
    case KEY_LEFT:
        if (w->t0 == 0 && w->span_us == 0) return 0;
        w->t0 = (w->t0 > step) ? w->t0 - step : 0;
        return 1;
    case KEY_RIGHT: {
        uint32_t span_px = (uint32_t)pw * w->us_per_px;
        uint32_t max_t0 = (end > span_px) ? end - span_px : 0;
        uint32_t nt = w->t0 + step;
        w->t0 = (nt > max_t0) ? max_t0 : nt;
        return 1;
    }
    case KEY_UP:                                   /* zoom in */
        if (w->us_per_px > 1) { w->us_per_px = w->us_per_px / 2 + (w->us_per_px & 1); return 1; }
        return 0;
    case KEY_DOWN: {                               /* zoom out */
        uint32_t fit = (pw > 0 && w->span_us > 0) ? w->span_us / (uint32_t)pw + 1 : 1;
        if (w->us_per_px < fit) { w->us_per_px *= 2; return 1; }
        return 0;
    }
    }
    return 0;
}
