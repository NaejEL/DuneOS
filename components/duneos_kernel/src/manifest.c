#include "duneos/manifest.h"

#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "cJSON.h"
#include "duneos/abi.h"

static const char *TAG = "duneos/manifest";

/* Max size we'll read into RAM before complaining */
#define MANIFEST_MAX_BYTES  (16 * 1024)

static uint32_t parse_permissions(const cJSON *perms_arr)
{
    uint32_t bits = 0;
    if (!cJSON_IsArray(perms_arr)) return 0;

    const cJSON *item;
    cJSON_ArrayForEach(item, perms_arr) {
        if (!cJSON_IsString(item)) continue;
        const char *s = item->valuestring;
        if      (strcmp(s, "gpio")     == 0) bits |= DUNEOS_PERM_GPIO;
        else if (strcmp(s, "uart")     == 0) bits |= DUNEOS_PERM_UART;
        else if (strcmp(s, "spi")      == 0) bits |= DUNEOS_PERM_SPI;
        else if (strcmp(s, "i2c")      == 0) bits |= DUNEOS_PERM_I2C;
        else if (strcmp(s, "net")      == 0) bits |= DUNEOS_PERM_NET;
        else if (strcmp(s, "fs_read")  == 0) bits |= DUNEOS_PERM_FS_READ;
        else if (strcmp(s, "fs_write") == 0) bits |= DUNEOS_PERM_FS_WRITE;
        else ESP_LOGW(TAG, "unknown permission '%s' — ignored", s);
    }
    return bits;
}

esp_err_t duneos_manifest_read(duneos_manifest_t *out)
{
    if (!out) return ESP_ERR_INVALID_ARG;
    memset(out, 0, sizeof(*out));

    FILE *f = fopen(DUNEOS_MANIFEST_PATH, "r");
    if (!f) {
        ESP_LOGE(TAG, "cannot open %s", DUNEOS_MANIFEST_PATH);
        return ESP_ERR_NOT_FOUND;
    }

    /* Read the whole file into a heap buffer */
    char *buf = malloc(MANIFEST_MAX_BYTES);
    if (!buf) {
        fclose(f);
        return ESP_ERR_NO_MEM;
    }

    size_t len = fread(buf, 1, MANIFEST_MAX_BYTES - 1, f);
    fclose(f);
    buf[len] = '\0';

    if (len == 0) {
        ESP_LOGE(TAG, "%s is empty", DUNEOS_MANIFEST_PATH);
        free(buf);
        return ESP_ERR_INVALID_SIZE;
    }

    cJSON *root = cJSON_Parse(buf);
    free(buf);

    if (!root) {
        const char *err = cJSON_GetErrorPtr();
        ESP_LOGE(TAG, "JSON parse error near: %s", err ? err : "?");
        return ESP_ERR_INVALID_RESPONSE;
    }

    /* autoboot (optional) */
    const cJSON *autoboot = cJSON_GetObjectItemCaseSensitive(root, "autoboot");
    if (cJSON_IsString(autoboot) && autoboot->valuestring) {
        strlcpy(out->autoboot, autoboot->valuestring, sizeof(out->autoboot));
    }

    /* apps array */
    const cJSON *apps = cJSON_GetObjectItemCaseSensitive(root, "apps");
    if (!cJSON_IsArray(apps)) {
        ESP_LOGE(TAG, "manifest missing 'apps' array");
        cJSON_Delete(root);
        return ESP_ERR_INVALID_RESPONSE;
    }

    const cJSON *app_json;
    cJSON_ArrayForEach(app_json, apps) {
        if (out->app_count >= DUNEOS_MANIFEST_MAX_APPS) {
            ESP_LOGW(TAG, "manifest has more than %d apps — truncating",
                     DUNEOS_MANIFEST_MAX_APPS);
            break;
        }

        duneos_manifest_entry_t *entry = &out->apps[out->app_count];

        const cJSON *name = cJSON_GetObjectItemCaseSensitive(app_json, "name");
        const cJSON *path = cJSON_GetObjectItemCaseSensitive(app_json, "path");
        const cJSON *ver  = cJSON_GetObjectItemCaseSensitive(app_json, "version");
        const cJSON *abi  = cJSON_GetObjectItemCaseSensitive(app_json, "required_abi_version");
        const cJSON *perm = cJSON_GetObjectItemCaseSensitive(app_json, "permissions");

        if (!cJSON_IsString(name) || !cJSON_IsString(path)) {
            ESP_LOGW(TAG, "app entry missing 'name' or 'path' — skipping");
            continue;
        }

        strlcpy(entry->meta.name, name->valuestring, sizeof(entry->meta.name));
        strlcpy(entry->path,      path->valuestring, sizeof(entry->path));

        if (cJSON_IsString(ver)) {
            strlcpy(entry->meta.version, ver->valuestring,
                    sizeof(entry->meta.version));
        }

        entry->meta.required_abi_version =
            cJSON_IsNumber(abi) ? (uint32_t)abi->valuedouble : 1;

        entry->meta.permissions = parse_permissions(perm);

        ESP_LOGI(TAG, "  [%d] %s  %s  (ABI>=%lu, perms=0x%02lx)",
                 out->app_count,
                 entry->meta.name,
                 entry->path,
                 (unsigned long)entry->meta.required_abi_version,
                 (unsigned long)entry->meta.permissions);

        out->app_count++;
    }

    cJSON_Delete(root);

    ESP_LOGI(TAG, "manifest: %d app(s), autoboot='%s'",
             out->app_count, out->autoboot[0] ? out->autoboot : "(none)");
    return ESP_OK;
}

const duneos_manifest_entry_t *duneos_manifest_find(
    const duneos_manifest_t *m, const char *name)
{
    if (!m || !name) return NULL;
    for (int i = 0; i < m->app_count; i++) {
        if (strcmp(m->apps[i].meta.name, name) == 0) {
            return &m->apps[i];
        }
    }
    return NULL;
}
