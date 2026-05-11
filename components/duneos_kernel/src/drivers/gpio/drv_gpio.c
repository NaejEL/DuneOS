#include "duneos/dev_driver.h"
#include "duneos/gpio_ioctl.h"
#include "duneos/klog.h"

#include "driver/gpio.h"

#include <string.h>
#include <errno.h>

static int gpio_ioctl_cb(duneos_devfd_t *fd, int cmd, void *arg)
{
    (void)fd;
    switch (cmd) {
    case GPIOCHIP_GET_INFO: {
        gpiochip_info_t *info = arg;
        strncpy(info->name, "gpiochip0", sizeof(info->name) - 1);
        info->name[sizeof(info->name) - 1] = '\0';
        info->lines = (uint8_t)GPIO_NUM_MAX;
        return 0;
    }
    case GPIOCHIP_SET_DIR: {
        gpio_req_t *r = arg;
        gpio_config_t cfg = {
            .pin_bit_mask = (1ULL << r->line),
            .mode         = (r->dir == GPIO_DIR_OUTPUT)
                                ? GPIO_MODE_OUTPUT : GPIO_MODE_INPUT,
            .pull_up_en   = GPIO_PULLUP_DISABLE,
            .pull_down_en = GPIO_PULLDOWN_DISABLE,
            .intr_type    = GPIO_INTR_DISABLE,
        };
        if (gpio_config(&cfg) != ESP_OK) { errno = EINVAL; return -1; }
        return 0;
    }
    case GPIOCHIP_GET_VALUE: {
        gpio_req_t *r = arg;
        r->val = (uint8_t)gpio_get_level((gpio_num_t)r->line);
        return 0;
    }
    case GPIOCHIP_SET_VALUE: {
        gpio_req_t *r = arg;
        if (gpio_set_level((gpio_num_t)r->line, r->val) != ESP_OK) {
            errno = EINVAL; return -1;
        }
        return 0;
    }
    case GPIOCHIP_SET_PULL: {
        gpio_req_t *r = arg;
        gpio_pull_mode_t mode;
        switch (r->pull) {
        case GPIO_PULL_NONE:   mode = GPIO_FLOATING;         break;
        case GPIO_PULL_UP:     mode = GPIO_PULLUP_ONLY;      break;
        case GPIO_PULL_DOWN:   mode = GPIO_PULLDOWN_ONLY;    break;
        case GPIO_PULL_UPDOWN: mode = GPIO_PULLUP_PULLDOWN;  break;
        default: errno = EINVAL; return -1;
        }
        if (gpio_set_pull_mode((gpio_num_t)r->line, mode) != ESP_OK) {
            errno = EINVAL; return -1;
        }
        return 0;
    }
    case GPIOCHIP_SET_IRQ:
        errno = ENOSYS;
        return -1;
    default:
        errno = ENOTTY;
        return -1;
    }
}

static const duneos_dev_driver_t s_drv_gpio = {
    .name  = "gpiochip0",
    .ioctl = gpio_ioctl_cb,
};

void drv_gpio_register(void)
{
    duneos_dev_register(&s_drv_gpio);
}
