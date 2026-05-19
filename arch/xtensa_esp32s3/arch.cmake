# arch/xtensa_esp32s3/arch.cmake
#
# Self-selecting arch module for Xtensa ESP32-S3 / ESP-IDF.
# Included automatically by duneos_kernel/CMakeLists.txt via file(GLOB).
# Returns immediately when building for a different architecture.
#
# To add a new architecture: create arch/<new_arch>/arch.cmake — zero changes
# to duneos_kernel/CMakeLists.txt or any other core file.
#
# Guard uses three independent variables to survive both ESP-IDF build phases:
#   CONFIG_IDF_TARGET_ARCH_XTENSA — set from sdkconfig.cmake in normal build phase
#   IDF_TARGET_ARCH                — set via __build_write_properties in the
#                                    requirements phase (sdkconfig.cmake is NOT
#                                    loaded there, so CONFIG_* vars are empty)
#   DUNEOS_ARCH                    — set by non-ESP-IDF toolchain plugins (dbt)
if(NOT CONFIG_IDF_TARGET_ARCH_XTENSA
   AND NOT IDF_TARGET_ARCH STREQUAL "xtensa"
   AND NOT DUNEOS_ARCH STREQUAL "xtensa_esp32s3")
    return()
endif()

# HAL source implementations.  Public headers live in duneos_kernel/include/duneos/
# with no ESP-IDF types; ESP-IDF is contained entirely in these .c files.
list(APPEND DUNEOS_KERNEL_SRCS
    "${CMAKE_CURRENT_LIST_DIR}/hal/hal_uart.c"
    "${CMAKE_CURRENT_LIST_DIR}/hal/hal_gpio.c"
    "${CMAKE_CURRENT_LIST_DIR}/hal/hal_i2c.c"
    "${CMAKE_CURRENT_LIST_DIR}/hal/hal_spi.c"
    "${CMAKE_CURRENT_LIST_DIR}/hal/hal_adc.c"
    "${CMAKE_CURRENT_LIST_DIR}/hal/hal_time.c"
    "${CMAKE_CURRENT_LIST_DIR}/hal/hal_encoder.c"
)

# ESP-IDF component deps required by the HAL implementations above.
# These are ARCH-SCOPED: a RISC-V arch.cmake would list different deps here.
# Phase 26: driver + esp_driver_spi + esp_driver_sdspi removable once vfs.c
#           SD init migrates from direct SPI to hal_spi/hal_gpio.
list(APPEND DUNEOS_KERNEL_REQUIRES
    xtensa
    driver
    esp_driver_uart
    esp_driver_gpio
    esp_driver_i2c
    esp_driver_spi
    esp_driver_sdspi    # vfs.c SD SPI init — Phase 26 (VFS natif)
    esp_driver_pcnt
    esp_adc
    esp_timer
)
