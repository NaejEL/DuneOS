/*
 * libld2450 — see <duneos/ld2450.h>.
 *
 * Reference: HLK-LD2450 Serial Communication Protocol (Hi-Link, V1.02).
 */

#include "duneos/ld2450.h"

#include <errno.h>
#include <fcntl.h>
#include <string.h>
#include <unistd.h>

/* usleep is XSI-obsolescent — PicoLibc hides it under _POSIX_C_SOURCE.
 * The kernel exports it (symbols.c); declare it like the other apps do. */
extern int usleep(unsigned int useconds);

static const uint8_t k_hdr[4]  = { 0xAA, 0xFF, 0x03, 0x00 };

/* Config command frames (protocol §"Control commands"): header FD FC FB FA,
 * length LE16, command word LE16 (+ value), tail 04 03 02 01. Every command
 * must be bracketed by enable-config (0x00FF, value 0x0001) and end-config
 * (0x00FE). Multi-target tracking is command word 0x0090. */
static const uint8_t k_cmd_enable_cfg[] = {
    0xFD, 0xFC, 0xFB, 0xFA, 0x04, 0x00, 0xFF, 0x00, 0x01, 0x00,
    0x04, 0x03, 0x02, 0x01,
};
static const uint8_t k_cmd_multi_target[] = {
    0xFD, 0xFC, 0xFB, 0xFA, 0x02, 0x00, 0x90, 0x00,
    0x04, 0x03, 0x02, 0x01,
};
static const uint8_t k_cmd_end_cfg[] = {
    0xFD, 0xFC, 0xFB, 0xFA, 0x02, 0x00, 0xFE, 0x00,
    0x04, 0x03, 0x02, 0x01,
};

/* ----- pure decoding ------------------------------------------------------ */

static uint16_t le16(const uint8_t *p)
{
    return (uint16_t)(p[0] | (p[1] << 8));
}

/* LD2450 sign-flag int16: bit15 set → +(low 15 bits), clear → -raw. */
static int16_t sign15(uint16_t raw)
{
    return (raw & 0x8000u) ? (int16_t)(raw & 0x7FFFu) : (int16_t)-(int32_t)raw;
}

int ld2450_decode_frame(const uint8_t frame[LD2450_FRAME_BYTES],
                        ld2450_frame_t *out)
{
    if (memcmp(frame, k_hdr, sizeof(k_hdr)) != 0 ||
        frame[28] != 0x55 || frame[29] != 0xCC)
        return -EINVAL;

    for (int i = 0; i < LD2450_MAX_TARGETS; i++) {
        const uint8_t   *b = frame + 4 + i * 8;
        ld2450_target_t *t = &out->target[i];

        bool empty = true;
        for (int j = 0; j < 8; j++)
            if (b[j] != 0) { empty = false; break; }

        if (empty) {
            t->present   = false;
            t->x_mm      = 0;
            t->y_mm      = 0;
            t->speed_cms = 0;
            t->res_mm    = 0;
            continue;
        }
        t->present   = true;
        t->x_mm      = sign15(le16(b));
        t->y_mm      = sign15(le16(b + 2));
        t->speed_cms = sign15(le16(b + 4));
        t->res_mm    = le16(b + 6);
    }
    return 0;
}

void ld2450_parser_init(ld2450_parser_t *p)
{
    p->pos = 0;
}

int ld2450_parser_feed(ld2450_parser_t *p, uint8_t b, ld2450_frame_t *out)
{
    if (p->pos < sizeof(k_hdr)) {
        if (b == k_hdr[p->pos]) {
            p->buf[p->pos++] = b;
        } else {
            /* Resync — a mismatched byte may itself start a new header. */
            p->pos = (b == k_hdr[0]) ? 1 : 0;
            p->buf[0] = b;
        }
        return 0;
    }

    p->buf[p->pos++] = b;
    if (p->pos < LD2450_FRAME_BYTES)
        return 0;

    p->pos = 0;
    return ld2450_decode_frame(p->buf, out) == 0;
}

/* ----- serial I/O --------------------------------------------------------- */

int ld2450_open(const char *dev_path)
{
    int fd = open(dev_path, O_RDWR);
    return fd >= 0 ? fd : -errno;
}

static int write_all(int fd, const uint8_t *buf, int len)
{
    int off = 0;
    while (off < len) {
        int n = write(fd, buf + off, (unsigned)(len - off));
        if (n < 0) return -errno;
        if (n == 0) return -EIO;
        off += n;
    }
    return 0;
}

int ld2450_set_multi_target(int fd)
{
    int r;
    /* The sensor needs a beat to ACK each config command; 60 ms is well
     * above its response time and keeps total setup under 200 ms. */
    if ((r = write_all(fd, k_cmd_enable_cfg,  sizeof(k_cmd_enable_cfg)))  < 0) return r;
    usleep(60 * 1000);
    if ((r = write_all(fd, k_cmd_multi_target, sizeof(k_cmd_multi_target))) < 0) return r;
    usleep(60 * 1000);
    if ((r = write_all(fd, k_cmd_end_cfg,     sizeof(k_cmd_end_cfg)))     < 0) return r;
    usleep(60 * 1000);
    return 0;
}

int ld2450_read(int fd, ld2450_parser_t *p, ld2450_frame_t *out)
{
    uint8_t chunk[64];   /* stack buffer — see CLAUDE.md on static read buffers */
    int n = read(fd, chunk, sizeof(chunk));
    if (n < 0) return -errno;

    int got = 0;
    for (int i = 0; i < n; i++)
        if (ld2450_parser_feed(p, chunk[i], out))
            got = 1;
    return got;
}

int ld2450_close(int fd)
{
    return close(fd) == 0 ? 0 : -errno;
}
