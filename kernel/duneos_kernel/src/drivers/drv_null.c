#include "duneos/dev_driver.h"
#include "duneos/driver_init.h"
#include <string.h>

static ssize_t null_write(duneos_devfd_t *fd, const void *buf, size_t len)
{
    (void)fd; (void)buf;
    return (ssize_t)len;
}

static ssize_t zero_read(duneos_devfd_t *fd, void *buf, size_t len)
{
    (void)fd;
    memset(buf, 0, len);
    return (ssize_t)len;
}

static const duneos_dev_driver_t s_drv_null = {
    .name  = "null",
    .write = null_write,
};

static const duneos_dev_driver_t s_drv_zero = {
    .name = "zero",
    .read = zero_read,
};

void drv_null_register(void)
{
    duneos_dev_register(&s_drv_null);
    duneos_dev_register(&s_drv_zero);
}

DUNEOS_DRIVER_REGISTER(5, drv_null_register);
