/*
 * test_pthread — validates ADR 016 captured-mode pthread guard.
 *
 * The kernel refuses pthread_create when the calling app is running
 * captured (shared task with the shell). This test exercises both modes
 * from the same binary; how it's launched decides the path:
 *
 *   Captured  : `test_pthread` typed at usb_shell (no `heap_size`)
 *     Expected: "CAPTURED — pthread_create EPERM" + exit 0
 *
 *   Spawned   : declared in init.yaml, or bump `heap_size` and rerun
 *     Expected: "SPAWNED — thread ran" + exit 0
 *
 * Both outcomes are SUCCESS — the test just confirms the runtime mode
 * matched expectations. A non-EPERM error in captured mode (or a failure
 * to spawn in spawned mode) is the actual bug.
 *
 * Output uses dprintf(STDOUT_FILENO, ...) NOT printf — printf is not in
 * the kernel symbol table by design (would bypass the VFS). See
 * CLAUDE.md → Hard-Won Lessons.
 */

#include <errno.h>
#include <pthread.h>
#include <string.h>
#include <unistd.h>     /* STDOUT_FILENO */

/* dprintf is POSIX 2008 but not exposed by PicoLibc's stdio.h headers here.
 * Declare it ourselves — the kernel symbol table exports it (symbols.c). */
extern int  dprintf(int fd, const char *fmt, ...);
extern void duneos_exit(int code);

static volatile int s_thread_ran = 0;

static void *thread_body(void *arg)
{
    (void)arg;
    s_thread_ran = 1;
    return NULL;
}

void app_main(void)
{
    pthread_t t;
    int rc = pthread_create(&t, NULL, thread_body, NULL);

    if (rc == EPERM) {
        dprintf(STDOUT_FILENO,
                "test_pthread: CAPTURED mode — pthread_create EPERM "
                "(ADR 016 guard active)\n");
        duneos_exit(0);
        return;
    }

    if (rc != 0) {
        dprintf(STDOUT_FILENO,
                "test_pthread: UNEXPECTED — pthread_create rc=%d (%s)\n",
                rc, strerror(rc));
        duneos_exit(1);
        return;
    }

    /* Spawned mode: the thread runs, we wait for it. */
    pthread_join(t, NULL);
    dprintf(STDOUT_FILENO,
            "test_pthread: SPAWNED mode — thread ran=%d "
            "(pthread_create succeeded)\n", s_thread_ran);
    duneos_exit(s_thread_ran ? 0 : 1);
}
