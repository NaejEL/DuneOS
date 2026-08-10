/*
 * libdune — DuneOS system wrappers (ABI v3).
 *
 * Provides the non-POSIX surface declared in <duneos/libdune.h>:
 *   - App lifecycle (duneos_exit, duneos_service_ready, duneos_wdt_reset)
 *   - Inter-app messaging (duneos_send, duneos_recv)
 *   - Supervisor control (launch, list, restart)
 *   - VFS queries (vfs_list_mounts, vfs_flash_available, vfs_sd_available)
 *   - Dynamic loader (load, run, run_captured, unload, get_manifest)
 *   - Low-level system (duneos_sys_restart, duneos_sys_free_heap)
 *
 * __errno is also defined here so that PicoLibc's errno macro
 * (-D__PICOLIBC_ERRNO_FUNCTION=__errno) routes through the kernel's
 * per-task errno rather than a static cell.
 */

#include <duneos/libdune.h>
#include <stdint.h>

extern const duneos_api_t *__duneos_api_ptr;

/* -------------------------------------------------------------------------
 * PicoLibc errno bridge
 *
 * PicoLibc calls int *__errno(void) to get the per-thread errno pointer.
 * Routing it through the kernel ensures apps share FreeRTOS task-local errno
 * with the kernel's newlib implementation.
 * ---------------------------------------------------------------------- */

int *__errno(void)
{
    return __duneos_api_ptr->sys.errno_fn();
}

/* -------------------------------------------------------------------------
 * App lifecycle
 * ---------------------------------------------------------------------- */

void duneos_exit(int code)
{
    __duneos_api_ptr->sys.exit(code);
    /* Satisfy the compiler's noreturn analysis — sys.exit never returns. */
    __builtin_unreachable();
}

void duneos_service_ready(void)
{
    __duneos_api_ptr->sys.service_ready();
}

void duneos_wdt_reset(void)
{
    __duneos_api_ptr->sys.wdt_reset();
}

/* -------------------------------------------------------------------------
 * Inter-app messaging
 * ---------------------------------------------------------------------- */

int duneos_send(const char *dest, const void *data, size_t len)
{
    return __duneos_api_ptr->sys.send(dest, data, len);
}

int duneos_recv(duneos_msg_t *out_msg, uint32_t timeout_ms)
{
    return __duneos_api_ptr->sys.recv((void *)out_msg, timeout_ms);
}

/* -------------------------------------------------------------------------
 * Supervisor control
 * ---------------------------------------------------------------------- */

int duneos_supervisor_launch(const char *path)
{
    return __duneos_api_ptr->sys.supervisor_launch(path);
}

int duneos_supervisor_running_count(void)
{
    return __duneos_api_ptr->sys.supervisor_running_count();
}

int duneos_supervisor_list_slots(duneos_slot_info_t *out, int count)
{
    return __duneos_api_ptr->sys.supervisor_list_slots((void *)out, count);
}

int duneos_supervisor_restart_by_name(const char *name)
{
    return __duneos_api_ptr->sys.supervisor_restart_by_name(name);
}

/* -------------------------------------------------------------------------
 * VFS queries
 * ---------------------------------------------------------------------- */

int duneos_vfs_list_mounts(char (*out)[32], int max)
{
    return __duneos_api_ptr->sys.vfs_list_mounts(out, max);
}

bool duneos_vfs_flash_available(void)
{
    return __duneos_api_ptr->sys.vfs_flash_available();
}

bool duneos_vfs_sd_available(void)
{
    return __duneos_api_ptr->sys.vfs_sd_available();
}

/* -------------------------------------------------------------------------
 * Dynamic loader
 * ---------------------------------------------------------------------- */

int duneos_loader_load(const char *path, void **out_app)
{
    return __duneos_api_ptr->sys.loader_load(path, out_app);
}

int duneos_loader_run(void *app)
{
    return __duneos_api_ptr->sys.loader_run(app);
}

int duneos_loader_run_captured(void *app, char **out_buf, size_t *out_len)
{
    return __duneos_api_ptr->sys.loader_run_captured(app, out_buf, out_len);
}

void duneos_loader_unload(void *app)
{
    __duneos_api_ptr->sys.loader_unload(app);
}

const duneos_app_manifest_t *duneos_loader_get_manifest(const void *app)
{
    return (const duneos_app_manifest_t *)
        __duneos_api_ptr->sys.loader_get_manifest(app);
}

/* -------------------------------------------------------------------------
 * Config paths
 *
 * Pure string formatting — no kernel call. snprintf returning >= outsz means
 * truncation; we treat that as failure rather than silently returning a bad
 * path.
 * ---------------------------------------------------------------------- */

#include <stdio.h>
#include <string.h>

int duneos_config_path(const char *app_name, char *out, size_t outsz)
{
    if (!app_name || !out || outsz == 0) return -1;
    /* The config tree is staged into the LittleFS sysbin, which mounts at the
     * root "/" — so the physical path is /etc/<app>/config.yaml. */
    int n = snprintf(out, outsz, "/etc/%s/config.yaml", app_name);
    if (n < 0 || (size_t)n >= outsz) return -1;
    return 0;
}

/* -------------------------------------------------------------------------
 * Low-level system
 * ---------------------------------------------------------------------- */

void duneos_sys_restart(void)
{
    __duneos_api_ptr->sys.esp_restart();
}

uint32_t duneos_sys_free_heap(void)
{
    return __duneos_api_ptr->sys.esp_get_free_heap_size();
}
