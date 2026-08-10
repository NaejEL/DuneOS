/*
 * head — print the first N lines of each file (default 10).
 *
 *   head [-n N] [FILE...]
 *
 * With no FILE, reads stdin. With several files, each is preceded by a
 * "==> name <==" header (like coreutils head).
 */

#include <unistd.h>
#include <fcntl.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <errno.h>

#include <duneos/bin_args.h>

#define USAGE "usage: head [-n N] [FILE...]"

static int g_n = 10;

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

static void head_fd(int fd, const char *name)
{
    if (name) {
        write(STDOUT_FILENO, "==> ", 4);
        write(STDOUT_FILENO, name, strlen(name));
        write(STDOUT_FILENO, " <==\n", 5);
    }
    lr_t r = { .fd = fd, .len = 0, .pos = 0 };
    char line[256];
    int  count = 0;
    while (count < g_n && lr_getline(&r, line, sizeof(line)) >= 0) {
        write(STDOUT_FILENO, line, strlen(line));
        write(STDOUT_FILENO, "\n", 1);
        count++;
    }
}

void app_main(void)
{
    char ab[DUNEOS_EXEC_ARGS_BUF_SIZE];
    char *argv[16]; char *cwd;
    int argc = duneos_bin_args(ab, sizeof(ab), &cwd, argv, 16);

    const char *files[16]; int nf = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-n") && i + 1 < argc) { g_n = atoi(argv[++i]); }
        else if (argv[i][0] == '-' && argv[i][1] == 'n' && argv[i][2]) { g_n = atoi(argv[i] + 2); }
        else if (argv[i][0] == '-' && argv[i][1]) { duneos_bin_badopt("head", argv[i][1], USAGE); return; }
        else if (nf < 16) { files[nf++] = argv[i]; }
    }
    if (g_n < 0) g_n = 0;

    if (nf == 0) { head_fd(STDIN_FILENO, NULL); return; }
    for (int i = 0; i < nf; i++) {
        char path[256];
        duneos_bin_resolve(files[i], cwd, path, sizeof(path));
        int fd = open(path, O_RDONLY);
        if (fd < 0) { char m[300]; snprintf(m, sizeof(m), "%s: %s", path, strerror(errno)); duneos_bin_err("head", m); continue; }
        head_fd(fd, nf > 1 ? files[i] : NULL);
        close(fd);
    }
}
