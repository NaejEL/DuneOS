/*
 * /dev/logic0 — synchronous logic-capture device (see ADR 020).
 *
 * Thin device wrapper over the logic HAL: LOGIC_SET_CONFIG records which GPIOs
 * to sample and the window timeouts, then each read() runs one capture window
 * and returns packed transition records. A single capture session is supported
 * at a time (one sniffer) — the config lives in a driver-global, not per-fd.
 */
#include "duneos/dev_driver.h"
#include "duneos/driver_init.h"
#include "duneos/logic_ioctl.h"
#include "duneos/hal_logic.h"
#include "duneos/hal_gpio.h"

#include <errno.h>
#include <string.h>

_Static_assert(sizeof(logic_sample_t) == sizeof(duneos_logic_sample_t),
               "logic_sample_t must match the HAL sample layout");

static logic_config_t s_cfg;
static bool           s_configured;

static int logic_set_config(const logic_config_t *cfg)
{
    if (cfg->n_channels < 1 || cfg->n_channels > LOGIC_MAX_CHANNELS) {
        errno = EINVAL;
        return -1;
    }
    for (int i = 0; i < cfg->n_channels; i++) {
        int g = cfg->channel_gpio[i];
        if (duneos_hal_gpio_set_dir(g, DUNEOS_GPIO_DIR_INPUT) != 0) return -1;
        duneos_hal_gpio_set_pull(g, DUNEOS_GPIO_PULL_UP);
    }
    s_cfg        = *cfg;
    s_configured = true;
    return 0;
}

static int logic_ioctl_cb(duneos_devfd_t *fd, int cmd, void *arg)
{
    (void)fd;
    switch (cmd) {
    case LOGIC_SET_CONFIG:
        return logic_set_config((const logic_config_t *)arg);
    case LOGIC_GET_INFO: {
        logic_info_t *info = arg;
        info->max_channels = LOGIC_MAX_CHANNELS;
        info->_pad[0] = info->_pad[1] = info->_pad[2] = 0;
        info->sample_khz = duneos_hal_logic_sample_khz();
        return 0;
    }
    default:
        errno = ENOTTY;
        return -1;
    }
}

static ssize_t logic_read(duneos_devfd_t *fd, void *buf, size_t len)
{
    (void)fd;
    if (!s_configured) { errno = EINVAL; return -1; }

    int max = (int)(len / sizeof(logic_sample_t));
    if (max < 1) { errno = EINVAL; return -1; }

    duneos_hal_logic_cfg_t hc = {
        .n                  = s_cfg.n_channels,
        .idle_us            = s_cfg.idle_us,
        .hard_cap_us        = s_cfg.hard_cap_us,
        .trigger_timeout_us = s_cfg.trigger_timeout_us,
    };
    memcpy(hc.gpio, s_cfg.channel_gpio, s_cfg.n_channels);

    int n = duneos_hal_logic_capture(&hc, (duneos_logic_sample_t *)buf, max);
    if (n < 0) return -1;
    return (ssize_t)((size_t)n * sizeof(logic_sample_t));
}

static const duneos_dev_driver_t s_drv_logic = {
    .name  = "logic0",
    .read  = logic_read,
    .ioctl = logic_ioctl_cb,
};

void drv_logic_register(void)
{
    duneos_dev_register(&s_drv_logic);
}

DUNEOS_DRIVER_REGISTER(5, drv_logic_register);
