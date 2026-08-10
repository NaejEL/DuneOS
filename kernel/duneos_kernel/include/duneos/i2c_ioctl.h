#pragma once

#include <stdint.h>

/*
 * DuneOS I2C device interface — /dev/i2c-N
 *
 * Mirrors the Linux i2c-dev userspace API (<linux/i2c.h>, <linux/i2c-dev.h>):
 * a transfer is an array of `struct i2c_msg`, executed back-to-back with
 * repeated STARTs (no STOP between messages of one I2C_RDWR call).
 *
 *   int fd = open("/dev/i2c-0", O_RDWR);
 *
 *   // Simple read()/write() use the address set with I2C_SLAVE:
 *   uint16_t addr = 0x68;
 *   ioctl(fd, I2C_SLAVE, (void *)(uintptr_t)addr);
 *   write(fd, tx, tx_len);
 *   read(fd, rx, rx_len);
 *
 *   // Combined register read (write reg pointer, repeated-START, read data):
 *   uint8_t reg = 0x00, val[2];
 *   struct i2c_msg msgs[2] = {
 *       { .addr = addr, .flags = 0,        .len = 1, .buf = &reg   },
 *       { .addr = addr, .flags = I2C_M_RD, .len = 2, .buf = val    },
 *   };
 *   struct i2c_rdwr_ioctl_data xfer = { .msgs = msgs, .nmsgs = 2 };
 *   ioctl(fd, I2C_RDWR, &xfer);
 *
 *   // Probe an address (i2cdetect-style): a zero-length write message.
 *   struct i2c_msg p = { .addr = a, .flags = 0, .len = 0, .buf = NULL };
 *   struct i2c_rdwr_ioctl_data xfer = { .msgs = &p, .nmsgs = 1 };
 *   int present = (ioctl(fd, I2C_RDWR, &xfer) == 0);
 *
 *   close(fd);
 */

/* ioctl() is exported by the kernel ABI; declare it here for -nostdlib apps. */
extern int ioctl(int fd, unsigned long request, ...);

/* One I2C message. Mirrors Linux struct i2c_msg. */
struct i2c_msg {
    uint16_t addr;    /* 7-bit slave address                                 */
    uint16_t flags;   /* 0 = write, I2C_M_RD = read                          */
    uint16_t len;     /* bytes in buf; len == 0 on a write is an ACK probe   */
    uint8_t *buf;     /* data buffer (NULL allowed when len == 0)            */
};

#define I2C_M_RD  0x0001u   /* message is a read (default is write) */

/* Payload for the I2C_RDWR ioctl. Mirrors Linux struct i2c_rdwr_ioctl_data. */
struct i2c_rdwr_ioctl_data {
    struct i2c_msg *msgs;    /* array of messages */
    uint32_t        nmsgs;   /* number of messages in the array */
};

/* /dev/i2c-N ioctl numbers (same values as Linux i2c-dev). */
#define I2C_SLAVE  0x0703   /* arg: 7-bit address as the value — addr for read()/write() */
#define I2C_RDWR   0x0707   /* arg: struct i2c_rdwr_ioctl_data* — returns 0 on full success */

/* DuneOS extension (no Linux equivalent): tear down + re-init the controller,
 * restoring the SCL/SDA pin mux after a sniffer repurposed those GPIOs for edge
 * capture — lets you switch use without a reboot. arg ignored. */
#define I2C_RESET  0x0710
