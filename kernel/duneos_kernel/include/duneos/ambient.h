#pragma once

/*
 * Ambient system state published to tmpfs — ADR 027.
 *
 * Producers (one per signal) write the whole struct in a single write(); readers
 * read it in a single read(). A missing file means "feature off" — a reader that
 * can't open the path renders that slot as absent. State here is ephemeral and
 * non-persistent by construction (tmpfs, ADR 005); anything that must survive a
 * reboot is config and lives under /flash/etc, not here.
 *
 * Format is a small fixed binary struct, not text: a single write() of a struct
 * this size is effectively atomic on tmpfs, so readers never need locking and a
 * torn read can't happen. This matches the existing battery snapshot, which is
 * grandfathered at /tmp/battery as a battery_info_t (<duneos/battery_ioctl.h>) —
 * the status bar reads it directly.
 *
 * Consumers: sdk/ui/ui_statusbar.c (ui_statusbar_top). Producers: the wifi
 * config app (AMBIENT_WIFI_PATH), kb_iomatrix (AMBIENT_KBD_PATH).
 */

#include <stdint.h>

#define AMBIENT_BATTERY_PATH  "/tmp/battery"      /* battery_info_t (grandfathered) */
#define AMBIENT_WIFI_PATH     "/tmp/state/wifi"   /* ambient_wifi_t  */
#define AMBIENT_KBD_PATH      "/tmp/state/kbd"    /* ambient_kbd_t   */
#define AMBIENT_STACK_PATH    "/tmp/state/stack"  /* ambient_stack_t */

/* The /tmp/state directory producers create before their first write. */
#define AMBIENT_STATE_DIR     "/tmp/state"

enum {
    AMBIENT_WIFI_DOWN       = 0,
    AMBIENT_WIFI_CONNECTING = 1,
    AMBIENT_WIFI_UP         = 2,
};

typedef struct {
    uint8_t state;       /* AMBIENT_WIFI_* */
    int8_t  rssi_dbm;    /* e.g. -78; meaningful only when state == AMBIENT_WIFI_UP */
    char    ssid[33];    /* NUL-terminated (full 32-char SSID); empty when down */
} ambient_wifi_t;

/* Modifier-latch bitmask (ADR 027 item 3 — kb_iomatrix sticky Fn/Shift/Ctrl/Alt). */
enum {
    AMBIENT_MOD_FN    = 1u << 0,
    AMBIENT_MOD_SHIFT = 1u << 1,
    AMBIENT_MOD_CTRL  = 1u << 2,
    AMBIENT_MOD_ALT   = 1u << 3,
};

typedef struct {
    uint8_t locks;       /* OR of AMBIENT_MOD_* — which modifiers are latched   */
    uint8_t oneshot;     /* subset of `locks` armed in one-shot mode (own colour)*/
} ambient_kbd_t;

/*
 * Boot-time stack watermark of main_task (LEG-37). Written once by the kernel,
 * right after the init.yaml launch — the deepest point of boot — and never
 * updated afterwards, so a reader gets the boot peak and not a live figure.
 * Producer: main/main.c. Consumer: apps/system/bin/free.
 */
typedef struct {
    uint32_t total;      /* main_task stack size, bytes                        */
    uint32_t peak;       /* high-water mark at the end of boot, bytes used     */
} ambient_stack_t;
