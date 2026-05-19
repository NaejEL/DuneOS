#include "duneos/dev_driver.h"
#include "duneos/hal_spi.h"
#include "duneos/spi_ioctl.h"
#include "duneos/klog.h"
#include "board_config.h"

#include <errno.h>
#include <string.h>

static const char *TAG = "duneos/spi";

/*
 * Per-fd state.  Each open() adds a device to the bus with independent CS,
 * mode, and speed; close() removes it.  Fits in DUNEOS_DEV_PRIV_SIZE (16 B).
 */
typedef struct {
    duneos_hal_spi_dev_t     *dev; /* 4 bytes (32-bit pointer) */
    duneos_hal_spi_dev_config_t cfg; /* cs_pin(4)+freq_hz(4)+mode(1)+pad(3) = 12 B */
} spi_priv_t;

_Static_assert(sizeof(spi_priv_t) <= DUNEOS_DEV_PRIV_SIZE, "spi_priv_t too large");

#define DEFAULT_SPEED_HZ  1000000u
#define DEFAULT_MODE      0u

static duneos_hal_spi_t *s_bus = NULL;

static int spi_open_cb(duneos_devfd_t *fd, int flags)
{
    (void)flags;
    spi_priv_t *p = (spi_priv_t *)fd->priv;
    memset(p, 0, sizeof(*p));
    p->cfg.cs_pin  = -1;
    p->cfg.freq_hz = DEFAULT_SPEED_HZ;
    p->cfg.mode    = DEFAULT_MODE;

    p->dev = duneos_hal_spi_dev_add(s_bus, &p->cfg);
    if (!p->dev) {
        klog_e(TAG, "spi_dev_add failed");
        errno = EIO;
        return -1;
    }
    return 0;
}

static int spi_close_cb(duneos_devfd_t *fd)
{
    spi_priv_t *p = (spi_priv_t *)fd->priv;
    duneos_hal_spi_dev_remove(p->dev);
    p->dev = NULL;
    return 0;
}

static int spi_ioctl_cb(duneos_devfd_t *fd, int cmd, void *arg)
{
    spi_priv_t *p = (spi_priv_t *)fd->priv;

    switch (cmd) {
    case SPI_SET_MODE: {
        uint8_t mode = *(uint8_t *)arg;
        if (mode > 3) { errno = EINVAL; return -1; }
        p->cfg.mode = mode;
        if (duneos_hal_spi_dev_reconfig(p->dev, &p->cfg) != 0) { errno = EIO; return -1; }
        return 0;
    }
    case SPI_SET_SPEED: {
        p->cfg.freq_hz = *(uint32_t *)arg;
        if (duneos_hal_spi_dev_reconfig(p->dev, &p->cfg) != 0) { errno = EIO; return -1; }
        return 0;
    }
    case SPI_SET_CS: {
        p->cfg.cs_pin = *(int *)arg;
        if (duneos_hal_spi_dev_reconfig(p->dev, &p->cfg) != 0) { errno = EIO; return -1; }
        return 0;
    }
    case SPI_TRANSFER: {
        spi_xfer_t *xfr = (spi_xfer_t *)arg;
        if (duneos_hal_spi_transfer(p->dev, xfr->tx_buf, xfr->rx_buf, xfr->len) != 0) {
            errno = EIO; return -1;
        }
        return 0;
    }
    default:
        errno = ENOTTY;
        return -1;
    }
}

static ssize_t spi_write_cb(duneos_devfd_t *fd, const void *buf, size_t len)
{
    spi_priv_t *p = (spi_priv_t *)fd->priv;
    if (duneos_hal_spi_transfer(p->dev, buf, NULL, len) != 0) { errno = EIO; return -1; }
    return (ssize_t)len;
}

static ssize_t spi_read_cb(duneos_devfd_t *fd, void *buf, size_t len)
{
    spi_priv_t *p = (spi_priv_t *)fd->priv;
    if (duneos_hal_spi_transfer(p->dev, NULL, buf, len) != 0) { errno = EIO; return -1; }
    return (ssize_t)len;
}

static const duneos_dev_driver_t s_drv_spi = {
    .name  = "spi-1",
    .open  = spi_open_cb,
    .close = spi_close_cb,
    .read  = spi_read_cb,
    .write = spi_write_cb,
    .ioctl = spi_ioctl_cb,
};

void drv_spi_register(void)
{
#ifndef DUNEOS_SPI1_BUS_SHARED
    /* Bus is independent — initialise it here. */
    duneos_hal_spi_bus_config_t bus_cfg = {
        .bus_id          = DUNEOS_SPI1_HOST,
        .mosi_pin        = DUNEOS_SPI1_MOSI_PIN,
        .miso_pin        = DUNEOS_SPI1_MISO_PIN,
        .clk_pin         = DUNEOS_SPI1_CLK_PIN,
        .max_transfer_sz = 4096,
    };
    s_bus = duneos_hal_spi_bus_init(&bus_cfg);
    if (!s_bus) {
        klog_e(TAG, "spi_bus_init failed");
        return;
    }
#else
    /* Bus was already initialised by vfs.c (SD card mount). Attach to it. */
    s_bus = duneos_hal_spi_bus_attach(DUNEOS_SPI1_HOST);
    if (!s_bus) {
        klog_e(TAG, "spi_bus_attach failed");
        return;
    }
#endif
    duneos_dev_register(&s_drv_spi);
}
