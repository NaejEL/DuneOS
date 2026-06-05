#pragma once
#include <stdint.h>

/*
 * Shared network-interface info, transport-agnostic (WiFi STA, Ethernet, …).
 * Part of the public DuneOS SDK.
 */

typedef struct {
    char   ip[16];       /* "192.168.1.100\0" — empty string if not connected */
    char   gw[16];       /* "192.168.1.1\0"                                   */
    char   netmask[16];  /* "255.255.255.0\0"                                 */
    char   ssid[33];     /* connected AP SSID (WiFi only; empty for Ethernet) */
    int8_t rssi;         /* dBm; 0 if unknown / wired                         */
    char   mac[18];      /* "aa:bb:cc:dd:ee:ff\0"                             */
} duneos_net_info_t;

/*
 * Populate *info with the Ethernet interface's current IP configuration.
 * Returns 0 if /dev/eth0 has a valid (DHCP/static) IPv4 address, -1 otherwise.
 * Only present when CONFIG_DUNEOS_DRV_ETH=y.
 */
int duneos_eth_get_info(duneos_net_info_t *info);
