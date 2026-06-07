/*
 * files — graphical file explorer (ranger-style).
 *
 * Two panes: a directory listing on the left, a live preview of the selected
 * entry on the right. Enter a directory or open a file with Enter; Left goes to
 * the parent. Opening a file gives a full-screen viewer built on libui widgets:
 * ui_pager (scrollable text), ui_hexview (hexdump for binaries), or a blitted
 * picture for .dr rasters. Reusing the widgets keeps viewers consistent across
 * apps (see also i2cscope).
 *
 * The root "/" is synthetic (DuneOS mounts /flash and /sd, there is no real
 * root dir) — it lists those two mounts. Layout sizes derive from the screen
 * (ADR 024), so the same .dap fits any panel.
 */

#include <unistd.h>
#include <fcntl.h>
#include <string.h>
#include <stdio.h>
#include <dirent.h>
#include <sys/stat.h>

#include "duneos/input_ioctl.h"
#include "duneos/gfx.h"
#include "duneos/ui.h"
#include "duneos/ui_pager.h"
#include "duneos/ui_hexview.h"
#include "duneos/image.h"

extern void duneos_exit(int code);

#define PATH_LEN   256
#define NAME_LEN   48
#define MAX_ENTS   64
#define FILE_CAP   4096   /* bytes peeked into a file (preview + viewer window) */

#define C_DIR   GFX_RGB(120, 175, 255)
#define C_FILE  GFX_RGB(220, 220, 220)
#define C_DIM   GFX_RGB(130, 140, 160)

enum { K_TEXT, K_HEX, K_IMAGE };

static gfx_ctx_t *g;
static ui_t      *ui;
static int        s_input = -1;
static uint16_t   s_sw, s_sh;
static int        s_bar_h, s_lw;

static char       s_cwd[PATH_LEN] = "/";
typedef struct { char name[NAME_LEN]; uint8_t is_dir; } entry_t;
static entry_t    s_ent[MAX_ENTS];
static char       s_disp[MAX_ENTS][NAME_LEN + 2];
static const char *s_disp_ptr[MAX_ENTS];
static int        s_nent;

static ui_list_t    s_list;
static ui_pager_t   s_pager;
static ui_hexview_t s_hex;

static unsigned char s_buf[FILE_CAP];
static int        s_buflen;

enum { MODE_BROWSE, MODE_VIEW };
static int  s_mode;
static int  s_view_kind;
static char s_view_path[PATH_LEN];

/* ----- path + listing ---------------------------------------------------- */

static void full_path(const char *name, char *out)
{
    if (strcmp(s_cwd, "/") == 0) snprintf(out, PATH_LEN, "/%s", name);
    else                         snprintf(out, PATH_LEN, "%s/%s", s_cwd, name);
}

static void add_entry(const char *name, int is_dir)
{
    if (s_nent >= MAX_ENTS) return;
    entry_t *e = &s_ent[s_nent];
    snprintf(e->name, NAME_LEN, "%s", name);
    e->is_dir = (uint8_t)is_dir;
    snprintf(s_disp[s_nent], NAME_LEN + 2, "%s%s", name, is_dir ? "/" : "");
    s_disp_ptr[s_nent] = s_disp[s_nent];
    s_nent++;
}

static void scan_dir(void)
{
    s_nent = 0;
    if (strcmp(s_cwd, "/") == 0) {
        add_entry("flash", 1);
        add_entry("sd", 1);
    } else {
        DIR *d = opendir(s_cwd);
        if (d) {
            struct dirent *e;
            while (s_nent < MAX_ENTS && (e = readdir(d)) != NULL) {
                if (e->d_name[0] == '\0' ||
                    strcmp(e->d_name, ".") == 0 || strcmp(e->d_name, "..") == 0)
                    continue;
                char p[PATH_LEN];
                full_path(e->d_name, p);
                struct stat st;
                int is_dir = (stat(p, &st) == 0 && S_ISDIR(st.st_mode));
                add_entry(e->d_name, is_dir);
            }
            closedir(d);
        }
    }
    ui_list_set_items(&s_list, s_disp_ptr, s_nent);
}

static void go_into(const char *name)
{
    if (strcmp(s_cwd, "/") == 0) {
        char tmp[PATH_LEN];
        snprintf(tmp, sizeof(tmp), "/%s", name);
        snprintf(s_cwd, sizeof(s_cwd), "%s", tmp);
    } else {
        size_t l = strlen(s_cwd);
        snprintf(s_cwd + l, sizeof(s_cwd) - l, "/%s", name);
    }
}

static void go_parent(void)
{
    char *slash = strrchr(s_cwd, '/');
    if (!slash || slash == s_cwd) { s_cwd[0] = '/'; s_cwd[1] = '\0'; return; }
    *slash = '\0';
}

/* ----- type detection ---------------------------------------------------- */

static int has_dr_ext(const char *n)
{
    int L = (int)strlen(n);
    return L >= 3 && n[L-3] == '.' && (n[L-2]|0x20) == 'd' && (n[L-1]|0x20) == 'r';
}

static int classify(const char *name, const unsigned char *b, int n)
{
    if (has_dr_ext(name)) return K_IMAGE;
    int printable = 0;
    for (int i = 0; i < n; i++) {
        unsigned char c = b[i];
        if (c == '\n' || c == '\r' || c == '\t' || (c >= 0x20 && c < 0x7f)) printable++;
    }
    return (n > 0 && printable * 100 / n >= 90) ? K_TEXT : K_HEX;
}

static int peek_file(const char *path)
{
    int fd = open(path, O_RDONLY);
    if (fd < 0) { s_buflen = 0; return -1; }
    int n = read(fd, s_buf, FILE_CAP);
    close(fd);
    s_buflen = (n > 0) ? n : 0;
    return s_buflen;
}

/* ----- browse view ------------------------------------------------------- */

static void draw_preview(void)
{
    int px = s_lw + 4, py = s_bar_h + 2;
    uint16_t bg = ui_theme(ui)->bg, fg = ui_theme(ui)->fg;
    gfx_rect(g, s_lw + 2, s_bar_h, s_sw - s_lw - 2, s_sh - 2 * s_bar_h, bg);

    if (s_nent == 0) { gfx_text(g, px, py, "(empty)", C_DIM, bg); return; }

    entry_t *e = &s_ent[s_list.sel];
    char path[PATH_LEN];
    full_path(e->name, path);
    char lbl[40];

    if (e->is_dir) {
        gfx_text(g, px, py, "<DIR>", C_DIR, bg);
        int cnt = 0;
        DIR *d = opendir(path);
        if (d) {
            struct dirent *de;
            while ((de = readdir(d)) != NULL) {
                if (de->d_name[0] != '.') cnt++;
            }
            closedir(d);
        }
        snprintf(lbl, sizeof(lbl), "%d items", cnt);
        gfx_text(g, px, py + 12, lbl, fg, bg);
        gfx_text(g, px, py + 26, "Enter: open", C_DIM, bg);
        return;
    }

    if (peek_file(path) <= 0 && !has_dr_ext(e->name)) {
        gfx_text(g, px, py, "(empty)", C_DIM, bg);
        return;
    }
    int kind = classify(e->name, s_buf, s_buflen);
    if (kind == K_IMAGE) {
        uint16_t iw, ih;
        if (duneos_image_info_dr(path, &iw, &ih) == 0)
            snprintf(lbl, sizeof(lbl), "image %ux%u", iw, ih);
        else
            snprintf(lbl, sizeof(lbl), "image");
        gfx_text(g, px, py, lbl, C_FILE, bg);
        gfx_text(g, px, py + 14, "Enter: view", C_DIM, bg);
    } else if (kind == K_TEXT) {
        ui_pager_init(&s_pager, px, py, s_sw - px - 2, s_sh - 2 * s_bar_h - 4);
        ui_pager_set(&s_pager, (const char *)s_buf, s_buflen);
        ui_pager_draw(ui, &s_pager);
    } else {
        snprintf(lbl, sizeof(lbl), "binary %d B", s_buflen);
        gfx_text(g, px, py, lbl, C_FILE, bg);
        gfx_text(g, px, py + 14, "Enter: hex", C_DIM, bg);
    }
}

static void draw_browse(void)
{
    ui_clear(ui);
    ui_titlebar(ui, s_cwd);
    gfx_rect(g, s_lw, s_bar_h, 1, s_sh - 2 * s_bar_h, ui_theme(ui)->border);
    ui_list_draw(ui, &s_list);
    draw_preview();
    ui_statusbar(ui, "Enter=open  Left=up  Esc=quit");
    ui_flush(ui);
}

/* ----- full-screen viewer (libui widgets) -------------------------------- */

static void draw_view(void)
{
    ui_clear(ui);
    char *base = strrchr(s_view_path, '/');
    ui_titlebar(ui, base ? base + 1 : s_view_path);

    if (s_view_kind == K_IMAGE) {
        uint16_t iw, ih;
        if (duneos_image_info_dr(s_view_path, &iw, &ih) == 0) {
            int x = ((int)s_sw - (int)iw) / 2;
            int y = s_bar_h + ((s_sh - 2 * s_bar_h) - (int)ih) / 2;
            if (x < 0) x = 0;
            if (y < s_bar_h) y = s_bar_h;
            duneos_image_blit_dr(g, x, y, s_view_path);
        }
        ui_statusbar(ui, "Esc=back");
    } else if (s_view_kind == K_TEXT) {
        ui_pager_draw(ui, &s_pager);
        ui_statusbar(ui, "Up/Down=scroll  Esc=back");
    } else {
        ui_hexview_draw(ui, &s_hex);
        ui_statusbar(ui, "Up/Down=scroll  Esc=back");
    }
    ui_flush(ui);
}

static void open_file(entry_t *e)
{
    full_path(e->name, s_view_path);
    if (peek_file(s_view_path) <= 0 && !has_dr_ext(e->name)) return;
    s_view_kind = classify(e->name, s_buf, s_buflen);

    int vx = 2, vy = s_bar_h + 2, vw = s_sw - 4, vh = s_sh - 2 * s_bar_h - 2;
    if (s_view_kind == K_TEXT) {
        ui_pager_init(&s_pager, vx, vy, vw, vh);
        ui_pager_set(&s_pager, (const char *)s_buf, s_buflen);
    } else if (s_view_kind == K_HEX) {
        ui_hexview_init(&s_hex, vx, vy, vw, vh);
        ui_hexview_set(&s_hex, s_buf, s_buflen);
    }
    s_mode = MODE_VIEW;
    draw_view();
}

/* ----- main -------------------------------------------------------------- */

void app_main(void)
{
    g = gfx_open_mode(GFX_MODE_STREAM);
    if (!g) duneos_exit(10);
    s_input = open("/dev/input/event0", O_RDONLY);
    if (s_input < 0) { gfx_close(g); duneos_exit(11); }
    ui = ui_create(g);
    if (!ui) { close(s_input); gfx_close(g); duneos_exit(10); }

    ui_size(ui, &s_sw, &s_sh);
    s_bar_h = 8 + 2 * ui_theme(ui)->pad;
    s_lw    = s_sw * 44 / 100;

    ui_list_init(&s_list, 0, s_bar_h, s_lw, s_sh - 2 * s_bar_h);
    scan_dir();
    draw_browse();

    for (;;) {
        input_event_t ev;
        if (read(s_input, &ev, sizeof(ev)) != (int)sizeof(ev)) continue;
        if (ev.type != INPUT_EV_KEY || ev.value == INPUT_VAL_RELEASE) continue;
        uint16_t k = ev.code;

        if (s_mode == MODE_VIEW) {
            if (k == KEY_ESC || k == KEY_LEFT) { s_mode = MODE_BROWSE; draw_browse(); }
            else if (s_view_kind == K_TEXT) { if (ui_pager_key(&s_pager, k))  draw_view(); }
            else if (s_view_kind == K_HEX)  { if (ui_hexview_key(&s_hex, k))  draw_view(); }
            continue;
        }

        switch (k) {
        case KEY_UP:
        case KEY_DOWN:
            if (ui_list_key(&s_list, k) == UI_LIST_MOVED) {
                ui_list_draw(ui, &s_list);
                draw_preview();
                ui_flush(ui);
            }
            break;
        case KEY_ENTER:
        case KEY_RIGHT:
            if (s_nent > 0) {
                entry_t *e = &s_ent[s_list.sel];
                if (e->is_dir) { go_into(e->name); scan_dir(); draw_browse(); }
                else if (k == KEY_ENTER) open_file(e);
            }
            break;
        case KEY_LEFT:
            if (strcmp(s_cwd, "/") != 0) { go_parent(); scan_dir(); draw_browse(); }
            break;
        case KEY_ESC:
            goto done;
        }
    }

done:
    ui_destroy(ui);
    close(s_input);
    gfx_close(g);
    duneos_exit(0);
}
