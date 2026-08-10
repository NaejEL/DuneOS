/*
 * logd — userspace log housekeeping daemon (ADR 038).
 *
 * Owns the active log directory (/sd/log if an SD card is mounted, else /log)
 * and keeps it bounded so apps can log freely without ever filling the card or
 * the internal flash:
 *
 *   - per-file cap: when <app>.log exceeds file_cap, rotate it to <app>.log.1
 *     (one generation kept). The dlog writer reopens <app>.log on its next line.
 *   - directory cap: when the whole dir exceeds dir_cap, drop the .1 rotations
 *     oldest-first until back under budget.
 *
 * Housekeeping runs out of band (every interval_s), so dlog() itself never
 * blocks on rotation. Config is read from /etc/logd/config.yaml if present,
 * else the built-in defaults apply. Run as an init service, restart: always.
 */

#include <unistd.h>
#include <fcntl.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <dirent.h>
#include <sys/stat.h>

extern int duneos_config_path(const char *app_name, char *out, size_t outsz);
extern int usleep(unsigned int useconds);

#define MAX_ENTRIES   24
#define NAME_MAX_LEN  64

/* Backend-aware defaults (each overridable via /etc/logd/config.yaml). Flash is
 * the tiny shared sysbin partition → small caps; an SD card has room for the MB
 * range, so logs there get a far larger budget and keep real history. */
static long s_file_cap   = 32 * 1024;
static long s_dir_cap    = 64 * 1024;
static int  s_interval_s = 30;

#define SD_FILE_CAP  (128 * 1024)
#define SD_DIR_CAP   (4 * 1024 * 1024)

static char s_base[64];
static int  s_klog_fd = -1;     /* /dev/klog cursor, opened only when on SD */

static int sd_mounted(void)
{
    struct stat st;
    return stat("/sd", &st) == 0 && S_ISDIR(st.st_mode);
}

/* Tiny "<key>: <int>" reader over the optional config file. */
static long cfg_int(const char *buf, const char *key, long def)
{
    const char *p = strstr(buf, key);
    if (!p) return def;
    p += strlen(key);
    while (*p == ' ' || *p == ':' || *p == '\t') p++;
    if (*p < '0' || *p > '9') return def;
    return (long)atoi(p);   /* atol is not exported; ILP32 so int == long */
}

static void load_config(void)
{
    char path[96];
    if (duneos_config_path("logd", path, sizeof(path)) != 0) return;
    int fd = open(path, O_RDONLY);
    if (fd < 0) return;
    char buf[512];
    int n = read(fd, buf, sizeof(buf) - 1);
    close(fd);
    if (n <= 0) return;
    buf[n] = '\0';
    s_file_cap   = cfg_int(buf, "file_cap_kb", s_file_cap / 1024) * 1024;
    s_dir_cap    = cfg_int(buf, "dir_cap_kb",  s_dir_cap  / 1024) * 1024;
    s_interval_s = (int)cfg_int(buf, "interval_s", s_interval_s);
    if (s_interval_s < 1) s_interval_s = 1;
}

static char s_names[MAX_ENTRIES][NAME_MAX_LEN];
static int  s_count;

/* Snapshot the directory listing so we never mutate (rename/unlink) the dir
 * while a readdir() iterator is live over it. */
static void snapshot(void)
{
    s_count = 0;
    DIR *d = opendir(s_base);
    if (!d) return;
    struct dirent *e;
    while ((e = readdir(d)) != NULL && s_count < MAX_ENTRIES) {
        if (e->d_name[0] == '.') continue;
        strncpy(s_names[s_count], e->d_name, NAME_MAX_LEN - 1);
        s_names[s_count][NAME_MAX_LEN - 1] = '\0';
        s_count++;
    }
    closedir(d);
}

static long file_size(const char *name)
{
    char p[128];
    snprintf(p, sizeof(p), "%s/%s", s_base, name);
    struct stat st;
    return (stat(p, &st) == 0) ? (long)st.st_size : 0;
}

static int is_suffix(const char *name, const char *suf)
{
    size_t ln = strlen(name), ls = strlen(suf);
    return ln > ls && strcmp(name + ln - ls, suf) == 0;
}

static void rotate_big(void)
{
    for (int i = 0; i < s_count; i++) {
        if (!is_suffix(s_names[i], ".log")) continue;
        if (file_size(s_names[i]) < s_file_cap) continue;
        char path[128], old[140];
        snprintf(path, sizeof(path), "%s/%s",    s_base, s_names[i]);
        snprintf(old,  sizeof(old),  "%s/%s.1",  s_base, s_names[i]);
        unlink(old);            /* drop the previous generation */
        rename(path, old);      /* writer recreates <app>.log on next dlog() */
    }
}

static long dir_total(void)
{
    long total = 0;
    for (int i = 0; i < s_count; i++) total += file_size(s_names[i]);
    return total;
}

static void enforce_dir_cap(void)
{
    if (dir_total() <= s_dir_cap) return;
    /* Reclaim rotated generations first; they are pure history. */
    for (int i = 0; i < s_count && dir_total() > s_dir_cap; i++) {
        if (!is_suffix(s_names[i], ".1")) continue;
        char p[128];
        snprintf(p, sizeof(p), "%s/%s", s_base, s_names[i]);
        unlink(p);
        s_names[i][0] = '\0';   /* exclude from later dir_total() passes */
    }
}

/* Persist the kernel log (dmesg) to SD. /dev/klog is a cursor: each read()
 * returns only bytes appended since the last one, so holding the fd open and
 * draining each tick tails the kernel ring into a real file. Open-append-close
 * per chunk so a concurrent rotation of kernel.log never races us. Only runs on
 * SD — persisting the 16 KiB ring to the small internal flash isn't worth the
 * wear. The first drain captures the whole boot history (cursor starts oldest). */
static void drain_klog(void)
{
    if (s_klog_fd < 0) return;
    char path[80];
    snprintf(path, sizeof(path), "%s/kernel.log", s_base);
    char buf[512];
    int n;
    while ((n = read(s_klog_fd, buf, sizeof(buf))) > 0) {
        int fd = open(path, O_WRONLY | O_CREAT | O_APPEND, 0644);
        if (fd < 0) break;
        write(fd, buf, n);
        close(fd);
    }
}

void app_main(void)
{
    int sd = sd_mounted();
    snprintf(s_base, sizeof(s_base), "%s", sd ? "/sd/log" : "/log");
    if (sd) { s_file_cap = SD_FILE_CAP; s_dir_cap = SD_DIR_CAP; }
    mkdir(s_base, 0755);

    load_config();   /* optional overrides win over the backend defaults */

    if (sd) s_klog_fd = open("/dev/klog", O_RDONLY);

    for (;;) {
        drain_klog();         /* tail kernel.log first so it's sized for the pass */
        snapshot();
        rotate_big();
        snapshot();           /* re-read after renames before the quota pass */
        enforce_dir_cap();
        usleep((unsigned)s_interval_s * 1000000u);
    }
}
