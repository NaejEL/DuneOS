/*
 * g_shell — DuneOS graphical shell, rebuilt on the libui toolkit.
 *
 * Layout (240x135):
 *   titlebar          "g_shell  <cwd>"
 *   textview          scrolling command output
 *   input line        "$ " + editable command + cursor
 *
 * Rendering goes through <duneos/ui.h> (theme, titlebar, textview, input) over
 * libgfx in STREAM mode — no userspace back-buffer. Command dispatch is the
 * shared shell_cmds.c; sh_out/sh_outln push wrapped lines into the textview.
 *
 * ESC quits back to the launcher (cooperative-quit convention); 'exit' and
 * 'clear' are handled inline, everything else goes to exec_line().
 */

#include <unistd.h>
#include <fcntl.h>
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <sys/stat.h>

#include "duneos/input_ioctl.h"
#include "duneos/gfx.h"
#include "duneos/ui.h"

extern void duneos_exit(int code);

/* ----- layout / storage -------------------------------------------------- */

#define MAXCOLS    40              /* wrap-buffer width (>= any 240px row)   */
#define SCROLLBACK 24              /* output lines kept                       */
#define LINE_W     (MAXCOLS + 1)
#define INPUT_MAX  64

static gfx_ctx_t   *s_gfx   = NULL;
static ui_t        *s_ui    = NULL;
static int          s_input = -1;

static ui_textview_t s_tv;
static ui_input_t    s_in;
static char          s_outbuf[SCROLLBACK][LINE_W];
static char          s_inbuf[INPUT_MAX + 1];
static int           s_cols = 30;

static char s_cwd[256] = "/sd";    /* required by shell_cmds.c */

#define HIST_DEPTH 16
static char s_hist[HIST_DEPTH][INPUT_MAX + 1];
static int  s_hist_count;
static int  s_hist_nav = -1;       /* -1 = editing a fresh line */

/* ----- output buffer (required by shell_cmds.c) -------------------------- */

/* Captured-app stdout now streams in arbitrary chunks (the loader hands us each
 * write as it happens), so the in-progress row must persist across sh_write
 * calls — wrapping at \n or the column width, flushed explicitly when a command
 * finishes. sh_out/sh_outln keep their old discrete-message behaviour. */
static char s_row[MAXCOLS + 1];
static int  s_rowcol;

static void sh_flush(void)
{
    if (s_rowcol > 0) { s_row[s_rowcol] = '\0'; ui_textview_push(&s_tv, s_row); s_rowcol = 0; }
}

static void sh_write(const char *data, int len)
{
    for (int i = 0; i < len; i++) {
        char c = data[i];
        if (c == '\r') continue;
        if (c == '\n' || s_rowcol >= s_cols) {
            s_row[s_rowcol] = '\0'; ui_textview_push(&s_tv, s_row); s_rowcol = 0;
            if (c == '\n') continue;
        }
        if (s_rowcol < MAXCOLS) s_row[s_rowcol++] = c;
    }
}

static void sh_out(const char *s)   { sh_write(s, (int)strlen(s)); sh_flush(); }
static void sh_outln(const char *s) { sh_out(s); ui_textview_push(&s_tv, ""); }

/* ----- shared command dispatch ------------------------------------------- */

#include "../../system/shell_core/shell_cmds.c"

/* ----- rendering --------------------------------------------------------- */

static void draw_title(void)
{
    char buf[64];
    snprintf(buf, sizeof(buf), "g_shell  %s", s_cwd);
    ui_titlebar(s_ui, buf);
}

static void redraw_all(void)
{
    ui_clear(s_ui);
    draw_title();
    ui_textview_draw(s_ui, &s_tv);
    ui_input_draw(s_ui, &s_in);
    ui_flush(s_ui);
}

/* ----- history ----------------------------------------------------------- */

static void hist_push(const char *line)
{
    if (line[0] == '\0') return;
    if (s_hist_count > 0 &&
        strcmp(s_hist[(s_hist_count - 1) % HIST_DEPTH], line) == 0) return;
    snprintf(s_hist[s_hist_count % HIST_DEPTH], INPUT_MAX + 1, "%s", line);
    s_hist_count++;
}

static void hist_load(int idx)
{
    snprintf(s_inbuf, INPUT_MAX + 1, "%s", s_hist[idx % HIST_DEPTH]);
    s_in.len = (int)strlen(s_inbuf);
    s_in.pos = s_in.len;
}

/* KEY_UP / KEY_DOWN: walk the ring like a serial shell, oldest kept entry to
 * the freshly-cleared line. Redraws the input row. */
static void hist_nav(int up)
{
    if (up) {
        if (s_hist_count == 0) return;
        int next = (s_hist_nav < 0) ? s_hist_count - 1 : s_hist_nav - 1;
        if (next < s_hist_count - HIST_DEPTH) next = s_hist_count - HIST_DEPTH;
        if (next < 0) next = 0;
        s_hist_nav = next;
        hist_load(s_hist_nav);
    } else {
        if (s_hist_nav < 0) return;
        if (++s_hist_nav >= s_hist_count) {
            s_hist_nav = -1; s_inbuf[0] = '\0'; s_in.len = 0; s_in.pos = 0;
        } else {
            hist_load(s_hist_nav);
        }
    }
    ui_input_draw(s_ui, &s_in);
    ui_flush(s_ui);
}

/* ----- tab completion ---------------------------------------------------- */

/* Extend the input line with the longest common completion of its trailing
 * token; on an ambiguous second Tab, list the choices into the scrollback.
 * Gathering is the shared shell_cmds.c engine — only the rendering is ours. */
static void complete_input(void)
{
    int tok_start, base_off;
    int cnt = shell_completions(s_inbuf, s_in.len, &tok_start, &base_off);
    if (cnt <= 0) return;

    int typed = s_in.len - (tok_start + base_off);   /* base chars already typed */

    char lcp[128];
    int  lcl = shell_comp_lcp(lcp, sizeof(lcp));
    int  add = lcl - typed;
    for (int k = 0; k < add && s_in.len < INPUT_MAX; k++)
        s_inbuf[s_in.len++] = lcp[typed + k];

    if (cnt == 1) {
        int isdir = 0;
        shell_comp_name(0, &isdir);
        char sep = isdir ? '/' : ' ';
        if (s_in.len < INPUT_MAX) s_inbuf[s_in.len++] = sep;
    }
    s_inbuf[s_in.len] = '\0';
    s_in.pos = s_in.len;

    if (cnt > 1 && add <= 0) {
        char row[MAXCOLS + 1];
        int  col = 0;
        for (int i = 0; i < cnt; i++) {
            int isdir = 0;
            const char *nm = shell_comp_name(i, &isdir);
            int nl   = (int)strlen(nm);
            int need = nl + (isdir ? 1 : 0) + 2;
            if (col > 0 && col + need > s_cols) {
                row[col] = '\0'; ui_textview_push(&s_tv, row); col = 0;
            }
            for (int j = 0; j < nl && col < MAXCOLS; j++) row[col++] = nm[j];
            if (isdir && col < MAXCOLS) row[col++] = '/';
            if (col < MAXCOLS) row[col++] = ' ';
            if (col < MAXCOLS) row[col++] = ' ';
        }
        if (col > 0) { row[col] = '\0'; ui_textview_push(&s_tv, row); }
        redraw_all();
    } else {
        ui_input_draw(s_ui, &s_in);
        ui_flush(s_ui);
    }
}

/* ----- main -------------------------------------------------------------- */

void app_main(void)
{
    /* Diagnostic exit codes (mirror the launcher):
     *   10 = display open failed
     *   11 = /dev/input/event0 open failed                                   */
    s_gfx = gfx_open_mode(GFX_MODE_STREAM);
    if (!s_gfx) duneos_exit(10);

    s_input = open("/dev/input/event0", O_RDONLY);
    if (s_input < 0) { gfx_close(s_gfx); duneos_exit(11); }

    s_ui = ui_create(s_gfx);
    if (!s_ui) { close(s_input); gfx_close(s_gfx); duneos_exit(10); }

    uint16_t sw, sh;
    ui_size(s_ui, &sw, &sh);
    s_cols = sw / 8;
    if (s_cols > MAXCOLS) s_cols = MAXCOLS;

    int bar_h = 8 + 2 * ui_theme(s_ui)->pad;
    ui_textview_init(&s_tv, 0, bar_h, sw, sh - bar_h - 12,
                     &s_outbuf[0][0], SCROLLBACK, LINE_W);
    ui_input_init(&s_in, 2, sh - 10, sw - 2, s_inbuf, sizeof(s_inbuf), "$ ");

    sh_outln("DuneOS g_shell — type 'help', Esc to exit");
    redraw_all();

    for (;;) {
        input_event_t ev;
        if (read(s_input, &ev, sizeof(ev)) != (int)sizeof(ev)) continue;
        if (ev.type != INPUT_EV_KEY || ev.value == INPUT_VAL_RELEASE) continue;

        if (ev.code == KEY_TAB) { complete_input(); continue; }
        if (ev.code == KEY_UP)   { hist_nav(1); continue; }
        if (ev.code == KEY_DOWN) { hist_nav(0); continue; }

        switch (ui_input_key(&s_in, ev.code)) {
        case UI_INPUT_CHANGED:
            ui_input_draw(s_ui, &s_in);
            ui_flush(s_ui);
            break;

        case UI_INPUT_CANCEL:
            goto done;

        case UI_INPUT_SUBMIT: {
            if (s_in.len == 0) break;

            char echo[MAXCOLS + 4];
            snprintf(echo, sizeof(echo), "$ %s", s_inbuf);
            sh_out(echo);

            char cmd[INPUT_MAX + 1];
            memcpy(cmd, s_inbuf, (size_t)s_in.len + 1);
            hist_push(cmd);
            s_hist_nav = -1;
            ui_input_clear(&s_in);

            if      (strcmp(cmd, "exit")  == 0) goto done;
            else if (strcmp(cmd, "clear") == 0) ui_textview_clear(&s_tv);
            else                                exec_line(cmd);

            sh_flush();   /* push any trailing partial row from streamed output */
            redraw_all();
            break;
        }

        case UI_INPUT_NONE:
            break;
        }
    }

done:
    ui_destroy(s_ui);
    gfx_close(s_gfx);
    close(s_input);
    duneos_exit(0);
}
