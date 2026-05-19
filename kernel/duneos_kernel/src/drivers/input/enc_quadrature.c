/*
 * Quadrature rotary encoder backend for /dev/input/event0.
 *
 * Polls A and B GPIO pins and decodes the Gray-code sequence into signed
 * rotation steps.  Emits INPUT_EV_REL / REL_WHEEL events: +1 = CW, -1 = CCW.
 *
 * Gray-code state machine (standard quadrature):
 *   CW  sequence: AB = 00 → 10 → 11 → 01 → 00
 *   CCW sequence: AB = 00 → 01 → 11 → 10 → 00
 *
 * Board defines required (board_config.h):
 *   DUNEOS_HAVE_ENCODER       — must be defined
 *   DUNEOS_ENCODER_A_PIN      — GPIO for channel A
 *   DUNEOS_ENCODER_B_PIN      — GPIO for channel B
 */

#include "drv_input_priv.h"
#include "duneos/input_ioctl.h"
#include "duneos/klog.h"
#include "duneos/hal_gpio.h"
#include "duneos/hal_time.h"

#include "board_config.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "duneos/enc_quadrature";

/*
 * Transition table: index = (prev_ab << 2) | cur_ab
 * Value: +1 CW step, -1 CCW step, 0 no valid transition.
 * Derived from the standard quadrature Gray-code sequence.
 */
static const int8_t s_trans[16] = {
/*       cur: 00  01  10  11  */
/* prev 00 */  0, -1, +1,  0,
/* prev 01 */ +1,  0,  0, -1,
/* prev 10 */ -1,  0,  0, +1,
/* prev 11 */  0, +1, -1,  0,
};

static void enc_task(void *arg)
{
    (void)arg;
    uint8_t prev_ab = (uint8_t)((duneos_hal_gpio_get_level(DUNEOS_ENCODER_A_PIN) << 1)
                               | duneos_hal_gpio_get_level(DUNEOS_ENCODER_B_PIN));
    int accum = 0;

    while (1) {
        uint8_t cur_ab = (uint8_t)((duneos_hal_gpio_get_level(DUNEOS_ENCODER_A_PIN) << 1)
                                  | duneos_hal_gpio_get_level(DUNEOS_ENCODER_B_PIN));

        if (cur_ab != prev_ab) {
            accum += s_trans[(prev_ab << 2) | cur_ab];
            prev_ab = cur_ab;

            /* Emit one step per full detent (4 transitions) */
            if (accum >= 4 || accum <= -4) {
                input_event_t ev = {
                    .time_ms = (uint32_t)(duneos_hal_monotonic_us() / 1000),
                    .type    = INPUT_EV_REL,
                    .code    = REL_WHEEL,
                    .value   = (accum > 0) ? 1 : -1,
                };
                drv_input_push_event(&ev);
                accum = 0;
            }
        }
        vTaskDelay(pdMS_TO_TICKS(2));   /* 500 Hz — fast enough for ~30 rpm */
    }
}

void enc_quadrature_init(void)
{
    const int pins[2] = { DUNEOS_ENCODER_A_PIN, DUNEOS_ENCODER_B_PIN };
    for (int i = 0; i < 2; i++) {
        duneos_hal_gpio_set_dir(pins[i], DUNEOS_GPIO_DIR_INPUT);
        duneos_hal_gpio_set_pull(pins[i], DUNEOS_GPIO_PULL_UP);
    }
    xTaskCreate(enc_task, "enc_quad", 1024, NULL, 6, NULL);
    klog_i(TAG, "encoder A=%d B=%d ready", DUNEOS_ENCODER_A_PIN, DUNEOS_ENCODER_B_PIN);
}
