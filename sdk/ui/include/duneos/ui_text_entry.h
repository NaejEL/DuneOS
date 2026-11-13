#pragma once

/*
 * libui — the portable way to ask for a string.
 *
 * Resolves at runtime: on a board with a full keyboard it is the existing
 * ui_input_t line editor; on a board without one it is the on-screen keyboard.
 * The app writes one flow and gets the right one.
 *
 * The vkbd is reached only through the backend the app passes in — see the
 * zero-cost note in <duneos/ui_vkbd.h>.
 *
 *   ui_vkbd_backend()   an app that wants the fallback passes this
 *   NULL                an app that does not: the line editor on every board
 */

#include <stdint.h>
#include <stdbool.h>

#include "duneos/ui.h"
#include "duneos/ui_vkbd.h"

typedef enum {
    UI_TEXT_ENTRY_NONE = 0,
    UI_TEXT_ENTRY_CHANGED,   /* text or focus moved — redraw */
    UI_TEXT_ENTRY_SUBMIT,    /* read te->buf */
    UI_TEXT_ENTRY_CANCEL,
} ui_text_entry_event_t;

typedef struct {
    char *buf;       /* caller storage: the real text, cap bytes incl NUL  */
    char *render;    /* caller storage, cap bytes; required when mask      */
    int   cap;
    bool  mask;      /* draw '*' instead of the characters                 */
    bool  use_vkbd;
    const ui_vkbd_backend_t *backend;
    ui_input_t line;
    ui_vkbd_t  vkbd;
} ui_text_entry_t;

/*
 * `have_keyboard` comes from duneos_input_has_keyboard(&in.topo) — the board's
 * answer, not a compile-time guess. With no `vkbd` backend the line editor is
 * used regardless, which is the right degradation: a board without a keyboard
 * can still submit an empty string rather than deadlock.
 *
 * `buf` is cleared. `render` may be NULL when mask is false.
 */
void ui_text_entry_init(ui_text_entry_t *te, int x, int y, int w, int h,
                        char *buf, char *render, int cap, const char *prompt,
                        bool mask, bool have_keyboard,
                        const ui_vkbd_backend_t *vkbd);

void ui_text_entry_draw(ui_t *ui, ui_text_entry_t *te);

ui_text_entry_event_t ui_text_entry_intent(ui_text_entry_t *te,
                                           const duneos_intent_t *it);
