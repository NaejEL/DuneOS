#pragma once

/*
 * libui — coverflow carousel widget (optional module).
 *
 * A horizontal icon carousel: the focused item's icon is shown large and
 * centred with its label below, and its neighbours are scaled down on each
 * side — the launcher's "coverflow" look, reusable by any app (file picker,
 * level select, …).
 *
 * Labels are a borrowed array `labels[i]`. Icons are fetched lazily through a
 * callback `icon_fn(i, ctx)` that returns the path to item i's `.dr` raster (see
 * <duneos/image.h>) or NULL — so the widget stores no per-item paths (the app
 * can resolve into a single reused buffer; see ui_app_icon_path()). A NULL or
 * unreadable icon draws a tinted placeholder with the label's initial.
 *
 * The focused icon is large and centred; `radius` items on each side are shown
 * progressively smaller (coverflow). Responsive (ADR 024): the focused size
 * defaults to a fraction of the widget height, so it grows with the screen.
 *
 * This module pulls in libimage; add BOTH $SDK/ui/ui_carousel.c and
 * $SDK/image/libimage.c to the app's sources.
 *
 * Usage:
 *   ui_carousel_t car;
 *   ui_carousel_init(&car, 0, bar_h, ui_screen_w(ui), ui_screen_h(ui) - 2*bar_h);
 *   ui_carousel_set_items(&car, labels, n, my_icon_fn, my_ctx);
 *   ui_carousel_draw(ui, &car);
 *   ... on key: switch (ui_carousel_key(&car, code)) { ... } ...
 */

#include <stdint.h>
#include "duneos/ui.h"

/* Returns the .dr path for item `idx` (into caller-owned storage, reusable
 * between calls — the widget consumes it before the next call), or NULL. */
typedef const char *(*ui_carousel_icon_fn)(int idx, void *ctx);

typedef struct {
    int x, y, w, h;                /* area the carousel occupies               */
    const char *const *labels;     /* borrowed item labels                     */
    ui_carousel_icon_fn icon_fn;   /* lazy icon-path provider                   */
    void *icon_ctx;                /* opaque, passed to icon_fn                 */
    int n;
    int sel;                       /* focused item                             */
    int big;                       /* focused icon size px; 0 = auto from h     */
    int radius;                    /* items shown each side; 0 = auto (2)       */
} ui_carousel_t;

void ui_carousel_init(ui_carousel_t *c, int x, int y, int w, int h);
void ui_carousel_set_items(ui_carousel_t *c, const char *const *labels, int n,
                           ui_carousel_icon_fn icon_fn, void *icon_ctx);
void ui_carousel_draw(ui_t *ui, const ui_carousel_t *c);

typedef enum {
    UI_CAROUSEL_NONE = 0,   /* key ignored                                      */
    UI_CAROUSEL_MOVED,      /* selection changed — redraw                       */
    UI_CAROUSEL_SELECT,     /* Enter on items[sel]                              */
    UI_CAROUSEL_CANCEL,     /* Esc                                              */
} ui_carousel_event_t;

/* Left/Up = previous, Right/Down = next (both wrap), Enter = select, Esc =
 * cancel. Feed key codes from <duneos/input_ioctl.h>. */
ui_carousel_event_t ui_carousel_key(ui_carousel_t *c, uint16_t key);

/*
 * Resolve an app's icon to a `.dr` path via the ADR 023 search path:
 *   1. <dap_dir>/<name>.dr   (adjacent — side-loaded apps)
 *   2. /flash/share/icons/<icon_name>.dr  (flashed shared theme dir)
 *   3. /flash/share/icons/application.dr  (OS generic fallback)
 * `dap_path` is the app's .dap path; `icon_name` is its manifest `icon:` field
 * (may be NULL/""). Writes the first existing path to `out` and returns true,
 * or returns false (caller passes NULL to the carousel → placeholder).
 */
bool ui_app_icon_path(const char *dap_path, const char *icon_name,
                      char *out, int outsz);
