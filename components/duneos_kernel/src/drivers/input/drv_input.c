/*
 * DuneOS input event infrastructure — /dev/input/event0
 *
 * Generic layer: ring buffer, VFS device, blocking read().
 * Hardware backends (IOMatrix, GPIO buttons, encoder) call drv_input_push_event()
 * from their own source file compiled under CONFIG_DUNEOS_DRV_INPUT_*.
 */

#include "duneos/dev_driver.h"
#include "duneos/input_ioctl.h"
#include "duneos/klog.h"
#include "drv_input_priv.h"

#ifdef CONFIG_DUNEOS_DRV_INPUT_IOMATRIX
extern void kb_iomatrix_init(void);
#endif
#ifdef CONFIG_DUNEOS_DRV_INPUT_BTNGPIO
extern void btn_gpio_init(void);
#endif
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
    portEXIT_CRITICAL(&s_mux);

    return (ssize_t)(count * sizeof(input_event_t));
}

/* ----- driver init -------------------------------------------------------- */

static esp_err_t input_init(void)
{
    s_data_sem = xSemaphoreCreateBinary();
    if (!s_data_sem) return ESP_ERR_NO_MEM;
#ifdef CONFIG_DUNEOS_DRV_INPUT_IOMATRIX
    kb_iomatrix_init();
#endif
#ifdef CONFIG_DUNEOS_DRV_INPUT_BTNGPIO
    btn_gpio_init();
#endif
#ifdef CONFIG_DUNEOS_DRV_INPUT_ENCODER
    enc_quadrature_init();
#endif
    klog_i(TAG, "/dev/input/event0 ready");
    return ESP_OK;
}

static const duneos_dev_driver_t s_drv_input = {
    .name = "input/event0",
    .init = input_init,
    .read = input_read,
};

void drv_input_register(void)
{
    duneos_dev_register(&s_drv_input);
}
