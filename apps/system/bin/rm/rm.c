#include <unistd.h>
#include <string.h>
#include <stdio.h>
#include <errno.h>

#include <duneos/bin_args.h>

void app_main(void)
{
    char buf[DUNEOS_EXEC_ARGS_BUF_SIZE];
    char *argv[4]; char *cwd;
    int argc = duneos_bin_args(buf, sizeof(buf), &cwd, argv, 4);

    if (argc < 2) {
        const char msg[] = "usage: rm <file>\r\n";
        write(STDOUT_FILENO, msg, sizeof(msg) - 1);
        return;
    }

    char path[256];
    duneos_bin_resolve(argv[1], cwd, path, sizeof(path));

    if (unlink(path) != 0) {
        char msg[128];
        snprintf(msg, sizeof(msg), "rm: %s: %s\r\n", path, strerror(errno));
        write(STDOUT_FILENO, msg, strlen(msg));
    }
}
