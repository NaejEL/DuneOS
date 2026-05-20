/*
 * battery_daemon — board-agnostic battery sampler.
 *
 * Opens the active board's gauge chip via libbattery (which dispatches to
 * the chip-specific backend selected by capability resolution), then
 * periodically rewrites a binary battery_info_t snapshot to /tmp/battery.
 * Readers (apps/system/bin/battery, future indicators) `read()` that
 * file — no kernel driver is involved.
 *
 * The daemon is chip-agnostic: changing the gauge chip on a board (e.g.
 * from BQ27220 to MAX17043) requires zero edits here. Only the board's
 * `battery.type` and a sdk/sensor/lib<chip>.c implementing
 * `duneos_battery_ops` are needed — see CLAUDE.md "Adding a battery
 * backend".
 *
 * Restart policy: declare `restart: always` in init.yaml.
 */

#include <fcntl.h>
#include <unistd.h>
#include <string.h>

#include <duneos/board.h>
#include <duneos/battery.h>

#define SAMPLE_PERIOD_MS  2000

extern int  usleep(unsigned int useconds);
extern void duneos_exit(int code);

void app_main(void)
{
    battery_handle_t *h = battery_open();
    if (!h) duneos_exit(2);

    int tmp = open(DUNEOS_BATTERY_TMPFS_PATH, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (tmp < 0) { battery_close(h); duneos_exit(3); }

    for (;;) {
        battery_info_t info;
        memset(&info, 0, sizeof(info));
        if (battery_read(h, &info) == 0) {
            lseek(tmp, 0, SEEK_SET);
            write(tmp, &info, sizeof(info));
        }
        usleep(SAMPLE_PERIOD_MS * 1000);
    }
}
