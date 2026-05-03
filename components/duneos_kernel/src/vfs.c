#include "duneos/vfs.h"

#include "esp_log.h"
#include "esp_vfs_fat.h"
#include "driver/spi_master.h"
#include "driver/gpio.h"
#include "sdmmc_cmd.h"

#include "board_config.h"

static const char *TAG = "duneos/vfs";

#define SD_MOUNT_POINT  "/sd"
#define SD_MAX_FILES    8

static sdmmc_card_t *s_card = NULL;
static bool          s_initialized = false;

esp_err_t duneos_vfs_mount_sd(void)
{
    esp_err_t err;

    sdmmc_host_t host = SDSPI_HOST_DEFAULT();
    host.slot = DUNEOS_SD_SPI_HOST;

    spi_bus_config_t bus_cfg = {
        .mosi_io_num     = DUNEOS_SD_MOSI_PIN,
        .miso_io_num     = DUNEOS_SD_MISO_PIN,
        .sclk_io_num     = DUNEOS_SD_CLK_PIN,
        .quadwp_io_num   = -1,
        .quadhd_io_num   = -1,
        .max_transfer_sz = 4096,
    };

    err = spi_bus_initialize(DUNEOS_SD_SPI_HOST, &bus_cfg, SDSPI_DEFAULT_DMA);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "SPI bus init failed: %s", esp_err_to_name(err));
        return err;
    }

    sdspi_device_config_t slot_cfg = SDSPI_DEVICE_CONFIG_DEFAULT();
    slot_cfg.gpio_cs   = DUNEOS_SD_CS_PIN;
    slot_cfg.host_id   = DUNEOS_SD_SPI_HOST;
#if DUNEOS_SD_CD_PIN >= 0
    slot_cfg.gpio_cd   = DUNEOS_SD_CD_PIN;
#endif

    esp_vfs_fat_sdmmc_mount_config_t mount_cfg = {
        .format_if_mount_failed = false,
        .max_files              = SD_MAX_FILES,
        .allocation_unit_size   = 16 * 1024,
    };

    err = esp_vfs_fat_sdspi_mount(SD_MOUNT_POINT, &host, &slot_cfg,
                                   &mount_cfg, &s_card);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "SD mount failed: %s", esp_err_to_name(err));
        if (err == ESP_FAIL) {
            ESP_LOGE(TAG, "Is a FAT-formatted SD card inserted?");
        }
        spi_bus_free(DUNEOS_SD_SPI_HOST);
        return err;
    }

    ESP_LOGI(TAG, "SD mounted at %s — %s %.1f GB",
             SD_MOUNT_POINT,
             s_card->cid.name,
             (double)((uint64_t)s_card->csd.capacity * s_card->csd.sector_size)
                 / (1024.0 * 1024.0 * 1024.0));
    return ESP_OK;
}

esp_err_t duneos_vfs_mount_tmp(void)
{
    /* TODO Phase 2: register a RAM-backed VFS at /tmp */
    ESP_LOGW(TAG, "/tmp not yet implemented");
    return ESP_OK;
}

esp_err_t duneos_vfs_mount_dev(void)
{
    /* TODO Phase 2: register /dev with uart, gpio, spi, i2c device files */
    ESP_LOGW(TAG, "/dev not yet implemented");
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

    esp_vfs_fat_sdcard_unmount(SD_MOUNT_POINT, s_card);
    spi_bus_free(DUNEOS_SD_SPI_HOST);
    s_card = NULL;
    s_initialized = false;

    ESP_LOGI(TAG, "VFS unmounted");
    return ESP_OK;
}
