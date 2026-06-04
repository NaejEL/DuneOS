#pragma once

/*
 * DuneOS Hardware Interface — Ethernet (medium-agnostic MAC control)
 *
 * Pure-C API: no esp_err_t, no RMII/SPI medium details.
 * Medium-specific wiring (MDC/MDIO pins, CLK mode, SPI CS …) is read from
 * board_config.h internally by the arch implementation.
 *
 * Implementations:
 *   arch/xtensa_esp32/hal/hal_eth.c  — RMII (ESP32 integrated MAC)
 *   arch/xtensa_esp32s3/hal/hal_eth.c — SPI Ethernet (future: W5500, …)
 *
 * Phase boundary: duneos_hal_eth_start() attaches to esp_netif pre-Phase 27.
 * Phase 27 migrates the network stack to vendored lwIP + duneos_netif_*.
 *
 * Returns: 0 on success, -errno on failure.
 */

#include <stdint.h>
#include <stdbool.h>

/*
 * Called by the kernel when Ethernet link state changes.
 * Invoked from an ESP-IDF event task — do not call blocking DuneOS APIs.
 */
typedef void (*duneos_hal_eth_link_cb_t)(bool up, uint32_t speed_mbps, bool full_duplex);

/*
 * Initialise the Ethernet MAC and PHY.
 * Reads all medium-specific config (pins, PHY address, clock mode) from
 * board_config.h — no parameters needed.
 * Must be called before duneos_hal_eth_start().
 */
int duneos_hal_eth_init(void);

/* Start the Ethernet interface (enables RX/TX, attaches netif). */
int duneos_hal_eth_start(void);

/* Stop the Ethernet interface (disables RX/TX, detaches netif). */
int duneos_hal_eth_stop(void);

/* Read the MAC address burned into the chip. mac must be 6 bytes. */
int duneos_hal_eth_get_mac(uint8_t mac[6]);

/*
 * Register a link-state callback. Only one callback is supported per arch
 * implementation. Pass NULL to deregister.
 * Must be called before duneos_hal_eth_start() to avoid missing the first
 * link-up event.
 */
int duneos_hal_eth_set_link_callback(duneos_hal_eth_link_cb_t cb);
