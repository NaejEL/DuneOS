/*
 * libcharger — chip-agnostic dispatch layer for the battery charger.
 *
 * Every call forwards to `duneos_charger_ops`, the vtable defined by whichever
 * chip library is linked alongside this file (libbq25896.c, …). Adding a new
 * charger chip requires zero edits here: ship sdk/sensor/lib<chip>.c exporting
 * `const charger_backend_ops_t duneos_charger_ops`, declare it in board.yaml
 * as `charger: {type: <chip>, ...}`, and the capability resolver pulls the
 * right source for `capabilities: [charger]`.
 *
 * Mirrors libbattery.c (ADR 014 + ADR 015 Pattern 2).
 */

#include "duneos/charger.h"

charger_handle_t *charger_open(void)
{
    return duneos_charger_ops.open();
}

int charger_read(charger_handle_t *h, charger_info_t *info)
{
    return duneos_charger_ops.read(h, info);
}

void charger_close(charger_handle_t *h)
{
    duneos_charger_ops.close(h);
}

const char *charger_state_name(charger_state_t s)
{
    switch (s) {
    case CHARGER_NOT_CHARGING: return "not charging";
    case CHARGER_PRECHARGE:    return "pre-charge";
    case CHARGER_CHARGING:     return "charging";
    case CHARGER_DONE:         return "charged";
    case CHARGER_UNKNOWN:
    default:                   return "unknown";
    }
}

/*
 * Default: nothing to configure. A chip backend with a profile to apply
 * defines this strongly and overrides us.
 *
 * Weak rather than an obligation, for the same reason as
 * duneos_battery_provision(): a new backend should not have to write a no-op
 * to link. Kept out of charger_backend_ops_t on purpose — that table is what
 * every app with the `charger` capability calls through, and this function may
 * write the chip's configuration.
 */
__attribute__((weak))
int duneos_charger_configure(charger_handle_t *h)
{
    (void)h;
    return 0;
}
