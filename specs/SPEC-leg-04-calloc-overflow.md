Status: APPROVED

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

6. **Criteria 1 to 4 are proven by execution, not by review.** A dedicated test application runs on
   the LEG-27 QEMU bench and exercises the real `duneos_supervisor_app_calloc` on the real slot
   heap: an overflowing product must return `NULL`, a normal allocation must return a block whose
   whole length reads back as zero, and the app must report its own verdict on a channel the bench
   asserts on. `tests/host/` cannot reach this code — it depends on FreeRTOS, the supervisor and the
   ESP-IDF heap — so the bench is the only executable path, and the point of Milestone 0 was to stop
   fixing this class of defect blind.

7. The bench run is wired so a failure of that application fails `dbt qemu`'s exit code, on at least
   one QEMU board. A test whose verdict nobody reads is not a test.

8. The test application exercises **both** branches of the function, or the spec records why only
   one is reachable from an app and how the other was checked. Criterion 4 requires the two branches
   to agree, and the fallback path is taken when no slot heap exists — an app running under the
   supervisor may never reach it.

## Out of scope

Auditing the other allocation functions exposed to applications; introducing a TLSF allocator or
stack canaries (Phase 20); modifying `libdune`.

## Risks

The function sits on the allocation path of every application: an error in the guard condition would
break all allocation. Criterion 3 requires explicitly checking that a normal allocation still works
and is correctly zeroed.

The overflow itself is the cheap part; the verification is not. Adding a `.dap` to the bench costs
more than the three-line guard, and the temptation will be to skip it because the fix "obviously
works". That is exactly the reasoning that shipped a stack overflow to a working board in LEG-37:
the change was small, the review was clean, and nothing executed it on the target. `n = 0` and
`size = 0` are the cases most likely to be got wrong by a guard written from the shape of the bug
rather than from the contract.

## Verification record

Criterion 8 asks for both branches, or a record of why only one is reachable. **Both were
reached and both were executed**, from a single boot of `apps/user/qemu_calloc`:

- **slot heap** — `app_main`. The branch is taken when `slot_by_task_unsafe()` finds the caller's
  slot *and* that slot has a `heap_handle`, which `supervisor.c` only registers for a manifest
  declaring `heap_size >= 64`. The app declares `heap_size: 4096`.
- **`heap_caps_calloc` fallback** — a pthread child of the same app. The slot lookup matches on
  the FreeRTOS task handle, and a child task is not any slot's task, so the lookup returns NULL.

The two are told apart by observation, not by reading the source: one identical 16 KiB request is
refused in `app_main` (the pool is 4 KiB) and served in the thread. On `esp32s3-qemu-psram` the
two allocations also land in different address ranges (`0x3fce…` DRAM vs `0x3c05…` PSRAM).

**On the overflow vectors.** A vector is only worth its line if it answers differently on a
guarded and an unguarded kernel, which needs the product to wrap *and* to wrap to a size the
allocator on that branch will serve. The two shapes this spec names have the first property and
not the second: `SIZE_MAX/2 * 4` wraps to `0xFFFFFFFC` (no heap serves 4 GiB) and
`0x10000 * 0x10000` wraps to exactly `0` (both allocators refuse size 0), so both return `NULL`
with or without the guard. This was found by removing the guard and re-running the bench: it
stayed green. They are kept because they pin that the guard does not change what those calls
answer, but they assert nothing on their own.

The set that does assert something is, per branch:

| vector | wrapped size | what only it can catch |
| --- | --- | --- |
| `calloc(0x40000001, 4)` | 4 | the guard is missing entirely |
| `calloc(0x20000001, 8)` | 8 | idem |
| `calloc(4, 0x40000001)` | 4 | idem, with the large operand second |
| `calloc(0x1000001, 0x200)` | 512 | the guard is present but **wrong** — a magnitude test on the operands rather than a check of the product |
| `calloc(0x200, 0x1000001)` | 512 | idem, operands swapped |

The mid-magnitude pair fires on the **slot-heap branch only**: on the fallback branch
`heap_caps_calloc()` re-checks the product itself, so it answers `NULL` even on a kernel whose
own guard is wrong. That is not a weakness of the vector — the slot-heap branch is the one
SPEC-leg-04 exists to fix, and it is the only branch where a defective guard is observable at
all. It does dictate the wrapped size, below.

The mid-magnitude pair was added after a second mutation experiment: a kernel carrying
`if (n > 0x10000000u || size > 0x10000000u) return NULL;` — no product check at all, and still
serving a 512-byte block for an 8 GiB request — passed the whole bench green, because every
vector then present had one huge operand. With `calloc(0x1000001, 0x200)` the same wrong kernel
fails `dbt qemu` with exit 1, naming the assertion:
`calloc(0x1000001, 0x200) wrapped to a 512-byte block instead of returning NULL`. Both operands
there are values a real application could legitimately pass, so nothing short of an actual product
check answers `NULL`. The wrapped size is 512 rather than the more obvious 4096 deliberately: the
slot pool is 4096 bytes in total, so a 4096-byte request is refused by the pool itself, and on the
one branch where a wrong guard is observable such a vector would prove nothing.

Both mutation runs, on `esp32s3-qemu`, in a scratch copy of the tree:

| kernel | payload | `dbt qemu` |
| --- | --- | --- |
| wrong guard | vectors before this change | **exit 0** — `PASS: every assertion matched`, the bug |
| wrong guard | vectors after this change | **exit 1** — `[MISS] qemu_calloc passed every calloc-overflow check`, naming the wrapped 512-byte block |

Missing-guard and wrong-guard were both confirmed by mutation: on the unguarded kernel
`calloc(0x40000001, 4)` returned a 4-byte block from the slot heap and `NULL` from the fallback —
the branch inconsistency criterion 4 is about — and `dbt qemu` exited 1 naming the assertion.

**Criterion 5 is not proven by execution, and no cheap way to prove it exists.** "An existing
application using `calloc()` keeps working unchanged" has no executable check in the loop:
`qemu_smoke` calls no allocator at all (it passes for boot reasons), and the only pre-existing
`calloc()` callers in the tree — `sdk/ui/ui.c` and `sdk/display/libst7789.c` — are display-bound
and cannot run on a headless emulator. Exercising one of them on the bench would mean building a
display mock, which buys a green light rather than information and is not worth its maintenance.
What the criterion actually rests on is therefore: the guard is transparent for every
non-overflowing product (`__builtin_mul_overflow` returns false and `total == n * size`), the
`qemu_calloc` payload executes the normal path on both branches and reads the block back as zero
over its whole length, and `dbt buildall` compiles all 58 apps unchanged. That is inference plus
adjacent execution, not a direct test, and it is recorded here as such.

`DUNEOS_ABI_VERSION` is **not** bumped: no exported symbol is added, removed or re-typed
(`calloc` keeps its signature and its slot in the table), and no ABI struct layout changes.

## Open questions

None
