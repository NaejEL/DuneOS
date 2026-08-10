#include "duneos/i2c_decode.h"
#include "tassert.h"

#define MAX_SAMPLES 1024
#define MAX_TOKENS  64

typedef struct {
    i2c_sample_t s[MAX_SAMPLES];
    int          n;
    uint32_t     t;
    int          scl_bit, sda_bit;
} stream_t;

static void st_init(stream_t *st, int scl_bit, int sda_bit)
{
    st->n = 0;
    st->t = 0;
    st->scl_bit = scl_bit;
    st->sda_bit = sda_bit;
}

static void emit(stream_t *st, int scl, int sda)
{
    st->t += 10;
    st->s[st->n].t_us   = st->t;
    st->s[st->n].levels = ((uint32_t)scl << st->scl_bit) |
                          ((uint32_t)sda << st->sda_bit);
    st->n++;
}

/* Decoder assumes idle-high; SDA falling while SCL high = START. */
static void emit_start(stream_t *st)
{
    emit(st, 1, 1);
    emit(st, 1, 0);
}

static void emit_restart(stream_t *st)
{
    emit(st, 0, 1);
    emit(st, 1, 1);
    emit(st, 1, 0);
}

static void emit_byte(stream_t *st, uint8_t byte, int ack)
{
    for (int b = 7; b >= 0; b--) {
        int bit = (byte >> b) & 1;
        emit(st, 0, bit);
        emit(st, 1, bit);
    }
    emit(st, 0, !ack);
    emit(st, 1, !ack);      /* 9th clock: SDA low = ACK */
}

static void emit_stop(stream_t *st)
{
    emit(st, 0, 0);
    emit(st, 1, 0);
    emit(st, 1, 1);
}

static int decode(const stream_t *st, i2c_token_t *out, int max)
{
    return i2c_decode(st->s, st->n, st->scl_bit, st->sda_bit, out, max);
}

static void check_tok(const i2c_token_t *t, uint8_t type, uint8_t val,
                      uint8_t rw, uint8_t ack)
{
    CHECK_INT(t->type, type);
    CHECK_INT(t->val,  val);
    CHECK_INT(t->rw,   rw);
    CHECK_INT(t->ack,  ack);
}

static void test_write_txn(void)
{
    stream_t st;
    st_init(&st, 1, 0);
    emit_start(&st);
    emit_byte(&st, 0x50 << 1, 1);          /* addr 0x50, W, ACK */
    emit_byte(&st, 0xDE, 1);
    emit_byte(&st, 0xAD, 0);               /* NACK on last byte */
    emit_stop(&st);

    i2c_token_t tok[MAX_TOKENS];
    int n = decode(&st, tok, MAX_TOKENS);
    CHECK_INT(n, 5);
    CHECK_INT(tok[0].type, I2C_TOK_START);
    check_tok(&tok[1], I2C_TOK_ADDR, 0x50, 0, 1);
    check_tok(&tok[2], I2C_TOK_DATA, 0xDE, 0, 1);
    check_tok(&tok[3], I2C_TOK_DATA, 0xAD, 0, 0);
    CHECK_INT(tok[4].type, I2C_TOK_STOP);
    CHECK(tok[0].t_us > 0);
    CHECK(tok[4].t_us > tok[0].t_us);
}

static void test_read_txn(void)
{
    stream_t st;
    st_init(&st, 1, 0);
    emit_start(&st);
    emit_byte(&st, (0x68 << 1) | 1, 1);    /* addr 0x68, R, ACK */
    emit_byte(&st, 0x42, 0);
    emit_stop(&st);

    i2c_token_t tok[MAX_TOKENS];
    int n = decode(&st, tok, MAX_TOKENS);
    CHECK_INT(n, 4);
    check_tok(&tok[1], I2C_TOK_ADDR, 0x68, 1, 1);
    check_tok(&tok[2], I2C_TOK_DATA, 0x42, 0, 0);
    CHECK_INT(tok[3].type, I2C_TOK_STOP);
}

static void test_repeated_start(void)
{
    stream_t st;
    st_init(&st, 1, 0);
    emit_start(&st);
    emit_byte(&st, 0x11 << 1, 1);          /* write register pointer... */
    emit_byte(&st, 0x01, 1);
    emit_restart(&st);
    emit_byte(&st, (0x11 << 1) | 1, 1);    /* ...then read back */
    emit_byte(&st, 0xFF, 0);
    emit_stop(&st);

    i2c_token_t tok[MAX_TOKENS];
    int n = decode(&st, tok, MAX_TOKENS);
    CHECK_INT(n, 7);
    CHECK_INT(tok[0].type, I2C_TOK_START);
    check_tok(&tok[1], I2C_TOK_ADDR, 0x11, 0, 1);
    check_tok(&tok[2], I2C_TOK_DATA, 0x01, 0, 1);
    CHECK_INT(tok[3].type, I2C_TOK_START);
    check_tok(&tok[4], I2C_TOK_ADDR, 0x11, 1, 1);
    check_tok(&tok[5], I2C_TOK_DATA, 0xFF, 0, 0);
    CHECK_INT(tok[6].type, I2C_TOK_STOP);
}

static void test_capacity_limit(void)
{
    stream_t st;
    st_init(&st, 1, 0);
    emit_start(&st);
    emit_byte(&st, 0x50 << 1, 1);
    emit_byte(&st, 0xAA, 1);
    emit_stop(&st);

    i2c_token_t tok[3];
    int n = decode(&st, tok, 3);
    CHECK_INT(n, 3);
    CHECK_INT(tok[0].type, I2C_TOK_START);
    check_tok(&tok[1], I2C_TOK_ADDR, 0x50, 0, 1);
    check_tok(&tok[2], I2C_TOK_DATA, 0xAA, 0, 1);
}

static void test_other_bit_positions(void)
{
    stream_t st;
    st_init(&st, 5, 2);
    emit_start(&st);
    emit_byte(&st, (0x3C << 1), 1);
    emit_stop(&st);

    i2c_token_t tok[MAX_TOKENS];
    int n = decode(&st, tok, MAX_TOKENS);
    CHECK_INT(n, 3);
    check_tok(&tok[1], I2C_TOK_ADDR, 0x3C, 0, 1);
}

static void test_idle_and_noise(void)
{
    i2c_token_t tok[MAX_TOKENS];
    CHECK_INT(i2c_decode(NULL, 0, 1, 0, tok, MAX_TOKENS), 0);

    /* Clock edges before any START must not produce tokens. */
    stream_t st;
    st_init(&st, 1, 0);
    emit(&st, 1, 1);
    emit(&st, 0, 1);
    emit(&st, 1, 1);
    emit(&st, 0, 1);
    emit(&st, 1, 1);
    CHECK_INT(decode(&st, tok, MAX_TOKENS), 0);

    /* STOP with no prior START is still reported (decoder is stateless there). */
    st_init(&st, 1, 0);
    emit(&st, 1, 0);   /* looks like START from idle-high assumption */
    emit(&st, 1, 1);   /* immediately released: STOP */
    int n = decode(&st, tok, MAX_TOKENS);
    CHECK_INT(n, 2);
    CHECK_INT(tok[0].type, I2C_TOK_START);
    CHECK_INT(tok[1].type, I2C_TOK_STOP);
}

int main(void)
{
    test_write_txn();
    test_read_txn();
    test_repeated_start();
    test_capacity_limit();
    test_other_bit_positions();
    test_idle_and_noise();
    return t_report("test_i2c_decode");
}
