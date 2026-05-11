#include "duneos/init.h"
#include "duneos/klog.h"

#include "cJSON.h"

#include <fcntl.h>
#include <unistd.h>
#include <string.h>
#include <stdlib.h>

static const char *TAG = "duneos/init";

#define INIT_JSON_BUF_MAX 2048

static duneos_restart_policy_t parse_restart(const char *s)
{
    if (!s)                         return DUNEOS_RESTART_NO;
    if (strcmp(s, "always") == 0)   return DUNEOS_RESTART_ALWAYS;
    if (strcmp(s, "on-failure") == 0) return DUNEOS_RESTART_ON_FAILURE;
    return DUNEOS_RESTART_NO;
}

esp_err_t duneos_init_load(duneos_init_config_t *cfg)
{
    if (!cfg) return ESP_ERR_INVALID_ARG;
    cfg->count = 0;

    int fd = open(DUNEOS_INIT_PATH, O_RDONLY);
    if (fd < 0) return ESP_ERR_NOT_FOUND;

    char *buf = malloc(INIT_JSON_BUF_MAX);
    if (!buf) { close(fd); return ESP_ERR_NO_MEM; }

    ssize_t n = read(fd, buf, INIT_JSON_BUF_MAX - 1);
    close(fd);

    if (n <= 0) { free(buf); return ESP_FAIL; }
    buf[n] = '\0';

    cJSON *root = cJSON_ParseWithLength(buf, (size_t)n);
    free(buf);
    if (!root) {
        klog_e(TAG, "JSON parse error in %s", DUNEOS_INIT_PATH);
        return ESP_FAIL;
    }

    cJSON *services = cJSON_GetObjectItemCaseSensitive(root, "services");
    if (!cJSON_IsArray(services)) {
        klog_e(TAG, "init.json: 'services' array missing");
        cJSON_Delete(root);
        return ESP_FAIL;
    }

    cJSON *svc;
    cJSON_ArrayForEach(svc, services) {
        if (cfg->count >= DUNEOS_MAX_SERVICES) {
            klog_w(TAG, "init.json: more than %d services — ignoring extras",
                   DUNEOS_MAX_SERVICES);
            break;
        }

        cJSON *path_item    = cJSON_GetObjectItemCaseSensitive(svc, "path");
        cJSON *restart_item = cJSON_GetObjectItemCaseSensitive(svc, "restart");

        if (!cJSON_IsString(path_item) || !path_item->valuestring) {
            klog_w(TAG, "init.json: service entry missing 'path' — skipping");
            continue;
        }

        duneos_service_desc_t *desc = &cfg->services[cfg->count];
        strlcpy(desc->path, path_item->valuestring, sizeof(desc->path));
        desc->restart = parse_restart(
            cJSON_IsString(restart_item) ? restart_item->valuestring : NULL);

        cfg->count++;
    }

    cJSON_Delete(root);
    klog_i(TAG, "loaded %d service(s) from %s", cfg->count, DUNEOS_INIT_PATH);
    return ESP_OK;
}
