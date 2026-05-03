#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "esp_err.h"

#include "duneos/abi.h"
#include "duneos/task.h"
#include "duneos/vfs.h"
#include "duneos/loader.h"

static const char *TAG = "duneos";

/*
 * DuneOS kernel entry point — runs as a standard ESP-IDF app, on top of the
 * ESP-IDF bootloader. Not to be confused with the ESP-IDF app bootloader.
 *
 * Startup sequence:
 *   1. Init VFS and mount SD card at /sd
 *   2. Read /sd/apps/manifest.json to determine which app to launch
 *   3. Load the target ELF, resolve symbols against the kernel export table
 *   4. Jump to app_main — the kernel supervisor task keeps running alongside
 */
void app_main(void)
{
    ESP_LOGI(TAG, "DuneOS " DUNEOS_VERSION_STRING
                  " (ABI v%d) starting", DUNEOS_ABI_VERSION);

    esp_err_t err;

    err = duneos_vfs_init();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "VFS init failed: %s", esp_err_to_name(err));
        return;
    }

    /* TODO Phase 1: read /sd/apps/manifest.json and select app to launch */
    /* TODO Phase 3: duneos_loader_load(), resolve symbols, jump to app_main */

    ESP_LOGI(TAG, "kernel ready — no app loaded (loader not yet implemented)");
}
