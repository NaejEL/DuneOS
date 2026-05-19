/*
 * SPI HAL backend — ESP-IDF implementation.
 * Bridges duneos/hal_spi.h to driver/spi_master.h.
 * ESP-IDF types stay completely inside this translation unit.
 */
#include "duneos/hal_spi.h"
#include "duneos/klog.h"

#include "driver/spi_master.h"
#include "esp_err.h"

#include <stdlib.h>
#include <errno.h>

static const char *TAG = "duneos/hal_spi";

struct duneos_hal_spi {
    spi_host_device_t host;
    bool              owns_bus; /* false when attached to an existing bus */
};

struct duneos_hal_spi_dev {
    duneos_hal_spi_t    *bus;
    spi_device_handle_t  handle;
};

duneos_hal_spi_t *duneos_hal_spi_bus_init(const duneos_hal_spi_bus_config_t *cfg)
{
    spi_bus_config_t bus_cfg = {
        .mosi_io_num     = cfg->mosi_pin,
        .miso_io_num     = cfg->miso_pin,
        .sclk_io_num     = cfg->clk_pin,
        .quadwp_io_num   = -1,
        .quadhd_io_num   = -1,
        .max_transfer_sz = cfg->max_transfer_sz ? cfg->max_transfer_sz : 4096,
    };
    spi_host_device_t host = (spi_host_device_t)cfg->bus_id;

    esp_err_t err = spi_bus_initialize(host, &bus_cfg, SPI_DMA_CH_AUTO);
    if (err != ESP_OK) {
        klog_e(TAG, "spi_bus_initialize bus_id=%d: %s",
               cfg->bus_id, esp_err_to_name(err));
        return NULL;
    }

    duneos_hal_spi_t *h = malloc(sizeof(*h));
    if (!h) { spi_bus_free(host); return NULL; }
    h->host     = host;
    h->owns_bus = true;
    return h;
}

duneos_hal_spi_t *duneos_hal_spi_bus_attach(int bus_id)
{
    duneos_hal_spi_t *h = malloc(sizeof(*h));
    if (!h) return NULL;
    h->host     = (spi_host_device_t)bus_id;
    h->owns_bus = false;
    return h;
}

void duneos_hal_spi_bus_deinit(duneos_hal_spi_t *bus)
{
    if (!bus) return;
    if (bus->owns_bus)
        spi_bus_free(bus->host);
    free(bus);
}

duneos_hal_spi_dev_t *duneos_hal_spi_dev_add(duneos_hal_spi_t *bus,
                                              const duneos_hal_spi_dev_config_t *cfg)
{
    duneos_hal_spi_dev_t *dev = malloc(sizeof(*dev));
    if (!dev) return NULL;
    dev->bus    = bus;
    dev->handle = NULL;

    spi_device_interface_config_t if_cfg = {
        .mode           = cfg->mode,
        .clock_speed_hz = (int)cfg->freq_hz,
        .spics_io_num   = cfg->cs_pin,
        .queue_size     = 1,
    };
    esp_err_t err = spi_bus_add_device(bus->host, &if_cfg, &dev->handle);
    if (err != ESP_OK) {
        klog_e(TAG, "spi_bus_add_device: %s", esp_err_to_name(err));
        free(dev);
        return NULL;
    }
    return dev;
}

int duneos_hal_spi_dev_reconfig(duneos_hal_spi_dev_t *dev,
                                 const duneos_hal_spi_dev_config_t *cfg)
{
    if (dev->handle) {
        spi_bus_remove_device(dev->handle);
        dev->handle = NULL;
    }

    spi_device_interface_config_t if_cfg = {
        .mode           = cfg->mode,
        .clock_speed_hz = (int)cfg->freq_hz,
        .spics_io_num   = cfg->cs_pin,
        .queue_size     = 1,
    };
    esp_err_t err = spi_bus_add_device(dev->bus->host, &if_cfg, &dev->handle);
    if (err != ESP_OK) { errno = EIO; return -1; }
    return 0;
}

void duneos_hal_spi_dev_remove(duneos_hal_spi_dev_t *dev)
{
    if (!dev) return;
    if (dev->handle) spi_bus_remove_device(dev->handle);
    free(dev);
}

int duneos_hal_spi_transfer(duneos_hal_spi_dev_t *dev,
                             const void *tx_buf, void *rx_buf, size_t len)
{
    spi_transaction_t t = {
        .length    = len * 8,
        .tx_buffer = tx_buf,
        .rx_buffer = rx_buf,
    };
    if (spi_device_transmit(dev->handle, &t) != ESP_OK) { errno = EIO; return -1; }
    return 0;
}
