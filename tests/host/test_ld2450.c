/*
 * sdk/sensor/libld2450.c — the pure decoding half (ld2450_decode_frame,
 * ld2450_parser_feed). No I/O, so it runs here exactly as it runs on target.
 */

#include <errno.h>
#include <string.h>

#include "duneos/ld2450.h"
#include "tassert.h"

static void put16(uint8_t *p, uint16_t v)
{
    p[0] = (uint8_t)(v & 0xFF);
    p[1] = (uint8_t)(v >> 8);
}

static void frame_init(uint8_t f[LD2450_FRAME_BYTES])
{
    memset(f, 0, LD2450_FRAME_BYTES);
    f[0] = 0xAA; f[1] = 0xFF; f[2] = 0x03; f[3] = 0x00;
    f[28] = 0x55; f[29] = 0xCC;
}

static void set_target(uint8_t f[LD2450_FRAME_BYTES], int slot,
                       uint16_t x, uint16_t y, uint16_t speed, uint16_t res)
{
    uint8_t *b = f + 4 + slot * 8;
    put16(b, x);
    put16(b + 2, y);
    put16(b + 4, speed);
    put16(b + 6, res);
}

/* Protocol V1.02 §"Radar data output" spells both of these out. */
static void test_worked_examples(void)
{
    uint8_t f[LD2450_FRAME_BYTES];
    frame_init(f);
    set_target(f, 0, 0x86B1, 0x030E, 0x8005, 0x0028);

    ld2450_frame_t out;
    memset(&out, 0xAA, sizeof(out));
    CHECK_INT(ld2450_decode_frame(f, &out), 0);
    CHECK(out.target[0].present);
    CHECK_INT(out.target[0].x_mm, 1713);
    CHECK_INT(out.target[0].y_mm, -782);
    CHECK_INT(out.target[0].speed_cms, 5);
    CHECK_INT(out.target[0].res_mm, 40);
}

static void test_absent_slots_are_zeroed(void)
{
    uint8_t f[LD2450_FRAME_BYTES];
    frame_init(f);
    set_target(f, 1, 0x8001, 0x8002, 0x8003, 0x0004);

    ld2450_frame_t out;
    memset(&out, 0x5A, sizeof(out));
    CHECK_INT(ld2450_decode_frame(f, &out), 0);

    for (int i = 0; i < LD2450_MAX_TARGETS; i++) {
        if (i == 1) {
            CHECK(out.target[i].present);
            continue;
        }
        CHECK(!out.target[i].present);
        CHECK_INT(out.target[i].x_mm, 0);
        CHECK_INT(out.target[i].y_mm, 0);
        CHECK_INT(out.target[i].speed_cms, 0);
        CHECK_INT(out.target[i].res_mm, 0);
    }
    CHECK_INT(out.target[1].x_mm, 1);
    CHECK_INT(out.target[1].y_mm, 2);
    CHECK_INT(out.target[1].speed_cms, 3);
    CHECK_INT(out.target[1].res_mm, 4);
}

/* An all-zero block is "no target", but a target reported at the origin with
 * a non-zero resolution is a real one — the emptiness test is the whole
 * block, not the coordinates. */
static void test_zero_coordinates_with_resolution_is_present(void)
{
    uint8_t f[LD2450_FRAME_BYTES];
    frame_init(f);
    set_target(f, 2, 0, 0, 0, 0x0001);

    ld2450_frame_t out;
    memset(&out, 0, sizeof(out));
    CHECK_INT(ld2450_decode_frame(f, &out), 0);
    CHECK(out.target[2].present);
    CHECK_INT(out.target[2].res_mm, 1);
}

static void test_bad_header_and_tail_rejected(void)
{
    ld2450_frame_t out;
    uint8_t f[LD2450_FRAME_BYTES];

    for (int i = 0; i < 4; i++) {
        frame_init(f);
        f[i] ^= 0xFF;
        CHECK_INT(ld2450_decode_frame(f, &out), -EINVAL);
    }

    frame_init(f);
    f[28] = 0x54;
    CHECK_INT(ld2450_decode_frame(f, &out), -EINVAL);

    frame_init(f);
    f[29] = 0xCD;
    CHECK_INT(ld2450_decode_frame(f, &out), -EINVAL);
}

static int feed_all(ld2450_parser_t *p, const uint8_t *bytes, size_t n,
                    ld2450_frame_t *out)
{
    int frames = 0;
    for (size_t i = 0; i < n; i++)
        frames += ld2450_parser_feed(p, bytes[i], out);
    return frames;
}

static void test_resync_over_garbage(void)
{
    uint8_t f[LD2450_FRAME_BYTES];
    frame_init(f);
    set_target(f, 0, 0x86B1, 0x030E, 0x8005, 0x0028);

    const uint8_t garbage[] = { 0x00, 0x12, 0xFF, 0x03, 0x00, 0x7E };
    uint8_t stream[sizeof(garbage) + LD2450_FRAME_BYTES];
    memcpy(stream, garbage, sizeof(garbage));
    memcpy(stream + sizeof(garbage), f, LD2450_FRAME_BYTES);

    ld2450_parser_t p;
    ld2450_frame_t out;
    ld2450_parser_init(&p);
    memset(&out, 0, sizeof(out));
    CHECK_INT(feed_all(&p, stream, sizeof(stream), &out), 1);
    CHECK_INT(out.target[0].x_mm, 1713);
    CHECK_INT(out.target[0].y_mm, -782);
}

/* The resync branch that a naive "reset to 0" gets wrong: the byte that broke
 * the header match is itself the first byte of the next one. */
static void test_header_byte_inside_garbage(void)
{
    uint8_t f[LD2450_FRAME_BYTES];
    frame_init(f);
    set_target(f, 0, 0x8064, 0x8064, 0x8000, 0x0000);

    uint8_t stream[3 + LD2450_FRAME_BYTES];
    stream[0] = 0xAA;
    stream[1] = 0xFF;
    stream[2] = 0xAA;   /* breaks the match at index 2 and restarts it */
    memcpy(stream + 3, f + 1, LD2450_FRAME_BYTES - 1);

    ld2450_parser_t p;
    ld2450_frame_t out;
    ld2450_parser_init(&p);
    memset(&out, 0, sizeof(out));
    CHECK_INT(feed_all(&p, stream, sizeof(stream), &out), 1);
    CHECK_INT(out.target[0].x_mm, 100);
}

static void test_two_back_to_back_frames(void)
{
    uint8_t f[LD2450_FRAME_BYTES];
    frame_init(f);
    set_target(f, 0, 0x86B1, 0x030E, 0x8005, 0x0028);

    ld2450_parser_t p;
    ld2450_frame_t out;
    ld2450_parser_init(&p);
    memset(&out, 0, sizeof(out));
    CHECK_INT(feed_all(&p, f, LD2450_FRAME_BYTES, &out), 1);
    CHECK_INT(feed_all(&p, f, LD2450_FRAME_BYTES, &out), 1);
}

/* A frame whose payload is intact but whose tail is wrong must not be handed
 * out, and must not wedge the parser for the frame behind it. */
static void test_bad_tail_does_not_wedge_the_parser(void)
{
    uint8_t bad[LD2450_FRAME_BYTES], good[LD2450_FRAME_BYTES];
    frame_init(bad);
    bad[29] = 0x00;
    frame_init(good);
    set_target(good, 0, 0x86B1, 0x030E, 0x8005, 0x0028);

    ld2450_parser_t p;
    ld2450_frame_t out;
    ld2450_parser_init(&p);
    memset(&out, 0, sizeof(out));
    CHECK_INT(feed_all(&p, bad, LD2450_FRAME_BYTES, &out), 0);
    CHECK_INT(feed_all(&p, good, LD2450_FRAME_BYTES, &out), 1);
    CHECK_INT(out.target[0].x_mm, 1713);
}

int main(void)
{
    test_worked_examples();
    test_absent_slots_are_zeroed();
    test_zero_coordinates_with_resolution_is_present();
    test_bad_header_and_tail_rejected();
    test_resync_over_garbage();
    test_header_byte_inside_garbage();
    test_two_back_to_back_frames();
    test_bad_tail_does_not_wedge_the_parser();
    return t_report("test_ld2450");
}
