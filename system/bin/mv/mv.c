#include <unistd.h>
#include <string.h>
#include <stdio.h>
#include <errno.h>

#include <duneos/bin_args.h>

void app_main(void)
{
    static char buf[DUNEOS_EXEC_ARGS_BUF_SIZE];
    char *argv[5]; char *cwd;
    int argc = duneos_bin_args(buf, sizeof(buf), &cwd, argv, 5);

    if (argc < 3) {
        const char msg[] = "usage: mv <src> <dst>\r\n";
        write(STDOUT_FILENO, msg, sizeof(msg) - 1);
        return;
    }

    char src[256], dst[256];
    duneos_bin_resolve(argv[1], cwd, src, sizeof(src));
    duneos_bin_resolve(argv[2], cwd, dst, sizeof(dst));

    if (rename(src, dst) != 0) {
        char msg[256];
        snprintf(msg, sizeof(msg), "mv: %s -> %s: %s\r\n", src, dst, strerror(errno));
        write(STDOUT_FILENO, msg, strlen(msg));
    }
}
