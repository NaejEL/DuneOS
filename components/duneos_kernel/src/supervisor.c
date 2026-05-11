/*
 * DuneOS app supervisor.
 *
 * Lifecycle:
 *   supervisor_launch() → xTaskCreate(app_task_entry) → app->entry() runs
 *   app calls duneos_exit(code) → posts to s_exit_queue → vTaskDelete(NULL)
 *   supervisor_task receives → duneos_loader_unload(app) → marks slot free
 *
 *   If app_main() returns without calling duneos_exit(), the task wrapper
 *   treats that as duneos_exit(0).
 *
 * Messaging:
 *   Each running slot has a FreeRTOS queue (mailbox). duneos_send() looks up
 *   the target slot by name. duneos_recv() looks up the calling task's slot.
 *   Both are O(DUNEOS_MAX_RUNNING_APPS) — fine for a small embedded system.
 */

#include "duneos/supervisor.h"
#include "duneos/klog.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"

#include <string.h>
#include <stdlib.h>

static const char *TAG = "duneos/supervisor";

static duneos_loader_ops_t s_loader_ops;

void duneos_supervisor_register_loader(const duneos_loader_ops_t *ops)
{
    s_loader_ops = *ops;
}

/* ----- internal types ---------------------------------------------------- */

typedef struct {
    duneos_app_t           *app;
    TaskHandle_t            task;
    QueueHandle_t           mailbox;
    bool                    active;
    char                    name[DUNEOS_APP_NAME_MAX];
    duneos_restart_policy_t restart_policy;
    char                    restart_path[DUNEOS_PATH_MAX];
} app_slot_t;

typedef struct {
    TaskHandle_t task;
    int          code;
} exit_msg_t;

/* ----- state ------------------------------------------------------------- */

static app_slot_t        s_slots[DUNEOS_MAX_RUNNING_APPS];
static QueueHandle_t     s_exit_queue;
static SemaphoreHandle_t s_all_done;
static SemaphoreHandle_t s_lock;
static int               s_active_count = 0;
/* Counts slots in the unload→relaunch window to prevent spurious all_done */
static int               s_want_restart = 0;

/* ----- slot helpers ------------------------------------------------------ */

static app_slot_t *slot_by_task(TaskHandle_t t)
{
    for (int i = 0; i < DUNEOS_MAX_RUNNING_APPS; i++) {
        if (s_slots[i].active && s_slots[i].task == t) return &s_slots[i];
    }
    return NULL;
}

static app_slot_t *slot_by_name(const char *name)
{
    for (int i = 0; i < DUNEOS_MAX_RUNNING_APPS; i++) {
        if (s_slots[i].active && strcmp(s_slots[i].name, name) == 0)
            return &s_slots[i];
    }
    return NULL;
}

static app_slot_t *slot_alloc(void)
{
    for (int i = 0; i < DUNEOS_MAX_RUNNING_APPS; i++) {
        if (!s_slots[i].active) return &s_slots[i];
    }
    return NULL;
}

/* ----- app task wrapper -------------------------------------------------- */

static void app_task_entry(void *arg)
{
    app_slot_t *slot = (app_slot_t *)arg;
    klog_d(TAG, "starting '%s'", slot->name);
    s_loader_ops.run(slot->app);
    /* app_main returned without calling duneos_exit — treat as exit(0) */
    duneos_supervisor_app_exited(0);
    vTaskDelete(NULL);
}

/* ----- helpers ----------------------------------------------------------- */

static void maybe_signal_all_done(void)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    bool all_done = (s_active_count == 0 && s_want_restart == 0);
    xSemaphoreGive(s_lock);
    /* Binary semaphore: giving when already 1 is a safe no-op */
    if (all_done) xSemaphoreGive(s_all_done);
}

/* ----- supervisor task --------------------------------------------------- */

static void supervisor_task(void *arg)
{
    (void)arg;
    exit_msg_t msg;

    while (1) {
        if (xQueueReceive(s_exit_queue, &msg, portMAX_DELAY) != pdTRUE) continue;

        xSemaphoreTake(s_lock, portMAX_DELAY);

        app_slot_t *slot = slot_by_task(msg.task);
        if (!slot) {
            xSemaphoreGive(s_lock);
            klog_w(TAG, "exit from unknown task — ignoring");
            continue;
        }

        /* Capture all state under the lock, then release before any I/O or
         * heap work.  klog/ESP_LOGI takes an internal FreeRTOS log mutex;
         * holding s_lock while taking a second mutex triggers SMP priority-
         * inheritance interactions that can corrupt the FreeRTOS queue
         * structs.  Do all work with no locks held. */
        char                    name[DUNEOS_APP_NAME_MAX];
        char                    rpath[DUNEOS_PATH_MAX];
        strlcpy(name,  slot->name,         sizeof(name));
        strlcpy(rpath, slot->restart_path, sizeof(rpath));
        int                     code    = msg.code;
        duneos_app_t           *app     = slot->app;
        QueueHandle_t           mailbox = slot->mailbox;
        duneos_restart_policy_t policy  = slot->restart_policy;

        bool should_restart = (policy == DUNEOS_RESTART_ALWAYS) ||
                              (policy == DUNEOS_RESTART_ON_FAILURE && code != 0);

        slot->app     = NULL;
        slot->mailbox = NULL;
        slot->active  = false;
        s_active_count--;
        if (should_restart) s_want_restart++;

        xSemaphoreGive(s_lock);

        klog_i(TAG, "'%s' exited (code %d)%s", name, code,
               should_restart ? " — restarting" : "");

        s_loader_ops.unload(app);
        if (mailbox) vQueueDelete(mailbox);

        if (should_restart) {
            /* Relaunch inherits same policy so restarts persist */
            duneos_supervisor_launch_policy(rpath, policy);
            xSemaphoreTake(s_lock, portMAX_DELAY);
            s_want_restart--;
            xSemaphoreGive(s_lock);
        }

        maybe_signal_all_done();
    }
}

/* ----- public API -------------------------------------------------------- */

esp_err_t duneos_supervisor_init(void)
{
    memset(s_slots, 0, sizeof(s_slots));
    s_active_count = 0;

    s_lock = xSemaphoreCreateMutex();
    if (!s_lock) return ESP_ERR_NO_MEM;

    s_exit_queue = xQueueCreate(DUNEOS_MAX_RUNNING_APPS, sizeof(exit_msg_t));
    if (!s_exit_queue) return ESP_ERR_NO_MEM;

    /* Binary semaphore: taken by wait_all, given by supervisor when count→0 */
    s_all_done = xSemaphoreCreateBinary();
    if (!s_all_done) return ESP_ERR_NO_MEM;

    BaseType_t ret = xTaskCreate(supervisor_task, "duneos_sv",
                                  4096, NULL, DUNEOS_APP_TASK_PRIORITY + 1,
                                  NULL);
    if (ret != pdPASS) return ESP_ERR_NO_MEM;

    klog_d(TAG, "supervisor ready (max %d concurrent apps)", DUNEOS_MAX_RUNNING_APPS);
    return ESP_OK;
}

esp_err_t duneos_supervisor_launch_policy(const char *path,
                                            duneos_restart_policy_t policy)
{
    if (!path) return ESP_ERR_INVALID_ARG;

    xSemaphoreTake(s_lock, portMAX_DELAY);
    app_slot_t *slot = slot_alloc();
    if (!slot) {
        xSemaphoreGive(s_lock);
        klog_e(TAG, "no free app slots (max %d)", DUNEOS_MAX_RUNNING_APPS);
        return ESP_ERR_NO_MEM;
    }
    /* Pre-mark active to prevent a concurrent launch from grabbing this slot */
    slot->active = true;
    xSemaphoreGive(s_lock);

    duneos_app_t *app = NULL;
    esp_err_t err = s_loader_ops.load(path, &app);
    if (err != ESP_OK) {
        slot->active = false;
        return err;
    }

    const duneos_app_manifest_t *m = s_loader_ops.get_manifest(app);
    strlcpy(slot->name, m->name, sizeof(slot->name));
    strlcpy(slot->restart_path, path, sizeof(slot->restart_path));
    slot->restart_policy = policy;
    slot->app = app;

    slot->mailbox = xQueueCreate(DUNEOS_MAILBOX_DEPTH, sizeof(duneos_msg_t));
    /* Mailbox failure is non-fatal — messaging just won't work */

    uint32_t stack = m->stack_size > 0 ? m->stack_size : DUNEOS_APP_DEFAULT_STACK;

    /* Increment before xTaskCreate: the new task may exit and the supervisor
     * may process the exit before this task resumes after the yield inside
     * xTaskCreate, causing s_active_count to underflow to -1 if we increment
     * after.  Decrement back on task creation failure. */
    xSemaphoreTake(s_lock, portMAX_DELAY);
    s_active_count++;
    xSemaphoreGive(s_lock);

    BaseType_t ret = xTaskCreate(app_task_entry, slot->name,
                                  stack / sizeof(StackType_t),
                                  slot, DUNEOS_APP_TASK_PRIORITY, &slot->task);
    if (ret != pdPASS) {
        xSemaphoreTake(s_lock, portMAX_DELAY);
        s_active_count--;
        xSemaphoreGive(s_lock);
        s_loader_ops.unload(app);
        if (slot->mailbox) vQueueDelete(slot->mailbox);
        slot->active = false;
        klog_e(TAG, "xTaskCreate failed for '%s'", slot->name);
        return ESP_ERR_NO_MEM;
    }

    klog_d(TAG, "launched '%s' (stack %lu B, restart=%d)",
           slot->name, (unsigned long)stack, (int)policy);
    return ESP_OK;
}

esp_err_t duneos_supervisor_launch(const char *path)
{
    return duneos_supervisor_launch_policy(path, DUNEOS_RESTART_NO);
}

void duneos_supervisor_app_exited(int code)
{
    exit_msg_t msg = {
        .task = xTaskGetCurrentTaskHandle(),
        .code = code,
    };
    /* portMAX_DELAY: queue depth == max running apps, so this shouldn't block */
    xQueueSend(s_exit_queue, &msg, portMAX_DELAY);
}

void duneos_supervisor_wait_all(void)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    bool already_done = (s_active_count == 0 && s_want_restart == 0);
    xSemaphoreGive(s_lock);

    if (!already_done) xSemaphoreTake(s_all_done, portMAX_DELAY);
}

void duneos_service_ready(void)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    app_slot_t *slot = slot_by_task(xTaskGetCurrentTaskHandle());
    char name[DUNEOS_APP_NAME_MAX];
    strlcpy(name, slot ? slot->name : "(unknown)", sizeof(name));
    xSemaphoreGive(s_lock);
    klog_i(TAG, "service '%s' ready", name);
}

int duneos_supervisor_running_count(void)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    int n = s_active_count;
    xSemaphoreGive(s_lock);
    return n;
}

/* ----- messaging --------------------------------------------------------- */

int duneos_send(const char *dest, const void *data, size_t len)
{
    if (!dest || !data || len == 0 || len > DUNEOS_MSG_DATA_MAX) return -1;

    xSemaphoreTake(s_lock, portMAX_DELAY);
    app_slot_t *dst = slot_by_name(dest);
    xSemaphoreGive(s_lock);

    if (!dst || !dst->mailbox) return -1;

    duneos_msg_t msg;
    memset(&msg, 0, sizeof(msg));
    memcpy(msg.data, data, len);
    msg.len = len;

    /* Find sender name */
    xSemaphoreTake(s_lock, portMAX_DELAY);
    app_slot_t *src = slot_by_task(xTaskGetCurrentTaskHandle());
    if (src) strlcpy(msg.sender, src->name, sizeof(msg.sender));
    xSemaphoreGive(s_lock);

    return (xQueueSend(dst->mailbox, &msg, 0) == pdTRUE) ? 0 : -1;
}

int duneos_recv(duneos_msg_t *out, uint32_t timeout_ms)
{
    if (!out) return -1;

    xSemaphoreTake(s_lock, portMAX_DELAY);
    app_slot_t *slot = slot_by_task(xTaskGetCurrentTaskHandle());
    QueueHandle_t mb = slot ? slot->mailbox : NULL;
    xSemaphoreGive(s_lock);

    if (!mb) return -1;

    TickType_t ticks = (timeout_ms == 0) ? 0 : pdMS_TO_TICKS(timeout_ms);
    if (xQueueReceive(mb, out, ticks) != pdTRUE) return -1;
    return (int)out->len;
}
