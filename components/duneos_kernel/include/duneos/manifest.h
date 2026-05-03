#pragma once

#include "esp_err.h"
#include "duneos/abi.h"

/*
 * Read and parse /sd/apps/manifest.json.
 *
 * The manifest is the kernel's entry point into the app ecosystem.
 * It declares which app to auto-boot (or lists apps for a menu).
 *
 * Expected format:
 *   {
 *     "autoboot": "hello_world",
 *     "apps": [
 *       {
 *         "name": "hello_world",
 *         "path": "/sd/apps/hello_world.elf",
 *         "version": "0.1.0",
 *         "required_abi_version": 1,
 *         "permissions": ["fs_read", "uart"]
 *       }
 *     ]
 *   }
 */

#define DUNEOS_MANIFEST_PATH    "/sd/apps/manifest.json"
#define DUNEOS_MANIFEST_MAX_APPS 16

typedef struct {
    char path[128];
    duneos_app_manifest_t meta;
} duneos_manifest_entry_t;

typedef struct {
    char                   autoboot[DUNEOS_APP_NAME_MAX];  /* empty = show menu */
    duneos_manifest_entry_t apps[DUNEOS_MANIFEST_MAX_APPS];
    int                    app_count;
} duneos_manifest_t;

/*
 * Parse DUNEOS_MANIFEST_PATH into *out.
 * Caller owns *out (stack or heap — no internal allocation).
 */
esp_err_t duneos_manifest_read(duneos_manifest_t *out);

/*
 * Find the entry for a given app name, or NULL if not listed.
 */
const duneos_manifest_entry_t *duneos_manifest_find(
    const duneos_manifest_t *m, const char *name);
