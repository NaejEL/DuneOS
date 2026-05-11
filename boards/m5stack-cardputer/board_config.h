#pragma once

/*
 * Board configuration for M5Stack CardPuter.
 *
 * Hardware: ESP32-S3FN8 — 8MB embedded flash (octal), NO external PSRAM.
 * Re-generate with: python tools/duneos-bspgen.py boards/m5stack-cardputer.yaml
 */

#define DUNEOS_BOARD_NAME                        "m5stack-cardputer"
#define DUNEOS_CPU_ESP32S3                       1
#define DUNEOS_CPU_ARCH                          "xtensa"
#define DUNEOS_ELF_MACHINE                       EM_XTENSA
#define DUNEOS_FLASH_SIZE_MB                     8
#define DUNEOS_PSRAM_SIZE_MB                     0

/* ---------- UART0 (USB Serial/JTAG console on GPIO19/20, not these pins) --- */
#define DUNEOS_UART0_TX_PIN     43
#define DUNEOS_UART0_RX_PIN     44
#define DUNEOS_UART0_BAUD       115200

/* ---------- SPI2 (SD card) ------------------------------------------------ */
#define DUNEOS_SD_SPI_HOST      SPI2_HOST
#define DUNEOS_SD_MOSI_PIN      14
#define DUNEOS_SD_MISO_PIN      39
#define DUNEOS_SD_CLK_PIN       40
#define DUNEOS_SD_CS_PIN        12
#define DUNEOS_SD_FREQ_KHZ      20000
#define DUNEOS_SD_CD_PIN        (-1)

/* ---------- Display (ST7789, 240×135, dedicated SPI3 bus) ----------------- */
#define DUNEOS_DISPLAY_WIDTH    240
#define DUNEOS_DISPLAY_HEIGHT   135
#define DUNEOS_DISPLAY_SPI_HOST SPI3_HOST
#define DUNEOS_DISPLAY_MOSI_PIN 6
#define DUNEOS_DISPLAY_CLK_PIN  5
#define DUNEOS_DISPLAY_CS_PIN   37
#define DUNEOS_DISPLAY_DC_PIN   35
#define DUNEOS_DISPLAY_RST_PIN  33
#define DUNEOS_DISPLAY_BL_PIN   38
#define DUNEOS_DISPLAY_FREQ_HZ  40000000

/* ---------- Keyboard matrix (IOMatrix + 74HC138 row decoder) -------------- */
/*
 * Scanning: set A0/A1/A2 to binary value 0-7 → 74HC138 drives row Y(n) LOW.
 * Read 7 column pins (active LOW, pull-up): pressed key pulls column LOW.
 * Physical map: output 0-3 = even columns (left half), output 4-7 = odd cols.
 *   row   = output % 4
 *   col   = bit_index * 2 + (output >= 4 ? 1 : 0)
 * Logical matrix: 4 rows × 14 columns.
 */
#define DUNEOS_HAVE_KEYBOARD_MATRIX     1
#define DUNEOS_KB_ROW_A0_PIN            8
#define DUNEOS_KB_ROW_A1_PIN            9
#define DUNEOS_KB_ROW_A2_PIN            11
#define DUNEOS_KB_COL_PINS              {13, 15, 3, 4, 5, 6, 7}
#define DUNEOS_KB_NUM_COLS              7
#define DUNEOS_KB_MATRIX_ROWS           4
#define DUNEOS_KB_MATRIX_COLS           14

/* ---------- Battery (TP4057 + ADC voltage divider) ------------------------ */
/* VBAT_SENSE = VBAT/2 on GPIO10 = ADC1_CH9. TP4057 CHRG not wired to GPIO. */
#define DUNEOS_HAVE_BATTERY              1
#define DUNEOS_BATTERY_TYPE_ADC_SIMPLE   1
#define DUNEOS_BATTERY_ADC_UNIT          ADC_UNIT_1
#define DUNEOS_BATTERY_ADC_CHANNEL       9
#define DUNEOS_BATTERY_VDIV_FACTOR       2
#define DUNEOS_BATTERY_FULL_MV           4200
#define DUNEOS_BATTERY_EMPTY_MV          3300
#define DUNEOS_BATTERY_CHRG_GPIO         (-1)

/* ---------- Onboard WS2812 LED -------------------------------------------- */
#define DUNEOS_LED_STATUS_PIN   21
#define DUNEOS_LED_ACTIVE_HIGH  1
