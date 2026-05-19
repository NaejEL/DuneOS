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

    /* Lines 3…: argv entries */
    for (int i = 0; i < argc && i < maxargv; i++) {
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
