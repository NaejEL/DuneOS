/*
 * UART HAL backend — ESP-IDF implementation.
 * Bridges duneos/hal_uart.h to driver/uart.h.
 * ESP-IDF types stay completely inside this translation unit.
 */
#include "duneos/hal_uart.h"
#include "duneos/klog.h"

#include "driver/uart.h"
#include "driver/uart_vfs.h"
#include "esp_err.h"

#include <stdlib.h>
#include <errno.h>

static const char *TAG = "duneos/hal_uart";

#define DEFAULT_RX_BUF_SIZE 256

struct duneos_hal_uart {
    uart_port_t port;
};

duneos_hal_uart_t *duneos_hal_uart_init(const duneos_hal_uart_config_t *cfg)
{
    uart_config_t uart_cfg = {
        .baud_rate  = (int)cfg->baud_rate,
        .data_bits  = UART_DATA_8_BITS,
        .parity     = UART_PARITY_DISABLE,
        .stop_bits  = UART_STOP_BITS_1,
        .flow_ctrl  = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };

    size_t rx_buf = cfg->rx_buf_size ? cfg->rx_buf_size : DEFAULT_RX_BUF_SIZE;

    esp_err_t err = uart_driver_install((uart_port_t)cfg->port,
                                        (int)rx_buf, 0, 0, NULL, 0);
    if (err != ESP_OK) {
        klog_e(TAG, "uart_driver_install port=%d: %s",
               cfg->port, esp_err_to_name(err));
        return NULL;
    }

    err = uart_param_config((uart_port_t)cfg->port, &uart_cfg);
    if (err != ESP_OK) {
        uart_driver_delete((uart_port_t)cfg->port);
        return NULL;
    }

    err = uart_set_pin((uart_port_t)cfg->port,
                       cfg->tx_pin, cfg->rx_pin,
                       UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
    if (err != ESP_OK) {
        uart_driver_delete((uart_port_t)cfg->port);
        return NULL;
    }

    /* Route VFS stdin/stdout/stderr through this UART when requested. */
    if (cfg->use_as_console)
        uart_vfs_dev_use_driver((uart_port_t)cfg->port);

    duneos_hal_uart_t *h = malloc(sizeof(*h));
    if (!h) {
        uart_driver_delete((uart_port_t)cfg->port);
        return NULL;
    }
    h->port = (uart_port_t)cfg->port;

    klog_i(TAG, "UART%d ready TX=%d RX=%d baud=%lu",
           cfg->port, cfg->tx_pin, cfg->rx_pin, (unsigned long)cfg->baud_rate);
    return h;
}

void duneos_hal_uart_deinit(duneos_hal_uart_t *h)
{
    if (!h) return;
    uart_driver_delete(h->port);
    free(h);
}

int duneos_hal_uart_read(duneos_hal_uart_t *h, void *buf, size_t len)
{
    /* 100 ms timeout: returns 0 bytes (not -1) when RX is idle.
     * Allows read_line's idle-reprint timer and SHELL_FLUSH_ON_START to work.
     * portMAX_DELAY blocks forever — unsafe when garbage bytes can arrive
     * (e.g. floating RX pin on headless boards) and trigger spurious commands. */
    int n = uart_read_bytes(h->port, buf, (uint32_t)len, pdMS_TO_TICKS(100));
    if (n < 0) { errno = EIO; return -1; }
    return n;
}

int duneos_hal_uart_write(duneos_hal_uart_t *h, const void *buf, size_t len)
{
    int n = uart_write_bytes(h->port, buf, len);
    if (n < 0) { errno = EIO; return -1; }
    return n;
}
