/*
 * libbq27220 — battery backend for TI BQ27220 I2C fuel gauge.
 *
 * Implements the chip-agnostic battery_backend_ops_t vtable defined in
 * <duneos/battery.h>. libbattery.c forwards every public call (battery_open
 * / _read / _close) through this table.  Apps never see the chip name —
 * they declare `capabilities: [battery]` and the capability resolver
 * pulls this file in when the active board's `battery.type` is `bq27220`.
 *
 * Bus + address come from <duneos/board.h>, emitted by boardgen.py from
 * the active board's YAML (DUNEOS_BATTERY_I2C_DEV + DUNEOS_BATTERY_GAUGE_ADDR).
 */

#include "duneos/battery.h"
#include "duneos/board.h"
#include "duneos/i2c_ioctl.h"

#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>
#include <fcntl.h>
#include <unistd.h>

/* BQ27220 standard register map (16-bit little-endian reads). */
#define BQ27220_REG_VOLTAGE  0x04
#define BQ27220_REG_FLAGS    0x06
#define BQ27220_REG_SOC      0x1C

/* FLAGS bit definitions (Table 9 of BQ27220 TRM). */
#define BQ27220_FLAG_DSG    (1u << 0)
#define BQ27220_FLAG_FC     (1u << 9)

struct battery_handle {
    int bus_fd;
};

static battery_handle_t *bq27220_op_open(void)
{
    int bus = open(DUNEOS_BATTERY_I2C_DEV, O_RDWR);
    if (bus < 0) return NULL;

    battery_handle_t *h = malloc(sizeof(*h));
    if (!h) { close(bus); return NULL; }
    h->bus_fd = bus;

    /* Latch the gauge address on the fd — used by read()/write(). */
    ioctl(bus, I2C_SLAVE, (void *)(uintptr_t)DUNEOS_BATTERY_GAUGE_ADDR);

    return h;
}

static int read_reg16(int bus, uint8_t reg, uint16_t *out)
{
    struct i2c_msg msgs[2] = {
        { .addr = DUNEOS_BATTERY_GAUGE_ADDR, .flags = 0,        .len = 1, .buf = &reg },
        { .addr = DUNEOS_BATTERY_GAUGE_ADDR, .flags = I2C_M_RD, .len = 2, .buf = (uint8_t *)out },
    };
    struct i2c_rdwr_ioctl_data xfer = { .msgs = msgs, .nmsgs = 2 };
    if (ioctl(bus, I2C_RDWR, &xfer) < 0) return -errno;
    /* BQ27220 sends data little-endian — no byte-swap needed on LE hosts. */
    return 0;
}

static int bq27220_op_read(battery_handle_t *h, battery_info_t *out)
{
    uint16_t voltage, flags, soc;
    int r;

    if ((r = read_reg16(h->bus_fd, BQ27220_REG_VOLTAGE, &voltage)) < 0) return r;
    if ((r = read_reg16(h->bus_fd, BQ27220_REG_FLAGS,   &flags))   < 0) return r;
    if ((r = read_reg16(h->bus_fd, BQ27220_REG_SOC,     &soc))     < 0) return r;

    out->voltage_mv = voltage;
    out->percent    = (uint8_t)(soc > 100 ? 100 : soc);

    if (flags & BQ27220_FLAG_FC)        out->status = BATTERY_FULL;
    else if (flags & BQ27220_FLAG_DSG)  out->status = BATTERY_DISCHARGING;
    else                                out->status = BATTERY_CHARGING;

    return 0;
}

static void bq27220_op_close(battery_handle_t *h)
{
    if (!h) return;
    close(h->bus_fd);
    free(h);
}

const battery_backend_ops_t duneos_battery_ops = {
    .open  = bq27220_op_open,
    .read  = bq27220_op_read,
    .close = bq27220_op_close,
};
