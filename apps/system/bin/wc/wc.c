/*
 * wc — count lines, words and bytes.
 *
 *   wc [-lwc] [FILE...]
 *     -l lines   -w words   -c bytes   (default: all three)
 *
 * With no FILE, reads stdin.
 */

#include <unistd.h>
#include <fcntl.h>
#include <string.h>
#include <stdio.h>
#include <errno.h>

#include <duneos/bin_args.h>

#define USAGE "usage: wc [-lwc] [FILE...]"

static int g_l, g_w, g_c, g_any;

static void count_fd(int fd, const char *name)
{
    long lines = 0, words = 0, bytes = 0;
    int  inword = 0;
    char buf[256]; int n;
    while ((n = (int)read(fd, buf, sizeof(buf))) > 0) {
        bytes += n;
        for (int i = 0; i < n; i++) {
            char c = buf[i];
            if (c == '\n') lines++;
            if (c == ' ' || c == '\t' || c == '\n' || c == '\r') inword = 0;
            else if (!inword) { inword = 1; words++; }
        }
    }
    char out[320]; int o = 0;
    if (g_l || !g_any) o += snprintf(out + o, sizeof(out) - o, "%8ld", lines);
    if (g_w || !g_any) o += snprintf(out + o, sizeof(out) - o, "%8ld", words);
    if (g_c || !g_any) o += snprintf(out + o, sizeof(out) - o, "%8ld", bytes);
    if (name) o += snprintf(out + o, sizeof(out) - o, " %s", name);
    o += snprintf(out + o, sizeof(out) - o, "\n");
    write(STDOUT_FILENO, out, (size_t)o);
}

void app_main(void)
{
    char ab[DUNEOS_EXEC_ARGS_BUF_SIZE];
    char *argv[16]; char *cwd;
    int argc = duneos_bin_args(ab, sizeof(ab), &cwd, argv, 16);

    duneos_opts_t o;
    if (duneos_bin_getopts(argc, argv, "lwc", &o) != 0) { duneos_bin_badopt("wc", o.bad, USAGE); return; }
    g_l = duneos_opt(&o, 'l'); g_w = duneos_opt(&o, 'w'); g_c = duneos_opt(&o, 'c');
    g_any = g_l || g_w || g_c;

    if (o.argi >= argc) { count_fd(STDIN_FILENO, NULL); return; }
    for (int i = o.argi; i < argc; i++) {
        char path[256];
        duneos_bin_resolve(argv[i], cwd, path, sizeof(path));
        int fd = open(path, O_RDONLY);
        if (fd < 0) { char m[300]; snprintf(m, sizeof(m), "%s: %s", argv[i], strerror(errno)); duneos_bin_err("wc", m); continue; }
        count_fd(fd, argv[i]);
        close(fd);
    }
}
