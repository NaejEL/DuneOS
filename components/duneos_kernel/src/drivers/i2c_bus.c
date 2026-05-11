#include "i2c_bus.h"
#include "duneos/klog.h"
#include "board_config.h"

#include "driver/i2c_master.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

#include <errno.h>

static const char *TAG = "duneos/i2c_bus";

static i2c_master_bus_handle_t s_bus  = NULL;
static SemaphoreHandle_t       s_lock = NULL;

esp_err_t i2c_bus_init(void)
{
    i2c_master_bus_config_t cfg = {
        .i2c_port            = I2C_NUM_0,
        .sda_io_num          = DUNEOS_I2C0_SDA_PIN,
        .scl_io_num          = DUNEOS_I2C0_SCL_PIN,
        .clk_source          = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt   = 7,
        .flags.enable_internal_pullup = true,
    };

    esp_err_t err = i2c_new_master_bus(&cfg, &s_bus);
    if (err != ESP_OK) {
        klog_e(TAG, "i2c_new_master_bus: %s", esp_err_to_name(err));
        return err;
    }

    s_lock = xSemaphoreCreateMutex();
    if (!s_lock) {
        i2c_del_master_bus(s_bus);
        s_bus = NULL;
        return ESP_ERR_NO_MEM;
    }

    klog_i(TAG, "I2C0 ready SDA=%d SCL=%d freq=%dHz",
           DUNEOS_I2C0_SDA_PIN, DUNEOS_I2C0_SCL_PIN, DUNEOS_I2C0_FREQ_HZ);
    return ESP_OK;
}

esp_err_t i2c_bus_write_read(int bus_id,
                              uint16_t       addr,
                              const uint8_t *tx_buf, uint16_t tx_len,
                                    uint8_t *rx_buf, uint16_t rx_len)
{
    (void)bus_id;
    if (!s_bus) return ESP_ERR_INVALID_STATE;

    i2c_device_config_t dev_cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address  = addr,
        .scl_speed_hz    = DUNEOS_I2C0_FREQ_HZ,
    };

    i2c_master_dev_handle_t dev = NULL;
    esp_err_t err;

    xSemaphoreTake(s_lock, portMAX_DELAY);

    err = i2c_master_bus_add_device(s_bus, &dev_cfg, &dev);
    if (err == ESP_OK) {
        if (tx_len > 0 && rx_len > 0) {
            err = i2c_master_transmit_receive(dev, tx_buf, tx_len,
                                              rx_buf, rx_len, 100);
        } else if (tx_len > 0) {
            err = i2c_master_transmit(dev, tx_buf, tx_len, 100);
        } else if (rx_len > 0) {
            err = i2c_master_receive(dev, rx_buf, rx_len, 100);
        }
        i2c_master_bus_rm_device(dev);
    }

    xSemaphoreGive(s_lock);
    return err;
}
