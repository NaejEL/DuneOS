#pragma once

/*
 * DuneOS SDK — libcharger: the battery charger, as opposed to the gauge.
 *
 * Two chips, two jobs, and the difference matters. The gauge measures what the
 * cell holds; the charger decides when to stop filling it. Ask the gauge "is
 * it full?" and you get its opinion, derived from a current taper it may never
 * have witnessed. Ask the charger and you get the fact.
 *
 * That is not academic. On the t-embed the charger terminated at 256 mA while
 * the gauge's taper threshold was 100 mA, so the charge current fell from 256
 * straight to zero without ever crossing the gauge's threshold: the gauge's FC
 * flag never set, and the indicator sat at 96% forever on a battery the
 * charger considered finished. CHRG_STAT said "termination done" throughout.
 *
 * Mirrors <duneos/battery.h>: one chip-agnostic API, one vtable per chip
 * (lib<chip>.c exporting duneos_charger_ops), selected by the board's
 * `charger.type` through capability resolution. Apps declare
 * `capabilities: [charger]` and never name a chip.
 */

#include <stdbool.h>
#include <stdint.h>

typedef struct charger_handle charger_handle_t;

/* What the charger is doing. Deliberately not a bitmask: these are mutually
 * exclusive phases, and the point is to replace a guess with one answer. */
typedef enum {
    CHARGER_UNKNOWN = 0,
    CHARGER_NOT_CHARGING,     /* idle, whether or not input is present      */
    CHARGER_PRECHARGE,        /* cell below the fast-charge threshold       */
    CHARGER_CHARGING,         /* constant current, or constant voltage      */
    CHARGER_DONE,             /* termination reached — as full as this
                               * charger's settings will make it            */
} charger_state_t;

typedef struct {
    charger_state_t state;
    bool     input_power_good;  /* a usable source is attached              */
    bool     charge_enabled;    /* the charger is allowed to charge         */
    bool     otg_enabled;       /* boost mode: supplying 5 V, not charging  */
    uint16_t target_mv;         /* the voltage it charges TO                */
    uint16_t recharge_mv;       /* falls below this and a new cycle starts  */
    uint16_t charge_current_ma; /* fast-charge current limit                */
    uint16_t term_current_ma;   /* stops when the current falls below this  */
} charger_info_t;

typedef struct {
    /* Probe the charger. Reads bus + address from <duneos/board.h>.
     * Returns a handle, or NULL on failure. */
    charger_handle_t *(*open)(void);

    /* Sample the state above. Returns 0, or -errno on bus error. */
    int (*read)(charger_handle_t *h, charger_info_t *info);

    /* Tear down. */
    void (*close)(charger_handle_t *h);
} charger_backend_ops_t;

/* The single symbol every chip backend (lib<chip>.c) exports. */
extern const charger_backend_ops_t duneos_charger_ops;

charger_handle_t *charger_open(void);
int               charger_read(charger_handle_t *h, charger_info_t *info);
void              charger_close(charger_handle_t *h);

/* Human-readable `state`, for logs and diagnostics. */
const char       *charger_state_name(charger_state_t s);

/*
 * Apply the board's declared charge profile to the chip, and say whether
 * anything changed:
 *    0  nothing to do — already matching, or the board declares no profile
 *    1  the charger was reconfigured
 *  <0   -errno
 *
 * Call it from the battery daemon at startup, or from a tool whose declared
 * purpose is to reconfigure the chip (apps/user/bqprov). Never from something
 * that only reads — the same reasoning as duneos_battery_provision(), and the
 * same reason it is not in the ops vtable above: that table is what every app
 * holding the `charger` capability reaches through, and a diagnostic must not
 * reprogram hardware as a side effect of being run.
 *
 * Why it exists: a charger left on its power-on defaults fills whatever cell
 * happens to be attached with whatever current the silicon guessed, and stops
 * at whatever taper the silicon guessed. Those are properties of the board.
 */
int               duneos_charger_configure(charger_handle_t *h);
