#include "duneos/init.h"
#include "duneos/klog.h"

#include <fcntl.h>
#include <unistd.h>
#include <string.h>
#include <stdlib.h>
#include <ctype.h>

static const char *TAG = "duneos/init";

#define INIT_BUF_MAX 2048

static duneos_restart_policy_t parse_restart(const char *s)
{
    if (!s)                              return DUNEOS_RESTART_NO;
    if (strcmp(s, "always") == 0)        return DUNEOS_RESTART_ALWAYS;
    if (strcmp(s, "on-failure") == 0)    return DUNEOS_RESTART_ON_FAILURE;
    return DUNEOS_RESTART_NO;
}

/* Trim leading whitespace in-place, return pointer to first non-space. */
static const char *ltrim(const char *s)
{
    while (*s && isspace((unsigned char)*s)) s++;
    return s;
}

/*
 * Minimal init.yaml parser.
 *
 * Accepts only the fixed schema used by DuneOS:
 *
 *   services:
 *     - path: /sd/apps/shell.dap
 *       restart: always
 *     - path: /sd/apps/wifi.dap
 *       restart: on-failure
 *
 * Rules:
 *  - Lines starting with '#' are ignored.
 *  - A line whose first non-space character is '-' starts a new service entry.
 *  - "key: value" pairs are parsed for "path" and "restart".
 *  - Indentation is not validated beyond detecting list items.
 */
static esp_err_t parse_yaml(char *buf, duneos_init_config_t *cfg)
{
    char *line = buf;
    duneos_service_desc_t *cur = NULL;

    while (*line) {
        /* Find end of line */
        char *eol = line;
        while (*eol && *eol != '\n') eol++;
        char saved = *eol;
        *eol = '\0';
        /* Strip trailing \r and other whitespace (handles Windows CRLF files) */
        char *tail = eol - 1;
        while (tail >= line && isspace((unsigned char)*tail)) *tail-- = '\0';

        const char *trimmed = ltrim(line);

        if (*trimmed == '#' || *trimmed == '\0') {
            goto next_line;
        }

        /* New list item */
        if (*trimmed == '-') {
            if (cfg->count >= DUNEOS_MAX_SERVICES) {
                klog_w(TAG, "init.yaml: more than %d services — ignoring extras",
                       DUNEOS_MAX_SERVICES);
                goto next_line;
            }
            cur = &cfg->services[cfg->count];
            memset(cur, 0, sizeof(*cur));
            cfg->count++;
            trimmed = ltrim(trimmed + 1);   /* skip '-' and any space after it */
            if (*trimmed == '\0') goto next_line;
            /* Fall through: the same line may have "key: value" after '-' */
        }

        /* key: value */
        const char *colon = strchr(trimmed, ':');
        if (!colon || !cur) goto next_line;

        char key[32];
        size_t klen = (size_t)(colon - trimmed);
        if (klen == 0 || klen >= sizeof(key)) goto next_line;
        memcpy(key, trimmed, klen);
        key[klen] = '\0';

        const char *val = ltrim(colon + 1);

        if (strcmp(key, "path") == 0) {
            if (val[0] != '/') {
                /* Paths without leading '/' are relative — prepend it.
                 * init.json historically stored paths as "sd/apps/foo.dap". */
                char norm[sizeof(cur->path)];
                snprintf(norm, sizeof(norm), "/%s", val);
                strlcpy(cur->path, norm, sizeof(cur->path));
            } else {
                strlcpy(cur->path, val, sizeof(cur->path));
            }
        } else if (strcmp(key, "restart") == 0) {
            cur->restart = parse_restart(val);
        }
        /* "services:" and unknown keys are silently skipped */

next_line:
        *eol = saved;
        line = (*eol == '\n') ? eol + 1 : eol;
    }

    return cfg->count > 0 ? ESP_OK : ESP_FAIL;
}

esp_err_t duneos_init_load(duneos_init_config_t *cfg)
{
    if (!cfg) return ESP_ERR_INVALID_ARG;

    static const char *const paths[] = {
        DUNEOS_INIT_PATH_FLASH,
        DUNEOS_INIT_PATH_SD,
        NULL,
    };

    char *buf = malloc(INIT_BUF_MAX);
    if (!buf) return ESP_ERR_NO_MEM;

    cfg->count = 0;

    /* Merge services from all init files (flash first, then SD).
     * Duplicate paths are skipped so a service in both files runs once. */
    for (int i = 0; paths[i]; i++) {
        int fd = open(paths[i], O_RDONLY);
        if (fd < 0) continue;

        ssize_t n = read(fd, buf, INIT_BUF_MAX - 1);
        close(fd);
        if (n <= 0) continue;
        buf[n] = '\0';

        duneos_init_config_t *tmp = calloc(1, sizeof(duneos_init_config_t));
        if (!tmp) { klog_w(TAG, "init: OOM skipping %s", paths[i]); continue; }

        if (parse_yaml(buf, tmp) != ESP_OK || tmp->count == 0) {
            klog_w(TAG, "YAML parse error or empty services list in %s", paths[i]);
            free(tmp);
            continue;
        }

        int added = 0;
        for (int s = 0; s < tmp->count; s++) {
            int dup = 0;
            for (int j = 0; j < cfg->count; j++) {
                if (strcmp(cfg->services[j].path, tmp->services[s].path) == 0) {
                    dup = 1; break;
                }
            }
            if (dup) continue;
            if (cfg->count >= DUNEOS_MAX_SERVICES) {
                klog_w(TAG, "init: max services (%d) reached, skipping %s",
                       DUNEOS_MAX_SERVICES, tmp->services[s].path);
                break;
            }
            cfg->services[cfg->count++] = tmp->services[s];
            added++;
        }
        free(tmp);
        klog_i(TAG, "loaded %d service(s) from %s", added, paths[i]);
    }

    free(buf);
    return cfg->count > 0 ? ESP_OK : ESP_ERR_NOT_FOUND;
}
