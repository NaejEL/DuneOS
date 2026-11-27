#pragma once

/*
 * Loader limits and the app-file filter, factored out of loader.c so the host
 * test harness consumes the same definitions instead of copying them.
 *
 * loader.c cannot be compiled on the host (ESP-IDF, FreeRTOS, cJSON, klog), so
 * anything the harness must mirror had to be duplicated by hand, with nothing
 * linking the copies: a change on one side desynchronised the other silently,
 * with no compile error. Everything here is plain C over libc, includable from
 * both, so there is exactly one definition to change.
 */

#include <stdbool.h>
#include <stddef.h>
#include <string.h>
#include <strings.h>

#include "duneos/elf.h"

/* Upper bound on ELF section-header count — the loader rejects apps above it.
 * A generous ceiling, not a per-app cost: section_bases is allocated to the
 * real e_shnum (P2.1, audit §3). Section counts are modest now that apps merge
 * sections (no -ffunction-sections, audit P0). */
#define DUNEOS_LOADER_MAX_SECTIONS 1024u

/* e_machine the loader accepts, per target ISA. loader.c selects one from its
 * CONFIG_IDF_TARGET_ARCH_*; a host harness names the one its corpus is built
 * for, since no CONFIG_* is defined off-target. */
#define DUNEOS_LOADER_MACHINE_XTENSA EM_XTENSA
#define DUNEOS_LOADER_MACHINE_RISCV  EM_RISCV

/*
 * Whether a directory entry is a candidate app binary for duneos_loader_scan().
 * FAT SD cards return 8.3 uppercase names, hence the case-insensitive compare;
 * the length floor rejects a name that is nothing but the extension.
 */
static inline bool duneos_loader_name_is_app(const char *name)
{
    size_t len = strlen(name);
    if (len < 5) return false;

    const char *ext = name + len - 4;
    return strcasecmp(ext, ".elf") == 0 || strcasecmp(ext, ".dap") == 0;
}
