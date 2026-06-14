#include <unistd.h>
#include <fcntl.h>
#include <string.h>
#include <stdio.h>
#include <sys/stat.h>
#include <dirent.h>
#include <errno.h>
#include <stdarg.h>

#include <duneos/bin_args.h>

/* Kernel ABI: list active VFS mount points ("/flash", "/sd", "/tmp", "/dev"). */
extern int duneos_vfs_list_mounts(char (*out)[32], int max);

static void out(const char *s)           { write(STDOUT_FILENO, s, strlen(s)); }
static void outf(const char *fmt, ...)
{
    char buf[256]; va_list ap; va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap); va_end(ap); out(buf);
}

/* Render a byte count as a short human-readable string (1023, 4.0K, 12M…).
 * Integer math only — bins can't use double (the loader has no libgcc soft-float
 * helpers; __muldf3 & friends would be unresolved symbols at load time). */
static void human(long bytes, char *o, size_t osz)
{
    const char *u = "BKMGT";
    int  i = 0;
    long n = bytes, div = 1;
    while (n >= 1024 && u[i + 1]) { n /= 1024; div *= 1024; i++; }
    if (i == 0) { snprintf(o, osz, "%ld", bytes); return; }
    long whole = bytes / div;
    long tenth = (bytes * 10 / div) % 10;     /* one decimal place */
    if (whole < 10) snprintf(o, osz, "%ld.%ld%c", whole, tenth, u[i]);
    else            snprintf(o, osz, "%ld%c", whole, u[i]);
}

static void print_entry(const char *name, int is_dir, long size,
                        int long_fmt, int human_fmt)
{
    if (!long_fmt) { out(name); if (is_dir) out("/"); out("  "); return; }

    char sz[16];
    if (human_fmt) human(size, sz, sizeof(sz));
    else           snprintf(sz, sizeof(sz), "%ld", size);
    outf("%c %8s  %s%s\r\n", is_dir ? 'd' : '-', sz, name, is_dir ? "/" : "");
}

static int g_long, g_all, g_human;

static void list_dir(const char *resolved)
{
    /* Root is virtual — list mount points, not a real directory. */
    if (strcmp(resolved, "/") == 0) {
        char mounts[8][32];
        int n = duneos_vfs_list_mounts(mounts, 8);
        for (int i = 0; i < n; i++)
            print_entry(mounts[i] + 1, 1, 0, g_long, g_human);
        if (!g_long) out("\r\n");
        return;
    }

    DIR *dir = opendir(resolved);
    if (!dir) { outf("ls: %s: %s\r\n", resolved, strerror(errno)); return; }

    int shown = 0;
    struct dirent *ent;
    while ((ent = readdir(dir)) != NULL) {
        if (!g_all && ent->d_name[0] == '.') continue;   /* hide dotfiles */

        int  is_dir = 0;
        long size   = 0;
        char fpath[320];
        snprintf(fpath, sizeof(fpath), "%s/%s", resolved, ent->d_name);
        struct stat st;
        if (stat(fpath, &st) == 0) { is_dir = S_ISDIR(st.st_mode); size = (long)st.st_size; }

        print_entry(ent->d_name, is_dir, size, g_long, g_human);
        shown++;
    }
    closedir(dir);
    if (!g_long && shown) out("\r\n");
}

void app_main(void)
{
    char buf[DUNEOS_EXEC_ARGS_BUF_SIZE];
    char *argv[16]; char *cwd;
    int argc = duneos_bin_args(buf, sizeof(buf), &cwd, argv, 16);
    if (argc == 0)
        out("ls: warn: exec_args missing — using default cwd\r\n");

    const char *targets[16]; int nt = 0;
    for (int i = 1; i < argc; i++) {
        if (argv[i][0] == '-' && argv[i][1]) {
            for (const char *f = argv[i] + 1; *f; f++) {
                switch (*f) {
                case 'l': g_long = 1;  break;
                case 'a': g_all = 1;   break;
                case 'h': g_human = 1; break;
                default:
                    outf("ls: invalid option -- '%c'\r\n", *f);
                    out("usage: ls [-lah] [path...]\r\n");
                    return;
                }
            }
        } else if (nt < 16) {
            targets[nt++] = argv[i];
        }
    }

    if (nt == 0) { list_dir(cwd); return; }

    /* Each operand: a file prints its own line; a directory is listed (with a
     * "name:" header when more than one operand, like coreutils ls). */
    for (int i = 0; i < nt; i++) {
        char resolved[256];
        duneos_bin_resolve(targets[i], cwd, resolved, sizeof(resolved));

        struct stat st;
        if (strcmp(resolved, "/") != 0 && stat(resolved, &st) != 0) {
            outf("ls: %s: %s\r\n", targets[i], strerror(errno));
            continue;
        }
        int is_dir = (strcmp(resolved, "/") == 0) || S_ISDIR(st.st_mode);
        if (is_dir) {
            if (nt > 1) outf("%s%s:\r\n", targets[i], "");
            list_dir(resolved);
        } else {
            print_entry(targets[i], 0, (long)st.st_size, g_long, g_human);
            if (!g_long) out("\r\n");
        }
    }
}
