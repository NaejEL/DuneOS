/*
 * g_shell — DuneOS graphical shell.
 *
 * Requires a display (config from /flash/board.info, any chip supported by
 * libdisp) and /dev/input/event0 (keyboard).
 * Deploy on boards that have both; add to init.yaml to auto-start.
 *
 * Terminal layout (240×135, 8×8 font, 30 cols × 16 rows):
 *   Row  0:     status bar
 *   Rows 1-14:  scrolling output (14 lines)
 *   Row 15:     input line
 *
 * Command dispatch is shared via shell_cmds.c.  sh_out/sh_outln push text
 * into the terminal output buffer instead of writing to a serial fd.
 */

#include <unistd.h>
#include <fcntl.h>
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <sys/stat.h>

#include "duneos/input_ioctl.h"
#include "duneos/disp.h"
#include "duneos/bin_args.h"
#include "font8x8.h"

extern int dprintf(int fd, const char *fmt, ...);
extern int usleep(unsigned int useconds);
extern void duneos_exit(int code);

/* ----- layout ------------------------------------------------------------ */

#define COLS      30
#define OUT_ROWS  14
#define INPUT_MAX 64

/* ----- palette ----------------------------------------------------------- */

#define C_BG        0x0000u
#define C_FG        0xFFFFu
#define C_STATUS_BG 0x000Fu
#define C_STATUS_FG 0xFFFFu
#define C_PROMPT    0x07E0u
#define C_CURSOR_BG 0x07E0u
#define C_CURSOR_FG 0x0000u

/* ----- state ------------------------------------------------------------- */

static disp_ctx_t *s_disp  = NULL;
static int         s_input = -1;

static char s_out[OUT_ROWS][COLS + 1];
static int  s_out_used = 0;
static char s_cwd[256] = "/sd";    /* required by shell_cmds.c */
static char s_line[INPUT_MAX + 1];
static int  s_line_len = 0;
static int  s_exit = 0;

static uint16_t s_px[240 * 8];

/* ----- terminal output buffer -------------------------------------------- */

static void out_push(const char *line)
{
    if (s_out_used < OUT_ROWS) {
        strncpy(s_out[s_out_used], line, COLS);
        s_out[s_out_used][COLS] = '\0';
        s_out_used++;
    } else {
        memmove(s_out[0], s_out[1], (OUT_ROWS - 1) * (COLS + 1));
        strncpy(s_out[OUT_ROWS - 1], line, COLS);
        s_out[OUT_ROWS - 1][COLS] = '\0';
    }
}

/* Split multi-line text (with word wrap) into the output buffer. */
static void out_text(const char *text)
{
    char line[COLS + 1];
    int  col = 0;
    for (const char *p = text; *p; p++) {
        if (*p == '\r') continue;
        if (*p == '\n' || col == COLS) {
            line[col] = '\0'; out_push(line); col = 0;
            if (*p == '\n') continue;
        }
        line[col++] = *p;
    }
    if (col > 0) { line[col] = '\0'; out_push(line); }
}

/* ----- sh_out / sh_outln (required by shell_cmds.c) --------------------- */

static void sh_out(const char *s)   { out_text(s); }
static void sh_outln(const char *s) { out_text(s); out_push(""); }

/* ----- include shared command dispatch ----------------------------------- */

/* From apps/user/g_shell/, "../../" resolves to apps/, so this picks up
 * apps/system/shell_core/shell_cmds.c (the shared shell command dispatch). */
#include "../../system/shell_core/shell_cmds.c"

/* ----- rendering --------------------------------------------------------- */

static void render_row(int row, const char *text, int len,
                        uint16_t fg, uint16_t bg, int cursor_col)
{
    for (int col = 0; col < COLS; col++) {
        char c = (col < len) ? text[col] : ' ';
        if ((unsigned char)c < 32 || (unsigned char)c > 126) c = ' ';
        const uint8_t *g = font8x8_basic[(unsigned char)c - 32];
        uint16_t this_fg = fg, this_bg = bg;
        if (col == cursor_col) { this_fg = C_CURSOR_FG; this_bg = C_CURSOR_BG; }
        for (int r = 0; r < 8; r++) {
            uint8_t bits = g[r];
            for (int x = 0; x < 8; x++)
                s_px[r * 240 + col * 8 + x] = ((bits >> x) & 1) ? this_fg : this_bg;
        }
    }
    disp_write_area(s_disp, 0, (uint16_t)(row * 8), 240, 8, s_px);
}

static void draw_status(void)
{
    char buf[COLS + 1];
    snprintf(buf, sizeof(buf), " g_shell  %s", s_cwd);
    render_row(0, buf, strlen(buf), C_STATUS_FG, C_STATUS_BG, -1);
}

static void draw_output(void)
{
    for (int i = 0; i < OUT_ROWS; i++) {
        if (i < s_out_used)
            render_row(i + 1, s_out[i], strlen(s_out[i]), C_FG, C_BG, -1);
        else
            render_row(i + 1, "", 0, C_FG, C_BG, -1);
    }
}

static void draw_input(void)
{
    char buf[COLS + 1];
    buf[0] = '$'; buf[1] = ' ';
    int avail = COLS - 3;
    int start = (s_line_len > avail) ? s_line_len - avail : 0;
    int show  = s_line_len - start;
    memcpy(buf + 2, s_line + start, show);
    int cur_col = 2 + show;
    buf[cur_col] = ' ';
    render_row(15, buf, cur_col + 1, C_PROMPT, C_BG, cur_col);
}

static void full_redraw(void)
{
    draw_status();
    draw_output();
    draw_input();
}

/* ----- main loop --------------------------------------------------------- */

void app_main(void)
{
    s_disp  = disp_open();
    s_input = open("/dev/input/event0", O_RDONLY);

    if (!s_disp || s_input < 0) {
        if (s_disp)        disp_close(s_disp);
        if (s_input >= 0) close(s_input);
        duneos_exit(1);
    }

    full_redraw();

    while (!s_exit) {
        input_event_t ev;
        if (read(s_input, &ev, sizeof(ev)) != (int)sizeof(ev)) continue;

        if (ev.type == INPUT_EV_KEY && ev.value != INPUT_VAL_RELEASE) {
            uint16_t k = ev.code;
            if (k >= 0x20 && k < 0x7F && s_line_len < INPUT_MAX) {
                s_line[s_line_len++] = (char)k;
                s_line[s_line_len]   = '\0';
                draw_input();
            } else if (k == KEY_BACKSPACE && s_line_len > 0) {
                s_line[--s_line_len] = '\0';
                draw_input();
            } else if (k == KEY_ENTER && s_line_len > 0) {
                char echo[COLS + 1];
                snprintf(echo, sizeof(echo), "$ %.*s", COLS - 2, s_line);
                out_push(echo);

                char cmd[INPUT_MAX + 1];
                memcpy(cmd, s_line, s_line_len + 1);
                s_line_len = 0; s_line[0] = '\0';

                if (strcmp(cmd, "exit")  == 0) { s_exit = 1; }
                else if (strcmp(cmd, "clear") == 0) { s_out_used = 0; }
                else exec_line(cmd);

                draw_status();
                draw_output();
                draw_input();
            } else if (k == KEY_ESC) {
                s_line_len = 0; s_line[0] = '\0';
                draw_input();
            }
        }
    }

    disp_close(s_disp);
    close(s_input);
    duneos_exit(0);
}
