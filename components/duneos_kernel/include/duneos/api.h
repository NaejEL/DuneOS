#pragma once

/*
 * DuneOS typed kernel API table — ABI v3.
 *
 * The kernel maintains one static instance of duneos_api_t (returned by
 * duneos_api_get()).  When loading an app built with libdune.a, the loader
 * writes the kernel instance address into the app's __duneos_api_ptr symbol
 * before calling app_main.  libdune.a wrapper functions then dispatch all
 * POSIX calls through this table, providing the app with a typed, stable ABI
 * without per-symbol string-based resolution at load time.
 *
 * ABI stability rules (enforced by bumping DUNEOS_ABI_VERSION on any break):
 *   - Never reorder or rename existing function pointer fields.
 *   - Never change an existing function pointer's signature.
 *   - New entries MUST be appended at the END of the relevant sub-struct.
 *   - New sub-structs may be added at the END of duneos_api_t.
 *   - Apps that strictly require v3 features declare required_abi_version: 3.
 *
 * Backward compatibility: apps without __duneos_api_ptr (ABI v1/v2) continue
 * working via per-symbol resolution from duneos_symbol_table_get().
 *
 * Struct layout note: on Xtensa 32-bit, Newlib (kernel) and PicoLibc (apps)
 * share compatible layouts for all types used here — both follow the same
 * POSIX 32-bit ILP32 model.  This assumption is documented, not enforced at
 * compile time.  A future duneos_stat_t / duneos_dirent_t could replace the
 * standard types if a mismatch is ever detected.
 */

#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>
#include <sys/types.h>   /* ssize_t, off_t */
#include <sys/stat.h>    /* struct stat */
#include <time.h>        /* struct timespec, clockid_t */
#include <sys/time.h>    /* struct timeval */
#include <pthread.h>     /* pthread_t, pthread_mutex_t, pthread_attr_t */
#if __has_include(<semaphore.h>)
#include <semaphore.h>
#else
/* bare-metal PicoLibc lacks semaphore.h.  On ESP-IDF/FreeRTOS the POSIX sem
 * is a heap-allocated object referenced by pointer, so sem_t is pointer-sized.
 * Kernel-side wrappers in api.c see the real sem_t via their own semaphore.h. */
typedef void *sem_t;
#endif

/* Symbol injected by the loader to hand the API table to the app.
 * libdune.a defines this as:  const duneos_api_t *__duneos_api_ptr = NULL;
 * The loader writes duneos_api_get() here before calling app_main.          */
#define DUNEOS_API_SYMBOL  "__duneos_api_ptr"

/* -------------------------------------------------------------------------
 * File-system sub-table
 *
 * Varargs functions (open, fcntl, ioctl, dprintf) take explicit concrete
 * arguments here so that function-pointer types are fully checkable by the
 * compiler.  libdune.a wrappers extract the varargs and forward as fixed args.
 *
 * Directory handles are void * in this table to avoid pulling a concrete
 * DIR / struct dirent definition into the ABI boundary.  libdune.a casts to
 * the correct types on both sides.
 * ---------------------------------------------------------------------- */
typedef struct {
    /* File operations */
    int      (*open)   (const char *path, int flags, int mode);
    int      (*close)  (int fd);
    ssize_t  (*read)   (int fd, void *buf, size_t n);       /* validated */
    ssize_t  (*write)  (int fd, const void *buf, size_t n); /* validated */
    off_t    (*lseek)  (int fd, off_t off, int whence);
    int      (*fstat)  (int fd, struct stat *st);
    int      (*stat)   (const char *path, struct stat *st);
    int      (*unlink) (const char *path);
    int      (*rename) (const char *old_path, const char *new_path);
    int      (*dup)    (int fd);
    int      (*dup2)   (int fd, int newfd);
    int      (*fcntl)  (int fd, int cmd, int arg);
    int      (*ioctl)  (int fd, unsigned long req, void *arg);
    int      (*dprintf)(int fd, const char *fmt, ...);
    /* Directory operations — void* avoids DIR/dirent layout assumptions */
    void    *(*opendir) (const char *path);        /* returns DIR * */
    void    *(*readdir) (void *dir);               /* returns struct dirent * */
    int      (*closedir)(void *dir);
    int      (*mkdir)   (const char *path, unsigned mode);
    int      (*rmdir)   (const char *path);
} duneos_fs_api_t;

/* -------------------------------------------------------------------------
 * Memory sub-table
 *
 * Routes to the per-app isolated heap when the manifest declares heap_size,
 * or falls back to the global heap.  This matches the behaviour of
 * duneos_supervisor_app_malloc / free / realloc / calloc.
 * ---------------------------------------------------------------------- */
typedef struct {
    void *(*malloc) (size_t size);
    void  (*free)   (void *ptr);
    void *(*realloc)(void *ptr, size_t size);
    void *(*calloc) (size_t n, size_t size);
} duneos_mem_api_t;

/* -------------------------------------------------------------------------
 * Thread & synchronisation sub-table
 *
 * Standard POSIX pthreads and POSIX semaphores.  ESP-IDF implements these
 * over FreeRTOS; the concrete struct sizes are determined by ESP-IDF, not
 * by the libc, so they are identical regardless of Newlib vs. PicoLibc.
 * ---------------------------------------------------------------------- */
typedef struct {
    int        (*pthread_create)       (pthread_t *, const pthread_attr_t *,
                                        void *(*)(void *), void *);
    int        (*pthread_join)         (pthread_t, void **);
    void       (*pthread_exit)         (void *);
    pthread_t  (*pthread_self)         (void);
    int        (*pthread_mutex_init)   (pthread_mutex_t *,
                                        const pthread_mutexattr_t *);
    int        (*pthread_mutex_destroy)(pthread_mutex_t *);
    int        (*pthread_mutex_lock)   (pthread_mutex_t *);
    int        (*pthread_mutex_trylock)(pthread_mutex_t *);
    int        (*pthread_mutex_unlock) (pthread_mutex_t *);
    /* sem_t * replaced with void * — semaphore.h absent in bare-metal PicoLibc */
    int        (*sem_init)    (void *sem, int pshared, unsigned val);
    int        (*sem_destroy) (void *sem);
    int        (*sem_wait)    (void *sem);
    int        (*sem_trywait) (void *sem);
    int        (*sem_post)    (void *sem);
} duneos_thread_api_t;

/* -------------------------------------------------------------------------
 * Time sub-table
 * ---------------------------------------------------------------------- */
typedef struct {
    int      (*clock_gettime)(clockid_t id, struct timespec *tp);
    int      (*gettimeofday) (struct timeval *tv, void *tz);
    int      (*usleep)       (unsigned usec);
    unsigned (*sleep)        (unsigned sec);
    int      (*nanosleep)    (const struct timespec *req, struct timespec *rem);
} duneos_time_api_t;

/* -------------------------------------------------------------------------
 * DuneOS system sub-table
 *
 * Lifecycle, IPC, VFS queries, dynamic loader, and misc system calls.
 *
 * Loader functions use void * instead of duneos_app_t * to avoid a circular
 * dependency between api.h and loader.h (loader.h → abi.h → api.h would
 * create a cycle through duneos_loader's component dependency on
 * duneos_kernel).  libdune.a wrappers cast to the correct types.
 *
 * IPC: duneos_recv's out parameter is void * (wraps duneos_msg_t *) for the
 * same reason — duneos_msg_t is defined in supervisor.h which apps don't
 * include directly.
 * ---------------------------------------------------------------------- */
typedef struct {
    /* App lifecycle */
    void     (*exit)            (int code);
    void     (*service_ready)   (void);
    void     (*wdt_reset)       (void);

    /* Inter-app messaging */
    int      (*send)            (const char *dest, const void *data, size_t len);
    int      (*recv)            (void *out_msg, uint32_t timeout_ms);

    /* Supervisor control */
    int      (*supervisor_launch)            (const char *path);
    int      (*supervisor_running_count)     (void);
    int      (*supervisor_list_slots)        (void *out, int count);
    int      (*supervisor_restart_by_name)   (const char *name);

    /* VFS state queries */
    int      (*vfs_list_mounts)     (char (*out)[32], int max);
    bool     (*vfs_flash_available) (void);
    bool     (*vfs_sd_available)    (void);

    /* Dynamic loader (used by the shell's `run` command) */
    int      (*loader_load)          (const char *path, void **out_app);
    int      (*loader_run)           (void *app);
    int      (*loader_run_captured)  (void *app, char **out_buf, size_t *out_len);
    void     (*loader_unload)        (void *app);
    const void *(*loader_get_manifest)(const void *app);

    /* Low-level system */
    void     (*esp_restart)          (void);
    uint32_t (*esp_get_free_heap_size)(void);

    /* errno accessor — apps compiled with -D__PICOLIBC_ERRNO_FUNCTION=__errno
     * route errno through this; it must return the calling task's per-task
     * errno pointer (same value as the kernel's __errno()).                   */
    int     *(*errno_fn)             (void);
} duneos_sys_api_t;

/* -------------------------------------------------------------------------
 * Root API table
 *
 * The `version` field holds DUNEOS_ABI_VERSION at kernel build time so that
 * apps can detect which features are available without relying solely on the
 * required_abi_version manifest field.
 * ---------------------------------------------------------------------- */
typedef struct {
    uint32_t             version;   /* DUNEOS_ABI_VERSION at kernel build time */
    duneos_fs_api_t      fs;
    duneos_mem_api_t     mem;
    duneos_thread_api_t  thread;
    duneos_time_api_t    time;
    duneos_sys_api_t     sys;
} duneos_api_t;

/* Returns the kernel's single, always-valid API table instance.
 * Valid after duneos_loader_init() — never NULL at app-launch time.         */
const duneos_api_t *duneos_api_get(void);
