/*
 * Phase 20 memory-hardening smoke test.
 *
 * Meant to run as a supervisor-launched app (NOT via the shell's synchronous
 * "run" command, which has no crash protection).
 *
 * Recommended init.yaml entry:
 *
 *   services:
 *     - path: /sd/apps/test_hardening.dap
 *       restart: always
 *
 * The app uses a state counter in /tmp/.hw_state to advance through each test
 * on successive supervisor-triggered restarts.  Watch the kernel log on the
 * serial console.
 *
 * State machine:
 *   0  heap pool   — malloc/free from per-app heap (exits 0)
 *   1  bad ptr     — write(NULL) returns EFAULT, kernel alive (exits 0)
 *   2  div by zero — integer ÷ 0 → CPU exception → killed code 127
 *   3  null store  — write to address 0 → CPU exception → killed code 127
 *   4  wdt         — spin without duneos_wdt_reset → killed code 125
 *   5  done        — all tests complete, resets counter (exits 0)
 *
 * NOTE — stack overflow is NOT tested via infinite recursion.
 * On Xtensa, deep recursion exhausts the 8 hardware register windows before
 * the FreeRTOS canary check has a chance to fire at a context switch.  The
 * WindowOverflow exception handler then faults on an already-empty stack,
 * producing a double exception (BREAK in _DoubleExceptionVector) that bypasses
 * both our recovery handler and the canary hook.  The canary mechanism is
 * still active in production — it correctly detects moderate overflows where
 * a context switch occurs before the windows are exhausted.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>

extern void duneos_exit(int code);
extern void duneos_wdt_reset(void);

#define STATE_FILE "/tmp/.hw_state"
#define NUM_TESTS  5

static void out(const char *s) { write(STDOUT_FILENO, s, strlen(s)); }
static void outln(const char *s) { out(s); out("\r\n"); }

/* ---- state helpers ---- */

static int read_state(void)
{
    char buf[8] = "0";
    int fd = open(STATE_FILE, O_RDONLY);
    if (fd >= 0) {
        int n = (int)read(fd, buf, sizeof(buf) - 1);
        if (n > 0) buf[n] = '\0';
        close(fd);
    }
    return atoi(buf);
}

static void write_state(int s)
{
    char buf[8];
    snprintf(buf, sizeof(buf), "%d", s);
    int fd = open(STATE_FILE, O_WRONLY | O_CREAT | O_TRUNC, 0600);
    if (fd >= 0) { write(fd, buf, strlen(buf)); close(fd); }
}

/* ---- tests ---- */

static void test_heap(void)
{
    outln("[test_hardening] heap: allocating 512 B from per-app pool...");
    void *p = malloc(512);
    if (!p) { outln("[test_hardening] heap: FAIL — malloc returned NULL"); return; }
    memset(p, 0xAB, 512);
    const unsigned char *b = (unsigned char *)p;
    int ok = 1;
    for (int i = 0; i < 512; i++) if (b[i] != 0xAB) { ok = 0; break; }
    free(p);
    outln(ok ? "[test_hardening] heap: OK (allocated, pattern verified, freed)"
             : "[test_hardening] heap: FAIL (memory corruption)");
}

static void test_bad_ptr(void)
{
    outln("[test_hardening] ptr:  calling write(fd=1, NULL, 4)...");
    ssize_t r = write(STDOUT_FILENO, NULL, 4);
    char buf[96];
    snprintf(buf, sizeof(buf),
             "[test_hardening] ptr:  write()=%d errno=%d (14=EFAULT) — %s\r\n",
             (int)r, errno,
             (r == -1 && errno == EFAULT) ? "OK, kernel alive" : "UNEXPECTED");
    out(buf);
}

static void test_divzero(void)
{
    outln("[test_hardening] div:  1 / 0 — expect KERN CPU exception kill...");
    volatile int z = 0;
    volatile int r = 1 / z;
    (void)r;
}

static void test_null_store(void)
{
    outln("[test_hardening] null: store to address 0 — expect KERN CPU exception kill...");
    volatile int *p = NULL;
    *p = 42;
}

static void test_wdt(void)
{
    outln("[test_hardening] wdt:  spinning without duneos_wdt_reset (timeout=3s)...");
    while (1) { /* intentional spin — WDT will fire */ }
}

/* ---- entry ---- */

void app_main(void)
{
    int state = read_state();
    char banner[72];
    snprintf(banner, sizeof(banner),
             "[test_hardening] === Phase 20 test %d/%d ===\r\n", state, NUM_TESTS - 1);
    out(banner);

    /* Advance BEFORE the (possibly crashing) test so the counter survives. */
    write_state(state < NUM_TESTS ? state + 1 : 0);

    switch (state) {
    case 0: test_heap();       break;
    case 1: test_bad_ptr();    break;
    case 2: test_divzero();    break;
    case 3: test_null_store(); break;
    case 4: test_wdt();        break;
    default:
        outln("[test_hardening] all tests done — resetting counter");
        write_state(0);
        break;
    }

    duneos_exit(0);
}
