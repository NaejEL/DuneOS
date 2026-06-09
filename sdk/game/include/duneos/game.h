#pragma once

/*
 * libgame — a tiny game-loop helper for DuneOS apps.
 *
 * The one thing arcade games need that the regular apps don't: a *cadenced,
 * non-blocking* loop — advance the world on a fixed tick (gravity, snake step)
 * while staying instantly responsive to the keyboard. game_wait() does both with
 * a single select() that sleeps until the next tick but wakes early on a key.
 *
 *   game_t gm;
 *   if (game_open(&gm) != 0) duneos_exit(10);
 *   for (;;) {
 *       uint16_t key;
 *       switch (game_wait(&gm, period_ms, &key)) {
 *       case GAME_KEY:  handle(key); break;     // a key was pressed
 *       case GAME_TICK: step(); render(&gm); break;
 *       case GAME_QUIT: goto done;              // input gone
 *       }
 *   }
 *
 * Rendering is plain libgfx (game_cell() helps for grid games); game_over()
 * draws a centred end screen and waits for a key.
 */

#include <stdint.h>
#include "duneos/gfx.h"
#include "duneos/ui.h"

typedef struct {
    gfx_ctx_t *gfx;
    ui_t      *ui;
    int        input;
    uint16_t   w, h;
    uint32_t   next_tick_ms;   /* internal */
} game_t;

typedef enum { GAME_KEY, GAME_TICK, GAME_QUIT } game_event_t;

/* Open the display (STREAM), a ui context, and /dev/input/event0. 0 / -1. */
int  game_open(game_t *gm);
void game_close(game_t *gm);

/* Milliseconds from the monotonic clock. */
uint32_t game_now_ms(void);

/* Block until the next tick (period_ms from the previous tick) or a key press.
 * Returns GAME_KEY with *key set, GAME_TICK, or GAME_QUIT. period_ms may change
 * between calls (variable speed). */
game_event_t game_wait(game_t *gm, uint32_t period_ms, uint16_t *key);

/* Fill the screen with one colour (libgfx passthrough). */
void game_clear(game_t *gm, uint16_t color);

/* Draw grid cell (gx,gy) of `px` pixels at origin (ox,oy), with a 1px gap so
 * cells read as a grid. */
void game_cell(game_t *gm, int ox, int oy, int gx, int gy, int px, uint16_t color);

/* Same as game_cell but onto any gfx context — pass an offscreen canvas
 * (gfx_canvas_new) to compose a whole region in RAM and present it flicker-free,
 * or gm->gfx to draw a single cell straight to the display (incremental update).
 * Drawing per-cell straight to a STREAM display is itself flicker-free: an 8x8
 * rect is one tiny atomic SPI write, so games that only touch a few cells per
 * tick (snake) need no canvas at all — just redraw the cells that changed. */
void game_cell_at(gfx_ctx_t *g, int ox, int oy, int gx, int gy, int px, uint16_t color);

/* Centred "Game Over" style overlay (title + score line); waits for a key and
 * returns its code. */
uint16_t game_over(game_t *gm, const char *title, int score);
