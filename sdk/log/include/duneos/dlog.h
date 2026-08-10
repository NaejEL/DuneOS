#pragma once
/*
 * dlog — DuneOS userspace logger (ADR 038).
 *
 * The userspace counterpart to klog: where klog is the kernel's dmesg ring,
 * dlog is a shared, file-backed syslog for apps. Each app logs to its own file
 * under the active log directory:
 *
 *   /sd/log/<app>.log   when an SD card is mounted (space, no internal-flash wear)
 *   /log/<app>.log      otherwise (LittleFS root, fallback)
 *
 * dlog picks the backend automatically at dlog_open(). Lines are timestamped
 * (monotonic seconds.ms) and tagged with a level. The `logd` daemon enforces
 * per-file rotation and a directory quota out of band, so apps never block on
 * housekeeping. The `logs` tool reads these files (the dlog equivalent of klog).
 *
 * Pull into an app with, in duneos.yaml:
 *     sources:
 *       - $SDK/log/dlog.c
 *     permissions: 96      # FS_READ (32) | FS_WRITE (64)
 *
 * Each dlog() call open-append-closes the file, so it is safe even while logd
 * rotates the log underneath it. Not async-signal-safe; call from normal code.
 */

typedef enum {
    DLOG_ERR   = 0,
    DLOG_WARN  = 1,
    DLOG_INFO  = 2,
    DLOG_DEBUG = 3,
} dlog_level_t;

/* Resolve the backend dir and bind this app's logfile name. Returns 0, or -1 on
 * a bad/empty name. Safe to call once at startup; subsequent calls re-bind. */
int  dlog_open(const char *app_name);

/* Append one timestamped line. No-op if dlog_open() was not called. */
void dlog(dlog_level_t level, const char *fmt, ...) __attribute__((format(printf, 2, 3)));

/* Stop logging (further dlog() calls become no-ops until dlog_open() again). */
void dlog_close(void);

#define DLOGE(...) dlog(DLOG_ERR,   __VA_ARGS__)
#define DLOGW(...) dlog(DLOG_WARN,  __VA_ARGS__)
#define DLOGI(...) dlog(DLOG_INFO,  __VA_ARGS__)
#define DLOGD(...) dlog(DLOG_DEBUG, __VA_ARGS__)
