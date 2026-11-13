/*
 * libbq25896 — charger backend for the TI BQ25896 single-cell Li-ion charger.
 *
 * Implements the chip-agnostic charger_backend_ops_t vtable from
 * <duneos/charger.h>. Bus and address come from <duneos/board.h>, emitted by
 * boardgen from the board's `charger:` section.
 *
 * The chip does the hard part by itself: constant current until the cell nears
 * its target, then constant voltage while the current tapers off. Nothing here
 * ramps anything down — that would be re-implementing in software what the
 * silicon exists to do. What the host decides is only *how much* current, *to
 * what voltage*, and *when to call it finished*. Those three are properties of
 * the cell soldered to the board, so board.yaml states them and this applies
 * them.
 *
 * Register fields per the BQ25896 datasheet, SLUSC76C (July 2015, rev. May
 * 2018). Two of them bit me and are called out where they are read: CHG_CONFIG
 * is REG03 bit 4 and not bit 5 (bit 5 is OTG_CONFIG), and ITERM carries a
 * 64 mA offset on top of its 64 mA step.
 */

#include "duneos/charger.h"
#include "duneos/board.h"
#include "duneos/i2c_ioctl.h"

#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>
#include <fcntl.h>
#include <unistd.h>

#define REG_CHG_CFG     0x03   /* [5] OTG_CONFIG, [4] CHG_CONFIG            */
#define REG_ICHG        0x04   /* [6:0] fast-charge current, 64 mA/step      */
#define REG_PRECHG      0x05   /* [7:4] IPRECHG, [3:0] ITERM                */
#define REG_VREG        0x06   /* [7:2] VREG, [1] BATLOWV, [0] VRECHG       */
#define REG_TERM_CFG    0x07   /* [7] EN_TERM                                */
#define REG_STATUS      0x0B   /* [7:5] VBUS_STAT, [4:3] CHRG_STAT, [2] PG   */

#define CHG_CONFIG_BIT  (1u << 4)
#define OTG_CONFIG_BIT  (1u << 5)
#define EN_TERM_BIT     (1u << 7)
#define PG_STAT_BIT     (1u << 2)

/* VREG: 3.840 V + 16 mV per step, 6 bits. */
#define VREG_OFFSET_MV  3840u
#define VREG_STEP_MV    16u
#define VREG_MAX_FIELD  0x3Fu

/* ICHG: 64 mA per step from zero, 7 bits. */
#define ICHG_STEP_MA    64u
#define ICHG_MAX_FIELD  0x7Fu

/* ITERM: 64 mA offset, 64 mA per step, 4 bits → 64..1024 mA. */
#define ITERM_OFFSET_MA 64u
#define ITERM_STEP_MA   64u
#define ITERM_MAX_FIELD 0x0Fu

struct charger_handle {
    int bus_fd;
};

static int reg_read(int bus, uint8_t reg, uint8_t *out)
{
    struct i2c_msg msgs[2] = {
        { .addr = DUNEOS_CHARGER_ADDR, .flags = 0,        .len = 1, .buf = &reg },
        { .addr = DUNEOS_CHARGER_ADDR, .flags = I2C_M_RD, .len = 1, .buf = out  },
    };
    struct i2c_rdwr_ioctl_data xfer = { .msgs = msgs, .nmsgs = 2 };
    if (ioctl(bus, I2C_RDWR, &xfer) < 0) return -errno;
    return 0;
}

static int reg_write(int bus, uint8_t reg, uint8_t val)
{
    uint8_t buf[2] = { reg, val };
    struct i2c_msg m = { .addr = DUNEOS_CHARGER_ADDR, .flags = 0,
                         .len = 2, .buf = buf };
    struct i2c_rdwr_ioctl_data xfer = { .msgs = &m, .nmsgs = 1 };
    if (ioctl(bus, I2C_RDWR, &xfer) < 0) return -errno;
    return 0;
}

/* Replace `mask` bits of `reg` with `val`, leaving the rest alone. Returns 1
 * if the register actually changed, 0 if it already held that value. */
static int reg_update(int bus, uint8_t reg, uint8_t mask, uint8_t val)
{
    uint8_t cur = 0;
    int rc = reg_read(bus, reg, &cur);
    if (rc < 0) return rc;
    uint8_t want = (uint8_t)((cur & ~mask) | (val & mask));
    if (want == cur) return 0;
    if ((rc = reg_write(bus, reg, want)) < 0) return rc;
    return 1;
}

static charger_handle_t *bq25896_op_open(void)
{
    int bus = open(DUNEOS_CHARGER_I2C_DEV, O_RDWR);
    if (bus < 0) return NULL;

    /* Refuse a handle if the chip is not answering: a charger_info_t full of
     * zeroes reads as "not charging, no input power", which is a plausible
     * lie. Better to fail open() and let the caller say the charger is
     * missing. REG0B is read-only status, so this probes without writing. */
    uint8_t probe = 0;
    if (reg_read(bus, REG_STATUS, &probe) < 0) { close(bus); return NULL; }

    charger_handle_t *h = malloc(sizeof(*h));
    if (!h) { close(bus); return NULL; }
    h->bus_fd = bus;
    return h;
}

static int bq25896_op_read(charger_handle_t *h, charger_info_t *out)
{
    uint8_t chg_cfg = 0, ichg = 0, prechg = 0, vreg = 0, status = 0;
    int rc;

    if ((rc = reg_read(h->bus_fd, REG_CHG_CFG, &chg_cfg)) < 0) return rc;
    if ((rc = reg_read(h->bus_fd, REG_ICHG,    &ichg))    < 0) return rc;
    if ((rc = reg_read(h->bus_fd, REG_PRECHG,  &prechg))  < 0) return rc;
    if ((rc = reg_read(h->bus_fd, REG_VREG,    &vreg))    < 0) return rc;
    if ((rc = reg_read(h->bus_fd, REG_STATUS,  &status))  < 0) return rc;

    memset(out, 0, sizeof(*out));

    /* CHRG_STAT, REG0B[4:3]: 00 not charging, 01 pre-charge, 10 fast charging
     * (constant current or constant voltage), 11 termination done. */
    switch ((status >> 3) & 0x3) {
    case 0:  out->state = CHARGER_NOT_CHARGING; break;
    case 1:  out->state = CHARGER_PRECHARGE;    break;
    case 2:  out->state = CHARGER_CHARGING;     break;
    default: out->state = CHARGER_DONE;         break;
    }

    out->input_power_good = (status & PG_STAT_BIT) != 0;
    out->charge_enabled   = (chg_cfg & CHG_CONFIG_BIT) != 0;
    out->otg_enabled      = (chg_cfg & OTG_CONFIG_BIT) != 0;

    out->target_mv = (uint16_t)(VREG_OFFSET_MV +
                                ((vreg >> 2) & VREG_MAX_FIELD) * VREG_STEP_MV);
    /* VRECHG, REG06[0]: how far below the target a cell must fall before a
     * new cycle starts. 100 mV or 200 mV, nothing in between. */
    out->recharge_mv = (uint16_t)(out->target_mv - ((vreg & 1u) ? 200u : 100u));

    out->charge_current_ma =
        (uint16_t)((ichg & ICHG_MAX_FIELD) * ICHG_STEP_MA);
    out->term_current_ma =
        (uint16_t)(ITERM_OFFSET_MA + (prechg & ITERM_MAX_FIELD) * ITERM_STEP_MA);

    return 0;
}

static void bq25896_op_close(charger_handle_t *h)
{
    if (!h) return;
    close(h->bus_fd);
    free(h);
}

const charger_backend_ops_t duneos_charger_ops = {
    .open  = bq25896_op_open,
    .read  = bq25896_op_read,
    .close = bq25896_op_close,
};

/* ----- the declared profile -------------------------------------------- */

#if defined(DUNEOS_CHARGER_VOLTAGE_MV) || \
    defined(DUNEOS_CHARGER_CURRENT_MA) || \
    defined(DUNEOS_CHARGER_TERM_CURRENT_MA)

/* Round to the nearest representable step, then clamp. Rounding down a target
 * voltage is the safe direction for a cell; rounding a current is immaterial.
 * boardgen already range-checks these, so the clamps are belt and braces, not
 * the validation. */
static uint8_t field_from(unsigned value, unsigned offset, unsigned step,
                          unsigned max_field)
{
    if (value <= offset) return 0;
    unsigned f = (value - offset) / step;
    return (uint8_t)(f > max_field ? max_field : f);
}

int duneos_charger_configure(charger_handle_t *h)
{
    if (!h) return -EINVAL;
    int changed = 0, rc;

#ifdef DUNEOS_CHARGER_VOLTAGE_MV
    uint8_t vf = field_from(DUNEOS_CHARGER_VOLTAGE_MV, VREG_OFFSET_MV,
                            VREG_STEP_MV, VREG_MAX_FIELD);
    if ((rc = reg_update(h->bus_fd, REG_VREG, 0xFCu,
                         (uint8_t)(vf << 2))) < 0) return rc;
    changed |= rc;
#endif

#ifdef DUNEOS_CHARGER_CURRENT_MA
    uint8_t cf = field_from(DUNEOS_CHARGER_CURRENT_MA, 0,
                            ICHG_STEP_MA, ICHG_MAX_FIELD);
    if ((rc = reg_update(h->bus_fd, REG_ICHG, ICHG_MAX_FIELD, cf)) < 0) return rc;
    changed |= rc;
#endif

#ifdef DUNEOS_CHARGER_TERM_CURRENT_MA
    /* The one that decides how full "full" is, and — because the gauge
     * declares a taper threshold of its own — whether the gauge ever notices
     * the charge finishing. See the board.yaml comment and the bspgen check
     * that keeps the two numbers consistent. */
    uint8_t tf = field_from(DUNEOS_CHARGER_TERM_CURRENT_MA, ITERM_OFFSET_MA,
                            ITERM_STEP_MA, ITERM_MAX_FIELD);
    if ((rc = reg_update(h->bus_fd, REG_PRECHG, ITERM_MAX_FIELD, tf)) < 0) return rc;
    changed |= rc;

    /* Termination must be on for ITERM to mean anything. */
    if ((rc = reg_update(h->bus_fd, REG_TERM_CFG, EN_TERM_BIT,
                         EN_TERM_BIT)) < 0) return rc;
    changed |= rc;
#endif

    return changed ? 1 : 0;
}

#endif /* any profile declared */
