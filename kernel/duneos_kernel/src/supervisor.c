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
 *
 * Phase 20 — Memory hardening:
 *   - Per-app heap pool (multi_heap_register) for malloc isolation
 *   - Software WDT: supervisor checks every 500 ms, app calls duneos_wdt_reset()
 *   - Stack overflow hook: kills app task, kernel survives
 *   - CPU exception handler (Xtensa): forced-exit recovery without kernel reboot
 *   - Syscall pointer validation: NULL + peripheral range check
 */

#include "duneos/supervisor.h"
#include "duneos/klog.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/timers.h"

#include "esp_heap_caps.h"
#include "esp_system.h"
#include "multi_heap.h"

#include <string.h>
#include <stdlib.h>
#include <stdbool.h>

#ifdef CONFIG_IDF_TARGET_ARCH_XTENSA
#include "xtensa_api.h"       /* xt_set_exception_handler / xt_exc_handler */
#include "xtensa/corebits.h"  /* EXCCAUSE_*, XCHAL_EXCCAUSE_NUM */
#include "esp_rom_sys.h"
#endif

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
    uint32_t                restart_count;
    bool                    force_restart;

    /* Phase 24.7: circuit breaker — disable restart after too many crashes
     * in a short window. Prevents a bad init.yaml from looping forever and
     * blocking the user out (USB MSC may not enumerate during a restart
     * storm). Defaults: 3 crashes in 30 s → restart disabled. */
    uint32_t                breaker_window_start_ms;  /* tick of first crash in window */
    uint32_t                breaker_crashes_in_window;
    uint32_t                breaker_max_crashes;       /* 0 = use DUNEOS_BREAKER_DEFAULT_MAX */
    uint32_t                breaker_window_ms;         /* 0 = use DUNEOS_BREAKER_DEFAULT_MS  */
    bool                    breaker_tripped;           /* true once limit hit; restart skipped */

    /* Phase 20: per-app heap pool */
    uint8_t            *heap_pool_buf;   /* raw buffer for this app's heap */
    size_t              heap_pool_size;
    multi_heap_handle_t heap_handle;     /* NULL when heap_size == 0 */

    /* Phase 20: data pool bounds (from loader, for bounds checking) */
    uintptr_t           data_pool_base;
    size_t              data_pool_size;

    /* Phase 20: crash recovery — set by exception/overflow/WDT handlers */
    bool                crashed;    /* prevents double-post from WDT on already-killed slot */
    uint32_t            exc_cause;  /* Xtensa EXCCAUSE at crash */
    uint32_t            exc_pc;     /* PC at crash */

    /* Phase 20: software WDT */
    TickType_t          wdt_timeout_ticks;  /* 0 = disabled */
    TickType_t          wdt_last_kick_tick;

    /* Phase 22: static stack allocation — gives us exact bounds for the
     * app-writable pointer validator.  We own the stack memory so we know
     * [stack_mem, stack_mem+stack_size) is a valid write target.           */
    uint8_t            *stack_mem;       /* heap_caps_malloc'd stack buffer */
    size_t              stack_size;      /* bytes (== manifest stack_size or default) */
    StaticTask_t        tcb;             /* FreeRTOS TCB — must outlive the task */
} app_slot_t;

typedef struct {
    TaskHandle_t task;
    int          code;
    bool         forced; /* if true, supervisor must vTaskDelete before unload */
} exit_msg_t;

/* ----- state ------------------------------------------------------------- */

static app_slot_t        s_slots[DUNEOS_MAX_RUNNING_APPS];
static QueueHandle_t     s_exit_queue;
static SemaphoreHandle_t s_all_done;
/* Counting semaphore — signalled once per app exit handled by supervisor_task.
 * Lets callers block on "an app has exited" without spinning on running_count.
 * Used by duneos_supervisor_wait_for_completion(). */
static SemaphoreHandle_t s_exit_event;
static SemaphoreHandle_t s_lock;
static int               s_active_count = 0;
/* Counts slots in the unload→relaunch window to prevent spurious all_done */
static int               s_want_restart = 0;
static TimerHandle_t     s_wdt_timer;

/* Phase 25.4: exit observer callback (init.c registers this for `after:`). */
static duneos_exit_observer_fn s_exit_observer = NULL;

/* Minimum pool size accepted by multi_heap_register (internal requirement) */
#define MULTI_HEAP_MIN_SIZE  64u

/* Phase 24.7 circuit breaker defaults — used when init.yaml doesn't set
 * per-service overrides. Stops a crashing service from looping forever. */
#define DUNEOS_BREAKER_DEFAULT_MAX     3u      /* crashes allowed in window */
#define DUNEOS_BREAKER_DEFAULT_MS  30000u      /* window length in ms */

/* WDT check interval for the software timer */
#define DUNEOS_WDT_CHECK_MS  500u

/* ----- slot helpers ------------------------------------------------------ */

/* Lock-free scan: safe from ISR/exception context (O(4), read-only). */
static app_slot_t *slot_by_task_unsafe(TaskHandle_t t)
{
    for (int i = 0; i < DUNEOS_MAX_RUNNING_APPS; i++) {
        if (s_slots[i].task == t) return &s_slots[i];
    }
    return NULL;
}

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

/* ----- forward declarations ---------------------------------------------- */

#ifdef CONFIG_IDF_TARGET_ARCH_XTENSA
static void register_exc_handlers(void *arg);
#endif

/* ----- app task wrapper -------------------------------------------------- */

static void app_task_entry(void *arg)
{
    app_slot_t *slot = (app_slot_t *)arg;

    /* Self-assign slot->task at the earliest point. The launcher used to
     * write slot->task = handle AFTER xTaskCreate, but a fast-exiting app
     * (e.g. test_exit calling duneos_exit(0) immediately) could race ahead
     * and deliver its exit_msg to supervisor_task before the launcher's
     * assignment ran. slot_by_task(msg.task) then returned NULL ("stale
     * exit"), the supervisor skipped the decrement, s_active_count stayed
     * inflated, and any waiter (e.g. shell's wait_for_completion) hung
     * forever. Writing here closes the race because supervisor_task can
     * only observe an exit_msg AFTER this task has run at least once. */
    slot->task = xTaskGetCurrentTaskHandle();

#ifdef CONFIG_IDF_TARGET_ARCH_XTENSA
    /* Ensure exception handlers are installed on whichever core this task
     * landed on.  _xtos_set_exception_handler is per-core, so this must run
     * on the actual executing core before any app code runs. */
    register_exc_handlers(NULL);
#endif

    klog_d(TAG, "starting '%s'", slot->name);
    slot->crashed = false;
    s_loader_ops.run(slot->app);
    /* app_main returned without calling duneos_exit — treat as exit(0).
     * duneos_supervisor_app_exited deletes the task before returning, so
     * the exec pool is never accessed again after this call. */
    duneos_supervisor_app_exited(0);
}

/* ----- Phase 20: Xtensa CPU exception handler ---------------------------- */

#ifdef CONFIG_IDF_TARGET_ARCH_XTENSA

static xt_exc_handler s_orig_exc[XCHAL_EXCCAUSE_NUM];

/* Causes we intercept for app recovery. EXCCAUSE_INSTR_PROHIBITED catches
 * the case where an app jumps to address 0 (call through NULL function
 * pointer) — the most common failure mode for apps with unresolved-symbol
 * relocations the loader left at NULL. Without it, that scenario panics
 * the kernel instead of just killing the offending app. */
static const int s_exc_causes[] = {
    EXCCAUSE_ILLEGAL,
    EXCCAUSE_DIVIDE_BY_ZERO,
    EXCCAUSE_LOAD_PROHIBITED,
    EXCCAUSE_STORE_PROHIBITED,
    EXCCAUSE_INSTR_PROHIBITED,
};
#define NUM_EXC_CAUSES  ((int)(sizeof(s_exc_causes)/sizeof(s_exc_causes[0])))

/* Landing pad after CPU exception recovery.
 * The exception handler redirects frame->pc here before returning so that rfe
 * resumes at this function with normal PS (INTLEVEL=0, no EXCM, task context).
 *
 * We notify the supervisor via xQueueSend (not xQueueSendFromISR) because the
 * exception fired at INTLEVEL=0 — we are in task context without the kernel
 * lock.  On SMP, calling xQueueSendFromISR from the exception handler (which
 * only masks interrupts via INTLEVEL, not via the kernel spinlock) races with
 * xTaskIncrementTick on the other core, which holds the kernel lock while
 * iterating the delayed-task list.  That race leaves uxNumberOfItems=1 with
 * self-referential list pointers, causing the next tick to read a phantom TCB
 * and crash with LoadProhibited. */
static void safe_app_exit(void)
{
    duneos_supervisor_app_exited(DUNEOS_EXIT_CRASHED);
}

static void app_exception_handler(XtExcFrame *frame)
{
    TaskHandle_t t    = xTaskGetCurrentTaskHandle();
    app_slot_t  *slot = slot_by_task_unsafe(t);

    /* frame->ps bits 0-3 = PS.INTLEVEL.  Non-zero means the exception fired
     * inside an ISR or critical section; we cannot safely redirect frame->pc
     * because rfe would resume the wrong stack.  Fall through to the original
     * panic handler so the kernel reboots cleanly in that case. */
    bool in_task_context = ((frame->ps & 0xFu) == 0u);

    if (slot && slot->active && !slot->crashed && in_task_context) {
        slot->crashed   = true;
        slot->exc_cause = frame->exccause;
        slot->exc_pc    = frame->pc;
        esp_rom_printf("[KERN] app '%s' exception cause=%lu PC=0x%08lx — killing\n",
                       slot->name,
                       (unsigned long)frame->exccause,
                       (unsigned long)frame->pc);
        /* Redirect to safe_app_exit; supervisor notification happens there from
         * task context with proper FreeRTOS kernel-lock semantics. */
        frame->pc = (uint32_t)safe_app_exit;
        return;
    }

    /* Kernel task or unrecognised — call the original handler (panic/reboot) */
    int cause = (int)frame->exccause;
    if (cause >= 0 && cause < XCHAL_EXCCAUSE_NUM && s_orig_exc[cause])
        s_orig_exc[cause](frame);
    esp_system_abort("unhandled kernel CPU exception");
}

/* Per-core flag: true once handlers have been installed on that core. */
static bool s_exc_registered[portNUM_PROCESSORS];

/* Register our exception handlers on the calling core.
 * Safe to call multiple times: idempotent after the first registration.
 * s_orig_exc[] is updated only the first time a given cause is registered
 * (i.e. when prev != app_exception_handler), so the real fallback handler
 * is preserved regardless of which core registers first. */
static void register_exc_handlers(void *arg)
{
    (void)arg;
    int core = (int)xPortGetCoreID();
    if (s_exc_registered[core]) return;
    s_exc_registered[core] = true;

    for (int i = 0; i < NUM_EXC_CAUSES; i++) {
        int c = s_exc_causes[i];
        xt_exc_handler prev = xt_set_exception_handler(c, app_exception_handler);
        /* Only save when prev is not already our handler: preserves the real
         * original (ESP-IDF panic) even if called multiple times. */
        if (prev != app_exception_handler)
            s_orig_exc[c] = prev;
    }
}

#endif /* CONFIG_IDF_TARGET_ARCH_XTENSA */

/* ----- Phase 20: FreeRTOS stack overflow hook ---------------------------- */

/* Called from ISR (tick) context when FreeRTOS detects stack canary corruption.
 * We post a forced exit to the supervisor queue so it can vTaskDelete the
 * offending task and free its resources.  Kernel tasks abort normally. */
void vApplicationStackOverflowHook(TaskHandle_t task, char *name)
{
    app_slot_t *slot = slot_by_task_unsafe(task);
    if (slot && slot->active && !slot->crashed) {
        slot->crashed = true;
        esp_rom_printf("[KERN] stack overflow: app '%s' (task '%s') — killing\n",
                       slot->name, name);
        BaseType_t woken = pdFALSE;
        exit_msg_t msg   = { .task = task, .code = DUNEOS_EXIT_STACK_OVF,
                             .forced = true };
        xQueueSendFromISR(s_exit_queue, &msg, &woken);
        portYIELD_FROM_ISR(woken);
        return;
    }
    esp_system_abort("kernel task stack overflow");
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

static void slot_heap_free(app_slot_t *slot)
{
    if (slot->heap_pool_buf) {
        if (slot->heap_handle) {
            bool pool_ok = multi_heap_check(slot->heap_handle, false);
            if (!pool_ok)
                klog_e(TAG, "slot_heap_free: per-app heap corrupted before free!");
        }
        heap_caps_free(slot->heap_pool_buf);
        slot->heap_pool_buf  = NULL;
        slot->heap_handle    = NULL;
        slot->heap_pool_size = 0;
    }
}

/* ----- WDT timer callback ------------------------------------------------ */

/* Runs in the FreeRTOS timer-daemon task context (a proper task, not an ISR).
 * Using a timer here — instead of blocking the supervisor on a finite-timeout
 * xQueueReceive — keeps the supervisor task out of the FreeRTOS delayed-task
 * list.  If the supervisor used xQueueReceive(500ms) it would be inserted into
 * xDelayedTaskList every cycle; hardware ISRs (SPI DMA, keyboard scan) that
 * call xQueueSendFromISR concurrently with xTaskIncrementTick on the other
 * core can race on that list without holding the kernel spinlock, corrupting
 * uxNumberOfItems and crashing with LoadProhibited inside xTaskIncrementTick. */
static void wdt_timer_cb(TimerHandle_t timer)
{
    (void)timer;
    TickType_t now = xTaskGetTickCount();
    for (int i = 0; i < DUNEOS_MAX_RUNNING_APPS; i++) {
        app_slot_t *s = &s_slots[i];
        if (!s->active || !s->wdt_timeout_ticks || s->crashed) continue;
        if ((now - s->wdt_last_kick_tick) >= s->wdt_timeout_ticks) {
            s->crashed = true;
            esp_rom_printf("[KERN] WDT: app '%s' timeout\n", s->name);
            exit_msg_t msg = { .task = s->task, .code = DUNEOS_EXIT_WDT, .forced = true };
            xQueueSend(s_exit_queue, &msg, 0);
        }
    }
}

/* ----- supervisor task --------------------------------------------------- */

static void supervisor_task(void *arg)
{
    (void)arg;
    exit_msg_t msg;

    while (1) {
        xQueueReceive(s_exit_queue, &msg, portMAX_DELAY);

        xSemaphoreTake(s_lock, portMAX_DELAY);

        app_slot_t *slot = slot_by_task(msg.task);
        if (!slot) {
            xSemaphoreGive(s_lock);
            /* Expected after WDT/crash: supervisor already cleared the slot for
             * a forced exit, then the app's own duneos_exit() message arrives. */
            klog_d(TAG, "stale exit (task=%p code=%d forced=%d) — ignoring",
                   msg.task, msg.code, msg.forced);
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

        bool should_restart = slot->force_restart ||
                              (policy == DUNEOS_RESTART_ALWAYS) ||
                              (policy == DUNEOS_RESTART_ON_FAILURE && code != 0);

        /* Phase 24.7 circuit breaker: if this service is restarting,
         * count it against the recent-crashes window. Trip the breaker
         * once it crosses the threshold; do not restart again until the
         * service is manually re-launched.                              */
        bool breaker_tripping = false;
        if (should_restart) {
            uint32_t now_ms   = (uint32_t)(xTaskGetTickCount() * portTICK_PERIOD_MS);
            uint32_t max_c    = slot->breaker_max_crashes ? slot->breaker_max_crashes
                                                          : DUNEOS_BREAKER_DEFAULT_MAX;
            uint32_t window   = slot->breaker_window_ms   ? slot->breaker_window_ms
                                                          : DUNEOS_BREAKER_DEFAULT_MS;

            if (slot->breaker_crashes_in_window == 0 ||
                (now_ms - slot->breaker_window_start_ms) > window) {
                /* First crash, or last window expired → start a new window. */
                slot->breaker_window_start_ms   = now_ms;
                slot->breaker_crashes_in_window = 1;
            } else {
                slot->breaker_crashes_in_window++;
            }

            if (slot->breaker_crashes_in_window > max_c) {
                slot->breaker_tripped = true;
                should_restart        = false;
                breaker_tripping      = true;
            }
        }

        /* Kill the task before releasing the lock so we stop its execution
         * before unloading its code. Only needed for forced exits (stack
         * overflow, WDT, external restart) — in the normal path the app task
         * already deleted itself via vTaskDelete(NULL). */
        if (msg.forced) vTaskDelete(msg.task);

        slot->force_restart      = false;
        slot->app                = NULL;
        slot->mailbox            = NULL;
        slot->task               = NULL;
        slot->active             = false;
        slot->wdt_timeout_ticks  = 0;   /* prevent stale WDT fire during reload */
        s_active_count--;
        if (should_restart) {
            slot->restart_count++;
            s_want_restart++;
        }

        xSemaphoreGive(s_lock);

        /* klog_e is forwarded to ESP_LOGE → visible on serial console */
        klog_e(TAG, "'%s' exited (code %d)%s", name, code,
               should_restart ? " — restarting"
                              : (breaker_tripping
                                    ? " — circuit breaker tripped, restart disabled"
                                    : ""));

        /* Free the exiting app FIRST, so by the time the observer launches
         * dependent services (Phase 25.4 `after:`) the slot is fully free
         * and any kernel resources the app held (SPI handles, GPIO claims,
         * heap pool) have been released. Otherwise a dependent service can
         * race the unloading app for the same device.
         *
         * The 1-tick yield (~10 ms) between the heap operations is empirically
         * required: without it, the supervisor task at priority 3 monopolises
         * Core 0 across unload + free + free + observer-launch-×N and the
         * device hard-reboots. We never saw the panic message — symptom is
         * a clean reset right after the exiting app's "exited (code N)" log.
         * Hypothesis: FreeRTOS task-termination housekeeping (IDLE-driven for
         * statically-allocated tasks) needs a scheduling opportunity before
         * the next heap free or xTaskCreate. Backlog: "Kernel resilience
         * under app-crash storms" — find the root cause and remove the yield. */
        s_loader_ops.unload(app);
        vTaskDelay(1);
        slot_heap_free(slot);           /* free per-app heap pool */
        vTaskDelay(1);
        /* Phase 22: free the static stack buffer after the task is deleted.
         * vTaskDelete (called above for forced exits, or by the task itself)
         * guarantees the TCB is no longer referenced by FreeRTOS before the
         * supervisor processes the exit message. */
        if (slot->stack_mem) {
            heap_caps_free(slot->stack_mem);
            slot->stack_mem  = NULL;
            slot->stack_size = 0;
            vTaskDelay(1);
        }
        if (mailbox) {
            vQueueDelete(mailbox);
            vTaskDelay(1);
        }

        if (should_restart) {
            /* Relaunch inherits same policy so restarts persist */
            esp_err_t rerr = duneos_supervisor_launch_policy(rpath, policy);
            if (rerr != ESP_OK)
                klog_e(TAG, "'%s' relaunch FAILED (err %d) — service will not restart",
                       name, rerr);
            xSemaphoreTake(s_lock, portMAX_DELAY);
            s_want_restart--;
            xSemaphoreGive(s_lock);
        }

        /* Phase 25.4: notify registered observers (init.c uses this for the
         * `after:` dependency mechanism). Called without holding s_lock and
         * AFTER unload so dependent services see a clean state. */
        if (s_exit_observer) s_exit_observer(name, code);

        maybe_signal_all_done();
        /* Signal callers waiting for any app to exit (e.g. shell `run`). */
        xSemaphoreGive(s_exit_event);
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

    /* Counting semaphore: one give() per app exit handled by supervisor_task.
     * Capacity sized to absorb back-to-back exits without dropping events. */
    s_exit_event = xSemaphoreCreateCounting(DUNEOS_MAX_RUNNING_APPS * 4, 0);
    if (!s_exit_event) return ESP_ERR_NO_MEM;

    /* 24 KB: supervisor calls duneos_loader_load on restart, which chains
     * fopen → FatFS → SPI DMA — a very deep call stack. 16 KB was tight
     * for a single load; the Phase 25.4 `after:` observer chains MULTIPLE
     * loads in sequence (one per deferred service released by the same
     * exit), so we need more headroom to survive boot-time fan-outs (e.g.
     * splash → usb_shell + kb_iomatrix + g_shell). 4 KB overflows into
     * adjacent BSS and corrupts FreeRTOS list pointers. */
    /* Pin to Core 0: all DuneOS tasks run on Core 0 to avoid cross-core
     * SMP races on FreeRTOS ready/delayed lists without kernel spinlock. */
    BaseType_t ret = xTaskCreatePinnedToCore(supervisor_task, "duneos_sv",
                                              24576, NULL,
                                              DUNEOS_APP_TASK_PRIORITY + 1,
                                              NULL, 0);
    if (ret != pdPASS) return ESP_ERR_NO_MEM;

#ifdef CONFIG_IDF_TARGET_ARCH_XTENSA
    /* Register on Core 0 now so kernel-task exceptions are caught before any
     * app launches.  Each app_task_entry() also calls register_exc_handlers()
     * on whichever core FreeRTOS assigns it — that's the true guarantee for
     * app recovery; this call merely covers the init window. */
    register_exc_handlers(NULL);
#endif

    /* Software WDT timer: fires every 500 ms from the FreeRTOS timer-daemon
     * task.  Using a timer (not a finite xQueueReceive timeout) keeps the
     * supervisor task out of the FreeRTOS delayed-task list, which prevents
     * a race with xQueueSendFromISR callers that lack the kernel spinlock. */
    s_wdt_timer = xTimerCreate("sv_wdt",
                               pdMS_TO_TICKS(DUNEOS_WDT_CHECK_MS),
                               pdTRUE,        /* auto-reload */
                               NULL,
                               wdt_timer_cb);
    if (!s_wdt_timer) return ESP_ERR_NO_MEM;
    xTimerStart(s_wdt_timer, 0);

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
    /* Pre-mark active to prevent a concurrent launch from grabbing this slot.
     * Name the slot NOW from the path basename — the manifest name only lands
     * after the (slower) load below, but the exit observer's `after:` matching
     * and the `services` listing need a stable, non-blank name for the whole
     * time the slot is active. Without this, an app that exits quickly (e.g.
     * splash) can be observed with a blank name, so `after: splash` never
     * matches and the dependent service is never released.
     * Stash the previous occupant's name first so the circuit-breaker's
     * same-app check below keeps comparing against the right thing. */
    slot->active = true;
    char prev_name[DUNEOS_APP_NAME_MAX];
    strlcpy(prev_name, slot->name, sizeof(prev_name));
    {
        const char *base = strrchr(path, '/');
        base = base ? base + 1 : path;
        size_t n = 0;
        while (base[n] && base[n] != '.' && n + 1 < sizeof(slot->name)) {
            slot->name[n] = base[n];
            n++;
        }
        slot->name[n] = '\0';
    }
    xSemaphoreGive(s_lock);

    duneos_app_t *app = NULL;
    esp_err_t err = s_loader_ops.load(path, &app);
    if (err != ESP_OK) {
        slot->active = false;
        return err;
    }

    const duneos_app_manifest_t *m = s_loader_ops.get_manifest(app);

    /* Phase 24.7 circuit breaker: PRESERVE state across automatic restarts of
     * the same app, but RESET when the slot starts hosting a different app
     * (manual launch of something new). Compare against the existing
     * slot->name BEFORE we overwrite it. */
    bool same_app_as_before = (prev_name[0] != '\0' &&
                               strcmp(prev_name, m->name) == 0);

    strlcpy(slot->name, m->name, sizeof(slot->name));
    strlcpy(slot->restart_path, path, sizeof(slot->restart_path));
    slot->restart_policy = policy;
    slot->app = app;

    if (!same_app_as_before) {
        slot->restart_count             = 0;
        slot->breaker_window_start_ms   = 0;
        slot->breaker_crashes_in_window = 0;
        slot->breaker_max_crashes       = 0;   /* 0 = use DEFAULT_MAX */
        slot->breaker_window_ms         = 0;   /* 0 = use DEFAULT_MS  */
        slot->breaker_tripped           = false;
    }
    /* If it's the same app being restarted by the supervisor loop, leave the
     * breaker fields alone — they're updated in supervisor_task on each exit,
     * and that's the entire point of the breaker. */

    /* Phase 20: per-app heap pool */
    slot->heap_pool_buf  = NULL;
    slot->heap_handle    = NULL;
    slot->heap_pool_size = 0;
    if (m->heap_size >= MULTI_HEAP_MIN_SIZE) {
        slot->heap_pool_buf = heap_caps_malloc(m->heap_size,
                                                MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
        if (slot->heap_pool_buf) {
            slot->heap_pool_size = m->heap_size;
            slot->heap_handle    = multi_heap_register(slot->heap_pool_buf,
                                                        slot->heap_pool_size);
            klog_d(TAG, "'%s' heap pool: %lu B @ %p",
                   slot->name, (unsigned long)m->heap_size, slot->heap_pool_buf);
        } else {
            klog_w(TAG, "'%s' heap pool alloc failed (%lu B) — using global heap",
                   slot->name, (unsigned long)m->heap_size);
        }
    }

    /* Phase 20: record data pool bounds for syscall pointer validation */
    if (s_loader_ops.get_data_pool)
        s_loader_ops.get_data_pool(app, &slot->data_pool_base, &slot->data_pool_size);

    /* Phase 20: software WDT */
    slot->wdt_timeout_ticks = m->wdt_timeout_ms
                              ? pdMS_TO_TICKS(m->wdt_timeout_ms) : 0;
    slot->wdt_last_kick_tick = xTaskGetTickCount();

    slot->mailbox = xQueueCreate(DUNEOS_MAILBOX_DEPTH, sizeof(duneos_msg_t));
    /* Mailbox failure is non-fatal — messaging just won't work */

    uint32_t stack = m->stack_size > 0 ? m->stack_size : DUNEOS_APP_DEFAULT_STACK;

    /* Phase 22: allocate the task stack ourselves so we know its exact bounds.
     * xTaskCreateStaticPinnedToCore requires an external stack buffer and a
     * StaticTask_t (TCB) that both outlive the task — both live in app_slot_t.
     * Aligning to 16 bytes satisfies Xtensa's CALL0/CALL8 ABI requirements. */
    slot->stack_mem  = heap_caps_aligned_alloc(16, stack,
                                                MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    slot->stack_size = stack;
    if (!slot->stack_mem) {
        s_loader_ops.unload(app);
        slot_heap_free(slot);
        if (slot->mailbox) vQueueDelete(slot->mailbox);
        slot->active = false;
        klog_e(TAG, "stack alloc failed for '%s' (%lu B)", slot->name, (unsigned long)stack);
        return ESP_ERR_NO_MEM;
    }

    /* Increment before xTaskCreate: the new task may exit and the supervisor
     * may process the exit before this task resumes after the yield inside
     * xTaskCreate, causing s_active_count to underflow to -1 if we increment
     * after.  Decrement back on task creation failure. */
    xSemaphoreTake(s_lock, portMAX_DELAY);
    s_active_count++;
    xSemaphoreGive(s_lock);

    /* IDF v6+: stack depth is in BYTES. StaticTask_t lives in slot->tcb and
     * must remain valid until the task is deleted. */
    TaskHandle_t handle = xTaskCreateStaticPinnedToCore(
        app_task_entry, slot->name,
        stack, slot,
        DUNEOS_APP_TASK_PRIORITY,
        (StackType_t *)slot->stack_mem,
        &slot->tcb,
        0);
    if (!handle) {
        xSemaphoreTake(s_lock, portMAX_DELAY);
        s_active_count--;
        xSemaphoreGive(s_lock);
        s_loader_ops.unload(app);
        slot_heap_free(slot);
        heap_caps_free(slot->stack_mem);
        slot->stack_mem = NULL;
        if (slot->mailbox) vQueueDelete(slot->mailbox);
        slot->active = false;
        klog_e(TAG, "xTaskCreateStatic failed for '%s'", slot->name);
        return ESP_ERR_NO_MEM;
    }
    /* slot->task is now self-assigned by app_task_entry on its first
     * instruction (see comment there). Assigning it here would create a
     * race where a fast-exiting app delivers its exit_msg before this
     * assignment runs, leaving slot_by_task() returning NULL.            */
    (void)handle;

    klog_d(TAG, "launched '%s' (stack %lu B, heap %lu B, wdt %lu ms, restart=%d)",
           slot->name, (unsigned long)stack,
           (unsigned long)m->heap_size, (unsigned long)m->wdt_timeout_ms,
           (int)policy);
    return ESP_OK;
}

esp_err_t duneos_supervisor_launch(const char *path)
{
    return duneos_supervisor_launch_policy(path, DUNEOS_RESTART_NO);
}

bool duneos_supervisor_captured_active(void)
{
    return s_loader_ops.captured_active && s_loader_ops.captured_active();
}

void duneos_exit(int code)
{
    /* ADR 016: if the current task is running a captured app, unwind via
     * longjmp back to duneos_loader_run_captured's setjmp checkpoint
     * instead of deleting the task (which would kill the shell). The
     * loader registers captured_active+captured_longjmp callbacks at
     * init time; older builds without them fall through to the
     * spawned-mode path harmlessly. */
    if (duneos_supervisor_captured_active()) {
        s_loader_ops.captured_longjmp(code);   /* does not return */
    }

    duneos_supervisor_app_exited(code);
    /* duneos_supervisor_app_exited calls vTaskDelete(NULL) and never returns,
     * but the compiler cannot see that without the noreturn attribute on the
     * FreeRTOS function.  The loop is unreachable but satisfies noreturn. */
    while (1) {}
}

void duneos_supervisor_app_exited(int code)
{
    exit_msg_t msg = {
        .task   = xTaskGetCurrentTaskHandle(),
        .code   = code,
        .forced = false,
    };
    xQueueSend(s_exit_queue, &msg, portMAX_DELAY);
    /* Delete ourselves immediately so the scheduler never resumes this task
     * in app code after the supervisor has processed the exit message and
     * potentially unloaded (freed) the exec pool.  On SMP the supervisor
     * can run on Core 1 between the xQueueSend and our return, making the
     * app's return-from-duneos_exit execute freed/reused code. */
    vTaskDelete(NULL);
    while (1) {} /* unreachable — prevents compiler from assuming we return */
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

void duneos_supervisor_set_exit_observer(duneos_exit_observer_fn fn)
{
    s_exit_observer = fn;
}

int duneos_supervisor_running_count(void)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    int n = s_active_count;
    xSemaphoreGive(s_lock);
    return n;
}

/* Block until s_active_count drops to <= target_count.
 *
 * Replaces the historical busy-wait pattern
 *   while (duneos_supervisor_running_count() > target) usleep(100000);
 * which hung intermittently — the shell task at app priority shared CPU
 * with the spawned app at the same priority, and the supervisor's exit
 * processing could be starved long enough that the shell never observed
 * the count drop within its polling window.
 *
 * This version blocks on a counting semaphore signalled exactly once per
 * processed exit. Each wake-up rechecks the count; if still above target,
 * it sleeps again. No busy-wait, no missed events.                       */
void duneos_supervisor_wait_for_completion(int target_count)
{
    for (;;) {
        if (duneos_supervisor_running_count() <= target_count) return;
        xSemaphoreTake(s_exit_event, portMAX_DELAY);
    }
}

int duneos_supervisor_list_slots(duneos_slot_info_t *out, int count)
{
    if (!out || count <= 0) return 0;
    int n = count < DUNEOS_MAX_RUNNING_APPS ? count : DUNEOS_MAX_RUNNING_APPS;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    for (int i = 0; i < n; i++) {
        out[i].active        = s_slots[i].active;
        out[i].restart_policy= s_slots[i].restart_policy;
        out[i].restart_count = s_slots[i].restart_count;
        strlcpy(out[i].name, s_slots[i].active ? s_slots[i].name : "",
                sizeof(out[i].name));
    }
    xSemaphoreGive(s_lock);
    return n;
}

int duneos_supervisor_restart_by_name(const char *name)
{
    if (!name) return -1;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    app_slot_t *slot = slot_by_name(name);
    if (!slot) {
        xSemaphoreGive(s_lock);
        return -1;
    }
    slot->force_restart = true;
    TaskHandle_t task = slot->task;
    xSemaphoreGive(s_lock);

    /* Post a forced exit on behalf of the target task. The supervisor task
     * will vTaskDelete it (to stop execution before unloading its code)
     * then relaunch it. */
    exit_msg_t msg = { .task = task, .code = 0, .forced = true };
    xQueueSend(s_exit_queue, &msg, portMAX_DELAY);
    return 0;
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

/* ----- Phase 20: per-app memory API ------------------------------------- */

void *duneos_supervisor_app_malloc(size_t size)
{
    app_slot_t *slot = slot_by_task_unsafe(xTaskGetCurrentTaskHandle());
    if (slot && slot->active && slot->heap_handle)
        return multi_heap_malloc(slot->heap_handle, size);
    return heap_caps_malloc(size, MALLOC_CAP_DEFAULT);
}

void *duneos_supervisor_app_realloc(void *ptr, size_t size)
{
    app_slot_t *slot = slot_by_task_unsafe(xTaskGetCurrentTaskHandle());
    if (slot && slot->heap_handle && slot->heap_pool_buf && ptr) {
        uintptr_t p    = (uintptr_t)ptr;
        uintptr_t base = (uintptr_t)slot->heap_pool_buf;
        if (p >= base && p < base + slot->heap_pool_size)
            return multi_heap_realloc(slot->heap_handle, ptr, size);
    }
    return heap_caps_realloc(ptr, size, MALLOC_CAP_DEFAULT);
}

void *duneos_supervisor_app_calloc(size_t n, size_t size)
{
    app_slot_t *slot = slot_by_task_unsafe(xTaskGetCurrentTaskHandle());
    if (slot && slot->active && slot->heap_handle) {
        void *p = multi_heap_malloc(slot->heap_handle, n * size);
        if (p) memset(p, 0, n * size);
        return p;
    }
    return heap_caps_calloc(n, size, MALLOC_CAP_DEFAULT);
}

void duneos_supervisor_app_free(void *ptr)
{
    if (!ptr) return;
    app_slot_t *slot = slot_by_task_unsafe(xTaskGetCurrentTaskHandle());
    if (slot && slot->heap_handle && slot->heap_pool_buf) {
        uintptr_t p    = (uintptr_t)ptr;
        uintptr_t base = (uintptr_t)slot->heap_pool_buf;
        if (p >= base && p < base + slot->heap_pool_size) {
            multi_heap_free(slot->heap_handle, ptr);
            return;
        }
    }
    heap_caps_free(ptr);
}

bool duneos_supervisor_check_user_ptr(const void *ptr, size_t len)
{
    if (!ptr) return false;
    uintptr_t p = (uintptr_t)ptr;

    /* Reject the NULL page, peripheral registers, and Xtensa IRAM.
     * This is the permissive path for read-source buffers: the pointer may
     * legitimately point into kernel-returned string data (e.g. strerror). */
    if (p < 0x1000u)                              return false;
    if (p >= 0x60000000u && p < 0x70000000u)      return false;
    /* ESP32-S3 IRAM: 0x40370000-0x403DFFFF — app must not pass code addrs  */
    if (p >= 0x40370000u && p < 0x403E0000u)      return false;

    /* Guard against pointer + len wrapping around the address space          */
    if (len > 0 && p + len < p)                   return false;

    return true;
}

bool duneos_supervisor_check_app_writable_ptr(const void *ptr, size_t len)
{
    /* Strict check: the pointer must be within one of the app's own memory
     * regions.  Used for read() targets — the kernel writes into this buffer
     * so it MUST be app-owned, writable memory.
     *
     * Valid regions for the calling slot:
     *   [data_pool_base, data_pool_base + data_pool_size)  — rodata/data/bss
     *   [heap_pool_buf,  heap_pool_buf  + heap_pool_size)  — per-app heap
     *   [stack_mem,      stack_mem      + stack_size)       — task stack
     *
     * Falls back to the permissive check when called from a non-app task
     * (should not happen, but avoids a hard failure during early boot).     */
    if (!ptr) return false;
    uintptr_t p    = (uintptr_t)ptr;
    uintptr_t pend = p + len;

    if (p < 0x1000u)                              return false;
    if (p >= 0x60000000u && p < 0x70000000u)      return false;
    if (len > 0 && pend < p)                      return false;  /* overflow */

    app_slot_t *slot = slot_by_task_unsafe(xTaskGetCurrentTaskHandle());
    if (!slot || !slot->active) {
        /* Non-app task — apply the permissive check only */
        return duneos_supervisor_check_user_ptr(ptr, len);
    }

    /* data pool: rodata + data + bss sections */
    if (slot->data_pool_base && slot->data_pool_size) {
        uintptr_t base = slot->data_pool_base;
        uintptr_t end  = base + slot->data_pool_size;
        if (p >= base && (len == 0 || pend <= end)) return true;
    }

    /* per-app heap pool */
    if (slot->heap_pool_buf) {
        uintptr_t base = (uintptr_t)slot->heap_pool_buf;
        uintptr_t end  = base + slot->heap_pool_size;
        if (p >= base && (len == 0 || pend <= end)) return true;
    }

    /* task stack (Phase 22: known because we allocate it statically) */
    if (slot->stack_mem) {
        uintptr_t base = (uintptr_t)slot->stack_mem;
        uintptr_t end  = base + slot->stack_size;
        if (p >= base && (len == 0 || pend <= end)) return true;
    }

    klog_w("supervisor",
           "syscall: ptr 0x%08lx+%zu outside app '%s' bounds — EFAULT",
           (unsigned long)p, len, slot->name);
    return false;
}

void duneos_supervisor_wdt_reset(void)
{
    app_slot_t *slot = slot_by_task_unsafe(xTaskGetCurrentTaskHandle());
    if (slot && slot->active)
        slot->wdt_last_kick_tick = xTaskGetTickCount();
}
