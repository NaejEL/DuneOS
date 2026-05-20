#pragma once

#include <stdint.h>

/*
 * DuneOS input event API — /dev/input/event0
 *
 * read() on /dev/input/event0 blocks until at least one event is available,
 * then returns one or more input_event_t structs.  The buffer passed to read()
 * must be a multiple of sizeof(input_event_t).
 *
 * Inspired by Linux evdev (linux/input.h).
 */

/* ioctl command codes
 *   INPUT_INJECT_EVENT — used by userspace input source daemons
 *   (apps/system/kb_iomatrix.dap, apps/system/btn_gpio.dap, …) to push an
 *   input_event_t into the kernel's /dev/input/event0 ring buffer. The
 *   daemon does the hardware polling + keymap decoding; the kernel is just
 *   the event router. Requires DUNEOS_PERM_INPUT in the app manifest. */
#define INPUT_INJECT_EVENT  0x01    /* ← const input_event_t *           */

/* ioctl() is exported by the kernel ABI; declare it here for -nostdlib apps. */
extern int ioctl(int fd, unsigned long request, ...);

/* Event types */
#define INPUT_EV_KEY    1   /* key press or release         */
#define INPUT_EV_REL    2   /* relative axis change         */

/* Non-printable key codes */
#define KEY_BACKSPACE   0x08
#define KEY_TAB         0x09
#define KEY_ENTER       0x0d
#define KEY_ESC         0x1b
#define KEY_DELETE      0x7f
/* Modifier keys (above ASCII range) */
#define KEY_CTRL        0x80
#define KEY_SHIFT       0x81
#define KEY_ALT         0x82
#define KEY_OPT         0x83
/* Navigation keys */
#define KEY_UP          0x84
#define KEY_DOWN        0x85
#define KEY_LEFT        0x86
#define KEY_RIGHT       0x87
/* Special */
#define KEY_FN          0xff

/* Relative axis codes (used with INPUT_EV_REL) */
#define REL_WHEEL       8   /* rotary encoder / scroll wheel; value +1=CW -1=CCW */

/* Event values (INPUT_EV_KEY) */
#define INPUT_VAL_RELEASE   0
#define INPUT_VAL_PRESS     1
#define INPUT_VAL_REPEAT    2

typedef struct {
    uint32_t time_ms;   /* duneos_hal_monotonic_us() / 1000 at event time          */
    uint8_t  type;      /* INPUT_EV_KEY or INPUT_EV_REL                            */
    uint16_t code;      /* EV_KEY: ASCII / KEY_*;  EV_REL: REL_*                   */
    int32_t  value;     /* EV_KEY: INPUT_VAL_*;    EV_REL: signed delta            */
} input_event_t;
