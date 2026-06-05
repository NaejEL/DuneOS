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
#include "duneos/appmeta.h"
#include "duneos/input_ioctl.h"

extern void duneos_exit(int code);
extern int  duneos_supervisor_launch(const char *path);
extern int  duneos_supervisor_running_count(void);
extern void duneos_supervisor_wait_for_completion(int target_count);

#define MAX_APPS      32
#define NAME_LEN      32
#define PATH_LEN      288

static char        s_names[MAX_APPS][NAME_LEN];
static char        s_paths[MAX_APPS][PATH_LEN];
static const char *s_name_ptrs[MAX_APPS];
static int         s_count;
static int         s_total_dap;   /* .dap files seen (before display filter) */
static int         s_meta_ok;     /* of those, manifests read successfully    */

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

static void render_home(ui_t *ui, const ui_list_t *list)
{
    ui_clear(ui);
    ui_titlebar(ui, "DuneOS");
    ui_list_draw(ui, list);
    ui_statusbar(ui, "Up/Down  Enter=run");
    ui_flush(ui);
}

/* ----- launch ------------------------------------------------------------ */

/* Release the display, run the app to completion in its own slot, then let
 * the caller reacquire the display and repaint. */
static void launch(const char *path, gfx_ctx_t **gfx, ui_t **ui)
{
    ui_destroy(*ui);
    gfx_close(*gfx);
    *ui  = NULL;
    *gfx = NULL;

    int before = duneos_supervisor_running_count();
    if (duneos_supervisor_launch(path) == 0)
        duneos_supervisor_wait_for_completion(before);

    *gfx = gfx_open_mode(GFX_MODE_STREAM);
    if (*gfx) *ui = ui_create(*gfx);
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

    uint16_t sw, sh;
    ui_size(ui, &sw, &sh);
    int bar_h = 8 + 2 * ui_theme(ui)->pad;

    ui_list_t list;
    ui_list_init(&list, 0, bar_h, sw, sh - 2 * bar_h);
    ui_list_set_items(&list, s_name_ptrs, s_count);

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
        render_home(ui, &list);
    }

    for (;;) {
        input_event_t ev;
        if (read(input, &ev, sizeof(ev)) != (int)sizeof(ev)) continue;
        if (ev.type != INPUT_EV_KEY || ev.value == INPUT_VAL_RELEASE) continue;
        if (s_count == 0) continue;

        switch (ui_list_key(&list, ev.code)) {
        case UI_LIST_MOVED:
            ui_list_draw(ui, &list);
            ui_flush(ui);
            break;
        case UI_LIST_SELECT:
            launch(s_paths[list.sel], &gfx, &ui);
            if (!ui) { if (input >= 0) close(input); duneos_exit(10); }
            render_home(ui, &list);
            break;
        case UI_LIST_CANCEL:
        case UI_LIST_NONE:
            break;
        }
    }
}
