/*
 * scenario.c — SD-loaded probe scenarios.
 *
 * A scenario is a small text file on /sd/i2cscope/<name>.scn describing a device
 * address and a list of register reads to run and interpret. The engine lists
 * the files, runs the selected one through the SDK libsmbus, and shows the
 * decoded values — so a new chip is "supported" by dropping a text file on the
 * SD card, no rebuild. See the scenarios/ directory for examples.
 *
 * Line format (blank lines and '#' comments ignored):
 *   name <free text>
 *   addr 0xNN
 *   <type> <reg> <unit> <label...>
 * where <type> is one of:
 *   byte   1-byte register read (0..255)
 *   word   2-byte little-endian (SMBus word)
 *   sword  2-byte little-endian, signed
 *   hex    2-byte, shown as 0xXXXX
 *   temp   2-byte 0.1 K, shown as degrees C
 * <unit> "-" means none.
 */

#include "i2cscope.h"
#include "duneos/smbus.h"

#include <dirent.h>
#include <stdint.h>

#define SC_DIR        "/sd/i2cscope"
#define SC_MAX_FILES  24
#define SC_NAMELEN    32
#define SC_MAX_STEPS  16

enum { ST_BYTE, ST_WORD, ST_SWORD, ST_HEX, ST_TEMP };

typedef struct {
    uint8_t type;
    uint8_t reg;
    char    unit[6];
    char    label[18];
} scn_step_t;

enum { SUB_LIST, SUB_RUN };
static int s_sub;

static ui_list_t s_list;
static char        s_files[SC_MAX_FILES][SC_NAMELEN];
static const char *s_fileptr[SC_MAX_FILES];
static int         s_nfiles;

static char       s_name[28];
static uint8_t    s_addr;
static scn_step_t s_steps[SC_MAX_STEPS];
static int        s_nsteps;
static int        s_val[SC_MAX_STEPS];
static int        s_ok[SC_MAX_STEPS];

/* ----- parsing ----------------------------------------------------------- */

static int has_scn_ext(const char *n)
{
    int L = (int)strlen(n);
    if (L < 4) return 0;
    const char *e = n + L - 4;
    return e[0] == '.' && (e[1] | 0x20) == 's' && (e[2] | 0x20) == 'c' && (e[3] | 0x20) == 'n';
}

static int next_tok(char **p, char *out, int outsz)
{
    char *s = *p;
    while (*s == ' ' || *s == '\t') s++;
    if (!*s) { *p = s; return 0; }
    int i = 0;
    while (*s && *s != ' ' && *s != '\t' && i < outsz - 1) out[i++] = *s++;
    out[i] = '\0';
    *p = s;
    return 1;
}

static int parse_hex(const char *s)
{
    if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) s += 2;
    int v = 0, n = 0;
    for (; hexnib(*s) >= 0; s++) { v = v * 16 + hexnib(*s); n++; }
    return n ? v : -1;
}

static int type_of(const char *t)
{
    if (!strcmp(t, "byte"))  return ST_BYTE;
    if (!strcmp(t, "word"))  return ST_WORD;
    if (!strcmp(t, "sword")) return ST_SWORD;
    if (!strcmp(t, "hex"))   return ST_HEX;
    if (!strcmp(t, "temp"))  return ST_TEMP;
    return -1;
}

static void parse_line(char *line)
{
    int L = (int)strlen(line);
    while (L > 0 && (line[L - 1] == '\r' || line[L - 1] == ' ' || line[L - 1] == '\t'))
        line[--L] = '\0';

    char *p = line;
    char tok[24];
    if (!next_tok(&p, tok, sizeof(tok))) return;
    if (tok[0] == '#') return;

    if (!strcmp(tok, "name")) {
        while (*p == ' ') p++;
        strncpy(s_name, p, sizeof(s_name) - 1);
        s_name[sizeof(s_name) - 1] = '\0';
        return;
    }
    if (!strcmp(tok, "addr")) {
        char a[12];
        if (next_tok(&p, a, sizeof(a))) { int v = parse_hex(a); if (v >= 0) s_addr = (uint8_t)v; }
        return;
    }

    int ty = type_of(tok);
    if (ty < 0 || s_nsteps >= SC_MAX_STEPS) return;
    char rt[12], ut[8] = { 0 };
    if (!next_tok(&p, rt, sizeof(rt))) return;
    int reg = parse_hex(rt);
    if (reg < 0) return;
    next_tok(&p, ut, sizeof(ut));

    scn_step_t *st = &s_steps[s_nsteps++];
    st->type = (uint8_t)ty;
    st->reg  = (uint8_t)reg;
    strncpy(st->unit, ut, sizeof(st->unit) - 1);
    st->unit[sizeof(st->unit) - 1] = '\0';
    if (!strcmp(st->unit, "-")) st->unit[0] = '\0';
    while (*p == ' ') p++;
    strncpy(st->label, p, sizeof(st->label) - 1);
    st->label[sizeof(st->label) - 1] = '\0';
}

static int scn_load(const char *path)
{
    int fd = open(path, O_RDONLY);
    if (fd < 0) return -1;
    char buf[1024];
    int n = read(fd, buf, sizeof(buf) - 1);
    close(fd);
    if (n <= 0) return -1;
    buf[n] = '\0';

    s_name[0] = '\0';
    s_addr    = 0;
    s_nsteps  = 0;
    char *line = buf;
    while (line && *line) {
        char *nl = strchr(line, '\n');
        if (nl) *nl = '\0';
        parse_line(line);
        line = nl ? nl + 1 : NULL;
    }
    return (s_addr && s_nsteps) ? 0 : -1;
}

/* ----- run + format ------------------------------------------------------ */

static void scn_run(void)
{
    for (int i = 0; i < s_nsteps; i++) {
        int v = (s_steps[i].type == ST_BYTE)
                    ? smbus_read_byte_data(g_i2c, s_addr, s_steps[i].reg)
                    : smbus_read_word_data(g_i2c, s_addr, s_steps[i].reg);
        s_ok[i]  = (v >= 0);
        s_val[i] = (v >= 0) ? v : 0;
    }
}

static void fmt_value(const scn_step_t *st, int val, char *out, int outsz)
{
    switch (st->type) {
    case ST_HEX:
        snprintf(out, outsz, "0x%04X", (unsigned)(val & 0xFFFF));
        break;
    case ST_SWORD:
        snprintf(out, outsz, "%d %s", (int)(int16_t)val, st->unit);
        break;
    case ST_TEMP: {
        int t = val - 2732;           /* 0.1 K → 0.1 °C (approx) */
        int frac = t % 10; if (frac < 0) frac = -frac;
        snprintf(out, outsz, "%d.%d C", t / 10, frac);
        break;
    }
    default:
        if (st->unit[0]) snprintf(out, outsz, "%d %s", val, st->unit);
        else             snprintf(out, outsz, "%d", val);
        break;
    }
}

/* ----- screens ----------------------------------------------------------- */

static void scan_files(void)
{
    s_nfiles = 0;
    DIR *d = opendir(SC_DIR);
    if (d) {
        struct dirent *e;
        while (s_nfiles < SC_MAX_FILES && (e = readdir(d)) != NULL) {
            if (!has_scn_ext(e->d_name)) continue;
            strncpy(s_files[s_nfiles], e->d_name, SC_NAMELEN - 1);
            s_files[s_nfiles][SC_NAMELEN - 1] = '\0';
            s_fileptr[s_nfiles] = s_files[s_nfiles];
            s_nfiles++;
        }
        closedir(d);
    }
    ui_list_set_items(&s_list, s_fileptr, s_nfiles);
}

static void draw_list(void)
{
    ui_clear(g_ui);
    ui_titlebar(g_ui, "scenarios");
    if (s_nfiles == 0) ui_message(g_ui, "No .scn on " SC_DIR, "Esc=back");
    else               ui_list_draw(g_ui, &s_list);
    ui_statusbar(g_ui, "Enter=run  R=rescan  Esc=back");
    ui_flush(g_ui);
}

static void draw_run(void)
{
    ui_clear(g_ui);
    char t[40];
    snprintf(t, sizeof(t), "%s  0x%02X", s_name[0] ? s_name : "scenario", s_addr);
    ui_titlebar(g_ui, t);

    uint16_t bg = ui_theme(g_ui)->bg;
    int y = g_bar_h + 2, rh = 12;
    char vbuf[24];
    for (int i = 0; i < s_nsteps && y + 8 <= g_sh - g_bar_h; i++) {
        gfx_text(g_gfx, 2, y, s_steps[i].label[0] ? s_steps[i].label : "?", C_DATA, bg);
        if (s_ok[i]) {
            fmt_value(&s_steps[i], s_val[i], vbuf, sizeof(vbuf));
            gfx_text(g_gfx, 150, y, vbuf, C_RESP, bg);
        } else {
            gfx_text(g_gfx, 150, y, "ERR", C_NACK, bg);
        }
        y += rh;
    }
    ui_statusbar(g_ui, "R=run  Esc=back");
    ui_flush(g_ui);
}

void scenario_open(void)
{
    s_sub = SUB_LIST;
    ui_list_init(&s_list, 0, g_bar_h, g_sw, g_sh - 2 * g_bar_h);
    scan_files();
    draw_list();
}

int scenario_event(uint16_t k)
{
    if (s_sub == SUB_LIST) {
        if (k == 'r' || k == 'R') { scan_files(); draw_list(); return 0; }
        switch (ui_list_key(&s_list, k)) {
        case UI_LIST_MOVED: ui_list_draw(g_ui, &s_list); ui_flush(g_ui); break;
        case UI_LIST_SELECT:
            if (s_nfiles > 0) {
                char path[64];
                snprintf(path, sizeof(path), "%s/%s", SC_DIR, s_files[s_list.sel]);
                if (scn_load(path) == 0) { scn_run(); s_sub = SUB_RUN; draw_run(); }
                else { ui_statusbar(g_ui, "parse failed (need addr + steps)"); ui_flush(g_ui); }
            }
            break;
        case UI_LIST_CANCEL: return 1;
        case UI_LIST_NONE: break;
        }
        return 0;
    }

    /* SUB_RUN */
    if (k == 'r' || k == 'R') { scn_run(); draw_run(); }
    else if (k == KEY_ESC)    { s_sub = SUB_LIST; draw_list(); }
    return 0;
}
