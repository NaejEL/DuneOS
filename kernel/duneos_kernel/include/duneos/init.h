#pragma once

#include "duneos/supervisor.h"

/*
 * DuneOS init system — reads init.yaml (flash first, then SD) and produces a
 * list of services for the supervisor to launch at boot.
 *
 * init.yaml format:
 *   services:
 *     - path: /flash/bin/usb_shell.dap
 *       restart: always
 *     - path: /sd/apps/wifi.dap
 *       restart: on-failure
 *
 * "restart" values: "no" (default), "always", "on-failure"
 */

#define DUNEOS_INIT_PATH_FLASH       "/flash/init.yaml"
#define DUNEOS_INIT_PATH_FLASH_SAFE  "/flash/init.yaml.safe"
#define DUNEOS_INIT_PATH_SD          "/sd/init.yaml"
#define DUNEOS_INIT_PATH             DUNEOS_INIT_PATH_SD  /* backward compat alias */
#define DUNEOS_MAX_SERVICES 8

typedef struct {
    char                    path[DUNEOS_PATH_MAX];
    duneos_restart_policy_t restart;
} duneos_service_desc_t;

typedef struct {
    duneos_service_desc_t services[DUNEOS_MAX_SERVICES];
    int                   count;
} duneos_init_config_t;

/*
 * Parse init.yaml (flash then SD, merged with name-based dedup) and populate
 * cfg.
 *
 * Returns 0 on success (cfg->count > 0).
 * Returns -EINVAL if cfg is NULL.
 * Returns -ENOMEM if the parse buffer cannot be allocated.
 * Returns -ENOENT if neither init file exists or both are empty/unparseable —
 *         caller should fall back to the legacy scan/autoboot path.
 *
 * Per ADR 001 (docs/adr/001-error-model.md): kernel public APIs return
 * int with POSIX -errno semantics. No esp_err_t in this header.
 */
int duneos_init_load(duneos_init_config_t *cfg);
