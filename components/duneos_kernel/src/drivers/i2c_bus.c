#include "i2c_bus.h"
#include "duneos/hal_i2c.h"
#include "board_config.h"

static duneos_hal_i2c_t *s_bus = NULL;

int i2c_bus_init(void)
{
    duneos_hal_i2c_config_t cfg = {
        .port    = 0,
        .sda_pin = DUNEOS_I2C0_SDA_PIN,
        .scl_pin = DUNEOS_I2C0_SCL_PIN,
        .freq_hz = DUNEOS_I2C0_FREQ_HZ,
    };
    s_bus = duneos_hal_i2c_init(&cfg);
    return s_bus ? 0 : -1;
}

int i2c_bus_write_read(int bus_id,
                       uint16_t        addr,
                       const uint8_t  *tx_buf, uint16_t tx_len,
                             uint8_t  *rx_buf, uint16_t rx_len)
{
    (void)bus_id;
    return duneos_hal_i2c_write_read(s_bus, addr, tx_buf, tx_len, rx_buf, rx_len);
}
