#include "duneos/dev_driver.h"
#include "duneos/driver_init.h"
#include "duneos/hal_gpio.h"
#include "duneos/hal_time.h"
#include "duneos/gpio_ioctl.h"

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

#include <string.h>
#include <errno.h>
#include <stdint.h>
#include <fcntl.h>

/* ----- edge-event ring (Linux GPIO line-event style) --------------------- */
/*
 * A single global ring shared by all readers of /dev/gpiochip0, fed from the
 * GPIO ISR. read() drains it; arming/disarming a line is via GPIOCHIP_SET_IRQ.
 * The ISR keeps to ISR-safe calls only (level read + monotonic clock + ring
 * push + give-from-ISR).
 */
#define GPIO_EV_RING  512   /* deep enough to absorb select() latency at I2C edge rates */

static gpio_event_t      s_ring[GPIO_EV_RING];
static volatile int      s_head = 0;
static volatile int      s_tail = 0;
static portMUX_TYPE      s_mux  = portMUX_INITIALIZER_UNLOCKED;
static SemaphoreHandle_t s_ev_sem;

static const duneos_dev_driver_t s_drv_gpio;   /* fwd ref for the ISR's select notify */

static void gpio_edge_isr(void *arg)
{
    int line  = (int)(intptr_t)arg;
    int level = duneos_hal_gpio_get_level(line);

    portENTER_CRITICAL_ISR(&s_mux);
    int next = (s_head + 1) % GPIO_EV_RING;
    if (next != s_tail) {
        gpio_event_t *e = &s_ring[s_head];
        e->timestamp_us = (uint32_t)duneos_hal_monotonic_us();
        e->line  = (uint8_t)line;
        e->edge  = level ? GPIO_EDGE_RISING : GPIO_EDGE_FALLING;
        e->level = (uint8_t)(level > 0);
        e->_pad  = 0;
        s_head = next;
    }
    portEXIT_CRITICAL_ISR(&s_mux);

    BaseType_t hpw = pdFALSE;
    xSemaphoreGiveFromISR(s_ev_sem, &hpw);
    int sel_woken = 0;
    duneos_dev_select_notify_isr(&s_drv_gpio, &sel_woken);
    if (hpw == pdTRUE || sel_woken) portYIELD_FROM_ISR();
}

static bool gpio_readable(duneos_devfd_t *fd)
{
    (void)fd;
    return s_head != s_tail;
}

/* ----- VFS callbacks ----------------------------------------------------- */

static int gpio_init(void)
{
    s_ev_sem = xSemaphoreCreateBinary();
    return s_ev_sem ? 0 : -1;
}

static ssize_t gpio_read(duneos_devfd_t *fd, void *buf, size_t len)
{
    size_t n = len / sizeof(gpio_event_t);
    if (n == 0) { errno = EINVAL; return -1; }

    if (fd->oflags & O_NONBLOCK) {
        /* Non-blocking: report EAGAIN on an empty ring instead of waiting. Lets
         * a sniffer drain a burst in a tight loop (no select() active, so the
         * ISR stays minimal and drops fewer edges) and re-arm select() only
         * when the ring runs dry. */
        portENTER_CRITICAL(&s_mux);
        bool empty = (s_head == s_tail);
        portEXIT_CRITICAL(&s_mux);
        if (empty) { errno = EAGAIN; return -1; }
    } else {
        xSemaphoreTake(s_ev_sem, portMAX_DELAY);
    }

    size_t        count = 0;
    gpio_event_t *out   = (gpio_event_t *)buf;

    portENTER_CRITICAL(&s_mux);
    while (count < n && s_head != s_tail) {
        out[count++] = s_ring[s_tail];
        s_tail = (s_tail + 1) % GPIO_EV_RING;
    }
    bool more = (s_head != s_tail);
    portEXIT_CRITICAL(&s_mux);

    /* Re-signal if events remain so a one-event-per-read() blocking caller
     * can't strand the rest (lost-wakeup fix). Non-blocking readers loop on
     * EAGAIN instead, so they don't touch the semaphore. */
    if (more && !(fd->oflags & O_NONBLOCK)) xSemaphoreGive(s_ev_sem);

    return (ssize_t)(count * sizeof(gpio_event_t));
}

static int gpio_ioctl_cb(duneos_devfd_t *fd, int cmd, void *arg)
{
    (void)fd;
    switch (cmd) {
    case GPIOCHIP_GET_INFO: {
        gpiochip_info_t *info = arg;
        strncpy(info->name, "gpiochip0", sizeof(info->name) - 1);
        info->name[sizeof(info->name) - 1] = '\0';
        info->lines = (uint8_t)duneos_hal_gpio_count();
        return 0;
    }
    case GPIOCHIP_SET_DIR: {
        gpio_req_t *r = arg;
        return duneos_hal_gpio_set_dir(r->line, (duneos_gpio_dir_t)r->dir);
    }
    case GPIOCHIP_GET_VALUE: {
        gpio_req_t *r = arg;
        int v = duneos_hal_gpio_get_level(r->line);
        if (v < 0) return -1;
        r->val = (uint8_t)v;
        return 0;
    }
    case GPIOCHIP_SET_VALUE: {
        gpio_req_t *r = arg;
        return duneos_hal_gpio_set_level(r->line, r->val);
    }
    case GPIOCHIP_SET_PULL: {
        gpio_req_t *r = arg;
        return duneos_hal_gpio_set_pull(r->line, (duneos_gpio_pull_t)r->pull);
    }
    case GPIOCHIP_SET_IRQ: {
        gpio_irq_req_t *r = arg;
        duneos_gpio_intr_type_t t;
        switch (r->edge) {
        case GPIO_EDGE_RISING:  t = DUNEOS_GPIO_INTR_POSEDGE; break;
        case GPIO_EDGE_FALLING: t = DUNEOS_GPIO_INTR_NEGEDGE; break;
        case GPIO_EDGE_BOTH:    t = DUNEOS_GPIO_INTR_ANYEDGE; break;
        case GPIO_EDGE_NONE:    t = DUNEOS_GPIO_INTR_DISABLE; break;
        default: errno = EINVAL; return -1;
        }
        return duneos_hal_gpio_set_intr(r->line, t, gpio_edge_isr,
                                        (void *)(intptr_t)r->line);
    }
    default:
        errno = ENOTTY;
        return -1;
    }
}

static const duneos_dev_driver_t s_drv_gpio = {
    .name     = "gpiochip0",
    .init     = gpio_init,
    .read     = gpio_read,
    .ioctl    = gpio_ioctl_cb,
    .readable = gpio_readable,
};

void drv_gpio_register(void)
{
    duneos_dev_register(&s_drv_gpio);
}

DUNEOS_DRIVER_REGISTER(2, drv_gpio_register);
