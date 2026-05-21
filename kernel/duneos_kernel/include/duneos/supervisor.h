#pragma once

#include <stddef.h>
#include "esp_err.h"
#include <stdbool.h>
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
#define DUNEOS_PATH_MAX          128     /* max length for stored service paths */

#define DUNEOS_APP_DEFAULT_STACK 8192   /* bytes — overridden by manifest stack_size */
#define DUNEOS_APP_TASK_PRIORITY 2      /* tskIDLE_PRIORITY + 2 */

/* Special exit codes set by the kernel when killing an app abnormally */
#define DUNEOS_EXIT_CRASHED    127  /* CPU exception (load/store prohibited, illegal, div/0) */
#define DUNEOS_EXIT_STACK_OVF  126  /* FreeRTOS stack overflow detection */
#define DUNEOS_EXIT_WDT        125  /* Software watchdog timeout */

/*
 * Restart policy for services launched via duneos_supervisor_launch_policy().
 * RESTART_NO    — service exits and is not relaunched (default).
 * RESTART_ALWAYS — relaunched unconditionally on exit, regardless of exit code.
 * RESTART_ON_FAILURE — relaunched only when exit code != 0.
 *
 * wait_all() never returns while any slot has a pending restart or a running
 * service with a non-NO restart policy.
 */
typedef enum {
    DUNEOS_RESTART_NO         = 0,
    DUNEOS_RESTART_ALWAYS     = 1,
    DUNEOS_RESTART_ON_FAILURE = 2,
} duneos_restart_policy_t;

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
typedef void (*duneos_get_data_pool_fn_t)(const duneos_app_t *app,
                                          uintptr_t *base, size_t *size);
/* ADR 016: callbacks for captured-app exit unwinding. */
typedef bool      (*duneos_captured_active_fn_t)(void);
typedef void      (*duneos_captured_longjmp_fn_t)(int code) __attribute__((noreturn));

typedef struct {
    duneos_load_fn_t             load;
    duneos_run_fn_t              run;
    duneos_unload_fn_t           unload;
    duneos_get_manifest_fn_t     get_manifest;
    duneos_get_data_pool_fn_t    get_data_pool;
    duneos_captured_active_fn_t  captured_active;   /* may be NULL */
    duneos_captured_longjmp_fn_t captured_longjmp;  /* may be NULL */
} duneos_loader_ops_t;

/* Called by duneos_loader_init() before any supervisor_launch() */
void duneos_supervisor_register_loader(const duneos_loader_ops_t *ops);

/* Start the supervisor (call once from app_main before any launch) */
esp_err_t duneos_supervisor_init(void);

/* Load path and start it as a new FreeRTOS task. Non-blocking. */
esp_err_t duneos_supervisor_launch(const char *path);

/* Like duneos_supervisor_launch() but attaches a restart policy to the slot. */
esp_err_t duneos_supervisor_launch_policy(const char *path,
                                           duneos_restart_policy_t policy);

/* Called by duneos_exit() from within an app task (and by the task wrapper) */
void duneos_supervisor_app_exited(int code);

/* Block until every launched app has exited and no restart is pending */
void duneos_supervisor_wait_all(void);

/* Current number of live app tasks */
int duneos_supervisor_running_count(void);

/* Block until s_active_count drops to <= target_count. Uses a counting
 * semaphore signalled once per processed exit — no busy-wait, no missed
 * events. Typical caller: a shell that just launched an app and wants
 * to wait until that app (and only that app) exits. Pass the pre-launch
 * running count as target_count.                                         */
void duneos_supervisor_wait_for_completion(int target_count);

/*
 * Snapshot of one supervisor slot — safe to read after the call returns.
 * active=false means the slot is empty; name/policy/restart_count are zeroed.
 */
typedef struct {
    char                    name[DUNEOS_APP_NAME_MAX];
    bool                    active;
    duneos_restart_policy_t restart_policy;
    uint32_t                restart_count;
} duneos_slot_info_t;

/* Fill out[] with up to count slot snapshots; returns number of entries filled. */
int duneos_supervisor_list_slots(duneos_slot_info_t *out, int count);

/* Force-kill and relaunch the named slot regardless of its restart policy.
 * Returns 0 if found and kill queued, -1 if name not found or slot inactive. */
int duneos_supervisor_restart_by_name(const char *name);

/*
 * Signal that this service has finished initialisation and is ready.
 * Logs the event; future phases will use this to unblock dependent services.
 * Safe to call from any app task after duneos_supervisor_init().
 */
void duneos_service_ready(void);

/*
 * Exit observer (Phase 25.4) — set a callback that fires whenever an app
 * exits. The init system uses this to release services that were waiting
 * for a predecessor via `after:` in init.yaml. Only one observer at a
 * time; calling with NULL clears it. Called from the supervisor task —
 * keep work short (no blocking primitives on s_lock).
 */
typedef void (*duneos_exit_observer_fn)(const char *name, int code);
void duneos_supervisor_set_exit_observer(duneos_exit_observer_fn fn);

/* Send a message to the named app's mailbox. 0=ok, -1=error/full/not found */
int duneos_send(const char *dest, const void *data, size_t len);

/* Receive a message from this app's mailbox. Returns bytes received or -1 */
int duneos_recv(duneos_msg_t *out, uint32_t timeout_ms);

/* -------------------------------------------------------------------------
 * Phase 20 — Memory hardening API
 * ---------------------------------------------------------------------- */

/*
 * Allocate/free from the calling app's per-app heap (manifest heap_size > 0).
 * Falls back to the global heap when no per-app heap is configured.
 * Safe to call from any app task context.
 */
void *duneos_supervisor_app_malloc(size_t size);
void *duneos_supervisor_app_realloc(void *ptr, size_t size);
void *duneos_supervisor_app_calloc(size_t n, size_t size);
void  duneos_supervisor_app_free(void *ptr);

/*
 * Permissive pointer validation — rejects NULL, peripheral range, and IRAM.
 * Used for read-source buffers (kernel reads FROM ptr) which may legitimately
 * point into kernel-returned data (e.g. strerror result).
 */
bool duneos_supervisor_check_user_ptr(const void *ptr, size_t len);

/*
 * Strict pointer validation — ptr must be within the calling app's own
 * allocated memory (data pool, per-app heap, or task stack).
 * Used for read-target buffers (kernel writes INTO ptr).
 * Falls back to check_user_ptr when called from a non-app context.
 */
bool duneos_supervisor_check_app_writable_ptr(const void *ptr, size_t len);

/*
 * Kick the software watchdog for the calling app task.
 * The app must call this at least once every wdt_timeout_ms (from manifest)
 * to avoid being killed by the supervisor.  No-op if WDT is disabled.
 */
void duneos_supervisor_wdt_reset(void);

/*
 * App-callable exit — notify the supervisor and delete the calling task.
 * Exported to apps via the symbol table and the API table.
 * Never returns.
 */
void duneos_exit(int code) __attribute__((noreturn));
