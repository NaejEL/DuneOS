#pragma once

/*
 * DuneOS SDK — libappmeta: read an app's embedded manifest from a .dap file
 * without loading the app.
 *
 * Each .dap (ET_REL ELF) carries its manifest as a JSON string in the
 * ".duneos_manifest" section (see tools/dbt/builder.py). This helper opens the
 * file, locates that section, and extracts the few fields a launcher cares
 * about — no ELF relocation, no exec-pool allocation, just file reads.
 *
 * Used by the launcher to (a) show the manifest `name` instead of the bare
 * filename and (b) keep the grid to graphical apps (those declaring the
 * `display` capability). Phase 25 extends appmeta_t with the icon asset read
 * from a parallel section.
 */

#include <stdbool.h>

typedef struct {
    char name[32];      /* manifest "name", or "" if absent                 */
    char icon[64];      /* manifest "icon" string, or "" if absent          */
    bool has_display;   /* manifest capabilities[] contains "display"       */
    bool launchable;    /* manifest "launchable" (default true if absent):  */
                        /* false marks a boot asset / the launcher itself   */
} appmeta_t;

/*
 * Read the embedded manifest of the .dap at dap_path into *out.
 * Returns 0 on success, -1 if the file can't be opened, is not an ELF32 LE
 * ET_REL image the shared lib/elf reader accepts, has no ".duneos_manifest"
 * section, or carries one outside 2..4096 bytes (the loader's own bound — the
 * manifest is rejected, never truncated). On -1 *out is left zeroed.
 *
 * Reentrant, and it allocates: the section header table, the section name
 * table and the manifest payload are transient mallocs, so a caller with a
 * per-app heap must budget for them (ADR 025).
 */
int appmeta_read(const char *dap_path, appmeta_t *out);
