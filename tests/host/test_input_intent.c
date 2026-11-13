/*
 * sdk/input/libinput.c — the translation core. Pure: no fd, no allocation, no
 * FreeRTOS, no ESP-IDF, so it runs here exactly as it runs on target.
 *
 * Every map is constructed in the test body (LEG-38): nothing here reads a
 * generated board_config.h, so the suite behaves identically on a clean
 * checkout and on a machine where bspgen has run.
 */

#include <string.h>

#include "duneos/input.h"
#include "tassert.h"

/* ----- map builders ------------------------------------------------------ */

static input_topology_t keyboard_map(void)
{
    input_topology_t t;
    memset(&t, 0, sizeof(t));
    t.version   = INPUT_TOPOLOGY_VERSION;
    t.flags     = INPUT_TOPO_KEYBOARD;
    t.batch_max = INPUT_INTENT_BATCH_MAX;
    return t;
}

static input_topology_t rotary_map(uint16_t counts_per_step)
{
    input_topology_t t;
    memset(&t, 0, sizeof(t));
    t.version   = INPUT_TOPOLOGY_VERSION;
    t.batch_max = INPUT_INTENT_BATCH_MAX;
    t.n_axes    = 1;
    t.axes[0].code            = REL_WHEEL;
    t.axes[0].intent_neg      = INPUT_INTENT_PREV;
    t.axes[0].intent_pos      = INPUT_INTENT_NEXT;
    t.axes[0].counts_per_step = counts_per_step;
    return t;
}

static input_topology_t alt_axis_map(void)
{
    input_topology_t t = rotary_map(1);
    t.axes[0].intent_neg = INPUT_INTENT_ALT_PREV;
    t.axes[0].intent_pos = INPUT_INTENT_ALT_NEXT;
    return t;
}

/* A rotary board with a VALIDATE button — the T-Embed shape. The double-click
 * ALT latch needs both an axis to act on and a VALIDATE key to count. */
static input_topology_t rotary_with_validate(void)
{
    input_topology_t t = rotary_map(1);
    t.n_keys            = 1;
    t.keys[0].code      = KEY_ENTER;
    t.keys[0].intent    = INPUT_INTENT_VALIDATE;
    t.keys[0].intent_long = INPUT_INTENT_NONE;
    t.keys[0].long_ms   = 0;
    return t;
}

/* One button, with or without a long-press intent. */
static input_topology_t button_map(uint8_t shrt, uint8_t lng, uint16_t long_ms)
{
    input_topology_t t;
    memset(&t, 0, sizeof(t));
    t.version   = INPUT_TOPOLOGY_VERSION;
    t.batch_max = INPUT_INTENT_BATCH_MAX;
    t.n_keys    = 1;
    t.keys[0].code        = KEY_ENTER;
    t.keys[0].intent      = shrt;
    t.keys[0].intent_long = lng;
    t.keys[0].long_ms     = long_ms;
    return t;
}

/* ----- event builders ---------------------------------------------------- */

static input_event_t key_ev(uint16_t code, int32_t value, uint32_t t_ms)
{
    input_event_t e = { t_ms, INPUT_EV_KEY, code, value };
    return e;
}

static input_event_t rel_ev(int32_t delta)
{
    input_event_t e = { 0, INPUT_EV_REL, REL_WHEEL, delta };
    return e;
}

/* ----- harness ----------------------------------------------------------- */

typedef struct {
    input_topology_t     topo;
    duneos_input_state_t st;
    duneos_intent_t      out[INPUT_INTENT_BATCH_MAX];
    int                  n;
} fixture_t;

static void fx_init(fixture_t *f, input_topology_t topo)
{
    memset(f, 0, sizeof(*f));
    f->topo = topo;
}

static int feed(fixture_t *f, input_event_t ev, int max_out)
{
    memset(f->out, 0, sizeof(f->out));
    f->n = duneos_input_translate(&f->topo, &f->st, &ev, f->out, max_out);
    return f->n;
}

/* The single-intent case, which is most of them. */
static uint8_t one(fixture_t *f, input_event_t ev)
{
    int n = feed(f, ev, INPUT_INTENT_BATCH_MAX);
    CHECK_INT(n, 1);
    return n == 1 ? f->out[0].kind : INPUT_INTENT_NONE;
}

/* ----- criterion 1: keyboard, rotary, release, unmapped ------------------ */

static void test_keyboard_maps_the_vocabulary(void)
{
    fixture_t f;
    fx_init(&f, keyboard_map());

    CHECK_INT(one(&f, key_ev(KEY_UP,        INPUT_VAL_PRESS, 0)), INPUT_INTENT_PREV);
    CHECK_INT(one(&f, key_ev(KEY_DOWN,      INPUT_VAL_PRESS, 0)), INPUT_INTENT_NEXT);
    CHECK_INT(one(&f, key_ev(KEY_ENTER,     INPUT_VAL_PRESS, 0)), INPUT_INTENT_VALIDATE);
    CHECK_INT(one(&f, key_ev(KEY_ESC,       INPUT_VAL_PRESS, 0)), INPUT_INTENT_BACK);
    CHECK_INT(one(&f, key_ev(KEY_BACKSPACE, INPUT_VAL_PRESS, 0)), INPUT_INTENT_ERASE);
    CHECK_INT(one(&f, key_ev(KEY_LEFT,      INPUT_VAL_PRESS, 0)), INPUT_INTENT_ALT_PREV);
    CHECK_INT(one(&f, key_ev(KEY_RIGHT,     INPUT_VAL_PRESS, 0)), INPUT_INTENT_ALT_NEXT);
}

static void test_printable_keys_carry_their_character(void)
{
    fixture_t f;
    fx_init(&f, keyboard_map());

    CHECK_INT(one(&f, key_ev('k', INPUT_VAL_PRESS, 0)), INPUT_INTENT_TEXT);
    CHECK_INT(f.out[0].ch, 'k');

    CHECK_INT(one(&f, key_ev(' ', INPUT_VAL_PRESS, 0)), INPUT_INTENT_TEXT);
    CHECK_INT(f.out[0].ch, ' ');

    CHECK_INT(one(&f, key_ev('~', INPUT_VAL_PRESS, 0)), INPUT_INTENT_TEXT);
    CHECK_INT(f.out[0].ch, '~');
}

static void test_rotary_maps_to_prev_and_next(void)
{
    fixture_t f;
    fx_init(&f, rotary_map(1));

    CHECK_INT(one(&f, rel_ev(+1)), INPUT_INTENT_NEXT);
    CHECK_INT(one(&f, rel_ev(-1)), INPUT_INTENT_PREV);
}

static void test_release_yields_nothing(void)
{
    fixture_t f;
    fx_init(&f, keyboard_map());
    CHECK_INT(feed(&f, key_ev(KEY_UP, INPUT_VAL_RELEASE, 0), INPUT_INTENT_BATCH_MAX), 0);
    CHECK_INT(feed(&f, key_ev('k',    INPUT_VAL_RELEASE, 0), INPUT_INTENT_BATCH_MAX), 0);
}

static void test_repeat_yields_an_intent(void)
{
    fixture_t f;
    fx_init(&f, keyboard_map());
    CHECK_INT(one(&f, key_ev(KEY_DOWN, INPUT_VAL_REPEAT, 0)), INPUT_INTENT_NEXT);
}

static void test_unmapped_yields_nothing(void)
{
    fixture_t f;

    /* KEY_TAB and KEY_DELETE have no intent by design — they stay reachable
     * only through the unchanged raw path. */
    fx_init(&f, keyboard_map());
    CHECK_INT(feed(&f, key_ev(KEY_TAB,    INPUT_VAL_PRESS, 0), INPUT_INTENT_BATCH_MAX), 0);
    CHECK_INT(feed(&f, key_ev(KEY_DELETE, INPUT_VAL_PRESS, 0), INPUT_INTENT_BATCH_MAX), 0);
    CHECK_INT(feed(&f, key_ev(KEY_FN,     INPUT_VAL_PRESS, 0), INPUT_INTENT_BATCH_MAX), 0);

    /* A board with no keyboard does not get the keyboard mapping for free. */
    fx_init(&f, rotary_map(1));
    CHECK_INT(feed(&f, key_ev(KEY_UP, INPUT_VAL_PRESS, 0), INPUT_INTENT_BATCH_MAX), 0);
    CHECK_INT(feed(&f, key_ev('k',    INPUT_VAL_PRESS, 0), INPUT_INTENT_BATCH_MAX), 0);

    /* An axis the board does not declare. */
    fx_init(&f, keyboard_map());
    CHECK_INT(feed(&f, rel_ev(+1), INPUT_INTENT_BATCH_MAX), 0);
}

static void test_the_empty_map_translates_nothing(void)
{
    /* A board declaring no input source, and a kernel answering ENOTTY, both
     * land here. It must be quiet, not undefined. */
    fixture_t f;
    input_topology_t empty;
    memset(&empty, 0, sizeof(empty));
    fx_init(&f, empty);

    CHECK_INT(feed(&f, key_ev(KEY_UP, INPUT_VAL_PRESS, 0), INPUT_INTENT_BATCH_MAX), 0);
    CHECK_INT(feed(&f, rel_ev(+1),                         INPUT_INTENT_BATCH_MAX), 0);
    CHECK(!duneos_input_has_keyboard(&empty));
}

static void test_reserved_abs_events_are_ignored(void)
{
    fixture_t f;
    fx_init(&f, keyboard_map());
    input_event_t abs = { 0, INPUT_EV_ABS, ABS_X, 100 };
    CHECK_INT(feed(&f, abs, INPUT_INTENT_BATCH_MAX), 0);
}

/* ----- criterion 2: the relative-motion accumulator --------------------- */

static void test_counts_below_the_step_emit_nothing(void)
{
    const int32_t N = 4;
    fixture_t f;
    fx_init(&f, rotary_map((uint16_t)N));

    for (int32_t i = 0; i < N - 1; i++)
        CHECK_INT(feed(&f, rel_ev(+1), INPUT_INTENT_BATCH_MAX), 0);

    CHECK_INT(one(&f, rel_ev(+1)), INPUT_INTENT_NEXT);
}

static void test_a_whole_step_in_one_event_emits_once(void)
{
    fixture_t f;
    fx_init(&f, rotary_map(4));
    CHECK_INT(one(&f, rel_ev(+4)), INPUT_INTENT_NEXT);
    CHECK_INT(one(&f, rel_ev(-4)), INPUT_INTENT_PREV);
}

static void test_a_batch_above_the_clamp_is_clamped(void)
{
    fixture_t f;
    fx_init(&f, rotary_map(1));

    /* A fast spin: far more counts than the clamp allows intents. */
    int n = feed(&f, rel_ev(+1000), INPUT_INTENT_BATCH_MAX);
    CHECK_INT(n, INPUT_INTENT_BATCH_MAX);
    for (int i = 0; i < n; i++)
        CHECK_INT(f.out[i].kind, INPUT_INTENT_NEXT);

    /* The overflow is dropped, not banked: the next single count is one step,
     * not a replay of the spin. */
    CHECK_INT(one(&f, rel_ev(+1)), INPUT_INTENT_NEXT);
}

static void test_max_out_bounds_the_batch_too(void)
{
    fixture_t f;
    fx_init(&f, rotary_map(1));
    CHECK_INT(feed(&f, rel_ev(+5), 2), 2);
}

static void test_the_topology_clamp_bounds_the_batch(void)
{
    fixture_t f;
    input_topology_t t = rotary_map(1);
    t.batch_max = 3;
    fx_init(&f, t);
    CHECK_INT(feed(&f, rel_ev(+50), INPUT_INTENT_BATCH_MAX), 3);
}

static void test_a_reversal_does_not_leak_a_stale_partial_count(void)
{
    const int32_t N = 4;
    fixture_t f;
    fx_init(&f, rotary_map((uint16_t)N));

    /* Three counts one way — no step yet — then one the other way. The three
     * must not combine into an immediate step backwards. */
    for (int32_t i = 0; i < N - 1; i++) feed(&f, rel_ev(+1), INPUT_INTENT_BATCH_MAX);
    CHECK_INT(feed(&f, rel_ev(-1), INPUT_INTENT_BATCH_MAX), 0);

    /* And the new direction needs its own full N counts. */
    for (int32_t i = 0; i < N - 2; i++)
        CHECK_INT(feed(&f, rel_ev(-1), INPUT_INTENT_BATCH_MAX), 0);
    CHECK_INT(one(&f, rel_ev(-1)), INPUT_INTENT_PREV);
}

static void test_counts_per_step_zero_behaves_as_one(void)
{
    fixture_t f;
    fx_init(&f, rotary_map(0));
    CHECK_INT(one(&f, rel_ev(+1)), INPUT_INTENT_NEXT);
}

/* ----- criterion 3: the second axis ------------------------------------- */

static void test_a_declared_alt_axis_produces_alt_intents(void)
{
    fixture_t f;
    fx_init(&f, alt_axis_map());
    CHECK_INT(one(&f, rel_ev(+1)), INPUT_INTENT_ALT_NEXT);
    CHECK_INT(one(&f, rel_ev(-1)), INPUT_INTENT_ALT_PREV);
}

static void test_a_board_with_no_alt_source_never_produces_alt(void)
{
    fixture_t f;
    int32_t deltas[] = { +1, -1, +7, -7, +1000 };

    /* A primary-axis rotary and two buttons: the T-Embed's shape. No input it
     * can produce may surface as a second-axis intent, or an app would zoom
     * where the user asked to navigate. */
    input_topology_t t = rotary_map(1);
    t.n_keys = 2;
    t.keys[0].code = KEY_ENTER; t.keys[0].intent = INPUT_INTENT_VALIDATE;
    t.keys[1].code = KEY_ESC;   t.keys[1].intent = INPUT_INTENT_BACK;
    fx_init(&f, t);

    for (size_t i = 0; i < sizeof(deltas) / sizeof(deltas[0]); i++) {
        int n = feed(&f, rel_ev(deltas[i]), INPUT_INTENT_BATCH_MAX);
        for (int j = 0; j < n; j++) {
            CHECK(f.out[j].kind != INPUT_INTENT_ALT_PREV);
            CHECK(f.out[j].kind != INPUT_INTENT_ALT_NEXT);
        }
    }
    for (int v = INPUT_VAL_RELEASE; v <= INPUT_VAL_REPEAT; v++) {
        uint16_t codes[] = { KEY_ENTER, KEY_ESC, KEY_UP, KEY_LEFT, 'a' };
        for (size_t i = 0; i < sizeof(codes) / sizeof(codes[0]); i++) {
            int n = feed(&f, key_ev(codes[i], v, 0), INPUT_INTENT_BATCH_MAX);
            for (int j = 0; j < n; j++) {
                CHECK(f.out[j].kind != INPUT_INTENT_ALT_PREV);
                CHECK(f.out[j].kind != INPUT_INTENT_ALT_NEXT);
            }
        }
    }
}

/* ----- criterion 4: long press ------------------------------------------ */

#define LONG_MS 500

static void test_a_short_press_yields_the_short_intent(void)
{
    fixture_t f;
    fx_init(&f, button_map(INPUT_INTENT_VALIDATE, INPUT_INTENT_BACK, LONG_MS));

    /* Nothing on press: the duration is not known yet. */
    CHECK_INT(feed(&f, key_ev(KEY_ENTER, INPUT_VAL_PRESS, 1000), INPUT_INTENT_BATCH_MAX), 0);
    CHECK_INT(one(&f, key_ev(KEY_ENTER, INPUT_VAL_RELEASE, 1000 + LONG_MS - 1)),
              INPUT_INTENT_VALIDATE);
}

static void test_a_long_press_yields_the_long_intent(void)
{
    fixture_t f;
    fx_init(&f, button_map(INPUT_INTENT_VALIDATE, INPUT_INTENT_BACK, LONG_MS));

    feed(&f, key_ev(KEY_ENTER, INPUT_VAL_PRESS, 1000), INPUT_INTENT_BATCH_MAX);
    CHECK_INT(one(&f, key_ev(KEY_ENTER, INPUT_VAL_RELEASE, 1000 + LONG_MS)),
              INPUT_INTENT_BACK);
}

static void test_the_threshold_resolves_on_repeat_when_one_arrives(void)
{
    fixture_t f;
    fx_init(&f, button_map(INPUT_INTENT_VALIDATE, INPUT_INTENT_BACK, LONG_MS));

    feed(&f, key_ev(KEY_ENTER, INPUT_VAL_PRESS, 0), INPUT_INTENT_BATCH_MAX);
    CHECK_INT(feed(&f, key_ev(KEY_ENTER, INPUT_VAL_REPEAT, LONG_MS - 1),
                   INPUT_INTENT_BATCH_MAX), 0);
    CHECK_INT(one(&f, key_ev(KEY_ENTER, INPUT_VAL_REPEAT, LONG_MS)),
              INPUT_INTENT_BACK);
}

static void test_a_button_with_no_long_intent_fires_whatever_the_duration(void)
{
    fixture_t f;

    /* On press, immediately — a button that cannot mean two things must not
     * wait for a release to say the one thing it means. */
    fx_init(&f, button_map(INPUT_INTENT_VALIDATE, INPUT_INTENT_NONE, 0));
    CHECK_INT(one(&f, key_ev(KEY_ENTER, INPUT_VAL_PRESS, 0)), INPUT_INTENT_VALIDATE);
    CHECK_INT(feed(&f, key_ev(KEY_ENTER, INPUT_VAL_RELEASE, 100000),
                   INPUT_INTENT_BATCH_MAX), 0);

    /* Held for an hour: still exactly the short intent, still exactly once. */
    fx_init(&f, button_map(INPUT_INTENT_VALIDATE, INPUT_INTENT_NONE, 0));
    CHECK_INT(one(&f, key_ev(KEY_ENTER, INPUT_VAL_PRESS, 0)), INPUT_INTENT_VALIDATE);
    CHECK_INT(feed(&f, key_ev(KEY_ENTER, INPUT_VAL_RELEASE, 3600000),
                   INPUT_INTENT_BATCH_MAX), 0);
}

static const uint32_t k_durations[] = {
    0, 1, LONG_MS - 1, LONG_MS, LONG_MS + 1, 100000
};
#define N_DURATIONS (sizeof(k_durations) / sizeof(k_durations[0]))

/* A press and its release are one intent at every duration, for a button that
 * declares a long intent and for one that does not. A double-fire here would
 * launch an app twice from one click. */
static void test_a_press_and_its_release_are_one_intent(void)
{
    for (size_t d = 0; d < N_DURATIONS; d++) {
        for (int lng = 0; lng < 2; lng++) {
            fixture_t f;
            fx_init(&f, button_map(INPUT_INTENT_VALIDATE,
                                   lng ? INPUT_INTENT_BACK : INPUT_INTENT_NONE,
                                   lng ? LONG_MS : 0));
            int total = feed(&f, key_ev(KEY_ENTER, INPUT_VAL_PRESS, 0),
                             INPUT_INTENT_BATCH_MAX);
            total += feed(&f, key_ev(KEY_ENTER, INPUT_VAL_RELEASE,
                                     k_durations[d]), INPUT_INTENT_BATCH_MAX);
            CHECK_INT(total, 1);
        }
    }
}

/* A REPEAT in the middle must not turn one press of a long-press button into
 * two intents: the threshold fires once, and the release that follows is
 * silent. (A button with no long intent is different on purpose — see
 * test_repeat_on_a_plain_button_is_an_auto_repeat.) */
static void test_a_repeat_does_not_double_fire_a_long_press_button(void)
{
    for (size_t d = 0; d < N_DURATIONS; d++) {
        fixture_t f;
        fx_init(&f, button_map(INPUT_INTENT_VALIDATE, INPUT_INTENT_BACK, LONG_MS));

        int total = feed(&f, key_ev(KEY_ENTER, INPUT_VAL_PRESS, 0),
                         INPUT_INTENT_BATCH_MAX);
        total += feed(&f, key_ev(KEY_ENTER, INPUT_VAL_REPEAT, k_durations[d]),
                      INPUT_INTENT_BATCH_MAX);
        total += feed(&f, key_ev(KEY_ENTER, INPUT_VAL_RELEASE, k_durations[d]),
                      INPUT_INTENT_BATCH_MAX);
        CHECK_INT(total, 1);
    }
}

/* On a button that cannot mean two things, REPEAT is auto-repeat and is meant
 * to yield an intent per event — that is how a held arrow scrolls. The press
 * fires, each repeat fires, the release is silent. */
static void test_repeat_on_a_plain_button_is_an_auto_repeat(void)
{
    fixture_t f;
    fx_init(&f, button_map(INPUT_INTENT_NEXT, INPUT_INTENT_NONE, 0));

    CHECK_INT(one(&f, key_ev(KEY_ENTER, INPUT_VAL_PRESS, 0)), INPUT_INTENT_NEXT);
    for (int i = 0; i < 3; i++)
        CHECK_INT(one(&f, key_ev(KEY_ENTER, INPUT_VAL_REPEAT, (uint32_t)(100 * i))),
                  INPUT_INTENT_NEXT);
    CHECK_INT(feed(&f, key_ev(KEY_ENTER, INPUT_VAL_RELEASE, 500),
                   INPUT_INTENT_BATCH_MAX), 0);
}

static void test_a_release_without_its_press_yields_nothing(void)
{
    /* The app may have opened the device mid-press. */
    fixture_t f;
    fx_init(&f, button_map(INPUT_INTENT_VALIDATE, INPUT_INTENT_BACK, LONG_MS));
    CHECK_INT(feed(&f, key_ev(KEY_ENTER, INPUT_VAL_RELEASE, 9999),
                   INPUT_INTENT_BATCH_MAX), 0);
}

static void test_a_declared_button_overrides_the_keyboard_mapping(void)
{
    /* A board with both a keyboard and a declared button: the declaration wins
     * for that code, and the keyboard still maps everything else. */
    fixture_t f;
    input_topology_t t = button_map(INPUT_INTENT_BACK, INPUT_INTENT_NONE, 0);
    t.flags |= INPUT_TOPO_KEYBOARD;
    fx_init(&f, t);

    CHECK_INT(one(&f, key_ev(KEY_ENTER, INPUT_VAL_PRESS, 0)), INPUT_INTENT_BACK);
    CHECK_INT(one(&f, key_ev(KEY_UP,    INPUT_VAL_PRESS, 0)), INPUT_INTENT_PREV);
    CHECK(duneos_input_has_keyboard(&t));
}

static void test_null_arguments_are_refused_quietly(void)
{
    fixture_t f;
    fx_init(&f, keyboard_map());
    input_event_t ev = key_ev(KEY_UP, INPUT_VAL_PRESS, 0);

    CHECK_INT(duneos_input_translate(0, &f.st, &ev, f.out, 4), 0);
    CHECK_INT(duneos_input_translate(&f.topo, 0, &ev, f.out, 4), 0);
    CHECK_INT(duneos_input_translate(&f.topo, &f.st, 0, f.out, 4), 0);
    CHECK_INT(duneos_input_translate(&f.topo, &f.st, &ev, 0, 4), 0);
    CHECK_INT(duneos_input_translate(&f.topo, &f.st, &ev, f.out, 0), 0);
    CHECK(!duneos_input_has_keyboard(0));
}

/* ----- rotary ALT latch (double-click of VALIDATE) ----------------------- */

static void test_a_double_click_latches_alt_on_the_wheel(void)
{
    fixture_t f;
    fx_init(&f, rotary_with_validate());

    /* At rest the wheel is the primary pair. */
    CHECK_INT(one(&f, rel_ev(+1)), INPUT_INTENT_NEXT);

    /* First click emits VALIDATE; second within the window is swallowed. */
    CHECK_INT(one(&f, key_ev(KEY_ENTER, INPUT_VAL_PRESS, 100)), INPUT_INTENT_VALIDATE);
    CHECK_INT(feed(&f, key_ev(KEY_ENTER, INPUT_VAL_PRESS, 300),
                   INPUT_INTENT_BATCH_MAX), 0);

    /* Now the wheel drives the ALT pair. */
    CHECK_INT(one(&f, rel_ev(+1)), INPUT_INTENT_ALT_NEXT);
    CHECK_INT(one(&f, rel_ev(-1)), INPUT_INTENT_ALT_PREV);
}

static void test_a_second_double_click_releases_the_latch(void)
{
    fixture_t f;
    fx_init(&f, rotary_with_validate());

    CHECK_INT(one(&f, key_ev(KEY_ENTER, INPUT_VAL_PRESS, 100)), INPUT_INTENT_VALIDATE);
    CHECK_INT(feed(&f, key_ev(KEY_ENTER, INPUT_VAL_PRESS, 300),
                   INPUT_INTENT_BATCH_MAX), 0);
    CHECK_INT(one(&f, rel_ev(+1)), INPUT_INTENT_ALT_NEXT);   /* latched */

    /* A second double-click, its gap measured from a fresh first press — the
     * toggle reset validate_last_ms, so t=300's press was NOT a usable anchor. */
    CHECK_INT(one(&f, key_ev(KEY_ENTER, INPUT_VAL_PRESS, 1000)), INPUT_INTENT_VALIDATE);
    CHECK_INT(feed(&f, key_ev(KEY_ENTER, INPUT_VAL_PRESS, 1200),
                   INPUT_INTENT_BATCH_MAX), 0);
    CHECK_INT(one(&f, rel_ev(+1)), INPUT_INTENT_NEXT);       /* back to primary */
}

static void test_a_single_click_does_not_latch(void)
{
    fixture_t f;
    fx_init(&f, rotary_with_validate());

    CHECK_INT(one(&f, key_ev(KEY_ENTER, INPUT_VAL_PRESS, 100)), INPUT_INTENT_VALIDATE);
    CHECK_INT(one(&f, rel_ev(+1)), INPUT_INTENT_NEXT);       /* still primary */
}

static void test_two_clicks_too_far_apart_do_not_latch(void)
{
    fixture_t f;
    fx_init(&f, rotary_with_validate());

    /* 500 ms apart, over the 400 ms window: two real VALIDATEs, no toggle. */
    CHECK_INT(one(&f, key_ev(KEY_ENTER, INPUT_VAL_PRESS, 100)), INPUT_INTENT_VALIDATE);
    CHECK_INT(one(&f, key_ev(KEY_ENTER, INPUT_VAL_PRESS, 600)), INPUT_INTENT_VALIDATE);
    CHECK_INT(one(&f, rel_ev(+1)), INPUT_INTENT_NEXT);
}

static void test_a_keyboard_board_never_latches_on_double_click(void)
{
    fixture_t f;
    fx_init(&f, keyboard_map());   /* n_axes == 0 */

    /* Both presses are plain VALIDATEs; the second is not swallowed, because a
     * keyboard board reaches ALT through its arrows and has no axis to latch. */
    CHECK_INT(one(&f, key_ev(KEY_ENTER, INPUT_VAL_PRESS, 100)), INPUT_INTENT_VALIDATE);
    CHECK_INT(one(&f, key_ev(KEY_ENTER, INPUT_VAL_PRESS, 200)), INPUT_INTENT_VALIDATE);
}

int main(void)
{
    test_keyboard_maps_the_vocabulary();
    test_printable_keys_carry_their_character();
    test_rotary_maps_to_prev_and_next();
    test_release_yields_nothing();
    test_repeat_yields_an_intent();
    test_unmapped_yields_nothing();
    test_the_empty_map_translates_nothing();
    test_reserved_abs_events_are_ignored();

    test_counts_below_the_step_emit_nothing();
    test_a_whole_step_in_one_event_emits_once();
    test_a_batch_above_the_clamp_is_clamped();
    test_max_out_bounds_the_batch_too();
    test_the_topology_clamp_bounds_the_batch();
    test_a_reversal_does_not_leak_a_stale_partial_count();
    test_counts_per_step_zero_behaves_as_one();

    test_a_declared_alt_axis_produces_alt_intents();
    test_a_board_with_no_alt_source_never_produces_alt();

    test_a_short_press_yields_the_short_intent();
    test_a_long_press_yields_the_long_intent();
    test_the_threshold_resolves_on_repeat_when_one_arrives();
    test_a_button_with_no_long_intent_fires_whatever_the_duration();
    test_a_press_and_its_release_are_one_intent();
    test_a_repeat_does_not_double_fire_a_long_press_button();
    test_repeat_on_a_plain_button_is_an_auto_repeat();
    test_a_release_without_its_press_yields_nothing();
    test_a_declared_button_overrides_the_keyboard_mapping();
    test_null_arguments_are_refused_quietly();

    test_a_double_click_latches_alt_on_the_wheel();
    test_a_second_double_click_releases_the_latch();
    test_a_single_click_does_not_latch();
    test_two_clicks_too_far_apart_do_not_latch();
    test_a_keyboard_board_never_latches_on_double_click();

    return t_report("test_input_intent");
}
