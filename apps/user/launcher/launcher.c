/*
 * launcher — DuneOS graphical app launcher (contest 2026 centrepiece).
 *
 * Scans the install dirs for .dap apps, shows them in a navigable list, and
 * launches the selected one in its own supervisor slot. Built on libui /
 * libgfx in STREAM mode (no back-buffer) so it leaves enough DRAM for the
 * app it spawns.
 *
 * Display ownership: the launcher releases /dev/disp0 before launching and
 * reacquires it on return. Two apps cannot share the display SPI CS at once
 * (see CLAUDE.md), and the launcher has nothing to draw while it blocks on
 * the child anyway.
 *
 * Discovery scans /sd/apps (where user apps land via deploy / USB-MSC drag &
 * drop). Each .dap's embedded manifest is read with libappmeta: only apps that
 * declare the `display` capability are shown — that filters out CLI tools and
 * daemons that happen to sit in the same dir. The grid label is the manifest
 * `name`, not the bare filename. Per-app icons come with the Phase 25 pipeline
 * (libappmeta already exposes the icon field for it).
 */

#include <unistd.h>
#include <fcntl.h>
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <dirent.h>
#include <sys/stat.h>

#include "duneos/gfx.h"
#include "duneos/ui.h"
#include "duneos/ui_carousel.h"
#include "duneos/appmeta.h"
#include "duneos/input_ioctl.h"

extern void duneos_exit(int code);
extern int  duneos_supervisor_chain(const char *child_path);

/* Where we stash the highlighted app's name across a handoff, so the launcher
 * reopens on the same selection after the child exits (ADR 031). */
#define SEL_FILE "/tmp/.launcher_sel"

#define MAX_APPS      32
#define NAME_LEN      32
#define PATH_LEN      288
#define ICON_PATH_LEN 96    /* "/flash/share/icons/<name>.dr" + margin */

static char        s_names[MAX_APPS][NAME_LEN];
static char        s_paths[MAX_APPS][PATH_LEN];
static char        s_icon_names[MAX_APPS][NAME_LEN]; /* manifest icon name, or "" */
static const char *s_name_ptrs[MAX_APPS];
static char        s_icon_scratch[ICON_PATH_LEN];    /* reused by the icon callback */
static int         s_count;
static int         s_total_dap;   /* .dap files seen (before display filter) */
static int         s_meta_ok;     /* of those, manifests read successfully    */

static ui_carousel_t s_car;

static const char *const k_dirs[] = { "/sd/apps" };

/* ----- discovery --------------------------------------------------------- */

/* Case-insensitive ".dap" suffix test. strcasecmp is not in the kernel export
 * table, and FAT returns 8.3 names uppercased, so compare by hand. */
static bool has_dap_ext(const char *name, int *base_len)
{
    int len = (int)strlen(name);
    if (len < 4) return false;
    const char *e = name + len - 4;
    char c1 = e[1] | 0x20, c2 = e[2] | 0x20, c3 = e[3] | 0x20;
    if (e[0] != '.' || c1 != 'd' || c2 != 'a' || c3 != 'p') return false;
    *base_len = len - 4;
    return true;
}

static bool already_listed(const char *name)
{
    for (int i = 0; i < s_count; i++)
        if (strcmp(s_names[i], name) == 0) return true;
    return false;
}

static void copy_name(char *dst, const char *src)
{
    int i = 0;
    for (; src[i] && i < NAME_LEN - 1; i++) dst[i] = src[i];
    dst[i] = '\0';
}

static void scan_dir(const char *dir)
{
    DIR *d = opendir(dir);
    if (!d) return;
    struct dirent *e;
    while (s_count < MAX_APPS && (e = readdir(d)) != NULL) {
        int base_len;
        if (!has_dap_ext(e->d_name, &base_len)) continue;
        s_total_dap++;

        char path[PATH_LEN];
        snprintf(path, sizeof(path), "%s/%s", dir, e->d_name);

        appmeta_t meta;
        if (appmeta_read(path, &meta) != 0) continue;   /* unreadable / not a dap */
        s_meta_ok++;
        if (!meta.has_display) continue;                /* CLI / daemon — hide    */

        /* Display name: manifest name, falling back to the filename base. */
        char name[NAME_LEN];
        if (meta.name[0]) {
            copy_name(name, meta.name);
        } else {
            if (base_len >= NAME_LEN) base_len = NAME_LEN - 1;
            memcpy(name, e->d_name, base_len);
            name[base_len] = '\0';
        }
        if (already_listed(name)) continue;

        copy_name(s_names[s_count], name);
        memcpy(s_paths[s_count], path, sizeof(s_paths[s_count]));
        copy_name(s_icon_names[s_count], meta.icon);   /* "" if none */
        s_name_ptrs[s_count] = s_names[s_count];
        s_count++;
    }
    closedir(d);
}

static void scan_apps(void)
{
    s_count = 0;
    s_total_dap = 0;
    s_meta_ok = 0;
    for (size_t i = 0; i < sizeof(k_dirs) / sizeof(k_dirs[0]); i++)
        scan_dir(k_dirs[i]);
}

/* ----- rendering --------------------------------------------------------- */

/* Carousel icon provider: resolve item idx's .dr path lazily (ADR 023 search
 * path) into one shared buffer — so per-app storage is just the icon name. */
static const char *launcher_icon(int idx, void *ctx)
{
    (void)ctx;
    return ui_app_icon_path(s_paths[idx], s_icon_names[idx],
                            s_icon_scratch, sizeof(s_icon_scratch))
               ? s_icon_scratch : NULL;
}

/* Title + reusable libui coverflow carousel + position hint. The carousel owns
 * the icon/label area; the launcher only frames it. */
static void render_home(ui_t *ui)
{
    ui_clear(ui);
    ui_statusbar_top(ui, "DuneOS");
    ui_carousel_draw(ui, &s_car);
    char st[32];
    snprintf(st, sizeof(st), "%d/%d   < >  Enter", s_car.sel + 1, s_car.n);
    ui_statusbar(ui, st);
    ui_flush(ui);
}

/* ----- selection persistence (survives a handoff) ------------------------ */

static void save_sel(void)
{
    if (s_car.sel < 0 || s_car.sel >= s_count) return;
    int fd = open(SEL_FILE, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) return;
    write(fd, s_names[s_car.sel], (int)strlen(s_names[s_car.sel]));
    close(fd);
}

/* Reopen on the app we handed off to, so launcher→game→launcher feels seamless. */
static void restore_sel(void)
{
    char buf[NAME_LEN];
    int fd = open(SEL_FILE, O_RDONLY);
    if (fd < 0) return;
    int n = (int)read(fd, buf, sizeof(buf) - 1);
    close(fd);
    if (n <= 0) return;
    buf[n] = '\0';
    for (int i = 0; i < s_count; i++)
        if (strcmp(s_names[i], buf) == 0) { s_car.sel = i; return; }
}

/* ----- launch (navigation-stack handoff, ADR 031) ------------------------ */

/* Release the display + input, register the app as our successor, and exit.
 * The supervisor frees our RAM, runs the app in the freed space, and relaunches
 * us when it exits — so the launcher is never resident during a child, and a
 * RAM-hungry app (e.g. tetris' 32 KiB canvas) gets the contiguous block it needs. */
static void launch(const char *path, gfx_ctx_t *gfx, ui_t *ui, int input)
{
    save_sel();
    ui_destroy(ui);
    gfx_close(gfx);
    if (input >= 0) close(input);
    duneos_supervisor_chain(path);
    duneos_exit(0);   /* does not return — supervisor takes over */
}

/* ----- main -------------------------------------------------------------- */

void app_main(void)
{
    /* Diagnostic exit codes mirror g_shell's convention:
     *   10 = display open failed
     *   11 = /dev/input/event0 open failed                                  */
    gfx_ctx_t *gfx = gfx_open_mode(GFX_MODE_STREAM);
    if (!gfx) duneos_exit(10);

    int input = open("/dev/input/event0", O_RDONLY);
    if (input < 0) { gfx_close(gfx); duneos_exit(11); }

    ui_t *ui = ui_create(gfx);
    if (!ui) { close(input); gfx_close(gfx); duneos_exit(10); }

    scan_apps();

    /* Carousel fills the area between the title and status bars; sizes derive
     * from the screen (ADR 024 — responsive). */
    int bar_h = 8 + 2 * ui_theme(ui)->pad;
    ui_carousel_init(&s_car, 0, bar_h, ui_screen_w(ui), ui_screen_h(ui) - 2 * bar_h);
    ui_carousel_set_items(&s_car, s_name_ptrs, s_count, launcher_icon, NULL);
    restore_sel();   /* reopen on the app we last handed off to (ADR 031) */

    if (s_count == 0) {
        /* Diagnostic body: how the scan classified /sd/apps. Lets us tell apart
         * "dir empty / wrong path" (0 dap), "manifests unreadable" (dap>read),
         * and "no graphical apps / stale builds without capabilities"
         * (read>0, gui 0) without a serial console. */
        char body[64];
        snprintf(body, sizeof(body), "/sd/apps: %d dap, %d read, %d gui",
                 s_total_dap, s_meta_ok, s_count);
        ui_message(ui, "No apps found", body);
        ui_statusbar(ui, "Empty");
        ui_flush(ui);
    } else {
        render_home(ui);
    }

    for (;;) {
        input_event_t ev;
        if (read(input, &ev, sizeof(ev)) != (int)sizeof(ev)) continue;
        if (ev.type != INPUT_EV_KEY || ev.value == INPUT_VAL_RELEASE) continue;
        if (s_count == 0) continue;

        switch (ui_carousel_key(&s_car, ev.code)) {
        case UI_CAROUSEL_MOVED:
            render_home(ui);
            break;
        case UI_CAROUSEL_SELECT:
            launch(s_paths[s_car.sel], gfx, ui, input);   /* hands off + exits */
            break;
        case UI_CAROUSEL_CANCEL:   /* launcher is home — Esc does not quit */
        case UI_CAROUSEL_NONE:
            break;
        }
    }
}
