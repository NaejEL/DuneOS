/*
 * Ethernet HAL backend — ESP32 RMII (integrated MAC).
 *
 * Reads medium config from board_config.h:
 *   DUNEOS_ETH_MDC_PIN, DUNEOS_ETH_MDIO_PIN
 *   DUNEOS_ETH_PHY_ADDR, DUNEOS_ETH_PHY_TYPE
 *   DUNEOS_ETH_CLK_MODE   (ETH_CLOCK_GPIO0_IN | ETH_CLOCK_GPIO17_OUT | …)
 *
 * drv_eth.c calls only hal_eth.h — no RMII or IDF types leak out.
 *
 * Phase 27: duneos_hal_eth_start() will switch from esp_netif to duneos_netif_*.
 */

#include "duneos/hal_eth.h"
#include "duneos/hal_phy.h"
#include "duneos/klog.h"
#include "board_config.h"

#include "esp_eth.h"
#include "esp_eth_mac.h"
#include "esp_eth_mac_esp.h"
#include "esp_eth_phy.h"
#include "esp_netif.h"
#include "esp_event.h"
#include "esp_mac.h"
#include "freertos/FreeRTOS.h"

#include <errno.h>
#include <string.h>

#define TAG "duneos/hal_eth"

static esp_eth_handle_t      s_eth_handle  = NULL;
static esp_netif_t          *s_netif       = NULL;
static duneos_hal_eth_link_cb_t s_link_cb  = NULL;

/* Link state lives here so kernel callers (drv_eth.c) need no RTOS primitive:
 * written from the event task, read via duneos_hal_eth_get_link(), guarded by
 * a spinlock for a consistent snapshot. */
static portMUX_TYPE          s_link_mux    = portMUX_INITIALIZER_UNLOCKED;
static bool                  s_link_up     = false;
static uint32_t              s_link_mbps   = 0;
static bool                  s_link_fd     = false;

static void _on_eth_event(void *arg, esp_event_base_t base,
                          int32_t event_id, void *data)
{
    bool     up;
    uint32_t mbps;
    bool     full_duplex;

    if (event_id == ETHERNET_EVENT_CONNECTED) {
        /* Speed/duplex available via ioctl after link-up. */
        eth_speed_t  speed  = ETH_SPEED_100M;
        eth_duplex_t duplex = ETH_DUPLEX_FULL;
        esp_eth_ioctl(s_eth_handle, ETH_CMD_G_SPEED,  &speed);
        esp_eth_ioctl(s_eth_handle, ETH_CMD_G_DUPLEX_MODE, &duplex);
        up          = true;
        mbps        = (speed == ETH_SPEED_100M) ? 100 : 10;
        full_duplex = (duplex == ETH_DUPLEX_FULL);
    } else if (event_id == ETHERNET_EVENT_DISCONNECTED) {
        up = false; mbps = 0; full_duplex = false;
    } else {
        return;
    }

    portENTER_CRITICAL(&s_link_mux);
    s_link_up   = up;
    s_link_mbps = mbps;
    s_link_fd   = full_duplex;
    portEXIT_CRITICAL(&s_link_mux);

    if (s_link_cb) s_link_cb(up, mbps, full_duplex);
}

int duneos_hal_eth_init(void)
{
    /* lwIP stack and event loop must exist before the EMAC receive task starts.
     * Both calls are idempotent if the WiFi driver already ran them. */
    esp_err_t _e = esp_netif_init();
    if (_e != ESP_OK && _e != ESP_ERR_INVALID_STATE) {
        klog_e(TAG, "esp_netif_init failed: %s", esp_err_to_name(_e));
        return -EIO;
    }

    _e = esp_event_loop_create_default();
    if (_e != ESP_OK && _e != ESP_ERR_INVALID_STATE) {
        klog_e(TAG, "esp_event_loop_create_default failed: %s", esp_err_to_name(_e));
        return -EIO;
    }

    const duneos_hal_phy_config_t phy_cfg = {
        .type = DUNEOS_ETH_PHY_TYPE,
        .addr = DUNEOS_ETH_PHY_ADDR,
    };
    if (duneos_hal_phy_init(&phy_cfg) < 0) {
        klog_e(TAG, "PHY init failed");
        return -EIO;
    }

    eth_esp32_emac_config_t mac_cfg = ETH_ESP32_EMAC_DEFAULT_CONFIG();
    mac_cfg.smi_gpio.mdc_num              = DUNEOS_ETH_MDC_PIN;
    mac_cfg.smi_gpio.mdio_num             = DUNEOS_ETH_MDIO_PIN;
    mac_cfg.clock_config.rmii.clock_mode  = DUNEOS_ETH_CLK_MODE;
    mac_cfg.clock_config.rmii.clock_gpio  = DUNEOS_ETH_CLK_GPIO;

    eth_mac_config_t mac_config = ETH_MAC_DEFAULT_CONFIG();
    esp_eth_mac_t *mac = esp_eth_mac_new_esp32(&mac_cfg, &mac_config);
    if (!mac) {
        klog_e(TAG, "MAC alloc failed");
        return -ENOMEM;
    }

    esp_eth_config_t eth_cfg = ETH_DEFAULT_CONFIG(mac, duneos_hal_phy_handle());
    if (esp_eth_driver_install(&eth_cfg, &s_eth_handle) != ESP_OK) {
        klog_e(TAG, "ETH driver install failed");
        return -EIO;
    }

    klog_i(TAG, "RMII MAC OK (MDC=%d MDIO=%d CLK_MODE=%d CLK_GPIO=%d PHY@%d)",
           DUNEOS_ETH_MDC_PIN, DUNEOS_ETH_MDIO_PIN,
           (int)DUNEOS_ETH_CLK_MODE, DUNEOS_ETH_CLK_GPIO, DUNEOS_ETH_PHY_ADDR);
    return 0;
}

int duneos_hal_eth_start(void)
{
    if (!s_eth_handle) { errno = EINVAL; return -1; }

    /* event loop and lwIP already initialised in duneos_hal_eth_init(). */
    if (esp_event_handler_register(ETH_EVENT, ESP_EVENT_ANY_ID,
                                   _on_eth_event, NULL) != ESP_OK) {
        klog_e(TAG, "event handler register failed");
        return -EIO;
    }

    esp_netif_config_t netif_cfg = ESP_NETIF_DEFAULT_ETH();
    s_netif = esp_netif_new(&netif_cfg);
    if (!s_netif) {
        klog_e(TAG, "netif alloc failed");
        return -ENOMEM;
    }

    esp_eth_netif_glue_handle_t glue = esp_eth_new_netif_glue(s_eth_handle);
    if (!glue) {
        klog_e(TAG, "netif glue alloc failed");
        return -ENOMEM;
    }
    if (esp_netif_attach(s_netif, glue) != ESP_OK) {
        klog_e(TAG, "netif attach failed");
        return -EIO;
    }

    if (esp_eth_start(s_eth_handle) != ESP_OK) {
        klog_e(TAG, "esp_eth_start failed");
        return -EIO;
    }
    return 0;
}

int duneos_hal_eth_stop(void)
{
    if (!s_eth_handle) { errno = EINVAL; return -1; }
    if (esp_eth_stop(s_eth_handle) != ESP_OK) { errno = EIO; return -1; }
    return 0;
}

int duneos_hal_eth_get_mac(uint8_t mac[6])
{
    if (!s_eth_handle) { errno = EINVAL; return -1; }
    if (esp_eth_ioctl(s_eth_handle, ETH_CMD_G_MAC_ADDR, mac) != ESP_OK) {
        errno = EIO;
        return -1;
    }
    return 0;
}

int duneos_hal_eth_get_link(bool *up, uint32_t *speed_mbps, bool *full_duplex)
{
    portENTER_CRITICAL(&s_link_mux);
    bool     u = s_link_up;
    uint32_t m = s_link_mbps;
    bool     d = s_link_fd;
    portEXIT_CRITICAL(&s_link_mux);

    if (up)          *up          = u;
    if (speed_mbps)  *speed_mbps  = m;
    if (full_duplex) *full_duplex = d;
    return 0;
}

int duneos_hal_eth_set_link_callback(duneos_hal_eth_link_cb_t cb)
{
    s_link_cb = cb;
    return 0;
}
