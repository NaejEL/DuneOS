/*
 * duneos/hal_time.h — DuneOS Hardware Interface: hardware-backed time
 *
 * Pure-C public API.  No ESP-IDF types, no proprietary headers.
 * Implementation: arch/xtensa_esp32s3/hal/hal_time.c
 *
 * Scope: hardware-timer-backed delay and monotonic clock only.
 * Task sleep / OS tick belong to the OSAL layer (Phase 27).
 */
#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * duneos_hal_delay_us — busy-wait for exactly `us` microseconds.
 * Uses the hardware timer — does NOT yield the scheduler.
 * Keep durations short (< 1 ms); use OS sleep for longer waits.
 */
void duneos_hal_delay_us(uint32_t us);

/*
 * duneos_hal_monotonic_us — monotonic clock in microseconds since boot.
 * Never resets, never wraps within a normal device lifetime (64-bit range).
 * Suitable for timestamping input events and computing elapsed time.
 */
uint64_t duneos_hal_monotonic_us(void);

#ifdef __cplusplus
}
#endif
