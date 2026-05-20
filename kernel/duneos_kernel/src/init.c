#include "duneos/init.h"
#include "duneos/klog.h"
#include "board_config.h"

#include <errno.h>
#include <fcntl.h>
#include <unistd.h>
#include <string.h>
#include <stdlib.h>
#include <stdbool.h>
#include <ctype.h>

#ifdef DUNEOS_HAVE_RECOVERY_PIN
#include "duneos/hal_gpio.h"
#include "duneos/hal_time.h"
#endif

static const char *TAG = "duneos/init";

/* ----- recovery / safe-boot pin (Phase 24.7) ---------------------------- */

/* Read the recovery pin once at boot. If held to its active level when
 * duneos_init_load() runs, the kernel loads /flash/init.yaml.safe instead
 * of /flash/init.yaml + /sd/init.yaml. Escape hatch from a bricked
 * init.yaml without re-flashing. Returns false when the board doesn't
 * declare a recovery_pin. Non-destructive (input + pull-up). */
static bool recovery_pin_held(void)
{
#ifdef DUNEOS_HAVE_RECOVERY_PIN
    duneos_hal_gpio_set_dir (DUNEOS_RECOVERY_PIN, DUNEOS_GPIO_DIR_INPUT);
    duneos_hal_gpio_set_pull(DUNEOS_RECOVERY_PIN,
                             DUNEOS_RECOVERY_PIN_ACTIVE_LOW
                               ? DUNEOS_GPIO_PULL_UP
                               : DUNEOS_GPIO_PULL_DOWN);
    /* Settle the pull resistor before reading. */
    duneos_hal_delay_us(1000);
    int level = duneos_hal_gpio_get_level(DUNEOS_RECOVERY_PIN);
    if (level < 0) return false;
    bool active_low = DUNEOS_RECOVERY_PIN_ACTIVE_LOW;
    return active_low ? (level == 0) : (level == 1);
#else
    return false;
#endif
}

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
static int parse_yaml(char *buf, duneos_init_config_t *cfg)
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

    return cfg->count > 0 ? 0 : -EIO;
}

/* Extract app name from path: "/flash/bin/usb_shell.dap" → "usb_shell".
 * Used for cross-filesystem deduplication (same app, different path prefix). */
static void app_name_from_path(const char *path, char *out, size_t outsz)
{
    const char *base = strrchr(path, '/');
    base = base ? base + 1 : path;
    size_t len = strlen(base);
    if (len > 4 && strcmp(base + len - 4, ".dap") == 0) len -= 4;
    if (len >= outsz) len = outsz - 1;
    memcpy(out, base, len);
    out[len] = '\0';
}

int duneos_init_load(duneos_init_config_t *cfg)
{
    if (!cfg) return -EINVAL;

    /* Recovery / safe-boot path: if the board declares a recovery_pin and
     * it's held at boot, load ONLY /flash/init.yaml.safe — skip the normal
     * /flash/init.yaml + /sd/init.yaml chain. Lets the user escape from a
     * crash-looping daemon by holding a button at power-on. */
    bool safe = recovery_pin_held();
    if (safe) {
        klog_w(TAG, "recovery pin held — booting safe mode (%s)",
               DUNEOS_INIT_PATH_FLASH_SAFE);
    }

    static const char *const paths_normal[] = {
        DUNEOS_INIT_PATH_FLASH,
        DUNEOS_INIT_PATH_SD,
        NULL,
    };
    static const char *const paths_safe[] = {
        DUNEOS_INIT_PATH_FLASH_SAFE,
        NULL,
    };
    const char *const *paths = safe ? paths_safe : paths_normal;

    char *buf = malloc(INIT_BUF_MAX);
    if (!buf) return -ENOMEM;

    cfg->count = 0;

    /* Merge services from all init files (flash first, then SD).
     * Duplicate entries are skipped by app name (basename without .dap) so
     * "/flash/bin/usb_shell.dap" and "/sd/bin/usb_shell.dap" both resolve to
     * the same service and the flash-side entry wins. */
    for (int i = 0; paths[i]; i++) {
        int fd = open(paths[i], O_RDONLY);
        if (fd < 0) continue;

        ssize_t n = read(fd, buf, INIT_BUF_MAX - 1);
        close(fd);
        if (n <= 0) continue;
        buf[n] = '\0';

        duneos_init_config_t *tmp = calloc(1, sizeof(duneos_init_config_t));
        if (!tmp) { klog_w(TAG, "init: OOM skipping %s", paths[i]); continue; }

        if (parse_yaml(buf, tmp) != 0 || tmp->count == 0) {
            klog_w(TAG, "YAML parse error or empty services list in %s", paths[i]);
            free(tmp);
            continue;
        }

        int added = 0;
        for (int s = 0; s < tmp->count; s++) {
            char new_name[64];
            app_name_from_path(tmp->services[s].path, new_name, sizeof(new_name));
            int dup = 0;
            for (int j = 0; j < cfg->count; j++) {
                char existing_name[64];
                app_name_from_path(cfg->services[j].path, existing_name, sizeof(existing_name));
                if (strcmp(existing_name, new_name) == 0) {
                    klog_d(TAG, "init: skipping '%s' from %s (already registered as '%s')",
                           new_name, paths[i], cfg->services[j].path);
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
    return cfg->count > 0 ? 0 : -ENOENT;
}
