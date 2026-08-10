/*
 * ui_statusbar_top — a system status bar drawn at the top of the screen.
 *
 * Shows ambient state published by other components (ADR 027): battery, WiFi,
 * modifier locks, and the wall clock, plus an optional app title on the left.
 * It reads each signal from its well-known tmpfs path and renders only the
 * slots whose producer is present — no WiFi glyph when WiFi was never started,
 * no clock until the realtime clock is set. Nothing here owns or polls a
 * device; the producers do.
 *
 * Drawn opt-in by the foreground app (launcher / g_shell / tools), replacing
 * ui_titlebar. Games never call it, so they keep the whole panel. Uses only the
 * public libui + libgfx API, so it links like any other widget source.
 */

#include "duneos/ui.h"
#include "duneos/ambient.h"
#include "duneos/battery_ioctl.h"

#include <fcntl.h>
#include <unistd.h>
#include <string.h>
#include <stdio.h>
#include <time.h>

static int read_blob(const char *path, void *buf, size_t sz)
{
    int fd = open(path, O_RDONLY);
    if (fd < 0) return -1;
    int n = (int)read(fd, buf, sz);
    close(fd);
    return (n == (int)sz) ? 0 : -1;
}

/* RSSI dBm → 0..4 signal level. */
static int rssi_level(int dbm)
{
    if (dbm >= -55) return 4;
    if (dbm >= -65) return 3;
    if (dbm >= -75) return 2;
    if (dbm >= -85) return 1;
    return 0;
}

#define WIFI_W  (4 * 3)    /* 4 bars, 2px wide + 1px gap */

static void draw_wifi(gfx_ctx_t *g, int x, int y, int level, uint16_t on, uint16_t off)
{
    for (int i = 0; i < 4; i++) {
        int h = 2 + i * 2;                         /* 2,4,6,8 px ascending bars */
        gfx_rect(g, x + i * 3, y + 8 - h, 2, h, i < level ? on : off);
    }
}

#define BATT_W  (14 + 2)   /* body + terminal nub */

static void draw_batt(gfx_ctx_t *g, int x, int y, int pct, int charging,
                      uint16_t outline, uint16_t bg, uint16_t fg,
                      uint16_t low, uint16_t chg)
{
    int bw = 14, bh = 8;
    gfx_rect(g, x, y, bw, bh, outline);
    gfx_rect(g, x + 1, y + 1, bw - 2, bh - 2, bg);
    gfx_rect(g, x + bw, y + 2, 2, bh - 4, outline);    /* nub */
    if (pct < 0) pct = 0;
    if (pct > 100) pct = 100;
    int fw = (bw - 4) * pct / 100;
    uint16_t col = charging ? chg : (pct <= 20 ? low : fg);
    if (fw > 0) gfx_rect(g, x + 2, y + 2, fw, bh - 4, col);
}

/* Wall-clock HH:MM, UTC, only if the realtime clock looks set (epoch past late
 * 2023 — otherwise it's counting from boot at 1970 and meaningless). No tz
 * database in-app; a tz offset from config can come later. Returns 1 if filled. */
static int clock_hhmm(char out[6])
{
    struct timespec ts;
    if (clock_gettime(CLOCK_REALTIME, &ts) != 0) return 0;
    if (ts.tv_sec < 1700000000) return 0;
    uint32_t day = (uint32_t)(ts.tv_sec % 86400u);
    snprintf(out, 6, "%02u:%02u", (unsigned)(day / 3600u), (unsigned)((day % 3600u) / 60u));
    return 1;
}

void ui_statusbar_top(ui_t *ui, const char *title)
{
    if (!ui) return;
    gfx_ctx_t *g = ui_gfx(ui);
    const ui_theme_t *th = ui_theme(ui);
    int W   = ui_screen_w(ui);
    int pad = th->pad;
    int y   = pad;

    gfx_rect(g, 0, 0, W, pad * 2 + 8, th->bar_bg);

    int x = W - pad;   /* right cursor, items placed right-to-left */

    char clk[6];
    if (clock_hhmm(clk)) {
        x -= 5 * 8;
        gfx_text(g, x, y, clk, th->bar_fg, th->bar_bg);
        x -= pad;
    }

    battery_info_t b;
    if (read_blob(AMBIENT_BATTERY_PATH, &b, sizeof(b)) == 0) {
        char pc[5];
        snprintf(pc, sizeof(pc), "%u%%", (unsigned)b.percent);
        x -= (int)strlen(pc) * 8;
        gfx_text(g, x, y, pc, th->bar_fg, th->bar_bg);
        x -= 2 + BATT_W;
        draw_batt(g, x, y, b.percent, b.status != BATTERY_DISCHARGING,
                  th->bar_fg, th->bar_bg, th->bar_fg,
                  GFX_RGB(235, 90, 90), GFX_RGB(90, 210, 110));
        x -= pad;
    }

    ambient_wifi_t wf;
    if (read_blob(AMBIENT_WIFI_PATH, &wf, sizeof(wf)) == 0 && wf.state != AMBIENT_WIFI_DOWN) {
        int lvl = (wf.state == AMBIENT_WIFI_UP) ? rssi_level(wf.rssi_dbm) : 0;
        x -= WIFI_W;
        draw_wifi(g, x, y, lvl, th->bar_fg, th->border);
        x -= pad;
    }

    ambient_kbd_t kb;
    if (read_blob(AMBIENT_KBD_PATH, &kb, sizeof(kb)) == 0 && kb.locks) {
        static const struct { uint8_t bit; const char *tag; } mods[] = {
            { AMBIENT_MOD_ALT,   "ALT" },
            { AMBIENT_MOD_CTRL,  "CTL" },
            { AMBIENT_MOD_SHIFT, "SFT" },
            { AMBIENT_MOD_FN,    "FN"  },
        };
        /* Green = sticky (toggle, stays until re-tapped); amber = one-shot
         * (armed for the next key only). */
        const uint16_t col_toggle  = GFX_RGB(80, 220, 120);
        const uint16_t col_oneshot = GFX_RGB(240, 170, 40);
        for (unsigned i = 0; i < sizeof(mods) / sizeof(mods[0]); i++) {
            if (!(kb.locks & mods[i].bit)) continue;
            uint16_t fg = (kb.oneshot & mods[i].bit) ? col_oneshot : col_toggle;
            x -= (int)strlen(mods[i].tag) * 8;
            gfx_text(g, x, y, mods[i].tag, fg, th->bar_bg);
            x -= pad;
        }
    }

    if (title && title[0] && x > pad)
        ui_label(ui, pad, y, x - pad, title, UI_ALIGN_LEFT, th->bar_fg, th->bar_bg);
}

/* Poll every ambient input the bar renders and repaint only when one changed.
 * Call from the app's idle tick (select() timeout). One-shot modifiers arm on
 * the physical key RELEASE and emit no input event, so event-driven repaints
 * alone can never track the indicator — polling the blobs is the only way.
 * Static snapshot: one bar per app, the widget is compiled into each app. */
int ui_statusbar_top_poll(ui_t *ui, const char *title)
{
    struct snap {
        ambient_kbd_t  kb;
        ambient_wifi_t wf;
        uint8_t        batt_pc, batt_st;
        char           clk[6];
    };
    static struct snap last;
    static int have_last;

    struct snap cur;
    memset(&cur, 0, sizeof(cur));
    read_blob(AMBIENT_KBD_PATH, &cur.kb, sizeof(cur.kb));
    read_blob(AMBIENT_WIFI_PATH, &cur.wf, sizeof(cur.wf));
    battery_info_t b;
    if (read_blob(AMBIENT_BATTERY_PATH, &b, sizeof(b)) == 0) {
        cur.batt_pc = b.percent;
        cur.batt_st = (uint8_t)b.status;
    }
    clock_hhmm(cur.clk);

    if (have_last && memcmp(&cur, &last, sizeof(cur)) == 0) return 0;
    last = cur;
    have_last = 1;
    ui_statusbar_top(ui, title);
    return 1;
}
