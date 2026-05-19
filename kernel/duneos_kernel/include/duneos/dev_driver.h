#pragma once

/*
 * DuneOS kernel device driver interface.
 *
 * A driver registers itself with duneos_dev_register().  The devfs routes all
 * VFS calls (open/close/read/write/ioctl) to the registered driver by name.
 *
 * Per-fd state is stored in duneos_devfd_t.priv[DUNEOS_DEV_PRIV_SIZE].
 * Each driver casts priv to its own struct — no heap allocation needed.
 *
 * Adding a new /dev device:
 *   1. Create kernel/duneos_kernel/src/drivers/drv_mydev.c
 *   2. Define a static duneos_dev_driver_t with the required callbacks
 *   3. Export void drv_mydev_register(void) which calls duneos_dev_register()
 *   4. Call drv_mydev_register() from duneos_vfs_mount_dev() in vfs_dev.c
 *   5. Add drv_mydev.c to SRCS in CMakeLists.txt
 */

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <sys/types.h>

/* Bytes available in duneos_devfd_t.priv for driver-private per-fd data.
 * Enough for: klog (size_t pos = 4B), i2c (uint16_t addr = 2B), gpio (1B). */
#define DUNEOS_DEV_PRIV_SIZE   16

/* Maximum number of concurrently registered device drivers. */
#define DUNEOS_DEV_MAX_DRIVERS 16

struct duneos_devfd;

typedef struct {
    /* Name of the device node — must be unique, e.g. "uart0", "i2c-0". */
    const char *name;

    /* One-time hardware initialisation called at registration time.
     * NULL = no init required. Return 0 on success, -1 on failure.
     * Failure prevents the device from appearing in /dev. */
    int (*init)(void);

    /* VFS operations.  NULL pointer → default behaviour:
     *   open  → succeed (return 0)
     *   close → succeed (return 0)
     *   read  → EBADF (-1)
     *   write → EBADF (-1)
     *   ioctl → ENOTTY (-1) */
    int     (*open) (struct duneos_devfd *fd, int flags);
    int     (*close)(struct duneos_devfd *fd);
    ssize_t (*read) (struct duneos_devfd *fd,       void *buf, size_t len);
    ssize_t (*write)(struct duneos_devfd *fd, const void *buf, size_t len);
    int     (*ioctl)(struct duneos_devfd *fd, int cmd, void *arg);
} duneos_dev_driver_t;

/* Per open-file-descriptor state managed by vfs_dev.c. */
typedef struct duneos_devfd {
    bool                       open;
    int                        oflags;
    const duneos_dev_driver_t *drv;
    uint8_t                    priv[DUNEOS_DEV_PRIV_SIZE];
} duneos_devfd_t;

/*
 * Register a device driver.  Calls drv->init() if non-NULL.
 * Must be called before any app opens the device.
 * Returns 0 on success, -1 on failure (init() returned -1 or table full).
 */
int duneos_dev_register(const duneos_dev_driver_t *drv);
