#pragma once

/*
 * libdune — DuneOS userspace POSIX wrapper library (ABI v3).
 *
 * Apps built with libdune.a include this header to access the API table and
 * the DuneOS-specific functions that are not part of standard POSIX.
 *
 * Usage:
 *   - Link the app against libdune.a (dbt.py build does this automatically).
 *   - Include <duneos/libdune.h> only when you need the non-POSIX extensions
 *     (duneos_exit, duneos_send, duneos_recv, etc.).
 *   - Standard POSIX calls (open, read, malloc, pthread_create, ...) are
 *     provided by libdune.a transparently; no include is required for them.
 *
 * Internals:
 *   The loader writes the kernel's duneos_api_t pointer to __duneos_api_ptr
 *   before app_main is called.  All libdune.a wrapper functions dispatch
 *   through that pointer.  __duneos_api_ptr is NULL before loader injection;
 *   calling libdune functions before app_main is entered is undefined.
 */

#include <duneos/api.h>
#include <duneos/abi.h>       /* duneos_app_manifest_t, permission bits */
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * Kernel API table pointer — written by the loader before app_main.
 * Always non-NULL during normal app execution.
 */
extern const duneos_api_t *__duneos_api_ptr;

/* -------------------------------------------------------------------------
 * DuneOS lifecycle
 * ---------------------------------------------------------------------- */

/*
 * Terminate the calling app with the given exit code.
 * Notifies the supervisor, which frees app resources and applies the restart
 * policy from init.yaml.  Never returns.
 */
void duneos_exit(int code) __attribute__((noreturn));

/* Signal that this service has finished initialisation. */
void duneos_service_ready(void);

/* Kick the software watchdog for this app task. */
void duneos_wdt_reset(void);

/* -------------------------------------------------------------------------
 * Inter-app messaging
 * ---------------------------------------------------------------------- */

/* IPC message — mirrors duneos_msg_t from supervisor.h */
typedef struct {
    char   data[64];   /* DUNEOS_MSG_DATA_MAX */
    size_t len;
    char   sender[64]; /* DUNEOS_APP_NAME_MAX */
} duneos_msg_t;

/* Send len bytes to the named app's mailbox (non-blocking, drops if full).
 * Returns 0 on success, -1 on error.                                        */
int duneos_send(const char *dest, const void *data, size_t len);

/* Receive a message into out_msg (blocks up to timeout_ms, 0 = no wait).
 * Returns bytes received, or -1 on timeout / error.                         */
int duneos_recv(duneos_msg_t *out_msg, uint32_t timeout_ms);

/* -------------------------------------------------------------------------
 * Supervisor control
 * ---------------------------------------------------------------------- */

/* Restart policy mirror — matches supervisor.h's duneos_restart_policy_t.
 * Both must stay in sync; ABI v3 guarantees identical numeric values.     */
typedef enum {
    DUNEOS_RESTART_NO         = 0,
    DUNEOS_RESTART_ALWAYS     = 1,
    DUNEOS_RESTART_ON_FAILURE = 2,
} duneos_restart_policy_t;

/* Slot snapshot — matches supervisor.h's duneos_slot_info_t.              */
typedef struct {
    char                    name[64];   /* DUNEOS_APP_NAME_MAX */
    bool                    active;
    duneos_restart_policy_t restart_policy;
    uint32_t                restart_count;
} duneos_slot_info_t;

int  duneos_supervisor_launch(const char *path);
int  duneos_supervisor_running_count(void);
void duneos_supervisor_wait_for_completion(int target_count);
int  duneos_supervisor_list_slots(duneos_slot_info_t *out, int count);
int  duneos_supervisor_restart_by_name(const char *name);

/* -------------------------------------------------------------------------
 * VFS queries
 * ---------------------------------------------------------------------- */

/* Fill out[] with up to max mount-point strings (each 32 bytes).
 * Returns number of mounts written.                                       */
int  duneos_vfs_list_mounts(char (*out)[32], int max);
bool duneos_vfs_flash_available(void);
bool duneos_vfs_sd_available(void);

/* -------------------------------------------------------------------------
 * Dynamic loader (used by the `run` shell command)
 * ---------------------------------------------------------------------- */

int  duneos_loader_load(const char *path, void **out_app);
int  duneos_loader_run(void *app);
int  duneos_loader_run_captured(void *app, char **out_buf, size_t *out_len);
void duneos_loader_unload(void *app);
const duneos_app_manifest_t *duneos_loader_get_manifest(const void *app);

/* -------------------------------------------------------------------------
 * Low-level system
 * ---------------------------------------------------------------------- */

/* Reboot the device — equivalent to esp_restart(). */
void     duneos_sys_restart(void);
/* Total free heap in bytes across all regions. */
uint32_t duneos_sys_free_heap(void);

#ifdef __cplusplus
}
#endif
