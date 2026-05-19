#include "duneos/dev_driver.h"
#include "duneos/battery_ioctl.h"
#include "duneos/klog.h"
#include "board_config.h"
#include "i2c_bus.h"

#include <errno.h>
#include <string.h>

/* BQ27220 standard register map (16-bit little-endian reads). */
#define BQ27220_REG_VOLTAGE  0x04   /* mV — raw cell voltage            */
#define BQ27220_REG_FLAGS    0x06   /* status flags                      */
#define BQ27220_REG_SOC      0x1C   /* state of charge, 0–100 %          */

/* FLAGS bit definitions (Table 9 of BQ27220 TRM). */
#define BQ27220_FLAG_DSG    (1u << 0)   /* discharging                   */
#define BQ27220_FLAG_FC     (1u << 9)   /* full-charge detected           */

static uint16_t bq27220_read_reg(uint8_t reg)
{
    uint8_t buf[2] = {0, 0};
    i2c_bus_write_read(0, DUNEOS_BATTERY_GAUGE_ADDR, &reg, 1, buf, 2);
    return (uint16_t)(buf[0] | ((uint16_t)buf[1] << 8));
}

static int battery_ioctl(duneos_devfd_t *fd, int cmd, void *arg)
{
    (void)fd;
    if (cmd != BATTERY_GET_INFO) { errno = ENOTTY; return -1; }

    battery_info_t *info = arg;
    info->voltage_mv = bq27220_read_reg(BQ27220_REG_VOLTAGE);
    info->percent    = (uint8_t)bq27220_read_reg(BQ27220_REG_SOC);
    uint16_t flags   = bq27220_read_reg(BQ27220_REG_FLAGS);

    if (flags & BQ27220_FLAG_FC)
        info->status = BATTERY_FULL;
    else if (flags & BQ27220_FLAG_DSG)
        info->status = BATTERY_DISCHARGING;
    else
        info->status = BATTERY_CHARGING;

    return 0;
}

static const duneos_dev_driver_t s_drv_battery_bq27220 = {
    .name  = "battery0",
    .ioctl = battery_ioctl,
};

void drv_battery_bq27220_register(void)
{
    /*
     * i2c_bus_init() was already called by drv_i2c_register() which must
     * be enabled whenever CONFIG_DUNEOS_DRV_BATTERY_BQ27220 is set (Kconfig
     * dependency: depends on DUNEOS_DRV_I2C).
     */
    duneos_dev_register(&s_drv_battery_bq27220);
}
