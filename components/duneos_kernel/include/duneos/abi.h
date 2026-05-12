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
#define DUNEOS_VERSION_STRING   "0.2.0"

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
    uint32_t stack_size;            /* app task stack in bytes; 0 → default */
} duneos_app_manifest_t;

/* Permission bits — enforced by the loader during symbol resolution */
#define DUNEOS_PERM_GPIO        (1u << 0)
#define DUNEOS_PERM_UART        (1u << 1)
#define DUNEOS_PERM_SPI         (1u << 2)
#define DUNEOS_PERM_I2C         (1u << 3)
#define DUNEOS_PERM_NET         (1u << 4)
#define DUNEOS_PERM_FS_READ     (1u << 5)
#define DUNEOS_PERM_FS_WRITE    (1u << 6)
#define DUNEOS_PERM_BATTERY     (1u << 7)
#define DUNEOS_PERM_INPUT       (1u << 8)
#define DUNEOS_PERM_FB          (1u << 9)
#define DUNEOS_PERM_NET_RAW     (1u << 10)  /* raw 802.11 frame injection via /dev/raw80211 */

/*
 * One entry in the kernel export symbol table.
 *
 * required_perm: if non-zero, the loader refuses to resolve this symbol
 * unless the app's manifest permissions bitmask includes all required bits.
 *
 * This is NOT a CPU-level syscall table — there is no privilege separation
 * on Xtensa without an MMU. It is simply a table of function pointers.
 */
typedef struct {
    const char *name;
    void       *ptr;
    uint32_t    required_perm;  /* 0 = always allowed */
} duneos_symbol_t;

/* Retrieve the kernel export table (NULL-terminated array) */
const duneos_symbol_t *duneos_symbol_table_get(void);

/*
 * Opaque handle to a loaded app.  Defined in duneos_loader; declared here so
 * supervisor.h (in duneos_kernel) can reference it without depending on loader.
 */
struct duneos_app;
typedef struct duneos_app duneos_app_t;
