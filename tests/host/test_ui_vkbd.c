/*
 * sdk/ui/ui_vkbd.c — the on-screen keyboard state machine. Pure: the rendering
 * half lives in ui_vkbd_draw.c, so this runs here exactly as it runs on target.
 *
 * What it must guarantee: a board with four intents can type, and the caller's
 * buffer is never written at or past cap-1.
 */

#include <string.h>

#include "duneos/ui_vkbd.h"
#include "tassert.h"

/* Intents as compound literals, so a test reads as the gesture it performs. */
#define IT(kind, ch)  (&(duneos_intent_t){ (kind), (ch), 0 })

#define PREV      IT(INPUT_INTENT_PREV, 0)
#define NEXT      IT(INPUT_INTENT_NEXT, 0)
#define ALT_PREV  IT(INPUT_INTENT_ALT_PREV, 0)
#define ALT_NEXT  IT(INPUT_INTENT_ALT_NEXT, 0)
#define VALIDATE  IT(INPUT_INTENT_VALIDATE, 0)
#define BACK      IT(INPUT_INTENT_BACK, 0)
#define ERASE     IT(INPUT_INTENT_ERASE, 0)
#define TEXT(ch)  IT(INPUT_INTENT_TEXT, (ch))

/* Walk the focus to the cell holding `glyph` and commit it. Returns false if no
 * cell carries it, so a test cannot silently pass on a layout change. */
static bool type_glyph(ui_vkbd_t *k, char glyph)
{
    int n = ui_vkbd_cell_count();
    for (int i = 0; i < n; i++) {
        if (ui_vkbd_cell_glyph(k, k->sel) == glyph)
            return ui_vkbd_intent(k, VALIDATE) == UI_VKBD_CHANGED;
        ui_vkbd_intent(k, NEXT);
    }
    return false;
}

/* Move the focus onto the cell whose label is `label` (an action cell). */
static bool focus_label(ui_vkbd_t *k, const char *label)
{
    int n = ui_vkbd_cell_count();
    for (int i = 0; i < n; i++) {
        char buf[4];
        ui_vkbd_cell_label(k, k->sel, buf, (int)sizeof(buf));
        if (strcmp(buf, label) == 0) return true;
        ui_vkbd_intent(k, NEXT);
    }
    return false;
}

/* ----- traversal -------------------------------------------------------- */

static void test_next_and_prev_wrap(void)
{
    char buf[8];
    ui_vkbd_t k;
    ui_vkbd_init(&k, 0, 0, 160, 80, buf, (int)sizeof(buf));
    int n = ui_vkbd_cell_count();
    CHECK(n > 0);

    CHECK_INT(k.sel, 0);
    CHECK_INT(ui_vkbd_intent(&k, PREV), UI_VKBD_MOVED);
    CHECK_INT(k.sel, n - 1);                 /* wrapped backwards off cell 0 */
    CHECK_INT(ui_vkbd_intent(&k, NEXT), UI_VKBD_MOVED);
    CHECK_INT(k.sel, 0);                     /* and forwards off the end     */

    /* A full lap returns to the start and never leaves the grid. */
    for (int i = 0; i < n; i++) {
        ui_vkbd_intent(&k, NEXT);
        CHECK(k.sel >= 0 && k.sel < n);
    }
    CHECK_INT(k.sel, 0);
}

static void test_the_second_axis_moves_by_a_row_and_wraps(void)
{
    char buf[8];
    ui_vkbd_t k;
    ui_vkbd_init(&k, 0, 0, 160, 80, buf, (int)sizeof(buf));
    int n = ui_vkbd_cell_count();

    CHECK_INT(ui_vkbd_intent(&k, ALT_NEXT), UI_VKBD_MOVED);
    CHECK_INT(k.sel, UI_VKBD_COLS);
    CHECK_INT(ui_vkbd_intent(&k, ALT_PREV), UI_VKBD_MOVED);
    CHECK_INT(k.sel, 0);

    ui_vkbd_intent(&k, ALT_PREV);
    CHECK(k.sel >= 0 && k.sel < n);          /* wrapped, still in the grid */

    CHECK(ui_vkbd_rows() > 0);
    CHECK(ui_vkbd_rows() * UI_VKBD_COLS >= n);
}

/* ----- insertion -------------------------------------------------------- */

static void test_validate_on_a_glyph_inserts_it(void)
{
    char buf[16];
    ui_vkbd_t k;
    ui_vkbd_init(&k, 0, 0, 160, 80, buf, (int)sizeof(buf));
    CHECK_STR(buf, "");

    CHECK(type_glyph(&k, 'a'));
    CHECK_STR(buf, "a");
    CHECK_INT(k.len, 1);

    CHECK(type_glyph(&k, 'b'));
    CHECK_STR(buf, "ab");
    CHECK_INT(k.len, 2);
}

static void test_a_password_is_typable_with_four_intents_only(void)
{
    /* The whole point of the vkbd: PREV/NEXT/VALIDATE/BACK and nothing else.
     * This is what makes INPUT_INTENT_TEXT reachable on a rotary board. */
    const char *want = "hunter2";
    char buf[16];
    ui_vkbd_t k;
    ui_vkbd_init(&k, 0, 0, 160, 80, buf, (int)sizeof(buf));

    for (const char *c = want; *c; c++)
        CHECK(type_glyph(&k, *c));
    CHECK_STR(buf, want);
}

static void test_the_shift_layer_reaches_upper_case(void)
{
    char buf[16];
    ui_vkbd_t k;
    ui_vkbd_init(&k, 0, 0, 160, 80, buf, (int)sizeof(buf));

    CHECK(focus_label(&k, "Ab"));
    CHECK_INT(ui_vkbd_intent(&k, VALIDATE), UI_VKBD_MOVED);
    CHECK(k.shift);
    CHECK(type_glyph(&k, 'Q'));
    CHECK_STR(buf, "Q");

    CHECK(focus_label(&k, "Ab"));
    ui_vkbd_intent(&k, VALIDATE);
    CHECK(!k.shift);
    CHECK(type_glyph(&k, 'q'));
    CHECK_STR(buf, "Qq");
}

static void test_a_text_intent_inserts_directly(void)
{
    char buf[8];
    ui_vkbd_t k;
    ui_vkbd_init(&k, 0, 0, 160, 80, buf, (int)sizeof(buf));

    CHECK_INT(ui_vkbd_intent(&k, TEXT('z')), UI_VKBD_CHANGED);
    CHECK_STR(buf, "z");
}

/* ----- erase, submit, cancel -------------------------------------------- */

static void test_erase_removes_the_last_character(void)
{
    char buf[16];
    ui_vkbd_t k;
    ui_vkbd_init(&k, 0, 0, 160, 80, buf, (int)sizeof(buf));

    CHECK(type_glyph(&k, 'a'));
    CHECK(type_glyph(&k, 'b'));
    CHECK_INT(ui_vkbd_intent(&k, ERASE), UI_VKBD_CHANGED);
    CHECK_STR(buf, "a");
    CHECK_INT(k.len, 1);

    CHECK_INT(ui_vkbd_intent(&k, ERASE), UI_VKBD_CHANGED);
    CHECK_STR(buf, "");

    /* Erasing an empty buffer is not a change, and must not underflow. */
    CHECK_INT(ui_vkbd_intent(&k, ERASE), UI_VKBD_NONE);
    CHECK_INT(k.len, 0);
    CHECK_STR(buf, "");
}

static void test_the_erase_cell_erases_too(void)
{
    char buf[16];
    ui_vkbd_t k;
    ui_vkbd_init(&k, 0, 0, 160, 80, buf, (int)sizeof(buf));

    CHECK(type_glyph(&k, 'a'));
    CHECK(focus_label(&k, "<x"));
    CHECK_INT(ui_vkbd_intent(&k, VALIDATE), UI_VKBD_CHANGED);
    CHECK_STR(buf, "");
}

static void test_the_ok_cell_submits(void)
{
    char buf[16];
    ui_vkbd_t k;
    ui_vkbd_init(&k, 0, 0, 160, 80, buf, (int)sizeof(buf));

    CHECK(type_glyph(&k, 'a'));
    CHECK(focus_label(&k, "OK"));
    CHECK_INT(ui_vkbd_intent(&k, VALIDATE), UI_VKBD_SUBMIT);
    CHECK_STR(buf, "a");   /* submit does not clear */
}

static void test_back_cancels(void)
{
    char buf[16];
    ui_vkbd_t k;
    ui_vkbd_init(&k, 0, 0, 160, 80, buf, (int)sizeof(buf));
    CHECK_INT(ui_vkbd_intent(&k, BACK), UI_VKBD_CANCEL);
}

/* ----- the bound that matters ------------------------------------------- */

static void test_the_buffer_is_never_written_past_cap_minus_one(void)
{
    /* A guard byte behind the buffer: an off-by-one is visible rather than
     * merely corrupting whatever the caller put there. */
    struct { char buf[5]; char guard[4]; } s;
    memset(&s, 0x7f, sizeof(s));

    ui_vkbd_t k;
    ui_vkbd_init(&k, 0, 0, 160, 80, s.buf, (int)sizeof(s.buf));

    /* cap 5 holds 4 characters plus the NUL. Ask for far more. */
    for (int i = 0; i < 50; i++)
        ui_vkbd_intent(&k, TEXT('x'));

    CHECK_INT(k.len, 4);
    CHECK_STR(s.buf, "xxxx");
    CHECK_INT(s.buf[4], '\0');
    for (size_t i = 0; i < sizeof(s.guard); i++)
        CHECK_INT(s.guard[i], 0x7f);

    /* A full buffer reports no change rather than silently dropping. */
    CHECK_INT(ui_vkbd_intent(&k, TEXT('y')), UI_VKBD_NONE);

    /* And the same through the grid. */
    CHECK(!type_glyph(&k, 'a'));
    CHECK_STR(s.buf, "xxxx");
    for (size_t i = 0; i < sizeof(s.guard); i++)
        CHECK_INT(s.guard[i], 0x7f);
}

static void test_a_one_byte_buffer_holds_only_the_nul(void)
{
    char buf[1];
    ui_vkbd_t k;
    ui_vkbd_init(&k, 0, 0, 160, 80, buf, 1);
    CHECK_INT(buf[0], '\0');
    CHECK_INT(ui_vkbd_intent(&k, TEXT('x')), UI_VKBD_NONE);
    CHECK_INT(buf[0], '\0');
    CHECK_INT(k.len, 0);
}

static void test_every_cell_has_a_label_that_fits(void)
{
    char buf[8];
    ui_vkbd_t k;
    ui_vkbd_init(&k, 0, 0, 160, 80, buf, (int)sizeof(buf));

    for (int layer = 0; layer < 2; layer++) {
        k.shift = (layer != 0);
        for (int i = 0; i < ui_vkbd_cell_count(); i++) {
            char label[4];
            memset(label, 0x5a, sizeof(label));
            ui_vkbd_cell_label(&k, i, label, (int)sizeof(label));
            size_t len = strnlen(label, sizeof(label));
            CHECK(len >= 1 && len <= 2);
            CHECK_INT(label[len], '\0');
        }
    }
}

static void test_null_arguments_are_refused_quietly(void)
{
    char buf[8];
    ui_vkbd_t k;
    ui_vkbd_init(&k, 0, 0, 160, 80, buf, (int)sizeof(buf));

    CHECK_INT(ui_vkbd_intent(0, NEXT), UI_VKBD_NONE);
    CHECK_INT(ui_vkbd_intent(&k, 0), UI_VKBD_NONE);
    CHECK_INT(ui_vkbd_cell_glyph(0, 0), 0);
    ui_vkbd_init(0, 0, 0, 0, 0, 0, 0);   /* must not fault */

    /* A buffer too small for a label leaves it empty rather than overrunning. */
    char tiny[2] = { 0x5a, 0x5a };
    ui_vkbd_cell_label(&k, 0, tiny, 1);
    CHECK_INT(tiny[0], '\0');
    CHECK_INT(tiny[1], 0x5a);
}

int main(void)
{
    test_next_and_prev_wrap();
    test_the_second_axis_moves_by_a_row_and_wraps();

    test_validate_on_a_glyph_inserts_it();
    test_a_password_is_typable_with_four_intents_only();
    test_the_shift_layer_reaches_upper_case();
    test_a_text_intent_inserts_directly();

    test_erase_removes_the_last_character();
    test_the_erase_cell_erases_too();
    test_the_ok_cell_submits();
    test_back_cancels();

    test_the_buffer_is_never_written_past_cap_minus_one();
    test_a_one_byte_buffer_holds_only_the_nul();
    test_every_cell_has_a_label_that_fits();
    test_null_arguments_are_refused_quietly();

    return t_report("test_ui_vkbd");
}
