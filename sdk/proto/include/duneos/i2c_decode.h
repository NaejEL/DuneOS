#pragma once

/*
 * libproto — I2C protocol decoder (shared SDK).
 *
 * Decodes a synchronous SCL/SDA transition stream into START / address / data /
 * STOP tokens with ACK/NACK. Edge-based (SDA sampled on the SCL rising edge),
 * so clock stretching is handled implicitly. Used by both the live sniffer
 * (i2cscope) and offline VCD viewers (waves) — same algorithm, no duplication.
 */

#include <stdint.h>

enum { I2C_TOK_START, I2C_TOK_ADDR, I2C_TOK_DATA, I2C_TOK_STOP };

typedef struct {
    uint8_t  type;   /* I2C_TOK_*                       */
    uint8_t  val;    /* address (7-bit) or data byte    */
    uint8_t  rw;     /* address: 1 = read, 0 = write    */
    uint8_t  ack;    /* 1 = ACK, 0 = NACK               */
    uint32_t t_us;   /* time of the token (for overlays)*/
} i2c_token_t;

/* One synchronous sample: channel levels bitmask at time t_us. Matches the
 * layout of logic_sample_t (<duneos/logic_ioctl.h>), so a capture buffer can be
 * cast to this directly. */
typedef struct {
    uint32_t t_us;
    uint32_t levels;
} i2c_sample_t;

/* Decode `n` samples into `out` (capacity `max`). scl_bit / sda_bit are the bit
 * positions of SCL / SDA within each sample's `levels`. Returns the token
 * count (<= max). */
int i2c_decode(const i2c_sample_t *s, int n, int scl_bit, int sda_bit,
               i2c_token_t *out, int max);
