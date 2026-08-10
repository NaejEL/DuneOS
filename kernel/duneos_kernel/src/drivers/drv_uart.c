#include "duneos/dev_driver.h"
#include "duneos/driver_init.h"
#include "duneos/hal_uart.h"
#include "duneos/klog.h"
#include "board_config.h"

#include <errno.h>

/* One instance per board UART. The dev_driver_t is the first member so the
 * shared read/write callbacks can recover the instance from fd->drv without
 * per-port callback duplication. */
typedef struct {
    duneos_dev_driver_t      drv;
    duneos_hal_uart_t       *h;
    duneos_hal_uart_config_t cfg;
} uart_dev_t;

static ssize_t uart_read_cb(duneos_devfd_t *fd, void *buf, size_t len)
{
    uart_dev_t *d = (uart_dev_t *)fd->drv;
    return duneos_hal_uart_read(d->h, buf, len);
}

static ssize_t uart_write_cb(duneos_devfd_t *fd, const void *buf, size_t len)
{
    uart_dev_t *d = (uart_dev_t *)fd->drv;
    return duneos_hal_uart_write(d->h, buf, len);
}

static int uart_dev_init(uart_dev_t *d)
{
    d->h = duneos_hal_uart_init(&d->cfg);
    return d->h ? 0 : -1;
}

static int uart0_init(void);
static uart_dev_t s_uart0 = {
    .drv = {
        .name  = "uart0",
        .init  = uart0_init,
        .read  = uart_read_cb,
        .write = uart_write_cb,
    },
    .cfg = {
        .port           = 0,
        .baud_rate      = DUNEOS_UART0_BAUD,
        .tx_pin         = DUNEOS_UART0_TX_PIN,
        .rx_pin         = DUNEOS_UART0_RX_PIN,
        .rx_buf_size    = 0,   /* use driver default */
        .use_as_console = true,
    },
};
static int uart0_init(void) { return uart_dev_init(&s_uart0); }

#ifdef DUNEOS_UART1_TX_PIN
/* UART1 is a plain data port (e.g. an external sensor on the CardPuter Grove
 * connector) — never a console candidate, so no stdio byte may ever leak to
 * the wire. */
static int uart1_init(void);
static uart_dev_t s_uart1 = {
    .drv = {
        .name  = "uart1",
        .init  = uart1_init,
        .read  = uart_read_cb,
        .write = uart_write_cb,
    },
    .cfg = {
        .port           = 1,
        .baud_rate      = DUNEOS_UART1_BAUD,
        .tx_pin         = DUNEOS_UART1_TX_PIN,
        .rx_pin         = DUNEOS_UART1_RX_PIN,
        .rx_buf_size    = 0,
        .use_as_console = false,
    },
};
static int uart1_init(void) { return uart_dev_init(&s_uart1); }
#endif

void drv_uart_register(void)
{
    duneos_dev_register(&s_uart0.drv);
#ifdef DUNEOS_UART1_TX_PIN
    duneos_dev_register(&s_uart1.drv);
#endif
}

DUNEOS_DRIVER_REGISTER(5, drv_uart_register);
