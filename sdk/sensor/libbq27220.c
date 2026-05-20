#include "duneos/bq27220.h"
#include "duneos/i2c_ioctl.h"

#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
/* ioctl() prototype is provided by duneos/i2c_ioctl.h (included above). */

/* BQ27220 standard register map (16-bit little-endian reads). */
#define BQ27220_REG_VOLTAGE  0x04   /* mV — raw cell voltage            */
#define BQ27220_REG_FLAGS    0x06   /* status flags                      */
#define BQ27220_REG_SOC      0x1C   /* state of charge, 0–100 %          */

/* FLAGS bit definitions (Table 9 of BQ27220 TRM). */
#define BQ27220_FLAG_DSG    (1u << 0)   /* discharging                   */
#define BQ27220_FLAG_FC     (1u << 9)   /* full-charge detected           */

struct bq27220_s {
    int     bus_fd;
    uint8_t addr;
};

bq27220_handle_t *bq27220_open(int bus_fd, uint8_t addr)
{
    bq27220_handle_t *h = malloc(sizeof(*h));
    if (!h) return NULL;
    h->bus_fd = bus_fd;
    h->addr   = addr;

    /* Latch the address on the fd — all subsequent transfers use this addr. */
    uint16_t a = addr;
    ioctl(bus_fd, I2C_SET_ADDR, &a);

    return h;
}

void bq27220_close(bq27220_handle_t *h)
{
    free(h);
}

static int read_reg16(bq27220_handle_t *h, uint8_t reg, uint16_t *out)
{
    i2c_rdwr_t xfer = {
        .tx_buf = &reg,
        .tx_len = 1,
        .rx_buf = (uint8_t *)out,
        .rx_len = 2,
    };
    if (ioctl(h->bus_fd, I2C_RDWR, &xfer) < 0)
        return -errno;
    /* BQ27220 sends data little-endian — no byte-swap needed on LE hosts */
    return 0;
}

int bq27220_read(bq27220_handle_t *h, battery_info_t *out)
{
    uint16_t voltage, flags, soc;

    int r = read_reg16(h, BQ27220_REG_VOLTAGE, &voltage);
    if (r < 0) return r;
    r = read_reg16(h, BQ27220_REG_FLAGS, &flags);
    if (r < 0) return r;
    r = read_reg16(h, BQ27220_REG_SOC, &soc);
    if (r < 0) return r;

    out->voltage_mv = voltage;
    out->percent    = (uint8_t)(soc > 100 ? 100 : soc);

    if (flags & BQ27220_FLAG_FC)
        out->status = BATTERY_FULL;
    else if (flags & BQ27220_FLAG_DSG)
        out->status = BATTERY_DISCHARGING;
    else
        out->status = BATTERY_CHARGING;

    return 0;
}
