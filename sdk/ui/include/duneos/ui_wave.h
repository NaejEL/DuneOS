#pragma once

/*
 * libui — wave widget: a digital-timing (logic-analyzer) chronogram.
 *
 * Renders N channels as square waves on a shared time axis from a transition
 * stream — each sample is the channel-level bitmask at a timestamp (the same
 * shape a logic capture or a VCD produces). Pan along time and zoom. Generic:
 * any digital signals (I2C, SPI, UART, GPIO), not protocol-specific. Apps draw
 * protocol-decode overlays themselves, aligned via ui_wave_time_x().
 *
 *   ui_wave_t w;
 *   ui_wave_init(&w, 0, bar_h, ui_screen_w(ui), area_h);
 *   ui_wave_set(&w, samples, n, nch, labels);
 *   ui_wave_draw(ui, &w);
 *   ... on key: if (ui_wave_key(&w, code)) ui_wave_draw(ui, &w); ...
 */

#include <stdint.h>
#include "duneos/ui.h"

/* One transition: channel levels (bit i = channel i) at time t_us. Matches the
 * layout of logic_sample_t / i2c_sample_t, so buffers can be cast across. */
typedef struct { uint32_t t_us; uint32_t levels; } ui_wave_sample_t;

typedef struct {
    int x, y, w, h;
    const ui_wave_sample_t *samples;
    int n;
    int nch;                       /* channel count */
    const char *const *labels;     /* nch channel names (≤ 3 chars shown) */
    uint32_t t0;                   /* left-edge time (us) */
    uint32_t us_per_px;            /* zoom */
    uint32_t span_us;              /* total capture span (bounds) */
} ui_wave_t;

void ui_wave_init(ui_wave_t *w, int x, int y, int wd, int ht);
void ui_wave_set(ui_wave_t *w, const ui_wave_sample_t *s, int n, int nch,
                 const char *const *labels);   /* fits the whole capture, resets view */
void ui_wave_draw(ui_t *ui, const ui_wave_t *w);

/* Left/Right pan, Up/Down zoom. Returns 1 if the view moved (redraw). */
int ui_wave_key(ui_wave_t *w, uint16_t key);

/* Pixel x for a timestamp (clamped to the plot area) — for decode overlays. */
int ui_wave_time_x(const ui_wave_t *w, uint32_t t_us);
