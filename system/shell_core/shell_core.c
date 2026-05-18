/*
 * shell_core.c — DuneOS VT100 shell engine (serial/fd backend).
 *
 * NOT a standalone app. Include from a backend, define SHELL_BANNER, then
 * call shell_run(fd):
 *
 *   #define SHELL_BANNER "DuneOS usb_shell"
 *   #include "../shell_core/shell_core.c"
 *
 * Provides sh_out/sh_outln for shell_cmds, then includes it.
 * Handles \n→\r\n translation in one buffered write (not byte-by-byte).
 */

#include <unistd.h>
#include <fcntl.h>
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <stdarg.h>

#ifndef SHELL_BANNER
#define SHELL_BANNER "DuneOS shell"
#endif

#define SHELL_PROMPT  "$ "
#define LINE_MAX_LEN  256
#define HISTORY_DEPTH 16

/* ----- state ------------------------------------------------------------- */

static char s_cwd[256] = "/sd";          /* required by shell_cmds.c */
static char s_history[HISTORY_DEPTH][LINE_MAX_LEN];
static int  s_hist_count = 0;
static int  s_hist_nav   = -1;
static int  s_fd_r       = -1;           /* read fd  (stdin or device) */
static int  s_fd_w       = -1;           /* write fd (stdout or same device) */

/* ----- I/O (sh_out / sh_outln required by shell_cmds.c) ----------------- */

static void raw_write(const char *s, int len)
{
    const char *p = s; int rem = len;
    while (rem > 0) { int n = write(s_fd_w, p, rem); if (n <= 0) break; p += n; rem -= n; }
}

/* Translate \n → \r\n in one pass, write the result in one shot. */
static void sh_out(const char *s)
{
    if (!s || !*s) return;
    size_t len = strlen(s);
    /* Upper bound: every \n becomes \r\n = at most 2× length */
    char *buf = malloc(len * 2 + 1);
    if (!buf) { raw_write(s, len); return; }
    size_t out = 0;
    for (size_t i = 0; i < len; i++) {
        if (s[i] == '\n' && (i == 0 || s[i - 1] != '\r'))
            buf[out++] = '\r';
        buf[out++] = s[i];
    }
    raw_write(buf, out);
    free(buf);
}

static void sh_outln(const char *s)
{
    sh_out(s);
    raw_write("\r\n", 2);
}

static void sh_printf(const char *fmt, ...)
{
    char buf[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    sh_out(buf);
}

/* ----- VT100 line editor ------------------------------------------------- */

static void history_push(const char *line)
{
    if (line[0] == '\0') return;
    if (s_hist_count > 0 &&
        strcmp(s_history[(s_hist_count - 1) % HISTORY_DEPTH], line) == 0)
        return;
    /* sh_strlcpy from shell_cmds.c is included after this file, so use strncpy */
    strncpy(s_history[s_hist_count % HISTORY_DEPTH], line, LINE_MAX_LEN - 1);
    s_history[s_hist_count % HISTORY_DEPTH][LINE_MAX_LEN - 1] = '\0';
    s_hist_count++;
}

/*
 * read_line: blocking VT100 line editor.
 * Returns: length of line on Enter, 0 on Ctrl-C or connection idle timeout.
 * Returning 0 lets the main loop reprint the prompt — so if the host
 * connects after boot, the prompt appears within the next timeout cycle.
 */
static int read_line(char *buf, int max)
{
    int n = 0;
    s_hist_nav = -1;

    /* idle counter: reprint prompt after ~5 s of silence so a host that
     * connects after boot sees the prompt without typing blind. */
    int idle_ticks = 0;
    const int IDLE_REPRMT = 50;  /* 50 × 100 ms = 5 s */

    while (1) {
        char c;
        int r = read(s_fd_r, &c, 1);
        if (r <= 0) {
            /* read timed out or errored — increment idle counter */
            if (n == 0 && ++idle_ticks >= IDLE_REPRMT) return 0;
            extern int usleep(unsigned int);
            usleep(1000);
            continue;
        }
        idle_ticks = 0;

        if (c == '\r' || c == '\n') {
            if (n == 0) { idle_ticks = 0; continue; }
            raw_write("\r\n", 2);
            break;
        }

        if (c == 127 || c == '\b') {
            if (n > 0) { n--; raw_write("\b \b", 3); }
            continue;
        }

        if (c == '\x1b') {
            char seq[2] = {0};
            read(s_fd_r, &seq[0], 1);
            read(s_fd_r, &seq[1], 1);
            if (seq[0] == '[') {
                int erase = n;
                if (seq[1] == 'A') { /* up */
                    int next = (s_hist_nav < 0) ? s_hist_count - 1 : s_hist_nav - 1;
                    if (next < 0 || s_hist_count == 0) continue;
                    if (next < s_hist_count - HISTORY_DEPTH)
                        next = s_hist_count - HISTORY_DEPTH;
                    s_hist_nav = next;
                    while (erase--) raw_write("\b \b", 3);
                    strncpy(buf, s_history[s_hist_nav % HISTORY_DEPTH], max - 1);
                    buf[max - 1] = '\0';
                    n = (int)strlen(buf);
                    raw_write(buf, n);
                } else if (seq[1] == 'B') { /* down */
                    if (s_hist_nav < 0) continue;
                    while (erase--) raw_write("\b \b", 3);
                    int next = s_hist_nav + 1;
                    if (next >= s_hist_count) {
                        s_hist_nav = -1; buf[0] = '\0'; n = 0;
                    } else {
                        s_hist_nav = next;
                        strncpy(buf, s_history[s_hist_nav % HISTORY_DEPTH], max - 1);
                        buf[max - 1] = '\0';
                        n = (int)strlen(buf);
                        raw_write(buf, n);
                    }
                }
            }
            continue;
        }

        if (c == '\x03') { raw_write("^C\r\n", 4); buf[0] = '\0'; return 0; }

        if (c >= 0x20 && c < 0x7f && n < max - 1) {
            buf[n++] = c;
            write(s_fd_w, &c, 1);
        }
    }
    buf[n] = '\0';
    return n;
}

/* ----- include shared command dispatch ----------------------------------- */

#include "shell_cmds.c"

/* ----- main loop --------------------------------------------------------- */

/* fd_r: read fd (STDIN_FILENO or a device fd).
 * fd_w: write fd (STDOUT_FILENO or same device fd for bidirectional devices). */
static void shell_run(int fd_r, int fd_w)
{
    s_fd_r = fd_r;
    s_fd_w = fd_w;

#ifdef SHELL_FLUSH_ON_START
    /* Drain stale RX bytes buffered before the shell started.
     * Only safe when the read fd has a timeout (e.g. /dev/ttyUSB0).
     * Do NOT define SHELL_FLUSH_ON_START when using blocking stdin. */
    { char d[64]; while (read(s_fd_r, d, sizeof(d)) > 0); }
#endif

    sh_outln("\r\n" SHELL_BANNER " -- type 'help' for commands");
    sh_printf("cwd: %s\r\n", s_cwd);

    char line[LINE_MAX_LEN];
    while (1) {
        sh_printf("[%s]" SHELL_PROMPT, s_cwd);
        int n = read_line(line, sizeof(line));
        if (n < 0) break;
        if (n == 0) { raw_write("\r\033[2K", 5); continue; } /* erase stale prompt, reprint */
        history_push(line);
        exec_line(line);
    }
}
