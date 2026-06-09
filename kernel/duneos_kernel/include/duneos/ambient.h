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
#define AMBIENT_WIFI_PATH     "/tmp/state/wifi"   /* ambient_wifi_t */
#define AMBIENT_KBD_PATH      "/tmp/state/kbd"    /* ambient_kbd_t  */

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
    char    ssid[32];    /* NUL-terminated; empty when down */
} ambient_wifi_t;

/* Modifier-lock bitmask (ADR 027 item 3 — kb_iomatrix sticky Fn/Shift/Opt). */
enum {
    AMBIENT_MOD_FN    = 1u << 0,
    AMBIENT_MOD_SHIFT = 1u << 1,
    AMBIENT_MOD_OPT   = 1u << 2,
};

typedef struct {
    uint8_t locks;       /* OR of AMBIENT_MOD_* — which modifiers are latched */
} ambient_kbd_t;
