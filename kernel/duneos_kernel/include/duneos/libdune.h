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
 * Captured app contract (ADR 016)
 *
 * When the shell runs a `.dap` "inline" (no `heap_size` declared in the
 * manifest), the app body executes in the SHELL'S task — not in a separate
 * task. This is the "captured" mode, and it has three contracts the app
 * author MUST honour:
 *
 *   1. NO pthread_create. The kernel will refuse with EPERM. A thread
 *      spawned here would keep running in the shell's task after the
 *      captured app unwinds, corrupting it. If you need threads, declare
 *      `heap_size: <bytes>` in your duneos.yaml — the shell will detect
 *      that and dispatch the app to spawned mode (its own task) instead.
 *
 *   2. FREE WHAT YOU MALLOC. There's no per-app heap pool to wipe on exit,
 *      so any malloc'd memory you don't free leaks into the shell.
 *
 *   3. CLOSE WHAT YOU OPEN. Same reason — any open() / opendir() / socket()
 *      handle stays open in the shell's fd table after you exit.
 *
 * `duneos_exit(code)` is safe — the kernel longjmps back to the loader's
 * setjmp checkpoint and unwinds cleanly; the shell survives and gets the
 * captured app's stdout. Spawned-mode apps (heap_size > 0) have no such
 * restrictions because they're real tasks.
 * ---------------------------------------------------------------------- */

/* -------------------------------------------------------------------------
 * DuneOS lifecycle
 * ---------------------------------------------------------------------- */

/*
 * Terminate the calling app with the given exit code.
 * Notifies the supervisor, which frees app resources and applies the restart
 * policy from init.yaml.  Never returns.
 *
 * Captured-mode safety (ADR 016): if this is called while running captured,
 * the kernel longjmps back to the loader instead of vTaskDelete-ing the
 * shell task. See the "Captured app contract" block above.
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

/* Per-app memory snapshot — matches supervisor.h's duneos_proc_mem_t.       */
typedef struct {
    char     name[64];      /* DUNEOS_APP_NAME_MAX */
    uint32_t data_size;     /* data pool (.data/.bss/.rodata)        */
    uint32_t stack_size;    /* reserved task stack                   */
    uint32_t stack_used;    /* peak stack use (reserved − high-water) */
    uint32_t heap_size;     /* reserved per-app heap pool (0 if none) */
    uint32_t heap_used;     /* currently allocated in the heap pool  */
} duneos_proc_mem_t;

int  duneos_supervisor_launch(const char *path);
int  duneos_supervisor_running_count(void);
void duneos_supervisor_wait_for_completion(int target_count);
int  duneos_supervisor_list_slots(duneos_slot_info_t *out, int count);
int  duneos_supervisor_list_mem(duneos_proc_mem_t *out, int count);
int  duneos_supervisor_restart_by_name(const char *name);
/* Hand off to child_path and exit; the supervisor relaunches us when it exits
 * (ADR 031). Call then duneos_exit(). Frees our RAM for the child. */
int  duneos_supervisor_chain(const char *child_path);

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
 * Configuration paths (Phase 25.5)
 *
 * DuneOS standard: an app's configuration lives at
 *   /flash/etc/<app_name>/config.yaml
 *
 * The /etc tree is staged into the LittleFS sysbin, which mounts at /flash —
 * so the physical path is under /flash (a bare /etc has no mount). Apps are
 * free to read from anywhere, but this convention lets users find config in a
 * predictable place (mirrors /etc on Linux, rooted at /flash) and lets `dbt
 * flashimg` provision per-board defaults from `boards/<board>/etc/`.
 *
 * duneos_config_path() builds the canonical path string. It does NOT open
 * the file or check that it exists — call open() / stat() afterwards.
 *
 * Returns 0 on success, -1 if outsz is too small (path would be truncated).
 * ---------------------------------------------------------------------- */
int duneos_config_path(const char *app_name, char *out, size_t outsz);

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
