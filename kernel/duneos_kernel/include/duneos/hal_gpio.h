#pragma once

/*
 * DuneOS Hardware Interface — GPIO
 *
 * Pure-C API: no esp_err_t, no proprietary types.
 * The ESP-IDF implementation lives in arch/xtensa_esp32s3/hal/hal_gpio.c.
 *
 * Returns: 0 on success, -1 on failure (errno set).
 */

#include <stdint.h>

typedef enum {
    DUNEOS_GPIO_DIR_INPUT  = 0,
    DUNEOS_GPIO_DIR_OUTPUT = 1,
} duneos_gpio_dir_t;

typedef enum {
    DUNEOS_GPIO_PULL_NONE   = 0,
    DUNEOS_GPIO_PULL_UP     = 1,
    DUNEOS_GPIO_PULL_DOWN   = 2,
    DUNEOS_GPIO_PULL_UPDOWN = 3,
} duneos_gpio_pull_t;

typedef enum {
    DUNEOS_GPIO_INTR_DISABLE    = 0,
    DUNEOS_GPIO_INTR_POSEDGE    = 1,
    DUNEOS_GPIO_INTR_NEGEDGE    = 2,
    DUNEOS_GPIO_INTR_ANYEDGE    = 3,
    DUNEOS_GPIO_INTR_LOW_LEVEL  = 4,
    DUNEOS_GPIO_INTR_HIGH_LEVEL = 5,
} duneos_gpio_intr_type_t;

typedef void (*duneos_gpio_isr_t)(void *arg);

/* Number of GPIO lines available on this platform. */
int duneos_hal_gpio_count(void);

int duneos_hal_gpio_set_dir  (int gpio_num, duneos_gpio_dir_t dir);
int duneos_hal_gpio_set_pull (int gpio_num, duneos_gpio_pull_t pull);

/* Read GPIO level. Returns 0 or 1 on success, -1 on error (errno set). */
int duneos_hal_gpio_get_level(int gpio_num);
int duneos_hal_gpio_set_level(int gpio_num, int value);

/* Attach/detach an ISR. Pass DUNEOS_GPIO_INTR_DISABLE to remove existing ISR. */
int duneos_hal_gpio_set_intr (int gpio_num, duneos_gpio_intr_type_t intr_type,
                               duneos_gpio_isr_t isr, void *arg);
