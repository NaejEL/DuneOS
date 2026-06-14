/*
 * rm — remove files and directories.
 *
 *   rm [-rf] FILE...
 *     -r  remove directories and their contents recursively
 *     -f  ignore nonexistent operands, never error
 *
 * Path threaded through one stack buffer (captured bin); recursion depth-capped.
 */

#include <unistd.h>
#include <string.h>
#include <stdio.h>
#include <sys/stat.h>
#include <dirent.h>
#include <errno.h>

#include <duneos/bin_args.h>

#define USAGE    "usage: rm [-rf] FILE..."
#define PLEN     512
#define MAXDEPTH 16

static int g_r, g_f;

static int rm_tree(char *path, int len, int depth)
{
    if (depth >= MAXDEPTH) { duneos_bin_err("rm", "directory tree too deep"); return -1; }
    DIR *d = opendir(path);
    if (!d) return -1;
    struct dirent *e; int rc = 0;
    while ((e = readdir(d)) != NULL) {
        if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, "..")) continue;
        int n = snprintf(path + len, PLEN - len, "/%s", e->d_name);
        if (len + n < PLEN) {
            struct stat st;
            if (stat(path, &st) == 0 && S_ISDIR(st.st_mode)) { rm_tree(path, len + n, depth + 1); rmdir(path); }
            else unlink(path);
        }
        path[len] = '\0';
    }
    closedir(d);
    return rc;
}

void app_main(void)
{
    char ab[DUNEOS_EXEC_ARGS_BUF_SIZE];
    char *argv[16]; char *cwd;
    int argc = duneos_bin_args(ab, sizeof(ab), &cwd, argv, 16);

    duneos_opts_t o;
    if (duneos_bin_getopts(argc, argv, "rf", &o) != 0) { duneos_bin_badopt("rm", o.bad, USAGE); return; }
    g_r = duneos_opt(&o, 'r');
    g_f = duneos_opt(&o, 'f');

    if (o.argi >= argc) { if (!g_f) duneos_bin_err("rm", USAGE); return; }

    for (int i = o.argi; i < argc; i++) {
        char path[PLEN];
        duneos_bin_resolve(argv[i], cwd, path, sizeof(path));

        struct stat st;
        if (stat(path, &st) != 0) {
            if (!g_f) { char m[300]; snprintf(m, sizeof(m), "%s: %s", argv[i], strerror(errno)); duneos_bin_err("rm", m); }
            continue;
        }
        if (S_ISDIR(st.st_mode)) {
            if (!g_r) { char m[300]; snprintf(m, sizeof(m), "%s: is a directory (use -r)", argv[i]); duneos_bin_err("rm", m); continue; }
            rm_tree(path, (int)strlen(path), 0);
            if (rmdir(path) != 0 && !g_f) { char m[300]; snprintf(m, sizeof(m), "%s: %s", argv[i], strerror(errno)); duneos_bin_err("rm", m); }
        } else {
            if (unlink(path) != 0 && !g_f) { char m[300]; snprintf(m, sizeof(m), "%s: %s", argv[i], strerror(errno)); duneos_bin_err("rm", m); }
        }
    }
}
