#include "duneos/dev_driver.h"
#include "duneos/battery_ioctl.h"
#include "duneos/klog.h"
#include "board_config.h"

#include "esp_adc/adc_oneshot.h"

#include <errno.h>
#include <string.h>

static const char *TAG = "duneos/drv_battery_adc";

/* ESP32-S3 ADC1 full-scale at ATTEN_DB_12 is ~3100 mV at pin. */
#define ADC_FULL_SCALE_MV  3100
#define ADC_MAX_RAW        4095

static adc_oneshot_unit_handle_t s_adc;

static esp_err_t battery_init(void)
{
    adc_oneshot_unit_init_cfg_t unit_cfg = {
        .unit_id = DUNEOS_BATTERY_ADC_UNIT,
    };
    esp_err_t err = adc_oneshot_new_unit(&unit_cfg, &s_adc);
    if (err != ESP_OK) {
        klog_e(TAG, "adc_oneshot_new_unit: %s", esp_err_to_name(err));
        return err;
    }

    adc_oneshot_chan_cfg_t ch_cfg = {
        .atten    = ADC_ATTEN_DB_12,
        .bitwidth = ADC_BITWIDTH_DEFAULT,
    };
    err = adc_oneshot_config_channel(s_adc, DUNEOS_BATTERY_ADC_CHANNEL, &ch_cfg);
    if (err != ESP_OK) {
        klog_e(TAG, "adc_oneshot_config_channel: %s", esp_err_to_name(err));
        return err;
    }

    klog_i(TAG, "adc_simple battery: unit=%d ch=%d vdiv=%d full=%dmV empty=%dmV",
           DUNEOS_BATTERY_ADC_UNIT, DUNEOS_BATTERY_ADC_CHANNEL,
           DUNEOS_BATTERY_VDIV_FACTOR,
           DUNEOS_BATTERY_FULL_MV, DUNEOS_BATTERY_EMPTY_MV);
    return ESP_OK;
}

static int battery_ioctl(duneos_devfd_t *fd, int cmd, void *arg)
{
    (void)fd;
    if (cmd != BATTERY_GET_INFO) { errno = ENOTTY; return -1; }

    battery_info_t *info = arg;
    memset(info, 0, sizeof(*info));

    int raw = 0;
    adc_oneshot_read(s_adc, DUNEOS_BATTERY_ADC_CHANNEL, &raw);

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
    info->status = gpio_get_level(DUNEOS_BATTERY_CHRG_GPIO)
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
