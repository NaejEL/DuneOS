#pragma once

/*
 * bin_args.h — argument passing for apps/system/bin/ utility apps.
 *
 * Convention for /sd/bin/<cmd>.dap apps launched by the shell PATH fallback:
 *
 *   1. Shell writes /tmp/.exec_args before calling duneos_loader_run().
 *   2. Bin app calls duneos_bin_args() in app_main() to retrieve argc/argv/cwd.
 *   3. Bin app returns from app_main() (NEVER calls duneos_exit()).
 *      Calling duneos_exit() from a synchronously-run app deletes the shell task.
 *
 * File format (/tmp/.exec_args):
 *   Line 1: current working directory
 *   Line 2: argc (decimal)
 *   Lines 3…N: one argv[] entry per line
 *
 * Args cannot contain newlines — true for any shell-parsed input.
 */

#include <fcntl.h>
#include <unistd.h>
#include <string.h>
#include <stdlib.h>

#define DUNEOS_EXEC_ARGS_PATH     "/tmp/.exec_args"
#define DUNEOS_EXEC_ARGS_BUF_SIZE 512

/*
 * Read exec args into caller-supplied buffer.
 * buf must be DUNEOS_EXEC_ARGS_BUF_SIZE bytes.
 * Sets *cwd_out to point into buf (default "/sd" on failure).
 * Fills argv[0..argc-1] with pointers into buf.
 * Returns argc (0 on failure).
 */
static inline int duneos_bin_args(char *buf, int bufsz,
                                   char **cwd_out,
                                   char **argv, int maxargv)
{
    if (cwd_out) *cwd_out = (char *)"/sd";

    int fd = open(DUNEOS_EXEC_ARGS_PATH, O_RDONLY);
    if (fd < 0) return 0;
    int n = (int)read(fd, buf, bufsz - 1);
    close(fd);
    if (n <= 0) return 0;
    buf[n] = '\0';

    char *p = buf;
    char *eol;

    /* Line 1: cwd */
    eol = strchr(p, '\n');
    if (!eol) return 0;
    *eol = '\0';
    if (cwd_out) *cwd_out = p;
    p = eol + 1;

    /* Line 2: argc */
    eol = strchr(p, '\n');
    if (!eol) return 0;
    *eol = '\0';
    int argc = atoi(p);
    p = eol + 1;

    /* Lines 3…: argv entries. Cap at maxargv — only that many argv[] slots were
     * filled, so the caller must never see a larger argc (it would index past
     * its argv[] array). Triggered once shell globbing yields many arguments. */
    if (argc > maxargv) argc = maxargv;
    for (int i = 0; i < argc; i++) {
        eol = strchr(p, '\n');
        if (eol) *eol = '\0';
        argv[i] = p;
        if (eol) p = eol + 1;
    }
    return argc;
}

/* Resolve a path argument against cwd (like the shell's resolve_path). */
static inline void duneos_bin_resolve(const char *arg, const char *cwd,
                                       char *out, int outsz)
{
    if (arg[0] == '/') {
        snprintf(out, outsz, "%s", arg);
    } else {
        snprintf(out, outsz, "%s/%s", cwd, arg);
    }
}

/* -------------------------------------------------------------------------
 * Shared option parsing — uniform single-char flags across every bin.
 *
 * Supports bundled flags (-lah == -l -a -h), "--" to end option parsing, and
 * unknown-flag detection. Option *arguments* (-n 5) are not handled here — the
 * few bins that need a value read the following argv[] entry by hand.
 * ---------------------------------------------------------------------- */

typedef struct {
    unsigned char set[128];   /* set[c]=1 when flag -c was given */
    int           argi;       /* argv index of the first non-flag (or argc) */
    char          bad;        /* offending flag on error, else 0 */
} duneos_opts_t;

/* Parse argv[1..] for the single-char flags listed in `valid`. Returns 0 on
 * success (opts->argi = first positional), -1 on an unknown flag (opts->bad). */
static inline int duneos_bin_getopts(int argc, char **argv, const char *valid,
                                     duneos_opts_t *opts)
{
    memset(opts->set, 0, sizeof(opts->set));
    opts->bad = 0;
    int i = 1;
    for (; i < argc; i++) {
        const char *a = argv[i];
        if (a[0] != '-' || a[1] == '\0') break;             /* positional / "-" */
        if (a[1] == '-' && a[2] == '\0') { i++; break; }    /* "--" ends options */
        for (const char *f = a + 1; *f; f++) {
            if (!strchr(valid, *f)) { opts->bad = *f; opts->argi = i; return -1; }
            opts->set[(unsigned char)*f & 0x7f] = 1;
        }
    }
    opts->argi = i;
    return 0;
}

static inline int duneos_opt(const duneos_opts_t *o, char c)
{
    return o->set[(unsigned char)c & 0x7f];
}

/* Uniform diagnostics on stdout (the shell's captured stream). */
static inline void duneos_bin_err(const char *cmd, const char *msg)
{
    char b[192];
    int n = snprintf(b, sizeof(b), "%s: %s\r\n", cmd, msg);
    if (n > 0) write(STDOUT_FILENO, b, (size_t)n);
}

static inline void duneos_bin_badopt(const char *cmd, char bad, const char *usage)
{
    char b[192];
    int n = snprintf(b, sizeof(b), "%s: invalid option -- '%c'\r\n%s\r\n",
                     cmd, bad, usage);
    if (n > 0) write(STDOUT_FILENO, b, (size_t)n);
}
