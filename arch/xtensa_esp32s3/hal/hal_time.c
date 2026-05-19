/*
 * arch/xtensa_esp32s3/hal/hal_time.c — ESP-IDF implementation of duneos/hal_time.h
 *
 * esp_rom_delay_us: ROM busy-wait, no FreeRTOS dependency.
 * esp_timer_get_time: 64-bit microsecond counter backed by hardware timer group.
 */
#include "duneos/hal_time.h"

#include "esp_rom_sys.h"
#include "esp_timer.h"

void duneos_hal_delay_us(uint32_t us)
{
    esp_rom_delay_us(us);
}

uint64_t duneos_hal_monotonic_us(void)
{
    return (uint64_t)esp_timer_get_time();
}
