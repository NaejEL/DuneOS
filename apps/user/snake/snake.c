/*
 * snake — the classic, on the libgame loop.
 *
 * Arrow keys steer; the snake steps on a fixed tick that speeds up as you eat.
 * Hit a wall or yourself and it's over. All the timing/input plumbing is in
 * libgame; this is just the rules + drawing.
 */

#include <stdio.h>
#include "duneos/game.h"
#include "duneos/input_ioctl.h"

extern void duneos_exit(int code);

#define CELL    8
#define MAX_LEN 512
#define BAR     10

#define C_BG    GFX_RGB(14, 18, 26)
#define C_BODY  GFX_RGB(70, 200, 110)
#define C_HEAD  GFX_RGB(150, 240, 160)
#define C_FOOD  GFX_RGB(240, 90, 90)
#define C_BAR   GFX_RGB(235, 235, 235)

static game_t gm;
static int    cols, rows, ox, oy;

static int  sx[MAX_LEN], sy[MAX_LEN], slen;
static int  dx, dy, pdx, pdy;     /* current + pending direction */
static int  fx, fy, score;

static uint32_t s_rng;
static int rnd(int n)
{
    s_rng ^= s_rng << 13; s_rng ^= s_rng >> 17; s_rng ^= s_rng << 5;
    return (int)(s_rng % (uint32_t)n);
}

static int on_snake(int x, int y)
{
    for (int i = 0; i < slen; i++) if (sx[i] == x && sy[i] == y) return 1;
    return 0;
}

static void place_food(void)
{
    do { fx = rnd(cols); fy = rnd(rows); } while (on_snake(fx, fy));
}

static void reset(void)
{
    slen = 3;
    for (int i = 0; i < slen; i++) { sx[i] = cols / 2 - i; sy[i] = rows / 2; }
    dx = 1; dy = 0; pdx = 1; pdy = 0;
    score = 0;
    place_food();
}

static void draw_score(void)
{
    char s[24];
    snprintf(s, sizeof(s), "snake  %d", score);
    gfx_text(gm.gfx, 2, 1, s, C_BAR, C_BG);
}

/* Full repaint — only on (re)start. Per-tick updates are incremental (step). */
static void render(void)
{
    game_clear(&gm, C_BG);
    draw_score();
    game_cell(&gm, ox, oy, fx, fy, CELL, C_FOOD);
    for (int i = 0; i < slen; i++)
        game_cell(&gm, ox, oy, sx[i], sy[i], CELL, i == 0 ? C_HEAD : C_BODY);
    gfx_flush(gm.gfx);
}

/* Advance one step and paint only what changed: erase the vacated tail, recolor
 * the old head to body, draw the new head. No full-screen clear ⇒ no flicker
 * (each cell is one small atomic write on the STREAM display). */
static int step(void)
{
    dx = pdx; dy = pdy;
    int hx = sx[0] + dx, hy = sy[0] + dy;
    if (hx < 0 || hx >= cols || hy < 0 || hy >= rows) return 0;   /* wall */
    for (int i = 0; i < slen; i++) if (sx[i] == hx && sy[i] == hy) return 0; /* self */

    int grow = (hx == fx && hy == fy);
    int tail_x = sx[slen - 1], tail_y = sy[slen - 1];

    int n = grow ? slen + 1 : slen;
    if (n > MAX_LEN) n = MAX_LEN;
    for (int i = n - 1; i > 0; i--) { sx[i] = sx[i - 1]; sy[i] = sy[i - 1]; }
    sx[0] = hx; sy[0] = hy;
    slen = n;

    if (!grow) game_cell(&gm, ox, oy, tail_x, tail_y, CELL, C_BG);  /* erase tail */
    if (slen > 1) game_cell(&gm, ox, oy, sx[1], sy[1], CELL, C_BODY); /* old head */
    game_cell(&gm, ox, oy, sx[0], sy[0], CELL, C_HEAD);              /* new head */

    if (grow) {
        score++;
        place_food();
        game_cell(&gm, ox, oy, fx, fy, CELL, C_FOOD);
        draw_score();
    }
    return 1;
}

static void set_dir(int ndx, int ndy)
{
    if (ndx == -dx && ndy == -dy) return;     /* no reversing */
    pdx = ndx; pdy = ndy;
}

void app_main(void)
{
    if (game_open(&gm) != 0) duneos_exit(10);
    s_rng = game_now_ms() | 1u;

    cols = gm.w / CELL;
    rows = (gm.h - BAR) / CELL;
    ox   = (gm.w - cols * CELL) / 2;
    oy   = BAR;

    reset();
    render();

    for (;;) {
        uint16_t key;
        uint32_t period = 160 - (score * 4 > 110 ? 110 : score * 4);  /* speeds up */
        switch (game_wait(&gm, period, &key)) {
        case GAME_KEY:
            switch (key) {
            case KEY_UP:    set_dir(0, -1); break;
            case KEY_DOWN:  set_dir(0,  1); break;
            case KEY_LEFT:  set_dir(-1, 0); break;
            case KEY_RIGHT: set_dir(1,  0); break;
            case KEY_ESC:   goto done;
            }
            break;
        case GAME_TICK:
            if (!step()) {
                if (game_over(&gm, "Game Over", score) == KEY_ESC) goto done;
                reset();
                render();
            }
            /* successful step already painted incrementally — no render() */
            break;
        case GAME_QUIT:
            goto done;
        }
    }

done:
    game_close(&gm);
    duneos_exit(0);
}
