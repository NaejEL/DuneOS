#include <unistd.h>
#include <fcntl.h>
#include <string.h>
#include <stdio.h>
#include <sys/stat.h>
#include <dirent.h>
#include <errno.h>
#include <stdarg.h>

#include <duneos/bin_args.h>

static void out(const char *s)           { write(STDOUT_FILENO, s, strlen(s)); }
static void outn(const char *s)          { out(s); out("\r\n"); }
static void outf(const char *fmt, ...)
{
    char buf[256]; va_list ap; va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap); va_end(ap); out(buf);
}

void app_main(void)
{
    static char buf[DUNEOS_EXEC_ARGS_BUF_SIZE];
    char *argv[8]; char *cwd;
    int argc = duneos_bin_args(buf, sizeof(buf), &cwd, argv, 8);
    if (argc == 0) {
        outn("ls: warn: exec_args missing — using default cwd");
    }

    int long_fmt = 0;
    const char *target = cwd;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-l") == 0) { long_fmt = 1; continue; }
        target = argv[i];
    }

    char resolved[256];
    duneos_bin_resolve(target, cwd, resolved, sizeof(resolved));

    if (strcmp(resolved, "/") == 0) {
        outn(long_fmt ? "d        0  dev\r\nd        0  sd\r\nd        0  tmp"
                      : "dev  sd  tmp");
        return;
    }

    DIR *dir = opendir(resolved);
    if (!dir) { outf("ls: %s: %s\r\n", resolved, strerror(errno)); return; }

    struct dirent *ent;
    while ((ent = readdir(dir)) != NULL) {
        if (long_fmt) {
            char fpath[320];
            snprintf(fpath, sizeof(fpath), "%s/%s", resolved, ent->d_name);
            struct stat st;
            if (stat(fpath, &st) == 0)
                outf("%c %8ld  %s\r\n", S_ISDIR(st.st_mode) ? 'd' : '-',
                     (long)st.st_size, ent->d_name);
            else
                outf("? %8d  %s\r\n", 0, ent->d_name);
        } else {
            out(ent->d_name); out("  ");
        }
    }
    closedir(dir);
    if (!long_fmt) out("\r\n");
}
