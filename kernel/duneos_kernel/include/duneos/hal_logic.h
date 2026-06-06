#pragma once

/*
 * DuneOS Hardware Interface — synchronous logic capture (see ADR 020).
 *
 * Pure-C API: no esp_err_t, no SDK types. The ESP-IDF polled implementation
 * lives in arch/xtensa_esp32s3/hal/hal_logic.c; a future DMA backend (LCD_CAM)
 * implements the same contract.
 */

#include <stdint.h>

#define DUNEOS_HAL_LOGIC_MAX_CH 8

typedef struct {
    uint8_t  gpio[DUNEOS_HAL_LOGIC_MAX_CH]; /* physical GPIO per channel bit  */
    uint8_t  n;                             /* channel count (1..MAX_CH)      */
    uint32_t idle_us;                       /* stop after this no-change gap  */
    uint32_t hard_cap_us;                   /* absolute capture ceiling       */
    uint32_t trigger_timeout_us;            /* give up waiting for activity   */
} duneos_hal_logic_cfg_t;

typedef struct {
    uint32_t t_us;    /* microseconds since the first recorded sample */
    uint32_t levels;  /* bit i = level of channel i                   */
} duneos_logic_sample_t;

/*
 * Capture one window into out[] (capacity `max` samples).
 *
 * Busy-waits — interrupts ON, preemptible — up to cfg->trigger_timeout_us for
 * any channel to change. If nothing changes, returns 0. Once activity is seen,
 * disables interrupts on the calling core and tight-polls, writing one sample
 * per transition of the channel mask, until out is full, cfg->idle_us elapses
 * with no change, or cfg->hard_cap_us is reached. out[0] is the initial state
 * at t_us = 0.
 *
 * Returns: >0 = number of samples captured; 0 = no activity (trigger timeout);
 *          -1 = error (errno set).
 *
 * The caller must have configured the GPIOs as inputs beforehand.
 */
int duneos_hal_logic_capture(const duneos_hal_logic_cfg_t *cfg,
                             duneos_logic_sample_t *out, int max);

/*
 * Effective sample rate of the polled loop, in kHz (measured/estimated once).
 * Informational — record timestamps are exact CPU-cycle counts, not quantised.
 */
uint32_t duneos_hal_logic_sample_khz(void);
