/*
 * DuneOS devfs — /dev VFS routing layer.
 *
 * This file is a pure dispatcher: it implements the esp_vfs_t callbacks and
 * routes each call to the registered duneos_dev_driver_t for the open fd.
 *
 * No device-specific logic lives here.  Each device driver lives in
 * kernel/duneos_kernel/src/drivers/drv_*.c and registers itself via
 * duneos_dev_register() during duneos_vfs_mount_dev().
 */

#include "duneos/dev_driver.h"
#include "duneos/vfs.h"
#include "duneos/klog.h"

#include "esp_vfs.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

#include <sys/select.h>

#include <string.h>
#include <stdlib.h>
#include <stdarg.h>
#include <errno.h>
#include <fcntl.h>
#include <dirent.h>
#include <sys/stat.h>

#define DEVFS_MAX_FDS  16

static const char *TAG = "duneos/devfs";

/* ----- driver registry --------------------------------------------------- */

static const duneos_dev_driver_t *s_drivers[DUNEOS_DEV_MAX_DRIVERS];
static int                        s_driver_count = 0;

int duneos_dev_register(const duneos_dev_driver_t *drv)
{
    if (s_driver_count >= DUNEOS_DEV_MAX_DRIVERS) {
        klog_e(TAG, "driver table full, cannot register '%s'", drv->name);
        return -1;
    }
    if (drv->init) {
        if (drv->init() != 0) {
            klog_e(TAG, "drv '%s' init failed", drv->name);
            return -1;
        }
    }
    s_drivers[s_driver_count++] = drv;
    klog_i(TAG, "/dev/%s registered", drv->name);
    return 0;
}

static const duneos_dev_driver_t *driver_find(const char *name)
{
    for (int i = 0; i < s_driver_count; i++) {
        if (strcmp(s_drivers[i]->name, name) == 0)
            return s_drivers[i];
    }
    return NULL;
}

/* ----- fd table ---------------------------------------------------------- */

static duneos_devfd_t    s_fds[DEVFS_MAX_FDS];
static SemaphoreHandle_t s_lock;

static int fd_alloc(const duneos_dev_driver_t *drv, int oflags)
{
    for (int i = 0; i < DEVFS_MAX_FDS; i++) {
        if (!s_fds[i].open) {
            memset(&s_fds[i], 0, sizeof(s_fds[i]));
            s_fds[i].open   = true;
            s_fds[i].oflags = oflags;
            s_fds[i].drv    = drv;
            return i;
        }
    }
    return -1;
}

/* ----- VFS callbacks ----------------------------------------------------- */

static int devfs_open(const char *path, int flags, int mode)
{
    (void)mode;

    /* path arrives as "/uart0", "/i2c-0", etc. — strip leading slash */
    const char *name = (*path == '/') ? path + 1 : path;

    const duneos_dev_driver_t *drv = driver_find(name);
    if (!drv) { errno = ENOENT; return -1; }

    xSemaphoreTake(s_lock, portMAX_DELAY);
    int fd = fd_alloc(drv, flags);
    xSemaphoreGive(s_lock);
    if (fd < 0) { errno = EMFILE; return -1; }

    if (drv->open) {
        int rc = drv->open(&s_fds[fd], flags);
        if (rc < 0) {
            s_fds[fd].open = false;
            return -1;
        }
    }
    return fd;
}

static int devfs_close(int fd)
{
    if (fd < 0 || fd >= DEVFS_MAX_FDS || !s_fds[fd].open) {
        errno = EBADF; return -1;
    }
    int rc = 0;
    if (s_fds[fd].drv->close)
        rc = s_fds[fd].drv->close(&s_fds[fd]);
    s_fds[fd].open = false;
    return rc;
}

static ssize_t devfs_read(int fd, void *buf, size_t len)
{
    if (fd < 0 || fd >= DEVFS_MAX_FDS || !s_fds[fd].open) {
        errno = EBADF; return -1;
    }
    if (!s_fds[fd].drv->read) { errno = EBADF; return -1; }
    return s_fds[fd].drv->read(&s_fds[fd], buf, len);
}

static ssize_t devfs_write(int fd, const void *buf, size_t len)
{
    if (fd < 0 || fd >= DEVFS_MAX_FDS || !s_fds[fd].open) {
        errno = EBADF; return -1;
    }
    if (!s_fds[fd].drv->write) { errno = EBADF; return -1; }
    return s_fds[fd].drv->write(&s_fds[fd], buf, len);
}

static off_t devfs_lseek(int fd, off_t offset, int whence)
{
    (void)fd; (void)offset; (void)whence;
    errno = ESPIPE;
    return (off_t)-1;
}

static int devfs_ioctl(int fd, int cmd, va_list args)
{
    if (fd < 0 || fd >= DEVFS_MAX_FDS || !s_fds[fd].open) {
        errno = EBADF; return -1;
    }
    if (!s_fds[fd].drv->ioctl) { errno = ENOTTY; return -1; }
    void *arg = va_arg(args, void *);
    return s_fds[fd].drv->ioctl(&s_fds[fd], cmd, arg);
}

static int devfs_fstat(int fd, struct stat *st)
{
    if (fd < 0 || fd >= DEVFS_MAX_FDS || !s_fds[fd].open) {
        errno = EBADF; return -1;
    }
    memset(st, 0, sizeof(*st));
    st->st_mode = S_IFCHR | 0666;
    return 0;
}

static int devfs_stat(const char *path, struct stat *st)
{
    const char *name = (*path == '/') ? path + 1 : path;
    if (!driver_find(name)) { errno = ENOENT; return -1; }
    memset(st, 0, sizeof(*st));
    st->st_mode = S_IFCHR | 0666;
    return 0;
}

/* ----- directory listing (uses driver registry as source of truth) -------- */

typedef struct {
    DIR    dir;   /* must be first — VFS casts DIR* to our struct */
    int    idx;
} devfs_dir_t;

static DIR *devfs_opendir(const char *name)
{
    (void)name;
    devfs_dir_t *d = calloc(1, sizeof(devfs_dir_t));
    if (!d) { errno = ENOMEM; return NULL; }
    return (DIR *)d;
}

static struct dirent *devfs_readdir(DIR *pdir)
{
    devfs_dir_t *d = (devfs_dir_t *)pdir;
    if (d->idx >= s_driver_count) return NULL;
    static struct dirent ent;
    ent.d_type = DT_CHR;
    strncpy(ent.d_name, s_drivers[d->idx++]->name, sizeof(ent.d_name) - 1);
    ent.d_name[sizeof(ent.d_name) - 1] = '\0';
    return &ent;
}

static int devfs_closedir(DIR *pdir)
{
    free(pdir);
    return 0;
}

/* ----- driver auto-registration (Phase 24.9) ------------------------------ */
/*
 * Each driver file drops one DUNEOS_DRIVER_REGISTER(prio, fn) at file scope.
 * The macro emits a GCC constructor that records (prio, fn, name) into the
 * static registry below before app_main() runs. At mount time we sort by
 * priority and call each init.
 *
 * USB MSC + USB CDC are NOT in the registry — they have a different init
 * phase (drv_usb_preinit runs BEFORE the VFS mounts, from vfs.c). They
 * stay manually wired below.
 */
#include "duneos/driver_init.h"

#define DUNEOS_MAX_REGISTERED_DRIVERS 32

typedef struct {
    int                   prio;
    duneos_driver_init_fn init;
    const char           *name;
} driver_record_t;

static driver_record_t s_registry[DUNEOS_MAX_REGISTERED_DRIVERS];
static int             s_registry_count = 0;

void _duneos_driver_record(int prio, duneos_driver_init_fn init, const char *name)
{
    /* Runs from a constructor, before app_main. No FreeRTOS primitives
     * used — pure static array append. Single-threaded at this stage. */
    if (s_registry_count >= DUNEOS_MAX_REGISTERED_DRIVERS) {
        /* No klog here — klog isn't initialised at constructor time. The
         * runtime check at run_init() time will flag it. */
        return;
    }
    s_registry[s_registry_count].prio = prio;
    s_registry[s_registry_count].init = init;
    s_registry[s_registry_count].name = name;
    s_registry_count++;
}

void duneos_drivers_run_init(void)
{
    if (s_registry_count == 0) {
        klog_w(TAG, "driver registry is empty");
        return;
    }
    if (s_registry_count >= DUNEOS_MAX_REGISTERED_DRIVERS) {
        klog_e(TAG, "driver registry overflowed (>=%d) — some drivers may be missing",
               DUNEOS_MAX_REGISTERED_DRIVERS);
    }

    /* Insertion sort by priority — stable, in-place, O(n²) but n < 32. */
    for (int i = 1; i < s_registry_count; i++) {
        driver_record_t key = s_registry[i];
        int j = i - 1;
        while (j >= 0 && s_registry[j].prio > key.prio) {
            s_registry[j + 1] = s_registry[j];
            j--;
        }
        s_registry[j + 1] = key;
    }

    klog_i(TAG, "registering %d driver(s)", s_registry_count);
    for (int i = 0; i < s_registry_count; i++) {
        klog_d(TAG, "  [prio %d] %s", s_registry[i].prio, s_registry[i].name);
        s_registry[i].init();
    }
}

/* USB stays manually wired — different init phase from the registry above. */
#ifdef CONFIG_DUNEOS_DRV_USB_MSC
extern void drv_usb_register(void);
#endif
#if defined(CONFIG_DUNEOS_DRV_USB_CDC) && !defined(CONFIG_ESP_CONSOLE_USB_CDC)
extern void drv_usb_cdc_register(void);
#endif

/* ----- select() / poll() ------------------------------------------------- */
/*
 * esp_vfs select bridge (pattern mirrors ESP-IDF's UART VFS). On start_select
 * the fd_sets hold devfs-local fds; we clear them and remember which read fds
 * each select watches. A driver gaining data calls duneos_dev_select_notify(),
 * which FD_SETs the matching fds and wakes the waiter via the esp_vfs sem.
 * Only read-readiness is modelled (our selectable devices are read-driven).
 */
#define DEVFS_MAX_SELECTS 4

typedef struct {
    bool                 active;
    esp_vfs_select_sem_t sem;
    fd_set              *readfds;       /* live set — ready bits written here */
    fd_set               watch;         /* read fds this select waits on      */
} dev_select_t;

static dev_select_t s_selects[DEVFS_MAX_SELECTS];
static portMUX_TYPE s_sel_mux = portMUX_INITIALIZER_UNLOCKED;

static bool fd_readable(int fd)
{
    if (fd < 0 || fd >= DEVFS_MAX_FDS || !s_fds[fd].open) return true; /* error→ready */
    const duneos_dev_driver_t *drv = s_fds[fd].drv;
    if (drv && drv->readable) return drv->readable(&s_fds[fd]);
    return true;   /* no readiness callback → treat as always-readable */
}

static esp_err_t devfs_start_select(int nfds, fd_set *readfds, fd_set *writefds,
                                    fd_set *exceptfds, esp_vfs_select_sem_t sem,
                                    void **end_select_args)
{
    *end_select_args = NULL;
    int max = nfds < DEVFS_MAX_FDS ? nfds : DEVFS_MAX_FDS;

    fd_set watch = *readfds;
    FD_ZERO(readfds);
    FD_ZERO(writefds);
    FD_ZERO(exceptfds);

    portENTER_CRITICAL(&s_sel_mux);

    dev_select_t *sl = NULL;
    for (int i = 0; i < DEVFS_MAX_SELECTS; i++)
        if (!s_selects[i].active) { sl = &s_selects[i]; break; }
    if (!sl) { portEXIT_CRITICAL(&s_sel_mux); return ESP_ERR_NO_MEM; }

    sl->active  = true;
    sl->sem     = sem;
    sl->readfds = readfds;
    sl->watch   = watch;

    bool any = false;
    for (int fd = 0; fd < max; fd++) {
        if (FD_ISSET(fd, &watch) && fd_readable(fd)) { FD_SET(fd, readfds); any = true; }
    }
    if (any) esp_vfs_select_triggered(sem);

    portEXIT_CRITICAL(&s_sel_mux);

    *end_select_args = sl;
    return ESP_OK;
}

static esp_err_t devfs_end_select(void *end_select_args)
{
    dev_select_t *sl = end_select_args;
    if (!sl) return ESP_OK;
    portENTER_CRITICAL(&s_sel_mux);
    sl->active  = false;
    sl->readfds = NULL;
    portEXIT_CRITICAL(&s_sel_mux);
    return ESP_OK;
}

void duneos_dev_select_notify(const duneos_dev_driver_t *drv)
{
    portENTER_CRITICAL(&s_sel_mux);
    for (int i = 0; i < DEVFS_MAX_SELECTS; i++) {
        dev_select_t *sl = &s_selects[i];
        if (!sl->active) continue;
        bool hit = false;
        for (int fd = 0; fd < DEVFS_MAX_FDS; fd++)
            if (FD_ISSET(fd, &sl->watch) && s_fds[fd].open && s_fds[fd].drv == drv) {
                FD_SET(fd, sl->readfds); hit = true;
            }
        if (hit) esp_vfs_select_triggered(sl->sem);
    }
    portEXIT_CRITICAL(&s_sel_mux);
}

void duneos_dev_select_notify_isr(const duneos_dev_driver_t *drv, int *woken)
{
    BaseType_t w = pdFALSE;
    portENTER_CRITICAL_ISR(&s_sel_mux);
    for (int i = 0; i < DEVFS_MAX_SELECTS; i++) {
        dev_select_t *sl = &s_selects[i];
        if (!sl->active) continue;
        bool hit = false;
        for (int fd = 0; fd < DEVFS_MAX_FDS; fd++)
            if (FD_ISSET(fd, &sl->watch) && s_fds[fd].open && s_fds[fd].drv == drv) {
                FD_SET(fd, sl->readfds); hit = true;
            }
        if (hit) esp_vfs_select_triggered_isr(sl->sem, &w);
    }
    portEXIT_CRITICAL_ISR(&s_sel_mux);
    if (woken) *woken = (int)w;
}

/* ----- mount ------------------------------------------------------------- */

esp_err_t duneos_vfs_mount_dev(void)
{
    s_lock = xSemaphoreCreateMutex();
    if (!s_lock) return ESP_ERR_NO_MEM;

    static const esp_vfs_t vfs = {
        .flags    = ESP_VFS_FLAG_DEFAULT,
        .open     = devfs_open,
        .close    = devfs_close,
        .read     = devfs_read,
        .write    = devfs_write,
        .lseek    = devfs_lseek,
        .ioctl    = devfs_ioctl,
        .fstat    = devfs_fstat,
        .stat     = devfs_stat,
        .opendir  = devfs_opendir,
        .readdir  = devfs_readdir,
        .closedir = devfs_closedir,
        .start_select = devfs_start_select,
        .end_select   = devfs_end_select,
    };

    esp_err_t err = esp_vfs_register("/dev", &vfs, NULL);
    if (err != ESP_OK) {
        klog_e(TAG, "esp_vfs_register: %s", esp_err_to_name(err));
        vSemaphoreDelete(s_lock);
        return err;
    }

    /* Phase 24.9: walk the registry built by GCC constructors at boot.
     * Each driver's DUNEOS_DRIVER_REGISTER macro put itself in the list;
     * we sort by priority and call each init. */
    duneos_drivers_run_init();

#ifdef CONFIG_DUNEOS_DRV_USB_MSC
    /* TinyUSB is already running (drv_usb_preinit in vfs.c); register the SD card
     * as the MSC storage backend now that duneos_vfs_get_sd_card() is valid. */
    drv_usb_register();
#endif
#if defined(CONFIG_DUNEOS_DRV_USB_CDC) && !defined(CONFIG_ESP_CONSOLE_USB_CDC)
    /* Register /dev/ttyUSB0 as a standalone VFS path (not under /dev devfs).
     * Skipped when the ESP console owns CDC I/O — it registers its own handler. */
    drv_usb_cdc_register();
#endif

    klog_i(TAG, "/dev ready (%d devices)", s_driver_count);
    return ESP_OK;
}
