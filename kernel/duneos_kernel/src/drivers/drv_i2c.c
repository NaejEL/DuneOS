#include "duneos/dev_driver.h"
#include "duneos/driver_init.h"
#include "duneos/i2c_ioctl.h"
#include "i2c_bus.h"

#include <errno.h>
#include <stdint.h>

/* Per-fd state: address set with I2C_SLAVE, used by read()/write(). */
typedef struct {
    uint16_t addr;
} i2c_priv_t;

_Static_assert(sizeof(i2c_priv_t) <= DUNEOS_DEV_PRIV_SIZE, "i2c_priv_t too large");

static int i2c_ioctl_cb(duneos_devfd_t *fd, int cmd, void *arg)
{
    i2c_priv_t *p = (i2c_priv_t *)fd->priv;
    switch (cmd) {
    case I2C_SLAVE:
        p->addr = (uint16_t)(uintptr_t)arg;     /* address passed by value, Linux-style */
        return 0;
    case I2C_RDWR: {
        struct i2c_rdwr_ioctl_data *d = arg;
        if (!d || !d->msgs || d->nmsgs == 0) { errno = EINVAL; return -1; }
        int n = i2c_transfer(d->msgs, (int)d->nmsgs);
        if (n < (int)d->nmsgs) { errno = EIO; return -1; }
        return 0;
    }
    case I2C_RESET:
        return i2c_bus_reinit();
    default:
        errno = ENOTTY;
        return -1;
    }
}

static ssize_t i2c_write_cb(duneos_devfd_t *fd, const void *buf, size_t len)
{
    if (len == 0) return 0;                      /* POSIX no-op; probe via I2C_RDWR */
    i2c_priv_t *p = (i2c_priv_t *)fd->priv;
    struct i2c_msg m = {
        .addr  = p->addr,
        .flags = 0,
        .len   = (uint16_t)len,
        .buf   = (uint8_t *)(uintptr_t)buf,
    };
    if (i2c_transfer(&m, 1) != 1) { errno = EIO; return -1; }
    return (ssize_t)len;
}

static ssize_t i2c_read_cb(duneos_devfd_t *fd, void *buf, size_t len)
{
    if (len == 0) return 0;
    i2c_priv_t *p = (i2c_priv_t *)fd->priv;
    struct i2c_msg m = {
        .addr  = p->addr,
        .flags = I2C_M_RD,
        .len   = (uint16_t)len,
        .buf   = (uint8_t *)buf,
    };
    if (i2c_transfer(&m, 1) != 1) { errno = EIO; return -1; }
    return (ssize_t)len;
}

static const duneos_dev_driver_t s_drv_i2c = {
    .name  = "i2c-0",
    .read  = i2c_read_cb,
    .write = i2c_write_cb,
    .ioctl = i2c_ioctl_cb,
};

void drv_i2c_register(void)
{
    i2c_bus_init();
    duneos_dev_register(&s_drv_i2c);
}

DUNEOS_DRIVER_REGISTER(1, drv_i2c_register);
