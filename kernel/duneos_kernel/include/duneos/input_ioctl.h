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

/*   INPUT_GET_TOPOLOGY — fills an input_topology_t with what this board's
 *   input hardware can express, as intents rather than key codes. The map is
 *   generated into the kernel's board_config.h, not into the app's
 *   <duneos/board.h>, so a .dap stays board-independent: same binary, same
 *   question at runtime, different answer per board. Same reason display
 *   geometry is queried through ioctl(FB_GET_INFO) rather than compiled in.
 *   Returns 0, or -1/ENOTTY on a kernel without the input driver. */
#define INPUT_GET_TOPOLOGY  0x02    /* → input_topology_t *              */

/* ioctl() is exported by the kernel ABI; declare it here for -nostdlib apps. */
extern int ioctl(int fd, unsigned long request, ...);

/* Event types */
#define INPUT_EV_KEY    1   /* key press or release         */
#define INPUT_EV_REL    2   /* relative axis change         */
/* Reserved for a future touchscreen (ESP32-P4). NOT IMPLEMENTED: no producer
 * emits it, no consumer reads it, and the translation core ignores it. The type
 * and axis codes are reserved here only so adding a pointer later does not
 * renumber the two types above. */
#define INPUT_EV_ABS    3   /* absolute axis — reserved, not implemented  */

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

/* Absolute axis codes (used with INPUT_EV_ABS) — reserved, not implemented. */
#define ABS_X           0
#define ABS_Y           1
#define ABS_PRESSURE    24

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


/* ----- intents: what the board can express, not which key it sends -------
 *
 * A key code is a hardware fact; an intent is what the user meant. The nine
 * below are derived from what the sdk/ui widgets dispatch on and nothing else:
 * a rotary notch and a KEY_DOWN are the same NEXT, so one .dap navigates a
 * keyboard board and a rotary board without a source edit.
 *
 * 2-D continuous control (snake, tetris, sdk/game) is NOT expressible here and
 * keeps reading raw key codes.
 *
 * Translation from events to intents lives in sdk/input (libinput.c), driven by
 * the topology below. The kernel never synthesises one: drv_input stays
 * evdev-faithful (ADR 019) so the rotary delta ui_wave needs survives.
 */
enum {
    INPUT_INTENT_NONE = 0,
    INPUT_INTENT_PREV,
    INPUT_INTENT_NEXT,
    INPUT_INTENT_ALT_PREV,   /* second axis — ui_wave zoom */
    INPUT_INTENT_ALT_NEXT,
    INPUT_INTENT_VALIDATE,
    INPUT_INTENT_BACK,
    INPUT_INTENT_TEXT,       /* carries a character */
    INPUT_INTENT_ERASE,
};

#define INPUT_MAP_MAX_KEYS      12
#define INPUT_MAP_MAX_AXES      2

/* Upper bound on intents one read() batch may yield, so a fast spin cannot
 * flood a redraw loop. Mirrored by BATCH_CLAMP in tools/dbt/input_map.py. */
#define INPUT_INTENT_BATCH_MAX  8

#define INPUT_TOPOLOGY_VERSION  1

/* Board has a full keyboard: printable keys reach INPUT_INTENT_TEXT directly
 * and the on-screen keyboard is unnecessary. */
#define INPUT_TOPO_KEYBOARD     (1u << 0)

typedef struct {
    uint16_t code;         /* KEY_* this button injects                      */
    uint8_t  intent;       /* on press, or on a release before long_ms       */
    uint8_t  intent_long;  /* INPUT_INTENT_NONE when none is declared        */
    uint16_t long_ms;      /* long-press threshold; 0 when intent_long is NONE */
} input_key_map_t;

typedef struct {
    uint16_t code;             /* REL_*                                      */
    uint8_t  intent_neg;       /* intent for a negative delta                */
    uint8_t  intent_pos;
    /* Axis steps per intent, in whatever unit the axis already emits — for a
     * rotary that is DETENTS, because the HAL divides raw quadrature counts
     * down to one REL_WHEEL per notch. 1 = one intent per notch; higher is
     * slower, not faster. */
    uint16_t counts_per_step;
} input_axis_map_t;

/* Flat POD, copied out by ioctl. An all-zero topology (n_keys == n_axes == 0,
 * no INPUT_TOPO_KEYBOARD) is the explicit "this board declares no input
 * source" answer, not a failure. */
typedef struct {
    uint16_t version;      /* INPUT_TOPOLOGY_VERSION                         */
    uint16_t flags;        /* INPUT_TOPO_*                                   */
    uint8_t  n_keys;
    uint8_t  n_axes;
    uint8_t  batch_max;    /* INPUT_INTENT_BATCH_MAX                         */
    uint8_t  reserved;
    input_key_map_t  keys[INPUT_MAP_MAX_KEYS];
    input_axis_map_t axes[INPUT_MAP_MAX_AXES];
} input_topology_t;

/* One translated intent. `ch` is meaningful only for INPUT_INTENT_TEXT; `value`
 * carries the raw signed delta of the axis that produced a relative intent, so
 * an app wanting acceleration can still see it.
 *
 * It lives in this header rather than in <duneos/input.h> so the sdk/ui widgets
 * can take one without putting sdk/input on their include path — a widget
 * consumes intents, it does not translate them. sdk/input produces them. */
typedef struct {
    uint8_t kind;    /* INPUT_INTENT_* */
    char    ch;
    int32_t value;
} duneos_intent_t;
