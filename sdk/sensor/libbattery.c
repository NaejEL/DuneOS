/*
 * libbattery — chip-agnostic dispatch layer.
 *
 * Every call forwards to `duneos_battery_ops`, the vtable defined by
 * whichever chip library is linked alongside this file (libbq27220.c,
 * libmax17043.c, libip5306.c, …).  Adding a new gauge chip requires
 * zero edits here — just ship sdk/sensor/lib<chip>.c that exports
 * `const battery_backend_ops_t duneos_battery_ops`, declare it in
 * board.yaml as `battery: {type: <chip>, ...}`, and the capability
 * resolver pulls the right source via `capabilities: [battery]`.
 *
 * Mirrors libdisp.c (ADR 014 + ADR 015 Pattern 2).
 */

#include "duneos/battery.h"

battery_handle_t *battery_open(void)
{
    return duneos_battery_ops.open();
}

int battery_read(battery_handle_t *h, battery_info_t *info)
{
    return duneos_battery_ops.read(h, info);
}

void battery_close(battery_handle_t *h)
{
    duneos_battery_ops.close(h);
}

/*
 * Default: nothing to configure. A chip backend that does have configuration
 * to keep in step with board.yaml defines this strongly and overrides us.
 *
 * Weak rather than a vtable entry, and weak rather than an obligation: most
 * gauges have nothing to provision, and a new backend should not have to write
 * a no-op to link. It is also deliberately outside battery_backend_ops_t —
 * that table is what every app holding the `battery` capability calls through,
 * and this function may write the chip's configuration.
 */
__attribute__((weak))
int duneos_battery_provision(battery_handle_t *h)
{
    (void)h;
    return 0;
}
