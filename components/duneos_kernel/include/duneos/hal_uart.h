#pragma once

/*
 * DuneOS Hardware Interface — UART
 *
 * Pure-C API: no esp_err_t, no proprietary types.
 * The ESP-IDF implementation lives in arch/xtensa_esp32s3/hal/hal_uart.c.
 *
 * Returns: 0 / pointer on success; -1 / NULL on failure (sets errno).
 */

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

typedef struct duneos_hal_uart duneos_hal_uart_t;

typedef struct {
    int      port;           /* UART port number (e.g. 0 for UART0)          */
    uint32_t baud_rate;
    int      tx_pin;         /* GPIO number; -1 = leave unchanged            */
    int      rx_pin;         /* GPIO number; -1 = leave unchanged            */
    size_t   rx_buf_size;    /* RX ring buffer bytes; 0 = use driver default */
    bool     use_as_console; /* Route fd 0/1/2 through this UART             */
} duneos_hal_uart_config_t;

duneos_hal_uart_t *duneos_hal_uart_init  (const duneos_hal_uart_config_t *cfg);
void               duneos_hal_uart_deinit(duneos_hal_uart_t *h);

/* Returns bytes transferred on success, -1 on error (errno set). */
int duneos_hal_uart_read (duneos_hal_uart_t *h, void       *buf, size_t len);
int duneos_hal_uart_write(duneos_hal_uart_t *h, const void *buf, size_t len);
