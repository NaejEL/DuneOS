#include "duneos/dev_driver.h"
#include "duneos/battery_ioctl.h"
#include "duneos/klog.h"
#include "duneos/hal_adc.h"
#include "duneos/hal_gpio.h"
#include "board_config.h"

#include <errno.h>
#include <string.h>

static const char *TAG = "duneos/drv_battery_adc";

/* ESP32-S3 ADC1 full-scale at ATTEN_DB_12 is ~3100 mV at pin. */
#define ADC_FULL_SCALE_MV  3100
#define ADC_MAX_RAW        4095

static duneos_hal_adc_t *s_adc;

static int battery_init(void)
{
    duneos_hal_adc_config_t cfg = {
        .unit_id  = DUNEOS_BATTERY_ADC_UNIT,
        .channel  = DUNEOS_BATTERY_ADC_CHANNEL,
        .atten    = DUNEOS_ADC_ATTEN_12DB,
        .bitwidth = 0,
    };
    s_adc = duneos_hal_adc_init(&cfg);
    if (!s_adc) {
        klog_e(TAG, "adc init failed");
        return -1;
    }
    klog_i(TAG, "adc_simple battery: unit=%u ch=%u vdiv=%d full=%dmV empty=%dmV",
           (unsigned)DUNEOS_BATTERY_ADC_UNIT, (unsigned)DUNEOS_BATTERY_ADC_CHANNEL,
           DUNEOS_BATTERY_VDIV_FACTOR,
           DUNEOS_BATTERY_FULL_MV, DUNEOS_BATTERY_EMPTY_MV);
    return 0;
}

static int battery_ioctl(duneos_devfd_t *fd, int cmd, void *arg)
{
    (void)fd;
    if (cmd != BATTERY_GET_INFO) { errno = ENOTTY; return -1; }

    battery_info_t *info = arg;
    memset(info, 0, sizeof(*info));

    int raw = 0;
    duneos_hal_adc_read(s_adc, &raw);

    /* raw → pin voltage → cell voltage */
    uint32_t pin_mv = (uint32_t)raw * ADC_FULL_SCALE_MV / ADC_MAX_RAW;
    info->voltage_mv = (uint16_t)(pin_mv * DUNEOS_BATTERY_VDIV_FACTOR);

    uint32_t range = DUNEOS_BATTERY_FULL_MV - DUNEOS_BATTERY_EMPTY_MV;
    if (info->voltage_mv <= DUNEOS_BATTERY_EMPTY_MV) {
        info->percent = 0;
    } else if (info->voltage_mv >= DUNEOS_BATTERY_FULL_MV) {
        info->percent = 100;
    } else {
        info->percent = (uint8_t)(
            (info->voltage_mv - DUNEOS_BATTERY_EMPTY_MV) * 100 / range);
    }

#if DUNEOS_BATTERY_CHRG_GPIO >= 0
    /* CHRG output of TP4057: low = charging, high/float = not charging */
    info->status = duneos_hal_gpio_get_level(DUNEOS_BATTERY_CHRG_GPIO)
                   ? BATTERY_DISCHARGING : BATTERY_CHARGING;
#else
    info->status = BATTERY_DISCHARGING;
#endif

    return 0;
}

static const duneos_dev_driver_t s_drv_battery_adc = {
    .name  = "battery0",
    .init  = battery_init,
    .ioctl = battery_ioctl,
};

void drv_battery_adc_simple_register(void)
{
    duneos_dev_register(&s_drv_battery_adc);
}
