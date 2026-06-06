/*
 * libui — see <duneos/ui.h>.
 *
 * Each draw call writes only its own rectangle so navigation can repaint a
 * single widget. Text is the built-in 8x8 gfx font; labels truncate rather
 * than wrap so a row never spills into the next.
 */

#include <stdlib.h>
#include <string.h>

#include "duneos/ui.h"
#include "duneos/input_ioctl.h"

#define GLYPH_W 8
#define GLYPH_H 8

struct ui {
    gfx_ctx_t *gfx;     /* borrowed */
    ui_theme_t theme;
    uint16_t   w, h;
};

ui_theme_t ui_theme_default(void)
{
    ui_theme_t t = {
        .bg        = GFX_RGB(  8,  10,  16),
        .fg        = GFX_RGB(220, 224, 232),
        .accent    = GFX_RGB( 36,  92, 200),
        .accent_fg = GFX_RGB(255, 255, 255),
        .bar_bg    = GFX_RGB( 20,  24,  40),
        .bar_fg    = GFX_RGB(255, 255, 255),
        .panel_bg  = GFX_RGB( 14,  16,  26),
        .border    = GFX_RGB( 64,  72,  96),
        .pad       = 3,
    };
    return t;
}

ui_t *ui_create(gfx_ctx_t *gfx)
{
    if (!gfx) return NULL;
    ui_t *ui = calloc(1, sizeof(*ui));
    if (!ui) return NULL;
    ui->gfx   = gfx;
    ui->theme = ui_theme_default();
    gfx_get_info(gfx, &ui->w, &ui->h);
    return ui;
}

void ui_destroy(ui_t *ui) { free(ui); }

void ui_set_theme(ui_t *ui, const ui_theme_t *theme)
{
    if (ui && theme) ui->theme = *theme;
}

const ui_theme_t *ui_theme(const ui_t *ui) { return ui ? &ui->theme : NULL; }

void ui_size(const ui_t *ui, uint16_t *w, uint16_t *h)
{
    if (!ui) return;
    if (w) *w = ui->w;
    if (h) *h = ui->h;
}

gfx_ctx_t *ui_gfx(const ui_t *ui) { return ui ? ui->gfx : NULL; }

/* Responsive layout helpers (ADR 024): size things as a fraction of the screen
 * instead of hardcoding pixels, so the same app lays out on any panel. */
int ui_screen_w(const ui_t *ui) { return ui ? ui->w : 0; }
int ui_screen_h(const ui_t *ui) { return ui ? ui->h : 0; }
int ui_pct_w(const ui_t *ui, int pct) { return ui ? ui->w * pct / 100 : 0; }
int ui_pct_h(const ui_t *ui, int pct) { return ui ? ui->h * pct / 100 : 0; }

void ui_flush(ui_t *ui) { if (ui) gfx_flush(ui->gfx); }

/* ----- primitives -------------------------------------------------------- */

void ui_clear(ui_t *ui)
{
    if (!ui) return;
    gfx_rect(ui->gfx, 0, 0, ui->w, ui->h, ui->theme.bg);
}

void ui_panel(ui_t *ui, int x, int y, int w, int h, bool border)
{
    if (!ui || w <= 0 || h <= 0) return;
    gfx_rect(ui->gfx, x, y, w, h, ui->theme.panel_bg);
    if (border) {
        uint16_t c = ui->theme.border;
        gfx_rect(ui->gfx, x,         y,         w, 1, c);
        gfx_rect(ui->gfx, x,         y + h - 1, w, 1, c);
        gfx_rect(ui->gfx, x,         y,         1, h, c);
        gfx_rect(ui->gfx, x + w - 1, y,         1, h, c);
    }
}

void ui_label(ui_t *ui, int x, int y, int w, const char *s,
              ui_align_t align, uint16_t fg, uint16_t bg)
{
    if (!ui || !s || w <= 0) return;

    gfx_rect(ui->gfx, x, y, w, GLYPH_H, bg);

    int max_chars = w / GLYPH_W;
    if (max_chars <= 0) return;

    char buf[64];
    int  len = (int)strlen(s);
    if (len > max_chars) len = max_chars;
    if (len > (int)sizeof(buf) - 1) len = (int)sizeof(buf) - 1;
    memcpy(buf, s, len);
    buf[len] = '\0';

    int tw = len * GLYPH_W;
    int tx = x;
    if (align == UI_ALIGN_CENTER) tx = x + (w - tw) / 2;
    else if (align == UI_ALIGN_RIGHT) tx = x + w - tw;

    gfx_text(ui->gfx, tx, y, buf, fg, bg);
}

void ui_titlebar(ui_t *ui, const char *title)
{
    if (!ui) return;
    int bar_h = GLYPH_H + 2 * ui->theme.pad;
    gfx_rect(ui->gfx, 0, 0, ui->w, bar_h, ui->theme.bar_bg);
    ui_label(ui, ui->theme.pad, ui->theme.pad, ui->w - 2 * ui->theme.pad,
             title ? title : "", UI_ALIGN_CENTER,
             ui->theme.bar_fg, ui->theme.bar_bg);
}

void ui_statusbar(ui_t *ui, const char *text)
{
    if (!ui) return;
    int bar_h = GLYPH_H + 2 * ui->theme.pad;
    int y = ui->h - bar_h;
    gfx_rect(ui->gfx, 0, y, ui->w, bar_h, ui->theme.bar_bg);
    ui_label(ui, ui->theme.pad, y + ui->theme.pad, ui->w - 2 * ui->theme.pad,
             text ? text : "", UI_ALIGN_LEFT,
             ui->theme.bar_fg, ui->theme.bar_bg);
}

void ui_message(ui_t *ui, const char *title, const char *body)
{
    if (!ui) return;
    ui_clear(ui);

    int lines = 1;
    if (body) { lines++; for (const char *p = body; *p; p++) if (*p == '\n') lines++; }
    int block_h = lines * (GLYPH_H + 2);
    int y = (ui->h - block_h) / 2;
    if (y < 0) y = 0;

    if (title) {
        ui_label(ui, 0, y, ui->w, title, UI_ALIGN_CENTER,
                 ui->theme.fg, ui->theme.bg);
        y += GLYPH_H + 2;
    }
    if (body) {
        const char *p = body;
        char line[64];
        while (*p) {
            int n = 0;
            while (p[n] && p[n] != '\n' && n < (int)sizeof(line) - 1) n++;
            memcpy(line, p, n);
            line[n] = '\0';
            ui_label(ui, 0, y, ui->w, line, UI_ALIGN_CENTER,
                     ui->theme.fg, ui->theme.bg);
            y += GLYPH_H + 2;
            p += n;
            if (*p == '\n') p++;
        }
    }
}

/* ----- list / menu widget ------------------------------------------------ */

void ui_list_init(ui_list_t *l, int x, int y, int w, int h)
{
    if (!l) return;
    l->x = x; l->y = y; l->w = w; l->h = h;
    l->row_h = 13;
    l->items = NULL; l->count = 0;
    l->sel = 0; l->top = 0;
}

void ui_list_set_items(ui_list_t *l, const char *const *items, int count)
{
    if (!l) return;
    l->items = items;
    l->count = count < 0 ? 0 : count;
    l->sel = 0; l->top = 0;
}

static int list_row_h(const ui_list_t *l)
{
    return l->row_h < GLYPH_H ? GLYPH_H : l->row_h;
}

static int list_visible_rows(const ui_list_t *l)
{
    int n = l->h / list_row_h(l);
    return n < 1 ? 1 : n;
}

void ui_list_draw(ui_t *ui, const ui_list_t *l)
{
    if (!ui || !l) return;
    int rh      = list_row_h(l);
    int visible = list_visible_rows(l);
    int ty      = (rh - GLYPH_H) / 2;

    for (int r = 0; r < visible; r++) {
        int idx = l->top + r;
        int ry  = l->y + r * rh;
        bool selected = (idx == l->sel) && (idx < l->count);
        uint16_t row_bg = selected ? ui->theme.accent : ui->theme.bg;
        uint16_t row_fg = selected ? ui->theme.accent_fg : ui->theme.fg;

        gfx_rect(ui->gfx, l->x, ry, l->w, rh, row_bg);
        if (idx < l->count && l->items && l->items[idx]) {
            int tx = l->x + ui->theme.pad;
            ui_label(ui, tx, ry + ty, l->w - ui->theme.pad, l->items[idx],
                     UI_ALIGN_LEFT, row_fg, row_bg);
        }
    }

    int painted = visible * rh;
    if (painted < l->h)
        gfx_rect(ui->gfx, l->x, l->y + painted, l->w, l->h - painted, ui->theme.bg);
}

static void list_reveal(ui_list_t *l, int visible)
{
    if (l->sel < l->top) l->top = l->sel;
    else if (l->sel >= l->top + visible) l->top = l->sel - visible + 1;
    if (l->top < 0) l->top = 0;
}

ui_list_event_t ui_list_key(ui_list_t *l, uint16_t key)
{
    if (!l) return UI_LIST_NONE;

    switch (key) {
    case KEY_ESC:
        return UI_LIST_CANCEL;
    case KEY_ENTER:
        return (l->count > 0) ? UI_LIST_SELECT : UI_LIST_NONE;
    case KEY_UP:
    case KEY_DOWN:
        break;
    default:
        return UI_LIST_NONE;
    }

    if (l->count <= 0) return UI_LIST_NONE;

    int prev = l->sel;
    if (key == KEY_UP)   l->sel = (l->sel - 1 + l->count) % l->count;
    else                 l->sel = (l->sel + 1) % l->count;

    list_reveal(l, list_visible_rows(l));

    return (l->sel != prev || l->count == 1) ? UI_LIST_MOVED : UI_LIST_NONE;
}

/* ----- text view widget -------------------------------------------------- */

void ui_textview_init(ui_textview_t *tv, int x, int y, int w, int h,
                      char *buf, int max_lines, int stride)
{
    if (!tv) return;
    tv->x = x; tv->y = y; tv->w = w; tv->h = h;
    tv->row_h = GLYPH_H;
    tv->buf = buf; tv->max_lines = max_lines; tv->stride = stride;
    tv->count = 0; tv->head = 0;
}

void ui_textview_clear(ui_textview_t *tv)
{
    if (!tv) return;
    tv->count = 0; tv->head = 0;
}

void ui_textview_push(ui_textview_t *tv, const char *line)
{
    if (!tv || !tv->buf || tv->max_lines <= 0 || tv->stride <= 0) return;
    int slot;
    if (tv->count < tv->max_lines) {
        slot = (tv->head + tv->count) % tv->max_lines;
        tv->count++;
    } else {
        slot = tv->head;
        tv->head = (tv->head + 1) % tv->max_lines;
    }
    char *dst = tv->buf + (size_t)slot * tv->stride;
    int i = 0;
    if (line) while (line[i] && i < tv->stride - 1) { dst[i] = line[i]; i++; }
    dst[i] = '\0';
}

void ui_textview_draw(ui_t *ui, const ui_textview_t *tv)
{
    if (!ui || !tv) return;
    int rh      = tv->row_h < GLYPH_H ? GLYPH_H : tv->row_h;
    int visible = tv->h / rh;
    if (visible < 1) visible = 1;

    gfx_rect(ui->gfx, tv->x, tv->y, tv->w, tv->h, ui->theme.bg);

    int start = tv->count > visible ? tv->count - visible : 0;
    int rows  = tv->count - start;
    int maxc  = tv->w / GLYPH_W;

    for (int r = 0; r < rows; r++) {
        int idx = (tv->head + start + r) % tv->max_lines;
        const char *line = tv->buf + (size_t)idx * tv->stride;
        char lb[64];
        int n = 0;
        while (line[n] && n < maxc && n < (int)sizeof(lb) - 1) { lb[n] = line[n]; n++; }
        lb[n] = '\0';
        gfx_text(ui->gfx, tv->x, tv->y + r * rh, lb, ui->theme.fg, ui->theme.bg);
    }
}

/* ----- input line widget ------------------------------------------------- */

void ui_input_init(ui_input_t *in, int x, int y, int w,
                   char *buf, int cap, const char *prompt)
{
    if (!in) return;
    in->x = x; in->y = y; in->w = w;
    in->prompt = prompt;
    in->buf = buf; in->cap = cap; in->len = 0;
    if (buf && cap > 0) buf[0] = '\0';
}

void ui_input_clear(ui_input_t *in)
{
    if (!in) return;
    in->len = 0;
    if (in->buf && in->cap > 0) in->buf[0] = '\0';
}

ui_input_event_t ui_input_key(ui_input_t *in, uint16_t key)
{
    if (!in || !in->buf) return UI_INPUT_NONE;
    switch (key) {
    case KEY_ENTER:     return UI_INPUT_SUBMIT;
    case KEY_ESC:       return UI_INPUT_CANCEL;
    case KEY_BACKSPACE:
        if (in->len > 0) { in->buf[--in->len] = '\0'; return UI_INPUT_CHANGED; }
        return UI_INPUT_NONE;
    default:
        if (key >= 0x20 && key < 0x7f && in->len < in->cap - 1) {
            in->buf[in->len++] = (char)key;
            in->buf[in->len]   = '\0';
            return UI_INPUT_CHANGED;
        }
        return UI_INPUT_NONE;
    }
}

void ui_spans(ui_t *ui, int x, int y, int w,
              const ui_span_t *spans, int n, uint16_t bg)
{
    if (!ui || !spans) return;
    gfx_rect(ui->gfx, x, y, w, GLYPH_H, bg);

    int cx = x, limit = x + w;
    char buf[40];
    for (int i = 0; i < n; i++) {
        if (!spans[i].text) continue;
        int maxc = (limit - cx) / GLYPH_W;
        if (maxc <= 0) break;
        int len = (int)strlen(spans[i].text);
        if (len > maxc) len = maxc;
        if (len > (int)sizeof(buf) - 1) len = (int)sizeof(buf) - 1;
        memcpy(buf, spans[i].text, len);
        buf[len] = '\0';
        gfx_text(ui->gfx, cx, y, buf, spans[i].fg, bg);
        cx += len * GLYPH_W;
    }
}

void ui_input_draw(ui_t *ui, const ui_input_t *in)
{
    if (!ui || !in) return;
    gfx_ctx_t        *g  = ui->gfx;
    const ui_theme_t *th = &ui->theme;

    gfx_rect(g, in->x, in->y, in->w, GLYPH_H, th->bg);

    int cols = in->w / GLYPH_W;
    if (cols < 1) return;

    int plen = in->prompt ? (int)strlen(in->prompt) : 0;
    if (plen > cols - 1) plen = cols - 1;
    if (plen > 0) {
        char p[24];
        int n = plen < (int)sizeof(p) - 1 ? plen : (int)sizeof(p) - 1;
        memcpy(p, in->prompt, n); p[n] = '\0';
        gfx_text(g, in->x, in->y, p, th->accent, th->bg);
    }

    int avail = cols - plen - 1;            /* reserve one column for the cursor */
    if (avail < 0) avail = 0;
    int start = in->len > avail ? in->len - avail : 0;
    int show  = in->len - start;

    char t[64];
    if (show > (int)sizeof(t) - 1) show = (int)sizeof(t) - 1;
    memcpy(t, in->buf + start, show); t[show] = '\0';
    int tx = in->x + plen * GLYPH_W;
    gfx_text(g, tx, in->y, t, th->fg, th->bg);

    gfx_rect(g, tx + show * GLYPH_W, in->y, GLYPH_W, GLYPH_H, th->accent);
}
