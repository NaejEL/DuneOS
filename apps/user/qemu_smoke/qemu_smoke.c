/*
 * qemu_smoke — the payload of the `dbt qemu` smoke test (LEG-27 / ADR 039).
 *
 * The kernel's klog_i/klog_w lines never reach the console: klog.c forwards
 * only level 'E' to ESP_LOG, everything else stays in the ring buffer. The
 * emulator can only observe UART, so this app drains /dev/klog to its stdout —
 * that is what makes the boot sequence (mount, scan, load) assertable from
 * outside — and then prints its own marker so a green run also proves the
 * loaded code actually executed. The marker claims exactly that and no more.
 */

#include <unistd.h>
#include <fcntl.h>
#include <string.h>

extern void duneos_exit(int code);

#define BANNER_BEGIN  "<<<DUNEOS-QEMU-SMOKE klog begin>>>\r\n"
#define BANNER_END    "<<<DUNEOS-QEMU-SMOKE klog end>>>\r\n"
#define MARKER_ENTERED "<<<DUNEOS-QEMU-SMOKE app_main entered>>>\r\n"
/* Worded as what it proves: app_main got here. It is printed *before*
 * duneos_exit(0), so it says nothing about the exit itself — and nothing the
 * kernel logs afterwards (unload, slot release) can be observed either, since
 * this app was the only reader draining the klog ring. */
#define MARKER_RAN    "<<<DUNEOS-QEMU-SMOKE app_main reached duneos_exit(0)>>>\r\n"

static void emit(const char *s)
{
    write(STDOUT_FILENO, s, strlen(s));
}

/* First instruction of the app, on a channel that does not depend on where a
 * supervisor-launched app's fd 1 points. If this marker appears and the ones
 * written to stdout do not, the payload ran and its stdout goes nowhere; if
 * neither appears, the payload was never launched. Two states that used to be
 * indistinguishable from outside. */
static void emit_entry_marker(void)
{
    int fd = open("/dev/uart0", O_WRONLY);
    if (fd < 0) return;
    write(fd, MARKER_ENTERED, strlen(MARKER_ENTERED));
    close(fd);
}

void app_main(void)
{
    emit_entry_marker();
    emit(BANNER_BEGIN);

    int fd = open("/dev/klog", O_RDONLY);
    if (fd >= 0) {
        /* Stack buffer on purpose: a static one lives in the app heap pool,
         * outside the slot bounds api_read validates against. */
        char    buf[256];
        ssize_t n;
        while ((n = read(fd, buf, sizeof(buf))) > 0) {
            write(STDOUT_FILENO, buf, (size_t)n);
        }
        close(fd);
    }

    emit(BANNER_END);
    emit(MARKER_RAN);
    duneos_exit(0);
}
