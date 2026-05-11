#include "duneos/dev_driver.h"
#include "duneos/klog.h"
#include "board_config.h"

#include "driver/uart.h"
#include "driver/uart_vfs.h"

#include <errno.h>

static const char *TAG = "duneos/drv_uart";

#define UART_RX_BUF  256

static esp_err_t uart_drv_init(void)
{
    uart_config_t cfg = {
        .baud_rate  = DUNEOS_UART0_BAUD,
        .data_bits  = UART_DATA_8_BITS,
        .parity     = UART_PARITY_DISABLE,
        .stop_bits  = UART_STOP_BITS_1,
        .flow_ctrl  = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };

    esp_err_t err = uart_driver_install(UART_NUM_0, UART_RX_BUF, 0, 0, NULL, 0);
    if (err != ESP_OK) {
        klog_e(TAG, "uart_driver_install: %s", esp_err_to_name(err));
        return err;
    }
    err = uart_param_config(UART_NUM_0, &cfg);
    if (err != ESP_OK) return err;

    err = uart_set_pin(UART_NUM_0,
                       DUNEOS_UART0_TX_PIN, DUNEOS_UART0_RX_PIN,
                       UART_PIN_NO_CHANGE,  UART_PIN_NO_CHANGE);
    if (err != ESP_OK) return err;

    /* Route stdout/stderr through the UART driver so write(1, ...) works. */
    uart_vfs_dev_use_driver(UART_NUM_0);
    klog_i(TAG, "UART0 ready TX=%d RX=%d baud=%d",
           DUNEOS_UART0_TX_PIN, DUNEOS_UART0_RX_PIN, DUNEOS_UART0_BAUD);
    return ESP_OK;
}

static ssize_t uart_read_cb(duneos_devfd_t *fd, void *buf, size_t len)
{
    (void)fd;
    int n = uart_read_bytes(UART_NUM_0, buf, (uint32_t)len, portMAX_DELAY);
    return (n < 0) ? (errno = EIO, -1) : n;
}

static ssize_t uart_write_cb(duneos_devfd_t *fd, const void *buf, size_t len)
{
    (void)fd;
    int n = uart_write_bytes(UART_NUM_0, buf, len);
    return (n < 0) ? (errno = EIO, -1) : n;
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
