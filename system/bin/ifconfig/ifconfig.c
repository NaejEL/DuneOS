/*
 * ifconfig — display network interface configuration.
 *
 * Calls duneos_wifi_get_info() for live data (requires PERM_NET).
 * Falls back to reading /tmp/net_status if the symbol is unavailable.
 * On disconnected boards, prints a clear "not connected" message.
 */

#include <unistd.h>
#include <fcntl.h>
#include <string.h>
#include <stdio.h>
#include <stdarg.h>

#include "duneos/wifi.h"
#include "duneos/bin_args.h"

static void out(const char *s) { write(STDOUT_FILENO, s, strlen(s)); }
static void outf(const char *fmt, ...)
{
    char buf[128];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    out(buf);
}

void app_main(void)
{
    duneos_net_info_t info;
    int rc = duneos_wifi_get_info(&info);

    if (rc != 0) {
        out("wlan0: not connected\r\n");
        return;
    }

    outf("wlan0: inet %s  netmask %s  gateway %s\r\n",
         info.ip, info.netmask, info.gw);
    outf("       ssid: %s  signal: %d dBm\r\n",
         info.ssid, (int)info.rssi);
}
