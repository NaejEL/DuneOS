/*
 * drv_gpiochip_pcf8574 — PCF8574 8-bit I2C quasi-bidirectional GPIO expander.
 *
 * Quasi-bidirectional means: there is NO direction register. Every pin has a
 * weak internal pull-up that's always active; writing 1 releases the
 * low-side driver and lets the pin float high (act as an input). Writing 0
 * actively pulls the pin low (act as an output). Reading the byte returns
 * the current pin states.
 *
 * Consequences for the GPIO ioctl model:
 *   SET_DIR(INPUT)  → write 1 to that bit (release low-side driver)
 *   SET_DIR(OUTPUT) → no-op (out_shadow already reflects the desired output;
 *                     subsequent SET_VALUE writes the actual level)
 *   SET_PULL         → no-op (pull-ups are weak and not configurable)
 *
 * Registers one /dev/gpiochipN per declared PCF8574 instance via the same
 * container_of pattern as drv_gpiochip_sx1509.c.
 */

#include "duneos/dev_driver.h"
#include "duneos/driver_init.h"
#include "duneos/gpio_ioctl.h"
#include "duneos/klog.h"
#include "gpiochip_table.h"
#include "../i2c_bus.h"

#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>

#include <errno.h>
#include <stdio.h>
#include <string.h>

#define TAG "duneos/gpiochip-pcf8574"

#define container_of(ptr, type, member) \
    ((type *)((char *)(ptr) - offsetof(type, member)))

typedef struct {
    duneos_dev_driver_t drv;
    char                name_buf[12];   /* "gpiochipN\0" */
    uint8_t             chip_id;
    uint8_t             bus;            /* I2C bus id */
    uint8_t             i2c_addr;
    uint8_t             pins;           /* always 8 */
    uint8_t             direction;      /* GPIO_DIR_INPUT or GPIO_DIR_OUTPUT — per-chip policy */
    uint8_t             out_shadow;     /* current output latch */
} pcf8574_slot_t;

/* One descriptor per board-declared expander; slot pool sized to match.
 * A board with no gpio_expanders never emits DUNEOS_GPIOCHIP_LIST, so guard the
 * table: a driver enabled without instances (raw Kconfig override) then compiles
 * to a no-op register() instead of failing at the initialiser. */
#ifdef DUNEOS_GPIOCHIP_LIST
static const duneos_gpiochip_desc_t s_descs[] = {
    DUNEOS_GPIOCHIP_LIST(DUNEOS_GPIOCHIP_ROW)
};
#define PCF8574_SLOT_MAX  (sizeof(s_descs) / sizeof(s_descs[0]))

static pcf8574_slot_t    s_slots[PCF8574_SLOT_MAX];
static int               s_num_slots = 0;
#endif
static SemaphoreHandle_t s_lock = NULL;

static pcf8574_slot_t *slot_from_fd(const duneos_devfd_t *fd)
{
    return container_of(fd->drv, pcf8574_slot_t, drv);
}

static int pcf8574_write_byte(pcf8574_slot_t *s, uint8_t val)
{
    return i2c_bus_write_read(s->bus, s->i2c_addr, &val, 1, NULL, 0);
}

static int pcf8574_read_byte(pcf8574_slot_t *s, uint8_t *out)
{
    /* PCF8574 returns its byte on any read — no register pointer. */
    return i2c_bus_write_read(s->bus, s->i2c_addr, NULL, 0, out, 1);
}

static int pcf8574_op_init(pcf8574_slot_t *s)
{
    /* INPUT chips: release all pins (high-impedance). OUTPUT chips: drive all low (safe). */
    s->out_shadow = (s->direction == GPIO_DIR_INPUT) ? 0xFF : 0x00;
    return pcf8574_write_byte(s, s->out_shadow);
}

/* ------------------------------------------------------------------ ioctl */

static int pcf8574_ioctl(duneos_devfd_t *fd, int cmd, void *arg)
{
    pcf8574_slot_t *s = slot_from_fd(fd);

    switch (cmd) {
    case GPIOCHIP_GET_INFO: {
        gpiochip_info_t *info = arg;
        strncpy(info->name, s->name_buf, sizeof(info->name) - 1);
        info->name[sizeof(info->name) - 1] = '\0';
        info->lines = s->pins;
        return 0;
    }
    case GPIOCHIP_SET_DIR: {
        gpio_req_t *r = arg;
        if (r->line >= s->pins) { errno = EINVAL; return -1; }
        if (r->dir == GPIO_DIR_INPUT) {
            xSemaphoreTake(s_lock, portMAX_DELAY);
            s->out_shadow |= (uint8_t)(1u << r->line);
            int rc = pcf8574_write_byte(s, s->out_shadow);
            xSemaphoreGive(s_lock);
            if (rc < 0) { errno = EIO; return -1; }
        }
        /* OUTPUT: no-op. SET_VALUE later will write the actual desired level. */
        return 0;
    }
    case GPIOCHIP_GET_VALUE: {
        gpio_req_t *r = arg;
        if (r->line >= s->pins) { errno = EINVAL; return -1; }
        uint8_t v;
        xSemaphoreTake(s_lock, portMAX_DELAY);
        int rc = pcf8574_read_byte(s, &v);
        xSemaphoreGive(s_lock);
        if (rc < 0) { errno = EIO; return -1; }
        r->val = (uint8_t)((v >> r->line) & 1u);
        return 0;
    }
    case GPIOCHIP_SET_VALUE: {
        gpio_req_t *r = arg;
        if (r->line >= s->pins) { errno = EINVAL; return -1; }
        if (s->direction == GPIO_DIR_INPUT) { errno = EPERM; return -1; }
        xSemaphoreTake(s_lock, portMAX_DELAY);
        if (r->val) s->out_shadow |=  (uint8_t)(1u << r->line);
        else        s->out_shadow &= ~(uint8_t)(1u << r->line);
        int rc = pcf8574_write_byte(s, s->out_shadow);
        xSemaphoreGive(s_lock);
        if (rc < 0) { errno = EIO; return -1; }
        return 0;
    }
    case GPIOCHIP_SET_PULL:
        /* PCF8574 pull-ups are weak, always-on, not configurable. NONE/DOWN
         * requests are rejected with EINVAL so the app knows it can't disable
         * the pull-up; UP/UPDOWN succeed as a no-op (the pull-up is implicit). */
        {
            gpio_req_t *r = arg;
            if (r->line >= s->pins)              { errno = EINVAL; return -1; }
            if (r->pull == GPIO_PULL_DOWN ||
                r->pull == GPIO_PULL_NONE)       { errno = EINVAL; return -1; }
            return 0;
        }
    case GPIOCHIP_SET_IRQ:
        errno = ENOSYS;
        return -1;
    default:
        errno = ENOTTY;
        return -1;
    }
}

/* ------------------------------------------------------------- registration */

#ifdef DUNEOS_GPIOCHIP_LIST
static int register_instance(const duneos_gpiochip_desc_t *d)
{
    if (s_num_slots >= (int)PCF8574_SLOT_MAX) return -1;  /* unreachable: pool == table */

    pcf8574_slot_t *s = &s_slots[s_num_slots];
    snprintf(s->name_buf, sizeof(s->name_buf), "gpiochip%u", (unsigned)d->id);
    s->drv.name   = s->name_buf;
    s->drv.ioctl  = pcf8574_ioctl;
    s->chip_id    = d->id;
    s->bus        = d->bus;
    s->i2c_addr   = d->addr;
    s->pins       = d->pins > 8 ? 8 : d->pins;
    s->direction  = d->direction;

    if (pcf8574_op_init(s) < 0) {
        klog_e(TAG, "%s: chip init failed (bus=%u addr=0x%02x)",
               s->name_buf, d->bus, d->addr);
        return -1;
    }
    if (duneos_dev_register(&s->drv) < 0) {
        klog_e(TAG, "%s: duneos_dev_register failed", s->name_buf);
        return -1;
    }
    klog_i(TAG, "%s: PCF8574 i2c-%u@0x%02x, %u lines (%s)",
           s->name_buf, d->bus, d->addr, (unsigned)s->pins,
           d->direction == GPIO_DIR_INPUT ? "in" : "out");
    s_num_slots++;
    return 0;
}
#endif /* DUNEOS_GPIOCHIP_LIST */

void drv_gpiochip_pcf8574_register(void)
{
#ifdef DUNEOS_GPIOCHIP_LIST
    if (!s_lock) s_lock = xSemaphoreCreateMutex();
    if (!s_lock) { klog_e(TAG, "mutex alloc failed"); return; }

    for (size_t i = 0; i < PCF8574_SLOT_MAX; i++) {
        if (strcmp(s_descs[i].type, "pcf8574") == 0)
            register_instance(&s_descs[i]);
    }
#else
    klog_w(TAG, "PCF8574 driver enabled but board declares no gpio_expanders");
#endif
}

DUNEOS_DRIVER_REGISTER(8, drv_gpiochip_pcf8574_register);
