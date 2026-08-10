/*
 * battery_pub — bridges a kernel-served battery to the ambient state file.
 *
 * Some boards (e.g. CardPuter) have no I2C fuel gauge: the battery is an ADC
 * voltage divider served directly by the kernel as /dev/battery0
 * (drv_battery_adc_simple). There is no libbattery backend for that path, so
 * battery_daemon — which drives I2C gauges — does not apply. This tiny bridge
 * reads /dev/battery0 via ioctl(BATTERY_GET_INFO) and rewrites /tmp/battery as
 * the same battery_info_t snapshot the I2C daemon produces.
 *
 * Result: the status bar (and any reader) gets battery from one ambient path,
 * /tmp/battery, regardless of whether the backend is a gauge or the ADC. The
 * consumer never knows which (ADR 027 / ADR 009). Exactly one battery producer
 * runs per board — battery_daemon for I2C gauges, battery_pub for kernel ADC.
 *
 * Restart policy: declare `restart: always` in init.yaml.
 */

#include <fcntl.h>
#include <unistd.h>
#include <string.h>

#include "duneos/battery_ioctl.h"   /* battery_info_t, BATTERY_GET_INFO, ioctl() */
#include "duneos/ambient.h"         /* AMBIENT_BATTERY_PATH */
#include "duneos/dlog.h"

#define SAMPLE_PERIOD_MS  2000

extern int  usleep(unsigned int useconds);
extern void duneos_exit(int code);

void app_main(void)
{
    dlog_open("battery_pub");

    int dev = open("/dev/battery0", O_RDONLY);
    if (dev < 0) { DLOGE("no /dev/battery0 (board has no ADC battery?)"); duneos_exit(2); }

    int out = open(AMBIENT_BATTERY_PATH, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (out < 0) { DLOGE("cannot write %s", AMBIENT_BATTERY_PATH); close(dev); duneos_exit(3); }

    DLOGI("publishing /dev/battery0 → %s", AMBIENT_BATTERY_PATH);

    for (;;) {
        battery_info_t info;
        memset(&info, 0, sizeof(info));
        if (ioctl(dev, BATTERY_GET_INFO, &info) == 0) {
            lseek(out, 0, SEEK_SET);
            write(out, &info, sizeof(info));
        }
        usleep(SAMPLE_PERIOD_MS * 1000);
    }
}
