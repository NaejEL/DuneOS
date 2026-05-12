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

void app_main(void)
{
    int fd = open("/dev/battery0", O_RDONLY);
    if (fd < 0) {
        outn("battery: /dev/battery0 not available on this board");
        return;
    }

    battery_info_t info;
    if (ioctl(fd, BATTERY_GET_INFO, &info) < 0) {
        outn("battery: ioctl failed");
        close(fd);
        return;
    }
    close(fd);

    const char *status_str =
        (info.status == BATTERY_CHARGING) ? "charging"    :
        (info.status == BATTERY_FULL)     ? "full"        : "discharging";

    outf("voltage: %u mV  charge: %u%%  status: %s\r\n",
         info.voltage_mv, (unsigned)info.percent, status_str);
}
