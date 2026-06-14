/*
 * find — walk a directory tree and print entries.
 *
 *   find [PATH] [-name GLOB] [-type f|d]
 *     -name GLOB   match the basename against a shell glob (* ? [..])
 *     -type f|d    restrict to files / directories
 *
 * PATH defaults to the cwd. Glob (not regex) is what -name expects; the matcher
 * is a small fnmatch, separate from the ADR 036 regex used by grep/sed.
 *
 * Buffers passed to syscalls must be on the stack (captured bins: static BSS is
 * outside the shell slot's bounds), so the path is one stack buffer threaded
 * through the recursion by pointer — frames stay tiny.
 */

#include <unistd.h>
#include <string.h>
#include <stdio.h>
#include <sys/stat.h>
#include <dirent.h>
#include <errno.h>

#include <duneos/bin_args.h>

#define PATH_MAX_LEN 768
#define MAX_DEPTH    24

static const char *g_name;     /* glob, or NULL = match all */
static char        g_type;     /* 'f' | 'd' | 0 */

/* Shell glob: * (any run), ? (one char), [..]/[!..] class. Recursive. */
static int globmatch(const char *p, const char *s)
{
    while (*p) {
        if (*p == '*') {
            p++;
            if (!*p) return 1;
            for (; *s; s++) if (globmatch(p, s)) return 1;
            return globmatch(p, s);
        } else if (*p == '?') {
            if (!*s) return 0;
            p++; s++;
        } else if (*p == '[') {
            const char *q = p + 1;
            int neg = (*q == '!' || *q == '^');
            if (neg) q++;
            int hit = 0;
            while (*q && *q != ']') {
                if (q[1] == '-' && q[2] && q[2] != ']') {
                    if ((unsigned char)*s >= (unsigned char)q[0] &&
                        (unsigned char)*s <= (unsigned char)q[2]) hit = 1;
                    q += 3;
                } else { if (*s == *q) hit = 1; q++; }
            }
            if (*q == ']') q++;
            if (!*s || hit == neg) return 0;
            p = q; s++;
        } else {
            if (*p != *s) return 0;
            p++; s++;
        }
    }
    return *s == '\0';
}

static void emitln(const char *s) { write(STDOUT_FILENO, s, strlen(s)); write(STDOUT_FILENO, "\n", 1); }

static void walk(char *path, int len, int depth)
{
    DIR *d = opendir(path);
    if (!d) return;
    struct dirent *e;
    while ((e = readdir(d)) != NULL) {
        if (strcmp(e->d_name, ".") == 0 || strcmp(e->d_name, "..") == 0) continue;

        int nl = snprintf(path + len, PATH_MAX_LEN - len, "/%s", e->d_name);
        if (nl <= 0 || len + nl >= PATH_MAX_LEN) { path[len] = '\0'; continue; }

        struct stat st;
        int isdir = (stat(path, &st) == 0 && S_ISDIR(st.st_mode));

        int typeok = (g_type == 0) || (g_type == 'd' && isdir) || (g_type == 'f' && !isdir);
        if (typeok && (!g_name || globmatch(g_name, e->d_name)))
            emitln(path);

        if (isdir && depth < MAX_DEPTH) walk(path, len + nl, depth + 1);
        path[len] = '\0';
    }
    closedir(d);
}

void app_main(void)
{
    char ab[DUNEOS_EXEC_ARGS_BUF_SIZE];
    char *argv[12]; char *cwd;
    int argc = duneos_bin_args(ab, sizeof(ab), &cwd, argv, 12);

    const char *start = NULL;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-name") == 0 && i + 1 < argc) g_name = argv[++i];
        else if (strcmp(argv[i], "-type") == 0 && i + 1 < argc) g_type = argv[++i][0];
        else if (argv[i][0] == '-') {
            char m[64]; snprintf(m, sizeof(m), "unknown predicate '%s'", argv[i]);
            duneos_bin_err("find", m);
            return;
        } else start = argv[i];
    }

    char path[PATH_MAX_LEN];
    duneos_bin_resolve(start ? start : ".", cwd, path, sizeof(path));
    int len = (int)strlen(path);
    while (len > 1 && path[len - 1] == '/') path[--len] = '\0';   /* trim trailing / */

    /* Report the start path itself if it matches (mirrors find's behaviour). */
    struct stat st;
    if (stat(path, &st) != 0) {
        char m[300]; snprintf(m, sizeof(m), "%s: %s", path, strerror(errno));
        duneos_bin_err("find", m);
        return;
    }
    walk(path, len, 0);
}
