/*
 * cat — concatenate files to stdout.
 *
 *   cat [FILE...]
 *
 * With no FILE (or "-"), reads stdin. Output streams chunk-by-chunk, so a large
 * file is not bounded by heap (ADR 032).
 */

#include <unistd.h>
#include <fcntl.h>
#include <string.h>
#include <stdio.h>
#include <errno.h>

#include <duneos/bin_args.h>

static void cat_fd(int fd)
{
    char buf[256]; int n;
    while ((n = (int)read(fd, buf, sizeof(buf))) > 0)
        write(STDOUT_FILENO, buf, (size_t)n);
}

void app_main(void)
{
    char ab[DUNEOS_EXEC_ARGS_BUF_SIZE];
    char *argv[16]; char *cwd;
    int argc = duneos_bin_args(ab, sizeof(ab), &cwd, argv, 16);

    if (argc < 2) { cat_fd(STDIN_FILENO); return; }

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-") == 0) { cat_fd(STDIN_FILENO); continue; }

        char path[256];
        duneos_bin_resolve(argv[i], cwd, path, sizeof(path));
        int fd = open(path, O_RDONLY);
        if (fd < 0) {
            char m[300];
            snprintf(m, sizeof(m), "%s: %s", argv[i], strerror(errno));
            duneos_bin_err("cat", m);
            continue;
        }
        cat_fd(fd);
        close(fd);
    }
}
