/*
 * arch/xtensa_esp32s3/hal/hal_adc.c — ESP-IDF implementation of duneos/hal_adc.h
 *
 * Uses esp_adc/adc_oneshot.h internally.  No ESP-IDF types cross the public boundary.
 *
 * unit_id convention: 1 = ADC1, 2 = ADC2 (1-based, matches chip naming).
 * ESP-IDF adc_unit_t is 0-based internally (ADC_UNIT_1=0, ADC_UNIT_2=1).
 */
#include "duneos/hal_adc.h"
#include "duneos/klog.h"

#include "esp_adc/adc_oneshot.h"

#include <stdlib.h>

static const char *TAG = "duneos/hal_adc";

struct duneos_hal_adc_s {
    adc_oneshot_unit_handle_t handle;
    adc_channel_t             channel;
};

duneos_hal_adc_t *duneos_hal_adc_init(const duneos_hal_adc_config_t *cfg)
{
    if (!cfg || cfg->unit_id < 1) return NULL;

    duneos_hal_adc_t *adc = calloc(1, sizeof(*adc));
    if (!adc) return NULL;

    adc->channel = (adc_channel_t)cfg->channel;

    /* board.yaml / board_config.h use 1-based ADC unit numbers; ESP-IDF is 0-based */
    adc_oneshot_unit_init_cfg_t unit_cfg = {
        .unit_id = (adc_unit_t)(cfg->unit_id - 1),
    };
    if (adc_oneshot_new_unit(&unit_cfg, &adc->handle) != ESP_OK) {
        klog_e(TAG, "adc_oneshot_new_unit(unit=%u) failed", (unsigned)cfg->unit_id);
        free(adc);
        return NULL;
    }

    static const adc_atten_t atten_map[] = {
        [DUNEOS_ADC_ATTEN_0DB]  = ADC_ATTEN_DB_0,
        [DUNEOS_ADC_ATTEN_2DB5] = ADC_ATTEN_DB_2_5,
        [DUNEOS_ADC_ATTEN_6DB]  = ADC_ATTEN_DB_6,
        [DUNEOS_ADC_ATTEN_12DB] = ADC_ATTEN_DB_12,
    };
    adc_oneshot_chan_cfg_t ch_cfg = {
        .atten    = atten_map[cfg->atten & 3],
        .bitwidth = cfg->bitwidth ? (adc_bitwidth_t)cfg->bitwidth : ADC_BITWIDTH_DEFAULT,
    };
    if (adc_oneshot_config_channel(adc->handle, adc->channel, &ch_cfg) != ESP_OK) {
        klog_e(TAG, "adc_oneshot_config_channel(ch=%u) failed", (unsigned)cfg->channel);
        adc_oneshot_del_unit(adc->handle);
        free(adc);
        return NULL;
    }

    return adc;
}

void duneos_hal_adc_deinit(duneos_hal_adc_t *adc)
{
    if (!adc) return;
    adc_oneshot_del_unit(adc->handle);
    free(adc);
}

int duneos_hal_adc_read(duneos_hal_adc_t *adc, int *raw_out)
{
    if (!adc || !raw_out) return -1;
    return adc_oneshot_read(adc->handle, adc->channel, raw_out) == ESP_OK ? 0 : -1;
}
