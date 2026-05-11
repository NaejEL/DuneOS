#pragma once

#include "esp_err.h"
#include "duneos/supervisor.h"

/*
 * DuneOS init system — reads /sd/init.json and produces a list of services
 * for the supervisor to launch at boot.
 *
 * init.json format:
 *   {
 *     "version": 1,
 *     "services": [
 *       { "path": "/sd/apps/shell.dap", "restart": "no" },
 *       { "path": "/sd/apps/wifi.dap",  "restart": "always" }
 *     ]
 *   }
 *
 * "restart" values: "no" (default), "always", "on-failure"
 */

#define DUNEOS_INIT_PATH    "/sd/init.json"
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
 * Parse /sd/init.json and populate cfg.
 * Returns ESP_OK on success.
 * Returns ESP_ERR_NOT_FOUND if the file does not exist (caller should fall
 * back to the legacy scan/autoboot path).
 * Returns ESP_FAIL on parse errors (caller may still fall back).
 */
esp_err_t duneos_init_load(duneos_init_config_t *cfg);
