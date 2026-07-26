#pragma once

#include "board_config.h"   /* DUNEOS_HAS_SD (authoritative; default below) */
#include "esp_err.h"
#include "sdmmc_cmd.h"
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

/* Match vfs.c: a board that omits DUNEOS_HAS_SD is treated as "SD present", so
 * this header and vfs.c agree on whether duneos_vfs_mount_sd() is declared —
 * regardless of include order in the translation unit. */
#ifndef DUNEOS_HAS_SD
#define DUNEOS_HAS_SD 1
#endif

/*
 * DuneOS VFS initialisation.
 *
 * Mount points after duneos_vfs_init():
 *   /flash — LittleFS on the 'sysbin' partition (always, fatal if missing)
 *   /sd    — FatFS on SD card (optional, see DUNEOS_HAS_SD in board_config.h)
 *   /tmp   — RAM-backed ephemeral storage
 *   /dev   — device files (uart, gpio, spi, i2c, …)
 */
esp_err_t duneos_vfs_init(void);
esp_err_t duneos_vfs_deinit(void);

/* Mount/unmount individual filesystems (called internally by duneos_vfs_init) */
esp_err_t duneos_vfs_mount_flash(void);
#if DUNEOS_HAS_SD
esp_err_t duneos_vfs_mount_sd(void);
#endif
esp_err_t duneos_vfs_mount_tmp(void);
esp_err_t duneos_vfs_mount_dev(void);

/* Runtime availability queries */
bool duneos_vfs_flash_available(void);
bool duneos_vfs_data_available(void);
bool duneos_vfs_sd_available(void);

/*
 * List active VFS mount points (e.g. "/flash", "/sd", "/tmp", "/dev").
 * Copies up to `max` mount paths into `out` (each slot is char[32]).
 * Returns the count written.
 */
int duneos_vfs_list_mounts(char (*out)[32], int max);

/* Total + free bytes of the filesystem backing `path` (for `df`). 0 on success,
 * -errno (e.g. -ENODEV for /tmp, /dev which have no block accounting). */
int duneos_fs_info(const char *path, uint64_t *total, uint64_t *freeb);

/*
 * Return the live sdmmc_card_t handle (or NULL if SD is not mounted).
 * Used by the USB MSC driver to hand raw sector access to TinyUSB.
 */
sdmmc_card_t *duneos_vfs_get_sd_card(void);

/*
 * First-boot provisioning: copy embedded firmware blobs to /flash/bin/.
 * No-op when g_duneos_blob_count == 0 (no blobs embedded at build time).
 * Skips individual files that already exist on flash.
 */
esp_err_t duneos_vfs_provision_flash(void);

/*
 * Firmware blob descriptor — defined in the generated blobs_gen.c.
 * Each blob corresponds to a .dap file embedded via COMPONENT_EMBED_FILES.
 */
typedef struct {
    const char    *name;
    const uint8_t *data;
    size_t         size;
} duneos_blob_entry_t;

extern const duneos_blob_entry_t g_duneos_blobs[];
extern const int                 g_duneos_blob_count;
