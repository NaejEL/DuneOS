#pragma once

/*
 * libui — on-screen keyboard, driven by PREV/NEXT/VALIDATE/BACK.
 *
 * How a board with no keyboard reaches INPUT_INTENT_TEXT: four intents are
 * enough to walk a grid and commit a glyph, so any board that can navigate a
 * list can type. That derivation is what makes TEXT reachable on the T-Embed
 * (see reachable_intents in tools/dbt/input_map.py).
 *
 * Caller-owned state and caller-owned buffer, no malloc. Two TUs:
 *   ui_vkbd.c       the state machine — pure, host-compiled and host-tested
 *   ui_vkbd_draw.c  the rendering, which needs libgfx
 * An app links both. Nothing else in sdk/ui references either, so an app that
 * does not list them pays zero bytes for the keyboard (there is no weak
 * reference and no registry: the only way in is the text-entry backend the app
 * passes explicitly — see ui_text_entry.h).
 */

#include <stdint.h>
#include <stdbool.h>

#include "duneos/input_ioctl.h"

#ifndef UI_T_DECLARED
#define UI_T_DECLARED
typedef struct ui ui_t;
#endif

/* Grid width in cells. The cell count is a function of the layout, so the row
 * count follows from it; see ui_vkbd_cell_count(). */
#define UI_VKBD_COLS  10

typedef enum {
    UI_VKBD_NONE = 0,
    UI_VKBD_MOVED,      /* focus or layer changed — redraw the grid */
    UI_VKBD_CHANGED,    /* buffer edited — redraw the grid and the text */
    UI_VKBD_SUBMIT,     /* read k->buf */
    UI_VKBD_CANCEL,
} ui_vkbd_event_t;

typedef struct {
    int   x, y, w, h;
    char *buf;      /* caller storage, cap bytes including the NUL */
    int   cap;
    int   len;
    int   sel;      /* focused cell, 0 .. ui_vkbd_cell_count()-1 */
    bool  shift;    /* upper-case layer */
} ui_vkbd_t;

/* buf is NUL-terminated at once; cap must be >= 1. */
void ui_vkbd_init(ui_vkbd_t *k, int x, int y, int w, int h, char *buf, int cap);

/*
 * Feed one intent.
 *   PREV / NEXT          move the focus by one cell, wrapping
 *   ALT_PREV / ALT_NEXT  move it by one row, wrapping
 *   VALIDATE             commit the focused cell (glyph, shift, erase, submit)
 *   ERASE                delete the last character
 *   TEXT                 insert it->ch directly (a keyboard board may still
 *                        drive the grid, e.g. to reach a glyph it lacks)
 *   BACK                 cancel
 * The buffer is never written at or past cap-1.
 */
ui_vkbd_event_t ui_vkbd_intent(ui_vkbd_t *k, const duneos_intent_t *it);

/* Layout queries, for the renderer and for tests. */
int  ui_vkbd_cell_count(void);
int  ui_vkbd_rows(void);
/* The glyph a cell commits in the current layer, or 0 for an action cell. */
char ui_vkbd_cell_glyph(const ui_vkbd_t *k, int idx);
/* The one- or two-character label to draw in a cell, written into `out`
 * (3 bytes is enough). Caller-provided so this TU keeps no writable statics. */
void ui_vkbd_cell_label(const ui_vkbd_t *k, int idx, char *out, int outsz);

/* ui_vkbd_draw.c */
void ui_vkbd_draw(ui_t *ui, const ui_vkbd_t *k);

/* Indirection so ui_text_entry.c never names a ui_vkbd_* symbol, and an app
 * that does not ask for the keyboard does not link it. Also in ui_vkbd_draw.c. */
typedef struct {
    void            (*init)(ui_vkbd_t *k, int x, int y, int w, int h,
                            char *buf, int cap);
    ui_vkbd_event_t (*intent)(ui_vkbd_t *k, const duneos_intent_t *it);
    void            (*draw)(ui_t *ui, const ui_vkbd_t *k);
} ui_vkbd_backend_t;

const ui_vkbd_backend_t *ui_vkbd_backend(void);
