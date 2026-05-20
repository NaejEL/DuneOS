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
#include "duneos/gpio_ioctl.h"
#include "duneos/klog.h"
#include "board_config.h"
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
    uint8_t             i2c_addr;
    uint8_t             pins;           /* always 8 */
    uint8_t             out_shadow;     /* current output latch */
} pcf8574_slot_t;

#define MAX_PCF8574_INSTANCES 4
static pcf8574_slot_t  s_slots[MAX_PCF8574_INSTANCES];
static int             s_num_slots = 0;
static SemaphoreHandle_t s_lock = NULL;

static pcf8574_slot_t *slot_from_fd(const duneos_devfd_t *fd)
{
    return container_of(fd->drv, pcf8574_slot_t, drv);
}

static int pcf8574_write_byte(pcf8574_slot_t *s, uint8_t val)
{
    return i2c_bus_write_read(0, s->i2c_addr, &val, 1, NULL, 0);
}

static int pcf8574_read_byte(pcf8574_slot_t *s, uint8_t *out)
{
    /* PCF8574 returns its byte on any read — no register pointer. */
    return i2c_bus_write_read(0, s->i2c_addr, NULL, 0, out, 1);
}

static int pcf8574_op_init(pcf8574_slot_t *s)
{
    /* Boot with all pins released (= all-input). */
    s->out_shadow = 0xFF;
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

static int register_instance(uint8_t chip_id, uint8_t i2c_addr, uint8_t pins)
{
    if (s_num_slots >= MAX_PCF8574_INSTANCES) {
        klog_e(TAG, "too many PCF8574 instances (>%d)", MAX_PCF8574_INSTANCES);
        return -1;
    }
    pcf8574_slot_t *s = &s_slots[s_num_slots];
    snprintf(s->name_buf, sizeof(s->name_buf), "gpiochip%u", (unsigned)chip_id);
    s->drv.name   = s->name_buf;
    s->drv.ioctl  = pcf8574_ioctl;
    s->chip_id    = chip_id;
    s->i2c_addr   = i2c_addr;
    s->pins       = pins > 8 ? 8 : pins;

    if (pcf8574_op_init(s) < 0) {
        klog_e(TAG, "%s: chip init failed (addr=0x%02x)", s->name_buf, i2c_addr);
        return -1;
    }
    if (duneos_dev_register(&s->drv) < 0) {
        klog_e(TAG, "%s: duneos_dev_register failed", s->name_buf);
        return -1;
    }
    klog_i(TAG, "%s: PCF8574 @0x%02x, %u lines", s->name_buf, i2c_addr, (unsigned)pins);
    s_num_slots++;
    return 0;
}

void drv_gpiochip_pcf8574_register(void)
{
    if (!s_lock) s_lock = xSemaphoreCreateMutex();
    if (!s_lock) { klog_e(TAG, "mutex alloc failed"); return; }

#ifdef DUNEOS_GPIOCHIP1_TYPE
    if (strcmp(DUNEOS_GPIOCHIP1_TYPE, "pcf8574") == 0)
        register_instance(1, DUNEOS_GPIOCHIP1_I2C_ADDR, DUNEOS_GPIOCHIP1_PINS);
#endif
#ifdef DUNEOS_GPIOCHIP2_TYPE
    if (strcmp(DUNEOS_GPIOCHIP2_TYPE, "pcf8574") == 0)
        register_instance(2, DUNEOS_GPIOCHIP2_I2C_ADDR, DUNEOS_GPIOCHIP2_PINS);
#endif
#ifdef DUNEOS_GPIOCHIP3_TYPE
    if (strcmp(DUNEOS_GPIOCHIP3_TYPE, "pcf8574") == 0)
        register_instance(3, DUNEOS_GPIOCHIP3_I2C_ADDR, DUNEOS_GPIOCHIP3_PINS);
#endif
#ifdef DUNEOS_GPIOCHIP4_TYPE
    if (strcmp(DUNEOS_GPIOCHIP4_TYPE, "pcf8574") == 0)
        register_instance(4, DUNEOS_GPIOCHIP4_I2C_ADDR, DUNEOS_GPIOCHIP4_PINS);
#endif
}
