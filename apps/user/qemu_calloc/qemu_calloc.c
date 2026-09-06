/*
 * qemu_calloc — executable proof of SPEC-leg-04 on the `dbt qemu` bench.
 *
 * `calloc` in an app resolves to libdune's wrapper, which calls
 * `__duneos_api_ptr->mem.calloc` = `duneos_supervisor_app_calloc` (api.c).
 * There is no host-testable seam: that function needs FreeRTOS, a live
 * supervisor slot and the ESP-IDF heap, so the emulator is the only place the
 * guard can actually be run rather than reviewed.
 *
 * The function has two branches and an app picks one by its manifest, not at
 * the call site:
 *
 *   slot heap  — `slot_by_task_unsafe()` finds the caller's slot and the slot
 *                has a `heap_handle`, which supervisor.c only creates when the
 *                manifest declares `heap_size >= 64`. This app declares one,
 *                so app_main takes this branch. It is the branch SPEC-leg-04
 *                fixes.
 *   fallback   — everything else falls through to `heap_caps_calloc()`.
 *                Reached here from a pthread child: slot lookup matches on the
 *                FreeRTOS task handle, and a child task is not any slot's
 *                task, so the lookup returns NULL. CHECK_BIG below turns that
 *                code-reading into an observation: one identical request is
 *                refused in app_main and served in the thread, which only two
 *                different allocators can do.
 *
 * Like qemu_smoke, this app drains /dev/klog to its stdout: klog.c forwards
 * only level 'E' to ESP_LOG, so the boot lines the bench asserts on reach the
 * emulated UART no other way. Its own verdict goes to /dev/uart0 as well as
 * stdout, so it survives a supervisor-launched app's fd 1 pointing elsewhere.
 */

#include <fcntl.h>
#include <pthread.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

extern int  dprintf(int fd, const char *fmt, ...);
extern void duneos_exit(int code);

#define BANNER_BEGIN   "<<<DUNEOS-QEMU-CALLOC klog begin>>>\r\n"
#define BANNER_END     "<<<DUNEOS-QEMU-CALLOC klog end>>>\r\n"
#define MARKER_ENTERED "<<<DUNEOS-QEMU-CALLOC app_main entered>>>\r\n"
#define MARKER_PASSED  "<<<DUNEOS-QEMU-CALLOC every check passed>>>\r\n"

/* Manifest heap_size. The slot pool is this big, so an allocation larger than
 * it cannot be served from it. */
#define SLOT_POOL_BYTES  4096u
#define CHECK_SMALL      512u
#define CHECK_BIG        16384u

static const char *s_failure;   /* first failed check, NULL while all pass */

static void fail(const char *what)
{
    if (!s_failure) s_failure = what;
}

static void emit(const char *s)
{
    write(STDOUT_FILENO, s, strlen(s));
}

static void emit_uart(const char *s)
{
    int fd = open("/dev/uart0", O_WRONLY);
    if (fd < 0) return;
    write(fd, s, strlen(s));
    close(fd);
}

/* Every request below goes through here so the operands reach calloc() as
 * runtime values. With literals, GCC constant-folds the product, warns about
 * it (-Walloc-size-larger-than=) and is entitled to reason about a call it has
 * already decided must fail — none of which tests the kernel. The volatile
 * locals are the whole point: an app asking for an absurd size at runtime is
 * exactly the case SPEC-leg-04 is about. */
static void *call_calloc(size_t n, size_t size)
{
    volatile size_t vn = n, vsize = size;
    return calloc(vn, vsize);
}

/* Returns 0 when every byte of `p[0..len)` is zero. */
static int all_zero(const unsigned char *p, size_t len)
{
    for (size_t i = 0; i < len; i++)
        if (p[i] != 0) return 0;
    return 1;
}

/* The checks common to both branches. `where` names the branch in the report.
 * `big_must_fail` is the branch probe: CHECK_BIG is four times the declared
 * slot pool, so the slot heap must refuse it and the global heap must serve
 * it. The pair of opposite outcomes for one identical request is what proves
 * the two calls really went to two different allocators. */
static void run_checks(const char *where, int big_must_fail)
{
    /* Criterion 1. A vector only earns its place if it answers differently on
     * a guarded and an unguarded kernel, which takes two properties at once:
     * the product must wrap, and it must wrap to a size the allocator on this
     * branch will actually serve. The two shapes SPEC-leg-04 names have the
     * first and not the second — SIZE_MAX/2 * 4 wraps to 0xFFFFFFFC (no heap
     * serves 4 GiB) and 0x10000 * 0x10000 wraps to exactly 0 (multi_heap_malloc
     * and heap_caps_* both refuse size 0) — so a bench built only on those two
     * passes on a broken kernel. Verified by removing the guard and re-running:
     * both stayed NULL.
     *
     * The vectors below wrap to 4, 8 and 512 bytes, all of which the 4 KiB slot
     * pool serves, so an unguarded kernel hands back a real block for a
     * multi-gigabyte request and is caught here. */
    void *ovf = call_calloc(0x40000001u, 4u);
    dprintf(STDOUT_FILENO, "qemu_calloc: %s overflow(0x40000001, 4) -> %p\n",
            where, ovf);
    if (ovf) {
        free(ovf);
        fail("calloc(0x40000001, 4) wrapped to a 4-byte block instead of "
             "returning NULL");
    }

    void *ovf2 = call_calloc(0x20000001u, 8u);
    dprintf(STDOUT_FILENO, "qemu_calloc: %s overflow(0x20000001, 8) -> %p\n",
            where, ovf2);
    if (ovf2) {
        free(ovf2);
        fail("calloc(0x20000001, 8) wrapped to an 8-byte block instead of "
             "returning NULL");
    }

    /* Mid-magnitude, and the reason this block exists. Every vector above has
     * one huge operand, so a kernel that merely rejects large operands —
     * `if (n > 0x10000000 || size > 0x10000000) return NULL;`, a guard written
     * from the shape of the bug rather than from the product — passes all of
     * them while still serving 512 bytes for an 8 GiB request. Here both
     * operands are ordinary (0x1000001 and 0x200, either of which a real app
     * could legitimately pass) and only the product, 0x2_0000_0200, wraps.
     * Nothing but an actual product check answers NULL to this.
     *
     * The wrapped size is 512 and not the more obvious 4096: the slot pool is
     * 4096 bytes total, so a 4096-byte request is refused by the pool itself
     * on the branch that matters and the vector would prove nothing there. */
    void *ovf_mid = call_calloc(0x1000001u, 0x200u);
    dprintf(STDOUT_FILENO, "qemu_calloc: %s overflow(0x1000001, 0x200) -> %p\n",
            where, ovf_mid);
    if (ovf_mid) {
        free(ovf_mid);
        fail("calloc(0x1000001, 0x200) wrapped to a 512-byte block instead of "
             "returning NULL");
    }

    /* Same product with the operands swapped, and one more vector whose *large*
     * operand is `size`. The guard is symmetric in n and size; the vector set
     * was not, and a guard that checked only `n` — or only the first argument
     * of a hand-written comparison — would have passed everything above. */
    void *ovf_mid_swapped = call_calloc(0x200u, 0x1000001u);
    dprintf(STDOUT_FILENO, "qemu_calloc: %s overflow(0x200, 0x1000001) -> %p\n",
            where, ovf_mid_swapped);
    if (ovf_mid_swapped) {
        free(ovf_mid_swapped);
        fail("calloc(0x200, 0x1000001) wrapped to a 512-byte block instead of "
             "returning NULL");
    }

    void *ovf_big_size = call_calloc(4u, 0x40000001u);
    dprintf(STDOUT_FILENO, "qemu_calloc: %s overflow(4, 0x40000001) -> %p\n",
            where, ovf_big_size);
    if (ovf_big_size) {
        free(ovf_big_size);
        fail("calloc(4, 0x40000001) wrapped to a 4-byte block instead of "
             "returning NULL");
    }

    /* The two shapes named in the spec. They cannot fail an unguarded build,
     * but they pin that the guard does not change what those calls answer. */
    void *ovf3 = call_calloc(SIZE_MAX / 2u, 4u);
    void *ovf4 = call_calloc(0x10000u, 0x10000u);
    dprintf(STDOUT_FILENO,
            "qemu_calloc: %s overflow(SIZE_MAX/2, 4) -> %p  "
            "overflow(0x10000, 0x10000) -> %p\n", where, ovf3, ovf4);
    if (ovf3) { free(ovf3); fail("calloc(SIZE_MAX/2, 4) did not return NULL"); }
    if (ovf4) { free(ovf4); fail("calloc(0x10000, 0x10000) did not return NULL"); }

    /* Criterion 2 — zero product. Both allocators under the function bail out
     * on a zero size, so NULL is the behaviour that was there before the guard
     * and the behaviour the guard must not change. */
    void *z1 = call_calloc(0u, 4u);
    void *z2 = call_calloc(4u, 0u);
    dprintf(STDOUT_FILENO, "qemu_calloc: %s zero(0,4) -> %p  zero(4,0) -> %p\n",
            where, z1, z2);
    if (z1) { free(z1); fail("calloc(0, 4) stopped returning NULL"); }
    if (z2) { free(z2); fail("calloc(4, 0) stopped returning NULL"); }

    /* Criterion 3, and the risk the spec names: the guard must not break the
     * ordinary path. Dirty the block, free it, ask for the same size again —
     * the allocator hands back the same region, so a zeroed result proves the
     * memset ran rather than proving the heap happened to be clean. */
    unsigned char *dirty = call_calloc(CHECK_SMALL, 1u);
    if (!dirty) {
        fail("a normal calloc() of CHECK_SMALL bytes returned NULL");
        return;
    }
    memset(dirty, 0xAA, CHECK_SMALL);
    free(dirty);

    unsigned char *p = call_calloc(CHECK_SMALL / 4u, 4u);
    dprintf(STDOUT_FILENO, "qemu_calloc: %s normal(%u, 4) -> %p\n",
            where, (unsigned)(CHECK_SMALL / 4u), (void *)p);
    if (!p) {
        fail("a normal calloc() returned NULL after the overflow calls");
        return;
    }
    if (!all_zero(p, CHECK_SMALL))
        fail("a normal calloc() block was not zero over its whole length");
    free(p);

    unsigned char *big = call_calloc(CHECK_BIG, 1u);
    dprintf(STDOUT_FILENO, "qemu_calloc: %s probe(%u) -> %p (want %s)\n",
            where, (unsigned)CHECK_BIG, (void *)big,
            big_must_fail ? "NULL" : "non-NULL");
    if (big_must_fail) {
        if (big) {
            free(big);
            fail("the slot-heap probe was served, so this call did not go to "
                 "the slot heap");
        }
        return;
    }
    if (!big) {
        fail("the fallback probe returned NULL, so the branch is unproven");
        return;
    }
    if (!all_zero(big, CHECK_BIG))
        fail("the fallback probe block was not zero over its whole length");
    free(big);
}

static void *fallback_branch(void *arg)
{
    (void)arg;
    /* No slot owns this task, so duneos_supervisor_app_calloc() takes the
     * heap_caps_calloc() path — where CHECK_BIG must now succeed. */
    run_checks("fallback", 0);
    return NULL;
}

void app_main(void)
{
    emit_uart(MARKER_ENTERED);
    emit(BANNER_BEGIN);

    int fd = open("/dev/klog", O_RDONLY);
    if (fd >= 0) {
        /* Stack buffer on purpose: a static one lives in the app heap pool,
         * outside the slot bounds api_read validates against. */
        char    buf[256];
        ssize_t n;
        while ((n = read(fd, buf, sizeof(buf))) > 0)
            write(STDOUT_FILENO, buf, (size_t)n);
        close(fd);
    }
    emit(BANNER_END);

    run_checks("slot-heap", 1);

    pthread_t t;
    int rc = pthread_create(&t, NULL, fallback_branch, NULL);
    if (rc != 0) {
        dprintf(STDOUT_FILENO, "qemu_calloc: pthread_create rc=%d\n", rc);
        fail("could not reach the fallback branch (pthread_create failed)");
    } else {
        pthread_join(t, NULL);
    }

    if (s_failure) {
        dprintf(STDOUT_FILENO,
                "<<<DUNEOS-QEMU-CALLOC FAILED: %s>>>\r\n", s_failure);
        emit_uart("<<<DUNEOS-QEMU-CALLOC FAILED>>>\r\n");
        duneos_exit(1);
        return;
    }

    emit(MARKER_PASSED);
    emit_uart(MARKER_PASSED);
    duneos_exit(0);
}
