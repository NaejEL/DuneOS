/*
 * touch — create empty files (and open existing ones for a no-op write).
 *
 *   touch FILE...
 *
 * No utime() in the SDK, so an existing file's mtime is not bumped — touch is
 * mainly "create if missing" here.
 */

#include <unistd.h>
#include <fcntl.h>
#include <string.h>
#include <stdio.h>
#include <errno.h>

#include <duneos/bin_args.h>

void app_main(void)
{
    char ab[DUNEOS_EXEC_ARGS_BUF_SIZE];
    char *argv[16]; char *cwd;
    int argc = duneos_bin_args(ab, sizeof(ab), &cwd, argv, 16);

    if (argc < 2) { duneos_bin_err("touch", "usage: touch FILE..."); return; }

    for (int i = 1; i < argc; i++) {
        char path[256];
        duneos_bin_resolve(argv[i], cwd, path, sizeof(path));
        int fd = open(path, O_WRONLY | O_CREAT, 0644);
        if (fd < 0) { char m[300]; snprintf(m, sizeof(m), "%s: %s", argv[i], strerror(errno)); duneos_bin_err("touch", m); continue; }
        close(fd);
    }
}
