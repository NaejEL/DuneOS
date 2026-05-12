#pragma once
#include <stdint.h>

/*
 * DuneOS WiFi STA API — callable by apps via the kernel symbol table.
 *
 * All ESP-IDF / FreeRTOS complexity is hidden inside drv_wifi.c.
 * Apps include only this header; no ESP-IDF headers required.
 *
 * Only available when CONFIG_DUNEOS_DRV_WIFI=y in the board's
 * sdkconfig.defaults. On boards without WiFi support the symbols simply
 * do not appear in the kernel export table and apps that request them
 * will fail to load.
 */

typedef struct {
    char   ip[16];       /* "192.168.1.100\0" — empty string if not connected */
    char   gw[16];       /* "192.168.1.1\0"                                   */
    char   netmask[16];  /* "255.255.255.0\0"                                 */
    char   ssid[33];     /* connected AP SSID                                 */
    int8_t rssi;         /* dBm; 0 if unknown                                 */
} duneos_net_info_t;

/*
 * Initialise the WiFi subsystem: NVS, netif, default event loop, WiFi driver.
 * Idempotent — safe (and cheap) to call multiple times.
 * Returns 0 on success, -1 on error.
 */
int duneos_wifi_init(void);

/*
 * Connect to an AP in STA mode.
 * Blocks until an IP address is assigned, or until timeout_ms milliseconds
 * have elapsed.  Pass 0 for timeout_ms to wait indefinitely.
 * Returns 0 on success (IP acquired), -1 on timeout or connection error.
 *
 * It is safe to call duneos_wifi_sta_connect() again after a failure or
 * disconnect — it will attempt to reconnect with the new credentials.
 */
int duneos_wifi_sta_connect(const char *ssid, const char *password,
                             uint32_t timeout_ms);

/*
 * Disconnect from the current AP and stop the WiFi radio.
 * Returns 0 on success.
 */
int duneos_wifi_sta_disconnect(void);

/*
 * Populate *info with current connection details (IP, GW, netmask, SSID, RSSI).
 * Returns 0 if connected with a valid IP, -1 if not connected.
 */
int duneos_wifi_get_info(duneos_net_info_t *info);

/*
 * Block until any network interface has a valid IP address, or until
 * timeout_ms milliseconds have elapsed.  Pass 0 to wait indefinitely.
 *
 * Transport-agnostic: fires on WiFi STA, WiFi AP, Ethernet — whatever
 * interface received an IP event first.  Intended for apps that want to
 * wait for "network up" without knowing or caring which daemon connected.
 *
 * Returns 0 if an IP is (or becomes) available, -1 on timeout.
 *
 * Requires CONFIG_DUNEOS_DRV_WIFI=y (the only network driver today).
 * When an Ethernet driver is added it will share the same event group.
 */
int duneos_netif_wait_ip(uint32_t timeout_ms);
