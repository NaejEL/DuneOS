#include "duneos/abi.h"
#include "duneos/supervisor.h"

#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <time.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/ioctl.h>
#include <dirent.h>
#include <pthread.h>
#include <semaphore.h>

#include "duneos/task.h"
#include "esp_system.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

/*
 * Kernel export symbol table — the ABI contract between DuneOS and apps.
 *
 * Rules:
 *  - Export POSIX primitives, not implementation details.
 *  - No FreeRTOS symbols — apps must stay RTOS-agnostic.
 *  - No printf: routes through newlib → _write() → VFS automatically.
 *  - Use required_perm to gate access to sensitive operations.
 *
 * SYM(name, ptr)         — always allowed (required_perm = 0)
 * SYM_P(name, ptr, perm) — requires perm bits in app manifest
 *
 * Adding a symbol is backwards-compatible.
 * Removing or renaming requires a DUNEOS_ABI_VERSION bump.
 */

#define SYM(n, p)         { (n), (void *)(p), 0 }
#define SYM_P(n, p, perm) { (n), (void *)(p), (perm) }

extern int *__errno(void);   /* newlib errno accessor — needed by any code that uses errno */
void duneos_exit(int code);
int  duneos_nanosleep(const struct timespec *req, struct timespec *rem);
/* dup/dup2 are static inline in ESP-IDF newlib — implement via fcntl */
static int duneos_dup(int fd)
{
    return fcntl(fd, F_DUPFD, 0);
}
static int duneos_dup2(int fd, int newfd)
{
    if (fd == newfd) return newfd;
    close(newfd);           /* make newfd available */
    return fcntl(fd, F_DUPFD, newfd);  /* F_DUPFD returns lowest fd >= newfd */
}

/* loader.c (duneos_loader component) — forward-declared to avoid circular include */
esp_err_t duneos_loader_load(const char *path, duneos_app_t **out_app);
esp_err_t duneos_loader_run(duneos_app_t *app);
void      duneos_loader_unload(duneos_app_t *app);
const duneos_app_manifest_t *duneos_loader_get_manifest(const duneos_app_t *app);

static const duneos_symbol_t s_symbol_table[] = {

    /* ------------------------------------------------------------------ */
    /* POSIX — file I/O                                                    */
    /* ------------------------------------------------------------------ */
    SYM("open",         open      ),
    SYM("close",        close     ),
    SYM("read",         read      ),
    SYM("write",        write     ),
    SYM("lseek",        lseek     ),
    SYM("fstat",        fstat     ),
    SYM("stat",         stat      ),
    SYM_P("unlink",     unlink,   DUNEOS_PERM_FS_WRITE),
    SYM_P("rename",     rename,   DUNEOS_PERM_FS_WRITE),
    SYM("dup",          duneos_dup ),
    SYM("dup2",         duneos_dup2),
    SYM("dprintf",      dprintf   ),
    SYM("ioctl",        ioctl     ),

    /* ------------------------------------------------------------------ */
    /* POSIX — directory                                                   */
    /* ------------------------------------------------------------------ */
    SYM_P("opendir",    opendir,  DUNEOS_PERM_FS_READ),
    SYM_P("readdir",    readdir,  DUNEOS_PERM_FS_READ),
    SYM("closedir",     closedir  ),
    SYM_P("mkdir",      mkdir,    DUNEOS_PERM_FS_WRITE),
    SYM_P("rmdir",      rmdir,    DUNEOS_PERM_FS_WRITE),

    /* ------------------------------------------------------------------ */
    /* POSIX — memory                                                      */
    /* ------------------------------------------------------------------ */
    SYM("malloc",       malloc    ),
    SYM("free",         free      ),
    SYM("realloc",      realloc   ),
    SYM("calloc",       calloc    ),

    /* ------------------------------------------------------------------ */
    /* POSIX — threads & synchronisation                                   */
    /* ------------------------------------------------------------------ */
    SYM("pthread_create",           pthread_create           ),
    SYM("pthread_join",             pthread_join             ),
    SYM("pthread_exit",             pthread_exit             ),
    SYM("pthread_self",             pthread_self             ),
    SYM("pthread_mutex_init",       pthread_mutex_init       ),
    SYM("pthread_mutex_destroy",    pthread_mutex_destroy    ),
    SYM("pthread_mutex_lock",       pthread_mutex_lock       ),
    SYM("pthread_mutex_trylock",    pthread_mutex_trylock    ),
    SYM("pthread_mutex_unlock",     pthread_mutex_unlock     ),
    SYM("sem_init",                 sem_init                 ),
    SYM("sem_destroy",              sem_destroy              ),
    SYM("sem_wait",                 sem_wait                 ),
    SYM("sem_trywait",              sem_trywait              ),
    SYM("sem_post",                 sem_post                 ),

    /* ------------------------------------------------------------------ */
    /* POSIX — time                                                        */
    /* ------------------------------------------------------------------ */
    SYM("clock_gettime",    clock_gettime    ),
    SYM("gettimeofday",     gettimeofday     ),
    SYM("usleep",           usleep           ),
    SYM("sleep",            sleep            ),
    SYM("nanosleep",        duneos_nanosleep ),

    /* ------------------------------------------------------------------ */
    /* String & memory utilities (pure — no I/O)                          */
    /* ------------------------------------------------------------------ */
    SYM("memcpy",   memcpy   ),
    SYM("memmove",  memmove  ),
    SYM("memset",   memset   ),
    SYM("memcmp",   memcmp   ),
    SYM("strlen",   strlen   ),
    SYM("strnlen",  strnlen  ),
    SYM("strcpy",   strcpy   ),
    SYM("strncpy",  strncpy  ),
    SYM("strlcpy",  strlcpy  ),
    SYM("strcat",   strcat   ),
    SYM("strncat",  strncat  ),
    SYM("strcmp",   strcmp   ),
    SYM("strncmp",  strncmp  ),
    SYM("strchr",   strchr   ),
    SYM("strrchr",  strrchr  ),
    SYM("strstr",   strstr   ),
    SYM("strtol",   strtol   ),
    SYM("strtoul",  strtoul  ),
    SYM("atoi",     atoi     ),
    SYM("sprintf",   sprintf   ),
    SYM("snprintf",  snprintf  ),
    SYM("vsnprintf", vsnprintf ),
    SYM("vsprintf",  vsprintf  ),
    SYM("sscanf",    sscanf    ),
    SYM("strerror",  strerror  ),
    SYM("__errno",   __errno   ),

    /* ------------------------------------------------------------------ */
    /* System                                                              */
    /* ------------------------------------------------------------------ */
    SYM("esp_restart",           esp_restart               ),
    SYM("esp_get_free_heap_size", esp_get_free_heap_size    ),

    /* ------------------------------------------------------------------ */
    /* DuneOS — lifecycle & IPC                                           */
    /* ------------------------------------------------------------------ */
    SYM("duneos_exit",                    duneos_exit                          ),
    SYM("duneos_run",                     duneos_supervisor_launch              ),
    SYM("duneos_send",                    duneos_send                           ),
    SYM("duneos_recv",                    duneos_recv                           ),
    SYM("duneos_service_ready",           duneos_service_ready                  ),
    SYM("duneos_supervisor_list_slots",   duneos_supervisor_list_slots          ),
    SYM("duneos_supervisor_restart_by_name", duneos_supervisor_restart_by_name ),

    /* ------------------------------------------------------------------ */
    /* DuneOS — loader (for shell's `run` command)                        */
    /* ------------------------------------------------------------------ */
    SYM("duneos_loader_load",         duneos_loader_load         ),
    SYM("duneos_loader_run",          duneos_loader_run          ),
    SYM("duneos_loader_unload",       duneos_loader_unload       ),
    SYM("duneos_loader_get_manifest", duneos_loader_get_manifest ),

    /* Sentinel */
    { NULL, NULL, 0 },
};

const duneos_symbol_t *duneos_symbol_table_get(void)
{
    return s_symbol_table;
}

void duneos_exit(int code)
{
    duneos_supervisor_app_exited(code);
    vTaskDelete(NULL);
}

int duneos_nanosleep(const struct timespec *req, struct timespec *rem)
{
    if (!req) return -1;
    uint64_t us = (uint64_t)req->tv_sec * 1000000ULL + req->tv_nsec / 1000;
    if (us > 0) usleep((useconds_t)us);
    if (rem) { rem->tv_sec = 0; rem->tv_nsec = 0; }
    return 0;
}
