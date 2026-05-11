#pragma once

#include <stddef.h>
#include "esp_err.h"
#include "duneos/abi.h"

/*
 * DuneOS app supervisor.
 *
 * The supervisor manages the lifecycle of running apps:
 *   1. duneos_supervisor_init()    — create supervisor task and queues
 *   2. duneos_supervisor_launch()  — load + start an app as a FreeRTOS task
 *   3. duneos_supervisor_wait_all() — block until all apps have exited
 *
 * duneos_exit() (in symbols.c) calls duneos_supervisor_app_exited() before
 * deleting the app task. The supervisor task then unloads the ELF sections.
 *
 * App-to-app messaging is a simple per-app mailbox queue:
 *   duneos_send(dest, data, len) — non-blocking; drops if mailbox full
 *   duneos_recv(msg, timeout_ms) — blocks up to timeout_ms
 *
 * All functions are safe to call from any task context after init.
 */

#define DUNEOS_MAX_RUNNING_APPS  4
#define DUNEOS_MAILBOX_DEPTH     8
#define DUNEOS_MSG_DATA_MAX      64

#define DUNEOS_APP_DEFAULT_STACK 8192   /* bytes — overridden by manifest stack_size */
#define DUNEOS_APP_TASK_PRIORITY 2      /* tskIDLE_PRIORITY + 2 */

typedef struct {
    char   data[DUNEOS_MSG_DATA_MAX];
    size_t len;
    char   sender[DUNEOS_APP_NAME_MAX];
} duneos_msg_t;

/*
 * Loader operations registered by duneos_loader at init time.
 * This breaks the circular dependency: duneos_kernel does not include
 * duneos/loader.h, but the supervisor still calls loader functions at runtime.
 */
typedef esp_err_t (*duneos_load_fn_t)(const char *path, duneos_app_t **out);
typedef esp_err_t (*duneos_run_fn_t)(duneos_app_t *app);
typedef void      (*duneos_unload_fn_t)(duneos_app_t *app);
typedef const duneos_app_manifest_t *(*duneos_get_manifest_fn_t)(const duneos_app_t *app);

typedef struct {
    duneos_load_fn_t         load;
    duneos_run_fn_t          run;
    duneos_unload_fn_t       unload;
    duneos_get_manifest_fn_t get_manifest;
} duneos_loader_ops_t;

/* Called by duneos_loader_init() before any supervisor_launch() */
void duneos_supervisor_register_loader(const duneos_loader_ops_t *ops);

/* Start the supervisor (call once from app_main before any launch) */
esp_err_t duneos_supervisor_init(void);

/* Load path and start it as a new FreeRTOS task. Non-blocking. */
esp_err_t duneos_supervisor_launch(const char *path);

/* Called by duneos_exit() from within an app task (and by the task wrapper) */
void duneos_supervisor_app_exited(int code);

/* Block until every launched app has exited */
void duneos_supervisor_wait_all(void);

/* Current number of live app tasks */
int duneos_supervisor_running_count(void);

/* Send a message to the named app's mailbox. 0=ok, -1=error/full/not found */
int duneos_send(const char *dest, const void *data, size_t len);

/* Receive a message from this app's mailbox. Returns bytes received or -1 */
int duneos_recv(duneos_msg_t *out, uint32_t timeout_ms);
