/*
 * PHY HAL backend — dispatches to IDF PHY factory by DUNEOS_ETH_PHY_TYPE.
 *
 * Internal to arch/xtensa_esp32: only hal_eth.c calls this.
 * The opaque handle is retrieved via duneos_hal_phy_handle() and passed
 * directly to esp_eth_driver_install() — no IDF PHY type leaks into hal_eth.c.
 */

#include "duneos/hal_phy.h"
#include "duneos/klog.h"

#include "esp_eth_phy.h"
#include "esp_eth_phy_lan87xx.h"
#include "esp_eth_phy_ksz80xx.h"
#include "esp_eth_phy_rtl8201.h"
#include "esp_eth_phy_ip101.h"

#include <errno.h>
#include <stddef.h>

#define TAG "duneos/hal_phy"

static esp_eth_phy_t *s_phy = NULL;

int duneos_hal_phy_init(const duneos_hal_phy_config_t *cfg)
{
    eth_phy_config_t phy_cfg = ETH_PHY_DEFAULT_CONFIG();
    phy_cfg.phy_addr         = cfg->addr;
    phy_cfg.reset_gpio_num   = -1;  /* no dedicated reset GPIO — board uses HW pull */

    switch (cfg->type) {
    case DUNEOS_ETH_PHY_LAN8720:
        s_phy = esp_eth_phy_new_lan87xx(&phy_cfg);
        break;
    case DUNEOS_ETH_PHY_KSZ8081:
        s_phy = esp_eth_phy_new_ksz80xx(&phy_cfg);
        break;
    case DUNEOS_ETH_PHY_RTL8201:
        s_phy = esp_eth_phy_new_rtl8201(&phy_cfg);
        break;
    case DUNEOS_ETH_PHY_IP101:
        s_phy = esp_eth_phy_new_ip101(&phy_cfg);
        break;
    default:
        klog_e(TAG, "unknown PHY type %d", (int)cfg->type);
        errno = EINVAL;
        return -1;
    }

    if (!s_phy) {
        klog_e(TAG, "PHY factory returned NULL");
        errno = ENOMEM;
        return -1;
    }
    return 0;
}

void *duneos_hal_phy_handle(void)
{
    return s_phy;
}

int duneos_hal_phy_get_link(bool *up, uint32_t *speed_mbps, bool *full_duplex)
{
    if (!s_phy) { errno = EINVAL; return -1; }
    /* IDF v6 exposes no synchronous link readback on the PHY handle — real
     * state is delivered asynchronously via ETH_EVENT. Don't fake a link-down
     * a caller might trust: zero the (NULL-safe) outs and report unsupported.
     * Use duneos_hal_eth_get_link() for the authoritative snapshot. */
    if (up)          *up          = false;
    if (speed_mbps)  *speed_mbps  = 0;
    if (full_duplex) *full_duplex = false;
    errno = ENOTSUP;
    return -1;
}

int duneos_hal_phy_reset(void)
{
    if (!s_phy) { errno = EINVAL; return -1; }
    if (s_phy->reset(s_phy) != ESP_OK) { errno = EIO; return -1; }
    return 0;
}
