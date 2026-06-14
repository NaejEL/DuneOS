/*
 * df — report filesystem total / used / free.
 *
 *   df [-h] [PATH...]
 *     -h  human-readable sizes
 *
 * With no PATH, reports every mount that has block accounting (/flash, /sd).
 * /tmp and /dev have none and are skipped.
 */

#include <unistd.h>
#include <string.h>
#include <stdio.h>
#include <stdint.h>

#include <duneos/bin_args.h>

extern int duneos_vfs_list_mounts(char (*out)[32], int max);
extern int duneos_fs_info(const char *path, uint64_t *total, uint64_t *freeb);

static int g_h;

static void human(uint64_t b, char *o, int osz)
{
    const char *u = "BKMGT";
    int i = 0;
    uint64_t n = b, div = 1;
    while (n >= 1024 && u[i + 1]) { n /= 1024; div *= 1024; i++; }
    if (i == 0) { snprintf(o, osz, "%llu", (unsigned long long)b); return; }
    unsigned long long whole = b / div, tenth = (b * 10 / div) % 10;
    if (whole < 10) snprintf(o, osz, "%llu.%llu%c", whole, tenth, u[i]);
    else            snprintf(o, osz, "%llu%c", whole, u[i]);
}

static void show(const char *mount)
{
    uint64_t total = 0, freeb = 0;
    if (duneos_fs_info(mount, &total, &freeb) != 0) return;   /* no block fs */
    uint64_t used = (total > freeb) ? total - freeb : 0;

    char st[20], su[20], sa[20];
    if (g_h) { human(total, st, sizeof(st)); human(used, su, sizeof(su)); human(freeb, sa, sizeof(sa)); }
    else {
        snprintf(st, sizeof(st), "%llu", (unsigned long long)total);
        snprintf(su, sizeof(su), "%llu", (unsigned long long)used);
        snprintf(sa, sizeof(sa), "%llu", (unsigned long long)freeb);
    }
    char out[160];
    int  n = snprintf(out, sizeof(out), "%-10s %10s %10s %10s\n", mount, st, su, sa);
    write(STDOUT_FILENO, out, (size_t)n);
}

void app_main(void)
{
    char ab[DUNEOS_EXEC_ARGS_BUF_SIZE];
    char *argv[16]; char *cwd;
    int argc = duneos_bin_args(ab, sizeof(ab), &cwd, argv, 16);

    duneos_opts_t o;
    if (duneos_bin_getopts(argc, argv, "h", &o) != 0) { duneos_bin_badopt("df", o.bad, "usage: df [-h] [PATH...]"); return; }
    g_h = duneos_opt(&o, 'h');

    char hdr[80];
    int  hn = snprintf(hdr, sizeof(hdr), "%-10s %10s %10s %10s\n", "Mounted", "Size", "Used", "Avail");
    write(STDOUT_FILENO, hdr, (size_t)hn);

    if (o.argi >= argc) {
        char mounts[8][32];
        int n = duneos_vfs_list_mounts(mounts, 8);
        for (int i = 0; i < n; i++) show(mounts[i]);
    } else {
        for (int i = o.argi; i < argc; i++) {
            char path[256];
            duneos_bin_resolve(argv[i], cwd, path, sizeof(path));
            show(path);
        }
    }
}
