#include "duneos/init.h"
#include "duneos/klog.h"
#include "board_config.h"

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

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
        } else if (strcmp(key, "after") == 0) {
            strlcpy(cur->after, val, sizeof(cur->after));
        }
        /* "services:" and unknown keys are silently skipped */

next_line:
        *eol = saved;
        line = (*eol == '\n') ? eol + 1 : eol;
    }

    /* No services is a valid outcome (e.g. `services: []` on a headless board);
     * the caller decides what to do with an empty list. parse_yaml only skips
     * unrecognised lines, so it never reports a "parse error" of its own. */
    return 0;
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
    bool found_any_file = false;
    for (int i = 0; paths[i]; i++) {
        int fd = open(paths[i], O_RDONLY);
        if (fd < 0) continue;

        ssize_t n = read(fd, buf, INIT_BUF_MAX - 1);
        close(fd);
        if (n <= 0) continue;
        buf[n] = '\0';
        found_any_file = true;

        duneos_init_config_t *tmp = calloc(1, sizeof(duneos_init_config_t));
        if (!tmp) { klog_w(TAG, "init: OOM skipping %s", paths[i]); continue; }

        if (parse_yaml(buf, tmp) != 0) {
            klog_w(TAG, "YAML parse error in %s", paths[i]);
            free(tmp);
            continue;
        }
        if (tmp->count == 0) {
            klog_i(TAG, "%s: empty services list — nothing to launch", paths[i]);
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
    /* -ENOENT only when no init.yaml file existed at all. An existing file
     * with `services: []` is intentional and must not trigger autoboot. */
    return found_any_file ? 0 : -ENOENT;
}

/* ------------------------------------------------------------------------- */
/* duneos_init_run() — launch services honouring the `after:` dependency.   */
/* ------------------------------------------------------------------------- */

/*
 * Pending list state — shared between duneos_init_run() and on_service_exit().
 *
 * We hold a copy of the parsed config (not a pointer to caller-owned memory)
 * because the caller may be on its way to kernel_idle() and the local cfg
 * could go out of scope before all deferred services fire. The footprint is
 * `DUNEOS_MAX_SERVICES * sizeof(duneos_service_desc_t)` = ~2.1 KiB on a kernel
 * that already burns 64 KiB on the kernel task stack. Worth it for simplicity.
 *
 * Concurrency: the supervisor task fires the exit observer; main task only
 * runs the initial pass. Both walk `s_pending[]`. Access is serialised by
 * the fact that main blocks on duneos_supervisor_wait_all() right after the
 * initial pass — but main may also race with an exit observer firing before
 * wait_all() takes the lock. We guard the pending array with a small mutex
 * so the deferred launch can't be missed.
 */

#define INIT_MAX_PENDING DUNEOS_MAX_SERVICES

typedef struct {
    duneos_service_desc_t desc;
    char                  pred_name[DUNEOS_APP_NAME_MAX];
    bool                  pending;   /* true until predecessor exits */
    bool                  launched;  /* true once supervisor_launch returned 0 */
} pending_entry_t;

static pending_entry_t   s_pending[INIT_MAX_PENDING];
static int               s_pending_count = 0;
static SemaphoreHandle_t s_pending_lock = NULL;

static void launch_one(const duneos_service_desc_t *s)
{
    klog_i(TAG, "starting service '%s' (restart=%d)",
           s->path, (int)s->restart);
    esp_err_t err = duneos_supervisor_launch_policy(s->path, s->restart);
    if (err != ESP_OK)
        klog_e(TAG, "failed to start '%s': %s", s->path, esp_err_to_name(err));
    /* No meminfo dump here: this runs in the init task on the boot path, and a
     * fault here is silent (console comes up after init) and boot-loops. Read
     * the memory picture interactively with `free` instead — it runs in its own
     * app task and cannot take down the boot. */
}

/* Exit observer — fires from the supervisor task when any app exits.
 * Walks the pending list and launches anything whose predecessor was `name`.
 *
 * Concurrency model: we are called from the supervisor task itself, so no
 * other observer invocation can race us. supervisor_launch_policy takes
 * s_lock internally, so we drop s_pending_lock around launch_one to keep
 * the lock order simple (s_pending_lock never held while taking s_lock).
 *
 * Pacing: yield one tick between successive launches so the newly created
 * task gets a chance to do its first init steps before the next loader.load
 * monopolises the supervisor's stack again. This was observed mattering
 * when one exit released 3+ deferred services that all wanted the display.
 */
static void on_service_exit(const char *name, int code)
{
    (void)code;
    if (!s_pending_lock) return;

    int released = 0;
    xSemaphoreTake(s_pending_lock, portMAX_DELAY);
    for (int i = 0; i < s_pending_count; i++) {
        if (!s_pending[i].pending) continue;
        if (strcmp(s_pending[i].pred_name, name) != 0) continue;

        klog_i(TAG, "after-dep: '%s' exited — releasing '%s'",
               name, s_pending[i].desc.path);
        s_pending[i].pending = false;
        duneos_service_desc_t snapshot = s_pending[i].desc;
        xSemaphoreGive(s_pending_lock);

        /* 20 ms yield between successive launches so the previously
         * launched dependent gets a scheduling slot before the next
         * loader_load monopolises the supervisor's stack again. See the
         * companion note in supervisor.c's exit-processing block. */
        if (released > 0) vTaskDelay(pdMS_TO_TICKS(20));
        launch_one(&snapshot);
        released++;

        xSemaphoreTake(s_pending_lock, portMAX_DELAY);
        s_pending[i].launched = true;
    }
    /* Diagnostic: an exit that released nothing while services are still
     * waiting means the exiting app's name didn't match any `after:` target —
     * usually a blank/unexpected slot name. Surfaces the actual name seen. */
    int still_waiting = 0;
    for (int i = 0; i < s_pending_count; i++)
        if (s_pending[i].pending) still_waiting++;
    xSemaphoreGive(s_pending_lock);

    if (released > 0)
        klog_i(TAG, "after-dep: released %d service(s) waiting on '%s'",
               released, name);
    else if (still_waiting > 0)
        klog_i(TAG, "after-dep: '%s' exited but %d service(s) still waiting (no name match)",
               name, still_waiting);
}

/* Look up a service by app-name in the parsed config. Returns NULL if absent. */
static const duneos_service_desc_t *find_by_name(const duneos_init_config_t *cfg,
                                                  const char *name)
{
    for (int i = 0; i < cfg->count; i++) {
        char base[DUNEOS_APP_NAME_MAX];
        app_name_from_path(cfg->services[i].path, base, sizeof(base));
        if (strcmp(base, name) == 0) return &cfg->services[i];
    }
    return NULL;
}

int duneos_init_run(const duneos_init_config_t *cfg)
{
    if (!cfg || cfg->count == 0) return 0;

    if (!s_pending_lock) {
        s_pending_lock = xSemaphoreCreateMutex();
        if (!s_pending_lock) {
            klog_e(TAG, "init: pending lock OOM — launching all services immediately");
            for (int i = 0; i < cfg->count; i++) launch_one(&cfg->services[i]);
            return cfg->count;
        }
    }

    /* First pass: classify each service into immediate vs deferred.
     * A service with `after: X` is deferred iff X is in cfg AND X eventually
     * exits (i.e. restart != always). Otherwise we log and launch at boot. */
    s_pending_count = 0;
    int scheduled = 0;
    for (int i = 0; i < cfg->count; i++) {
        const duneos_service_desc_t *s = &cfg->services[i];

        if (s->after[0] == '\0') {
            launch_one(s);
            scheduled++;
            continue;
        }

        const duneos_service_desc_t *pred = find_by_name(cfg, s->after);
        if (!pred) {
            klog_w(TAG, "'%s' after '%s' but predecessor not in init.yaml — launching immediately",
                   s->path, s->after);
            launch_one(s);
            scheduled++;
            continue;
        }
        if (pred->restart == DUNEOS_RESTART_ALWAYS) {
            klog_w(TAG, "'%s' after '%s' but predecessor has restart:always (never exits) — launching immediately",
                   s->path, s->after);
            launch_one(s);
            scheduled++;
            continue;
        }

        if (s_pending_count >= INIT_MAX_PENDING) {
            klog_w(TAG, "'%s' deferred but pending list full — launching immediately",
                   s->path);
            launch_one(s);
            scheduled++;
            continue;
        }

        pending_entry_t *p = &s_pending[s_pending_count++];
        p->desc = *s;
        strlcpy(p->pred_name, s->after, sizeof(p->pred_name));
        p->pending  = true;
        p->launched = false;
        klog_i(TAG, "service '%s' deferred until '%s' exits",
               s->path, s->after);
        scheduled++;
    }

    /* Register the observer only if we actually have deferred services. */
    if (s_pending_count > 0) {
        duneos_supervisor_set_exit_observer(on_service_exit);
    }

    return scheduled;
}
