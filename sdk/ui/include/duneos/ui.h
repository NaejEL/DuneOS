#pragma once

/*
 * DuneOS SDK — libui: a minimal retained-less widget toolkit on top of libgfx.
 *
 * Goal: give every app a consistent, themeable look (think a very small GTK)
 * without the memory cost of a full toolkit. Widgets are plain structs the
 * caller owns; libui only draws them and runs their navigation logic. There
 * is no global state, no allocation per widget, and no LVGL.
 *
 * Why not LVGL: a DuneOS app's code lives in a shared 64 KiB IRAM exec pool
 * (see kernel/duneos_loader). LVGL's .text alone overflows it. libui is a few
 * KiB of code and is meant to be linked into the app like any other SDK source.
 *
 * Drawing goes through a gfx_ctx_t (see <duneos/gfx.h>). libui works in either
 * gfx mode; in GFX_MODE_BUFFERED the caller must ui_flush() to present a frame,
 * in GFX_MODE_STREAM every call paints immediately and ui_flush() is a no-op.
 * Each widget draw touches only its own rectangle, so after one full repaint a
 * navigation step can redraw just the affected widget — cheap even over SPI.
 *
 * Usage:
 *   gfx_ctx_t *g = gfx_open_mode(GFX_MODE_STREAM);
 *   ui_t *ui = ui_create(g);
 *   ui_clear(ui);
 *   ui_titlebar(ui, "Launcher");
 *   ui_list_t list;
 *   ui_list_init(&list, 0, 12, 240, 135 - 24);
 *   ui_list_set_items(&list, names, n);
 *   ui_list_draw(ui, &list);
 *   ui_statusbar(ui, "Up/Down  Enter=run");
 *   ui_flush(ui);
 *   ... read keys, feed ui_list_key(), redraw on change ...
 */

#include <stdint.h>
#include <stdbool.h>
#include "duneos/gfx.h"

/* ----- theme ------------------------------------------------------------- */

typedef struct {
    uint16_t bg;          /* screen background                         */
    uint16_t fg;          /* default text                             */
    uint16_t accent;      /* selection / highlight fill               */
    uint16_t accent_fg;   /* text drawn on accent                     */
    uint16_t bar_bg;      /* title / status bar fill                  */
    uint16_t bar_fg;      /* title / status bar text                  */
    uint16_t panel_bg;    /* ui_panel fill                            */
    uint16_t border;      /* panel / divider lines                    */
    uint8_t  pad;         /* inner padding, px                        */
} ui_theme_t;

/* A dark default theme. Copy and tweak fields to restyle. */
ui_theme_t ui_theme_default(void);

/* ----- context ----------------------------------------------------------- */

typedef struct ui ui_t;

/* Borrow an open gfx context. Does not take ownership — the caller still
 * gfx_close()s it. Returns NULL on allocation failure or if gfx is NULL. */
ui_t *ui_create(gfx_ctx_t *gfx);
void  ui_destroy(ui_t *ui);

void              ui_set_theme(ui_t *ui, const ui_theme_t *theme);
const ui_theme_t *ui_theme(const ui_t *ui);
void              ui_size(const ui_t *ui, uint16_t *w, uint16_t *h);

/* Present the frame (gfx_flush passthrough; no-op in STREAM mode). */
void ui_flush(ui_t *ui);

/* ----- primitives -------------------------------------------------------- */

typedef enum {
    UI_ALIGN_LEFT = 0,
    UI_ALIGN_CENTER,
    UI_ALIGN_RIGHT,
} ui_align_t;

/* Fill the whole screen with the theme background. */
void ui_clear(ui_t *ui);

/* Filled rectangle in panel_bg, optional 1px border. */
void ui_panel(ui_t *ui, int x, int y, int w, int h, bool border);

/* One line of text aligned within [x, x+w). Truncated to fit; never wraps. */
void ui_label(ui_t *ui, int x, int y, int w, const char *s,
              ui_align_t align, uint16_t fg, uint16_t bg);

/* Full-width bar at the top showing centred title text. */
void ui_titlebar(ui_t *ui, const char *title);

/* Full-width bar at the bottom showing left-aligned hint text. */
void ui_statusbar(ui_t *ui, const char *text);

/* Clear the screen and draw a centred title + body (split on '\n'). Handy for
 * "Loading…" / error / empty-state screens. body may be NULL. */
void ui_message(ui_t *ui, const char *title, const char *body);

/* ----- list / menu widget ------------------------------------------------ */

typedef struct {
    int x, y, w, h;             /* geometry in px                          */
    int row_h;                  /* row height in px (>= 8); init sets 13   */
    const char *const *items;   /* borrowed array of NUL-terminated labels */
    int count;
    int sel;                    /* selected index, clamped to [0,count-1]  */
    int top;                    /* first visible row (scroll offset)       */
} ui_list_t;

/* row_h defaults to 13; override the field after init to change row height. */
void ui_list_init(ui_list_t *l, int x, int y, int w, int h);
void ui_list_set_items(ui_list_t *l, const char *const *items, int count);
void ui_list_draw(ui_t *ui, const ui_list_t *l);

typedef enum {
    UI_LIST_NONE = 0,   /* key ignored, no state change                    */
    UI_LIST_MOVED,      /* selection or scroll changed — redraw the list   */
    UI_LIST_SELECT,     /* Enter pressed on items[sel]                     */
    UI_LIST_CANCEL,     /* Esc pressed                                     */
} ui_list_event_t;

/* Feed a key code (KEY_UP/KEY_DOWN/KEY_ENTER/KEY_ESC from <duneos/input_ioctl.h>).
 * Updates sel/top and reports what happened. UP/DOWN wrap around the ends. */
ui_list_event_t ui_list_key(ui_list_t *l, uint16_t key);

/* ----- text view widget (scrolling line buffer) -------------------------- */

/*
 * A terminal-style scrolling text area. The caller owns the line storage: a
 * flat buffer of `max_lines * stride` bytes, each line a NUL-terminated slot
 * of `stride` bytes. push() appends a line (overwriting the oldest when full);
 * draw() shows the most recent lines that fit, newest at the bottom.
 */
typedef struct {
    int   x, y, w, h;
    int   row_h;        /* row height in px (>= 8); init sets 8 */
    char *buf;          /* caller storage: max_lines * stride bytes */
    int   max_lines;
    int   stride;       /* bytes per line slot (text + NUL) */
    int   count;        /* lines currently stored (<= max_lines) */
    int   head;         /* ring index of the oldest stored line */
} ui_textview_t;

void ui_textview_init(ui_textview_t *tv, int x, int y, int w, int h,
                      char *buf, int max_lines, int stride);
void ui_textview_clear(ui_textview_t *tv);
void ui_textview_push(ui_textview_t *tv, const char *line);   /* truncates to stride-1 */
void ui_textview_draw(ui_t *ui, const ui_textview_t *tv);

/* ----- input line widget ------------------------------------------------- */

/*
 * A single editable line with a prompt and a block cursor. The caller owns the
 * text buffer (`buf`, capacity `cap`). Feed key codes to ui_input_key().
 */
typedef struct {
    int         x, y, w;
    const char *prompt;   /* drawn before the text, e.g. "$ " (may be NULL) */
    char       *buf;      /* caller storage */
    int         cap;      /* buf capacity incl NUL */
    int         len;      /* current text length */
} ui_input_t;

void ui_input_init(ui_input_t *in, int x, int y, int w,
                   char *buf, int cap, const char *prompt);
void ui_input_clear(ui_input_t *in);
void ui_input_draw(ui_t *ui, const ui_input_t *in);

typedef enum {
    UI_INPUT_NONE = 0,    /* key ignored                                     */
    UI_INPUT_CHANGED,     /* text edited (printable / backspace) — redraw    */
    UI_INPUT_SUBMIT,      /* Enter — read in->buf                            */
    UI_INPUT_CANCEL,      /* Esc                                             */
} ui_input_event_t;

/* Printable ASCII appends; KEY_BACKSPACE deletes; KEY_ENTER submits;
 * KEY_ESC cancels. */
ui_input_event_t ui_input_key(ui_input_t *in, uint16_t key);
