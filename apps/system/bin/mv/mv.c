/*
 * mv — move / rename.
 *
 *   mv SRC DST          rename SRC to DST
 *   mv SRC... DIR       move each SRC into directory DIR
 */

#include <unistd.h>
#include <string.h>
#include <stdio.h>
#include <sys/stat.h>
#include <errno.h>

#include <duneos/bin_args.h>

static int is_dir(const char *p) { struct stat st; return stat(p, &st) == 0 && S_ISDIR(st.st_mode); }

void app_main(void)
{
    char ab[DUNEOS_EXEC_ARGS_BUF_SIZE];
    char *argv[16]; char *cwd;
    int argc = duneos_bin_args(ab, sizeof(ab), &cwd, argv, 16);

    if (argc < 3) { duneos_bin_err("mv", "usage: mv SRC... DEST"); return; }

    char dst[256];
    duneos_bin_resolve(argv[argc - 1], cwd, dst, sizeof(dst));
    int dest_is_dir = is_dir(dst);
    if (argc - 2 > 1 && !dest_is_dir) { duneos_bin_err("mv", "target is not a directory"); return; }

    for (int i = 1; i < argc - 1; i++) {
        char src[256];
        duneos_bin_resolve(argv[i], cwd, src, sizeof(src));

        char target[320];
        if (dest_is_dir) {
            const char *base = strrchr(argv[i], '/');
            base = base ? base + 1 : argv[i];
            snprintf(target, sizeof(target), "%s/%s", dst, base);
        } else {
            snprintf(target, sizeof(target), "%s", dst);
        }

        if (rename(src, target) != 0) {
            char m[400]; snprintf(m, sizeof(m), "%s -> %s: %s", argv[i], target, strerror(errno));
            duneos_bin_err("mv", m);
        }
    }
}
