/*
 * Quadrature encoder HAL — ESP-IDF PCNT backend.
 *
 * Strong-symbol implementation: the linker prefers this over the GPIO polling
 * fallback in hal_encoder_fallback.c (which uses __attribute__((weak))).
 *
 * x4 decoding: all 4 signal edges per mechanical cycle are counted.
 * One mechanical detent = 4 PCNT counts.
 * Watch points at ±DETENT_COUNTS fire the user callback once per detent.
 *
 * Edge/level action derivation (CW = +1, AB Gray-code 00→10→11→01→00):
 *   Channel A (A=edge, B=level):
 *     A rises, B=0 → CW  (+): INCREASE × KEEP   ✓
 *     A rises, B=1 → CCW (−): INCREASE × INVERSE ✓
 *     A falls, B=1 → CW  (+): DECREASE × INVERSE ✓
 *     A falls, B=0 → CCW (−): DECREASE × KEEP    ✓
 *   Channel B (B=edge, A=level): symmetric, same action codes.
 */

#include "duneos/hal_encoder.h"

#include "driver/pulse_cnt.h"
#include "esp_attr.h"

#include <errno.h>
#include <stdlib.h>

#define DETENT_COUNTS 4

struct duneos_hal_encoder_s {
    pcnt_unit_handle_t    unit;
    pcnt_channel_handle_t chan_a;
    pcnt_channel_handle_t chan_b;
    duneos_hal_encoder_cb_t cb;
    void                   *ctx;
};

static bool IRAM_ATTR pcnt_event_cb(pcnt_unit_handle_t unit,
                                    const pcnt_watch_event_data_t *edata,
                                    void *user_ctx)
{
    duneos_hal_encoder_t *enc = user_ctx;
    int delta = (edata->watch_point_value > 0) ? 1 : -1;
    pcnt_unit_clear_count(unit);
    enc->cb(delta, enc->ctx);
    return false; /* no higher-priority task wake needed */
}

duneos_hal_encoder_t *duneos_hal_encoder_open(const duneos_hal_encoder_config_t *cfg)
{
    if (!cfg || !cfg->cb) { errno = EINVAL; return NULL; }

    duneos_hal_encoder_t *enc = malloc(sizeof(*enc));
    if (!enc) { errno = ENOMEM; return NULL; }
    enc->cb  = cfg->cb;
    enc->ctx = cfg->ctx;

    /* Range ±8 absorbs brief mechanical bounce before the watch fires. */
    const pcnt_unit_config_t unit_cfg = {
        .high_limit =  DETENT_COUNTS * 2,
        .low_limit  = -DETENT_COUNTS * 2,
    };
    if (pcnt_new_unit(&unit_cfg, &enc->unit) != ESP_OK) goto fail_unit;

    /* Channel A: A is the edge pin, B is the level (direction) pin */
    const pcnt_chan_config_t chan_a_cfg = {
        .edge_gpio_num  = cfg->pin_a,
        .level_gpio_num = cfg->pin_b,
    };
    if (pcnt_new_channel(enc->unit, &chan_a_cfg, &enc->chan_a) != ESP_OK)
        goto fail_chan_a;
    pcnt_channel_set_edge_action(enc->chan_a,
        PCNT_CHANNEL_EDGE_ACTION_INCREASE,
        PCNT_CHANNEL_EDGE_ACTION_DECREASE);
    pcnt_channel_set_level_action(enc->chan_a,
        PCNT_CHANNEL_LEVEL_ACTION_INVERSE,
        PCNT_CHANNEL_LEVEL_ACTION_KEEP);

    /* Channel B: B is the edge pin, A is the level (direction) pin */
    const pcnt_chan_config_t chan_b_cfg = {
        .edge_gpio_num  = cfg->pin_b,
        .level_gpio_num = cfg->pin_a,
    };
    if (pcnt_new_channel(enc->unit, &chan_b_cfg, &enc->chan_b) != ESP_OK)
        goto fail_chan_b;
    pcnt_channel_set_edge_action(enc->chan_b,
        PCNT_CHANNEL_EDGE_ACTION_INCREASE,
        PCNT_CHANNEL_EDGE_ACTION_DECREASE);
    pcnt_channel_set_level_action(enc->chan_b,
        PCNT_CHANNEL_LEVEL_ACTION_KEEP,
        PCNT_CHANNEL_LEVEL_ACTION_INVERSE);

    if (pcnt_unit_add_watch_point(enc->unit,  DETENT_COUNTS) != ESP_OK) goto fail_wp;
    if (pcnt_unit_add_watch_point(enc->unit, -DETENT_COUNTS) != ESP_OK) goto fail_wp;

    const pcnt_event_callbacks_t cbs = { .on_reach = pcnt_event_cb };
    pcnt_unit_register_event_callbacks(enc->unit, &cbs, enc);

    pcnt_unit_enable(enc->unit);
    pcnt_unit_clear_count(enc->unit);
    pcnt_unit_start(enc->unit);
    return enc;

fail_wp:
    pcnt_del_channel(enc->chan_b);
fail_chan_b:
    pcnt_del_channel(enc->chan_a);
fail_chan_a:
    pcnt_del_unit(enc->unit);
fail_unit:
    free(enc);
    errno = EIO;
    return NULL;
}

void duneos_hal_encoder_close(duneos_hal_encoder_t *enc)
{
    if (!enc) return;
    pcnt_unit_stop(enc->unit);
    pcnt_unit_disable(enc->unit);
    pcnt_del_channel(enc->chan_b);
    pcnt_del_channel(enc->chan_a);
    pcnt_del_unit(enc->unit);
    free(enc);
}
