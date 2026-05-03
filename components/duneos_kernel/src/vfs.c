#include "duneos/vfs.h"

#include "esp_log.h"
#include "esp_vfs.h"
#include "esp_vfs_fat.h"

static const char *TAG = "duneos/vfs";

static bool s_initialized = false;

esp_err_t duneos_vfs_mount_sd(void)
{
    /* TODO Phase 2: configure SDMMC/SPI host from BSP board_config.h,
     * then call esp_vfs_fat_sdspi_mount() or esp_vfs_fat_sdmmc_mount() */
    ESP_LOGW(TAG, "SD mount not yet implemented");
    return ESP_OK;
}

esp_err_t duneos_vfs_mount_tmp(void)
{
    /* TODO Phase 2: register a RAM-backed VFS at /tmp */
    ESP_LOGW(TAG, "/tmp mount not yet implemented");
    return ESP_OK;
}

esp_err_t duneos_vfs_mount_dev(void)
{
    /* TODO Phase 2: register /dev with uart, gpio, spi, i2c device files */
    ESP_LOGW(TAG, "/dev mount not yet implemented");
    return ESP_OK;
}

esp_err_t duneos_vfs_init(void)
{
    if (s_initialized) {
        return ESP_ERR_INVALID_STATE;
    }

    esp_err_t err;

    err = duneos_vfs_mount_sd();
    if (err != ESP_OK) return err;

    err = duneos_vfs_mount_tmp();
    if (err != ESP_OK) return err;

    err = duneos_vfs_mount_dev();
    if (err != ESP_OK) return err;

    s_initialized = true;
    ESP_LOGI(TAG, "VFS ready (/sd /tmp /dev)");
    return ESP_OK;
}

esp_err_t duneos_vfs_deinit(void)
{
    if (!s_initialized) {
        return ESP_ERR_INVALID_STATE;
    }
    /* TODO: unmount all registered filesystems */
    s_initialized = false;
    return ESP_OK;
}
