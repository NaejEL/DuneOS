#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "esp_err.h"

#include "duneos/abi.h"
#include "duneos/task.h"
#include "duneos/vfs.h"
#include "duneos/manifest.h"
#include "duneos/loader.h"

static const char *TAG = "duneos";

/*
 * DuneOS kernel entry point — runs as a standard ESP-IDF app, on top of the
 * ESP-IDF bootloader. Not to be confused with the ESP-IDF app bootloader.
 *
 * Startup sequence:
 *   1. Init VFS — mount SD (/sd), tmp (/tmp), dev (/dev)
 *   2. Read /sd/apps/manifest.json
 *   3. Select app to launch (autoboot or menu)
 *   4. Load ELF into PSRAM, apply relocations, resolve symbols
 *   5. Run app — supervisor task stays alive alongside it
 */
void app_main(void)
{
    ESP_LOGI(TAG, "DuneOS " DUNEOS_VERSION_STRING
                  " (ABI v%d) starting", DUNEOS_ABI_VERSION);

    /* --- Phase 1: VFS init ------------------------------------------------ */
    esp_err_t err = duneos_vfs_init();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "VFS init failed: %s — halting", esp_err_to_name(err));
        return;
    }

    /* --- Phase 1: read app manifest --------------------------------------- */
    duneos_manifest_t manifest;
    err = duneos_manifest_read(&manifest);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "manifest read failed: %s — halting", esp_err_to_name(err));
        return;
    }

    if (manifest.app_count == 0) {
        ESP_LOGW(TAG, "no apps in manifest — nothing to launch");
        return;
    }

    /* --- Phase 1: select app ---------------------------------------------- */
    const duneos_manifest_entry_t *target = NULL;

    if (manifest.autoboot[0] != '\0') {
        target = duneos_manifest_find(&manifest, manifest.autoboot);
        if (!target) {
            ESP_LOGW(TAG, "autoboot app '%s' not found in manifest",
                     manifest.autoboot);
        }
    }

    /* Fall back to first app if autoboot is unset or not found */
    if (!target) {
        target = &manifest.apps[0];
        ESP_LOGI(TAG, "launching first app: %s", target->meta.name);
    } else {
        ESP_LOGI(TAG, "autoboot: %s", target->meta.name);
    }

    /* --- Phase 3: load ELF and run ---------------------------------------- */
    duneos_app_t *app = NULL;
    err = duneos_loader_load(target->path, &app);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "load failed (%s): %s",
                 target->path, esp_err_to_name(err));
        return;
    }

    err = duneos_loader_run(app);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "app '%s' exited with error: %s",
                 target->meta.name, esp_err_to_name(err));
    } else {
        ESP_LOGI(TAG, "app '%s' exited cleanly", target->meta.name);
    }

    duneos_loader_unload(app);
}
