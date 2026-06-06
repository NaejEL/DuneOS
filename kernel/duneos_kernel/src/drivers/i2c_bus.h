#pragma once

/*
 * Shared I2C core for kernel drivers (Linux-style).
 *
 * i2c_bus_init()   — bring up the single I2C0 controller. Call once from a
 *                    driver register hook (drv_i2c, gpio expanders, fuel
 *                    gauges) before the first transfer.
 * i2c_transfer()   — execute an array of struct i2c_msg back-to-back with
 *                    repeated STARTs, the way Linux i2c_transfer() does.
 *                    Mutex-protected. A zero-length write message is an
 *                    address-only ACK probe.
 *
 * All in-kernel I2C consumers (drv_i2c.c, the gpio expanders, fuel gauges)
 * share this one controller + serialisation mutex.
 */

#include "duneos/i2c_ioctl.h"   /* struct i2c_msg, I2C_M_RD */

int i2c_bus_init(void);

/*
 * Tear down and re-initialise the controller — restores the SCL/SDA pin mux
 * after another user (e.g. the i2cscope sniffer) has repurposed those GPIOs for
 * edge capture, so the bus can be used again without a reboot.
 */
int i2c_bus_reinit(void);

/*
 * Run num messages as one transaction. Returns the number of messages
 * transferred (== num on success), or a negative value on bus error.
 */
int i2c_transfer(const struct i2c_msg *msgs, int num);
