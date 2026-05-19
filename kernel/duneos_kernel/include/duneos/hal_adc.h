/*
 * duneos/hal_adc.h — DuneOS Hardware Interface: ADC (analog-to-digital converter)
 *
 * Pure-C public API.  No ESP-IDF types, no proprietary headers.
 * Implementation: arch/xtensa_esp32s3/hal/hal_adc.c (ESP-IDF adc_oneshot).
 */
#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct duneos_hal_adc_s duneos_hal_adc_t;

/*
 * Attenuation selects the input voltage range at the ADC pin.
 * Approximate full-scale voltages on ESP32-S3 (may vary by silicon revision):
 *   DUNEOS_ADC_ATTEN_0DB   : ~0 – 800 mV
 *   DUNEOS_ADC_ATTEN_2DB5  : ~0 – 1100 mV
 *   DUNEOS_ADC_ATTEN_6DB   : ~0 – 1350 mV
 *   DUNEOS_ADC_ATTEN_12DB  : ~0 – 3100 mV  (use for resistor-divider battery sense)
 */
typedef enum {
    DUNEOS_ADC_ATTEN_0DB  = 0,
    DUNEOS_ADC_ATTEN_2DB5 = 1,
    DUNEOS_ADC_ATTEN_6DB  = 2,
    DUNEOS_ADC_ATTEN_12DB = 3,
} duneos_adc_atten_t;

typedef struct {
    uint32_t           unit_id;   /* ADC hardware unit: 1 = ADC1, 2 = ADC2       */
    uint32_t           channel;   /* channel index (chip-specific, from BSP YAML) */
    duneos_adc_atten_t atten;     /* input attenuation                            */
    uint32_t           bitwidth;  /* 0 = arch default (12-bit), or explicit 9–13  */
} duneos_hal_adc_config_t;

/*
 * duneos_hal_adc_init — initialise an ADC unit + channel.
 * Returns an opaque handle on success, NULL on failure.
 * The caller owns the handle and must call duneos_hal_adc_deinit() when done.
 */
duneos_hal_adc_t *duneos_hal_adc_init(const duneos_hal_adc_config_t *cfg);

void duneos_hal_adc_deinit(duneos_hal_adc_t *adc);

/*
 * duneos_hal_adc_read — take a single sample.
 * Writes the raw integer value to *raw_out.
 * Returns 0 on success, -1 on hardware error.
 */
int duneos_hal_adc_read(duneos_hal_adc_t *adc, int *raw_out);

#ifdef __cplusplus
}
#endif
