/*
 * Logic-capture HAL backend — ESP-IDF / ESP32-S3, polled (see ADR 020).
 *
 * Synchronous register-poll sampler: read the GPIO input registers in a tight
 * loop and record the channel mask whenever it changes, timestamped from the
 * CPU cycle counter. Interrupts are disabled on the calling core during the
 * burst so a tick/ISR can't preempt the loop and miss a half-bit; the burst is
 * bounded (hard_cap) well under the interrupt watchdog. This is the reliable
 * alternative to per-edge GPIO interrupts, which can't keep pace at I2C rates.
 *
 * A future DMA backend (LCD_CAM/DVP) implements the same hal_logic.h contract.
 */
#include "duneos/hal_logic.h"

#include "freertos/FreeRTOS.h"
#include "esp_cpu.h"
#include "esp_private/esp_clk.h"
#include "soc/soc.h"
#include "soc/gpio_reg.h"

#include <errno.h>

static portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;

/* Per-channel decode of which input register and bit to test. */
typedef struct {
    uint8_t  hi;   /* 0 = GPIO_IN_REG (0..31), 1 = GPIO_IN1_REG (32..48) */
    uint32_t bit;  /* 1u << (gpio % 32)                                  */
} chmap_t;

static inline uint32_t cyc_per_us(void)
{
    int hz = esp_clk_cpu_freq();
    return hz > 0 ? (uint32_t)(hz / 1000000) : 240;
}

static inline uint32_t read_mask(const chmap_t *m, int n)
{
    uint32_t in0 = REG_READ(GPIO_IN_REG);
    uint32_t in1 = REG_READ(GPIO_IN1_REG);
    uint32_t v = 0;
    for (int i = 0; i < n; i++) {
        uint32_t reg = m[i].hi ? in1 : in0;
        if (reg & m[i].bit) v |= (1u << i);
    }
    return v;
}

int duneos_hal_logic_capture(const duneos_hal_logic_cfg_t *cfg,
                             duneos_logic_sample_t *out, int max)
{
    if (!cfg || !out || max < 1 || cfg->n < 1 || cfg->n > DUNEOS_HAL_LOGIC_MAX_CH) {
        errno = EINVAL;
        return -1;
    }

    chmap_t map[DUNEOS_HAL_LOGIC_MAX_CH];
    for (int i = 0; i < cfg->n; i++) {
        uint8_t g = cfg->gpio[i];
        map[i].hi  = (g >= 32) ? 1 : 0;
        map[i].bit = 1u << (g & 31);
    }

    const uint32_t cpm        = cyc_per_us();
    const uint32_t trig_to    = cfg->trigger_timeout_us ? cfg->trigger_timeout_us : 200000;
    const uint32_t hard_cap   = cfg->hard_cap_us ? cfg->hard_cap_us : 50000;
    const uint32_t idle_us    = cfg->idle_us;
    const uint32_t trig_cyc   = trig_to * cpm;
    const uint32_t cap_cyc    = hard_cap * cpm;
    const uint32_t idle_cyc   = idle_us * cpm;

    /* --- Trigger wait: interrupts on, preemptible, bounded. --------------- */
    uint32_t base = read_mask(map, cfg->n);
    uint32_t t_wait0 = esp_cpu_get_cycle_count();
    uint32_t cur = base;
    for (;;) {
        cur = read_mask(map, cfg->n);
        if (cur != base) break;
        if ((esp_cpu_get_cycle_count() - t_wait0) > trig_cyc) return 0;
    }

    /* --- Burst: interrupts off on this core, tight transition recorder. --- */
    int n = 0;
    portENTER_CRITICAL(&s_mux);
    uint32_t c0   = esp_cpu_get_cycle_count();
    uint32_t last = read_mask(map, cfg->n);
    out[0].t_us   = 0;
    out[0].levels = last;
    n = 1;
    uint32_t last_change = c0;
    while (n < max) {
        uint32_t now = esp_cpu_get_cycle_count();
        uint32_t m   = read_mask(map, cfg->n);
        if (m != last) {
            out[n].t_us   = (now - c0) / cpm;
            out[n].levels = m;
            n++;
            last        = m;
            last_change = now;
        } else {
            uint32_t since_change = now - last_change;
            uint32_t since_start  = now - c0;
            if ((idle_cyc && since_change > idle_cyc) || since_start > cap_cyc) break;
        }
    }
    portEXIT_CRITICAL(&s_mux);

    return n;
}

uint32_t duneos_hal_logic_sample_khz(void)
{
    /* Calibrate the empty read loop once: how many mask reads per microsecond,
     * interrupts off, for a representative 2-channel map. */
    static uint32_t cached = 0;
    if (cached) return cached;

    chmap_t map[2] = {
        { 0, 1u << 0 }, { 0, 1u << 1 },
    };
    const uint32_t cpm = cyc_per_us();
    volatile uint32_t sink = 0;

    portENTER_CRITICAL(&s_mux);
    uint32_t c0 = esp_cpu_get_cycle_count();
    uint32_t iters = 0;
    while ((esp_cpu_get_cycle_count() - c0) < (cpm * 1000)) { /* ~1 ms */
        sink ^= read_mask(map, 2);
        iters++;
    }
    portEXIT_CRITICAL(&s_mux);
    (void)sink;

    cached = iters; /* iters in ~1 ms == samples per ms == kHz */
    return cached;
}
