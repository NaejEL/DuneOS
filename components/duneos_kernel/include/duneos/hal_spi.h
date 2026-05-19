#pragma once

/*
 * DuneOS Hardware Interface — SPI
 *
 * Pure-C API: no esp_err_t, no proprietary types.
 * The ESP-IDF implementation lives in arch/xtensa_esp32s3/hal/hal_spi.c.
 *
 * Returns: 0 / pointer on success; -1 / NULL on failure (errno set).
 */

#include <stdint.h>
#include <stddef.h>

typedef struct duneos_hal_spi      duneos_hal_spi_t;      /* bus handle  */
typedef struct duneos_hal_spi_dev  duneos_hal_spi_dev_t;  /* device handle */

typedef struct {
    int  bus_id;           /* Platform bus ID (integer).
                            * On ESP32: 1 = SPI2_HOST, 2 = SPI3_HOST.       */
    int  mosi_pin;
    int  miso_pin;         /* -1 if MISO is not used                        */
    int  clk_pin;
    int  max_transfer_sz;  /* Max DMA transfer in bytes; 0 = driver default  */
} duneos_hal_spi_bus_config_t;

typedef struct {
    int      cs_pin;   /* Chip-select GPIO; -1 if CS is managed externally  */
    uint8_t  mode;     /* SPI mode 0–3                                       */
    uint8_t  _pad[3];
    uint32_t freq_hz;  /* Clock frequency                                    */
} duneos_hal_spi_dev_config_t;  /* 12 bytes */

/* Initialise a new SPI bus (pins and DMA). Returns NULL on failure. */
duneos_hal_spi_t *duneos_hal_spi_bus_init(const duneos_hal_spi_bus_config_t *cfg);

/* Attach to an already-initialised bus without re-running hardware init.
 * Used when the bus was initialised by another subsystem (e.g. SD card). */
duneos_hal_spi_t *duneos_hal_spi_bus_attach(int bus_id);

void duneos_hal_spi_bus_deinit(duneos_hal_spi_t *bus);

/* Add a device (CS line + protocol settings) to a bus. Returns NULL on failure. */
duneos_hal_spi_dev_t *duneos_hal_spi_dev_add   (duneos_hal_spi_t *bus,
                                                 const duneos_hal_spi_dev_config_t *cfg);

/* Replace the device config (removes and re-adds it). Returns 0 on success. */
int  duneos_hal_spi_dev_reconfig(duneos_hal_spi_dev_t *dev,
                                  const duneos_hal_spi_dev_config_t *cfg);

void duneos_hal_spi_dev_remove(duneos_hal_spi_dev_t *dev);

/* Full-duplex transfer. tx_buf or rx_buf may be NULL. Returns 0 on success. */
int  duneos_hal_spi_transfer(duneos_hal_spi_dev_t *dev,
                              const void *tx_buf, void *rx_buf, size_t len);
