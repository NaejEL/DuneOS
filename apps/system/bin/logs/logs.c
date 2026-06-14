/*
 * logs — read userspace logs (ADR 038), the dlog counterpart of `klog`.
 *
 *   logs            list the log files and their sizes
 *   logs <app>      print <app>.log  (".log" optional: `logs telnet`)
 *
 * Reads from the active log dir: /sd/log when an SD card is mounted, else /log.
 * Stack buffers only — a bin runs captured in the shell's task, where a static
 * buffer handed to read()/write() trips the kernel's pointer validation.
 */

#include <unistd.h>
#include <fcntl.h>
#include <string.h>
#include <stdio.h>
#include <stdarg.h>
#include <dirent.h>
#include <sys/stat.h>

#include <duneos/bin_args.h>

static void out(const char *s) { write(STDOUT_FILENO, s, strlen(s)); }
static void outf(const char *fmt, ...)
{
    char b[192]; va_list ap; va_start(ap, fmt);
    vsnprintf(b, sizeof(b), fmt, ap); va_end(ap); out(b);
}

static void resolve_base(char *o, size_t osz)
{
    struct stat st;
    if (stat("/sd", &st) == 0 && S_ISDIR(st.st_mode)) snprintf(o, osz, "/sd/log");
    else                                              snprintf(o, osz, "/log");
}

static void list(const char *base)
{
    DIR *d = opendir(base);
    if (!d) { outf("logs: no logs yet (%s)\r\n", base); return; }
    struct dirent *e; int any = 0;
    while ((e = readdir(d)) != NULL) {
        if (e->d_name[0] == '.') continue;
        char p[192]; snprintf(p, sizeof(p), "%s/%s", base, e->d_name);
        struct stat st; long sz = (stat(p, &st) == 0) ? (long)st.st_size : 0;
        outf("%9ld  %s\r\n", sz, e->d_name);
        any = 1;
    }
    closedir(d);
    if (!any) outf("(no logs in %s)\r\n", base);
}

static void cat(const char *base, const char *name)
{
    char p[192];
    if (strchr(name, '.')) snprintf(p, sizeof(p), "%s/%s",     base, name);
    else                   snprintf(p, sizeof(p), "%s/%s.log", base, name);
    int fd = open(p, O_RDONLY);
    if (fd < 0) { outf("logs: %s: no such log\r\n", name); return; }
    char buf[256]; int n;
    while ((n = read(fd, buf, sizeof(buf))) > 0) write(STDOUT_FILENO, buf, n);
    close(fd);
}

void app_main(void)
{
    char ab[DUNEOS_EXEC_ARGS_BUF_SIZE];
    char *argv[8]; char *cwd;
    int argc = duneos_bin_args(ab, sizeof(ab), &cwd, argv, 8);

    char base[64];
    resolve_base(base, sizeof(base));

    if (argc < 2) list(base);
    else          cat(base, argv[1]);
}
