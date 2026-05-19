#include "duneos/dev_driver.h"
#include "duneos/hal_uart.h"
#include "duneos/klog.h"
#include "board_config.h"

#include <errno.h>

static duneos_hal_uart_t *s_uart0 = NULL;

static int uart_drv_init(void)
{
    duneos_hal_uart_config_t cfg = {
        .port           = 0,
        .baud_rate      = DUNEOS_UART0_BAUD,
        .tx_pin         = DUNEOS_UART0_TX_PIN,
        .rx_pin         = DUNEOS_UART0_RX_PIN,
        .rx_buf_size    = 0,   /* use driver default */
        .use_as_console = true,
    };
    s_uart0 = duneos_hal_uart_init(&cfg);
    return s_uart0 ? 0 : -1;
}

static ssize_t uart_read_cb(duneos_devfd_t *fd, void *buf, size_t len)
{
    (void)fd;
    return duneos_hal_uart_read(s_uart0, buf, len);
}

static ssize_t uart_write_cb(duneos_devfd_t *fd, const void *buf, size_t len)
{
    (void)fd;
    return duneos_hal_uart_write(s_uart0, buf, len);
}

static const duneos_dev_driver_t s_drv_uart0 = {
    .name  = "uart0",
    .init  = uart_drv_init,
    .read  = uart_read_cb,
    .write = uart_write_cb,
};

void drv_uart_register(void)
{
    duneos_dev_register(&s_drv_uart0);
}
