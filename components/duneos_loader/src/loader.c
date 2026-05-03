#include "duneos/loader.h"

#include <stdlib.h>
#include <string.h>

#include "esp_log.h"
#include "esp_heap_caps.h"

static const char *TAG = "duneos/loader";

/*
 * Internal representation of a loaded application.
 *
 * All pointers below are allocated from PSRAM via heap_caps_malloc.
 * The .bss region is NOT read from the ELF — it is allocated and
 * zero-initialised by the loader (see Phase 3 implementation).
 */
struct duneos_app {
    duneos_app_manifest_t manifest;

    void    *text;      /* .text  — executable code  */
    size_t   text_size;
    void    *data;      /* .data  — initialised data */
    size_t   data_size;
    void    *bss;       /* .bss   — zero-init region (not from ELF image) */
    size_t   bss_size;
    void    *rodata;    /* .rodata */
    size_t   rodata_size;

    void  (*entry)(void);   /* resolved app_main address after relocation */
};

esp_err_t duneos_loader_load(const char *path, duneos_app_t **out_app)
{
    if (!path || !out_app) {
        return ESP_ERR_INVALID_ARG;
    }

    /*
     * TODO Phase 3:
     *  1. Open ELF file from path (POSIX open/read via VFS)
     *  2. Validate ELF magic, e_type == ET_REL, e_machine == EM_XTENSA
     *  3. Parse section headers, locate .text/.data/.bss/.rodata + manifest section
     *  4. Allocate PSRAM regions (heap_caps_malloc MALLOC_CAP_SPIRAM)
     *  5. Copy .text/.data/.rodata from file; zero-init .bss (memset, not from ELF)
     *  6. Parse manifest JSON from manifest section
     *  7. Check manifest.required_abi_version <= DUNEOS_ABI_VERSION
     *  8. Apply relocations (R_XTENSA_32, R_XTENSA_SLOT0_OP, …)
     *  9. Resolve external symbols via duneos_symbol_table_get()
     *     — reject load if app requests symbol not in table (permission check here)
     * 10. Locate app_main symbol, store in app->entry
     */

    ESP_LOGW(TAG, "loader not yet implemented (Phase 3)");
    *out_app = NULL;
    return ESP_ERR_NOT_SUPPORTED;
}

const duneos_app_manifest_t *duneos_loader_get_manifest(const duneos_app_t *app)
{
    if (!app) return NULL;
    return &app->manifest;
}

esp_err_t duneos_loader_run(duneos_app_t *app)
{
    if (!app || !app->entry) {
        return ESP_ERR_INVALID_ARG;
    }

    ESP_LOGI(TAG, "launching app '%s' v%s",
             app->manifest.name, app->manifest.version);

    /*
     * TODO Phase 3: set up per-app exception handler, feed Task WDT,
     * then call app->entry(). Use setjmp/longjmp or task notifications
     * to catch duneos_exit() and return cleanly here.
     */
    app->entry();

    return ESP_OK;
}

void duneos_loader_unload(duneos_app_t *app)
{
    if (!app) return;

    heap_caps_free(app->text);
    heap_caps_free(app->data);
    heap_caps_free(app->bss);
    heap_caps_free(app->rodata);
    free(app);
}
