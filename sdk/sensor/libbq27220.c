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

/* BQ27220 standard commands, 16-bit little-endian reads.
 * Authority: TI SLUUBD4A (rev. Nov 2022), table 2-1.
 *
 * These three were each off by one row for a long time — 0x04, 0x06 and 0x1C,
 * which are AtRateTimeToEmpty, Temperature and StandbyTimeToEmpty. The gauge
 * answered every read, so nothing failed: voltage and charge came back as
 * minutes, and Temperature at room temperature is about 2982 = 0x0BA6, whose
 * bit 9 is set — the bit read below as FC. The driver therefore reported a
 * full battery, always. Wrong rows of the right table produce a plausible
 * answer, which is why this survived. */
#define BQ27220_REG_VOLTAGE  0x08   /* Voltage(), mV                 */
#define BQ27220_REG_FLAGS    0x0A   /* BatteryStatus(), aka Flags()  */
#define BQ27220_REG_SOC      0x2C   /* RelativeStateOfCharge(), %    */
#define BQ27220_REG_DESIGN_CAPACITY 0x3C  /* DesignCapacity(), mAh, read-only */

extern int usleep(unsigned int useconds);

/* BatteryStatus bits, SLUUBD4A table 2-6: DSG is low byte bit 0, FC is high
 * byte bit 1. These two were right all along. */
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

/* ----- design capacity provisioning ------------------------------------- *
 *
 * The gauge computes RelativeStateOfCharge against Design Capacity. Left at
 * TI's reset value it is confidently wrong: this board's 1300 mAh cell read
 * 54% at 4111 mV because the gauge believed 3000 mAh.
 *
 * Design Capacity is data memory 0x929F (SLUUBD4A table in 3.4), reachable
 * only through the CONFIG UPDATE sequence of section 6.1, which is what the
 * code below is. Three things about it are worth knowing before touching it:
 *
 *   1. It writes RAM, not flash. The gauge reloads ROM defaults whenever it
 *      loses power (SLUUBD4A 8.1), so this is not provisioning — it is a
 *      startup check that must run again after the battery is disconnected.
 *      Hence `ensure`, not `set`: it reads first and writes only on mismatch.
 *   2. Gauging is SUSPENDED while in CONFIG UPDATE mode, and the gauge leaves
 *      it by itself after ~240 s. Every path below exits the mode, including
 *      the failure paths.
 *   3. Nothing bounds what you write. "None of the data written to memory is
 *      bounded by the fuel gauge" (SLUUBD4A 3.1) — a bad value is accepted and
 *      misinterpreted by the firmware. The caller's value comes from
 *      board.yaml via boardgen, which range-checks it.
 */
#ifdef DUNEOS_BATTERY_DESIGN_CAPACITY_MAH

#define BQ_REG_CONTROL       0x00
#define BQ_REG_OPSTATUS      0x3A
#define BQ_REG_MAC           0x3E   /* ManufacturerAccessControl */
#define BQ_REG_MACDATA       0x40
#define BQ_REG_MACDATASUM    0x60
#define BQ_REG_MACDATALEN    0x61

#define BQ_DM_FULL_CHARGE_CAPACITY 0x929D   /* I2, mAh — adjacent to the next */
#define BQ_DM_DESIGN_CAPACITY      0x929F   /* I2, mAh, ROM default 3000      */

/* The Control() keys need room to breathe; back-to-back writes are accepted on
 * the bus and change nothing. Paid only when a reprogram is actually due. */
#define BQ_KEY_GAP_US (1500 * 1000)

#define BQ_CMD_FULL_ACCESS   0xFFFF
#define BQ_CMD_ENTER_CFG     0x0090
#define BQ_CMD_EXIT_CFG_INIT 0x0091   /* exit and re-initialise with new data */

#define BQ_OPSTATUS_CFGUPDATE (1u << 10)  /* high byte bit 2 */
#define BQ_OPSTATUS_SEC_SHIFT 1
#define BQ_OPSTATUS_SEC_MASK  0x3
#define BQ_SEC_SEALED         0x3
#define BQ_SEC_FULL_ACCESS    0x1

static int write_bytes(int bus, uint8_t reg, const uint8_t *data, size_t n)
{
    uint8_t buf[8];
    if (n + 1 > sizeof(buf)) return -EINVAL;
    buf[0] = reg;
    for (size_t i = 0; i < n; i++) buf[i + 1] = data[i];
    struct i2c_msg m = { .addr = DUNEOS_BATTERY_GAUGE_ADDR, .flags = 0,
                         .len = (uint16_t)(n + 1), .buf = buf };
    struct i2c_rdwr_ioctl_data xfer = { .msgs = &m, .nmsgs = 1 };
    if (ioctl(bus, I2C_RDWR, &xfer) < 0) return -errno;
    return 0;
}

static int write_word(int bus, uint8_t reg, uint16_t v)
{
    uint8_t d[2] = { (uint8_t)(v & 0xFF), (uint8_t)(v >> 8) };
    return write_bytes(bus, reg, d, 2);
}

static int read_byte(int bus, uint8_t reg, uint8_t *out)
{
    struct i2c_msg msgs[2] = {
        { .addr = DUNEOS_BATTERY_GAUGE_ADDR, .flags = 0,        .len = 1, .buf = &reg },
        { .addr = DUNEOS_BATTERY_GAUGE_ADDR, .flags = I2C_M_RD, .len = 1, .buf = out  },
    };
    struct i2c_rdwr_ioctl_data xfer = { .msgs = msgs, .nmsgs = 2 };
    if (ioctl(bus, I2C_RDWR, &xfer) < 0) return -errno;
    return 0;
}

static int read_bytes(int bus, uint8_t reg, uint8_t *out, size_t n)
{
    struct i2c_msg msgs[2] = {
        { .addr = DUNEOS_BATTERY_GAUGE_ADDR, .flags = 0,        .len = 1, .buf = &reg },
        { .addr = DUNEOS_BATTERY_GAUGE_ADDR, .flags = I2C_M_RD,
          .len = (uint16_t)n, .buf = out },
    };
    struct i2c_rdwr_ioctl_data xfer = { .msgs = msgs, .nmsgs = 2 };
    if (ioctl(bus, I2C_RDWR, &xfer) < 0) return -errno;
    return 0;
}

/* Leave CONFIG UPDATE however we got here. Returns the error it was given, so
 * callers can `return exit_cfg(bus, rc);` and not lose the original cause. */
static int exit_cfg(int bus, int rc)
{
    write_word(bus, BQ_REG_CONTROL, BQ_CMD_EXIT_CFG_INIT);
    return rc;
}

/* Point the block window at `dm`, and leave it pointed there.
 *
 * The re-select is the whole trick. Reading MACDataSum() loses the window on
 * this part, so a straight read of MACData() afterwards returns whatever block
 * was last loaded — on this board a CEDV voltage table. The checksum then gets
 * computed over the wrong "old" bytes and the commit is silently rejected: no
 * error anywhere, the value simply does not change. Three attempts were spent
 * on that before a 32-byte dump of the window showed the voltage table instead
 * of the capacity. SLUUBD4A does not mention it. */
static int select_block(int bus, uint16_t dm, uint8_t *sum, uint8_t *len)
{
    uint8_t a[2] = { (uint8_t)(dm & 0xFF), (uint8_t)(dm >> 8) };
    int rc;
    if ((rc = write_bytes(bus, BQ_REG_MAC, a, 2)) < 0) return rc;
    usleep(50 * 1000);
    if ((rc = read_byte(bus, BQ_REG_MACDATASUM, sum)) < 0) return rc;
    if ((rc = read_byte(bus, BQ_REG_MACDATALEN, len)) < 0) return rc;
    if ((rc = write_bytes(bus, BQ_REG_MAC, a, 2)) < 0) return rc;
    usleep(50 * 1000);
    return 0;
}

/*
 * Make the gauge's Design Capacity match `want_mah`. Returns:
 *    0  already correct, nothing written
 *    1  reprogrammed
 *  <0   -errno, gauge left out of CONFIG UPDATE mode
 */
static int bq27220_ensure_design_capacity(int bus, uint16_t want_mah)
{
    uint16_t have = 0;
    int rc = read_reg16(bus, BQ27220_REG_DESIGN_CAPACITY, &have);
    if (rc < 0) return rc;
    if (have == want_mah) return 0;       /* the common path: one read, no write */

    uint16_t op = 0;
    if ((rc = read_reg16(bus, BQ_REG_OPSTATUS, &op)) < 0) return rc;
    if (((op >> BQ_OPSTATUS_SEC_SHIFT) & BQ_OPSTATUS_SEC_MASK) == BQ_SEC_SEALED)
        return -EACCES;   /* a sealed gauge needs keys we are not given */

    /* A gauge left in CONFIG UPDATE by an interrupted attempt ignores the
     * Control() keys below. Leave first. */
    if (op & BQ_OPSTATUS_CFGUPDATE) {
        write_word(bus, BQ_REG_CONTROL, BQ_CMD_EXIT_CFG_INIT);
        usleep(1000 * 1000);
    }

    /* FULL ACCESS, per SLUUBD4A 6.1 step 2: 0xFFFF twice, and the keys need
     * room to breathe — sent back-to-back the bus accepts them and nothing
     * happens. Honest caveat: on this part SEC was never observed to leave
     * UNSEALED, and the block write below succeeded anyway, so this step is
     * kept because the manual asks for it and a sealed-from-factory gauge
     * elsewhere may need it — not because its effect was ever measured. */
    if ((rc = write_word(bus, BQ_REG_CONTROL, BQ_CMD_FULL_ACCESS)) < 0) return rc;
    usleep(BQ_KEY_GAP_US);
    if ((rc = write_word(bus, BQ_REG_CONTROL, BQ_CMD_FULL_ACCESS)) < 0) return rc;
    usleep(BQ_KEY_GAP_US);

    if ((rc = write_word(bus, BQ_REG_CONTROL, BQ_CMD_ENTER_CFG)) < 0) return rc;

    /* "at least an 1100-ms delay after sending the SET_CFGUPDATE" and entry
     * "may take up to 1 second" (SLUUBD4A 4.6). Poll rather than guess. */
    int entered = 0;
    for (int i = 0; i < 30; i++) {
        usleep(100 * 1000);
        if (read_reg16(bus, BQ_REG_OPSTATUS, &op) == 0 &&
            (op & BQ_OPSTATUS_CFGUPDATE)) { entered = 1; break; }
    }
    if (!entered) return exit_cfg(bus, -EIO);

    /* One block covers both fields: Full Charge Capacity at 0x929D and Design
     * Capacity at 0x929F are adjacent. Learned Full Charge Capacity is meant
     * to start equal to Design Capacity (SLUUBD4A 4.9), and leaving it at 3000
     * beside a 1300 design is how RemainingCapacity ends up reporting more
     * charge than the cell can hold. Both get the same value. */
    uint8_t sum = 0, len = 0, old[4] = { 0, 0, 0, 0 };
    if ((rc = select_block(bus, BQ_DM_FULL_CHARGE_CAPACITY, &sum, &len)) < 0)
        return exit_cfg(bus, rc);
    if ((rc = read_bytes(bus, BQ_REG_MACDATA, old, 4)) < 0) return exit_cfg(bus, rc);

    /* Block data is big-endian, unlike every standard command on this part. */
    uint8_t neu[4] = { (uint8_t)(want_mah >> 8), (uint8_t)(want_mah & 0xFF),
                       (uint8_t)(want_mah >> 8), (uint8_t)(want_mah & 0xFF) };
    if ((rc = write_bytes(bus, BQ_REG_MACDATA, neu, 4)) < 0) return exit_cfg(bus, rc);
    usleep(50 * 1000);

    /* Checksum is the complement of the block's byte sum; the replacement form
     * avoids reading all 32 bytes (SLUUBD4A 6.1 step 11). */
    unsigned t = (255u - sum - old[0] - old[1] - old[2] - old[3]) & 0xFF;
    uint8_t new_sum =
        (uint8_t)(255u - ((t + neu[0] + neu[1] + neu[2] + neu[3]) & 0xFF));

    /* Sum and length in ONE transaction: "not executed until MACDataSum() and
     * MACDataLen() are written together as a word" (SLUUBD4A 2.31), which the
     * worked example in 6.1 contradicts by showing two separate byte writes.
     * The chip sides with 2.31. This write is the commit. */
    uint8_t commit[2] = { new_sum, len };
    if ((rc = write_bytes(bus, BQ_REG_MACDATASUM, commit, 2)) < 0)
        return exit_cfg(bus, rc);
    usleep(200 * 1000);

    exit_cfg(bus, 0);
    usleep(1000 * 1000);   /* let the re-init settle before anyone reads */

    /* Verify against the chip, not against our own arithmetic. */
    if ((rc = read_reg16(bus, BQ27220_REG_DESIGN_CAPACITY, &have)) < 0) return rc;
    if (have != want_mah) return -EIO;
    return 1;
}

int duneos_battery_provision(battery_handle_t *h)
{
    if (!h) return -EINVAL;
    return bq27220_ensure_design_capacity(h->bus_fd,
                                          DUNEOS_BATTERY_DESIGN_CAPACITY_MAH);
}

#else  /* board declares no design capacity — nothing to provision */

int duneos_battery_provision(battery_handle_t *h) { (void)h; return 0; }

#endif

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
