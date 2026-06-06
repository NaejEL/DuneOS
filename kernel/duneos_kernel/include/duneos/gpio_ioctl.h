#pragma once

/*
 * GPIO ioctl API for /dev/gpiochipN — uniform across native GPIO and expanders.
 *
 * Apps open the chip device, then drive all GPIO operations through ioctl().
 * This header is part of the public DuneOS SDK; include it in app code as:
 *   #include <duneos/gpio_ioctl.h>
 *
 * Usage pattern:
 *   int fd = open("/dev/gpiochip0", O_RDWR);
 *   gpio_req_t req = { .line = 21, .dir = GPIO_DIR_OUTPUT };
 *   ioctl(fd, GPIOCHIP_SET_DIR, &req);
 *   req.val = 1;
 *   ioctl(fd, GPIOCHIP_SET_VALUE, &req);
 *   close(fd);
 */

#include <stdint.h>

/* ioctl() is exported by the kernel ABI; declare it here for -nostdlib apps. */
extern int ioctl(int fd, unsigned long request, ...);

/* ioctl command codes */
#define GPIOCHIP_GET_INFO    0x01  /* → gpiochip_info_t                           */
#define GPIOCHIP_SET_DIR     0x02  /* ← gpio_req_t {line, dir}                    */
#define GPIOCHIP_GET_VALUE   0x03  /* ← gpio_req_t {line} → fills .val            */
#define GPIOCHIP_SET_VALUE   0x04  /* ← gpio_req_t {line, val}                    */
#define GPIOCHIP_SET_PULL    0x05  /* ← gpio_req_t {line, pull}                   */
#define GPIOCHIP_SET_IRQ     0x06  /* ← gpio_irq_req_t {line, edge} — arm/disarm edge events */

/* GPIO_DIR_* — direction constants for GPIOCHIP_SET_DIR */
#define GPIO_DIR_INPUT       0
#define GPIO_DIR_OUTPUT      1

/* GPIO_PULL_* — pull-resistor constants for GPIOCHIP_SET_PULL */
#define GPIO_PULL_NONE       0
#define GPIO_PULL_UP         1
#define GPIO_PULL_DOWN       2
#define GPIO_PULL_UPDOWN     3  /* both pull-up and pull-down enabled simultaneously */

/* GPIO_EDGE_* — edge selector for GPIOCHIP_SET_IRQ, and the edge reported in a
 * gpio_event_t (only RISING/FALLING are ever reported). GPIO_EDGE_NONE disarms. */
#define GPIO_EDGE_RISING     0
#define GPIO_EDGE_FALLING    1
#define GPIO_EDGE_BOTH       2
#define GPIO_EDGE_NONE       3

/*
 * Returned by GPIOCHIP_GET_INFO.
 */
typedef struct {
    char    name[16];  /* chip name, e.g. "gpiochip0"  */
    uint8_t lines;     /* number of GPIO lines on chip  */
} gpiochip_info_t;

/*
 * Argument for GPIOCHIP_SET_DIR, GPIOCHIP_GET_VALUE, GPIOCHIP_SET_VALUE,
 * GPIOCHIP_SET_PULL. Unused fields are ignored.
 */
typedef struct {
    uint8_t line;   /* GPIO pin number                        */
    uint8_t dir;    /* GPIO_DIR_* — used by SET_DIR           */
    uint8_t val;    /* 0 or 1 — written by GET_VALUE, read by SET_VALUE */
    uint8_t pull;   /* GPIO_PULL_* — used by SET_PULL         */
} gpio_req_t;

/*
 * Argument for GPIOCHIP_SET_IRQ (reserved, returns ENOSYS in Phase 8).
 * IRQ delivery to app tasks will be wired up in Phase 12 (/dev/input/event0).
 */
/*
 * Argument for GPIOCHIP_SET_IRQ. Arms (edge = RISING/FALLING/BOTH) or disarms
 * (edge = NONE) edge-event delivery on `line`. Once armed, read() on the
 * gpiochip fd returns gpio_event_t records (blocking until an edge occurs),
 * mirroring the Linux GPIO chardev line-event model.
 */
typedef struct {
    uint8_t line;
    uint8_t edge;   /* GPIO_EDGE_*          */
    int     signo;  /* reserved             */
} gpio_irq_req_t;

/*
 * One edge event, returned by read() on /dev/gpiochip0 after a line was armed
 * with GPIOCHIP_SET_IRQ. read() blocks until at least one event is available;
 * pass a buffer of N * sizeof(gpio_event_t) to drain up to N at once.
 */
typedef struct {
    uint32_t timestamp_us;   /* monotonic microseconds at the edge       */
    uint8_t  line;           /* GPIO line that changed                   */
    uint8_t  edge;           /* GPIO_EDGE_RISING or GPIO_EDGE_FALLING    */
    uint8_t  level;          /* line level after the edge (0/1)          */
    uint8_t  _pad;
} gpio_event_t;
