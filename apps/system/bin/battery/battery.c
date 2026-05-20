#include <unistd.h>
#include <fcntl.h>
#include <string.h>
#include <stdio.h>
#include <stdarg.h>

#include <duneos/battery_ioctl.h>

static void out(const char *s) { write(STDOUT_FILENO, s, strlen(s)); }
static void outn(const char *s) { out(s); out("\r\n"); }
static void outf(const char *fmt, ...)
{
    char buf[128]; va_list ap; va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap); va_end(ap); out(buf);
}

/*
 * Read battery_info_t — two backends:
 *   1. /tmp/battery   — written by battery_daemon (BQ27220 over I2C).
 *   2. /dev/battery0  — kernel adc_simple driver (CardPuter-style ADC).
 * Return 0 on success, -1 if neither backend is present.
 */
static int read_battery_info(battery_info_t *info)
{
    int fd = open("/tmp/battery", O_RDONLY);
    if (fd >= 0) {
        ssize_t n = read(fd, info, sizeof(*info));
        close(fd);
        if (n == (ssize_t)sizeof(*info)) return 0;
    }
    fd = open("/dev/battery0", O_RDONLY);
    if (fd >= 0) {
        int r = ioctl(fd, BATTERY_GET_INFO, info);
        close(fd);
        if (r == 0) return 0;
    }
    return -1;
}

void app_main(void)
{
    battery_info_t info;
    if (read_battery_info(&info) < 0) {
        outn("battery: no source available "
             "(/tmp/battery not fed and /dev/battery0 not present)");
        return;
    }

    const char *status_str =
        (info.status == BATTERY_CHARGING) ? "charging"    :
        (info.status == BATTERY_FULL)     ? "full"        : "discharging";

    outf("voltage: %u mV  charge: %u%%  status: %s\r\n",
         info.voltage_mv, (unsigned)info.percent, status_str);
}
