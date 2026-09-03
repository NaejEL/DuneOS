Status: PROPOSED

# SPEC-leg-04 — Detect `n * size` overflow in the `calloc` exposed to applications

## Context

Finding LEG-04 (major, XS): `kernel/duneos_kernel/src/supervisor.c:1291` implements the `calloc()`
offered to applications through the symbol table (`kernel/duneos_kernel/src/symbols.c`, around
l.222):

```c
void *p = multi_heap_malloc(slot->heap_handle, n * size);
if (p) memset(p, 0, n * size);
```

The product `n * size` is computed in 32-bit `size_t` with no overflow detection. An application
requesting, say, `calloc(0x10000, 0x10000)` gets a zero-sized or tiny buffer where it believes it
holds 4 GiB, then writes past it. On an MMU-less target, that write silently corrupts the slot heap
or neighbouring memory.

The fallback path immediately below (l.1295) uses `heap_caps_calloc`, which performs that check
itself: the contract is therefore inconsistent between the two branches of the same function.

## Scope

Add overflow detection to the `multi_heap_malloc` branch in `supervisor.c`, so that both branches of
the function honour the same contract as libc `calloc()`.

## Acceptance criteria

1. A call whose `n` and `size` product overflows `size_t` (for example `n = SIZE_MAX / 2` and
   `size = 4`) returns `NULL` without allocating or writing anything.
2. A call with `n = 0` or `size = 0` preserves the function's current behaviour (the chosen
   behaviour is documented by a comment in the code).
3. A valid call whose product does not overflow returns a block of `n * size` bytes fully zeroed:
   the contents of the returned block are zero over its whole length.
4. Both branches (allocation in the slot heap and the `heap_caps_calloc` fallback) behave
   identically in the face of overflow: both return `NULL`.
5. An existing application using `calloc()` (through `libdune`, for instance) keeps working
   unchanged.

## Out of scope

Auditing the other allocation functions exposed to applications; introducing a TLSF allocator or
stack canaries (Phase 20); modifying `libdune`.

## Risks

The function sits on the allocation path of every application: an error in the guard condition would
break all allocation. Criterion 3 requires explicitly checking that a normal allocation still works
and is correctly zeroed.

## Open questions

None
