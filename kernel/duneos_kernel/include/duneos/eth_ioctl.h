#pragma once

/*
 * Ethernet ioctl API for /dev/eth0.
 *
 * Part of the public DuneOS SDK; include it in app code as:
 *   #include <duneos/eth_ioctl.h>
 *
 * Usage pattern:
 *   int fd = open("/dev/eth0", O_RDWR);
 *   uint8_t mac[6];
 *   ioctl(fd, ETH_GET_MAC, mac);
 *   eth_link_status_t st;
 *   ioctl(fd, ETH_GET_LINK_STATUS, &st);
 */

#include <stdint.h>
#include <stdbool.h>

#define ETH_GET_MAC         0x01   /* arg: uint8_t[6]          */
#define ETH_GET_LINK_STATUS 0x02   /* arg: eth_link_status_t * */

typedef struct {
    bool     up;
    uint32_t speed_mbps;
    bool     full_duplex;
} eth_link_status_t;
