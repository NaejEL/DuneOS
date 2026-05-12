#include <unistd.h>
#include <fcntl.h>
#include <string.h>
#include <stdio.h>
#include <errno.h>

#include <duneos/bin_args.h>

void app_main(void)
{
    static char buf[DUNEOS_EXEC_ARGS_BUF_SIZE];
    char *argv[4]; char *cwd;
    int argc = duneos_bin_args(buf, sizeof(buf), &cwd, argv, 4);

    if (argc < 2) {
        const char msg[] = "usage: cat <file>\r\n";
        write(STDOUT_FILENO, msg, sizeof(msg) - 1);
        return;
    }

    char path[256];
    duneos_bin_resolve(argv[1], cwd, path, sizeof(path));

    int fd = open(path, O_RDONLY);
    if (fd < 0) {
        char msg[128];
        snprintf(msg, sizeof(msg), "cat: %s: %s\r\n", path, strerror(errno));
        write(STDOUT_FILENO, msg, strlen(msg));
        return;
    }

    char rbuf[256]; ssize_t n;
    while ((n = read(fd, rbuf, sizeof(rbuf))) > 0)
        write(STDOUT_FILENO, rbuf, n);
    close(fd);
    write(STDOUT_FILENO, "\r\n", 2);
}
