/*
 * drv_gpiochip_mcp23017 — STUB awaiting hardware.
 *
 * The Microchip MCP23017 is a 16-bit I2C GPIO expander similar in spirit to
 * the SX1509 (separate DIR/DATA/PULLUP registers), but the project does not
 * yet own a board with this chip. Implementing without hardware to test
 * against is a known anti-pattern (silent register-encoding bugs).
 *
 * This file exists so:
 *   1. CONFIG_DUNEOS_DRV_GPIOCHIP_MCP23017 has a compile target.
 *   2. vfs_dev.c's #ifdef wiring matches the SX1509/PCF8574 pattern.
 *   3. Tests that enable the option still compile clean.
 *
 * When a developer obtains MCP23017 hardware:
 *   - copy drv_gpiochip_sx1509.c as a starting template,
 *   - adjust register addresses (MCP23017 has banked registers: IODIRA=0x00,
 *     GPPUA=0x0C, GPIOA=0x12, OLATA=0x14, …, with offset 0x01 for B-bank),
 *   - test against real silicon (logic-analyser the I2C bus on init + RMW),
 *   - replace this stub with the working implementation.
 */

#include "duneos/klog.h"

#define TAG "duneos/gpiochip-mcp23017"

void drv_gpiochip_mcp23017_register(void)
{
    klog_w(TAG, "MCP23017 driver is a stub (not implemented). "
                "Enable only if you intend to write the driver — "
                "see drv_gpiochip_mcp23017.c for the recipe.");
}
