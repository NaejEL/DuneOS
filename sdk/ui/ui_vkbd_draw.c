/*
 * ui_vkbd_draw — rendering for the on-screen keyboard. Split from ui_vkbd.c so
 * the state machine stays free of libgfx and compiles on the host.
 */

#include "duneos/ui.h"
#include "duneos/ui_vkbd.h"
#include "duneos/ui_text_entry.h"

void ui_vkbd_draw(ui_t *ui, const ui_vkbd_t *k)
{
    if (!ui || !k) return;

    const ui_theme_t *th = ui_theme(ui);
    int rows = ui_vkbd_rows();
    if (rows <= 0) return;

    int cw = k->w / UI_VKBD_COLS;
    int ch = k->h / rows;
    if (cw < 3 || ch < 8) return;

    ui_panel(ui, k->x, k->y, k->w, k->h, true);

    int n = ui_vkbd_cell_count();
    for (int i = 0; i < n; i++) {
        int cx = k->x + (i % UI_VKBD_COLS) * cw;
        int cy = k->y + (i / UI_VKBD_COLS) * ch;
        bool on = (i == k->sel);
        uint16_t bg = on ? th->accent    : th->panel_bg;
        uint16_t fg = on ? th->accent_fg : th->fg;

        char label[4];
        ui_vkbd_cell_label(k, i, label, (int)sizeof(label));
        gfx_rect(ui_gfx(ui), cx, cy, cw, ch, bg);
        ui_label(ui, cx, cy + (ch - 8) / 2, cw, label, UI_ALIGN_CENTER, fg, bg);
    }
}

/* The only door into the keyboard from ui_text_entry.c. An app reaches the
 * on-screen keyboard by calling this; an app that does not call it links no
 * part of it. Defined here rather than in ui_vkbd.c so that file stays free of
 * libgfx and host-compilable. */
const ui_vkbd_backend_t *ui_vkbd_backend(void)
{
    static const ui_vkbd_backend_t ops = {
        .init   = ui_vkbd_init,
        .intent = ui_vkbd_intent,
        .draw   = ui_vkbd_draw,
    };
    return &ops;
}
