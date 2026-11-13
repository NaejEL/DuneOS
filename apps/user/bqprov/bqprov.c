/*
 * bqprov — bring the battery gauge's configuration in line with the board's,
 * on demand, and say what happened.
 *
 * The same thing battery_daemon does once at startup. This exists for the case
 * where you want it to happen now, deliberately, and want the result on the
 * console rather than in a log file — a board whose gauge was just reconnected,
 * or one where the daemon is not in the init chain.
 *
 * It is a caller, not a second implementation. The sequence lives in
 * sdk/sensor/lib<chip>.c behind duneos_battery_provision(), because the first
 * version of this tool WAS a second implementation and that is precisely the
 * duplication this repository keeps paying for.
 *
 * This WRITES the gauge's configuration. On the BQ27220 that configuration is
 * RAM: it reverts to the chip's ROM defaults when the gauge loses power, so
 * nothing here is permanent and a bad value is undone by disconnecting the
 * battery. Run `bqinfo` to see the result in full.
 */

#include <unistd.h>
#include <string.h>
#include <stdio.h>
#include <stdarg.h>

#include <duneos/board.h>
#include <duneos/battery.h>

extern void duneos_exit(int code);

static void out(const char *s) { write(STDOUT_FILENO, s, strlen(s)); }

static void outf(const char *fmt, ...)
{
    char buf[128];
    va_list ap; va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap); va_end(ap);
    out(buf);
}

void app_main(void)
{
    out("\r\n--- bqprov begin ---\r\n");

    battery_handle_t *h = battery_open();
    if (!h) { out("  FATAL cannot open the gauge\r\n");
              out("--- bqprov end ---\r\n"); duneos_exit(2); }

    battery_info_t before;
    memset(&before, 0, sizeof(before));
    battery_read(h, &before);
    outf("  before: %u mV, %u%%\r\n",
         (unsigned)before.voltage_mv, (unsigned)before.percent);

    int rc = duneos_battery_provision(h);

    battery_info_t after;
    memset(&after, 0, sizeof(after));
    battery_read(h, &after);
    outf("  after:  %u mV, %u%%\r\n",
         (unsigned)after.voltage_mv, (unsigned)after.percent);

    if (rc == 0)      out("  already matched the board, nothing written\r\n");
    else if (rc == 1) out("  OK reconfigured\r\n");
    else              outf("  FAIL %d — run bqinfo and read OperationStatus\r\n", rc);

    out("--- bqprov end ---\r\n");
    battery_close(h);
    duneos_exit(rc < 0 ? 4 : 0);
}
