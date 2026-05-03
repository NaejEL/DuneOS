#pragma once

#include "esp_err.h"

/*
 * DuneOS VFS initialisation.
 *
 * Builds on top of the ESP-IDF VFS layer (esp_vfs_register).
 * Does not reinvent a VFS — registers DuneOS-specific drivers into the
 * existing ESP-IDF dispatch mechanism.
 *
 * Mount points after duneos_vfs_init():
 *   /sd    — FatFS on SD card (SPI or SDMMC depending on BSP)
 *   /tmp   — RAM-backed ephemeral storage
 *   /dev   — device files (uart, gpio, spi, i2c, …)
 */
esp_err_t duneos_vfs_init(void);
esp_err_t duneos_vfs_deinit(void);

/* Mount/unmount individual filesystems (called internally by duneos_vfs_init) */
esp_err_t duneos_vfs_mount_sd(void);
esp_err_t duneos_vfs_mount_tmp(void);
esp_err_t duneos_vfs_mount_dev(void);
