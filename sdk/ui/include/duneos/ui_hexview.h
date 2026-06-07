#pragma once

/*
 * libui — hexview widget: a scrollable hexdump of a byte buffer.
 *
 * Borrows a byte buffer and renders it as `OFFSET  HH HH …  |ascii|` rows,
 * vertically scrollable. Bytes-per-row adapts to the widget width. Reusable for
 * inspecting any binary — a file viewer, a captured payload, a register dump.
 *
 *   ui_hexview_t h;
 *   ui_hexview_init(&h, 0, bar_h, ui_screen_w(ui), ui_screen_h(ui) - 2*bar_h);
 *   ui_hexview_set(&h, data, len);
 *   ui_hexview_draw(ui, &h);
 *   ... on key: if (ui_hexview_key(&h, code)) ui_hexview_draw(ui, &h); ...
 */

#include <stdint.h>
#include "duneos/ui.h"

typedef struct {
    int x, y, w, h;
    const unsigned char *data;   /* borrowed */
    int len;
    int scroll;   /* top visible row */
    int bpr;      /* bytes per row (from width) */
    int rows;     /* visible rows */
} ui_hexview_t;

void ui_hexview_init(ui_hexview_t *v, int x, int y, int w, int h);
void ui_hexview_set(ui_hexview_t *v, const unsigned char *data, int len); /* resets scroll */
void ui_hexview_draw(ui_t *ui, const ui_hexview_t *v);

/* Up/Down scroll. Returns 1 if the view moved (redraw), 0 otherwise. */
int ui_hexview_key(ui_hexview_t *v, uint16_t key);
