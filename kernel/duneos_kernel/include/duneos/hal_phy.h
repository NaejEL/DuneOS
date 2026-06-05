#pragma once

/*
 * DuneOS Hardware Interface — MDIO PHY (internal to RMII arch implementations)
 *
 * This header is an internal abstraction used by arch/xtensa_esp32/hal/hal_eth.c.
 * It is NOT part of the public DuneOS driver API — drv_eth.c uses only hal_eth.h.
 *
 * Maps to IDF esp_eth_phy_new_* factory functions; the dispatch is done in
 * hal_phy.c based on DUNEOS_ETH_PHY_TYPE from board_config.h.
 *
 * Returns: 0 on success, -errno on failure.
 */

#include <stdint.h>
#include <stdbool.h>

typedef enum {
    DUNEOS_ETH_PHY_LAN8720,   /* LAN87xx family (Kincony A16)          */
    DUNEOS_ETH_PHY_KSZ8081,   /* Microchip KSZ8081                     */
    DUNEOS_ETH_PHY_RTL8201,   /* Realtek RTL8201                       */
    DUNEOS_ETH_PHY_IP101,     /* IC Plus IP101G                        */
} duneos_eth_phy_type_t;

typedef struct {
    duneos_eth_phy_type_t type;
    uint8_t               addr;   /* MDIO address 0–31 */
} duneos_hal_phy_config_t;

/*
 * Initialise the PHY via the IDF factory for cfg->type.
 * Stores the resulting handle internally; subsequent calls from hal_eth.c
 * retrieve it via duneos_hal_phy_handle().
 */
int duneos_hal_phy_init(const duneos_hal_phy_config_t *cfg);

/*
 * Return the opaque esp_eth_phy_handle_t created by duneos_hal_phy_init().
 * Returns NULL if not yet initialised.
 */
void *duneos_hal_phy_handle(void);

/* Query current link state via the PHY's MDIO registers. */
int duneos_hal_phy_get_link(bool *up, uint32_t *speed_mbps, bool *full_duplex);

/* Assert and release the PHY reset line. */
int duneos_hal_phy_reset(void);
