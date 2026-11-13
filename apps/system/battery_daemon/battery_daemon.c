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
#include <duneos/charger.h>
#include <duneos/dlog.h>

#define SAMPLE_PERIOD_MS  2000

extern int  usleep(unsigned int useconds);
extern void duneos_exit(int code);

void app_main(void)
{
    dlog_open("battery_daemon");

    battery_handle_t *h = battery_open();
    if (!h) duneos_exit(2);

    /* Bring the gauge's own configuration in line with the board's before
     * publishing a single sample, so no reader ever sees a percentage computed
     * against the wrong pack. On the BQ27220 this configuration lives in RAM
     * and reverts on power loss, which is why it belongs at every startup and
     * not in a provisioning tool run once at the factory.
     *
     * Not fatal: a gauge that refuses reconfiguration still reports a voltage,
     * and a battery indicator that is wrong is better than a daemon that is
     * absent. Logged either way — a silently rescaled gauge would be worse
     * than both. */
    int prov = duneos_battery_provision(h);
    if (prov > 0)      DLOGI("gauge reconfigured to the board's pack");
    else if (prov < 0) DLOGW("gauge keeps its own configuration (%d)", prov);

    /* The charger, when the board has one. It is the authority on whether the
     * cell is full: the gauge only knows what it inferred from a current taper
     * it may never have witnessed, while the charger simply reports that it
     * stopped. Opening it is allowed to fail — a board can have a gauge and no
     * charger chip, and a missing charger must not cost the samples. */
    charger_handle_t *chg = NULL;
#ifdef DUNEOS_CHARGER_DRIVER
    chg = charger_open();
    if (!chg) {
        DLOGW("charger not responding; status will come from the gauge alone");
    } else {
        int crc = duneos_charger_configure(chg);
        if (crc > 0)      DLOGI("charger set to the board's charge profile");
        else if (crc < 0) DLOGW("charger keeps its own profile (%d)", crc);
    }
#endif

    int tmp = open(DUNEOS_BATTERY_TMPFS_PATH, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (tmp < 0) { battery_close(h); duneos_exit(3); }

    for (;;) {
        battery_info_t info;
        memset(&info, 0, sizeof(info));
        if (battery_read(h, &info) == 0) {
            /* Let the charger overrule the gauge on the charge phase. The
             * gauge's DSG/FC bits answer "is current flowing out" and "did I
             * detect termination"; the charger answers "am I filling it, and
             * did I finish". When they disagree the charger is right, and the
             * disagreement is the normal case on a board whose taper
             * thresholds have only just been made consistent. */
            if (chg) {
                charger_info_t ci;
                if (charger_read(chg, &ci) == 0 && ci.input_power_good) {
                    switch (ci.state) {
                    case CHARGER_DONE:      info.status = BATTERY_FULL;         break;
                    case CHARGER_CHARGING:
                    case CHARGER_PRECHARGE: info.status = BATTERY_CHARGING;     break;
                    case CHARGER_NOT_CHARGING:
                    case CHARGER_UNKNOWN:   break;   /* leave the gauge's view */
                    }
                }
            }
            lseek(tmp, 0, SEEK_SET);
            write(tmp, &info, sizeof(info));
        }
        usleep(SAMPLE_PERIOD_MS * 1000);
    }
}
