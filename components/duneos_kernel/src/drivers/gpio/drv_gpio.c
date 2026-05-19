#include "duneos/dev_driver.h"
#include "duneos/hal_gpio.h"
#include "duneos/gpio_ioctl.h"

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
        info->lines = (uint8_t)duneos_hal_gpio_count();
        return 0;
    }
    case GPIOCHIP_SET_DIR: {
        gpio_req_t *r = arg;
        return duneos_hal_gpio_set_dir(r->line, (duneos_gpio_dir_t)r->dir);
    }
    case GPIOCHIP_GET_VALUE: {
        gpio_req_t *r = arg;
        int v = duneos_hal_gpio_get_level(r->line);
        if (v < 0) return -1;
        r->val = (uint8_t)v;
        return 0;
    }
    case GPIOCHIP_SET_VALUE: {
        gpio_req_t *r = arg;
        return duneos_hal_gpio_set_level(r->line, r->val);
    }
    case GPIOCHIP_SET_PULL: {
        gpio_req_t *r = arg;
        return duneos_hal_gpio_set_pull(r->line, (duneos_gpio_pull_t)r->pull);
    }
    case GPIOCHIP_SET_IRQ:
        /* Userspace IRQ delivery not yet implemented. */
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
