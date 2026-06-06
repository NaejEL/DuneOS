/*
 * I2C HAL backend — ESP-IDF implementation.
 * Bridges duneos/hal_i2c.h to driver/i2c_master.h.
 * ESP-IDF types and FreeRTOS mutex stay completely inside this translation unit.
 */
#include "duneos/hal_i2c.h"
#include "duneos/klog.h"

#include "driver/i2c_master.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "esp_err.h"

#include <stdlib.h>
#include <errno.h>

static const char *TAG = "duneos/hal_i2c";

#define HAL_I2C_MAX_DEVICES 16

typedef struct {
    uint16_t                addr;
    i2c_master_dev_handle_t dev;
} i2c_dev_entry_t;

struct duneos_hal_i2c {
    i2c_master_bus_handle_t bus;
    SemaphoreHandle_t       lock;
    uint32_t                freq_hz;
    i2c_dev_entry_t         devs[HAL_I2C_MAX_DEVICES];
    int                     dev_count;
};

duneos_hal_i2c_t *duneos_hal_i2c_init(const duneos_hal_i2c_config_t *cfg)
{
    i2c_master_bus_config_t bus_cfg = {
        .i2c_port            = (i2c_port_num_t)cfg->port,
        .sda_io_num          = (gpio_num_t)cfg->sda_pin,
        .scl_io_num          = (gpio_num_t)cfg->scl_pin,
        .clk_source          = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt   = 7,
        .flags.enable_internal_pullup = true,
    };

    i2c_master_bus_handle_t bus = NULL;
    esp_err_t err = i2c_new_master_bus(&bus_cfg, &bus);
    if (err != ESP_OK) {
        klog_e(TAG, "i2c_new_master_bus port=%d: %s",
               cfg->port, esp_err_to_name(err));
        return NULL;
    }

    SemaphoreHandle_t lock = xSemaphoreCreateMutex();
    if (!lock) {
        i2c_del_master_bus(bus);
        return NULL;
    }

    duneos_hal_i2c_t *h = malloc(sizeof(*h));
    if (!h) {
        vSemaphoreDelete(lock);
        i2c_del_master_bus(bus);
        return NULL;
    }
    h->bus       = bus;
    h->lock      = lock;
    h->freq_hz   = cfg->freq_hz;
    h->dev_count = 0;

    klog_i(TAG, "I2C%d ready SDA=%d SCL=%d freq=%luHz",
           cfg->port, cfg->sda_pin, cfg->scl_pin, (unsigned long)cfg->freq_hz);
    return h;
}

void duneos_hal_i2c_deinit(duneos_hal_i2c_t *h)
{
    if (!h) return;
    for (int i = 0; i < h->dev_count; i++)
        i2c_master_bus_rm_device(h->devs[i].dev);
    i2c_del_master_bus(h->bus);
    vSemaphoreDelete(h->lock);
    free(h);
}

int duneos_hal_i2c_write_read(duneos_hal_i2c_t *h,
                               uint16_t        addr,
                               const uint8_t  *tx_buf, uint16_t tx_len,
                                     uint8_t  *rx_buf, uint16_t rx_len)
{
    if (!h) { errno = EINVAL; return -1; }

    xSemaphoreTake(h->lock, portMAX_DELAY);

    /* Find or create a persistent device handle for this address.
     * add_device/rm_device per-transaction causes heap churn and races. */
    i2c_master_dev_handle_t dev = NULL;
    for (int i = 0; i < h->dev_count; i++) {
        if (h->devs[i].addr == addr) {
            dev = h->devs[i].dev;
            break;
        }
    }
    if (!dev) {
        if (h->dev_count >= HAL_I2C_MAX_DEVICES) {
            xSemaphoreGive(h->lock);
            errno = ENOMEM;
            return -1;
        }
        i2c_device_config_t dev_cfg = {
            .dev_addr_length = I2C_ADDR_BIT_LEN_7,
            .device_address  = addr,
            .scl_speed_hz    = h->freq_hz,
        };
        esp_err_t cerr = i2c_master_bus_add_device(h->bus, &dev_cfg, &dev);
        if (cerr != ESP_OK) {
            xSemaphoreGive(h->lock);
            klog_e(TAG, "add_device addr=0x%02x: %s", addr, esp_err_to_name(cerr));
            errno = EIO;
            return -1;
        }
        h->devs[h->dev_count].addr = (uint16_t)addr;
        h->devs[h->dev_count].dev  = dev;
        h->dev_count++;
    }

    esp_err_t err = ESP_OK;
    if (tx_len > 0 && rx_len > 0) {
        err = i2c_master_transmit_receive(dev, tx_buf, tx_len,
                                          rx_buf, rx_len, -1);
    } else if (tx_len > 0) {
        err = i2c_master_transmit(dev, tx_buf, tx_len, -1);
    } else if (rx_len > 0) {
        err = i2c_master_receive(dev, rx_buf, rx_len, -1);
    }

    xSemaphoreGive(h->lock);

    if (err != ESP_OK) { errno = EIO; return -1; }
    return 0;
}

int duneos_hal_i2c_probe(duneos_hal_i2c_t *h, uint16_t addr)
{
    if (!h) { errno = EINVAL; return -1; }
    xSemaphoreTake(h->lock, portMAX_DELAY);
    esp_err_t err = i2c_master_probe(h->bus, addr, 50 /* ms */);
    xSemaphoreGive(h->lock);
    if (err != ESP_OK) { errno = ENXIO; return -1; }
    return 0;
}
