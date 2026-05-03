#include "duneos/abi.h"

#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "duneos/task.h"

/*
 * Kernel export symbol table.
 *
 * Every symbol listed here is part of the public ABI. Removing or renaming
 * an entry requires a DUNEOS_ABI_VERSION bump and a matching update in the
 * ELF loader's symbol resolution logic.
 *
 * Adding new symbols is backwards-compatible (old apps simply won't use them).
 */

/* Forward declarations for symbols exported from other translation units */
void duneos_exit(int code);

static const duneos_symbol_t s_symbol_table[] = {
    /* libc basics */
    { "printf",             printf             },
    { "sprintf",            sprintf            },
    { "snprintf",           snprintf           },
    { "memcpy",             memcpy             },
    { "memset",             memset             },
    { "strlen",             strlen             },
    { "strcmp",             strcmp             },
    { "strncmp",            strncmp            },

    /* FreeRTOS time (exposed directly — stable across FreeRTOS versions) */
    { "vTaskDelay",         vTaskDelay         },

    /* DuneOS task API */
    { "duneos_task_create",   duneos_task_create   },
    { "duneos_task_delete",   duneos_task_delete   },
    { "duneos_task_yield",    duneos_task_yield    },
    { "duneos_task_delay_ms", duneos_task_delay_ms },

    /* DuneOS lifecycle */
    { "duneos_exit",        duneos_exit        },

    /* Sentinel */
    { NULL, NULL },
};

const duneos_symbol_t *duneos_symbol_table_get(void)
{
    return s_symbol_table;
}

/*
 * Apps call duneos_exit() to terminate cleanly.
 * The loader's run() function catches this via longjmp or task notification
 * (implementation TBD in Phase 3).
 */
void duneos_exit(int code)
{
    /* TODO Phase 3: signal the supervisor task and suspend this task */
    (void)code;
    vTaskSuspend(NULL);
}
