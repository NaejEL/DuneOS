/*
 * ui_text_entry — line editor or on-screen keyboard, chosen from the board's
 * own answer about its hardware.
 */

#include "duneos/ui_text_entry.h"

#define LINE_H  12

void ui_text_entry_init(ui_text_entry_t *te, int x, int y, int w, int h,
                        char *buf, char *render, int cap, const char *prompt,
                        bool mask, bool have_keyboard,
                        const ui_vkbd_backend_t *vkbd)
{
    if (!te) return;

    te->buf      = buf;
    te->render   = render;
    te->cap      = cap;
    te->mask     = mask && render != 0;
    te->backend  = vkbd;
    te->use_vkbd = !have_keyboard && vkbd != 0;

    if (buf && cap > 0) buf[0] = '\0';

    ui_input_init(&te->line, x, y, w, buf, cap, prompt);

    if (te->use_vkbd) {
        int grid_y = y + LINE_H;
        int grid_h = h - LINE_H;
        if (grid_h < 8) grid_h = 8;
        te->backend->init(&te->vkbd, x, grid_y, w, grid_h, buf, cap);
    }
}

/* The widgets edit `buf` — the truth — and masking happens only at draw time,
 * over a copy. The alternative (letting the widget edit a mask and mirroring
 * each change back) has to reconstruct the edit from the key that caused it,
 * which is what wifi.c used to do. */
static const ui_input_t *line_for_draw(ui_text_entry_t *te)
{
    if (!te->mask) return &te->line;

    int n = te->line.len;
    if (n > te->cap - 1) n = te->cap - 1;
    for (int i = 0; i < n; i++) te->render[i] = '*';
    te->render[n < 0 ? 0 : n] = '\0';

    /* Same geometry and cursor, different bytes. */
    te->line.buf = te->render;
    return &te->line;
}

static void line_after_draw(ui_text_entry_t *te)
{
    if (te->mask) te->line.buf = te->buf;
}

void ui_text_entry_draw(ui_t *ui, ui_text_entry_t *te)
{
    if (!ui || !te) return;

    ui_input_draw(ui, line_for_draw(te));
    line_after_draw(te);

    if (te->use_vkbd) te->backend->draw(ui, &te->vkbd);
}

static ui_text_entry_event_t from_vkbd(ui_text_entry_t *te,
                                       const duneos_intent_t *it)
{
    switch (te->backend->intent(&te->vkbd, it)) {
    case UI_VKBD_MOVED:
    case UI_VKBD_CHANGED:
        te->line.len = te->vkbd.len;
        te->line.pos = te->vkbd.len;
        return UI_TEXT_ENTRY_CHANGED;
    case UI_VKBD_SUBMIT: return UI_TEXT_ENTRY_SUBMIT;
    case UI_VKBD_CANCEL: return UI_TEXT_ENTRY_CANCEL;
    default:             return UI_TEXT_ENTRY_NONE;
    }
}

ui_text_entry_event_t ui_text_entry_intent(ui_text_entry_t *te,
                                           const duneos_intent_t *it)
{
    if (!te || !it) return UI_TEXT_ENTRY_NONE;

    if (te->use_vkbd) return from_vkbd(te, it);

    switch (ui_input_intent(&te->line, it)) {
    case UI_INPUT_CHANGED: return UI_TEXT_ENTRY_CHANGED;
    case UI_INPUT_SUBMIT:  return UI_TEXT_ENTRY_SUBMIT;
    case UI_INPUT_CANCEL:  return UI_TEXT_ENTRY_CANCEL;
    default:               return UI_TEXT_ENTRY_NONE;
    }
}
