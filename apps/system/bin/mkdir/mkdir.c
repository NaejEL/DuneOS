/*
 * mkdir — create directories.
 *
 *   mkdir [-p] DIR...
 *     -p  create parent directories as needed; no error if a dir already exists
 */

#include <unistd.h>
#include <string.h>
#include <stdio.h>
#include <sys/stat.h>
#include <errno.h>

#include <duneos/bin_args.h>

#define USAGE "usage: mkdir [-p] DIR..."

/* Create each path component in turn (mkdir -p). */
static int mkdir_p(char *path)
{
    for (char *s = path + 1; *s; s++) {
        if (*s == '/') { *s = '\0'; mkdir(path, 0777); *s = '/'; }
    }
    if (mkdir(path, 0777) != 0 && errno != EEXIST) return -1;
    return 0;
}

void app_main(void)
{
    char ab[DUNEOS_EXEC_ARGS_BUF_SIZE];
    char *argv[16]; char *cwd;
    int argc = duneos_bin_args(ab, sizeof(ab), &cwd, argv, 16);

    duneos_opts_t o;
    if (duneos_bin_getopts(argc, argv, "p", &o) != 0) { duneos_bin_badopt("mkdir", o.bad, USAGE); return; }
    int parents = duneos_opt(&o, 'p');

    if (o.argi >= argc) { duneos_bin_err("mkdir", USAGE); return; }

    for (int i = o.argi; i < argc; i++) {
        char path[512];
        duneos_bin_resolve(argv[i], cwd, path, sizeof(path));
        int rc = parents ? mkdir_p(path) : mkdir(path, 0777);
        if (rc != 0) { char m[300]; snprintf(m, sizeof(m), "%s: %s", argv[i], strerror(errno)); duneos_bin_err("mkdir", m); }
    }
}
