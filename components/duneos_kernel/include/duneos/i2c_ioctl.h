#pragma once

#include <stdint.h>

/*
 * DuneOS I2C device interface — /dev/i2c-N
 *
 * Usage:
 *   int fd = open("/dev/i2c-0", O_RDWR);
 *   uint16_t addr = 0x68;
 *   ioctl(fd, I2C_SET_ADDR, &addr);
 *   write(fd, tx_buf, tx_len);          // transmit only
 *   read(fd, rx_buf, rx_len);           // receive only
 *   i2c_rdwr_t xfer = { tx, tx_len, rx, rx_len };
 *   ioctl(fd, I2C_RDWR, &xfer);         // write-then-read (no STOP between)
 *   close(fd);
 */

#define I2C_SET_ADDR   0x01   /* arg: uint16_t* — 7-bit slave address */
#define I2C_SET_SPEED  0x02   /* arg: uint32_t* — bus speed in Hz (future use) */
#define I2C_RDWR       0x03   /* arg: i2c_rdwr_t* — combined write-then-read */

typedef struct {
    const uint8_t *tx_buf;   /* data to transmit; NULL or tx_len==0 for read-only */
    uint16_t       tx_len;
    uint8_t       *rx_buf;   /* receive buffer; NULL or rx_len==0 for write-only */
    uint16_t       rx_len;
} i2c_rdwr_t;
