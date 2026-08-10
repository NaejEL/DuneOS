#pragma once

/*
 * libsmbus — SMBus operations over the Linux-style /dev/i2c-N interface.
 *
 * SMBus is a protocol layer on top of I2C (read/write byte/word, block, …).
 * This SDK library composes the standard SMBus transactions from i2c-dev
 * I2C_RDWR messages in userspace — no kernel SMBus support needed (ADR 015:
 * device protocols live as userspace libraries).
 *
 * Naming mirrors Linux <i2c/smbus.h> (`i2c_smbus_read_byte_data`, …) with one
 * deliberate deviation, documented per ADR 019: the slave address is an
 * explicit parameter. Linux's i2c_smbus_* take the address implicitly from a
 * prior ioctl(fd, I2C_SLAVE, addr); because we build the transaction in
 * userspace over I2C_RDWR, the address must travel with each call. Everything
 * else — semantics, byte order (SMBus words are little-endian), return values —
 * matches.
 *
 * Returns: a non-negative value on success (the read byte/word, or the number of
 * bytes read for block ops; 0 for writes/quick), or a negative -errno on error.
 *
 * Not yet implemented: SMBus block-data with a leading length byte
 * (read/write_block_data), process call, and PEC.
 */

#include <stdint.h>

/* Quick command: address + R/W bit only, no data (i2cdetect-style probe). */
int smbus_write_quick(int fd, uint8_t addr, uint8_t bit);

/* Single byte, no command register. */
int smbus_read_byte(int fd, uint8_t addr);
int smbus_write_byte(int fd, uint8_t addr, uint8_t value);

/* Byte from / to a command register. read returns 0..255. */
int smbus_read_byte_data(int fd, uint8_t addr, uint8_t command);
int smbus_write_byte_data(int fd, uint8_t addr, uint8_t command, uint8_t value);

/* 16-bit word from / to a command register (little-endian on the wire). */
int smbus_read_word_data(int fd, uint8_t addr, uint8_t command);
int smbus_write_word_data(int fd, uint8_t addr, uint8_t command, uint16_t value);

/* Fixed-length block from / to a command register (the I2C-block variant: no
 * SMBus length byte). Returns the number of bytes read for the read. */
int smbus_read_i2c_block(int fd, uint8_t addr, uint8_t command, uint8_t *buf, uint8_t len);
int smbus_write_i2c_block(int fd, uint8_t addr, uint8_t command, const uint8_t *buf, uint8_t len);
