#include "i2c_bus.h"
#include "duneos/hal_i2c.h"
#include "board_config.h"

#include <errno.h>

static duneos_hal_i2c_t *s_bus = NULL;

int i2c_bus_init(void)
{
    duneos_hal_i2c_config_t cfg = {
        .port    = 0,
        .sda_pin = DUNEOS_I2C0_SDA_PIN,
        .scl_pin = DUNEOS_I2C0_SCL_PIN,
        .freq_hz = DUNEOS_I2C0_FREQ_HZ,
    };
    s_bus = duneos_hal_i2c_init(&cfg);
    return s_bus ? 0 : -1;
}

int i2c_bus_reinit(void)
{
    if (s_bus) {
        duneos_hal_i2c_deinit(s_bus);
        s_bus = NULL;
    }
    return i2c_bus_init();
}

int i2c_transfer(const struct i2c_msg *msgs, int num)
{
    if (!msgs || num <= 0) { errno = EINVAL; return -1; }
    if (!s_bus)            { errno = ENODEV; return -1; }

    /* Common combined case: a write of the register pointer immediately
     * followed by a read from the same address. Map it to one repeated-START
     * transaction (what the HAL's write_read does), instead of two with a STOP
     * in between — many devices require the repeated START. */
    if (num == 2
        && !(msgs[0].flags & I2C_M_RD) && (msgs[1].flags & I2C_M_RD)
        && msgs[0].addr == msgs[1].addr && msgs[0].len > 0) {
        if (duneos_hal_i2c_write_read(s_bus, msgs[0].addr,
                                      msgs[0].buf, msgs[0].len,
                                      msgs[1].buf, msgs[1].len) != 0)
            return -1;
        return 2;
    }

    int done = 0;
    for (int i = 0; i < num; i++) {
        const struct i2c_msg *m = &msgs[i];
        int rc;
        if (m->flags & I2C_M_RD)
            rc = duneos_hal_i2c_write_read(s_bus, m->addr, NULL, 0, m->buf, m->len);
        else if (m->len == 0)
            rc = duneos_hal_i2c_probe(s_bus, m->addr);     /* address-only ACK check */
        else
            rc = duneos_hal_i2c_write_read(s_bus, m->addr, m->buf, m->len, NULL, 0);
        if (rc != 0) break;
        done++;
    }
    return done;
}
