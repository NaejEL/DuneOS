/*
 * sniff.c — passive I2C bus sniffer screen.
 *
 * Captures SCL/SDA synchronously via /dev/logic0 (ADR 020) — fixed-cadence
 * sampling, not per-edge interrupts, so nothing is dropped at clock rates.
 * Decodes the I2C protocol on-device (colour-coded) and saves the raw capture
 * as an indexed VCD on /sd that PulseView opens directly.
 */

#include "i2cscope.h"
#include "duneos/logic_ioctl.h"

#include <sys/select.h>
#include <sys/time.h>

#define MAX_EDGES 768        /* captured transitions (8 B each) */

/* logic0 channel assignment: bit 0 = SCL, bit 1 = SDA. */
#define M_SCL  (1u << 0)
#define M_SDA  (1u << 1)

static logic_sample_t s_edges[MAX_EDGES];
static int            s_nedges;
static char           s_vcd_path[40];

/* Decoded transaction tokens. */
enum { TOK_START, TOK_ADDR, TOK_DATA, TOK_STOP };
typedef struct { uint8_t type, val, rw, ack; } tok_t;
static tok_t s_tok[384];
static int   s_ntok;

/* Pick /sd/i2csniff-NNN.vcd with the lowest free index (so captures accumulate
 * instead of overwriting one fixed file). */
static void vcd_next_path(char *out, int outsz)
{
    for (int i = 0; i < 1000; i++) {
        snprintf(out, outsz, "/sd/i2csniff-%03d.vcd", i);
        int fd = open(out, O_RDONLY);
        if (fd < 0) return;          /* free index */
        close(fd);
    }
    snprintf(out, outsz, "/sd/i2csniff.vcd");
}

/* Non-blocking Esc check (drains one input event). Used between capture windows
 * so a sniff is abortable without an active select() during sampling. */
static int esc_pressed(void)
{
    fd_set rfds;
    FD_ZERO(&rfds);
    FD_SET(g_input, &rfds);
    struct timeval tv = { 0, 0 };
    if (select(g_input + 1, &rfds, NULL, NULL, &tv) > 0 && FD_ISSET(g_input, &rfds)) {
        input_event_t ev;
        if (read(g_input, &ev, sizeof(ev)) == (int)sizeof(ev) &&
            ev.type == INPUT_EV_KEY && ev.value != INPUT_VAL_RELEASE &&
            ev.code == KEY_ESC)
            return 1;
    }
    return 0;
}

/* Each read() of /dev/logic0 is one capture window: it waits (bounded) for bus
 * activity then bursts with interrupts off. We stitch windows into one timeline
 * and stop when the buffer fills or the user presses Esc. */
static void capture(void)
{
    int lg = open("/dev/logic0", O_RDONLY);
    if (lg < 0) return;

    logic_config_t cfg = {
        .channel_gpio       = { (uint8_t)g_scl_pin, (uint8_t)g_sda_pin },
        .n_channels         = 2,
        .idle_us            = 3000,    /* end window 3 ms after the last edge   */
        .hard_cap_us        = 40000,   /* burst ceiling (< interrupt watchdog)  */
        .trigger_timeout_us = 150000,  /* Esc latency between windows           */
    };
    if (ioctl(lg, LOGIC_SET_CONFIG, &cfg) != 0) { close(lg); return; }

    s_nedges = 0;
    uint32_t t_off = 0;     /* running offset so windows form one timeline */
    while (s_nedges < MAX_EDGES) {
        if (esc_pressed()) break;

        int room = MAX_EDGES - s_nedges;
        int n = read(lg, &s_edges[s_nedges], (size_t)room * sizeof(logic_sample_t));
        if (n <= 0) continue;          /* 0 = idle window, loop & recheck Esc */

        int cnt = n / (int)sizeof(logic_sample_t);
        for (int i = 0; i < cnt; i++) s_edges[s_nedges + i].t_us += t_off;
        t_off = s_edges[s_nedges + cnt - 1].t_us + 1000;   /* gap between windows */
        s_nedges += cnt;
    }

    close(lg);
}

/* Decode the captured transition stream into tokens. Edge-based: SDA is sampled
 * on the actual SCL rising edge, so clock stretching (slave holding SCL low) is
 * handled implicitly — there's simply no rising edge until the line is released. */
static void decode(void)
{
    s_ntok = 0;
    int scl = 1, sda = 1, active = 0, bits = 0, byte = 0, idx = 0;
    int cap = (int)(sizeof(s_tok) / sizeof(s_tok[0]));

    for (int i = 0; i < s_nedges && s_ntok < cap; i++) {
        uint32_t lv   = s_edges[i].levels;
        int      nscl = (lv & M_SCL) ? 1 : 0;
        int      nsda = (lv & M_SDA) ? 1 : 0;
        int      pscl = scl, psda = sda;

        /* SDA moving while SCL stays high → START / STOP. */
        if (pscl == 1 && nscl == 1 && nsda != psda) {
            if (psda == 1 && nsda == 0) {                /* START */
                s_tok[s_ntok++] = (tok_t){ TOK_START, 0, 0, 0 };
                active = 1; bits = 0; byte = 0; idx = 0;
            } else {                                     /* STOP */
                s_tok[s_ntok++] = (tok_t){ TOK_STOP, 0, 0, 0 };
                active = 0;
            }
        }

        /* SCL rising edge → clock a bit (sample SDA). */
        if (pscl == 0 && nscl == 1 && active) {
            if (bits < 8) { byte = (byte << 1) | (nsda & 1); bits++; }
            else {                                       /* 9th clock = ACK */
                tok_t t = { (uint8_t)(idx == 0 ? TOK_ADDR : TOK_DATA),
                            0, 0, (uint8_t)(nsda == 0) };
                if (idx == 0) { t.val = (uint8_t)(byte >> 1); t.rw = (uint8_t)(byte & 1); }
                else          { t.val = (uint8_t)byte; }
                s_tok[s_ntok++] = t;
                bits = 0; byte = 0; idx++;
            }
        }

        scl = nscl;
        sda = nsda;
    }
}

/* Flow-render the decoded transactions with a colour legend on top. Markers in
 * cyan (S/P), address in blue with /W or /R, data in white, and each byte's
 * ACK/NACK as a green '+' or red '-'. One transaction per row (wraps if long). */
static void render(void)
{
    const int legend_h = 10;
    const int ytop = g_bar_h, ybot = g_sh - (8 + 2 * ui_theme(g_ui)->pad), rh = 10;
    uint16_t bg = ui_theme(g_ui)->bg;
    gfx_rect(g_gfx, 0, ytop, g_sw, ybot - ytop, bg);

    if (s_ntok == 0) {
        ui_label(g_ui, 0, (ytop + ybot) / 2 - 4, g_sw, "no transactions decoded",
                 UI_ALIGN_CENTER, ui_theme(g_ui)->fg, bg);
        return;
    }

    static const ui_span_t legend[] = {
        { "addr ", C_ADDR }, { "data ", C_DATA },
        { "+ack ", C_ACK },  { "-nak", C_NACK },
    };
    ui_spans(g_ui, 0, ytop, g_sw, legend, 4, bg);

    int cx = 0, cy = ytop + legend_h;
    char buf[8];
    #define EMIT(str, col) do {                                          \
        const char *_s = (str); int _w = (int)strlen(_s) * 8;            \
        if (cx + _w > g_sw) { cx = 0; cy += rh; }                        \
        if (cy + 8 <= ybot) { gfx_text(g_gfx, cx, cy, _s, (col), bg); }  \
        cx += _w;                                                        \
    } while (0)

    for (int i = 0; i < s_ntok && cy + 8 <= ybot; i++) {
        tok_t    *t   = &s_tok[i];
        uint16_t  ack = t->ack ? C_ACK : C_NACK;
        switch (t->type) {
        case TOK_START:
            if (cx > 0) { cx = 0; cy += rh; }
            EMIT("S ", C_MARK);
            break;
        case TOK_ADDR:
            snprintf(buf, sizeof(buf), "%02X", t->val); EMIT(buf, C_ADDR);
            EMIT(t->rw ? "/R" : "/W", C_ADDR);
            EMIT(t->ack ? "+ " : "- ", ack);
            break;
        case TOK_DATA:
            snprintf(buf, sizeof(buf), "%02X", t->val); EMIT(buf, C_DATA);
            EMIT(t->ack ? "+ " : "- ", ack);
            break;
        case TOK_STOP:
            EMIT("P", C_MARK);
            cx = 0; cy += rh;
            break;
        }
    }
    #undef EMIT
}

/* Write the raw capture as a VCD that PulseView opens (2 logic channels). */
static int save_vcd(void)
{
    vcd_next_path(s_vcd_path, sizeof(s_vcd_path));
    int fd = open(s_vcd_path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) return -1;

    static const char hdr[] =
        "$timescale 1 us $end\n"
        "$var wire 1 ! SCL $end\n"
        "$var wire 1 \" SDA $end\n"
        "$enddefinitions $end\n"
        "#0\n1!\n1\"\n";
    write(fd, hdr, sizeof(hdr) - 1);

    /* Synchronous transition records → one VCD value-change per line that moved.
     * A record may carry only the other line's change, and window boundaries
     * repeat a state — so track last levels and skip non-changes. */
    int last_scl = 1, last_sda = 1;
    uint32_t t0 = s_nedges ? s_edges[0].t_us : 0;
    char buf[24];
    for (int i = 0; i < s_nedges; i++) {
        uint32_t lv   = s_edges[i].levels;
        int      nscl = (lv & M_SCL) ? 1 : 0;
        int      nsda = (lv & M_SDA) ? 1 : 0;
        if (nscl == last_scl && nsda == last_sda) continue;

        int n = snprintf(buf, sizeof(buf), "#%lu\n", (unsigned long)(s_edges[i].t_us - t0));
        write(fd, buf, n);
        if (nscl != last_scl) { n = snprintf(buf, sizeof(buf), "%d!\n", nscl); write(fd, buf, n); last_scl = nscl; }
        if (nsda != last_sda) { n = snprintf(buf, sizeof(buf), "%d\"\n", nsda); write(fd, buf, n); last_sda = nsda; }
    }
    close(fd);
    return 0;
}

static void draw(void)
{
    ui_clear(g_ui);
    char t[40];
    snprintf(t, sizeof(t), "sniff  SCL=%d SDA=%d", g_scl_pin, g_sda_pin);
    ui_titlebar(g_ui, t);
    if (s_nedges == 0)
        ui_label(g_ui, 0, g_sh / 2 - 4, g_sw, "Enter = capture",
                 UI_ALIGN_CENTER, ui_theme(g_ui)->fg, ui_theme(g_ui)->bg);
    else
        render();
    char st[44];
    snprintf(st, sizeof(st), "%d edges  Ent=cap S=save Esc", s_nedges);
    ui_statusbar(g_ui, st);
    ui_flush(g_ui);
}

void sniff_open(void)
{
    s_nedges = 0;
    draw();
}

int sniff_event(uint16_t k)
{
    if (k == KEY_ENTER) {
        ui_clear(g_ui);
        char t[40];
        snprintf(t, sizeof(t), "sniff  SCL=%d SDA=%d", g_scl_pin, g_sda_pin);
        ui_titlebar(g_ui, t);
        ui_label(g_ui, 0, g_sh / 2 - 4, g_sw, "Capturing...  Esc=stop",
                 UI_ALIGN_CENTER, ui_theme(g_ui)->fg, ui_theme(g_ui)->bg);
        ui_flush(g_ui);
        capture();
        decode();
        draw();
    } else if (k == 's' || k == 'S') {
        int ok = (s_nedges > 0) ? save_vcd() : -1;
        draw();
        char msg[48];
        if (ok == 0) snprintf(msg, sizeof(msg), "saved %s", s_vcd_path);
        else         snprintf(msg, sizeof(msg), "nothing to save");
        ui_statusbar(g_ui, msg);
        ui_flush(g_ui);
    } else if (k == KEY_ESC) {
        /* Sniffing repurposed SCL/SDA as GPIO — restore the I2C pin mux so
         * Scan/Xfer work again without a reboot. */
        ioctl(g_i2c, I2C_RESET, 0);
        return 1;
    }
    return 0;
}
