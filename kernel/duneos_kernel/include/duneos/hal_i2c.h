#pragma once

/*
 * DuneOS Hardware Interface — I2C
 *
 * Pure-C API: no esp_err_t, no proprietary types.
 * The ESP-IDF implementation lives in arch/xtensa_esp32s3/hal/hal_i2c.c.
 *
 * The implementation owns the serialisation mutex; callers do not need to lock.
 *
 * Returns: 0 / pointer on success; -1 / NULL on failure (errno set).
 */

#include <stdint.h>

typedef struct duneos_hal_i2c duneos_hal_i2c_t;

typedef struct {
    int      port;    /* I2C bus number (0 = I2C0, 1 = I2C1)     */
    int      sda_pin;
    int      scl_pin;
    uint32_t freq_hz;
} duneos_hal_i2c_config_t;

duneos_hal_i2c_t *duneos_hal_i2c_init   (const duneos_hal_i2c_config_t *cfg);
void              duneos_hal_i2c_deinit  (duneos_hal_i2c_t *h);

/*
 * Write then read in a single transaction.
 * Pass tx_len=0 for a pure read, rx_len=0 for a pure write.
 * Returns 0 on success, -1 on error (errno = EIO).
 */
int duneos_hal_i2c_write_read(duneos_hal_i2c_t *h,
                               uint16_t        addr,
                               const uint8_t  *tx_buf, uint16_t tx_len,
                                     uint8_t  *rx_buf, uint16_t rx_len);

/*
 * Probe a 7-bit address: emit START + address and check for ACK, with no data
 * phase and without caching a device handle (so a full bus scan does not
 * exhaust the device table). Returns 0 if a device ACKs, -1 otherwise.
 */
int duneos_hal_i2c_probe(duneos_hal_i2c_t *h, uint16_t addr);
