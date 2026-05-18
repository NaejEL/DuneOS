/*
 * DuneOS devfs — /dev VFS routing layer.
 *
 * This file is a pure dispatcher: it implements the esp_vfs_t callbacks and
 * routes each call to the registered duneos_dev_driver_t for the open fd.
 *
 * No device-specific logic lives here.  Each device driver lives in
 * components/duneos_kernel/src/drivers/drv_*.c and registers itself via
 * duneos_dev_register() during duneos_vfs_mount_dev().
 */

#include "duneos/dev_driver.h"
#include "duneos/vfs.h"
#include "duneos/klog.h"

#include "esp_vfs.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

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

esp_err_t duneos_dev_register(const duneos_dev_driver_t *drv)
{
    if (s_driver_count >= DUNEOS_DEV_MAX_DRIVERS) {
        klog_e(TAG, "driver table full, cannot register '%s'", drv->name);
        return ESP_ERR_NO_MEM;
    }
    if (drv->init) {
        esp_err_t err = drv->init();
        if (err != ESP_OK) {
            klog_e(TAG, "drv '%s' init failed: %s", drv->name, esp_err_to_name(err));
            return err;
        }
    }
    s_drivers[s_driver_count++] = drv;
    klog_i(TAG, "/dev/%s registered", drv->name);
    return ESP_OK;
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

/* ----- driver forward declarations --------------------------------------- */

#ifdef CONFIG_DUNEOS_DRV_NULL
extern void drv_null_register(void);
#endif
#ifdef CONFIG_DUNEOS_DRV_UART
extern void drv_uart_register(void);
#endif
#ifdef CONFIG_DUNEOS_DRV_KLOG
extern void drv_klog_register(void);
#endif
#ifdef CONFIG_DUNEOS_DRV_GPIO
extern void drv_gpio_register(void);
#endif
#ifdef CONFIG_DUNEOS_DRV_I2C
extern void drv_i2c_register(void);
#endif
#ifdef CONFIG_DUNEOS_DRV_BATTERY_ADC_SIMPLE
extern void drv_battery_adc_simple_register(void);
#endif
#ifdef CONFIG_DUNEOS_DRV_BATTERY_BQ27220
extern void drv_battery_bq27220_register(void);
#endif
#ifdef CONFIG_DUNEOS_DRV_SPI
extern void drv_spi_register(void);
#endif
#ifdef CONFIG_DUNEOS_DRV_INPUT
extern void drv_input_register(void);
#endif
#ifdef CONFIG_DUNEOS_DRV_DISP
extern void drv_disp_st7789_register(void);
#endif
#ifdef CONFIG_DUNEOS_DRV_FB
extern void drv_fb_st7789_register(void);
#endif
#ifdef CONFIG_DUNEOS_DRV_RAW80211
extern void drv_raw80211_register(void);
#endif
#ifdef CONFIG_DUNEOS_DRV_USB_MSC
/* Phase 2 of USB init: MSC storage backend (TinyUSB already up from preinit). */
extern void drv_usb_register(void);
#endif
#if defined(CONFIG_DUNEOS_DRV_USB_CDC) && !defined(CONFIG_ESP_CONSOLE_USB_CDC)
extern void drv_usb_cdc_register(void);
#endif

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
    };

    esp_err_t err = esp_vfs_register("/dev", &vfs, NULL);
    if (err != ESP_OK) {
        klog_e(TAG, "esp_vfs_register: %s", esp_err_to_name(err));
        vSemaphoreDelete(s_lock);
        return err;
    }

    /* Register drivers — order determines ls /dev listing order. */
#ifdef CONFIG_DUNEOS_DRV_NULL
    drv_null_register();
#endif
#ifdef CONFIG_DUNEOS_DRV_UART
    drv_uart_register();
#endif
#ifdef CONFIG_DUNEOS_DRV_KLOG
    drv_klog_register();
#endif
#ifdef CONFIG_DUNEOS_DRV_GPIO
    drv_gpio_register();
#endif
#ifdef CONFIG_DUNEOS_DRV_I2C
    drv_i2c_register();
#endif
#ifdef CONFIG_DUNEOS_DRV_BATTERY_ADC_SIMPLE
    drv_battery_adc_simple_register();
#endif
#ifdef CONFIG_DUNEOS_DRV_BATTERY_BQ27220
    drv_battery_bq27220_register();
#endif
#ifdef CONFIG_DUNEOS_DRV_SPI
    drv_spi_register();
#endif
#ifdef CONFIG_DUNEOS_DRV_INPUT
    drv_input_register();
#endif
#ifdef CONFIG_DUNEOS_DRV_DISP
    drv_disp_st7789_register();
#endif
#ifdef CONFIG_DUNEOS_DRV_FB
    drv_fb_st7789_register();
#endif
#ifdef CONFIG_DUNEOS_DRV_RAW80211
    drv_raw80211_register();
#endif

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
