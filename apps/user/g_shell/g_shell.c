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

/* ----- output buffer (required by shell_cmds.c) -------------------------- */

static void out_text(const char *text)
{
    char line[MAXCOLS + 1];
    int  col = 0;
    for (const char *p = text; *p; p++) {
        if (*p == '\r') continue;
        if (*p == '\n' || col == s_cols) {
            line[col] = '\0'; ui_textview_push(&s_tv, line); col = 0;
            if (*p == '\n') continue;
        }
        line[col++] = *p;
    }
    if (col > 0) { line[col] = '\0'; ui_textview_push(&s_tv, line); }
}

static void sh_out(const char *s)   { out_text(s); }
static void sh_outln(const char *s) { out_text(s); ui_textview_push(&s_tv, ""); }

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
            out_text(echo);

            char cmd[INPUT_MAX + 1];
            memcpy(cmd, s_inbuf, (size_t)s_in.len + 1);
            ui_input_clear(&s_in);

            if      (strcmp(cmd, "exit")  == 0) goto done;
            else if (strcmp(cmd, "clear") == 0) ui_textview_clear(&s_tv);
            else                                exec_line(cmd);

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
