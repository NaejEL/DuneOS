/*
 * radar — military-style PPI scope for the HLK-LD2450 mmWave sensor.
 *
 * Reads /dev/uart1 (Grove port, 256000 8N1) through sdk/sensor/libld2450 and
 * renders up to 3 moving targets on a 120-degree wedge: range arcs, bearing
 * spokes, an animated sweep with afterglow, per-target trails, and an info
 * panel (distance m, signed angle deg, speed km/h — PO decision Q7).
 *
 * Architecture (no busy-poll, no select() on the UART fd — the uart driver
 * has no `readable` callback so select() would spin):
 *   - a reader pthread blocks in ld2450_read() (the UART HAL read is bounded
 *     at ~100 ms) and publishes the latest decoded frame under a mutex;
 *   - the main loop is a cadenced select() on /dev/input/event0 (which IS
 *     event-driven) with a timeout to the next animation tick, libgame-style.
 * The scope wedge is composed each tick in a partial offscreen canvas and
 * presented in one blit — no full-screen clear, no flicker (Phase 24.10).
 *
 * Demo mode ('d', or automatic when /dev/uart1 is absent) synthesises wire
 * frames — garbage prefix included — and runs them through the same parser,
 * so the full decode/resync path stays exercised without hardware.
 */

#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdio.h>
#include <string.h>
#include <sys/select.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

#include "duneos/gfx.h"
#include "duneos/input_ioctl.h"
#include "duneos/ld2450.h"

extern void duneos_exit(int code);
extern int  dprintf(int fd, const char *fmt, ...);

#define RADAR_DEV        "/dev/uart1"
#define TICK_MS          40
#define DEMO_PERIOD_MS   100
#define WEDGE_HALF_DEG   60          /* LD2450 field of view is +/-60 deg */
#define RX_STALE_MS      1500
#define HIST_LEN         8

/* ----- phosphor palette --------------------------------------------------- */

#define C_BG        GFX_RGB(0, 10, 2)
#define C_PBG       GFX_RGB(6, 14, 6)
#define C_GRID      GFX_RGB(0, 110, 40)
#define C_GRID_DIM  GFX_RGB(0, 60, 22)
#define C_SWEEP     GFX_RGB(150, 255, 150)
#define C_TEXT      GFX_RGB(140, 230, 140)
#define C_DIM       GFX_RGB(60, 110, 60)
#define C_ALERT     GFX_RGB(255, 90, 60)

static const uint16_t k_tcol[LD2450_MAX_TARGETS] = {
    GFX_RGB(140, 255, 140),   /* T1 */
    GFX_RGB(120, 220, 255),   /* T2 */
    GFX_RGB(255, 230, 110),   /* T3 */
};

static const uint16_t k_trail_col[7] = {
    GFX_RGB(0, 176, 62), GFX_RGB(0, 148, 52), GFX_RGB(0, 120, 42),
    GFX_RGB(0,  94, 34), GFX_RGB(0,  70, 26), GFX_RGB(0,  50, 18),
    GFX_RGB(0,  34, 12),
};

static const uint16_t k_fade_col[4] = {
    GFX_RGB(0, 200, 70), GFX_RGB(0, 150, 52),
    GFX_RGB(0, 104, 36), GFX_RGB(0,  64, 22),
};

/* ----- integer trigonometry ---------------------------------------------- */

/* sin(d) * 1024, d = 0..90 */
static const int16_t k_sin1024[91] = {
       0,   18,   36,   54,   71,   89,  107,  125,  143,  160,  178,  195,  213,
     230,  248,  265,  282,  299,  316,  333,  350,  367,  384,  400,  416,  433,
     449,  465,  481,  496,  512,  527,  543,  558,  573,  587,  602,  616,  630,
     644,  658,  672,  685,  698,  711,  724,  737,  749,  761,  773,  784,  796,
     807,  818,  828,  839,  849,  859,  868,  878,  887,  896,  904,  912,  920,
     928,  935,  943,  949,  956,  962,  968,  974,  979,  984,  989,  994,  998,
    1002, 1005, 1008, 1011, 1014, 1016, 1018, 1020, 1022, 1023, 1023, 1024, 1024,
};

/* tan(d) * 256, d = 0..80 — bearing lookup for the info panel */
static const int16_t k_tan256[81] = {
       0,    4,    9,   13,   18,   22,   27,   31,   36,   41,   45,   50,
      54,   59,   64,   69,   73,   78,   83,   88,   93,   98,  103,  109,
     114,  119,  125,  130,  136,  142,  148,  154,  160,  166,  173,  179,
     186,  193,  200,  207,  215,  223,  231,  239,  247,  256,  265,  275,
     284,  294,  305,  316,  328,  340,  352,  366,  380,  394,  410,  426,
     443,  462,  481,  502,  525,  549,  575,  603,  634,  667,  703,  743,
     788,  837,  893,  955, 1027, 1109, 1204, 1317, 1452,
};

static int isin(int deg)
{
    deg %= 360;
    if (deg > 180)       deg -= 360;
    else if (deg < -180) deg += 360;
    int neg = deg < 0;
    if (neg) deg = -deg;
    if (deg > 90) deg = 180 - deg;
    int v = k_sin1024[deg];
    return neg ? -v : v;
}

static int icos(int deg)
{
    return isin(deg + 90);
}

static uint32_t isqrt32(uint32_t v)
{
    uint32_t r = 0, b = 1u << 30;
    while (b > v) b >>= 2;
    while (b) {
        if (v >= r + b) { v -= r + b; r = (r >> 1) + b; }
        else            { r >>= 1; }
        b >>= 2;
    }
    return r;
}

/* Signed bearing from boresight, degrees. x right, y ahead (LD2450 axes). */
static int angle_deg(int x, int y)
{
    int neg = x < 0;
    int32_t ax = neg ? -x : x;
    if (y <= 0) return ax ? (neg ? -90 : 90) : 0;
    int32_t t256 = ax * 256 / y;
    int a = 80;
    for (int i = 1; i <= 80; i++) {
        if (t256 < k_tan256[i]) {
            a = (t256 - k_tan256[i - 1] < k_tan256[i] - t256) ? i - 1 : i;
            break;
        }
    }
    return neg ? -a : a;
}

/* ----- app state ---------------------------------------------------------- */

static gfx_ctx_t *s_gfx;
static gfx_ctx_t *s_cv;              /* scope wedge canvas */
static int        s_input = -1;
static uint16_t   s_w, s_h;

static int s_px0, s_pw;              /* info panel x origin / width          */
static int s_cvx, s_cvy;             /* canvas position on screen            */
static int s_cw, s_ch;               /* canvas dimensions                    */
static int s_ax, s_ay, s_R;          /* wedge apex + radius, canvas coords   */

static const int k_range_mm[3] = { 2000, 4000, 6000 };
static int s_zoom = 1;               /* index into k_range_mm — default 4 m  */

static int s_swp10  = -WEDGE_HALF_DEG * 10;   /* sweep angle, tenths of deg */
static int s_swdir  = 1;

static int             s_fd = -1;
static pthread_t       s_thr;
static int             s_thr_ok;
static pthread_mutex_t s_mu;
static ld2450_frame_t  s_rx_frame;
static uint32_t        s_rx_seq;
static volatile int    s_stop;

static int             s_demo;
static ld2450_parser_t s_demo_parser;
static uint32_t        s_demo_last;

static ld2450_frame_t  s_cur;        /* frame currently displayed           */
static uint32_t        s_cur_ms;     /* last time live data arrived         */
static uint32_t        s_seen_seq;
static int             s_st_ok = 1;

typedef struct { int16_t x, y; uint32_t t; } hist_t;
static hist_t  s_hist[LD2450_MAX_TARGETS][HIST_LEN];
static uint8_t s_hn[LD2450_MAX_TARGETS], s_hw[LD2450_MAX_TARGETS];

/* info panel line cache — redraw only what changed */
#define PL_COUNT 10
static struct { char s[16]; uint16_t fg; int y; } s_pl[PL_COUNT];

static uint32_t now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint32_t)((uint64_t)ts.tv_sec * 1000u + ts.tv_nsec / 1000000u);
}

/* ----- parser self-test (acceptance criterion 5) -------------------------- */

/*
 * Reference vector — worked example from the HLK-LD2450 Serial Communication
 * Protocol (Hi-Link, V1.02), section "Radar data output": report frame
 * AA FF 03 00 | 0E 03 B1 86 10 00 40 01 | 16 zero bytes | 55 CC.
 * Expected decode for target 1 (sign-flag int16 encoding):
 *   X raw 0x030E (bit15 clear)  -> -782 mm
 *   Y raw 0x86B1 (bit15 set)    -> +1713 mm
 *   speed raw 0x0010            -> -16 cm/s
 *   resolution 0x0140           -> 320 mm
 * Targets 2 and 3: all-zero blocks -> absent.
 * The 5 leading bytes are deliberate garbage (including a false header start
 * 0xAA followed by a non-header byte) proving resynchronisation.
 */
static const uint8_t k_selftest_vec[] = {
    0x55, 0xCC, 0xAA, 0x00, 0xF3,
    0xAA, 0xFF, 0x03, 0x00,
    0x0E, 0x03, 0xB1, 0x86, 0x10, 0x00, 0x40, 0x01,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x55, 0xCC,
};

static int selftest(void)
{
    ld2450_parser_t p;
    ld2450_frame_t  f;
    int frames = 0;

    ld2450_parser_init(&p);
    for (unsigned i = 0; i < sizeof(k_selftest_vec); i++)
        if (ld2450_parser_feed(&p, k_selftest_vec[i], &f))
            frames++;

    int ok = frames == 1 &&
             f.target[0].present &&
             f.target[0].x_mm == -782 && f.target[0].y_mm == 1713 &&
             f.target[0].speed_cms == -16 && f.target[0].res_mm == 320 &&
             !f.target[1].present && !f.target[2].present;

    if (ok)
        dprintf(STDOUT_FILENO,
                "radar: ld2450 selftest OK: T1 x=-782mm y=1713mm v=-16cm/s "
                "res=320mm, T2/T3 absent (datasheet V1.02 example, "
                "resync over 5 garbage bytes)\n");
    else
        dprintf(STDOUT_FILENO,
                "radar: ld2450 selftest FAIL: frames=%d present=%d "
                "x=%d y=%d v=%d res=%u\n",
                frames, frames ? f.target[0].present : 0,
                frames ? f.target[0].x_mm : 0, frames ? f.target[0].y_mm : 0,
                frames ? f.target[0].speed_cms : 0,
                frames ? f.target[0].res_mm : 0);
    return ok;
}

/* ----- UART reader thread ------------------------------------------------- */

static void *rx_thread(void *arg)
{
    (void)arg;
    ld2450_parser_t parser;
    ld2450_parser_init(&parser);

    while (!s_stop) {
        ld2450_frame_t f;
        int r = ld2450_read(s_fd, &parser, &f);   /* HAL-bounded, <= ~100 ms */
        if (r < 0) break;
        if (r == 1) {
            pthread_mutex_lock(&s_mu);
            s_rx_frame = f;
            s_rx_seq++;
            pthread_mutex_unlock(&s_mu);
        }
    }
    return NULL;
}

/* ----- demo mode ---------------------------------------------------------- */

static uint16_t enc15(int v)
{
    return v >= 0 ? (uint16_t)(0x8000u | (uint16_t)v) : (uint16_t)(-v);
}

static void enc_block(uint8_t *b, int x, int y, int v)
{
    uint16_t ex = enc15(x), ey = enc15(y), ev = enc15(v);
    b[0] = (uint8_t)ex; b[1] = (uint8_t)(ex >> 8);
    b[2] = (uint8_t)ey; b[3] = (uint8_t)(ey >> 8);
    b[4] = (uint8_t)ev; b[5] = (uint8_t)(ev >> 8);
    b[6] = 0x40; b[7] = 0x01;                     /* resolution 320 mm */
}

/* Synthesise one wire frame (garbage-prefixed) and run it through the real
 * parser — demo mode exercises the exact same decode path as live data. */
static void gen_demo(uint32_t t)
{
    uint8_t raw[3 + LD2450_FRAME_BYTES];
    raw[0] = 0x13; raw[1] = 0xAA; raw[2] = 0x55;  /* incl. false header start */

    uint8_t *fr = raw + 3;
    fr[0] = 0xAA; fr[1] = 0xFF; fr[2] = 0x03; fr[3] = 0x00;
    fr[28] = 0x55; fr[29] = 0xCC;

    /* T1 — weaving walker */
    int ph_a = (int)((t % 9000u)  * 360 / 9000);
    int ph_d = (int)((t % 13000u) * 360 / 13000);
    int a1 = 38 * isin(ph_a) / 1024;
    int d1 = 1700 + 900 * isin(ph_d) / 1024;
    enc_block(fr + 4, d1 * isin(a1) / 1024, d1 * icos(a1) / 1024,
              90 * icos(ph_d) / 1024);

    /* T2 — pacing in and out on a fixed bearing */
    int ph2 = (int)(t % 11000u);
    int d2, v2;
    if (ph2 < 5500) { d2 =  700 + 3300 * ph2 / 5500;          v2 =  60; }
    else            { d2 = 4000 - 3300 * (ph2 - 5500) / 5500; v2 = -60; }
    enc_block(fr + 12, d2 * isin(-24) / 1024, d2 * icos(-24) / 1024, v2);

    /* T3 — intermittent loiterer (exercises the absent-slot path) */
    if (((t / 6000u) & 1u) == 0) {
        int d3 = 2600 + 150 * isin((int)((t % 7000u) * 360 / 7000)) / 1024;
        enc_block(fr + 20, d3 * isin(30) / 1024, d3 * icos(30) / 1024,
                  15 * isin((int)((t % 3000u) * 360 / 3000)) / 1024);
    } else {
        memset(fr + 20, 0, 8);
    }

    for (unsigned i = 0; i < sizeof(raw); i++)
        ld2450_parser_feed(&s_demo_parser, raw[i], &s_cur);
}

/* ----- tracks ------------------------------------------------------------- */

static void reset_tracks(void)
{
    memset(&s_cur, 0, sizeof(s_cur));
    memset(s_hn, 0, sizeof(s_hn));
    memset(s_hw, 0, sizeof(s_hw));
}

static void push_history(uint32_t now)
{
    for (int i = 0; i < LD2450_MAX_TARGETS; i++) {
        const ld2450_target_t *t = &s_cur.target[i];
        if (!t->present) continue;
        if (s_hn[i]) {
            const hist_t *last = &s_hist[i][(s_hw[i] + HIST_LEN - 1) % HIST_LEN];
            int dx = t->x_mm - last->x, dy = t->y_mm - last->y;
            if (dx * dx + dy * dy < 60 * 60 && now - last->t < 250)
                continue;
        }
        s_hist[i][s_hw[i]] = (hist_t){ t->x_mm, t->y_mm, now };
        s_hw[i] = (uint8_t)((s_hw[i] + 1) % HIST_LEN);
        if (s_hn[i] < HIST_LEN) s_hn[i]++;
    }
}

/* ----- scope rendering ---------------------------------------------------- */

static void cv_line(int x0, int y0, int x1, int y1, uint16_t c)
{
    int dx = x1 - x0, sx = dx < 0 ? -1 : 1;
    if (dx < 0) dx = -dx;
    int dy = y1 - y0, sy = dy < 0 ? -1 : 1;
    if (dy < 0) dy = -dy;
    int err = (dx > dy ? dx : -dy) / 2;
    for (;;) {
        gfx_pixel(s_cv, x0, y0, c);
        if (x0 == x1 && y0 == y1) break;
        int e2 = err;
        if (e2 > -dx) { err -= dy; x0 += sx; }
        if (e2 <  dy) { err += dx; y0 += sy; }
    }
}

static void cv_arc(int r, int step, uint16_t c)
{
    for (int a = -WEDGE_HALF_DEG; a <= WEDGE_HALF_DEG; a += step)
        gfx_pixel(s_cv, s_ax + r * isin(a) / 1024, s_ay - r * icos(a) / 1024, c);
}

static void sweep_end(int a10, int *ex, int *ey)
{
    int a = a10 / 10;
    *ex = s_ax + s_R * isin(a) / 1024;
    *ey = s_ay - s_R * icos(a) / 1024;
}

/* Map sensor mm coordinates into canvas pixels; 0 if outside the wedge or
 * beyond the current zoom range. */
static int blip_xy(int x_mm, int y_mm, int *px, int *py)
{
    int32_t rng = k_range_mm[s_zoom];
    int32_t ax = x_mm < 0 ? -x_mm : x_mm;
    if (y_mm < 0) return 0;
    if ((int32_t)x_mm * x_mm + (int32_t)y_mm * y_mm > rng * rng) return 0;
    if (ax * 256 > (int32_t)y_mm * k_tan256[WEDGE_HALF_DEG]) return 0;
    *px = s_ax + (int)((int32_t)x_mm * s_R / rng);
    *py = s_ay - (int)((int32_t)y_mm * s_R / rng);
    return 1;
}

static void render_scope(uint32_t now)
{
    gfx_fill(s_cv, C_BG);

    /* graticule: range arcs + bearing spokes */
    cv_arc(s_R / 4,     4, C_GRID_DIM);
    cv_arc(s_R / 2,     3, C_GRID_DIM);
    cv_arc(s_R * 3 / 4, 3, C_GRID_DIM);
    cv_arc(s_R,         1, C_GRID);
    for (int a = -WEDGE_HALF_DEG; a <= WEDGE_HALF_DEG; a += 30)
        for (int t = 6; t <= s_R; t += 5)
            gfx_pixel(s_cv, s_ax + t * isin(a) / 1024,
                            s_ay - t * icos(a) / 1024, C_GRID_DIM);
    gfx_rect(s_cv, s_ax - 1, s_ay - 1, 3, 3, C_GRID);

    int range_m = k_range_mm[s_zoom] / 1000;
    char lab[8];
    snprintf(lab, sizeof(lab), "%dm", range_m / 2);
    gfx_text(s_cv, s_ax + 4, s_ay - s_R / 2 - 9, lab, C_GRID, C_BG);
    snprintf(lab, sizeof(lab), "%dm", range_m);
    gfx_text(s_cv, s_ax + 4, s_ay - s_R - 1, lab, C_GRID, C_BG);

    /* sweep afterglow (dimmest first), then the sweep line */
    for (int k = 7; k >= 1; k--) {
        int a10 = s_swp10 - s_swdir * k * 30;
        if (a10 < -WEDGE_HALF_DEG * 10 || a10 > WEDGE_HALF_DEG * 10) continue;
        int ex, ey;
        sweep_end(a10, &ex, &ey);
        cv_line(s_ax, s_ay, ex, ey, k_trail_col[k - 1]);
    }
    int ex, ey;
    sweep_end(s_swp10, &ex, &ey);
    cv_line(s_ax, s_ay, ex, ey, C_SWEEP);

    /* target trails (phosphor decay) */
    for (int i = 0; i < LD2450_MAX_TARGETS; i++) {
        for (int k = 0; k < s_hn[i]; k++) {
            const hist_t *h = &s_hist[i][k];
            uint32_t age = now - h->t;
            if (age >= 1600) continue;
            int px, py;
            if (!blip_xy(h->x, h->y, &px, &py)) continue;
            gfx_rect(s_cv, px - 1, py - 1, 2, 2, k_fade_col[age / 400]);
        }
    }

    /* current blips on top */
    for (int i = 0; i < LD2450_MAX_TARGETS; i++) {
        const ld2450_target_t *t = &s_cur.target[i];
        if (!t->present) continue;
        int px, py;
        if (!blip_xy(t->x_mm, t->y_mm, &px, &py)) continue;
        gfx_rect(s_cv, px - 1, py - 1, 3, 3, k_tcol[i]);
        char d[2] = { (char)('1' + i), 0 };
        gfx_text(s_cv, px + 3, py - 9, d, k_tcol[i], C_BG);
    }

    gfx_canvas_present(s_gfx, s_cv, s_cvx, s_cvy);
}

/* ----- info panel --------------------------------------------------------- */

static void pline(int idx, const char *s, uint16_t fg)
{
    if (strcmp(s_pl[idx].s, s) == 0 && s_pl[idx].fg == fg) return;
    int x = s_px0 + 5;
    gfx_rect(s_gfx, x, s_pl[idx].y, s_pw - 6, 8, C_PBG);
    gfx_text(s_gfx, x, s_pl[idx].y, s, fg, C_PBG);
    strncpy(s_pl[idx].s, s, sizeof(s_pl[idx].s) - 1);
    s_pl[idx].s[sizeof(s_pl[idx].s) - 1] = 0;
    s_pl[idx].fg = fg;
}

static void panel_status(uint32_t now)
{
    char st[16];
    const char *mode;
    uint16_t fg;
    if (s_demo)                              { mode = "DEMO";  fg = k_tcol[2]; }
    else if (now - s_cur_ms < RX_STALE_MS && s_cur_ms) { mode = "LIVE"; fg = C_TEXT; }
    else                                     { mode = "NO RX"; fg = C_ALERT; }
    snprintf(st, sizeof(st), "%s R%dm", mode, k_range_mm[s_zoom] / 1000);
    pline(0, st, fg);
}

static void panel_targets(void)
{
    char l1[16], l2[16], l3[16];
    for (int i = 0; i < LD2450_MAX_TARGETS; i++) {
        const ld2450_target_t *t = &s_cur.target[i];
        int base = 1 + i * 3;
        if (!t->present) {
            snprintf(l1, sizeof(l1), "T%d  ---", i + 1);
            pline(base,     l1, C_DIM);
            pline(base + 1, "", C_DIM);
            pline(base + 2, "", C_DIM);
            continue;
        }
        uint32_t mm = isqrt32((uint32_t)((int32_t)t->x_mm * t->x_mm +
                                         (int32_t)t->y_mm * t->y_mm));
        int d10 = (int)((mm + 50) / 100);
        int ang = angle_deg(t->x_mm, t->y_mm);
        int k10 = (int)t->speed_cms * 36 / 100;
        int ka  = k10 < 0 ? -k10 : k10;
        snprintf(l1, sizeof(l1), "T%d  %d.%dm", i + 1, d10 / 10, d10 % 10);
        snprintf(l2, sizeof(l2), "  %d deg", ang);
        snprintf(l3, sizeof(l3), " %c%d.%d km/h", k10 < 0 ? '-' : '+',
                 ka / 10, ka % 10);
        pline(base,     l1, k_tcol[i]);
        pline(base + 1, l2, C_TEXT);
        pline(base + 2, l3, C_TEXT);
    }
}

static void draw_chrome(void)
{
    gfx_rect(s_gfx, 0, 0, s_w, s_h, C_BG);
    gfx_rect(s_gfx, s_px0, 0, s_pw, s_h, C_PBG);
    gfx_rect(s_gfx, s_px0, 0, 1, s_h, C_GRID_DIM);
    gfx_text(s_gfx, s_px0 + (s_pw - 48) / 2, 4, "LD2450", C_TEXT, C_PBG);

    /* keep every line above s_cvy — the canvas blit repaints that region */
    gfx_text(s_gfx, 4, 3,  "RADAR PPI",      C_GRID,     C_BG);
    gfx_text(s_gfx, 4, 13, "z zoom  d demo", C_GRID_DIM, C_BG);
    gfx_text(s_gfx, 4, 23, "esc quit",       C_GRID_DIM, C_BG);
    if (!s_st_ok)
        gfx_text(s_gfx, 4, 33, "selftest FAIL", C_ALERT, C_BG);
}

/* ----- main loop ---------------------------------------------------------- */

static void do_tick(uint32_t now)
{
    int fresh = 0;

    if (s_demo) {
        if (now - s_demo_last >= DEMO_PERIOD_MS) {
            s_demo_last = now;
            gen_demo(now);
            fresh = 1;
        }
    } else if (s_thr_ok) {
        pthread_mutex_lock(&s_mu);
        if (s_rx_seq != s_seen_seq) {
            s_seen_seq = s_rx_seq;
            s_cur = s_rx_frame;
            fresh = 1;
        }
        pthread_mutex_unlock(&s_mu);
        if (fresh) s_cur_ms = now;
    }

    if (fresh) {
        push_history(now);
        panel_targets();
    }

    s_swp10 += s_swdir * 20;
    if (s_swp10 >=  WEDGE_HALF_DEG * 10) { s_swp10 =  WEDGE_HALF_DEG * 10; s_swdir = -1; }
    if (s_swp10 <= -WEDGE_HALF_DEG * 10) { s_swp10 = -WEDGE_HALF_DEG * 10; s_swdir =  1; }

    render_scope(now);
    panel_status(now);
}

/* Returns 0 when the app should exit. */
static int handle_key(uint16_t k)
{
    switch (k) {
    case KEY_ESC:
    case 'q':
        return 0;
    case 'z': case 'Z': case KEY_UP:
        s_zoom = (s_zoom + 1) % 3;
        break;
    case KEY_DOWN:
        s_zoom = (s_zoom + 2) % 3;
        break;
    case 'd': case 'D':
        if (s_fd >= 0) {
            s_demo = !s_demo;
            reset_tracks();
            if (s_demo) ld2450_parser_init(&s_demo_parser);
            panel_targets();
        }
        break;
    default:
        break;
    }
    return 1;
}

static void cleanup(void)
{
    if (s_thr_ok) {
        s_stop = 1;
        pthread_join(s_thr, NULL);
        pthread_mutex_destroy(&s_mu);
    }
    if (s_fd >= 0) ld2450_close(s_fd);
    if (s_cv) gfx_canvas_free(s_cv);
    if (s_input >= 0) close(s_input);
    if (s_gfx) gfx_close(s_gfx);
}

void app_main(void)
{
    s_st_ok = selftest();

    s_gfx = gfx_open_mode(GFX_MODE_STREAM);
    if (!s_gfx) duneos_exit(10);
    s_input = open("/dev/input/event0", O_RDONLY);
    if (s_input < 0) { gfx_close(s_gfx); duneos_exit(11); }
    gfx_get_info(s_gfx, &s_w, &s_h);

    /* layout: scope column left, info panel right (responsive, ADR 024) */
    s_pw  = s_w * 3 / 8;
    if (s_pw < 80) s_pw = 80;
    s_px0 = s_w - s_pw;
    int scope_w = s_px0;
    int half    = (scope_w / 2 - 2) * 1024 / k_sin1024[WEDGE_HALF_DEG];
    s_R = s_h - 20 < half ? s_h - 20 : half;
    int hw = s_R * k_sin1024[WEDGE_HALF_DEG] / 1024 + 2;
    s_cw  = 2 * hw;
    s_ch  = s_R + 8;
    s_cvx = (scope_w - s_cw) / 2;
    s_cvy = s_h - s_ch;
    s_ax  = hw;
    s_ay  = s_ch - 4;

    s_cv = gfx_canvas_new(s_cw, s_ch);
    if (!s_cv) { cleanup(); duneos_exit(13); }

    for (int i = 0; i < PL_COUNT; i++) {
        s_pl[i].s[0] = 0;
        s_pl[i].y = (i == 0) ? 16 : 30 + ((i - 1) / 3) * 32 + ((i - 1) % 3) * 10;
    }

    s_fd = ld2450_open(RADAR_DEV);
    if (s_fd >= 0) {
        int r = ld2450_set_multi_target(s_fd);
        if (r < 0)
            dprintf(STDOUT_FILENO, "radar: multi-target cmd failed (%d)\n", r);
        pthread_mutex_init(&s_mu, NULL);
        s_thr_ok = pthread_create(&s_thr, NULL, rx_thread, NULL) == 0;
        if (!s_thr_ok) {
            dprintf(STDOUT_FILENO, "radar: rx thread failed, demo mode\n");
            ld2450_close(s_fd);
            s_fd = -1;
        }
    } else {
        dprintf(STDOUT_FILENO, "radar: %s unavailable (%d), demo mode\n",
                RADAR_DEV, s_fd);
    }
    s_demo = s_fd < 0;

    reset_tracks();
    draw_chrome();
    panel_targets();

    uint32_t next_tick = now_ms() + TICK_MS;
    for (;;) {
        uint32_t now = now_ms();
        int32_t  dt  = (int32_t)(next_tick - now);
        if (dt <= 0) {
            do_tick(now);
            next_tick += TICK_MS;
            if ((int32_t)(next_tick - now) <= 0) next_tick = now + TICK_MS;
            continue;
        }

        fd_set rf;
        FD_ZERO(&rf);
        FD_SET(s_input, &rf);
        struct timeval tv = { 0, dt * 1000 };
        int n = select(s_input + 1, &rf, NULL, NULL, &tv);
        if (n < 0) break;
        if (n == 0) continue;

        input_event_t ev;
        if (read(s_input, &ev, sizeof(ev)) != (int)sizeof(ev)) break;
        if (ev.type != INPUT_EV_KEY || ev.value == INPUT_VAL_RELEASE) continue;
        if (!handle_key(ev.code)) break;
    }

    cleanup();
    duneos_exit(0);
}
