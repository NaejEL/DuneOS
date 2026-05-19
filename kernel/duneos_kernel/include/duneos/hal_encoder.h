#pragma once

/*
 * DuneOS Hardware Interface — Quadrature encoder.
 *
 * Pure-C API: no ESP-IDF types.  Two implementations are compiled together:
 *   arch/xtensa_esp32s3/hal/hal_encoder.c    — ESP-IDF PCNT (strong symbol)
 *   src/drivers/input/hal_encoder_fallback.c — GPIO 500 Hz polling (weak)
 *
 * The linker automatically picks the PCNT implementation on targets that
 * provide it; the GPIO fallback is used on all other architectures.
 *
 * Callback contract:
 *   PCNT backend:    called from ISR context — no malloc, no VFS, no FreeRTOS
 *                    blocking calls.  Keep it short.
 *   Polling backend: called from task context — no such restriction.
 */

#include <stdint.h>

/*
 * Callback: delta = +1 per CW detent, -1 per CCW detent.
 * ctx: opaque value supplied at open time.
 */
typedef void (*duneos_hal_encoder_cb_t)(int delta, void *ctx);

typedef struct {
    int                     pin_a;
    int                     pin_b;
    duneos_hal_encoder_cb_t cb;
    void                   *ctx;
} duneos_hal_encoder_config_t;

typedef struct duneos_hal_encoder_s duneos_hal_encoder_t;

duneos_hal_encoder_t *duneos_hal_encoder_open(const duneos_hal_encoder_config_t *cfg);
void                  duneos_hal_encoder_close(duneos_hal_encoder_t *enc);
