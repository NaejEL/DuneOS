/*
 * drview — .dr raster gallery / viewer.
 *
 * Scans /share/icons and /sd for .dr images and shows them as a libui
 * coverflow gallery (the .dr files are their own thumbnails); Enter blits the
 * selected one full-screen. A tiny app — all the work is in libui (ui_carousel)
 * and libimage (duneos_image_blit_dr); this is just the plumbing.
 *
 * Until the ADR 026 "open with" handoff lands, it finds images itself; later it
 * will also accept a path argument from the file explorer.
 */

#include <unistd.h>
#include <fcntl.h>
#include <string.h>
#include <stdio.h>
#include <dirent.h>

#include "duneos/input_ioctl.h"
#include "duneos/gfx.h"
#include "duneos/ui.h"
#include "duneos/ui_carousel.h"
#include "duneos/image.h"

extern void duneos_exit(int code);

#define MAX_IMG  48
#define PATH_LEN 160
#define NAME_LEN 40

static gfx_ctx_t *g;
static ui_t      *ui;
static int        s_input = -1;
static uint16_t   s_sw, s_sh;

static char        s_path[MAX_IMG][PATH_LEN];
static char        s_name[MAX_IMG][NAME_LEN];
static const char *s_name_ptr[MAX_IMG];
static int         s_n;

static ui_carousel_t s_car;
enum { MODE_GALLERY, MODE_FULL };
static int s_mode;

static int has_dr(const char *n)
{
    int L = (int)strlen(n);
    return L >= 3 && n[L-3] == '.' && (n[L-2]|0x20) == 'd' && (n[L-1]|0x20) == 'r';
}

static void scan(const char *dir)
{
    DIR *d = opendir(dir);
    if (!d) return;
    struct dirent *e;
    while (s_n < MAX_IMG && (e = readdir(d)) != NULL) {
        if (!has_dr(e->d_name)) continue;
        snprintf(s_path[s_n], PATH_LEN, "%s/%s", dir, e->d_name);
        snprintf(s_name[s_n], NAME_LEN, "%s", e->d_name);
        s_name_ptr[s_n] = s_name[s_n];
        s_n++;
    }
    closedir(d);
}

static const char *icon_of(int idx, void *ctx) { (void)ctx; return s_path[idx]; }

static void draw_gallery(void)
{
    ui_clear(ui);
    ui_titlebar(ui, "drview");
    if (s_n == 0) { ui_message(ui, "No .dr images", "Esc=quit"); ui_flush(ui); return; }
    ui_carousel_draw(ui, &s_car);
    char st[40];
    snprintf(st, sizeof(st), "%d/%d  Enter=view  Esc=quit", s_car.sel + 1, s_n);
    ui_statusbar(ui, st);
    ui_flush(ui);
}

static void draw_full(void)
{
    ui_clear(ui);
    const char *p = s_path[s_car.sel];
    uint16_t iw, ih;
    if (duneos_image_info_dr(p, &iw, &ih) == 0) {
        int x = ((int)s_sw - (int)iw) / 2, y = ((int)s_sh - (int)ih) / 2;
        if (x < 0) x = 0;
        if (y < 0) y = 0;
        duneos_image_blit_dr(g, x, y, p);
    }
    ui_statusbar(ui, s_name[s_car.sel]);
    ui_flush(ui);
}

void app_main(void)
{
    g = gfx_open_mode(GFX_MODE_STREAM);
    if (!g) duneos_exit(10);
    s_input = open("/dev/input/event0", O_RDONLY);
    if (s_input < 0) { gfx_close(g); duneos_exit(11); }
    ui = ui_create(g);
    if (!ui) { close(s_input); gfx_close(g); duneos_exit(10); }
    ui_size(ui, &s_sw, &s_sh);

    scan("/share/icons");
    scan("/sd");

    int bar_h = 8 + 2 * ui_theme(ui)->pad;
    ui_carousel_init(&s_car, 0, bar_h, s_sw, s_sh - 2 * bar_h);
    ui_carousel_set_items(&s_car, s_name_ptr, s_n, icon_of, NULL);
    draw_gallery();

    for (;;) {
        input_event_t ev;
        if (read(s_input, &ev, sizeof(ev)) != (int)sizeof(ev)) continue;
        if (ev.type != INPUT_EV_KEY || ev.value == INPUT_VAL_RELEASE) continue;
        uint16_t k = ev.code;

        if (s_mode == MODE_FULL) {
            if (k == KEY_ESC || k == KEY_LEFT) { s_mode = MODE_GALLERY; draw_gallery(); }
            continue;
        }
        if (s_n == 0) { if (k == KEY_ESC) break; continue; }

        switch (ui_carousel_key(&s_car, k)) {
        case UI_CAROUSEL_MOVED:  draw_gallery(); break;
        case UI_CAROUSEL_SELECT: s_mode = MODE_FULL; draw_full(); break;
        case UI_CAROUSEL_CANCEL: goto done;
        case UI_CAROUSEL_NONE:   break;
        }
    }

done:
    ui_destroy(ui);
    close(s_input);
    gfx_close(g);
    duneos_exit(0);
}
