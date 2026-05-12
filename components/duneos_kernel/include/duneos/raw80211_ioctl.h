#pragma once
#include <stdint.h>

/*
 * DuneOS /dev/raw80211 ioctl API.
 *
 * Provides raw IEEE 802.11 frame injection without going through the normal
 * TCP/IP stack.  Analogous to AF_PACKET / monitor-mode sockets on Linux.
 *
 * Usage from an app:
 *
 *   #include "duneos/raw80211_ioctl.h"
 *
 *   int fd = open("/dev/raw80211", O_WRONLY);
 *   // optional: switch to AP interface (requires WIFI_MODE_APSTA active)
 *   int iface = DUNEOS_WIFI_IF_AP;
 *   ioctl(fd, RAW80211_SET_IFACE, &iface);
 *   write(fd, frame_buf, frame_len);   // injects one 802.11 frame
 *   close(fd);
 *
 * Requires:
 *   - CONFIG_DUNEOS_DRV_RAW80211=y  (board sdkconfig.defaults)
 *   - CONFIG_DUNEOS_DRV_WIFI=y      (dependency)
 *   - manifest permissions must include DUNEOS_PERM_NET_RAW (1u << 10)
 *   - WiFi must be initialised (e.g. wifi_daemon running, or duneos_wifi_init()
 *     called earlier in the same app)
 *
 * Interface values — binary-compatible with esp_wifi_types.h wifi_interface_t:
 */
#define DUNEOS_WIFI_IF_STA   0   /* Station interface (default) */
#define DUNEOS_WIFI_IF_AP    1   /* SoftAP interface — requires APSTA mode  */

/* ioctl commands */
#define RAW80211_SET_IFACE   0x01  /* ← int*  set active interface (STA or AP) */
#define RAW80211_GET_IFACE   0x02  /* → int*  get current interface            */
