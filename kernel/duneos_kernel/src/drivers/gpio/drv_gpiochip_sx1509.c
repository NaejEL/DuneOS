/*
 * drv_gpiochip_sx1509 — SX1509 16-bit I2C GPIO expander.
 *
 * Registers one /dev/gpiochipN node per declared SX1509 instance (board.yaml
 * `gpio_expanders: [{type: sx1509, i2c_addr, pins: 16}, ...]`). Multiple
 * instances on the same I2C bus are supported via the container_of pattern —
 * each slot embeds its own duneos_dev_driver_t.
 *
 * Datasheet register subset (auto-incrementing internal pointer):
 *   0x06 RegPullUpB    (pins 8..15)
 *   0x07 RegPullUpA    (pins 0..7)
 *   0x0E RegDirB       (1 = input,  0 = output)
 *   0x0F RegDirA
 *   0x10 RegDataB      (current state — read or output latch)
 *   0x11 RegDataA
 *
 * Thread-safety: a per-driver mutex serialises read-modify-write sequences
 * across instances. The shared i2c_bus already serialises bus transactions.
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

#define TAG "duneos/gpiochip-sx1509"

#define SX1509_REG_PULLUP_B  0x06
#define SX1509_REG_PULLUP_A  0x07
#define SX1509_REG_DIR_B     0x0E
#define SX1509_REG_DIR_A     0x0F
#define SX1509_REG_DATA_B    0x10
#define SX1509_REG_DATA_A    0x11

#define container_of(ptr, type, member) \
    ((type *)((char *)(ptr) - offsetof(type, member)))

typedef struct {
    duneos_dev_driver_t drv;
    char                name_buf[12];   /* "gpiochipN\0" */
    uint8_t             chip_id;
    uint8_t             bus;            /* I2C bus id */
    uint8_t             i2c_addr;
    uint8_t             pins;
    /* 16-bit shadows. SX1509 convention: dir bit=1 → input. */
    uint16_t            dir_shadow;     /* defaults to all-input (0xFFFF) */
    uint16_t            out_shadow;
    uint16_t            pullup_shadow;
} sx1509_slot_t;

/* One descriptor per board-declared expander; slot pool sized to match.
 * A board with no gpio_expanders never emits DUNEOS_GPIOCHIP_LIST, so guard the
 * table: a driver enabled without instances (raw Kconfig override) then compiles
 * to a no-op register() instead of failing at the initialiser. */
#ifdef DUNEOS_GPIOCHIP_LIST
static const duneos_gpiochip_desc_t s_descs[] = {
    DUNEOS_GPIOCHIP_LIST(DUNEOS_GPIOCHIP_ROW)
};
#define SX1509_SLOT_MAX  (sizeof(s_descs) / sizeof(s_descs[0]))

static sx1509_slot_t     s_slots[SX1509_SLOT_MAX];
static int               s_num_slots = 0;
#endif
static SemaphoreHandle_t s_lock = NULL;

static sx1509_slot_t *slot_from_fd(const duneos_devfd_t *fd)
{
    return container_of(fd->drv, sx1509_slot_t, drv);
}

/* Write a single register (1-byte payload). */
static int sx1509_write_reg(sx1509_slot_t *s, uint8_t reg, uint8_t val)
{
    uint8_t buf[2] = { reg, val };
    return i2c_bus_write_read(s->bus, s->i2c_addr, buf, 2, NULL, 0);
}

/* Read a single register (1-byte). */
static int sx1509_read_reg(sx1509_slot_t *s, uint8_t reg, uint8_t *out)
{
    return i2c_bus_write_read(s->bus, s->i2c_addr, &reg, 1, out, 1);
}

/* Push the 16-bit shadow `val` to a pair of registers (low_reg = pins 0..7,
 * low_reg-1 = pins 8..15; SX1509 reg map: A < B numerically reversed). */
static int sx1509_write_pair(sx1509_slot_t *s, uint8_t reg_a, uint16_t val)
{
    int r;
    if ((r = sx1509_write_reg(s, reg_a,     (uint8_t)(val & 0xFF))) < 0) return r;
    if ((r = sx1509_write_reg(s, reg_a - 1, (uint8_t)(val >> 8)))   < 0) return r;
    return 0;
}

static int sx1509_op_init(sx1509_slot_t *s)
{
    /* Reset shadows to known state: all inputs, no pull-ups, outputs low.
     * Pushing them now means the chip matches our shadow after init. */
    s->dir_shadow    = 0xFFFF;   /* all inputs */
    s->out_shadow    = 0x0000;
    s->pullup_shadow = 0x0000;

    int r = sx1509_write_pair(s, SX1509_REG_DIR_A,    s->dir_shadow);
    if (r < 0) { klog_e(TAG, "%s: init DIR write failed", s->name_buf); return r; }
    r = sx1509_write_pair(s, SX1509_REG_DATA_A,   s->out_shadow);
    if (r < 0) { klog_e(TAG, "%s: init DATA write failed", s->name_buf); return r; }
    r = sx1509_write_pair(s, SX1509_REG_PULLUP_A, s->pullup_shadow);
    if (r < 0) { klog_e(TAG, "%s: init PULLUP write failed", s->name_buf); return r; }
    return 0;
}

/* ------------------------------------------------------------------ ioctl */

static int sx1509_ioctl(duneos_devfd_t *fd, int cmd, void *arg)
{
    sx1509_slot_t *s = slot_from_fd(fd);

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
        xSemaphoreTake(s_lock, portMAX_DELAY);
        if (r->dir == GPIO_DIR_INPUT) s->dir_shadow |=  (uint16_t)(1u << r->line);
        else                          s->dir_shadow &= ~(uint16_t)(1u << r->line);
        int rc = sx1509_write_pair(s, SX1509_REG_DIR_A, s->dir_shadow);
        xSemaphoreGive(s_lock);
        if (rc < 0) { errno = EIO; return -1; }
        return 0;
    }
    case GPIOCHIP_GET_VALUE: {
        gpio_req_t *r = arg;
        if (r->line >= s->pins) { errno = EINVAL; return -1; }
        uint8_t lo = 0, hi = 0;
        xSemaphoreTake(s_lock, portMAX_DELAY);
        int rc1 = sx1509_read_reg(s, SX1509_REG_DATA_A, &lo);
        int rc2 = (s->pins > 8) ? sx1509_read_reg(s, SX1509_REG_DATA_B, &hi) : 0;
        xSemaphoreGive(s_lock);
        if (rc1 < 0 || rc2 < 0) { errno = EIO; return -1; }
        uint16_t data = (uint16_t)lo | ((uint16_t)hi << 8);
        r->val = (uint8_t)((data >> r->line) & 1u);
        return 0;
    }
    case GPIOCHIP_SET_VALUE: {
        gpio_req_t *r = arg;
        if (r->line >= s->pins) { errno = EINVAL; return -1; }
        xSemaphoreTake(s_lock, portMAX_DELAY);
        if (r->val) s->out_shadow |=  (uint16_t)(1u << r->line);
        else        s->out_shadow &= ~(uint16_t)(1u << r->line);
        int rc = sx1509_write_pair(s, SX1509_REG_DATA_A, s->out_shadow);
        xSemaphoreGive(s_lock);
        if (rc < 0) { errno = EIO; return -1; }
        return 0;
    }
    case GPIOCHIP_SET_PULL: {
        gpio_req_t *r = arg;
        if (r->line >= s->pins) { errno = EINVAL; return -1; }
        /* SX1509 supports pull-up only (no pull-down per pin); UP and UPDOWN
         * map to pull-up enabled, NONE/DOWN map to pull-up disabled. */
        xSemaphoreTake(s_lock, portMAX_DELAY);
        if (r->pull == GPIO_PULL_UP || r->pull == GPIO_PULL_UPDOWN)
            s->pullup_shadow |=  (uint16_t)(1u << r->line);
        else
            s->pullup_shadow &= ~(uint16_t)(1u << r->line);
        int rc = sx1509_write_pair(s, SX1509_REG_PULLUP_A, s->pullup_shadow);
        xSemaphoreGive(s_lock);
        if (rc < 0) { errno = EIO; return -1; }
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
    if (s_num_slots >= (int)SX1509_SLOT_MAX) return -1;  /* unreachable: pool == table */

    sx1509_slot_t *s = &s_slots[s_num_slots];
    snprintf(s->name_buf, sizeof(s->name_buf), "gpiochip%u", (unsigned)d->id);
    s->drv.name   = s->name_buf;
    s->drv.ioctl  = sx1509_ioctl;
    s->chip_id    = d->id;
    s->bus        = d->bus;
    s->i2c_addr   = d->addr;
    s->pins       = d->pins > 16 ? 16 : d->pins;

    if (sx1509_op_init(s) < 0) {
        klog_e(TAG, "%s: chip init failed (bus=%u addr=0x%02x)",
               s->name_buf, d->bus, d->addr);
        return -1;
    }
    if (duneos_dev_register(&s->drv) < 0) {
        klog_e(TAG, "%s: duneos_dev_register failed", s->name_buf);
        return -1;
    }
    klog_i(TAG, "%s: SX1509 i2c-%u@0x%02x, %u lines",
           s->name_buf, d->bus, d->addr, (unsigned)s->pins);
    s_num_slots++;
    return 0;
}
#endif /* DUNEOS_GPIOCHIP_LIST */

void drv_gpiochip_sx1509_register(void)
{
#ifdef DUNEOS_GPIOCHIP_LIST
    if (!s_lock) s_lock = xSemaphoreCreateMutex();
    if (!s_lock) { klog_e(TAG, "mutex alloc failed"); return; }

    /* Register every board-declared expander whose type is "sx1509". */
    for (size_t i = 0; i < SX1509_SLOT_MAX; i++) {
        if (strcmp(s_descs[i].type, "sx1509") == 0)
            register_instance(&s_descs[i]);
    }
#else
    klog_w(TAG, "SX1509 driver enabled but board declares no gpio_expanders");
#endif
}

DUNEOS_DRIVER_REGISTER(8, drv_gpiochip_sx1509_register);
