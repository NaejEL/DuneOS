#pragma once

/*
 * Board configuration for ESP32-S3-DevKitC-1.
 *
 * Re-generate with: python tools/duneos-bspgen.py boards/esp32s3-devkitc.yaml
 */

#define DUNEOS_BOARD_NAME                        "esp32s3-devkitc"
#define DUNEOS_CPU_ESP32S3                       1
#define DUNEOS_CPU_ARCH                          "xtensa"
#define DUNEOS_ELF_MACHINE                       EM_XTENSA
#define DUNEOS_FLASH_SIZE_MB                     8
#define DUNEOS_PSRAM_SIZE_MB                     8

/* ---------- UART0 (console) ---------- */
#define DUNEOS_UART0_TX_PIN     43
#define DUNEOS_UART0_RX_PIN     44
#define DUNEOS_UART0_BAUD       115200

/* ---------- SPI2 (SD card) ---------- */
#define DUNEOS_SD_SPI_HOST      SPI2_HOST
#define DUNEOS_SD_MOSI_PIN      11
#define DUNEOS_SD_MISO_PIN      13
#define DUNEOS_SD_CLK_PIN       12
#define DUNEOS_SD_CS_PIN        10
#define DUNEOS_SD_FREQ_KHZ      20000   /* 20 MHz — safe default, raise if stable */
#define DUNEOS_SD_CD_PIN        (-1)    /* card detect not wired */

/* ---------- SPI3 raw bus (/dev/spi-1) ---------- */
#define DUNEOS_HAVE_SPI         1
#define DUNEOS_SPI1_HOST        SPI3_HOST
#define DUNEOS_SPI1_MOSI_PIN    15
#define DUNEOS_SPI1_MISO_PIN    16
#define DUNEOS_SPI1_CLK_PIN     18
#define DUNEOS_SPI1_MAX_FREQ_HZ 40000000

/* ---------- I2C0 (general-purpose header pins) ---------- */
#define DUNEOS_HAVE_I2C         1
#define DUNEOS_I2C0_SDA_PIN     8
#define DUNEOS_I2C0_SCL_PIN     9
#define DUNEOS_I2C0_FREQ_HZ     400000

/* ---------- Status LED ---------- */
#define DUNEOS_LED_STATUS_PIN   2
#define DUNEOS_LED_ACTIVE_HIGH  1
