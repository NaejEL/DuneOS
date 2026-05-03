#pragma once

#include <stdint.h>
#include <stdbool.h>
#include "esp_err.h"

/*
 * Thin FreeRTOS abstraction layer.
 * Apps and kernel subsystems use this API; they do not call FreeRTOS directly.
 * This decouples the ABI from FreeRTOS internals and gives us a place to
 * add per-task accounting (watchdog, memory ownership, crash isolation).
 */

typedef void *duneos_task_handle_t;
typedef void (*duneos_task_func_t)(void *arg);

typedef struct {
    const char        *name;
    duneos_task_func_t func;
    void              *arg;
    uint32_t           stack_size;  /* bytes, allocated from DRAM by default */
    uint8_t            priority;    /* 0 (idle) … configMAX_PRIORITIES-1 */
    bool               use_psram_stack; /* allocate stack from PSRAM instead */
} duneos_task_config_t;

esp_err_t duneos_task_create(const duneos_task_config_t *cfg,
                              duneos_task_handle_t       *out_handle);

void duneos_task_delete(duneos_task_handle_t handle);
void duneos_task_yield(void);
void duneos_task_suspend(duneos_task_handle_t handle);
void duneos_task_resume(duneos_task_handle_t handle);

/* Delay the calling task — use instead of vTaskDelay directly */
void duneos_task_delay_ms(uint32_t ms);
