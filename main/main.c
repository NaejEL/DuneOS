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

static void kernel_idle_loop(void)
{
    ESP_LOGI(TAG, "kernel idle — no app running");
    while (1) {
        duneos_task_delay_ms(5000);
        ESP_LOGI(TAG, "idle");
    }
}

void app_main(void)
{
    ESP_LOGI(TAG, "DuneOS " DUNEOS_VERSION_STRING
                  " (ABI v%d) starting", DUNEOS_ABI_VERSION);

    /* VFS — SD failure is non-fatal during development */
    esp_err_t err = duneos_vfs_init();
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "VFS init failed (%s) — no SD card?",
                 esp_err_to_name(err));
        kernel_idle_loop();
        return;
    }

    /* Manifest */
    duneos_manifest_t manifest;
    err = duneos_manifest_read(&manifest);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "no manifest (%s) — drop to idle",
                 esp_err_to_name(err));
        kernel_idle_loop();
        return;
    }

    if (manifest.app_count == 0) {
        ESP_LOGW(TAG, "manifest lists no apps — idle");
        kernel_idle_loop();
        return;
    }

    /* Select app */
    const duneos_manifest_entry_t *target = NULL;
    if (manifest.autoboot[0] != '\0') {
        target = duneos_manifest_find(&manifest, manifest.autoboot);
        if (!target) {
            ESP_LOGW(TAG, "autoboot '%s' not in manifest — using first app",
                     manifest.autoboot);
        }
    }
    if (!target) {
        target = &manifest.apps[0];
    }

    ESP_LOGI(TAG, "launching: %s %s (ABI>=%lu)",
             target->meta.name,
             target->meta.version,
             (unsigned long)target->meta.required_abi_version);

    /* Load and run */
    duneos_app_t *app = NULL;
    err = duneos_loader_load(target->path, &app);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "load failed: %s", esp_err_to_name(err));
        kernel_idle_loop();
        return;
    }

    err = duneos_loader_run(app);
    ESP_LOGI(TAG, "app '%s' finished: %s",
             target->meta.name, esp_err_to_name(err));

    duneos_loader_unload(app);
    kernel_idle_loop();
}
