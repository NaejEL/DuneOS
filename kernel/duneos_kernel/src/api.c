/*
 * DuneOS kernel API table — ABI v3.
 *
 * This file owns the single static duneos_api_t instance and all the thin
 * wrapper functions it needs to bridge the typed API surface to the kernel's
 * internal implementations.
 *
 * Design notes:
 *   - read() wrapper uses check_app_writable_ptr (kernel writes to buffer).
 *   - write() wrapper uses check_user_ptr (kernel reads from buffer; the
 *     pointer may legitimately point into kernel-returned string data).
 *   - Directory handles are void* at the ABI boundary; wrappers cast.
 *   - Loader handles are void* at the ABI boundary; wrappers cast.
 *   - duneos_exit / duneos_supervisor_* come from supervisor.h.
 *   - Loader functions are forward-declared to avoid duneos_kernel →
 *     duneos_loader circular dependency (loader already requires kernel).
 */

/* Pull in semaphore.h before api.h so that __has_include sees it and the
 * real sem_t (ESP-IDF/FreeRTOS) takes precedence over our void* fallback. */
#include <semaphore.h>
#include <pthread.h>

#include "duneos/api.h"
#include "duneos/abi.h"
#include "duneos/supervisor.h"
#include "duneos/vfs.h"

#include "esp_system.h"

#include <unistd.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/ioctl.h>
#include <dirent.h>
#include <time.h>
#include <sys/time.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>

/* -------------------------------------------------------------------------
 * Forward declarations — loader functions live in duneos_loader component.
 * Including duneos/loader.h would create a kernel→loader dependency that
 * inverts the existing loader→kernel direction.  The types used here
 * (duneos_app_t, duneos_app_manifest_t) are defined in abi.h.
 * ---------------------------------------------------------------------- */
esp_err_t duneos_loader_load(const char *path, duneos_app_t **out_app);
esp_err_t duneos_loader_run(duneos_app_t *app);
esp_err_t duneos_loader_run_captured(duneos_app_t *app,
                                      char **out_buf, size_t *out_len);
void      duneos_loader_unload(duneos_app_t *app);
const duneos_app_manifest_t *duneos_loader_get_manifest(const duneos_app_t *app);

/* nanosleep bridge — defined in symbols.c; referenced here to avoid
 * duplicating the usleep-based implementation.                            */
int duneos_nanosleep(const struct timespec *req, struct timespec *rem);

/* Errno accessor from kernel's newlib (per-task TLS pointer)              */
extern int *__errno(void);

/* -------------------------------------------------------------------------
 * File-system wrappers
 * ---------------------------------------------------------------------- */

static int api_read(int fd, void *buf, size_t n)
{
    /* Kernel writes INTO buf — validate that it is app-writable memory.   */
    if (!duneos_supervisor_check_app_writable_ptr(buf, n)) {
        errno = EFAULT;
        return -1;
    }
    return (int)read(fd, buf, n);
}

static int api_write(int fd, const void *buf, size_t n)
{
    /* Kernel reads FROM buf — basic validity (not NULL, not peripheral).
     * The pointer may legitimately point into kernel-returned string data
     * (e.g. strerror(errno)), so we do not require it to be in app bounds. */
    if (!duneos_supervisor_check_user_ptr(buf, n)) {
        errno = EFAULT;
        return -1;
    }
    return (int)write(fd, buf, n);
}

/* open() is variadic — wrap with a concrete (path, flags, mode) signature  */
static int api_open(const char *path, int flags, int mode)
{
    return open(path, flags, mode);
}

/* dup/dup2 cannot be taken by address (static inline in ESP-IDF newlib)   */
static int api_dup(int fd)              { return fcntl(fd, F_DUPFD, 0); }
static int api_dup2(int fd, int newfd)
{
    if (fd == newfd) return newfd;
    close(newfd);
    return fcntl(fd, F_DUPFD, newfd);
}

/* dprintf absent from PicoLibc — bridge via vsnprintf + write             */
static int api_dprintf(int fd, const char *fmt, ...)
{
    char buf[256];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (n > 0) write(fd, buf, (size_t)n);
    return n;
}

/* Directory wrappers — cast to/from void* at the ABI boundary             */
static void *api_opendir (const char *path) { return (void *)opendir(path); }
static void *api_readdir (void *dir)         { return (void *)readdir((DIR *)dir); }
static int   api_closedir(void *dir)         { return closedir((DIR *)dir); }

static int api_mkdir(const char *path, unsigned mode)
{
    return mkdir(path, (mode_t)mode);
}

/* -------------------------------------------------------------------------
 * Loader wrappers — void* at ABI boundary, concrete types internally
 * ---------------------------------------------------------------------- */

static int api_loader_load(const char *path, void **out)
{
    return (int)duneos_loader_load(path, (duneos_app_t **)out);
}

static int api_loader_run(void *app)
{
    return (int)duneos_loader_run((duneos_app_t *)app);
}

static int api_loader_run_captured(void *app, char **buf, size_t *len)
{
    return (int)duneos_loader_run_captured((duneos_app_t *)app, buf, len);
}

static void api_loader_unload(void *app)
{
    duneos_loader_unload((duneos_app_t *)app);
}

static const void *api_loader_get_manifest(const void *app)
{
    return duneos_loader_get_manifest((const duneos_app_t *)app);
}

/* -------------------------------------------------------------------------
 * IPC wrappers — duneos_msg_t is hidden behind void* for apps
 * ---------------------------------------------------------------------- */

static int api_recv(void *out, uint32_t timeout_ms)
{
    return duneos_recv((duneos_msg_t *)out, timeout_ms);
}

static int api_supervisor_list_slots(void *out, int count)
{
    return duneos_supervisor_list_slots((duneos_slot_info_t *)out, count);
}

/* ADR 016: captured apps share the shell's task, so spawning a thread
 * from within would leave it running in the shell after the captured app
 * unwinds via longjmp — corrupting the shell. Refuse cleanly with EPERM.
 * Apps that need threads should declare heap_size > 0 in their manifest;
 * the shell auto-dispatches them to spawned mode. */
static int api_pthread_create(pthread_t *t, const pthread_attr_t *attr,
                              void *(*fn)(void *), void *arg)
{
    if (duneos_supervisor_captured_active()) {
        errno = EPERM;
        return EPERM;   /* pthread funcs return errno positive, not -1 */
    }
    return pthread_create(t, attr, fn, arg);
}

/* sem_t is absent from the bare-metal PicoLibc toolchain, so the API table
 * uses void *.  Kernel-side we have the real type from <semaphore.h>.      */
static int api_sem_init   (void *s, int pshared, unsigned val) { return sem_init   ((sem_t *)s, pshared, val); }
static int api_sem_destroy(void *s)                            { return sem_destroy ((sem_t *)s); }
static int api_sem_wait   (void *s)                            { return sem_wait    ((sem_t *)s); }
static int api_sem_trywait(void *s)                            { return sem_trywait ((sem_t *)s); }
static int api_sem_post   (void *s)                            { return sem_post    ((sem_t *)s); }

/* -------------------------------------------------------------------------
 * Time wrappers
 * ---------------------------------------------------------------------- */

static int api_usleep(unsigned usec)  { return usleep((useconds_t)usec); }

/* -------------------------------------------------------------------------
 * Kernel API table — single static instance
 *
 * Field order MUST match duneos_api_t exactly.  Never reorder.
 * ---------------------------------------------------------------------- */

static const duneos_api_t s_api = {
    .version = DUNEOS_ABI_VERSION,

    .fs = {
        .open     = api_open,
        .close    = close,
        .read     = (ssize_t (*)(int, void *, size_t))    api_read,
        .write    = (ssize_t (*)(int, const void *, size_t)) api_write,
        .lseek    = lseek,
        .fstat    = fstat,
        .stat     = stat,
        .unlink   = unlink,
        .rename   = rename,
        .dup      = api_dup,
        .dup2     = api_dup2,
        .fcntl    = (int (*)(int, int, int)) fcntl,
        .ioctl    = (int (*)(int, unsigned long, void *)) ioctl,
        .dprintf  = api_dprintf,
        .opendir  = api_opendir,
        .readdir  = api_readdir,
        .closedir = api_closedir,
        .mkdir    = api_mkdir,
        .rmdir    = rmdir,
    },

    .mem = {
        .malloc  = duneos_supervisor_app_malloc,
        .free    = duneos_supervisor_app_free,
        .realloc = duneos_supervisor_app_realloc,
        .calloc  = duneos_supervisor_app_calloc,
    },

    .thread = {
        .pthread_create        = api_pthread_create,
        .pthread_join          = pthread_join,
        .pthread_exit          = pthread_exit,
        .pthread_self          = pthread_self,
        .pthread_mutex_init    = pthread_mutex_init,
        .pthread_mutex_destroy = pthread_mutex_destroy,
        .pthread_mutex_lock    = pthread_mutex_lock,
        .pthread_mutex_trylock = pthread_mutex_trylock,
        .pthread_mutex_unlock  = pthread_mutex_unlock,
        .sem_init    = api_sem_init,
        .sem_destroy = api_sem_destroy,
        .sem_wait    = api_sem_wait,
        .sem_trywait = api_sem_trywait,
        .sem_post    = api_sem_post,
    },

    .time = {
        .clock_gettime = clock_gettime,
        .gettimeofday  = gettimeofday,
        .usleep        = api_usleep,
        .sleep         = sleep,
        .nanosleep     = duneos_nanosleep,
    },

    .sys = {
        .exit                       = duneos_exit,
        .service_ready              = duneos_service_ready,
        .wdt_reset                  = duneos_supervisor_wdt_reset,
        .send                       = duneos_send,
        .recv                       = api_recv,
        .supervisor_launch          = (int (*)(const char *)) duneos_supervisor_launch,
        .supervisor_running_count   = duneos_supervisor_running_count,
        .supervisor_list_slots      = api_supervisor_list_slots,
        .supervisor_restart_by_name = duneos_supervisor_restart_by_name,
        .vfs_list_mounts            = duneos_vfs_list_mounts,
        .vfs_flash_available        = duneos_vfs_flash_available,
        .vfs_sd_available           = duneos_vfs_sd_available,
        .loader_load                = api_loader_load,
        .loader_run                 = api_loader_run,
        .loader_run_captured        = api_loader_run_captured,
        .loader_unload              = api_loader_unload,
        .loader_get_manifest        = api_loader_get_manifest,
        .esp_restart                = esp_restart,
        .esp_get_free_heap_size     = esp_get_free_heap_size,
        .errno_fn                   = __errno,
    },
};

const duneos_api_t *duneos_api_get(void)
{
    return &s_api;
}
