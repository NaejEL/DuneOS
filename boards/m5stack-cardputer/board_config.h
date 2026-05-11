#pragma once

/*
 * Board configuration for M5Stack CardPuter.
 *
 * Hardware: ESP32-S3FN8, 8MB Flash (octal), no PSRAM
 * Re-generate with: python tools/duneos-bspgen.py boards/m5stack-cardputer.yaml
 */

#define DUNEOS_BOARD_NAME                        "m5stack-cardputer"
#define DUNEOS_CPU_ESP32S3                       1
#define DUNEOS_CPU_ARCH                          "xtensa"
#define DUNEOS_ELF_MACHINE                       EM_XTENSA
#define DUNEOS_FLASH_SIZE_MB                     8
#define DUNEOS_PSRAM_SIZE_MB                     0

/* ---------- UART0 (Not USB, use external UART) ---------- */
#define DUNEOS_UART0_TX_PIN     43
#define DUNEOS_UART0_RX_PIN     44
#define DUNEOS_UART0_BAUD       115200

/* ---------- SPI2 (SD card) — verify against schematic ---------- */
#define DUNEOS_SD_SPI_HOST      SPI2_HOST
#define DUNEOS_SD_MOSI_PIN      14
#define DUNEOS_SD_MISO_PIN      39
#define DUNEOS_SD_CLK_PIN       40
#define DUNEOS_SD_CS_PIN        12
#define DUNEOS_SD_FREQ_KHZ      20000
#define DUNEOS_SD_CD_PIN        (-1)

/* ---------- I2C0 (keyboard controller, IMU) ---------- */
#define DUNEOS_HAVE_I2C         1
#define DUNEOS_I2C0_SDA_PIN     13
#define DUNEOS_I2C0_SCL_PIN     15
#define DUNEOS_I2C0_FREQ_HZ     400000

/* ---------- Display (ST7789, 240x135, dedicated SPI3 bus) ---------- */
#define DUNEOS_DISPLAY_WIDTH    240
#define DUNEOS_DISPLAY_HEIGHT   135
#define DUNEOS_DISPLAY_CS_PIN   37
#define DUNEOS_DISPLAY_DC_PIN   35
#define DUNEOS_DISPLAY_RST_PIN  33
#define DUNEOS_DISPLAY_BL_PIN   38
#define DUNEOS_DISPLAY_MOSI_PIN 6
#define DUNEOS_DISPLAY_CLK_PIN  5
#define DUNEOS_DISPLAY_SPI_HOST SPI3_HOST
#define DUNEOS_DISPLAY_FREQ_HZ  40000000

/* ---------- Keyboard (I2C, addr 0x55) ---------- */
#define DUNEOS_KB_I2C_ADDR      0x55

/* ---------- Battery (TP4057 + ADC voltage divider) ---------- */
/* VBAT_SENSE = VBAT/2 on GPIO10 = ADC1_CH9. TP4057 CHRG not wired to GPIO. */
#define DUNEOS_HAVE_BATTERY              1
#define DUNEOS_BATTERY_TYPE_ADC_SIMPLE   1
#define DUNEOS_BATTERY_ADC_UNIT          ADC_UNIT_1
#define DUNEOS_BATTERY_ADC_CHANNEL       9
#define DUNEOS_BATTERY_VDIV_FACTOR       2
#define DUNEOS_BATTERY_FULL_MV           4200
#define DUNEOS_BATTERY_EMPTY_MV          3300
#define DUNEOS_BATTERY_CHRG_GPIO         (-1)

/* ---------- Onboard WS2812 LED ---------- */
#define DUNEOS_LED_STATUS_PIN   21
#define DUNEOS_LED_ACTIVE_HIGH  1
