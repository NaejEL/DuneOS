/*
 * libbattery — chip-agnostic battery API for DuneOS apps.
 *
 *   battery_handle_t *h = battery_open();
 *   battery_info_t info;
 *   battery_read(h, &info);
 *   battery_close(h);
 *
 * The active chip backend is selected at build time by the capability
 * resolver (capabilities.py) from `board.yaml battery.type`. Today:
 *   bq27220   → sdk/sensor/libbq27220.c
 *   max17043  → sdk/sensor/libmax17043.c   (future)
 *   ip5306    → sdk/sensor/libip5306.c     (future)
 *
 * Each chip lib exports `const battery_backend_ops_t duneos_battery_ops`
 * and reads the I2C device path / chip address from <duneos/board.h>
 * (emitted by boardgen.py). Apps therefore never name the chip — they
 * just `#include <duneos/battery.h>` and link against this lib plus
 * the chip backend pulled in by `capabilities: [battery]`.
 *
 * ADR 009 boundary: this is userspace because every gauge speaks a
 * register-level protocol over I2C; only one client (battery_daemon)
 * needs the device at a time. ADC voltage-divider batteries stay in
 * the kernel (/dev/battery0 via drv_battery_adc_simple.c) because the
 * ADC unit is a shared peripheral.
 */

#pragma once

#include "duneos/battery_ioctl.h"

typedef struct battery_handle battery_handle_t;

typedef struct {
    /* Probe + initialise the gauge. Reads pin/addr config from board.h.
     * Returns an opaque handle, or NULL on failure. */
    battery_handle_t *(*open)(void);

    /* Sample voltage / SOC / status. Returns 0, or -errno on bus error. */
    int (*read)(battery_handle_t *h, battery_info_t *info);

    /* Tear down. */
    void (*close)(battery_handle_t *h);
} battery_backend_ops_t;

/* The single symbol that every chip backend (lib<chip>.c) exports.
 * libbattery.c forwards every public call through this table. */
extern const battery_backend_ops_t duneos_battery_ops;

/* Convenience wrappers — identical pattern to libdisp.h. */
battery_handle_t *battery_open(void);
int               battery_read(battery_handle_t *h, battery_info_t *info);
void              battery_close(battery_handle_t *h);
