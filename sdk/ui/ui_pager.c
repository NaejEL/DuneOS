/*
 * libui — pager widget. See <duneos/ui_pager.h>.
 */

#include "duneos/ui_pager.h"
#include "duneos/input_ioctl.h"

#include <stddef.h>

#define ROW_H 10

void ui_pager_init(ui_pager_t *p, int x, int y, int w, int h)
{
    p->x = x; p->y = y; p->w = w; p->h = h;
    p->text = NULL; p->len = 0; p->scroll = 0; p->nlines = 0;
    p->rows = h / ROW_H;
}

void ui_pager_set(ui_pager_t *p, const char *text, int len)
{
    p->text   = text;
    p->len    = len;
    p->scroll = 0;
    int n = (len > 0) ? 1 : 0;
    for (int i = 0; i < len; i++) if (text[i] == '\n') n++;
    p->nlines = n;
}

void ui_pager_draw(ui_t *ui, const ui_pager_t *p)
{
    gfx_ctx_t *g = ui_gfx(ui);
    uint16_t bg = ui_theme(ui)->bg, fg = ui_theme(ui)->fg;
    gfx_rect(g, p->x, p->y, p->w, p->h, bg);
    if (!p->text) return;

    int cols = p->w / 8;
    int i = 0, line = 0;
    while (i < p->len && line < p->scroll) if (p->text[i++] == '\n') line++;

    char out[80];
    for (int row = 0; i < p->len && row < p->rows; row++) {
        int c = 0;
        while (i < p->len && p->text[i] != '\n' && c < cols && c < (int)sizeof(out) - 1) {
            unsigned char ch = (unsigned char)p->text[i++];
            out[c++] = (ch >= 0x20 && ch < 0x7f) ? (char)ch : '.';
        }
        out[c] = '\0';
        gfx_text(g, p->x, p->y + row * ROW_H, out, fg, bg);
        while (i < p->len && p->text[i] != '\n') i++;   /* drop clipped tail */
        if (i < p->len) i++;                              /* skip newline      */
    }
}

int ui_pager_key(ui_pager_t *p, uint16_t key)
{
    int max = p->nlines > p->rows ? p->nlines - p->rows : 0;
    if (key == KEY_UP && p->scroll > 0)        { p->scroll--; return 1; }
    if (key == KEY_DOWN && p->scroll < max)    { p->scroll++; return 1; }
    return 0;
}
