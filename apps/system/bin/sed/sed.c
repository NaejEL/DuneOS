/*
 * sed — minimal stream editor (ADR 036 regex). A practical subset:
 *
 *   sed [-n] 's/re/repl/[g][p]' [FILE...]   substitute (repl: & = match, \& literal)
 *   sed [-n] '/re/d'            [FILE...]   delete matching lines
 *   sed [-n] '/re/p'            [FILE...]   print matching lines (grep-like with -n)
 *
 * The delimiter after `s` may be any char (s|a|b|). No addresses/ranges, no
 * hold space — reach for the scripting interpreter (ADR 035) for more.
 */

#include <unistd.h>
#include <fcntl.h>
#include <string.h>
#include <stdio.h>
#include <errno.h>

#include <duneos/bin_args.h>
#include <duneos/re.h>

#define LINE_MAX 256
#define USAGE    "usage: sed [-n] 's/re/repl/[g][p]' | '/re/d' | '/re/p'  [FILE...]"

static int  g_nflag, g_global, g_pflag;
static char g_cmd;                       /* 's' | 'd' | 'p' */
static char g_repl[256];

static void emit(const char *s) { write(STDOUT_FILENO, s, strlen(s)); }
static void emitln(const char *s) { emit(s); emit("\n"); }

typedef struct { int fd; char buf[256]; int len, pos; } lr_t;
static int lr_getline(lr_t *r, char *out, int max)
{
    int o = 0;
    for (;;) {
        if (r->pos >= r->len) {
            r->len = (int)read(r->fd, r->buf, sizeof(r->buf));
            r->pos = 0;
            if (r->len <= 0) { if (o > 0) { out[o] = '\0'; return o; } return -1; }
        }
        char c = r->buf[r->pos++];
        if (c == '\r') continue;
        if (c == '\n') { out[o] = '\0'; return o; }
        if (o < max - 1) out[o++] = c;
    }
}

/* Copy from *s up to (unescaped) delim into out; \delim becomes a literal delim,
 * other escapes pass through (so \d etc. survive into the regex). Returns the
 * cursor just past the delim. */
static const char *copy_until(const char *s, char delim, char *out, int outsz)
{
    int o = 0;
    while (*s && *s != delim) {
        if (*s == '\\' && s[1] == delim) { if (o < outsz - 1) out[o++] = delim; s += 2; continue; }
        if (o < outsz - 1) out[o++] = *s;
        s++;
    }
    out[o] = '\0';
    return (*s == delim) ? s + 1 : s;
}

/* Apply s/re/repl/ to `line` into `out`. Returns 1 if any substitution happened. */
static int subst(const char *line, re_t prog, char *out, int outsz)
{
    const char *s = line;
    int o = 0, made = 0;
    for (;;) {
        int len, pos = re_matchp(prog, s, &len);
        if (pos < 0) break;
        for (int i = 0; i < pos && o < outsz - 1; i++) out[o++] = s[i];   /* pre-match */
        for (const char *r = g_repl; *r; r++) {                           /* replacement */
            if (*r == '\\' && r[1]) { r++; if (o < outsz - 1) out[o++] = *r; }
            else if (*r == '&') { for (int i = 0; i < len && o < outsz - 1; i++) out[o++] = s[pos + i]; }
            else if (o < outsz - 1) out[o++] = *r;
        }
        made = 1;
        s += pos + len;
        if (!g_global) break;
        if (len == 0) { if (*s && o < outsz - 1) out[o++] = *s, s++; else break; }
    }
    for (; *s && o < outsz - 1; s++) out[o++] = *s;   /* tail */
    out[o] = '\0';
    return made;
}

static void run_fd(int fd, re_t prog)
{
    lr_t r = { .fd = fd, .len = 0, .pos = 0 };
    char line[LINE_MAX], work[LINE_MAX];
    while (lr_getline(&r, line, sizeof(line)) >= 0) {
        if (g_cmd == 's') {
            int made = subst(line, prog, work, sizeof(work));
            if (made && g_pflag) emitln(work);
            if (!g_nflag) emitln(work);
        } else {
            int hit = (re_matchp(prog, line, &(int){0}) >= 0);
            if (g_cmd == 'd') { if (!hit && !g_nflag) emitln(line); }
            else /* 'p' */    { if (hit) emitln(line); if (!g_nflag) emitln(line); }
        }
    }
}

void app_main(void)
{
    char ab[DUNEOS_EXEC_ARGS_BUF_SIZE];
    char *argv[12]; char *cwd;
    int argc = duneos_bin_args(ab, sizeof(ab), &cwd, argv, 12);

    duneos_opts_t o;
    if (duneos_bin_getopts(argc, argv, "n", &o) != 0)
        { duneos_bin_badopt("sed", o.bad, USAGE); return; }
    g_nflag = duneos_opt(&o, 'n');

    int ai = o.argi;
    if (ai >= argc) { duneos_bin_err("sed", USAGE); return; }

    const char *script = argv[ai++];
    char rebuf[160];

    if (script[0] == 's') {
        char d = script[1];
        if (!d) { duneos_bin_err("sed", "bad s command"); return; }
        const char *sp = copy_until(script + 2, d, rebuf, sizeof(rebuf));
        sp = copy_until(sp, d, g_repl, sizeof(g_repl));
        for (; *sp; sp++) { if (*sp == 'g') g_global = 1; else if (*sp == 'p') g_pflag = 1; }
        g_cmd = 's';
    } else if (script[0] == '/') {
        const char *sp = copy_until(script + 1, '/', rebuf, sizeof(rebuf));
        g_cmd = *sp;
        if (g_cmd != 'd' && g_cmd != 'p') { duneos_bin_err("sed", "expected /re/d or /re/p"); return; }
    } else {
        duneos_bin_err("sed", USAGE);
        return;
    }

    re_t prog = re_compile(rebuf);
    if (!prog) { duneos_bin_err("sed", "bad pattern"); return; }

    if (ai >= argc) { run_fd(STDIN_FILENO, prog); return; }
    for (; ai < argc; ai++) {
        char path[256];
        duneos_bin_resolve(argv[ai], cwd, path, sizeof(path));
        int fd = open(path, O_RDONLY);
        if (fd < 0) { char m[300]; snprintf(m, sizeof(m), "%s: %s", path, strerror(errno)); duneos_bin_err("sed", m); continue; }
        run_fd(fd, prog);
        close(fd);
    }
}
