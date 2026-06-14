#pragma once

#include "duneos/supervisor.h"

/*
 * DuneOS init system — reads init.yaml (flash first, then SD) and produces a
 * list of services for the supervisor to launch at boot.
 *
 * init.yaml format:
 *   services:
 *     - path: /bin/usb_shell.dap
 *       restart: always
 *     - path: /bin/splash.dap
 *       restart: no
 *     - path: /bin/g_shell.dap
 *       restart: always
 *       after: splash         # don't start until splash has exited
 *     - path: /sd/apps/wifi.dap
 *       restart: on-failure
 *
 * Fields:
 *   path     : full path to the .dap file
 *   restart  : "no" (default) | "always" | "on-failure"
 *   after    : app-name of another service to wait for. The service is held
 *              back at boot and launched only after the named predecessor
 *              exits (any code). If `after:` references a service not in
 *              init.yaml, or one with `restart: always` (never exits),
 *              the dependency is logged and the service starts at boot.
 *              No cycles, no chains-of-chains optimisation — a tiny init,
 *              not systemd.
 */

#define DUNEOS_INIT_PATH_FLASH       "/init.yaml"
#define DUNEOS_INIT_PATH_FLASH_SAFE  "/init.yaml.safe"
#define DUNEOS_INIT_PATH_SD          "/sd/init.yaml"
#define DUNEOS_INIT_PATH             DUNEOS_INIT_PATH_SD  /* backward compat alias */
#define DUNEOS_MAX_SERVICES 8

typedef struct {
    char                    path[DUNEOS_PATH_MAX];
    duneos_restart_policy_t restart;
    char                    after[DUNEOS_APP_NAME_MAX];  /* empty = no dep */
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

/*
 * Launch every service in `cfg` honouring the `after:` dependency.
 *
 * Behaviour:
 *   - Services with empty `after` are launched immediately.
 *   - Services with `after: X` are deferred; when X exits (any code),
 *     they are launched.
 *   - If X is not in `cfg` (or has restart: always so never exits),
 *     the dependency is logged and the service is launched at boot.
 *
 * Returns the number of services scheduled (including deferred ones).
 * Returns 0 if `cfg` is empty.
 */
int duneos_init_run(const duneos_init_config_t *cfg);
