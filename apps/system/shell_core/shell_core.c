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

/* Write a chunk to the terminal, translating \n → \r\n. Allocation-free: it
 * buffers into a small stack window and flushes as that fills, so it doubles as
 * the captured-output stream sink (called repeatedly with arbitrary chunks). */
static void sh_write(const char *data, int len)
{
    char buf[128];
    int  o = 0;
    char prev = 0;
    for (int i = 0; i < len; i++) {
        char c = data[i];
        if (o >= (int)sizeof(buf) - 2) { raw_write(buf, o); o = 0; }
        if (c == '\n' && prev != '\r') buf[o++] = '\r';
        buf[o++] = c;
        prev = c;
    }
    if (o) raw_write(buf, o);
}

static void sh_out(const char *s)
{
    if (s && *s) sh_write(s, (int)strlen(s));
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

/* Build the prompt string. It carries no escape codes, so its on-screen width
 * equals strlen() — which the editor relies on when repainting. */
static void build_prompt(char *out, size_t sz)
{
    snprintf(out, sz, "[%s]" SHELL_PROMPT, s_cwd);
}

/* Defined in shell_cmds.c (included below — same translation unit). */
static int         shell_completions(const char *line, int len, int *tok_start, int *base_off);
static int         shell_comp_lcp(char *out, int outsz);
static const char *shell_comp_name(int i, int *isdir);

/* Repaint the whole edit line in place: carriage-return to column 0, clear to
 * end of line, write prompt + buffer, then park the cursor at `pos`. Single-row
 * editor (shell commands are short); a line long enough to wrap the terminal
 * width can leave artefacts — an accepted trade for not tracking screen rows. */
static void refresh_line(const char *prompt, const char *buf, int n, int pos)
{
    raw_write("\r\x1b[K", 4);
    raw_write(prompt, (int)strlen(prompt));
    raw_write(buf, n);
    int back = n - pos;
    if (back > 0) {
        char seq[16];
        int l = snprintf(seq, sizeof(seq), "\x1b[%dD", back);
        raw_write(seq, l);
    }
}

/* Pull the next byte of an escape sequence. An arrow key's bytes arrive in one
 * RX packet, so they're already buffered when we get here; a single read keeps
 * a bare ESC keypress from stalling on the fd's read timeout. */
static int esc_byte(void)
{
    char ch;
    return (read(s_fd_r, &ch, 1) > 0) ? (unsigned char)ch : -1;
}

/* Tab completion at the cursor: complete the token ending at *pp, inserting the
 * longest common prefix (and a separator if the match is unique); a second Tab
 * with nothing left to add lists the choices. Mutates buf, n and pos; repaints. */
static void complete_line(const char *prompt, char *buf, int *np, int *pp, int max)
{
    int n = *np, pos = *pp;
    int tok_start, base_off;
    int cnt = shell_completions(buf, pos, &tok_start, &base_off);
    if (cnt <= 0) return;

    int typed = pos - (tok_start + base_off);
    char lcp[128];
    int  lcl = shell_comp_lcp(lcp, sizeof(lcp));
    int  add = lcl - typed;

    char ins[130];
    int  ilen = 0;
    for (int k = 0; k < add && ilen < (int)sizeof(ins) - 1; k++) ins[ilen++] = lcp[typed + k];
    if (cnt == 1) {
        int isdir = 0;
        shell_comp_name(0, &isdir);
        if (ilen < (int)sizeof(ins) - 1) ins[ilen++] = isdir ? '/' : ' ';
    }

    if (ilen > 0 && n + ilen < max - 1) {
        memmove(buf + pos + ilen, buf + pos, n - pos);   /* open a gap       */
        memcpy(buf + pos, ins, ilen);                    /* drop the text in */
        n += ilen; pos += ilen;
        buf[n] = '\0';
        *np = n; *pp = pos;
    }

    if (cnt > 1 && add <= 0) {
        raw_write("\r\n", 2);
        for (int i = 0; i < cnt; i++) {
            int isdir = 0;
            const char *nm = shell_comp_name(i, &isdir);
            raw_write(nm, (int)strlen(nm));
            if (isdir) raw_write("/", 1);
            raw_write("  ", 2);
        }
        raw_write("\r\n", 2);
    }
    refresh_line(prompt, buf, n, pos);
}

/* ----- readline-style line editor ---------------------------------------- */

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

/* Replace the line with a history entry and move the cursor to its end. */
static void load_history(char *buf, int max, int idx, int *np, int *pp)
{
    strncpy(buf, s_history[idx % HISTORY_DEPTH], max - 1);
    buf[max - 1] = '\0';
    *np = *pp = (int)strlen(buf);
}

/*
 * read_line: blocking readline-style editor (emacs key bindings).
 * Prints `prompt`, edits in place, and returns the line length on Enter
 * (0 on an empty line or Ctrl-C). The buffer is always NUL-terminated.
 *
 * Bindings: ← →, Ctrl-B/F  move by char;  Ctrl-A/E  home/end;
 * Backspace / Ctrl-H  delete left;  Ctrl-D / Del  delete right;
 * Ctrl-K kill to EOL;  Ctrl-U kill to start;  Ctrl-W kill word;
 * Ctrl-L clear screen;  ↑ ↓ history;  Tab completion;  Ctrl-C cancel.
 */
static int read_line(char *buf, int max, const char *prompt)
{
    int n = 0, pos = 0;
    s_hist_nav = -1;
    buf[0] = '\0';
    refresh_line(prompt, buf, n, pos);

    int idle_ticks = 0;
    const int IDLE_REPRMT = 50;   /* 50 × ~100 ms ≈ 5 s */
    extern int usleep(unsigned int);

    for (;;) {
        char c;
        int r = read(s_fd_r, &c, 1);
        if (r <= 0) {
            if (n == 0 && ++idle_ticks >= IDLE_REPRMT) return 0;  /* late host: reprint */
            usleep(1000);
            continue;
        }
        idle_ticks = 0;

        if (c == '\r' || c == '\n') { raw_write("\r\n", 2); buf[n] = '\0'; return n; }
        if (c == '\x03') { raw_write("^C\r\n", 4); buf[0] = '\0'; return 0; }  /* Ctrl-C */
        if (c == '\t')   { complete_line(prompt, buf, &n, &pos, max); continue; }

        if (c == 0x01) { pos = 0; refresh_line(prompt, buf, n, pos); continue; }            /* Ctrl-A */
        if (c == 0x05) { pos = n; refresh_line(prompt, buf, n, pos); continue; }            /* Ctrl-E */
        if (c == 0x02) { if (pos > 0) pos--; refresh_line(prompt, buf, n, pos); continue; } /* Ctrl-B */
        if (c == 0x06) { if (pos < n) pos++; refresh_line(prompt, buf, n, pos); continue; } /* Ctrl-F */

        if (c == 0x7f || c == 0x08) {        /* Backspace / Ctrl-H: delete left */
            if (pos > 0) {
                memmove(buf + pos - 1, buf + pos, n - pos);
                n--; pos--; buf[n] = '\0';
                refresh_line(prompt, buf, n, pos);
            }
            continue;
        }
        if (c == 0x04) {                     /* Ctrl-D: delete under cursor (no EOF) */
            if (pos < n) {
                memmove(buf + pos, buf + pos + 1, n - pos - 1);
                n--; buf[n] = '\0';
                refresh_line(prompt, buf, n, pos);
            }
            continue;
        }
        if (c == 0x0b) {                     /* Ctrl-K: kill to end of line */
            if (pos < n) { n = pos; buf[n] = '\0'; refresh_line(prompt, buf, n, pos); }
            continue;
        }
        if (c == 0x15) {                     /* Ctrl-U: kill to start of line */
            if (pos > 0) {
                memmove(buf, buf + pos, n - pos);
                n -= pos; pos = 0; buf[n] = '\0';
                refresh_line(prompt, buf, n, pos);
            }
            continue;
        }
        if (c == 0x17) {                     /* Ctrl-W: kill previous word */
            int end = pos;
            while (pos > 0 && buf[pos - 1] == ' ') pos--;
            while (pos > 0 && buf[pos - 1] != ' ') pos--;
            if (end > pos) {
                memmove(buf + pos, buf + end, n - end);
                n -= (end - pos); buf[n] = '\0';
                refresh_line(prompt, buf, n, pos);
            }
            continue;
        }
        if (c == 0x0c) {                     /* Ctrl-L: clear screen, redraw at top */
            raw_write("\x1b[H\x1b[2J", 7);
            refresh_line(prompt, buf, n, pos);
            continue;
        }

        if (c == '\x1b') {                   /* arrows, Home/End, Delete */
            int a = esc_byte();
            if (a != '[' && a != 'O') continue;
            int b = esc_byte();
            if (b < 0) continue;
            if (b >= '0' && b <= '9') {       /* extended: ESC [ <num> ~ */
                int num = b - '0', d;
                while ((d = esc_byte()) >= '0' && d <= '9') num = num * 10 + (d - '0');
                if      (num == 3 && pos < n) {                       /* Delete */
                    memmove(buf + pos, buf + pos + 1, n - pos - 1);
                    n--; buf[n] = '\0';
                }
                else if (num == 1 || num == 7) pos = 0;               /* Home */
                else if (num == 4 || num == 8) pos = n;               /* End  */
                refresh_line(prompt, buf, n, pos);
                continue;
            }
            switch (b) {
            case 'C': if (pos < n) pos++; break;                      /* → */
            case 'D': if (pos > 0) pos--; break;                      /* ← */
            case 'H': pos = 0; break;                                 /* Home */
            case 'F': pos = n; break;                                 /* End  */
            case 'A':                                                 /* ↑ history */
                if (s_hist_count == 0) break;
                {
                    int next = (s_hist_nav < 0) ? s_hist_count - 1 : s_hist_nav - 1;
                    if (next < s_hist_count - HISTORY_DEPTH) next = s_hist_count - HISTORY_DEPTH;
                    if (next < 0) next = 0;
                    s_hist_nav = next;
                    load_history(buf, max, s_hist_nav, &n, &pos);
                }
                break;
            case 'B':                                                 /* ↓ history */
                if (s_hist_nav < 0) break;
                if (++s_hist_nav >= s_hist_count) { s_hist_nav = -1; buf[0] = '\0'; n = pos = 0; }
                else load_history(buf, max, s_hist_nav, &n, &pos);
                break;
            default: break;
            }
            refresh_line(prompt, buf, n, pos);
            continue;
        }

        if (c >= 0x20 && c < 0x7f && n < max - 1) {   /* printable: insert at cursor */
            memmove(buf + pos + 1, buf + pos, n - pos);
            buf[pos] = c;
            n++; pos++; buf[n] = '\0';
            refresh_line(prompt, buf, n, pos);
        }
    }
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
     * Loop for 20 × CDC_READ_TIMEOUT_MS (≈2 s) so boot-time log fragments
     * that arrive up to ~1.5 s after USB enumeration are silently discarded.
     * Only safe when the read fd has a timeout (e.g. /dev/ttyUSB0).
     * Do NOT define SHELL_FLUSH_ON_START when using blocking stdin. */
    { char d[64]; for (int _fi = 0; _fi < 20; _fi++) read(s_fd_r, d, sizeof(d)); }
#endif

    sh_outln("\r\n" SHELL_BANNER " -- type 'help' for commands");
    sh_printf("cwd: %s\r\n", s_cwd);

    char line[LINE_MAX_LEN];
    char prompt[CWD_MAX + 8];
    while (1) {
        build_prompt(prompt, sizeof(prompt));
        int n = read_line(line, sizeof(line), prompt);
        if (n <= 0) continue;   /* empty line / Ctrl-C — read_line already redrew */
        history_push(line);
        exec_line(line);
    }
}
