/*
 * GPIO button backend for /dev/input/event0.
 *
 * Polls a list of GPIO pins declared in board_config.h and emits INPUT_EV_KEY
 * press/release events.  10 ms poll period provides natural contact debounce.
 *
 * Board defines required (board_config.h):
 *   DUNEOS_HAVE_BTN_GPIO          — must be defined
 *   DUNEOS_BTN_GPIO_COUNT         — number of buttons
 *   DUNEOS_BTN_GPIO_PINS          — brace-initialiser list of GPIO numbers
 *   DUNEOS_BTN_GPIO_CODES         — brace-initialiser list of KEY_* codes
 *   All buttons are assumed active-LOW with internal pull-up enabled.
 */

#include "drv_input_priv.h"
#include "duneos/input_ioctl.h"
#include "duneos/klog.h"

#include "board_config.h"

#include "driver/gpio.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include <string.h>

static const char *TAG = "duneos/btn_gpio";

static const int     s_pins[DUNEOS_BTN_GPIO_COUNT]  = DUNEOS_BTN_GPIO_PINS;
static const uint8_t s_codes[DUNEOS_BTN_GPIO_COUNT] = DUNEOS_BTN_GPIO_CODES;
static bool          s_prev[DUNEOS_BTN_GPIO_COUNT];

static void btn_scan_task(void *arg)
{
    (void)arg;
    while (1) {
        uint32_t now = xTaskGetTickCount() * portTICK_PERIOD_MS;
        for (int i = 0; i < DUNEOS_BTN_GPIO_COUNT; i++) {
            bool pressed = (gpio_get_level(s_pins[i]) == 0);
            if (pressed == s_prev[i]) continue;
            s_prev[i] = pressed;
            input_event_t ev = {
                .time_ms = now,
                .type    = INPUT_EV_KEY,
                .code    = s_codes[i],
                .value   = pressed ? INPUT_VAL_PRESS : INPUT_VAL_RELEASE,
            };
            drv_input_push_event(&ev);
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

void btn_gpio_init(void)
{
    for (int i = 0; i < DUNEOS_BTN_GPIO_COUNT; i++) {
        gpio_config_t cfg = {
            .pin_bit_mask = (1ULL << s_pins[i]),
            .mode         = GPIO_MODE_INPUT,
            .pull_up_en   = GPIO_PULLUP_ENABLE,
            .pull_down_en = GPIO_PULLDOWN_DISABLE,
            .intr_type    = GPIO_INTR_DISABLE,
        };
        gpio_config(&cfg);
    }
    memset(s_prev, 0, sizeof(s_prev));
    xTaskCreate(btn_scan_task, "btn_scan", 1024, NULL, 5, NULL);
    klog_i(TAG, "%d button(s) registered", DUNEOS_BTN_GPIO_COUNT);
}
