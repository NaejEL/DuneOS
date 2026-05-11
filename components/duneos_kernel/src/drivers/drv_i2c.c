#include "duneos/dev_driver.h"
#include "duneos/i2c_ioctl.h"
#include "duneos/klog.h"
#include "i2c_bus.h"

#include <errno.h>
#include <stdint.h>

/* Per-fd state: current target address (7-bit). */
typedef struct {
    uint16_t addr;
} i2c_priv_t;

_Static_assert(sizeof(i2c_priv_t) <= DUNEOS_DEV_PRIV_SIZE, "i2c_priv_t too large");

static int i2c_ioctl_cb(duneos_devfd_t *fd, int cmd, void *arg)
{
    i2c_priv_t *p = (i2c_priv_t *)fd->priv;
    switch (cmd) {
    case I2C_SET_ADDR:
        p->addr = *(uint16_t *)arg;
        return 0;
    case I2C_SET_SPEED:
        /* accepted and silently ignored — bus speed is fixed at init time */
        return 0;
    case I2C_RDWR: {
        i2c_rdwr_t *xfr = arg;
        esp_err_t err = i2c_bus_write_read(0, p->addr,
                                            xfr->tx_buf, xfr->tx_len,
                                            xfr->rx_buf, xfr->rx_len);
        if (err != ESP_OK) { errno = EIO; return -1; }
        return 0;
    }
    default:
        errno = ENOTTY;
        return -1;
    }
}

static ssize_t i2c_write_cb(duneos_devfd_t *fd, const void *buf, size_t len)
{
    i2c_priv_t *p = (i2c_priv_t *)fd->priv;
    esp_err_t err = i2c_bus_write_read(0, p->addr,
                                        (const uint8_t *)buf, (uint16_t)len,
                                        NULL, 0);
    if (err != ESP_OK) { errno = EIO; return -1; }
    return (ssize_t)len;
}

static ssize_t i2c_read_cb(duneos_devfd_t *fd, void *buf, size_t len)
{
    i2c_priv_t *p = (i2c_priv_t *)fd->priv;
    esp_err_t err = i2c_bus_write_read(0, p->addr,
                                        NULL, 0,
                                        (uint8_t *)buf, (uint16_t)len);
    if (err != ESP_OK) { errno = EIO; return -1; }
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
