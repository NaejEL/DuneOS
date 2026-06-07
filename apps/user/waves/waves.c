/*
 * waves — VCD chronogram viewer (PulseView-lite).
 *
 * Opens a .vcd (Value Change Dump) — e.g. the captures i2cscope saves — and
 * draws the channels as a timing diagram (libui ui_wave), pan/zoom along time.
 * When SCL/SDA channels are present, it overlays the I2C decode (addr/data/ACK,
 * START/STOP) using the shared sdk/proto decoder — the same one i2cscope uses
 * live. Generic by format: any digital VCD renders as waves; protocol overlays
 * are added per protocol.
 *
 * Until the ADR 026 "open with" handoff lands, it lists the .vcd files on /sd
 * itself; later it will also take a path from the file explorer.
 */

#include <unistd.h>
#include <fcntl.h>
#include <string.h>
#include <stdio.h>
#include <dirent.h>

#include "duneos/input_ioctl.h"
#include "duneos/gfx.h"
#include "duneos/ui.h"
#include "duneos/ui_wave.h"
#include "duneos/i2c_decode.h"

extern void duneos_exit(int code);

#define MAX_VCD    32
#define PATH_LEN   160
#define NAME_LEN   40
#define MAX_CH     8
#define MAX_SAMP   1024
#define MAX_TOK    256

#define C_MARK GFX_RGB(  0, 200, 200)
#define C_ADDR GFX_RGB( 90, 170, 255)
#define C_DATA GFX_RGB(225, 225, 225)
#define C_ACK  GFX_RGB( 70, 220, 110)
#define C_NACK GFX_RGB(240,  90,  90)

static gfx_ctx_t *g;
static ui_t      *ui;
static int        s_input = -1;
static uint16_t   s_sw, s_sh;
static int        s_bar_h;

/* file browser */
static char        s_path[MAX_VCD][PATH_LEN];
static char        s_name[MAX_VCD][NAME_LEN];
static const char *s_name_ptr[MAX_VCD];
static int         s_nfile;
static ui_list_t   s_list;

/* parsed capture */
static ui_wave_sample_t s_samp[MAX_SAMP];
static int          s_nsamp;
static char         s_chid[MAX_CH];
static char         s_chname[MAX_CH][8];
static const char  *s_chptr[MAX_CH];
static int          s_nch;
static int          s_scl_bit = -1, s_sda_bit = -1;

static i2c_token_t  s_tok[MAX_TOK];
static int          s_ntok;

static ui_wave_t    s_wave;

enum { MODE_BROWSE, MODE_WAVE };
static int s_mode;

/* ----- tiny streaming line reader ---------------------------------------- */

static int   s_fd = -1;
static char  s_chunk[256];
static int   s_clen, s_cpos;

static int next_char(void)
{
    if (s_cpos >= s_clen) {
        s_clen = read(s_fd, s_chunk, sizeof(s_chunk));
        s_cpos = 0;
        if (s_clen <= 0) return -1;
    }
    return (unsigned char)s_chunk[s_cpos++];
}

static int read_line(char *out, int max)
{
    int i = 0, c;
    while ((c = next_char()) >= 0 && c != '\n')
        if (c != '\r' && i < max - 1) out[i++] = (char)c;
    out[i] = '\0';
    return (i > 0 || c >= 0) ? i : -1;
}

/* ----- VCD parsing ------------------------------------------------------- */

static int bit_of_id(char id)
{
    for (int i = 0; i < s_nch; i++) if (s_chid[i] == id) return i;
    return -1;
}

static int parse_vcd(const char *path)
{
    s_fd = open(path, O_RDONLY);
    if (s_fd < 0) return -1;
    s_clen = s_cpos = 0;
    s_nch = s_nsamp = 0;
    s_scl_bit = s_sda_bit = -1;

    char line[96];
    int defs = 0, pending = 0;
    uint32_t cur_t = 0, mask = 0;

    while (read_line(line, sizeof(line)) >= 0) {
        char *p = line;
        while (*p == ' ' || *p == '\t') p++;
        if (*p == '\0') continue;

        if (!defs) {
            if (strncmp(p, "$var", 4) == 0) {
                /* $var wire 1 <id> <name> $end */
                char *tok[6]; int nt = 0;
                for (char *q = p; *q && nt < 6; ) {
                    while (*q == ' ') q++;
                    if (!*q) break;
                    tok[nt++] = q;
                    while (*q && *q != ' ') q++;
                    if (*q) *q++ = '\0';
                }
                if (nt >= 5 && s_nch < MAX_CH) {
                    s_chid[s_nch] = tok[3][0];
                    snprintf(s_chname[s_nch], sizeof(s_chname[0]), "%s", tok[4]);
                    s_chptr[s_nch] = s_chname[s_nch];
                    if (strcmp(tok[4], "SCL") == 0) s_scl_bit = s_nch;
                    if (strcmp(tok[4], "SDA") == 0) s_sda_bit = s_nch;
                    s_nch++;
                }
            } else if (strncmp(p, "$enddefinitions", 15) == 0) {
                defs = 1;
            }
            continue;
        }

        if (*p == '#') {
            /* New timestamp: flush the previous one's final mask as a sample
             * (all value changes under a timestamp apply together). */
            if (pending && s_nsamp < MAX_SAMP) {
                s_samp[s_nsamp].t_us = cur_t;
                s_samp[s_nsamp].levels = mask;
                s_nsamp++;
            }
            cur_t = 0;
            for (char *q = p + 1; *q >= '0' && *q <= '9'; q++) cur_t = cur_t * 10 + (*q - '0');
            pending = 1;
        } else if ((*p == '0' || *p == '1') && p[1]) {
            int b = bit_of_id(p[1]);
            if (b >= 0) {
                if (*p == '1') mask |= (1u << b); else mask &= ~(1u << b);
            }
        }
    }
    if (pending && s_nsamp < MAX_SAMP) {       /* flush the last timestamp */
        s_samp[s_nsamp].t_us = cur_t;
        s_samp[s_nsamp].levels = mask;
        s_nsamp++;
    }
    close(s_fd);
    s_fd = -1;

    s_ntok = 0;
    if (s_scl_bit >= 0 && s_sda_bit >= 0)
        s_ntok = i2c_decode((const i2c_sample_t *)s_samp, s_nsamp,
                            s_scl_bit, s_sda_bit, s_tok, MAX_TOK);
    return s_nsamp > 0 ? 0 : -1;
}

/* ----- file browser ------------------------------------------------------ */

static int has_vcd(const char *n)
{
    int L = (int)strlen(n);
    return L >= 4 && n[L-4] == '.' && (n[L-3]|0x20) == 'v' &&
           (n[L-2]|0x20) == 'c' && (n[L-1]|0x20) == 'd';
}

static void scan_files(void)
{
    s_nfile = 0;
    DIR *d = opendir("/sd");
    if (d) {
        struct dirent *e;
        while (s_nfile < MAX_VCD && (e = readdir(d)) != NULL) {
            if (!has_vcd(e->d_name)) continue;
            snprintf(s_path[s_nfile], PATH_LEN, "/sd/%s", e->d_name);
            snprintf(s_name[s_nfile], NAME_LEN, "%s", e->d_name);
            s_name_ptr[s_nfile] = s_name[s_nfile];
            s_nfile++;
        }
        closedir(d);
    }
    ui_list_set_items(&s_list, s_name_ptr, s_nfile);
}

static void draw_browse(void)
{
    ui_clear(ui);
    ui_titlebar(ui, "waves");
    if (s_nfile == 0) ui_message(ui, "No .vcd on /sd", "Esc=quit");
    else              ui_list_draw(ui, &s_list);
    ui_statusbar(ui, "Enter=open  Esc=quit");
    ui_flush(ui);
}

/* ----- wave view + decode overlay ---------------------------------------- */

static void draw_overlay(int annot_y)
{
    uint16_t bg = ui_theme(ui)->bg;
    gfx_rect(g, 0, annot_y, s_sw, 10, bg);
    int last_x = -100;
    char b[8];
    for (int i = 0; i < s_ntok; i++) {
        i2c_token_t *t = &s_tok[i];
        int x = ui_wave_time_x(&s_wave, t->t_us);
        if (x < last_x + 9) continue;          /* avoid total overlap */
        switch (t->type) {
        case I2C_TOK_START: gfx_text(g, x, annot_y, "S", C_MARK, bg); break;
        case I2C_TOK_STOP:  gfx_text(g, x, annot_y, "P", C_MARK, bg); break;
        case I2C_TOK_ADDR:
            snprintf(b, sizeof(b), "%02X%c", t->val, t->rw ? 'r' : 'w');
            gfx_text(g, x, annot_y, b, t->ack ? C_ADDR : C_NACK, bg);
            break;
        case I2C_TOK_DATA:
            snprintf(b, sizeof(b), "%02X", t->val);
            gfx_text(g, x, annot_y, b, t->ack ? C_DATA : C_NACK, bg);
            break;
        }
        last_x = x;
    }
}

static void draw_wave(void)
{
    ui_clear(ui);
    char *base = strrchr(s_path[s_list.sel], '/');
    ui_titlebar(ui, base ? base + 1 : "waves");
    ui_wave_draw(ui, &s_wave);
    if (s_ntok > 0) draw_overlay(s_bar_h + s_wave.h);
    ui_statusbar(ui, "<>=pan  ^v=zoom  Esc=back");
    ui_flush(ui);
}

static void open_vcd(void)
{
    if (parse_vcd(s_path[s_list.sel]) != 0) return;
    int annot_h = (s_ntok > 0) ? 11 : 0;
    ui_wave_init(&s_wave, 0, s_bar_h, s_sw, s_sh - 2 * s_bar_h - annot_h);
    ui_wave_set(&s_wave, s_samp, s_nsamp, s_nch, s_chptr);
    s_mode = MODE_WAVE;
    draw_wave();
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

    ui_list_init(&s_list, 0, s_bar_h, s_sw, s_sh - 2 * s_bar_h);
    scan_files();
    draw_browse();

    for (;;) {
        input_event_t ev;
        if (read(s_input, &ev, sizeof(ev)) != (int)sizeof(ev)) continue;
        if (ev.type != INPUT_EV_KEY || ev.value == INPUT_VAL_RELEASE) continue;
        uint16_t k = ev.code;

        if (s_mode == MODE_WAVE) {
            if (k == KEY_ESC) { s_mode = MODE_BROWSE; draw_browse(); }
            else if (ui_wave_key(&s_wave, k)) draw_wave();
            continue;
        }
        if (s_nfile == 0) { if (k == KEY_ESC) break; continue; }

        switch (ui_list_key(&s_list, k)) {
        case UI_LIST_MOVED:  ui_list_draw(ui, &s_list); ui_flush(ui); break;
        case UI_LIST_SELECT: open_vcd(); break;
        case UI_LIST_CANCEL: goto done;
        case UI_LIST_NONE:   break;
        }
    }

done:
    ui_destroy(ui);
    close(s_input);
    gfx_close(g);
    duneos_exit(0);
}
