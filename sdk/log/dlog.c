/*
 * dlog — DuneOS userspace logger. See <duneos/dlog.h> for the contract.
 *
 * Design notes:
 *  - Backend is chosen by stat("/sd"): it succeeds iff the SD card is mounted
 *    (the VFS only registers "/sd" when FatFS mounts), so no kernel query is
 *    needed. SD wins (space + spares the internal flash); /log is the fallback.
 *  - open-append-close per line keeps no fd held across calls, so the `logd`
 *    daemon can rename/rotate the file at any time without racing us.
 *  - Stack buffers only (no static line buffer) — a captured app passing a
 *    static-BSS pointer to write() trips the kernel's pointer validation.
 */

#include "duneos/dlog.h"

#include <fcntl.h>
#include <unistd.h>
#include <string.h>
#include <stdio.h>
#include <stdarg.h>
#include <time.h>
#include <sys/stat.h>

static int  s_ready;
static char s_app[32];
static char s_path[96];

static void resolve_base(char *out, size_t osz)
{
    struct stat st;
    if (stat("/sd", &st) == 0 && S_ISDIR(st.st_mode))
        snprintf(out, osz, "/sd/log");
    else
        snprintf(out, osz, "/log");
}

int dlog_open(const char *app_name)
{
    if (!app_name || !app_name[0]) return -1;

    char base[64];
    resolve_base(base, sizeof(base));
    mkdir(base, 0755);                       /* idempotent; ignore EEXIST */

    snprintf(s_app,  sizeof(s_app),  "%s", app_name);
    snprintf(s_path, sizeof(s_path), "%s/%s.log", base, app_name);
    s_ready = 1;
    return 0;
}

void dlog(dlog_level_t level, const char *fmt, ...)
{
    if (!s_ready) return;
    if ((int)level < 0 || level > DLOG_DEBUG) level = DLOG_INFO;

    static const char LC[] = { 'E', 'W', 'I', 'D' };

    char line[256];
    struct timespec ts = { 0, 0 };
    clock_gettime(CLOCK_MONOTONIC, &ts);

    int n = snprintf(line, sizeof(line), "[%lu.%03lu] %c %s: ",
                     (unsigned long)ts.tv_sec,
                     (unsigned long)(ts.tv_nsec / 1000000),
                     LC[level], s_app);
    if (n < 0) return;
    if (n > (int)sizeof(line) - 2) n = sizeof(line) - 2;

    int rem = (int)sizeof(line) - n - 1;     /* leave room for '\n' */
    va_list ap; va_start(ap, fmt);
    int m = vsnprintf(line + n, rem + 1, fmt, ap);
    va_end(ap);
    if (m < 0) m = 0;
    if (m > rem) m = rem;                     /* truncated to fit */

    int len = n + m;
    line[len++] = '\n';

    int fd = open(s_path, O_WRONLY | O_CREAT | O_APPEND, 0644);
    if (fd < 0) return;
    write(fd, line, len);
    close(fd);
}

void dlog_close(void) { s_ready = 0; }
