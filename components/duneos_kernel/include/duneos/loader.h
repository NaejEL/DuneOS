#pragma once

#include <stdint.h>
#include "esp_err.h"
#include "duneos/abi.h"

/*
 * ELF loader — Phase 3 core.
 *
 * Loads ET_REL (relocatable object) ELF files from the SD card into PSRAM,
 * applies Xtensa relocations, resolves external symbols against the kernel
 * export table, and returns a handle to the loaded app.
 *
 * The loaded app's sections live in PSRAM:
 *   .text   — executable code  (heap_caps_malloc MALLOC_CAP_SPIRAM | EXEC)
 *   .data   — initialised data (heap_caps_malloc MALLOC_CAP_SPIRAM)
 *   .bss    — zero-initialised (heap_caps_malloc + memset — NOT copied from ELF)
 *   .rodata — read-only data   (heap_caps_malloc MALLOC_CAP_SPIRAM)
 */

typedef struct duneos_app duneos_app_t;

/*
 * Load an ELF from path (e.g. "/sd/apps/hello_world.elf").
 * On success, *out_app is a valid handle that must be freed with
 * duneos_loader_unload(). On failure, *out_app is NULL.
 */
esp_err_t duneos_loader_load(const char *path, duneos_app_t **out_app);

/* Retrieve the parsed manifest without launching the app */
const duneos_app_manifest_t *duneos_loader_get_manifest(const duneos_app_t *app);

/*
 * Jump to app_main(void) inside the loaded app.
 * Blocks until the app calls duneos_exit() or crashes.
 * The caller's task becomes the app supervisor for this run.
 */
esp_err_t duneos_loader_run(duneos_app_t *app);

/* Free all PSRAM sections and metadata for a loaded app */
void duneos_loader_unload(duneos_app_t *app);
