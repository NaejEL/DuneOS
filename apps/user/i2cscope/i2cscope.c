/*
 * i2cscope — I2C Swiss-army knife (contest 2026 forensic centrepiece).
 *
 * Multi-screen app, one file per tool (reference layout for DuneOS apps):
 *   scan.c  — probe the bus, list ACKing addresses, drill in to hexdump regs.
 *   xfer.c  — write arbitrary bytes to an address and read the response back,
 *             in one combined transaction; the primitive SMBus composes from.
 *   sniff.c — passively capture SCL/SDA via /dev/logic0, decode I2C on-device,
 *             and save the raw capture as a PulseView-openable VCD on /sd.
 *
 * This file owns app_main, the top-level menu, the shared hardware/UI context
 * (the g_* globals declared in i2cscope.h), and the event dispatch. Each screen
 * module exposes <s>_open()/<s>_event(); dbt compiles every *.c in the app dir
 * automatically. Bus access is the Linux-style /dev/i2c-0 interface (i2c_msg +
 * I2C_RDWR) plus the SDK libsmbus for register reads.
 */

#include "i2cscope.h"

extern void duneos_exit(int code);

#define DEFAULT_SCL_PIN 1        /* fallback if board.info has no i2c pins */
#define DEFAULT_SDA_PIN 2

/* Shared context (declared extern in i2cscope.h). */
gfx_ctx_t *g_gfx;
ui_t      *g_ui;
int        g_input = -1;
int        g_i2c   = -1;
uint16_t   g_sw, g_sh;
int        g_bar_h;
int        g_scl_pin = DEFAULT_SCL_PIN;
int        g_sda_pin = DEFAULT_SDA_PIN;

enum { SCR_MENU, SCR_SCAN, SCR_XFER, SCR_SNIFF, SCR_SCENARIO };
static int s_scr;

static ui_list_t s_menu;
static const char *const k_menu[] = { "Scan bus", "Xfer bus", "Sniff bus", "Scenarios" };
#define N_MENU 4

/* Read an integer "<key>: <n>" value from /flash/board.info, or `def`. Keeps
 * the app board-agnostic — the same .dap reads each board's pins at runtime. */
static int board_info_int(const char *key, int def)
{
    int fd = open("/flash/board.info", O_RDONLY);
    if (fd < 0) return def;
    char buf[768];
    int n = read(fd, buf, sizeof(buf) - 1);
    close(fd);
    if (n <= 0) return def;
    buf[n] = '\0';

    char pat[24];
    snprintf(pat, sizeof(pat), "%s:", key);
    char *p = strstr(buf, pat);
    if (!p) return def;
    p += strlen(pat);
    while (*p == ' ') p++;
    int neg = (*p == '-'); if (neg) p++;
    if (*p < '0' || *p > '9') return def;
    int v = 0;
    while (*p >= '0' && *p <= '9') v = v * 10 + (*p++ - '0');
    return neg ? -v : v;
}

static void menu_draw(void)
{
    ui_clear(g_ui);
    ui_titlebar(g_ui, "i2cscope");
    ui_list_draw(g_ui, &s_menu);
    ui_statusbar(g_ui, "Enter=open  Esc=quit");
    ui_flush(g_ui);
}

void app_main(void)
{
    g_gfx = gfx_open_mode(GFX_MODE_STREAM);
    if (!g_gfx) duneos_exit(10);
    g_input = open("/dev/input/event0", O_RDONLY);
    if (g_input < 0) { gfx_close(g_gfx); duneos_exit(11); }
    g_i2c = open("/dev/i2c-0", O_RDWR);
    if (g_i2c < 0) { close(g_input); gfx_close(g_gfx); duneos_exit(12); }

    g_ui = ui_create(g_gfx);
    if (!g_ui) { close(g_i2c); close(g_input); gfx_close(g_gfx); duneos_exit(10); }

    ui_size(g_ui, &g_sw, &g_sh);
    g_bar_h = 8 + 2 * ui_theme(g_ui)->pad;

    g_scl_pin = board_info_int("i2c0_scl", DEFAULT_SCL_PIN);
    g_sda_pin = board_info_int("i2c0_sda", DEFAULT_SDA_PIN);

    ui_list_init(&s_menu, 0, g_bar_h, g_sw, g_sh - 2 * g_bar_h);
    ui_list_set_items(&s_menu, k_menu, N_MENU);

    s_scr = SCR_MENU;
    menu_draw();

    for (;;) {
        input_event_t ev;
        if (read(g_input, &ev, sizeof(ev)) != (int)sizeof(ev)) continue;
        if (ev.type != INPUT_EV_KEY || ev.value == INPUT_VAL_RELEASE) continue;
        uint16_t k = ev.code;

        if (s_scr == SCR_MENU) {
            switch (ui_list_key(&s_menu, k)) {
            case UI_LIST_MOVED: ui_list_draw(g_ui, &s_menu); ui_flush(g_ui); break;
            case UI_LIST_SELECT:
                switch (s_menu.sel) {
                case 0: s_scr = SCR_SCAN;     scan_open();     break;
                case 1: s_scr = SCR_XFER;     xfer_open();     break;
                case 2: s_scr = SCR_SNIFF;    sniff_open();    break;
                case 3: s_scr = SCR_SCENARIO; scenario_open(); break;
                }
                break;
            case UI_LIST_CANCEL: goto done;
            case UI_LIST_NONE: break;
            }
            continue;
        }

        int pop = 0;
        switch (s_scr) {
        case SCR_SCAN:     pop = scan_event(k);     break;
        case SCR_XFER:     pop = xfer_event(k);     break;
        case SCR_SNIFF:    pop = sniff_event(k);    break;
        case SCR_SCENARIO: pop = scenario_event(k); break;
        }
        if (pop) { s_scr = SCR_MENU; menu_draw(); }
    }

done:
    ui_destroy(g_ui);
    close(g_i2c);
    close(g_input);
    gfx_close(g_gfx);
    duneos_exit(0);
}
