/*
 * du — summarise disk usage of a file tree.
 *
 *   du [-h] [PATH]
 *     -h  human-readable sizes (1.0K, 4M…)
 *
 * Prints the cumulative byte total of PATH (default cwd) — `du -s` style.
 * Integer math only (bins have no soft-float); recursion depth-capped.
 */

#include <unistd.h>
#include <string.h>
#include <stdio.h>
#include <sys/stat.h>
#include <dirent.h>
#include <errno.h>

#include <duneos/bin_args.h>

#define PLEN     512
#define MAXDEPTH 16

static int g_h;

static void human(long b, char *o, int osz)
{
    const char *u = "BKMGT";
    int  i = 0;
    long n = b, div = 1;
    while (n >= 1024 && u[i + 1]) { n /= 1024; div *= 1024; i++; }
    if (i == 0) { snprintf(o, osz, "%ld", b); return; }
    long whole = b / div, tenth = (b * 10 / div) % 10;
    if (whole < 10) snprintf(o, osz, "%ld.%ld%c", whole, tenth, u[i]);
    else            snprintf(o, osz, "%ld%c", whole, u[i]);
}

static long du_tree(char *path, int len, int depth)
{
    DIR *d = opendir(path);
    if (!d) { struct stat st; return (stat(path, &st) == 0) ? (long)st.st_size : 0; }

    long total = 0;
    struct dirent *e;
    while ((e = readdir(d)) != NULL) {
        if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, "..")) continue;
        int n = snprintf(path + len, PLEN - len, "/%s", e->d_name);
        if (len + n < PLEN) {
            struct stat st;
            if (stat(path, &st) == 0) {
                if (S_ISDIR(st.st_mode)) { if (depth < MAXDEPTH) total += du_tree(path, len + n, depth + 1); }
                else total += (long)st.st_size;
            }
        }
        path[len] = '\0';
    }
    closedir(d);
    return total;
}

void app_main(void)
{
    char ab[DUNEOS_EXEC_ARGS_BUF_SIZE];
    char *argv[16]; char *cwd;
    int argc = duneos_bin_args(ab, sizeof(ab), &cwd, argv, 16);

    duneos_opts_t o;
    if (duneos_bin_getopts(argc, argv, "h", &o) != 0) { duneos_bin_badopt("du", o.bad, "usage: du [-h] [PATH]"); return; }
    g_h = duneos_opt(&o, 'h');

    const char *target = (o.argi < argc) ? argv[o.argi] : ".";
    char path[PLEN];
    duneos_bin_resolve(target, cwd, path, sizeof(path));

    struct stat st;
    if (stat(path, &st) != 0) { char m[300]; snprintf(m, sizeof(m), "%s: %s", target, strerror(errno)); duneos_bin_err("du", m); return; }

    long total = du_tree(path, (int)strlen(path), 0);
    char sz[16];
    if (g_h) human(total, sz, sizeof(sz));
    else     snprintf(sz, sizeof(sz), "%ld", total);

    char out[320];
    int  n = snprintf(out, sizeof(out), "%s\t%s\n", sz, target);
    write(STDOUT_FILENO, out, (size_t)n);
}
