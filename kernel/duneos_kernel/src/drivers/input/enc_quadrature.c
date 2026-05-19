/*
 * Quadrature rotary encoder kernel driver — thin shim over duneos_hal_encoder.
 *
 * Delegates all hardware decoding to duneos_hal_encoder_open().  The HAL
 * provides either a PCNT ISR-driven backend (Xtensa ESP32-S3) or the GPIO
 * polling fallback for other architectures.
 *
 * Board defines required (board_config.h):
 *   DUNEOS_ENCODER_A_PIN  — GPIO for channel A
 *   DUNEOS_ENCODER_B_PIN  — GPIO for channel B
 */

#include "duneos/hal_encoder.h"
#include "duneos/input_ioctl.h"
#include "duneos/hal_time.h"
#include "duneos/klog.h"
#include "drv_input_priv.h"
#include "board_config.h"

static const char *TAG = "duneos/enc_quadrature";

static void enc_event_cb(int delta, void *ctx)
{
    (void)ctx;
    input_event_t ev = {
        .time_ms = (uint32_t)(duneos_hal_monotonic_us() / 1000),
        .type    = INPUT_EV_REL,
        .code    = REL_WHEEL,
        .value   = delta,
    };
    drv_input_push_event(&ev);
}

void enc_quadrature_init(void)
{
    const duneos_hal_encoder_config_t cfg = {
        .pin_a = DUNEOS_ENCODER_A_PIN,
        .pin_b = DUNEOS_ENCODER_B_PIN,
        .cb    = enc_event_cb,
        .ctx   = NULL,
    };
    duneos_hal_encoder_t *enc = duneos_hal_encoder_open(&cfg);
    if (!enc) {
        klog_e(TAG, "encoder open failed");
        return;
    }
    klog_i(TAG, "encoder A=%d B=%d ready", DUNEOS_ENCODER_A_PIN, DUNEOS_ENCODER_B_PIN);
    (void)enc; /* handle is never closed — encoder runs for the lifetime of the kernel */
}
