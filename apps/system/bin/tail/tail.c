/*
 * tail — print the last lines of a file, or follow it.
 *
 *   tail [-n N] [-f] FILE
 *     -n N  print the last N lines (default 10, capped at 32)
 *     -f    follow: print new data as it is appended (Ctrl-C to stop)
 */

#include <unistd.h>
#include <fcntl.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <errno.h>

extern int usleep(unsigned int useconds);

#include <duneos/bin_args.h>

#define RING_MAX 32
#define LINEW    128

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

void app_main(void)
{
    char ab[DUNEOS_EXEC_ARGS_BUF_SIZE];
    char *argv[8]; char *cwd;
    int argc = duneos_bin_args(ab, sizeof(ab), &cwd, argv, 8);

    int n_lines = 10, follow = 0;
    const char *path_arg = NULL;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-f")) follow = 1;
        else if (!strcmp(argv[i], "-n") && i + 1 < argc) n_lines = atoi(argv[++i]);
        else if (argv[i][0] == '-' && argv[i][1] == 'n' && argv[i][2]) n_lines = atoi(argv[i] + 2);
        else if (argv[i][0] == '-' && argv[i][1]) { duneos_bin_err("tail", "usage: tail [-n N] [-f] FILE"); return; }
        else path_arg = argv[i];
    }
    if (!path_arg) { duneos_bin_err("tail", "usage: tail [-n N] [-f] FILE"); return; }
    if (n_lines < 1) n_lines = 1;
    if (n_lines > RING_MAX) n_lines = RING_MAX;

    char path[256];
    duneos_bin_resolve(path_arg, cwd, path, sizeof(path));
    int fd = open(path, O_RDONLY);
    if (fd < 0) { char m[300]; snprintf(m, sizeof(m), "%s: %s", path_arg, strerror(errno)); duneos_bin_err("tail", m); return; }

    if (!follow) {
        char ring[RING_MAX][LINEW];
        int  count = 0, head = 0;
        lr_t r = { .fd = fd, .len = 0, .pos = 0 };
        char line[LINEW];
        while (lr_getline(&r, line, sizeof(line)) >= 0) {
            snprintf(ring[head], LINEW, "%s", line);
            head = (head + 1) % n_lines;
            if (count < n_lines) count++;
        }
        int start = (count < n_lines) ? 0 : head;
        for (int i = 0; i < count; i++) {
            const char *s = ring[(start + i) % n_lines];
            write(STDOUT_FILENO, s, strlen(s));
            write(STDOUT_FILENO, "\n", 1);
        }
    } else {
        lseek(fd, 0, SEEK_END);
        int flags = fcntl(STDIN_FILENO, F_GETFL, 0);
        fcntl(STDIN_FILENO, F_SETFL, flags | O_NONBLOCK);
        fcntl(fd, F_SETFL, O_NONBLOCK);
        write(STDOUT_FILENO, "[following -- Ctrl-C to stop]\n", 30);
        char rbuf[128];
        while (1) {
            ssize_t n = read(fd, rbuf, sizeof(rbuf));
            if (n > 0) write(STDOUT_FILENO, rbuf, (size_t)n);
            char c;
            if (read(STDIN_FILENO, &c, 1) > 0 && c == '\x03') { write(STDOUT_FILENO, "^C\n", 3); break; }
            if (n <= 0) usleep(100000);
        }
        fcntl(STDIN_FILENO, F_SETFL, flags);
    }
    close(fd);
}
