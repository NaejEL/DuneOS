#pragma once

/*
 * Shared I2C bus handle for kernel drivers.
 *
 * i2c_bus_init()        — call once from vfs_dev.c when CONFIG_DUNEOS_DRV_I2C
 *                         or CONFIG_DUNEOS_DRV_BATTERY_BQ27220 is enabled.
 * i2c_bus_write_read()  — mutex-protected write-then-read transaction.
 *                         Pass tx_len=0 for pure read, rx_len=0 for pure write.
 *
 * Both drv_i2c.c and drv_battery_bq27220.c link against this shared layer so
 * they share the same bus handle and serialisation mutex without duplicating
 * the HAL init code.
 */

#include <stdint.h>

int i2c_bus_init(void);

int i2c_bus_write_read(int bus_id,
                       uint16_t        addr,
                       const uint8_t  *tx_buf, uint16_t tx_len,
                             uint8_t  *rx_buf, uint16_t rx_len);
