/*
 * DuneOS Ethernet kernel driver — /dev/eth0
 *
 * Medium-agnostic: calls only hal_eth.h (no RMII, SPI, MDC, MDIO details).
 * arch/xtensa_esp32/hal/hal_eth.c provides the RMII backend for plain ESP32.
 *
 * Per ADR-009: Ethernet belongs in the kernel (lifecycle independence,
 * resource arbitration across concurrent apps).
 * Per ADR-015: self-registers via DUNEOS_DRIVER_REGISTER — no vfs_dev.c edit.
 *
 * Phase 27: duneos_hal_eth_start() will be migrated to vendored lwIP +
 * duneos_netif_*. Apps access the network via BSD socket exports today.
 */

#include "duneos/dev_driver.h"
#include "duneos/driver_init.h"
#include "duneos/hal_eth.h"
#include "duneos/klog.h"

#include <errno.h>
#include <stdint.h>
#include <string.h>

#define TAG "duneos/eth"

/* ioctl codes for /dev/eth0 */
#define ETH_GET_MAC         0x01   /* arg: uint8_t[6]             */
#define ETH_GET_LINK_STATUS 0x02   /* arg: eth_link_status_t *    */

typedef struct {
    bool     up;
    uint32_t speed_mbps;
    bool     full_duplex;
} eth_link_status_t;

/* Log-only: the authoritative link snapshot lives in the HAL (it owns the
 * synchronisation), queried on demand via duneos_hal_eth_get_link(). */
static void on_link_change(bool up, uint32_t speed_mbps, bool full_duplex)
{
    if (up)
        klog_i(TAG, "link up %lu Mbps %s-duplex",
               (unsigned long)speed_mbps, full_duplex ? "full" : "half");
    else
        klog_i(TAG, "link down");
}

static int eth_ioctl(duneos_devfd_t *fd, int cmd, void *arg)
{
    (void)fd;
    if (!arg) { errno = EFAULT; return -1; }

    switch (cmd) {
    case ETH_GET_MAC:
        return duneos_hal_eth_get_mac((uint8_t *)arg);
    case ETH_GET_LINK_STATUS: {
        eth_link_status_t *st = arg;
        return duneos_hal_eth_get_link(&st->up, &st->speed_mbps, &st->full_duplex);
    }
    default:
        errno = ENOTTY;
        return -1;
    }
}

static duneos_dev_driver_t s_eth_driver = {
    .name  = "eth0",
    .ioctl = eth_ioctl,
};

static void drv_eth_register(void)
{
    duneos_hal_eth_set_link_callback(on_link_change);

    if (duneos_hal_eth_init() < 0) {
        klog_e(TAG, "hal_eth_init failed — /dev/eth0 not registered");
        return;
    }
    if (duneos_hal_eth_start() < 0) {
        klog_e(TAG, "hal_eth_start failed — /dev/eth0 not registered");
        return;
    }
    if (duneos_dev_register(&s_eth_driver) < 0) {
        klog_e(TAG, "duneos_dev_register failed");
        return;
    }
    klog_i(TAG, "/dev/eth0 registered");
}

DUNEOS_DRIVER_REGISTER(5, drv_eth_register);
