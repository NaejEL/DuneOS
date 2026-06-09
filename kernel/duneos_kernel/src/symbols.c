#include "duneos/abi.h"
#include "duneos/supervisor.h"
#include "duneos/meminfo.h"
#include "duneos/vfs.h"

#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <stdarg.h>
#include <time.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/ioctl.h>
#include <dirent.h>
#include <pthread.h>
#include <semaphore.h>
#include <sys/select.h>

#include "duneos/task.h"
#include "esp_system.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#ifdef CONFIG_DUNEOS_DRV_WIFI
#include "duneos/wifi.h"
#include "lwip/sockets.h"
#include "lwip/netdb.h"
#include "lwip/inet.h"
#endif

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

extern int *__errno(void);   /* libc errno accessor — needed by any code that uses errno */

/* GCC soft-float / 64-bit runtime support — Xtensa has no 64-bit divide hw */
typedef long long          s_di_t;
typedef unsigned long long u_di_t;
extern s_di_t __divdi3(s_di_t, s_di_t);
extern s_di_t __moddi3(s_di_t, s_di_t);
extern u_di_t __udivdi3(u_di_t, u_di_t);
extern u_di_t __umoddi3(u_di_t, u_di_t);

/* duneos_exit is defined in supervisor.c and declared in supervisor.h */

/* nanosleep is not in ESP-IDF newlib — bridge via usleep */
int duneos_nanosleep(const struct timespec *req, struct timespec *rem)
{
    if (!req) { errno = EFAULT; return -1; }
    uint64_t us = (uint64_t)req->tv_sec * 1000000ULL + req->tv_nsec / 1000;
    if (us > 0) usleep((useconds_t)us);
    if (rem) { rem->tv_sec = 0; rem->tv_nsec = 0; }
    return 0;
}

/* dprintf is absent from PicoLibc — implement via vsnprintf + write */
static int duneos_dprintf(int fd, const char *fmt, ...)
{
    char buf[256];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (n > 0) write(fd, buf, (size_t)n);
    return n;
}

/* dup/dup2 are static inline in ESP-IDF newlib — cannot take their address */
static int duneos_dup(int fd)  { return fcntl(fd, F_DUPFD, 0); }
static int duneos_dup2(int fd, int newfd)
{
    if (fd == newfd) return newfd;
    close(newfd);
    return fcntl(fd, F_DUPFD, newfd);
}

/* loader.c (duneos_loader component) — forward-declared to avoid circular include */
esp_err_t duneos_loader_load(const char *path, duneos_app_t **out_app);
esp_err_t duneos_loader_run(duneos_app_t *app);
esp_err_t duneos_loader_run_captured(duneos_app_t *app, char **out_buf, size_t *out_len);
void      duneos_loader_unload(duneos_app_t *app);
const duneos_app_manifest_t *duneos_loader_get_manifest(const duneos_app_t *app);

/* Phase 20/22 — syscall wrappers with pointer validation */
static ssize_t duneos_read(int fd, void *buf, size_t len)
{
    if (!duneos_supervisor_check_app_writable_ptr(buf, len)) { errno = EFAULT; return -1; }
    return read(fd, buf, len);
}

static ssize_t duneos_write(int fd, const void *buf, size_t len)
{
    if (!duneos_supervisor_check_user_ptr(buf, len)) { errno = EFAULT; return -1; }
    return write(fd, buf, len);
}

static const duneos_symbol_t s_symbol_table[] = {

    /* ------------------------------------------------------------------ */
    /* POSIX — file I/O                                                    */
    /* ------------------------------------------------------------------ */
    SYM("open",         open           ),
    SYM("close",        close          ),
    SYM("read",         duneos_read    ),
    SYM("write",        duneos_write   ),
    SYM("lseek",        lseek     ),
    SYM("fstat",        fstat     ),
    SYM("stat",         stat      ),
    /* esp_vfs select — works on /dev fds (gpiochip0, input) AND sockets.
     * Not the lwIP-only lwip_select; no NET permission needed for device I/O. */
    SYM("select",       select    ),
    SYM_P("unlink",     unlink,   DUNEOS_PERM_FS_WRITE),
    SYM_P("rename",     rename,   DUNEOS_PERM_FS_WRITE),
    SYM("dup",          duneos_dup ),
    SYM("dup2",         duneos_dup2),
    SYM("fcntl",        fcntl      ),
    SYM("dprintf",      duneos_dprintf),
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
    /* POSIX — memory (per-app heap when configured, global heap fallback) */
    /* ------------------------------------------------------------------ */
    SYM("malloc",       duneos_supervisor_app_malloc  ),
    SYM("free",         duneos_supervisor_app_free    ),
    SYM("realloc",      duneos_supervisor_app_realloc ),
    SYM("calloc",       duneos_supervisor_app_calloc  ),

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
    /* GCC runtime — 64-bit integer arithmetic (Xtensa has no hw 64-div)  */
    /* ------------------------------------------------------------------ */
    SYM("__divdi3",  __divdi3  ),
    SYM("__moddi3",  __moddi3  ),
    SYM("__udivdi3", __udivdi3 ),
    SYM("__umoddi3", __umoddi3 ),

    /* ------------------------------------------------------------------ */
    /* System                                                              */
    /* ------------------------------------------------------------------ */
    SYM("esp_restart",           esp_restart               ),
    SYM("esp_get_free_heap_size", esp_get_free_heap_size    ),
    SYM("duneos_meminfo",        duneos_meminfo            ),

    /* ------------------------------------------------------------------ */
    /* DuneOS — lifecycle & IPC                                           */
    /* ------------------------------------------------------------------ */
    SYM("duneos_exit",                    duneos_exit                          ),
    SYM("duneos_run",                          duneos_supervisor_launch              ),
    SYM("duneos_supervisor_launch",            duneos_supervisor_launch              ),
    SYM("duneos_supervisor_running_count",     duneos_supervisor_running_count       ),
    SYM("duneos_supervisor_wait_for_completion", duneos_supervisor_wait_for_completion ),
    SYM("duneos_send",                         duneos_send                           ),
    SYM("duneos_recv",                         duneos_recv                           ),
    SYM("duneos_service_ready",                duneos_service_ready                  ),
    SYM("duneos_supervisor_list_slots",        duneos_supervisor_list_slots          ),
    SYM("duneos_supervisor_list_mem",          duneos_supervisor_list_mem            ),
    SYM("duneos_supervisor_chain",             duneos_supervisor_chain               ),
    SYM("duneos_vfs_list_mounts",              duneos_vfs_list_mounts                ),
    SYM("duneos_vfs_flash_available",          duneos_vfs_flash_available            ),
    SYM("duneos_vfs_sd_available",             duneos_vfs_sd_available               ),
    SYM("duneos_supervisor_restart_by_name",   duneos_supervisor_restart_by_name     ),
    SYM("duneos_wdt_reset",                    duneos_supervisor_wdt_reset           ),

    /* ------------------------------------------------------------------ */
    /* DuneOS — loader (for shell's `run` command)                        */
    /* ------------------------------------------------------------------ */
    SYM("duneos_loader_load",         duneos_loader_load         ),
    SYM("duneos_loader_run",          duneos_loader_run          ),
    SYM("duneos_loader_run_captured", duneos_loader_run_captured ),
    SYM("duneos_loader_unload",       duneos_loader_unload       ),
    SYM("duneos_loader_get_manifest", duneos_loader_get_manifest ),

#ifdef CONFIG_DUNEOS_DRV_WIFI
    /* ------------------------------------------------------------------ */
    /* WiFi STA — duneos_wifi_* wrappers                                  */
    /* ------------------------------------------------------------------ */
    SYM_P("duneos_wifi_init",            duneos_wifi_init,            DUNEOS_PERM_NET),
    SYM_P("duneos_wifi_sta_connect",     duneos_wifi_sta_connect,     DUNEOS_PERM_NET),
    SYM_P("duneos_wifi_sta_disconnect",  duneos_wifi_sta_disconnect,  DUNEOS_PERM_NET),
    SYM_P("duneos_wifi_get_info",        duneos_wifi_get_info,        DUNEOS_PERM_NET),
    SYM_P("duneos_netif_wait_ip",        duneos_netif_wait_ip,        DUNEOS_PERM_NET),

    /* ------------------------------------------------------------------ */
    /* BSD sockets — lwIP POSIX layer                                     */
    /* Apps reference these as "socket", "connect", etc.  The lwip_       */
    /* prefix is the real link-time name in the ESP-IDF binary.           */
    /* ------------------------------------------------------------------ */
    SYM_P("socket",       lwip_socket,     DUNEOS_PERM_NET),
    SYM_P("bind",         lwip_bind,       DUNEOS_PERM_NET),
    SYM_P("listen",       lwip_listen,     DUNEOS_PERM_NET),
    SYM_P("accept",       lwip_accept,     DUNEOS_PERM_NET),
    SYM_P("connect",      lwip_connect,    DUNEOS_PERM_NET),
    SYM_P("send",         lwip_send,       DUNEOS_PERM_NET),
    SYM_P("recv",         lwip_recv,       DUNEOS_PERM_NET),
    SYM_P("sendto",       lwip_sendto,     DUNEOS_PERM_NET),
    SYM_P("recvfrom",     lwip_recvfrom,   DUNEOS_PERM_NET),
    SYM_P("setsockopt",   lwip_setsockopt, DUNEOS_PERM_NET),
    SYM_P("getsockopt",   lwip_getsockopt, DUNEOS_PERM_NET),
    SYM_P("shutdown",     lwip_shutdown,   DUNEOS_PERM_NET),
    SYM_P("getaddrinfo",  lwip_getaddrinfo, DUNEOS_PERM_NET),
    SYM_P("freeaddrinfo", lwip_freeaddrinfo, DUNEOS_PERM_NET),
    SYM_P("inet_addr",    ipaddr_addr,      DUNEOS_PERM_NET),
    SYM_P("inet_ntoa_r",  ip4addr_ntoa_r,  DUNEOS_PERM_NET),
    SYM_P("inet_ntop",    lwip_inet_ntop,  DUNEOS_PERM_NET),
    SYM_P("inet_pton",    lwip_inet_pton,  DUNEOS_PERM_NET),
    SYM_P("poll",         lwip_poll,       DUNEOS_PERM_NET),  /* sockets only — /dev uses select() */
#endif /* CONFIG_DUNEOS_DRV_WIFI */

#ifdef CONFIG_DUNEOS_DRV_ETH
    SYM_P("duneos_eth_get_info",         duneos_eth_get_info,         DUNEOS_PERM_NET),
#endif

    /* Sentinel */
    { NULL, NULL, 0 },
};

const duneos_symbol_t *duneos_symbol_table_get(void)
{
    return s_symbol_table;
}

