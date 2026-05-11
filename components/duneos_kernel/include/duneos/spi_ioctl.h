#pragma once

#include <stdint.h>
#include <stddef.h>

/*
 * DuneOS SPI raw bus interface — /dev/spi-1
 *
 * Usage:
 *   int fd = open("/dev/spi-1", O_RDWR);
 *   int cs = 4;
 *   ioctl(fd, SPI_SET_CS, &cs);
 *   uint32_t hz = 1000000;
 *   ioctl(fd, SPI_SET_SPEED, &hz);
 *   write(fd, tx_buf, len);                   // transmit only, rx discarded
 *   spi_xfer_t xfr = { tx, rx, len };
 *   ioctl(fd, SPI_TRANSFER, &xfr);            // full-duplex
 *   close(fd);
 *
 * CS is asserted automatically around each write() / ioctl(SPI_TRANSFER).
 * Use SPI_SET_CS(-1) to disable hardware CS (bit-bang CS in userspace).
 */

#define SPI_SET_MODE   0x01  /* arg: uint8_t*  — CPOL/CPHA mode 0–3 */
#define SPI_SET_SPEED  0x02  /* arg: uint32_t* — clock frequency in Hz */
#define SPI_SET_CS     0x03  /* arg: int*      — CS GPIO pin (-1 = none) */
#define SPI_TRANSFER   0x04  /* arg: spi_xfer_t* — simultaneous tx+rx */

typedef struct {
    const uint8_t *tx_buf;  /* data to send; NULL → clock out zeros */
    uint8_t       *rx_buf;  /* receive buffer; NULL → discard */
    size_t         len;     /* bytes to transfer */
} spi_xfer_t;
