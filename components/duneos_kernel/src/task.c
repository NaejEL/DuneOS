#include "duneos/task.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_heap_caps.h"
#include "duneos/klog.h"

static const char *TAG = "duneos/task";

esp_err_t duneos_task_create(const duneos_task_config_t *cfg,
                              duneos_task_handle_t       *out_handle)
{
    if (!cfg || !cfg->func) {
        return ESP_ERR_INVALID_ARG;
    }

    StaticTask_t *tcb;
    StackType_t  *stack;

    if (cfg->use_psram_stack) {
        tcb   = heap_caps_malloc(sizeof(StaticTask_t), MALLOC_CAP_SPIRAM);
        stack = heap_caps_malloc(cfg->stack_size,      MALLOC_CAP_SPIRAM);
    } else {
        tcb   = heap_caps_malloc(sizeof(StaticTask_t), MALLOC_CAP_INTERNAL);
        stack = heap_caps_malloc(cfg->stack_size,      MALLOC_CAP_INTERNAL);
    }

    if (!tcb || !stack) {
        free(tcb);
        free(stack);
        klog_e(TAG, "failed to allocate stack/TCB for task '%s'", cfg->name);
        return ESP_ERR_NO_MEM;
    }

    TaskHandle_t handle = xTaskCreateStatic(
        cfg->func,
        cfg->name,
        cfg->stack_size / sizeof(StackType_t),
        cfg->arg,
        cfg->priority,
        stack,
        tcb
    );

    if (!handle) {
        free(tcb);
        free(stack);
        return ESP_FAIL;
    }

    if (out_handle) {
        *out_handle = handle;
    }
    return ESP_OK;
}

void duneos_task_delete(duneos_task_handle_t handle)
{
    vTaskDelete((TaskHandle_t)handle);
}

void duneos_task_yield(void)
{
    taskYIELD();
}

void duneos_task_suspend(duneos_task_handle_t handle)
{
    vTaskSuspend((TaskHandle_t)handle);
}

void duneos_task_resume(duneos_task_handle_t handle)
{
    vTaskResume((TaskHandle_t)handle);
}

void duneos_task_delay_ms(uint32_t ms)
{
    vTaskDelay(pdMS_TO_TICKS(ms));
}
