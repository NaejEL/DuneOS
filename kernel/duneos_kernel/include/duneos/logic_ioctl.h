#pragma once

/*
 * Logic-capture device API for /dev/logic0 — see ADR 020.
 *
 * Synchronous multi-line sampling, distinct from /dev/gpiochip0 (which is the
 * line-event / set-value interface). Use this to capture a serial bus (I2C, SPI,
 * 1-Wire, bit-bang) reliably: the lines are sampled at a fixed cadence faster
 * than the bus, so no edge is dropped and every level is correct — unlike
 * per-edge GPIO interrupts, which can't keep up at clock rates.
 *
 * Usage:
 *   int fd = open("/dev/logic0", O_RDONLY);
 *   logic_config_t cfg = {
 *       .channel_gpio = { SCL_PIN, SDA_PIN }, .n_channels = 2,
 *       .idle_us = 2000, .hard_cap_us = 50000, .trigger_timeout_us = 200000,
 *   };
 *   ioctl(fd, LOGIC_SET_CONFIG, &cfg);
 *   logic_sample_t buf[512];
 *   for (;;) {
 *       int n = read(fd, buf, sizeof(buf));   // one capture window
 *       if (n == 0) { check_ui_abort(); continue; }   // idle, no activity
 *       // buf[0..n/sizeof(logic_sample_t)) are transitions; channel i = bit i
 *   }
 *   close(fd);
 */

#include <stdint.h>

extern int ioctl(int fd, unsigned long request, ...);

#define LOGIC_MAX_CHANNELS 8

/* ioctl command codes */
#define LOGIC_SET_CONFIG  0x01  /* ← logic_config_t                         */
#define LOGIC_GET_INFO    0x02  /* → logic_info_t                           */

/*
 * Capture configuration. channel_gpio[i] is the physical GPIO sampled into bit
 * i of each record's `levels`. Timeouts are in microseconds.
 */
typedef struct {
    uint8_t  channel_gpio[LOGIC_MAX_CHANNELS]; /* GPIO per channel/bit       */
    uint8_t  n_channels;                       /* 1..LOGIC_MAX_CHANNELS      */
    uint8_t  _pad[3];
    uint32_t idle_us;             /* end window after this much no-change
                                     post-trigger (0 = until full / hard cap) */
    uint32_t hard_cap_us;         /* absolute window ceiling; kept below the
                                     interrupt watchdog. 0 → driver default   */
    uint32_t trigger_timeout_us;  /* give up waiting for first change; read()
                                     returns 0. 0 → driver default            */
} logic_config_t;

/*
 * One transition record: the channel mask sampled the instant it changed.
 * `t_us` is relative to the first sample of the current read() window. The
 * first record of a window carries the initial state at t_us = 0.
 */
typedef struct {
    uint32_t t_us;    /* microseconds since window start          */
    uint32_t levels;  /* bit i = level of channel i (0/1)         */
} logic_sample_t;

/*
 * Returned by LOGIC_GET_INFO. sample_khz is the polled sampler's measured
 * effective rate (a guide; record timestamps are exact CPU-cycle values, so the
 * capture is not quantised to this rate).
 */
typedef struct {
    uint8_t  max_channels;
    uint8_t  _pad[3];
    uint32_t sample_khz;
} logic_info_t;
