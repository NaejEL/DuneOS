/*
 * libbq27220 — userspace BQ27220 I2C fuel-gauge library.
 *
 * Opens /dev/i2c-0, reads the three standard BQ27220 registers via raw
 * ioctl, and fills a battery_info_t.  Does not depend on any kernel
 * battery driver being present.
 *
 * Usage:
 *   int bus = open("/dev/i2c-0", O_RDWR);
 *   bq27220_handle_t *h = bq27220_open(bus, 0x55);  // 0x55 = default addr
 *   battery_info_t info;
 *   bq27220_read(h, &info);
 *   bq27220_close(h);
 *   close(bus);
 */

#pragma once
#include "duneos/battery_ioctl.h"
#include <stdint.h>

typedef struct bq27220_s bq27220_handle_t;

/*
 * Open a BQ27220 on the given I2C bus fd.
 * addr is the 7-bit I2C address (0x55 is the factory default).
 * Returns NULL on allocation failure.  The caller owns bus_fd and must
 * keep it open for the lifetime of the handle.
 */
bq27220_handle_t *bq27220_open(int bus_fd, uint8_t addr);

/*
 * Read voltage, state-of-charge and charging status from the gauge.
 * Returns 0 on success, -errno on I2C error.
 */
int bq27220_read(bq27220_handle_t *h, battery_info_t *out);

/* Close the handle (does not close bus_fd). */
void bq27220_close(bq27220_handle_t *h);
