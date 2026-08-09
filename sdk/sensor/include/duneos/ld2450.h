#pragma once

/*
 * libld2450 — HLK-LD2450 24 GHz mmWave radar (UART 256000 8N1, ~10 Hz).
 *
 * Reference: HLK-LD2450 Serial Communication Protocol (Hi-Link, V1.02).
 *
 * Report frame (30 bytes):
 *   AA FF 03 00 | 3 × 8-byte target blocks | 55 CC
 * Each block: X (2B), Y (2B), speed (2B), distance resolution (2B), all
 * little-endian. X/Y (mm) and speed (cm/s) use the LD2450 sign-flag
 * encoding: bit15 set → positive, magnitude in the low 15 bits; bit15
 * clear → negative of the raw value (protocol §"Radar data output",
 * worked example: raw 0x86B1 → +1713 mm, raw 0x030E → -782 mm).
 * An absent target slot is 8 zero bytes.
 *
 * Two deliberately separate layers:
 *   - pure decoding: ld2450_parser_feed() / ld2450_decode_frame() are
 *     I/O-free and resynchronise on the frame header, so they can be
 *     exercised on a byte vector (garbage prefix included);
 *   - I/O: ld2450_open() / ld2450_set_multi_target() / ld2450_read()
 *     drive the serial device and feed the parser. ld2450_read() relies
 *     on the UART HAL's bounded read timeout (~100 ms) — it never spins.
 *
 * Errors follow ADR 001: 0 (or >= 0) on success, -errno on failure.
 */

#include <stdint.h>
#include <stdbool.h>

#define LD2450_MAX_TARGETS   3
#define LD2450_FRAME_BYTES   30
#define LD2450_BAUD          256000

typedef struct {
    bool     present;      /* false: this slot reported no target */
    int16_t  x_mm;         /* + right / - left of boresight       */
    int16_t  y_mm;         /* distance ahead of the sensor        */
    int16_t  speed_cms;    /* radial, + moving away / - closing   */
    uint16_t res_mm;       /* distance gate resolution            */
} ld2450_target_t;

typedef struct {
    ld2450_target_t target[LD2450_MAX_TARGETS];
} ld2450_frame_t;

typedef struct {
    uint8_t buf[LD2450_FRAME_BYTES];
    uint8_t pos;
} ld2450_parser_t;

/* ----- pure decoding (no I/O) -------------------------------------------- */

void ld2450_parser_init(ld2450_parser_t *p);

/* Feed one byte from the serial stream. Returns 1 when `b` completed a
 * valid frame (decoded into *out), 0 otherwise. Bytes that don't fit the
 * header/tail are discarded — the parser resynchronises on AA FF 03 00. */
int ld2450_parser_feed(ld2450_parser_t *p, uint8_t b, ld2450_frame_t *out);

/* Decode one already-framed 30-byte report. 0 on success, -EINVAL when
 * header or tail don't match. */
int ld2450_decode_frame(const uint8_t frame[LD2450_FRAME_BYTES],
                        ld2450_frame_t *out);

/* ----- serial I/O --------------------------------------------------------- */

/* Open the radar's serial device (e.g. "/dev/uart1").
 * Returns fd >= 0, or -errno. */
int ld2450_open(const char *dev_path);

/* Enable multi-target tracking (enable-config / multi-target / end-config
 * command sequence, protocol §"Control commands"). The sensor's ACK frames
 * are ignored; the report parser discards them naturally during resync.
 * 0 on success, -errno on write failure. */
int ld2450_set_multi_target(int fd);

/* Read whatever the UART currently holds (bounded by the HAL read timeout)
 * and run it through the parser. Returns 1 when at least one complete frame
 * was decoded into *out (the most recent one), 0 when no frame completed,
 * -errno on read error. */
int ld2450_read(int fd, ld2450_parser_t *p, ld2450_frame_t *out);

/* Close the serial device. 0 on success, -errno. */
int ld2450_close(int fd);
