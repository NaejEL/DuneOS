#include "duneos/dev_driver.h"
#include "duneos/spi_ioctl.h"
#include "duneos/klog.h"
#include "board_config.h"

#include "driver/spi_master.h"
#include "esp_err.h"

#include <errno.h>
#include <string.h>

static const char *TAG = "duneos/spi";

/*
 * Per-fd state.  Each open() adds a device to the bus with independent CS,
 * mode, and speed; close() removes it.  Fits in DUNEOS_DEV_PRIV_SIZE (16 B).
 */
typedef struct {
    spi_device_handle_t handle;    /* 4 bytes */
    int                 cs_gpio;   /* 4 bytes */
    uint32_t            speed_hz;  /* 4 bytes */
    uint8_t             mode;      /* 1 byte  */
    uint8_t             _pad[3];
} spi_priv_t;

_Static_assert(sizeof(spi_priv_t) <= DUNEOS_DEV_PRIV_SIZE, "spi_priv_t too large");

#define DEFAULT_SPEED_HZ  1000000u
#define DEFAULT_MODE      0u

static esp_err_t spi_dev_add(spi_priv_t *p)
{
    spi_device_interface_config_t cfg = {
        .mode           = p->mode,
        .clock_speed_hz = (int)p->speed_hz,
        .spics_io_num   = p->cs_gpio,
        .queue_size     = 1,
    };
    return spi_bus_add_device(DUNEOS_SPI1_HOST, &cfg, &p->handle);
}

/* Reconfigure an open device — remove and re-add with updated settings. */
static esp_err_t spi_dev_reconfig(spi_priv_t *p)
{
    if (p->handle) {
        spi_bus_remove_device(p->handle);
        p->handle = NULL;
    }
    return spi_dev_add(p);
}

static int spi_open_cb(duneos_devfd_t *fd, int flags)
{
    (void)flags;
    spi_priv_t *p = (spi_priv_t *)fd->priv;
    memset(p, 0, sizeof(*p));
    p->cs_gpio  = -1;
    p->speed_hz = DEFAULT_SPEED_HZ;
    p->mode     = DEFAULT_MODE;

    esp_err_t err = spi_dev_add(p);
    if (err != ESP_OK) {
        klog_e(TAG, "spi_bus_add_device: %s", esp_err_to_name(err));
        errno = EIO;
        return -1;
    }
    return 0;
}

static int spi_close_cb(duneos_devfd_t *fd)
{
    spi_priv_t *p = (spi_priv_t *)fd->priv;
    if (p->handle) {
        spi_bus_remove_device(p->handle);
        p->handle = NULL;
    }
    return 0;
}

static int spi_ioctl_cb(duneos_devfd_t *fd, int cmd, void *arg)
{
    spi_priv_t *p = (spi_priv_t *)fd->priv;

    switch (cmd) {
    case SPI_SET_MODE: {
        uint8_t mode = *(uint8_t *)arg;
        if (mode > 3) { errno = EINVAL; return -1; }
        p->mode = mode;
        if (spi_dev_reconfig(p) != ESP_OK) { errno = EIO; return -1; }
        return 0;
    }
    case SPI_SET_SPEED: {
        p->speed_hz = *(uint32_t *)arg;
        if (spi_dev_reconfig(p) != ESP_OK) { errno = EIO; return -1; }
        return 0;
    }
    case SPI_SET_CS: {
        p->cs_gpio = *(int *)arg;
        if (spi_dev_reconfig(p) != ESP_OK) { errno = EIO; return -1; }
        return 0;
    }
    case SPI_TRANSFER: {
        spi_xfer_t *xfr = (spi_xfer_t *)arg;
        spi_transaction_t t = {
            .length    = xfr->len * 8,
            .tx_buffer = xfr->tx_buf,
            .rx_buffer = xfr->rx_buf,
        };
        if (spi_device_transmit(p->handle, &t) != ESP_OK) { errno = EIO; return -1; }
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
    spi_transaction_t t = {
        .length    = len * 8,
        .tx_buffer = buf,
        .rx_buffer = NULL,
    };
    if (spi_device_transmit(p->handle, &t) != ESP_OK) { errno = EIO; return -1; }
    return (ssize_t)len;
}

static ssize_t spi_read_cb(duneos_devfd_t *fd, void *buf, size_t len)
{
    spi_priv_t *p = (spi_priv_t *)fd->priv;
    spi_transaction_t t = {
        .length    = len * 8,
        .tx_buffer = NULL,
        .rx_buffer = buf,
    };
    if (spi_device_transmit(p->handle, &t) != ESP_OK) { errno = EIO; return -1; }
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
    spi_bus_config_t bus_cfg = {
        .mosi_io_num     = DUNEOS_SPI1_MOSI_PIN,
        .miso_io_num     = DUNEOS_SPI1_MISO_PIN,
        .sclk_io_num     = DUNEOS_SPI1_CLK_PIN,
        .quadwp_io_num   = -1,
        .quadhd_io_num   = -1,
        .max_transfer_sz = 4096,
    };
    esp_err_t err = spi_bus_initialize(DUNEOS_SPI1_HOST, &bus_cfg, SPI_DMA_CH_AUTO);
    if (err != ESP_OK) {
        klog_e(TAG, "spi_bus_initialize: %s", esp_err_to_name(err));
        return;
    }
#endif
    /* When DUNEOS_SPI1_BUS_SHARED is set the bus was already initialised by
     * vfs.c (SD card mount) — just add devices on top of it. */
    duneos_dev_register(&s_drv_spi);
}
