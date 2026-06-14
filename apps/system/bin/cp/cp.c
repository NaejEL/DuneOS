/*
 * cp — copy files and directories.
 *
 *   cp [-r] SRC... DEST
 *     -r  copy directories recursively
 *
 * If DEST is a directory, each SRC is copied into it; otherwise a single SRC is
 * copied to DEST. Paths are threaded through fixed stack buffers (captured bins
 * run on the shell's stack), recursion depth-capped.
 */

#include <unistd.h>
#include <fcntl.h>
#include <string.h>
#include <stdio.h>
#include <sys/stat.h>
#include <dirent.h>
#include <errno.h>

#include <duneos/bin_args.h>

#define USAGE    "usage: cp [-r] SRC... DEST"
#define PLEN     512
#define MAXDEPTH 16

static int g_r;

static int is_dir(const char *p) { struct stat st; return stat(p, &st) == 0 && S_ISDIR(st.st_mode); }

static int copy_file(const char *src, const char *dst)
{
    int in = open(src, O_RDONLY);
    if (in < 0) { char m[300]; snprintf(m, sizeof(m), "%s: %s", src, strerror(errno)); duneos_bin_err("cp", m); return -1; }
    int out = open(dst, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (out < 0) { char m[300]; snprintf(m, sizeof(m), "%s: %s", dst, strerror(errno)); duneos_bin_err("cp", m); close(in); return -1; }

    char buf[512]; int n, rc = 0;
    while ((n = (int)read(in, buf, sizeof(buf))) > 0)
        if (write(out, buf, n) != n) { duneos_bin_err("cp", "short write"); rc = -1; break; }
    close(in); close(out);
    return rc;
}

/* src/dst are mutable PLEN buffers; slen/dlen are their current lengths. */
static int copy_tree(char *src, int slen, char *dst, int dlen, int depth)
{
    if (depth >= MAXDEPTH) { duneos_bin_err("cp", "directory tree too deep"); return -1; }
    mkdir(dst, 0777);
    DIR *d = opendir(src);
    if (!d) { char m[300]; snprintf(m, sizeof(m), "%s: %s", src, strerror(errno)); duneos_bin_err("cp", m); return -1; }

    struct dirent *e; int rc = 0;
    while ((e = readdir(d)) != NULL) {
        if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, "..")) continue;
        int sn = snprintf(src + slen, PLEN - slen, "/%s", e->d_name);
        int dn = snprintf(dst + dlen, PLEN - dlen, "/%s", e->d_name);
        if (slen + sn < PLEN && dlen + dn < PLEN) {
            if (is_dir(src)) { if (copy_tree(src, slen + sn, dst, dlen + dn, depth + 1) != 0) rc = -1; }
            else            { if (copy_file(src, dst) != 0) rc = -1; }
        }
        src[slen] = '\0'; dst[dlen] = '\0';
    }
    closedir(d);
    return rc;
}

void app_main(void)
{
    char ab[DUNEOS_EXEC_ARGS_BUF_SIZE];
    char *argv[16]; char *cwd;
    int argc = duneos_bin_args(ab, sizeof(ab), &cwd, argv, 16);

    duneos_opts_t o;
    if (duneos_bin_getopts(argc, argv, "r", &o) != 0) { duneos_bin_badopt("cp", o.bad, USAGE); return; }
    g_r = duneos_opt(&o, 'r');

    int n = argc - o.argi;
    if (n < 2) { duneos_bin_err("cp", USAGE); return; }

    char dst[PLEN];
    duneos_bin_resolve(argv[argc - 1], cwd, dst, sizeof(dst));
    int dest_is_dir = is_dir(dst);
    if (n - 1 > 1 && !dest_is_dir) { duneos_bin_err("cp", "target is not a directory"); return; }

    for (int i = o.argi; i < argc - 1; i++) {
        char src[PLEN];
        duneos_bin_resolve(argv[i], cwd, src, sizeof(src));

        char dstpath[PLEN];
        if (dest_is_dir) {
            const char *base = strrchr(argv[i], '/');
            base = base ? base + 1 : argv[i];
            snprintf(dstpath, sizeof(dstpath), "%s/%s", dst, base);
        } else {
            snprintf(dstpath, sizeof(dstpath), "%s", dst);
        }

        if (is_dir(src)) {
            if (!g_r) { char m[300]; snprintf(m, sizeof(m), "-r not specified; omitting directory '%s'", argv[i]); duneos_bin_err("cp", m); continue; }
            copy_tree(src, (int)strlen(src), dstpath, (int)strlen(dstpath), 0);
        } else {
            copy_file(src, dstpath);
        }
    }
}
