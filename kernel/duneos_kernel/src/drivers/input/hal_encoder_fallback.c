/*
 * Quadrature encoder HAL — GPIO polling fallback (weak symbols).
 *
 * Used on architectures without hardware PCNT (RISC-V, ARM, simulator).
 * On Xtensa ESP32-S3, arch/xtensa_esp32s3/hal/hal_encoder.c provides the
 * strong-symbol PCNT implementation, causing the linker to discard these.
 *
 * Polls A+B GPIO at 500 Hz; decodes via Gray-code transition table.
 * One detent = 4 transitions accumulated before emitting ±1 to the callback.
 * Callback is invoked from task context (no ISR restrictions).
 */

#include "duneos/hal_encoder.h"
#include "duneos/hal_gpio.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include <stdlib.h>
#include <errno.h>

struct duneos_hal_encoder_s {
    int                     pin_a;
    int                     pin_b;
    duneos_hal_encoder_cb_t cb;
    void                   *ctx;
    TaskHandle_t            task;
};

/*
 * Transition table: index = (prev_ab << 2) | cur_ab
 * +1 = CW step, -1 = CCW step, 0 = no valid transition.
 * Derived from Gray-code: CW = 00→10→11→01→00.
 */
static const int8_t s_trans[16] = {
/*         cur:  00   01   10   11  */
/* prev 00 */     0,  -1,  +1,   0,
/* prev 01 */    +1,   0,   0,  -1,
/* prev 10 */    -1,   0,   0,  +1,
/* prev 11 */     0,  +1,  -1,   0,
};

static void enc_poll_task(void *arg)
{
    duneos_hal_encoder_t *enc = arg;
    uint8_t prev_ab = (uint8_t)((duneos_hal_gpio_get_level(enc->pin_a) << 1)
                                | duneos_hal_gpio_get_level(enc->pin_b));
    int accum = 0;

    while (1) {
        uint8_t cur_ab = (uint8_t)((duneos_hal_gpio_get_level(enc->pin_a) << 1)
                                   | duneos_hal_gpio_get_level(enc->pin_b));
        if (cur_ab != prev_ab) {
            accum += s_trans[(prev_ab << 2) | cur_ab];
            prev_ab = cur_ab;
            if (accum >= 4 || accum <= -4) {
                enc->cb((accum > 0) ? 1 : -1, enc->ctx);
                accum = 0;
            }
        }
        vTaskDelay(pdMS_TO_TICKS(2)); /* 500 Hz — sufficient for ~30 rpm */
    }
}

__attribute__((weak))
duneos_hal_encoder_t *duneos_hal_encoder_open(const duneos_hal_encoder_config_t *cfg)
{
    if (!cfg || !cfg->cb) { errno = EINVAL; return NULL; }

    duneos_hal_encoder_t *enc = malloc(sizeof(*enc));
    if (!enc) { errno = ENOMEM; return NULL; }
    enc->pin_a = cfg->pin_a;
    enc->pin_b = cfg->pin_b;
    enc->cb    = cfg->cb;
    enc->ctx   = cfg->ctx;

    duneos_hal_gpio_set_dir(cfg->pin_a, DUNEOS_GPIO_DIR_INPUT);
    duneos_hal_gpio_set_pull(cfg->pin_a, DUNEOS_GPIO_PULL_UP);
    duneos_hal_gpio_set_dir(cfg->pin_b, DUNEOS_GPIO_DIR_INPUT);
    duneos_hal_gpio_set_pull(cfg->pin_b, DUNEOS_GPIO_PULL_UP);

    if (xTaskCreate(enc_poll_task, "enc_fallback", 1024, enc, 6, &enc->task) != pdPASS) {
        free(enc);
        errno = ENOMEM;
        return NULL;
    }
    return enc;
}

__attribute__((weak))
void duneos_hal_encoder_close(duneos_hal_encoder_t *enc)
{
    if (!enc) return;
    vTaskDelete(enc->task);
    free(enc);
}
