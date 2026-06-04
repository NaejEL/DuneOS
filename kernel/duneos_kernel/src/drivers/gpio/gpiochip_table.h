/*
 * gpiochip_table.h — board-declared I2C GPIO expander table.
 *
 * bspgen emits DUNEOS_GPIOCHIP_LIST(X) in board_config.h, one X(...) row per
 * `gpio_expanders:` entry in board.yaml. Each chip-type driver builds its
 * descriptor array from this list and registers only the rows whose `type`
 * matches. Adding an expander — or a whole I2C bus of them — is a board.yaml
 * edit: no driver change, no per-chip #ifdef, no fixed instance ceiling.
 *
 *   X(id, type, i2c_bus, i2c_addr, pins, direction)
 */
#ifndef DUNEOS_GPIOCHIP_TABLE_H
#define DUNEOS_GPIOCHIP_TABLE_H

#include <stdint.h>
#include "board_config.h"

typedef struct {
    uint8_t     id;          /* exposes /dev/gpiochip<id>             */
    const char *type;        /* "pcf8574", "sx1509", …               */
    uint8_t     bus;         /* I2C bus id (i2c_id in board.yaml)     */
    uint8_t     addr;        /* 7-bit I2C address                    */
    uint8_t     pins;
    uint8_t     direction;   /* GPIO_DIR_INPUT|OUTPUT — policy hint   */
} duneos_gpiochip_desc_t;

/* Row emitter passed to DUNEOS_GPIOCHIP_LIST(). */
#define DUNEOS_GPIOCHIP_ROW(id, type, bus, addr, pins, dir) \
    { (uint8_t)(id), (type), (uint8_t)(bus), (uint8_t)(addr), (uint8_t)(pins), (uint8_t)(dir) },

#endif /* DUNEOS_GPIOCHIP_TABLE_H */
