/*
 * btn_gpio — userspace GPIO button scanner daemon.
 *
 * Replaces the in-kernel btn_gpio.c (24-debt #5). Polls a list of GPIOs
 * declared in board.yaml `buttons:` and injects INPUT_EV_KEY press/release
 * events into /dev/input/event0 via ioctl(INPUT_INJECT_EVENT).
 *
 * All buttons are assumed active-LOW with internal pull-up enabled. 10 ms
 * poll period provides natural contact debounce.
 *
 * Board-agnostic: pin numbers and key codes come from <duneos/board.h>,
 * emitted by boardgen.py from the active board's YAML.
 */

#include <duneos/board.h>

extern void duneos_exit(int code);

#ifndef DUNEOS_BTN_GPIO_COUNT
/* Board has no `buttons:` section — daemon is a no-op stub here. */
void app_main(void) { duneos_exit(0); }
#else

#include <fcntl.h>
#include <unistd.h>
#include <string.h>
#include <stdbool.h>
#include <stdint.h>

#include <duneos/gpio_ioctl.h>
#include <duneos/input_ioctl.h>

extern int usleep(unsigned int useconds);

#define SCAN_PERIOD_MS  10

static const int     s_pins[DUNEOS_BTN_GPIO_COUNT]  = DUNEOS_BTN_GPIO_PINS;
static const uint8_t s_codes[DUNEOS_BTN_GPIO_COUNT] = DUNEOS_BTN_GPIO_CODES;
static bool          s_prev[DUNEOS_BTN_GPIO_COUNT];

void app_main(void)
{
    int gpio_fd  = open("/dev/gpiochip0", O_RDWR);
    if (gpio_fd  < 0) duneos_exit(2);
    int input_fd = open(DUNEOS_INPUT_DEV, O_RDWR);
    if (input_fd < 0) { close(gpio_fd); duneos_exit(3); }

    for (int i = 0; i < DUNEOS_BTN_GPIO_COUNT; i++) {
        gpio_req_t r = { .line = (uint8_t)s_pins[i], .dir  = GPIO_DIR_INPUT };
        ioctl(gpio_fd, GPIOCHIP_SET_DIR, &r);
        r.pull = GPIO_PULL_UP;
        ioctl(gpio_fd, GPIOCHIP_SET_PULL, &r);
    }
    memset(s_prev, 0, sizeof(s_prev));

    for (;;) {
        for (int i = 0; i < DUNEOS_BTN_GPIO_COUNT; i++) {
            gpio_req_t r = { .line = (uint8_t)s_pins[i] };
            if (ioctl(gpio_fd, GPIOCHIP_GET_VALUE, &r) < 0) continue;
            bool pressed = (r.val == 0);
            if (pressed == s_prev[i]) continue;
            s_prev[i] = pressed;
            input_event_t ev = {
                .time_ms = 0,
                .type    = INPUT_EV_KEY,
                .code    = s_codes[i],
                .value   = pressed ? INPUT_VAL_PRESS : INPUT_VAL_RELEASE,
            };
            ioctl(input_fd, INPUT_INJECT_EVENT, &ev);
        }
        usleep(SCAN_PERIOD_MS * 1000);
    }
}
#endif  /* DUNEOS_BTN_GPIO_COUNT */
