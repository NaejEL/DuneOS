#pragma once

#include <stdint.h>

/*
 * ABI contract between the DuneOS kernel and applications.
 *
 * DUNEOS_ABI_VERSION must be bumped on any breaking change to:
 *   - duneos_symbol_t layout
 *   - duneos_app_manifest_t layout
 *   - calling convention of any exported kernel symbol
 *
 * The loader rejects apps whose required_abi_version > DUNEOS_ABI_VERSION.
 */
#define DUNEOS_ABI_VERSION      1
#define DUNEOS_VERSION_STRING   "0.1.0"

/* ELF section name where the app embeds its manifest JSON */
#define DUNEOS_MANIFEST_SECTION ".duneos_manifest"

/* Maximum lengths for manifest string fields */
#define DUNEOS_APP_NAME_MAX     64
#define DUNEOS_APP_VERSION_MAX  16

/*
 * In-memory representation of a parsed app manifest.
 * The canonical form is JSON embedded in DUNEOS_MANIFEST_SECTION.
 */
typedef struct {
    char     name[DUNEOS_APP_NAME_MAX];
    char     version[DUNEOS_APP_VERSION_MAX];
    uint32_t required_abi_version;
    uint32_t permissions;           /* bitmask — see DUNEOS_PERM_* below */
} duneos_app_manifest_t;

/* Permission bits — enforced by the loader (capability model, no MMU) */
#define DUNEOS_PERM_GPIO        (1u << 0)
#define DUNEOS_PERM_UART        (1u << 1)
#define DUNEOS_PERM_SPI         (1u << 2)
#define DUNEOS_PERM_I2C         (1u << 3)
#define DUNEOS_PERM_NET         (1u << 4)
#define DUNEOS_PERM_FS_READ     (1u << 5)
#define DUNEOS_PERM_FS_WRITE    (1u << 6)

/*
 * One entry in the kernel export symbol table.
 * The table is a NULL-terminated array stored at a fixed address so that
 * the loader can walk it without knowing its size at compile time.
 *
 * This is NOT a CPU-level syscall table — there is no privilege separation
 * on Xtensa without an MMU. It is simply a table of function pointers.
 */
typedef struct {
    const char *name;
    void       *ptr;
} duneos_symbol_t;

/* Retrieve the kernel export table (NULL-terminated array) */
const duneos_symbol_t *duneos_symbol_table_get(void);
