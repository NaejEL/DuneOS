/*
 * battery_daemon — userspace BQ27220 sampler.
 *
 * Opens the board's I2C bus + the BQ27220 gas gauge via libbq27220, then
 * periodically rewrites a binary battery_info_t snapshot to /tmp/battery.
 * Readers (apps/system/bin/battery, future indicators) read that file —
 * no kernel battery driver is involved.
 *
 * Board configuration (I2C device path, gauge 7-bit address, output path)
 * comes from boardgen.py via <duneos/board.h>; the daemon is therefore
 * board-portable as long as the board declares battery.type: bq27220.
 *
 * Restart policy: declare `restart: always` in init.yaml — the daemon
 * crashes only on transient I2C errors and should come back immediately.
 */

#include <fcntl.h>
#include <unistd.h>
#include <string.h>

#include <duneos/board.h>

extern void duneos_exit(int code);

#ifdef DUNEOS_BATTERY_GAUGE_ADDR
/* Board declares a BQ27220 — pull in the sensor lib and run the daemon. */
#include <duneos/bq27220.h>

#define SAMPLE_PERIOD_MS  2000

extern int usleep(unsigned int useconds);

void app_main(void)
{
    int bus = open(DUNEOS_BATTERY_I2C_DEV, O_RDWR);
    if (bus < 0) duneos_exit(2);

    bq27220_handle_t *h = bq27220_open(bus, DUNEOS_BATTERY_GAUGE_ADDR);
    if (!h) { close(bus); duneos_exit(3); }

    int tmp = open(DUNEOS_BATTERY_TMPFS_PATH, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (tmp < 0) { bq27220_close(h); close(bus); duneos_exit(4); }

    for (;;) {
        battery_info_t info;
        memset(&info, 0, sizeof(info));
        if (bq27220_read(h, &info) == 0) {
            lseek(tmp, 0, SEEK_SET);
            write(tmp, &info, sizeof(info));
        }
        usleep(SAMPLE_PERIOD_MS * 1000);
    }
}
#else
/* Board has no BQ27220 — daemon is a stub that exits cleanly so it can be
 * present in /flash/bin/ on every board without harm. init.yaml on boards
 * without a BQ27220 simply doesn't reference it. */
void app_main(void)
{
    duneos_exit(0);
}
#endif
