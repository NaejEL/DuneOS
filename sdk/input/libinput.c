/*
 * libinput — the translation core. Pure by contract: see duneos/input.h.
 *
 * Nothing here opens a file, allocates, or names a FreeRTOS or ESP-IDF symbol,
 * so tests/host compiles it with the host CC. The fd side is libinput_fd.c.
 */

#include "duneos/input.h"

/* The keyboard's own mapping. A board with INPUT_TOPO_KEYBOARD does not
 * enumerate 80 keys in its topology — the vocabulary is fixed, so the map is
 * here, in the one place that consumes it.
 *
 * The primary axis (PREV/NEXT) is the pair a single rotary drives; the second
 * (ALT_*) is what a widget with two axes zooms with. Widgets having only one
 * axis accept both pairs, so every arrow navigates them.
 */
static uint8_t keyboard_intent(uint16_t code)
{
    switch (code) {
    case KEY_UP:        return INPUT_INTENT_PREV;
    case KEY_DOWN:      return INPUT_INTENT_NEXT;
    case KEY_LEFT:      return INPUT_INTENT_ALT_PREV;
    case KEY_RIGHT:     return INPUT_INTENT_ALT_NEXT;
    case KEY_ENTER:     return INPUT_INTENT_VALIDATE;
    case KEY_ESC:       return INPUT_INTENT_BACK;
    case KEY_BACKSPACE: return INPUT_INTENT_ERASE;
    default:
        if (code >= 0x20 && code < 0x7f) return INPUT_INTENT_TEXT;
        return INPUT_INTENT_NONE;
    }
}

/* Two VALIDATE presses closer than this toggle the rotary ALT latch. Generous:
 * a knob press is deliberate, and the cost of a miss is one stray VALIDATE,
 * which waves ignores. Short enough that two unrelated taps do not couple. */
#define INPUT_DOUBLECLICK_MS 400

/* The ALT counterpart of a primary intent, for the rotary latch. Anything that
 * is not PREV/NEXT passes through — the latch only has meaning for the axis. */
static uint8_t alt_of(uint8_t intent)
{
    switch (intent) {
    case INPUT_INTENT_PREV: return INPUT_INTENT_ALT_PREV;
    case INPUT_INTENT_NEXT: return INPUT_INTENT_ALT_NEXT;
    default:                return intent;
    }
}

static const input_key_map_t *find_key(const input_topology_t *topo, uint16_t code)
{
    int n = topo->n_keys;
    if (n > INPUT_MAP_MAX_KEYS) n = INPUT_MAP_MAX_KEYS;
    for (int i = 0; i < n; i++)
        if (topo->keys[i].code == code) return &topo->keys[i];
    return 0;
}

static int axis_index(const input_topology_t *topo, uint16_t code)
{
    int n = topo->n_axes;
    if (n > INPUT_MAP_MAX_AXES) n = INPUT_MAP_MAX_AXES;
    for (int i = 0; i < n; i++)
        if (topo->axes[i].code == code) return i;
    return -1;
}

static int emit(duneos_intent_t *out, int at, uint8_t kind, char ch, int32_t value)
{
    out[at].kind  = kind;
    out[at].ch    = ch;
    out[at].value = value;
    return at + 1;
}

static int clamp_out(const input_topology_t *topo, int max_out)
{
    int cap = topo->batch_max ? topo->batch_max : INPUT_INTENT_BATCH_MAX;
    if (cap > INPUT_INTENT_BATCH_MAX) cap = INPUT_INTENT_BATCH_MAX;
    return (max_out < cap) ? max_out : cap;
}

bool duneos_input_has_keyboard(const input_topology_t *topo)
{
    return topo && (topo->flags & INPUT_TOPO_KEYBOARD) != 0;
}

/* A button declaring intent_long resolves on release (or on the first REPEAT
 * past the threshold), so exactly one intent leaves one press. A button with no
 * intent_long never waits: it fires on press, whatever the duration. */
static int translate_held_key(const input_key_map_t *km,
                              duneos_input_state_t *st,
                              const input_event_t *ev,
                              duneos_intent_t *out, int max_out)
{
    bool mine = st->held_active && st->held_code == ev->code;

    if (ev->value == INPUT_VAL_PRESS) {
        st->held_active    = true;
        st->held_code      = ev->code;
        st->held_since_ms  = ev->time_ms;
        st->held_long_sent = false;
        return 0;
    }

    if (!mine) return 0;

    uint32_t held_ms = ev->time_ms - st->held_since_ms;

    if (ev->value == INPUT_VAL_REPEAT) {
        if (!st->held_long_sent && held_ms >= km->long_ms) {
            st->held_long_sent = true;
            return (max_out > 0)
                 ? emit(out, 0, km->intent_long, 0, 0) : 0;
        }
        return 0;
    }

    /* RELEASE */
    st->held_active = false;
    if (st->held_long_sent) return 0;
    uint8_t kind = (held_ms >= km->long_ms) ? km->intent_long : km->intent;
    return (max_out > 0) ? emit(out, 0, kind, 0, 0) : 0;
}

static int translate_key(const input_topology_t *topo,
                         duneos_input_state_t *st,
                         const input_event_t *ev,
                         duneos_intent_t *out, int max_out)
{
    const input_key_map_t *km = find_key(topo, ev->code);

    if (km && km->intent_long != INPUT_INTENT_NONE && km->long_ms > 0)
        return translate_held_key(km, st, ev, out, max_out);

    if (ev->value == INPUT_VAL_RELEASE) return 0;

    uint8_t kind = km ? km->intent
                      : (duneos_input_has_keyboard(topo)
                             ? keyboard_intent(ev->code)
                             : INPUT_INTENT_NONE);
    if (kind == INPUT_INTENT_NONE || max_out <= 0) return 0;

    /* Double-click of VALIDATE toggles the rotary ALT latch — but only on a
     * board that has an axis for it to act on. On a keyboard board n_axes is 0,
     * so VALIDATE there is only ever VALIDATE, and the CardPuter is untouched.
     * The first click still emits VALIDATE (harmless in waves); the second is
     * swallowed so the gesture does not also validate twice. Only a PRESS can
     * toggle: a held VALIDATE auto-repeats the intent, it does not flip the
     * latch. */
    if (kind == INPUT_INTENT_VALIDATE && topo->n_axes > 0
            && ev->value == INPUT_VAL_PRESS) {
        uint32_t gap = ev->time_ms - st->validate_last_ms;
        if (st->validate_last_ms != 0 && gap <= INPUT_DOUBLECLICK_MS) {
            st->alt_latched = !st->alt_latched;
            st->validate_last_ms = 0;   /* a triple-click is one toggle, not two */
            return 0;
        }
        st->validate_last_ms = ev->time_ms ? ev->time_ms : 1;  /* 0 means "none" */
    }

    char ch = (kind == INPUT_INTENT_TEXT) ? (char)ev->code : 0;
    return emit(out, 0, kind, ch, 0);
}

static int translate_rel(const input_topology_t *topo,
                         duneos_input_state_t *st,
                         const input_event_t *ev,
                         duneos_intent_t *out, int max_out)
{
    int ai = axis_index(topo, ev->code);
    if (ai < 0 || max_out <= 0) return 0;

    const input_axis_map_t *am = &topo->axes[ai];
    int32_t step = am->counts_per_step ? (int32_t)am->counts_per_step : 1;

    /* While the ALT latch is armed, the axis emits the ALT pair — the wheel
     * drives waves' second axis (pan) instead of its primary (zoom). */
    uint8_t pos = st->alt_latched ? alt_of(am->intent_pos) : am->intent_pos;
    uint8_t neg = st->alt_latched ? alt_of(am->intent_neg) : am->intent_neg;

    /* A reversal discards the partial count rather than carrying it across: a
     * leftover +N would turn the first count back the other way into an
     * immediate step, so one notch back would move two items. */
    if ((st->accum[ai] > 0 && ev->value < 0) || (st->accum[ai] < 0 && ev->value > 0))
        st->accum[ai] = 0;

    st->accum[ai] += ev->value;

    int n = 0;
    while (st->accum[ai] >= step && n < max_out) {
        st->accum[ai] -= step;
        n = emit(out, n, pos, 0, ev->value);
    }
    while (st->accum[ai] <= -step && n < max_out) {
        st->accum[ai] += step;
        n = emit(out, n, neg, 0, ev->value);
    }
    /* Clamped: drop what did not fit instead of letting it arrive later as a
     * phantom step the user did not ask for. */
    if (n == max_out) st->accum[ai] = 0;
    return n;
}

int duneos_input_translate(const input_topology_t *topo,
                           duneos_input_state_t *st,
                           const input_event_t *ev,
                           duneos_intent_t *out, int max_out)
{
    if (!topo || !st || !ev || !out) return 0;

    max_out = clamp_out(topo, max_out);
    if (max_out <= 0) return 0;

    switch (ev->type) {
    case INPUT_EV_KEY: return translate_key(topo, st, ev, out, max_out);
    case INPUT_EV_REL: return translate_rel(topo, st, ev, out, max_out);
    default:           return 0;   /* INPUT_EV_ABS is reserved, not implemented */
    }
}
