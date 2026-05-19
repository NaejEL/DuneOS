/*
 * GPIO HAL backend — ESP-IDF implementation.
 * Bridges duneos/hal_gpio.h to driver/gpio.h.
 * ESP-IDF types stay completely inside this translation unit.
 */
#include "duneos/hal_gpio.h"
#include "duneos/klog.h"

#include "driver/gpio.h"
#include "esp_err.h"

#include <errno.h>
#include <stdbool.h>

static const char *TAG = "duneos/hal_gpio";

static bool s_isr_service_installed = false;

int duneos_hal_gpio_count(void)
{
    return GPIO_NUM_MAX;
}

int duneos_hal_gpio_set_dir(int gpio_num, duneos_gpio_dir_t dir)
{
    gpio_config_t cfg = {
        .pin_bit_mask = (1ULL << gpio_num),
        .mode         = (dir == DUNEOS_GPIO_DIR_OUTPUT)
                            ? GPIO_MODE_OUTPUT : GPIO_MODE_INPUT,
        .pull_up_en   = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type    = GPIO_INTR_DISABLE,
    };
    if (gpio_config(&cfg) != ESP_OK) { errno = EINVAL; return -1; }
    return 0;
}

int duneos_hal_gpio_set_pull(int gpio_num, duneos_gpio_pull_t pull)
{
    gpio_pull_mode_t mode;
    switch (pull) {
    case DUNEOS_GPIO_PULL_NONE:   mode = GPIO_FLOATING;        break;
    case DUNEOS_GPIO_PULL_UP:     mode = GPIO_PULLUP_ONLY;     break;
    case DUNEOS_GPIO_PULL_DOWN:   mode = GPIO_PULLDOWN_ONLY;   break;
    case DUNEOS_GPIO_PULL_UPDOWN: mode = GPIO_PULLUP_PULLDOWN; break;
    default: errno = EINVAL; return -1;
    }
    if (gpio_set_pull_mode((gpio_num_t)gpio_num, mode) != ESP_OK) {
        errno = EINVAL; return -1;
    }
    return 0;
}

int duneos_hal_gpio_get_level(int gpio_num)
{
    return gpio_get_level((gpio_num_t)gpio_num);
}

int duneos_hal_gpio_set_level(int gpio_num, int value)
{
    if (gpio_set_level((gpio_num_t)gpio_num, (uint32_t)value) != ESP_OK) {
        errno = EINVAL; return -1;
    }
    return 0;
}

int duneos_hal_gpio_set_intr(int gpio_num, duneos_gpio_intr_type_t intr_type,
                              duneos_gpio_isr_t isr, void *arg)
{
    /* Lazily install the ISR service on first use. */
    if (!s_isr_service_installed) {
        esp_err_t err = gpio_install_isr_service(0);
        if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
            klog_e(TAG, "gpio_install_isr_service: %s", esp_err_to_name(err));
            errno = EIO; return -1;
        }
        s_isr_service_installed = true;
    }

    if (intr_type == DUNEOS_GPIO_INTR_DISABLE) {
        gpio_isr_handler_remove((gpio_num_t)gpio_num);
        gpio_set_intr_type((gpio_num_t)gpio_num, GPIO_INTR_DISABLE);
        return 0;
    }

    gpio_int_type_t idf_intr;
    switch (intr_type) {
    case DUNEOS_GPIO_INTR_POSEDGE:    idf_intr = GPIO_INTR_POSEDGE;    break;
    case DUNEOS_GPIO_INTR_NEGEDGE:    idf_intr = GPIO_INTR_NEGEDGE;    break;
    case DUNEOS_GPIO_INTR_ANYEDGE:    idf_intr = GPIO_INTR_ANYEDGE;    break;
    case DUNEOS_GPIO_INTR_LOW_LEVEL:  idf_intr = GPIO_INTR_LOW_LEVEL;  break;
    case DUNEOS_GPIO_INTR_HIGH_LEVEL: idf_intr = GPIO_INTR_HIGH_LEVEL; break;
    default: errno = EINVAL; return -1;
    }

    gpio_config_t gcfg = {
        .pin_bit_mask = (1ULL << gpio_num),
        .mode         = GPIO_MODE_INPUT,
        .pull_up_en   = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type    = idf_intr,
    };
    if (gpio_config(&gcfg) != ESP_OK) { errno = EINVAL; return -1; }

    if (gpio_isr_handler_add((gpio_num_t)gpio_num, isr, arg) != ESP_OK) {
        errno = EIO; return -1;
    }
    return 0;
}
