/*
 * libdune — memory wrappers (ABI v3).
 *
 * malloc/free/realloc/calloc route through the kernel's per-app heap when
 * manifest heap_size > 0, or through the global heap otherwise.  This
 * maintains the heap isolation guarantee from Phase 20.
 */

#include <duneos/api.h>
#include <stddef.h>

extern const duneos_api_t *__duneos_api_ptr;

void *malloc(size_t size)
{
    return __duneos_api_ptr->mem.malloc(size);
}

void free(void *ptr)
{
    __duneos_api_ptr->mem.free(ptr);
}

void *realloc(void *ptr, size_t size)
{
    return __duneos_api_ptr->mem.realloc(ptr, size);
}

void *calloc(size_t n, size_t size)
{
    return __duneos_api_ptr->mem.calloc(n, size);
}
