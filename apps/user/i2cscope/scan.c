/*
 * scan.c — bus scan + register hexdump screen.
 *
 * Two sub-states: a list of ACKing addresses, and a per-address register dump
 * driven by a "<reg> [count]" prompt. The register read goes through the SDK
 * libsmbus (smbus_read_i2c_block) — the same write-then-read an SMBus block read
 * composes from.
 */

#include "i2cscope.h"
#include "duneos/smbus.h"

#define MAX_ADDR   112
#define VIEW_LINES 18
#define LINE_W     34

enum { SUB_LIST, SUB_READ };
static int s_sub;

static ui_list_t     s_list;
static ui_textview_t s_tv;
static ui_input_t    s_in;
static char          s_viewbuf[VIEW_LINES][LINE_W];
static char          s_inbuf[24];

static uint8_t      s_addr[MAX_ADDR];
static char         s_addr_str[MAX_ADDR][6];
static const char  *s_addr_ptr[MAX_ADDR];
static int          s_naddr;
static uint8_t      s_sel_addr;

static int i2c_present(uint8_t addr)
{
    return smbus_write_quick(g_i2c, addr, 0) == 0;
}

static void scan_bus(void)
{
    s_naddr = 0;
    for (uint8_t a = 0x08; a <= 0x77 && s_naddr < MAX_ADDR; a++) {
        if (i2c_present(a)) {
            s_addr[s_naddr] = a;
            snprintf(s_addr_str[s_naddr], 6, "0x%02X", a);
            s_addr_ptr[s_naddr] = s_addr_str[s_naddr];
            s_naddr++;
        }
    }
    ui_list_set_items(&s_list, s_addr_ptr, s_naddr);
}

static int parse_read_cmd(const char *s, uint8_t *reg, int *count)
{
    while (*s == ' ') s++;
    if (*s == '0' && (s[1] == 'x' || s[1] == 'X')) s += 2;
    int r = 0, n = 0;
    for (; hexnib(*s) >= 0; s++) { r = r * 16 + hexnib(*s); n++; }
    if (n == 0) return -1;
    *reg = (uint8_t)r;
    while (*s == ' ') s++;
    int c = 0, cn = 0;
    for (; *s >= '0' && *s <= '9'; s++) { c = c * 10 + (*s - '0'); cn++; }
    if (cn == 0) c = 16;
    if (c < 1) c = 1;
    if (c > 128) c = 128;
    *count = c;
    return 0;
}

static void draw_list(void)
{
    ui_clear(g_ui);
    char t[40];
    snprintf(t, sizeof(t), "scan  %d found", s_naddr);
    ui_titlebar(g_ui, t);
    if (s_naddr == 0) ui_message(g_ui, "No I2C devices", "R=rescan  Esc=back");
    else              ui_list_draw(g_ui, &s_list);
    ui_statusbar(g_ui, "Enter=read  R=scan  Esc=back");
    ui_flush(g_ui);
}

static void draw_read(void)
{
    ui_clear(g_ui);
    char t[40];
    snprintf(t, sizeof(t), "read  0x%02X", s_sel_addr);
    ui_titlebar(g_ui, t);
    ui_textview_draw(g_ui, &s_tv);
    ui_input_draw(g_ui, &s_in);
    ui_flush(g_ui);
}

static void do_read(void)
{
    uint8_t reg; int count;
    if (parse_read_cmd(s_inbuf, &reg, &count) != 0) {
        ui_textview_push(&s_tv, "usage: <reg> [count]");
        return;
    }
    uint8_t data[128];
    if (smbus_read_i2c_block(g_i2c, s_sel_addr, reg, data, (uint8_t)count) < 0) {
        char e[LINE_W];
        snprintf(e, sizeof(e), "read 0x%02X: failed", reg);
        ui_textview_push(&s_tv, e);
        return;
    }
    for (int off = 0; off < count; off += 8) {
        char line[LINE_W];
        int pos = snprintf(line, sizeof(line), "%02X:", reg + off);
        for (int i = 0; i < 8 && off + i < count; i++)
            pos += snprintf(line + pos, sizeof(line) - pos, " %02X", data[off + i]);
        ui_textview_push(&s_tv, line);
    }
}

void scan_open(void)
{
    s_sub = SUB_LIST;
    ui_list_init(&s_list, 0, g_bar_h, g_sw, g_sh - 2 * g_bar_h);
    ui_textview_init(&s_tv, 0, g_bar_h, g_sw, g_sh - g_bar_h - 12,
                     &s_viewbuf[0][0], VIEW_LINES, LINE_W);
    ui_input_init(&s_in, 2, g_sh - 10, g_sw - 2, s_inbuf, sizeof(s_inbuf), "reg: ");
    scan_bus();
    draw_list();
}

int scan_event(uint16_t k)
{
    if (s_sub == SUB_LIST) {
        if (k == 'r' || k == 'R') { scan_bus(); draw_list(); return 0; }
        switch (ui_list_key(&s_list, k)) {
        case UI_LIST_MOVED: ui_list_draw(g_ui, &s_list); ui_flush(g_ui); break;
        case UI_LIST_SELECT:
            if (s_naddr > 0) {
                s_sel_addr = s_addr[s_list.sel];
                s_sub = SUB_READ;
                ui_textview_clear(&s_tv);
                ui_input_clear(&s_in);
                draw_read();
            }
            break;
        case UI_LIST_CANCEL: return 1;
        case UI_LIST_NONE: break;
        }
        return 0;
    }

    /* SUB_READ */
    switch (ui_input_key(&s_in, k)) {
    case UI_INPUT_CHANGED: ui_input_draw(g_ui, &s_in); ui_flush(g_ui); break;
    case UI_INPUT_SUBMIT:
        if (s_in.len > 0) { do_read(); ui_input_clear(&s_in); draw_read(); }
        break;
    case UI_INPUT_CANCEL:
        s_sub = SUB_LIST; draw_list();
        break;
    case UI_INPUT_NONE: break;
    }
    return 0;
}
