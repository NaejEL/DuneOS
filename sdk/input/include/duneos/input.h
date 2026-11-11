#pragma once

/*
 * DuneOS SDK — libinput: events to intents.
 *
 * One .dap navigates a CardPuter (keyboard matrix), a T-Embed (rotary + two
 * buttons) and later a touchscreen board, with no per-board source edit. The
 * board says what its hardware can express through ioctl(INPUT_GET_TOPOLOGY);
 * this library turns the events it emits into the nine intents the sdk/ui
 * widgets dispatch on. Same shape as display portability: the geometry is
 * queried at runtime, never compiled in.
 *
 * ---------------------------------------------------------------------------
 * LIMITATION — this layer is opt-in, and most of the tree has not opted in.
 *
 * SIX apps still open /dev/input/event0 and switch on KEY_* codes directly:
 * bin/input, drview, files, g_shell, i2cscope, radar. (Nine did before this
 * cycle; launcher, waves and wifi were migrated. btn_gpio and kb_iomatrix also
 * open the device but only to inject — they are producers, not readers.)
 *
 * Those six are unchanged and they are KEYBOARD-ONLY: on a board whose only
 * input is a rotary they receive INPUT_EV_REL events they do not read, and
 * appear frozen. That is a known half-migration, not a bug to rediscover. An
 * app becomes portable by reading intents through this header; nothing else
 * makes it portable, and no kernel change will (drv_input stays evdev-faithful,
 * ADR 019, so the raw rotary delta ui_wave needs survives).
 *
 * 2-D continuous control — snake, tetris, sdk/game — is NOT expressible as
 * intents and is not meant to be. Those keep raw codes by design.
 * ---------------------------------------------------------------------------
 *
 * Two units, separable on purpose:
 *
 *   libinput.c     the translation core. Pure: topology + state + event in,
 *                  intents out. No fd, no allocation, no FreeRTOS, no
 *                  ESP-IDF — so it is host-compiled and host-tested.
 *   libinput_fd.c  a thin wrapper that opens the device, asks for the
 *                  topology and runs read() through the core.
 *
 * An app that only needs translation (a test, a replay tool) links the first.
 *
 * Usage:
 *   duneos_input_t in;
 *   if (duneos_input_open(&in) != 0) duneos_exit(11);
 *   duneos_intent_t it[INPUT_INTENT_BATCH_MAX];
 *   int n = duneos_input_read(&in, it, INPUT_INTENT_BATCH_MAX);
 *   for (int i = 0; i < n; i++)
 *       switch (ui_carousel_intent(&car, &it[i])) { ... }
 */

#include <stdint.h>
#include <stdbool.h>

#include "duneos/input_ioctl.h"

/*
 * Translation state. CALLER-OWNED and never file-scope BSS in a captured app:
 * static BSS lives in the app's heap pool, outside the shell slot's bounds, and
 * api_read's pointer validation rejects it (CLAUDE.md). Put it on the stack or
 * in a struct the app already owns, and zero it before the first call.
 */
typedef struct {
    int32_t  accum[INPUT_MAP_MAX_AXES];  /* signed counts not yet a step */
    uint16_t held_code;
    uint32_t held_since_ms;
    bool     held_active;
    bool     held_long_sent;             /* long intent already emitted */
} duneos_input_state_t;

/*
 * Translate one event into 0..max_out intents.
 *
 * Returns the number written. A relative axis can yield several at once (a fast
 * spin), bounded by both max_out and topo->batch_max so a flick cannot flood a
 * redraw loop. `ev` of an unmapped code, or a key release that resolves to
 * nothing, yields 0 — never a spurious INPUT_INTENT_NONE.
 *
 * Takes ev->time_ms as its only clock: long-press resolves from it, with no
 * timer and no task. Velocity-based acceleration is deliberately absent in v1
 * (an 8-item carousel does not want skipping) and can be added later from the
 * same input with no API change.
 */
int duneos_input_translate(const input_topology_t *topo,
                           duneos_input_state_t *st,
                           const input_event_t *ev,
                           duneos_intent_t *out, int max_out);

/* True when the board declares a full keyboard — the text-entry entry point
 * uses it to choose the line editor over the on-screen keyboard. */
bool duneos_input_has_keyboard(const input_topology_t *topo);

/* ----- fd wrapper (libinput_fd.c) ---------------------------------------- */

typedef struct {
    int                  fd;
    input_topology_t     topo;
    duneos_input_state_t st;
} duneos_input_t;

/*
 * Open /dev/input/event0 and query its topology. Returns 0, or -1 with errno
 * set. A kernel without the input driver answers ENOTTY to the query; the
 * topology is then the empty map and translation yields nothing — the SDK must
 * build and behave with both the define and the device absent, so a board that
 * declares no input source is not a special case here.
 */
int duneos_input_open(duneos_input_t *in);
void duneos_input_close(duneos_input_t *in);

/* Blocking read of one event, translated. Returns intents written (may be 0),
 * or -1 on a read error. */
int duneos_input_read(duneos_input_t *in, duneos_intent_t *out, int max_out);

/* The raw fd, for select() alongside other sources. */
int duneos_input_fd(const duneos_input_t *in);
