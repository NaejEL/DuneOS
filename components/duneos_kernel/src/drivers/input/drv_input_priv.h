#pragma once

#include "duneos/input_ioctl.h"

/*
 * Internal API shared between drv_input.c (infrastructure) and hardware
 * backends (kb_iomatrix.c, future btn_gpio.c, enc_quadrature.c …).
 *
 * Backends call drv_input_push_event() to post events into the ring buffer.
 * Safe to call from any FreeRTOS task context (not from ISR).
 */
void drv_input_push_event(const input_event_t *ev);
