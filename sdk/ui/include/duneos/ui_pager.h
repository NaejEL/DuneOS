#pragma once

/*
 * libui — pager widget: a scrollable read-only view of a text buffer.
 *
 * Borrows a flat char buffer and renders it line by line within its rect, with
 * vertical scrolling. Long lines are clipped (not wrapped). Reusable anywhere an
 * app shows text it doesn't edit — a file viewer, a README, help, logs.
 *
 * (Distinct from ui_textview, which is a push-as-you-go terminal line ring;
 *  ui_pager views an existing buffer with a scroll offset.)
 *
 *   ui_pager_t p;
 *   ui_pager_init(&p, 0, bar_h, ui_screen_w(ui), ui_screen_h(ui) - 2*bar_h);
 *   ui_pager_set(&p, text, len);
 *   ui_pager_draw(ui, &p);
 *   ... on key: if (ui_pager_key(&p, code)) ui_pager_draw(ui, &p); ...
 */

#include <stdint.h>
#include "duneos/ui.h"

typedef struct {
    int x, y, w, h;
    const char *text;   /* borrowed */
    int len;
    int scroll;         /* top visible line */
    int nlines;         /* total lines (cached)  */
    int rows;           /* visible rows (cached)  */
} ui_pager_t;

void ui_pager_init(ui_pager_t *p, int x, int y, int w, int h);
void ui_pager_set(ui_pager_t *p, const char *text, int len);   /* resets scroll */
void ui_pager_draw(ui_t *ui, const ui_pager_t *p);

/* Up/Down (or Left/Right page is not handled) scroll. Returns 1 if the view
 * moved (redraw), 0 otherwise. */
int ui_pager_key(ui_pager_t *p, uint16_t key);
