# arch/xtensa_esp32/arch.cmake
#
# Self-selecting arch module for plain ESP32 (Xtensa LX6) / ESP-IDF.
# Adds ESP32-specific HAL implementations not shared with ESP32-S3.
#
# Standard HAL files (uart/gpio/i2c/spi/adc/time/encoder) are compiled via
# xtensa_esp32s3/arch.cmake which fires for all Xtensa IDF builds via
# CONFIG_IDF_TARGET_ARCH_XTENSA / IDF_TARGET_ARCH.
#
# Guard: CONFIG_IDF_TARGET_ESP32 (normal build phase) or DUNEOS_ARCH (dbt / Phase 28+).
# NOTE: CONFIG_IDF_TARGET_ESP32 is not available in the requirements phase;
# xtensa_esp32s3/arch.cmake adds esp_eth/esp_netif to REQUIRES for all Xtensa targets
# so the Ethernet headers are always present at configure time.

if(NOT CONFIG_IDF_TARGET_ESP32
   AND NOT DUNEOS_ARCH STREQUAL "xtensa_esp32")
    return()
endif()

# ----- Non-IDF / dbt builds (Phase 28+): add standard Xtensa HAL -----
# For IDF builds, xtensa_esp32s3/arch.cmake already adds these (it fires for all
# Xtensa via CONFIG_IDF_TARGET_ARCH_XTENSA) — adding them twice would cause a
# duplicate-source error, so this block is DUNEOS_ARCH-gated only.
if(DUNEOS_ARCH STREQUAL "xtensa_esp32")
    set(_S3_HAL "${CMAKE_CURRENT_LIST_DIR}/../xtensa_esp32s3/hal")
    list(APPEND DUNEOS_KERNEL_SRCS
        "${_S3_HAL}/hal_uart.c"
        "${_S3_HAL}/hal_gpio.c"
        "${_S3_HAL}/hal_i2c.c"
        "${_S3_HAL}/hal_spi.c"
        "${_S3_HAL}/hal_adc.c"
        "${_S3_HAL}/hal_time.c"
        "${_S3_HAL}/hal_encoder.c"
    )
    list(APPEND DUNEOS_KERNEL_REQUIRES
        xtensa
        driver
        esp_driver_uart
        esp_driver_gpio
        esp_driver_i2c
        esp_driver_spi
        esp_driver_sdspi
        esp_driver_pcnt
        esp_adc
        esp_timer
        esp_eth
        esp_netif
    )
endif()

# ----- ESP32-specific: RMII Ethernet HAL -----
# Only compiled when the Ethernet driver is enabled. The plain ESP32 has an
# integrated RMII MAC; ESP32-S3 does not (SPI Ethernet, future separate impl).
if(CONFIG_DUNEOS_DRV_ETH)
    list(APPEND DUNEOS_KERNEL_SRCS
        "${CMAKE_CURRENT_LIST_DIR}/hal/hal_eth.c"
        "${CMAKE_CURRENT_LIST_DIR}/hal/hal_phy.c"
    )
    list(APPEND DUNEOS_KERNEL_REQUIRES
        espressif__lan87xx
        espressif__ksz80xx
        espressif__rtl8201
        espressif__ip101
    )
endif()
