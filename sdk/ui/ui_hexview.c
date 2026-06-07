/*
 * libui — hexview widget. See <duneos/ui_hexview.h>.
 */

#include "duneos/ui_hexview.h"
#include "duneos/input_ioctl.h"

#include <stdio.h>

#define ROW_H 10

/* Row width budget: "OFFSET " (5 chars) + bpr*("HH " = 3) + bpr ascii.
 * → 8*(5 + bpr*4) px. Solve for the widget width. */
static int bytes_per_row(int w)
{
    int bpr = (w / 8 - 5) / 4;
    if (bpr < 1)  bpr = 1;
    if (bpr > 16) bpr = 16;
    return bpr;
}

void ui_hexview_init(ui_hexview_t *v, int x, int y, int w, int h)
{
    v->x = x; v->y = y; v->w = w; v->h = h;
    v->data = NULL; v->len = 0; v->scroll = 0;
    v->bpr = bytes_per_row(w);
    v->rows = h / ROW_H;
}

void ui_hexview_set(ui_hexview_t *v, const unsigned char *data, int len)
{
    v->data = data; v->len = len; v->scroll = 0;
}

void ui_hexview_draw(ui_t *ui, const ui_hexview_t *v)
{
    gfx_ctx_t *g = ui_gfx(ui);
    uint16_t bg = ui_theme(ui)->bg, fg = ui_theme(ui)->fg;
    gfx_rect(g, v->x, v->y, v->w, v->h, bg);
    if (!v->data) return;

    char line[80];
    int off = v->scroll * v->bpr;
    for (int row = 0; row < v->rows && off < v->len; row++, off += v->bpr) {
        int pos = snprintf(line, sizeof(line), "%04X ", off);
        for (int i = 0; i < v->bpr; i++)
            pos += (off + i < v->len)
                       ? snprintf(line + pos, sizeof(line) - pos, "%02X ", v->data[off + i])
                       : snprintf(line + pos, sizeof(line) - pos, "   ");
        for (int i = 0; i < v->bpr && off + i < v->len; i++) {
            unsigned char c = v->data[off + i];
            pos += snprintf(line + pos, sizeof(line) - pos, "%c",
                            (c >= 0x20 && c < 0x7f) ? c : '.');
        }
        gfx_text(g, v->x, v->y + row * ROW_H, line, fg, bg);
    }
}

int ui_hexview_key(ui_hexview_t *v, uint16_t key)
{
    int total = v->bpr > 0 ? (v->len + v->bpr - 1) / v->bpr : 0;
    int max   = total > v->rows ? total - v->rows : 0;
    if (key == KEY_UP && v->scroll > 0)     { v->scroll--; return 1; }
    if (key == KEY_DOWN && v->scroll < max) { v->scroll++; return 1; }
    return 0;
}
