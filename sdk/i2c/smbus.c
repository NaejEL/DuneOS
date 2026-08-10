/*
 * libsmbus — see <duneos/smbus.h>.
 *
 * Each SMBus op is expressed as one or two i2c_msg structs submitted via the
 * i2c-dev I2C_RDWR ioctl: a write message carries [command, data...], an
 * optional second read message (repeated-START) carries the response. SMBus
 * word order is little-endian, matching the I2C byte order on ESP32 (LE host),
 * so word read/write just (de)serialise low byte first.
 */

#include "duneos/smbus.h"
#include "duneos/i2c_ioctl.h"

#include <errno.h>
#include <stddef.h>

static int xrdwr(int fd, struct i2c_msg *m, int n)
{
    struct i2c_rdwr_ioctl_data x = { .msgs = m, .nmsgs = n };
    if (ioctl(fd, I2C_RDWR, &x) < 0) return -errno;
    return 0;
}

int smbus_write_quick(int fd, uint8_t addr, uint8_t bit)
{
    struct i2c_msg m = { .addr = addr, .flags = bit ? I2C_M_RD : 0, .len = 0, .buf = NULL };
    return xrdwr(fd, &m, 1);
}

int smbus_read_byte(int fd, uint8_t addr)
{
    uint8_t v = 0;
    struct i2c_msg m = { .addr = addr, .flags = I2C_M_RD, .len = 1, .buf = &v };
    int r = xrdwr(fd, &m, 1);
    return r < 0 ? r : v;
}

int smbus_write_byte(int fd, uint8_t addr, uint8_t value)
{
    struct i2c_msg m = { .addr = addr, .flags = 0, .len = 1, .buf = &value };
    return xrdwr(fd, &m, 1);
}

int smbus_read_byte_data(int fd, uint8_t addr, uint8_t command)
{
    uint8_t v = 0;
    struct i2c_msg m[2] = {
        { .addr = addr, .flags = 0,        .len = 1, .buf = &command },
        { .addr = addr, .flags = I2C_M_RD, .len = 1, .buf = &v       },
    };
    int r = xrdwr(fd, m, 2);
    return r < 0 ? r : v;
}

int smbus_write_byte_data(int fd, uint8_t addr, uint8_t command, uint8_t value)
{
    uint8_t b[2] = { command, value };
    struct i2c_msg m = { .addr = addr, .flags = 0, .len = 2, .buf = b };
    return xrdwr(fd, &m, 1);
}

int smbus_read_word_data(int fd, uint8_t addr, uint8_t command)
{
    uint8_t v[2] = { 0, 0 };
    struct i2c_msg m[2] = {
        { .addr = addr, .flags = 0,        .len = 1, .buf = &command },
        { .addr = addr, .flags = I2C_M_RD, .len = 2, .buf = v        },
    };
    int r = xrdwr(fd, m, 2);
    return r < 0 ? r : (v[0] | (v[1] << 8));   /* SMBus: little-endian */
}

int smbus_write_word_data(int fd, uint8_t addr, uint8_t command, uint16_t value)
{
    uint8_t b[3] = { command, (uint8_t)(value & 0xFF), (uint8_t)(value >> 8) };
    struct i2c_msg m = { .addr = addr, .flags = 0, .len = 3, .buf = b };
    return xrdwr(fd, &m, 1);
}

int smbus_read_i2c_block(int fd, uint8_t addr, uint8_t command, uint8_t *buf, uint8_t len)
{
    struct i2c_msg m[2] = {
        { .addr = addr, .flags = 0,        .len = 1,   .buf = &command },
        { .addr = addr, .flags = I2C_M_RD, .len = len, .buf = buf      },
    };
    int r = xrdwr(fd, m, 2);
    return r < 0 ? r : len;
}

int smbus_write_i2c_block(int fd, uint8_t addr, uint8_t command, const uint8_t *buf, uint8_t len)
{
    uint8_t b[1 + 32];
    if (len > 32) { errno = EINVAL; return -EINVAL; }
    b[0] = command;
    for (uint8_t i = 0; i < len; i++) b[1 + i] = buf[i];
    struct i2c_msg m = { .addr = addr, .flags = 0, .len = (uint16_t)(len + 1), .buf = b };
    return xrdwr(fd, &m, 1);
}
