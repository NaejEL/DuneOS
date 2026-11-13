/*
 * ui_vkbd — on-screen keyboard state machine. Pure: no gfx, no fd, no malloc,
 * so tests/host compiles it with the host CC. Rendering is ui_vkbd_draw.c.
 */

#include "duneos/ui_vkbd.h"

static const char k_glyphs[] =
    "abcdefghij"
    "klmnopqrst"
    "uvwxyz0123"
    "456789.-_@"
    " :/,!?+#%";

#define GLYPH_COUNT  ((int)(sizeof(k_glyphs) - 1))

/* Action cells follow the glyphs, so a cell index below GLYPH_COUNT is a glyph
 * and the grid stays one flat ring that PREV/NEXT walks. */
enum {
    CELL_SHIFT = GLYPH_COUNT,
    CELL_ERASE,
    CELL_OK,
    CELL_COUNT,
};

int ui_vkbd_cell_count(void) { return CELL_COUNT; }

int ui_vkbd_rows(void)
{
    return (CELL_COUNT + UI_VKBD_COLS - 1) / UI_VKBD_COLS;
}

static char upper(char c)
{
    return (c >= 'a' && c <= 'z') ? (char)(c - 'a' + 'A') : c;
}

char ui_vkbd_cell_glyph(const ui_vkbd_t *k, int idx)
{
    if (!k || idx < 0 || idx >= GLYPH_COUNT) return 0;
    char c = k_glyphs[idx];
    return k->shift ? upper(c) : c;
}

/* Writes into the caller's buffer rather than returning a pointer to a static
 * scratch: a file-scope buffer here would be app-pool BSS, which this file is
 * meant to stay clear of. */
void ui_vkbd_cell_label(const ui_vkbd_t *k, int idx, char *out, int outsz)
{
    if (!out || outsz < 1) return;
    out[0] = '\0';
    if (!k || outsz < 3) return;

    const char *fixed = 0;
    switch (idx) {
    case CELL_SHIFT: fixed = "Ab"; break;
    case CELL_ERASE: fixed = "<x"; break;
    case CELL_OK:    fixed = "OK"; break;
    default: break;
    }
    char c = fixed ? 0 : ui_vkbd_cell_glyph(k, idx);
    if (!fixed && c == ' ') fixed = "sp";

    if (fixed) {
        out[0] = fixed[0]; out[1] = fixed[1]; out[2] = '\0';
        return;
    }
    if (c == 0) return;
    out[0] = c;
    out[1] = '\0';
}

void ui_vkbd_init(ui_vkbd_t *k, int x, int y, int w, int h, char *buf, int cap)
{
    if (!k) return;
    k->x = x; k->y = y; k->w = w; k->h = h;
    k->buf = buf;
    k->cap = cap;
    k->len = 0;
    k->sel = 0;
    k->shift = false;
    if (buf && cap > 0) buf[0] = '\0';
}

/* cap-1 is the NUL's slot, so the last writable index is cap-2. */
static bool insert(ui_vkbd_t *k, char c)
{
    if (!k->buf || k->cap < 2 || k->len >= k->cap - 1) return false;
    k->buf[k->len++] = c;
    k->buf[k->len] = '\0';
    return true;
}

static bool erase(ui_vkbd_t *k)
{
    if (!k->buf || k->len <= 0) return false;
    k->buf[--k->len] = '\0';
    return true;
}

static void move(ui_vkbd_t *k, int by)
{
    int n = CELL_COUNT;
    k->sel = ((k->sel + by) % n + n) % n;
}

static ui_vkbd_event_t commit(ui_vkbd_t *k)
{
    switch (k->sel) {
    case CELL_SHIFT: k->shift = !k->shift; return UI_VKBD_MOVED;
    case CELL_ERASE: return erase(k) ? UI_VKBD_CHANGED : UI_VKBD_NONE;
    case CELL_OK:    return UI_VKBD_SUBMIT;
    default: break;
    }
    char c = ui_vkbd_cell_glyph(k, k->sel);
    if (c == 0) return UI_VKBD_NONE;
    return insert(k, c) ? UI_VKBD_CHANGED : UI_VKBD_NONE;
}

ui_vkbd_event_t ui_vkbd_intent(ui_vkbd_t *k, const duneos_intent_t *it)
{
    if (!k || !it) return UI_VKBD_NONE;

    switch (it->kind) {
    case INPUT_INTENT_PREV:     move(k, -1);              return UI_VKBD_MOVED;
    case INPUT_INTENT_NEXT:     move(k, +1);              return UI_VKBD_MOVED;
    case INPUT_INTENT_ALT_PREV: move(k, -UI_VKBD_COLS);   return UI_VKBD_MOVED;
    case INPUT_INTENT_ALT_NEXT: move(k, +UI_VKBD_COLS);   return UI_VKBD_MOVED;
    case INPUT_INTENT_VALIDATE: return commit(k);
    case INPUT_INTENT_ERASE:    return erase(k) ? UI_VKBD_CHANGED : UI_VKBD_NONE;
    case INPUT_INTENT_BACK:     return UI_VKBD_CANCEL;
    case INPUT_INTENT_TEXT:
        return insert(k, it->ch) ? UI_VKBD_CHANGED : UI_VKBD_NONE;
    default:                    return UI_VKBD_NONE;
    }
}
