/*
 * __duneos_api_ptr — the single link-time anchor for the entire libdune ABI.
 *
 * The loader finds this DEFINED symbol in the app's data section and writes
 * duneos_api_get() to it before calling app_main.  All libdune wrapper
 * functions read through this pointer.
 *
 * Placed in its own translation unit so the linker always pulls it in when
 * the app references any libdune symbol (archive member selection with -r).
 */

#include <duneos/api.h>

/* Explicitly NULL-initialised → .data section (not .bss).
 * The loader overwrites it; the explicit initialiser ensures the symbol has
 * a concrete address in the data pool that the loader's symbol scan can find
 * as a DEFINED symbol with a non-zero section index.                        */
const duneos_api_t *__duneos_api_ptr = (const duneos_api_t *)0;
