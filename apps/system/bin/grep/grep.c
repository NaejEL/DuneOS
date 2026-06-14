/*
 * grep — print lines matching a regular expression (ADR 036 engine).
 *
 *   grep [-vnci] PATTERN [FILE...]
 *     -v  invert: print non-matching lines
 *     -n  prefix each line with its line number
 *     -c  print only the count of matching lines
 *     -i  case-insensitive
 *
 * With no FILE, reads stdin (fd 0) — useful once pipelines land (ADR 034).
 */

#include <unistd.h>
#include <fcntl.h>
#include <string.h>
#include <stdio.h>
#include <ctype.h>
#include <errno.h>

#include <duneos/bin_args.h>
#include <duneos/re.h>

#define LINE_MAX 256
#define USAGE    "usage: grep [-vnci] PATTERN [FILE...]"

static int g_invert, g_number, g_count, g_icase, g_multi;

static void lower(char *s) { for (; *s; s++) *s = (char)tolower((unsigned char)*s); }

/* Minimal line reader: buffers an fd, returns one '\n'-stripped line (>=0 = len,
 * -1 = EOF). '\r' is dropped so CRLF files match cleanly. */
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

static void grep_fd(int fd, const char *name, re_t prog)
{
    lr_t r = { .fd = fd, .len = 0, .pos = 0 };
    char line[LINE_MAX], low[LINE_MAX];
    int  lineno = 0, matches = 0;

    while (lr_getline(&r, line, sizeof(line)) >= 0) {
        lineno++;
        const char *subj = line;
        if (g_icase) { snprintf(low, sizeof(low), "%s", line); lower(low); subj = low; }

        int len;
        int hit = (re_matchp(prog, subj, &len) >= 0);
        if (hit == g_invert) continue;          /* hit!=invert → print */

        matches++;
        if (g_count) continue;

        /* Write directly (no big assembly buffer — these bins run on the shell's
         * stack when captured, so locals are kept lean). */
        if (name) { write(STDOUT_FILENO, name, strlen(name)); write(STDOUT_FILENO, ":", 1); }
        if (g_number) {
            char p[16];
            int  pn = snprintf(p, sizeof(p), "%d:", lineno);
            write(STDOUT_FILENO, p, (size_t)pn);
        }
        write(STDOUT_FILENO, line, strlen(line));
        write(STDOUT_FILENO, "\n", 1);
    }

    if (g_count) {
        char out[300];
        int  n = 0;
        if (name) n += snprintf(out + n, sizeof(out) - n, "%s:", name);
        n += snprintf(out + n, sizeof(out) - n, "%d\n", matches);
        write(STDOUT_FILENO, out, (size_t)n);
    }
}

void app_main(void)
{
    char ab[DUNEOS_EXEC_ARGS_BUF_SIZE];
    char *argv[12]; char *cwd;
    int argc = duneos_bin_args(ab, sizeof(ab), &cwd, argv, 12);

    duneos_opts_t o;
    if (duneos_bin_getopts(argc, argv, "vnci", &o) != 0)
        { duneos_bin_badopt("grep", o.bad, USAGE); return; }
    g_invert = duneos_opt(&o, 'v');
    g_number = duneos_opt(&o, 'n');
    g_count  = duneos_opt(&o, 'c');
    g_icase  = duneos_opt(&o, 'i');

    int ai = o.argi;
    if (ai >= argc) { duneos_bin_err("grep", USAGE); return; }

    char pat[128];
    snprintf(pat, sizeof(pat), "%s", argv[ai++]);
    if (g_icase) lower(pat);
    re_t prog = re_compile(pat);
    if (!prog) { duneos_bin_err("grep", "bad pattern"); return; }

    int nfiles = argc - ai;
    g_multi = nfiles > 1;

    if (nfiles == 0) {
        grep_fd(STDIN_FILENO, NULL, prog);
        return;
    }
    for (; ai < argc; ai++) {
        char path[256];
        duneos_bin_resolve(argv[ai], cwd, path, sizeof(path));
        int fd = open(path, O_RDONLY);
        if (fd < 0) {
            char m[300];
            snprintf(m, sizeof(m), "%s: %s", path, strerror(errno));
            duneos_bin_err("grep", m);
            continue;
        }
        grep_fd(fd, g_multi ? argv[ai] : NULL, prog);
        close(fd);
    }
}
