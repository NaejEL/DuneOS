#pragma once

/*
 * DuneOS driver auto-registration (Phase 24.9, ADR 015 Pattern 1).
 *
 * Each kernel driver drops one DUNEOS_DRIVER_REGISTER(prio, init_fn) at
 * file scope. The macro expands to a GCC `constructor` function that
 * runs BEFORE app_main(), recording (prio, init_fn) into a static list.
 * At VFS mount time, duneos_drivers_run_init() sorts the list by
 * priority and calls each init function.
 *
 * No more #ifdef chain in vfs_dev.c, no central registry to edit when
 * adding a driver — Kconfig + CMakeLists gates compilation; an unused
 * driver contributes nothing to the list.
 *
 * Why constructors rather than an ELF section?
 *   - Pure standard C / GCC — no linker fragment, no ESP-IDF-specific
 *     section placement work. Portable to RP2040, STM32, etc.
 *   - ESP-IDF itself relies on constructors for esp_libc init —
 *     well-supported, well-tested.
 *   - Run-time priority sort handles ordering: the constructor exec
 *     order is unspecified, but we re-sort the list before calling
 *     anyone, so it doesn't matter.
 *
 * Priority levels (lower = runs earlier):
 *   1 = bus controllers (i2c, spi) — others depend on them
 *   2 = native peripherals (gpio chip)
 *   5 = standard drivers (null, uart, klog, fb, input, …)
 *   8 = bus consumers (gpio expanders, i2c sensors, …) — need bus init
 *
 * USB MSC + USB CDC remain manually wired in vfs_dev.c — they have a
 * different init phase (drv_usb_preinit runs BEFORE the VFS mounts,
 * from vfs.c). The macro is intentionally not used by them.
 */

#include <stddef.h>

typedef void (*duneos_driver_init_fn)(void);

/* Internal — append (prio, init, name) to the registry. Called by the
 * constructor that the macro generates. Not for direct use. */
void _duneos_driver_record(int prio, duneos_driver_init_fn init, const char *name);

/* Walk the registry in priority order, call each init. Called once from
 * duneos_vfs_mount_dev() during VFS mount. */
void duneos_drivers_run_init(void);

#define _DUNEOS_DRV_CAT2(a, b) a##b
#define _DUNEOS_DRV_CAT(a, b)  _DUNEOS_DRV_CAT2(a, b)

/*
 * Register `init_fn` to run at VFS mount time with the given priority.
 * Expands to a constructor that records the entry. The constructor's
 * exec priority is 101 — above ESP-IDF's default (100) so the kernel
 * has finished its own bring-up first, and we record AFTER esp_libc
 * is ready (klog, malloc available if a later step needed them).
 *
 * The macro is a statement at file scope. Use it AFTER the function
 * definition (or with a forward declaration) so init_fn is in scope.
 */
#define DUNEOS_DRIVER_REGISTER(prio_, init_fn) \
    static __attribute__((constructor(101))) \
    void _DUNEOS_DRV_CAT(_drv_ctor_, init_fn)(void) { \
        _duneos_driver_record((prio_), (init_fn), #init_fn); \
    }
