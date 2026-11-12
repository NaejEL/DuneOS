/*
 * bqinfo — dump a BQ27220 fuel gauge's standard command block to stdout.
 *
 * Exists because the interesting questions about a gauge are not answerable
 * from the two numbers the battery indicator shows. Three in particular:
 *
 *   - Is DesignCapacity programmed? The CEDV algorithm derives state of charge
 *     from it, and a pack shipped with the factory default reports a confident
 *     number that has nothing to do with the cell. DesignCapacity() is
 *     read-only at 0x3C, so this is answerable without touching the chip's
 *     data memory (which needs CONFIG UPDATE mode, suspends gauging, and is
 *     worse than useless if written wrong).
 *   - Does FullChargeCapacity track DesignCapacity, or has the gauge learned
 *     something different?
 *   - Are the registers the driver reads the ones it thinks? Printing the
 *     neighbouring commands makes a one-row offset obvious: a voltage of 0 next
 *     to an AtRateTimeToEmpty of 65535 reads very differently from a voltage of
 *     3900.
 *
 * Addresses are TI SLUUBD4A table 2-1. Bus and address come from
 * <duneos/board.h>, so this runs on any board declaring a bq27220.
 *
 * Writes nothing to the gauge. Reads every command once and exits.
 */

#include <unistd.h>
#include <fcntl.h>
#include <string.h>
#include <stdio.h>
#include <stdarg.h>
#include <stdint.h>
#include <errno.h>

#include "duneos/board.h"
#include "duneos/i2c_ioctl.h"

extern void duneos_exit(int code);

static void out(const char *s) { write(STDOUT_FILENO, s, strlen(s)); }

static void outf(const char *fmt, ...)
{
    char buf[128];
    va_list ap; va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap); va_end(ap);
    out(buf);
}

/* One command, read as a 16-bit little-endian word. Returns 0 or -errno. */
static int read_cmd(int bus, uint8_t reg, uint16_t *out_val)
{
    uint8_t r = reg;
    struct i2c_msg msgs[2] = {
        { .addr = DUNEOS_BATTERY_GAUGE_ADDR, .flags = 0,
          .len = 1, .buf = &r },
        { .addr = DUNEOS_BATTERY_GAUGE_ADDR, .flags = I2C_M_RD,
          .len = 2, .buf = (uint8_t *)out_val },
    };
    struct i2c_rdwr_ioctl_data xfer = { .msgs = msgs, .nmsgs = 2 };
    if (ioctl(bus, I2C_RDWR, &xfer) < 0) return -errno;
    return 0;
}

typedef struct {
    uint8_t     reg;
    const char *name;
    const char *unit;
    int         is_signed;
} cmd_t;

/* SLUUBD4A table 2-1. Not the whole table — the commands that say something
 * about charge, health and whether this driver is reading the right rows. */
static const cmd_t k_cmds[] = {
    { 0x04, "AtRateTimeToEmpty",     "min",  0 },
    { 0x06, "Temperature",           "0.1K", 0 },
    { 0x08, "Voltage",               "mV",   0 },
    { 0x0A, "BatteryStatus",         "bits", 0 },
    { 0x0C, "Current",               "mA",   1 },
    { 0x10, "RemainingCapacity",     "mAh",  0 },
    { 0x12, "FullChargeCapacity",    "mAh",  0 },
    { 0x14, "AverageCurrent",        "mA",   1 },
    { 0x1C, "StandbyTimeToEmpty",    "min",  0 },
    { 0x2A, "CycleCount",            "num",  0 },
    { 0x2C, "RelativeStateOfCharge", "%",    0 },
    { 0x2E, "StateOfHealth",         "%",    0 },
    { 0x30, "ChargeVoltage",         "mV",   0 },
    { 0x32, "ChargeCurrent",         "mA",   0 },
    { 0x3C, "DesignCapacity",        "mAh",  0 },
};

static void decode_status(uint16_t f)
{
    out("  BatteryStatus:");
    if (f & (1u << 0))  out(" DSG");
    if (f & (1u << 1))  out(" SYSDWN");
    if (f & (1u << 2))  out(" TDA");
    if (f & (1u << 3))  out(" BATTPRES");
    if (f & (1u << 4))  out(" AUTH_GD");
    if (f & (1u << 5))  out(" OCVGD");
    if (f & (1u << 6))  out(" TCA");
    if (f & (1u << 8))  out(" CHGINH");
    if (f & (1u << 9))  out(" FC");
    if (f & (1u << 10)) out(" OTD");
    if (f & (1u << 11)) out(" OTC");
    if (f & (1u << 12)) out(" SLEEP");
    if (f & (1u << 13)) out(" OCVFAIL");
    if (f & (1u << 14)) out(" OCVCOMP");
    if (f & (1u << 15)) out(" FD");
    out("\r\n");
}

void app_main(void)
{
    out("\r\n--- bqinfo begin ---\r\n");
    outf("  bus=%s addr=0x%02X\r\n",
         DUNEOS_BATTERY_I2C_DEV, (unsigned)DUNEOS_BATTERY_GAUGE_ADDR);

    int bus = open(DUNEOS_BATTERY_I2C_DEV, O_RDWR);
    if (bus < 0) {
        outf("  FATAL cannot open %s (errno %d)\r\n",
             DUNEOS_BATTERY_I2C_DEV, errno);
        out("--- bqinfo end ---\r\n");
        duneos_exit(2);
    }

    uint16_t status = 0;
    int have_status = 0;

    for (unsigned i = 0; i < sizeof(k_cmds) / sizeof(k_cmds[0]); i++) {
        uint16_t v = 0;
        int rc = read_cmd(bus, k_cmds[i].reg, &v);
        if (rc < 0) {
            outf("  0x%02X %-22s ERR %d\r\n",
                 (unsigned)k_cmds[i].reg, k_cmds[i].name, rc);
            continue;
        }
        if (k_cmds[i].is_signed) {
            outf("  0x%02X %-22s %6d %s\r\n", (unsigned)k_cmds[i].reg,
                 k_cmds[i].name, (int)(int16_t)v, k_cmds[i].unit);
        } else {
            outf("  0x%02X %-22s %6u %s  (0x%04X)\r\n", (unsigned)k_cmds[i].reg,
                 k_cmds[i].name, (unsigned)v, k_cmds[i].unit, (unsigned)v);
        }
        if (k_cmds[i].reg == 0x0A) { status = v; have_status = 1; }
    }

    if (have_status) decode_status(status);

    close(bus);
    out("--- bqinfo end ---\r\n");
    duneos_exit(0);
}
