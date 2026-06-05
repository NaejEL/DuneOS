/*
 * DuneOS input event infrastructure — /dev/input/event0
 *
 * Generic layer: ring buffer, VFS device, blocking read().
 * Hardware backends (IOMatrix, GPIO buttons, encoder) call drv_input_push_event()
 * from their own source file compiled under CONFIG_DUNEOS_DRV_INPUT_*.
 */

#include "duneos/dev_driver.h"
#include "duneos/driver_init.h"
#include "duneos/input_ioctl.h"
#include "duneos/klog.h"
#include "drv_input_priv.h"

#ifdef CONFIG_DUNEOS_DRV_INPUT_ENCODER
extern void enc_quadrature_init(void);
#endif

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

#include <string.h>
#include <errno.h>

static const char *TAG = "duneos/input";

/* ----- ring buffer -------------------------------------------------------- */

#define INPUT_RING_SIZE  64

static input_event_t     s_ring[INPUT_RING_SIZE];
static volatile int      s_head = 0;
static volatile int      s_tail = 0;
static portMUX_TYPE      s_mux  = portMUX_INITIALIZER_UNLOCKED;
static SemaphoreHandle_t s_data_sem;

void drv_input_push_event(const input_event_t *ev)
{
    portENTER_CRITICAL(&s_mux);
    bool was_empty = (s_head == s_tail);
    int next = (s_head + 1) % INPUT_RING_SIZE;
    if (next != s_tail) {
        s_ring[s_head] = *ev;
        s_head = next;
    }
    portEXIT_CRITICAL(&s_mux);

    if (was_empty)
        xSemaphoreGive(s_data_sem);
}

/* ----- VFS callbacks ------------------------------------------------------ */

static ssize_t input_read(duneos_devfd_t *fd, void *buf, size_t len)
{
    (void)fd;
    size_t n = len / sizeof(input_event_t);
    if (n == 0) { errno = EINVAL; return -1; }

    xSemaphoreTake(s_data_sem, portMAX_DELAY);

    size_t count = 0;
    input_event_t *out = (input_event_t *)buf;

    portENTER_CRITICAL(&s_mux);
    while (count < n && s_head != s_tail) {
        out[count++] = s_ring[s_tail];
        s_tail = (s_tail + 1) % INPUT_RING_SIZE;
    }
    bool more = (s_head != s_tail);
    portEXIT_CRITICAL(&s_mux);

    /* push_event only gives the semaphore on an empty→non-empty transition.
     * A reader that drains fewer events than are queued (e.g. one event per
     * read(), the common case) would otherwise leave the rest stranded with
     * the semaphore at 0 — a lost wakeup that hangs the next read until a new
     * empty→non-empty edge. Re-signal so leftover events stay readable. */
    if (more) xSemaphoreGive(s_data_sem);

    return (ssize_t)(count * sizeof(input_event_t));
}

/* ----- ioctl: userspace event injection ---------------------------------- */
/* Polling-based input sources (keyboard matrix scanners, GPIO buttons) live in
 * userspace daemons since 24-debt #5. They open /dev/input/event0 and call
 * ioctl(INPUT_INJECT_EVENT, &ev) to feed the same ring buffer. Encoder stays
 * kernel-side (ISR path) and calls drv_input_push_event() directly. */
static int input_ioctl(duneos_devfd_t *fd, int cmd, void *arg)
{
    (void)fd;
    switch (cmd) {
    case INPUT_INJECT_EVENT: {
        if (!arg) { errno = EINVAL; return -1; }
        drv_input_push_event((const input_event_t *)arg);
        return 0;
    }
    default:
        errno = ENOTTY;
        return -1;
    }
}

/* ----- driver init -------------------------------------------------------- */

static int input_init(void)
{
    s_data_sem = xSemaphoreCreateBinary();
    if (!s_data_sem) return -1;
#ifdef CONFIG_DUNEOS_DRV_INPUT_ENCODER
    enc_quadrature_init();
#endif
    klog_i(TAG, "/dev/input/event0 ready");
    return 0;
}

static const duneos_dev_driver_t s_drv_input = {
    .name  = "input/event0",
    .init  = input_init,
    .read  = input_read,
    .ioctl = input_ioctl,
};

void drv_input_register(void)
{
    duneos_dev_register(&s_drv_input);
}

DUNEOS_DRIVER_REGISTER(5, drv_input_register);
