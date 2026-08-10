#pragma once

#include <stdint.h>
#include "esp_err.h"
#include "duneos/abi.h"
#include "duneos/shellpipe.h"   /* duneos_shell_sink_fn */

/*
 * DuneOS ELF loader.
 *
 * Apps are ET_REL ELF files dropped in /sd/apps/. There is no separate
 * manifest file on the filesystem — each ELF embeds its own manifest as
 * a JSON string in the DUNEOS_MANIFEST_SECTION ELF section.
 *
 * Typical boot sequence:
 *   1. duneos_loader_scan()  — discover all apps on SD, read their manifests
 *   2. duneos_loader_load()  — load selected ELF into PSRAM, apply relocations
 *   3. duneos_loader_run()   — jump to app_main (blocks until duneos_exit())
 *   4. duneos_loader_unload() — free PSRAM
 */

/* -------------------------------------------------------------------------
 * App discovery
 * ---------------------------------------------------------------------- */

#define DUNEOS_APPS_DIR         "/sd/apps"
#define DUNEOS_AUTOBOOT_FILE    "/sd/autoboot"   /* contains app name, no extension */
#define DUNEOS_MAX_APPS         32

/*
 * Lightweight app descriptor built during scan.
 * The ELF is not loaded into PSRAM at this stage — only the manifest
 * section is read from the file to populate meta.
 */
typedef struct {
    char                  path[272];   /* DUNEOS_APPS_DIR + '/' + NAME_MAX + NUL */
    duneos_app_manifest_t meta;
} duneos_app_info_t;

/*
 * Scan DUNEOS_APPS_DIR for *.elf files.
 * For each file, open it, extract the embedded manifest, and populate
 * an entry in list[]. At most max entries are filled.
 * *found receives the number of valid entries written.
 *
 * Files whose manifest cannot be read (bad ELF, missing section, ABI
 * mismatch) are skipped with a warning log — not a fatal error.
 */
esp_err_t duneos_loader_scan(duneos_app_info_t *list, int max, int *found);

/*
 * Read /sd/autoboot and return the app info for that name, or NULL.
 * Falls back to list[0] if the file is absent or the name is not found.
 */
const duneos_app_info_t *duneos_loader_select(const duneos_app_info_t *list,
                                               int count);

/* -------------------------------------------------------------------------
 * App loading and execution
 * ---------------------------------------------------------------------- */

/* duneos_app_t is declared in duneos/abi.h; defined (struct body) in loader.c */

/* Register loader ops with the supervisor. Call once before supervisor_launch. */
void duneos_loader_init(void);

/*
 * Load an ELF from path into PSRAM and apply relocations.
 * On success *out_app is valid and must be freed with duneos_loader_unload().
 */
esp_err_t duneos_loader_load(const char *path, duneos_app_t **out_app);

/* Jump to app_main. Blocks until the app calls duneos_exit(). */
esp_err_t duneos_loader_run(duneos_app_t *app);

/*
 * Like duneos_loader_run() but captures the app's stdout into a heap buffer.
 * On success, *out_buf points to a NUL-terminated string that the caller must
 * free(), and *out_len receives the byte count (excluding the NUL).
 *
 * Stdout is redirected to /tmp/.duneos_stdout before the app runs and
 * restored via dup2 afterwards, so kernel log output (which goes to the
 * hardware UART, not fd 1) is unaffected.
 *
 * Captured-mode exit semantics (ADR 016): if the app calls duneos_exit(N)
 * the loader installs a setjmp checkpoint and longjmps back here, so the
 * shell task survives a captured-app exit. Use
 * duneos_loader_get_captured_exit_code() right after this call to read
 * the exit code (0 = clean / app returned without calling duneos_exit;
 * non-zero = value passed to duneos_exit).
 *
 * Returns ESP_ERR_NOT_SUPPORTED if dup/dup2 are unavailable on this build,
 * ESP_ERR_INVALID_STATE if called recursively from inside a captured app.
 */
esp_err_t duneos_loader_run_captured(duneos_app_t *app,
                                      char **out_buf, size_t *out_len);

/*
 * Like duneos_loader_run_captured() but streams the app's stdout: every write
 * the app makes is handed to `sink` as it happens, in the caller's task. No
 * /tmp spool, no whole-output buffer — so output is unbounded by heap, which a
 * captured `cat` of a large file needs. Same setjmp/exit semantics (ADR 016);
 * read the exit code with duneos_loader_get_captured_exit_code() afterwards.
 *
 * `sink` runs while the app holds fd 1, and may re-enter the VFS (e.g. write to
 * the console). Pass the shell's chunk writer + its context.
 */
esp_err_t duneos_loader_run_captured_streamed(duneos_app_t *app,
                                              duneos_shell_sink_fn sink, void *ctx);

/*
 * ADR 016 captured-mode handshake — called by the kernel's duneos_exit()
 * to detect a captured app and unwind via longjmp instead of deleting the
 * calling task. Apps never call these directly.
 *
 *   duneos_loader_captured_active()        — true while a captured app is
 *                                            running on the current task.
 *   duneos_loader_captured_longjmp(code)   — unwind to the setjmp set by
 *                                            duneos_loader_run_captured;
 *                                            does not return.
 *   duneos_loader_get_captured_exit_code() — last captured exit code,
 *                                            valid right after
 *                                            run_captured() returns.
 */
#include <stdbool.h>
bool duneos_loader_captured_active(void);
void duneos_loader_captured_longjmp(int code) __attribute__((noreturn));
int  duneos_loader_get_captured_exit_code(void);

/* Free all PSRAM sections and metadata. */
void duneos_loader_unload(duneos_app_t *app);

/* Return a pointer to the app's parsed manifest (valid until unload). */
const duneos_app_manifest_t *duneos_loader_get_manifest(const duneos_app_t *app);

/*
 * Return the base address and size of the app's monolithic data pool
 * (rodata + data + bss).  Valid until duneos_loader_unload().
 * Used by the supervisor for syscall pointer bounds checking.
 */
void duneos_loader_get_data_pool(const duneos_app_t *app,
                                  uintptr_t *base, size_t *size);
