/*
 * xfer.c — arbitrary write-then-read screen (i2ctransfer-style).
 *
 * A one-line command "<addr> [byte ...] [rN]" runs a combined transaction
 * (write bytes, repeated-START, read N) via I2C_RDWR — the raw primitive that
 * sits below SMBus. Results are kept in a small colour-coded log.
 */

#include "i2cscope.h"

#define XF_MAX 16          /* max write/read bytes per xfer      */
#define XF_LOG 6           /* recent transactions kept on screen */

static ui_input_t s_in;
static char       s_inbuf[24];

typedef struct {
    uint8_t addr, wlen, rlen, ok;
    uint8_t w[XF_MAX], r[XF_MAX];
} xfer_entry_t;
static xfer_entry_t s_log[XF_LOG];
static int          s_n;

/* Parse "<addr> [byte ...] [rN]": addr first (hex), then any hex bytes to write,
 * and an optional rN (decimal) to read N bytes back. e.g. "6B 2A r2" writes 0x2A
 * to 0x6B then reads 2 bytes (combined, repeated-START). "24 r4" is a pure read,
 * "50 00 AA" a pure write. Returns 0 on success, -1 on malformed input. */
static int parse_cmd(const char *s, uint8_t *addr, uint8_t *wbuf, int *wlen, int *rlen)
{
    *wlen = 0;
    *rlen = 0;
    while (*s == ' ') s++;
    if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) s += 2;
    int a = 0, an = 0;
    for (; hexnib(*s) >= 0; s++) { a = a * 16 + hexnib(*s); an++; }
    if (an == 0) return -1;
    *addr = (uint8_t)a;

    for (;;) {
        while (*s == ' ') s++;
        if (*s == '\0') break;
        if (*s == 'r' || *s == 'R') {
            s++;
            int c = 0, cn = 0;
            for (; *s >= '0' && *s <= '9'; s++) { c = c * 10 + (*s - '0'); cn++; }
            if (cn == 0) c = 1;
            if (c > XF_MAX) c = XF_MAX;
            *rlen = c;
            continue;
        }
        if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) s += 2;
        int b = 0, bn = 0;
        for (; hexnib(*s) >= 0 && bn < 2; s++) { b = b * 16 + hexnib(*s); bn++; }
        if (bn == 0) return -1;
        if (*wlen < XF_MAX) wbuf[(*wlen)++] = (uint8_t)b;
    }
    return (*wlen == 0 && *rlen == 0) ? -1 : 0;
}

static void log_push(const xfer_entry_t *e)
{
    if (s_n < XF_LOG) {
        s_log[s_n++] = *e;
    } else {
        for (int i = 1; i < XF_LOG; i++) s_log[i - 1] = s_log[i];
        s_log[XF_LOG - 1] = *e;
    }
}

static void do_xfer(void)
{
    uint8_t addr, wbuf[XF_MAX];
    int wlen, rlen;
    if (parse_cmd(s_inbuf, &addr, wbuf, &wlen, &rlen) != 0) return;

    uint8_t rbuf[XF_MAX];
    struct i2c_msg m[2];
    int nm = 0;
    if (wlen > 0) m[nm++] = (struct i2c_msg){ .addr = addr, .flags = 0,
                                              .len = (uint16_t)wlen, .buf = wbuf };
    if (rlen > 0) m[nm++] = (struct i2c_msg){ .addr = addr, .flags = I2C_M_RD,
                                              .len = (uint16_t)rlen, .buf = rbuf };
    struct i2c_rdwr_ioctl_data x = { .msgs = m, .nmsgs = nm };
    int ok = (ioctl(g_i2c, I2C_RDWR, &x) == 0);

    xfer_entry_t e;
    memset(&e, 0, sizeof(e));
    e.addr = addr;
    e.wlen = (uint8_t)wlen;
    e.ok   = (uint8_t)ok;
    memcpy(e.w, wbuf, wlen);
    if (ok && rlen > 0) { e.rlen = (uint8_t)rlen; memcpy(e.r, rbuf, rlen); }
    log_push(&e);
}

/* Colour-coded transaction log: address (blue), bytes written (white), bytes
 * read back (amber), and a green '+' / red '-' status. Input line at the bottom. */
static void draw(void)
{
    ui_clear(g_ui);
    ui_titlebar(g_ui, "xfer");
    uint16_t bg = ui_theme(g_ui)->bg;
    int ytop = g_bar_h, ybot = g_sh - 12, rh = 10;

    static const ui_span_t legend[] = {
        { "addr ", C_ADDR }, { "wr ", C_DATA }, { "rd ", C_RESP }, { "ok", C_ACK },
    };
    ui_spans(g_ui, 0, ytop, g_sw, legend, 4, bg);

    int cx = 0, cy = ytop + rh;
    char hb[4];
    #define EMIT(str, col) do {                                          \
        const char *_s = (str); int _w = (int)strlen(_s) * 8;            \
        if (cx + _w > g_sw) { cx = 0; cy += rh; }                        \
        if (cy + 8 <= ybot) { gfx_text(g_gfx, cx, cy, _s, (col), bg); }  \
        cx += _w;                                                        \
    } while (0)

    for (int i = 0; i < s_n && cy + 8 <= ybot; i++) {
        xfer_entry_t *e = &s_log[i];
        cx = 0;
        snprintf(hb, sizeof(hb), "%02X", e->addr); EMIT(hb, C_ADDR); EMIT(" ", C_ADDR);
        if (e->wlen) {
            EMIT("W:", C_DATA);
            for (int j = 0; j < e->wlen; j++) { snprintf(hb, sizeof(hb), "%02X", e->w[j]); EMIT(hb, C_DATA); }
            EMIT(" ", C_DATA);
        }
        if (e->ok && e->rlen) {
            EMIT("R:", C_RESP);
            for (int j = 0; j < e->rlen; j++) { snprintf(hb, sizeof(hb), "%02X", e->r[j]); EMIT(hb, C_RESP); }
            EMIT(" ", C_RESP);
        }
        EMIT(e->ok ? "+" : "-", e->ok ? C_ACK : C_NACK);
        cy += rh;
    }
    #undef EMIT

    if (s_n == 0)
        ui_label(g_ui, 0, ytop + rh, g_sw, "ex: 6B 2A r2",
                 UI_ALIGN_CENTER, ui_theme(g_ui)->fg, bg);

    ui_input_draw(g_ui, &s_in);
    ui_flush(g_ui);
}

void xfer_open(void)
{
    s_n = 0;
    ui_input_init(&s_in, 2, g_sh - 10, g_sw - 2, s_inbuf, sizeof(s_inbuf), "tx: ");
    draw();
}

int xfer_event(uint16_t k)
{
    switch (ui_input_key(&s_in, k)) {
    case UI_INPUT_CHANGED: ui_input_draw(g_ui, &s_in); ui_flush(g_ui); break;
    case UI_INPUT_SUBMIT:
        if (s_in.len > 0) { do_xfer(); ui_input_clear(&s_in); draw(); }
        break;
    case UI_INPUT_CANCEL: return 1;
    case UI_INPUT_NONE: break;
    }
    return 0;
}
