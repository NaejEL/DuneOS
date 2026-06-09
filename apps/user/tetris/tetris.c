/*
 * tetris — falling blocks, on the libgame loop.
 *
 * Left/Right move, Up rotate, Down soft-drop. Gravity ticks on the libgame
 * cadence and speeds up with each level. Standard 10x20 well.
 */

#include <stdio.h>
#include <string.h>
#include "duneos/game.h"
#include "duneos/input_ioctl.h"

extern void duneos_exit(int code);

#define COLS 10
#define ROWS 20
#define BAR  10

#define C_BG    GFX_RGB(14, 16, 24)
#define C_FRAME GFX_RGB(60, 70, 90)

static game_t   gm;
static gfx_ctx_t *well;              /* offscreen canvas for the play field */
static int      cell, ox, oy;
static uint16_t board[ROWS][COLS];   /* 0 = empty, else colour */

/* 7 tetrominoes × 4 rotations as 4x4 bit masks (bit set = 0x8000 >> (y*4+x)). */
static const uint16_t SHAPE[7][4] = {
    { 0x0F00, 0x2222, 0x00F0, 0x4444 }, /* I */
    { 0x44C0, 0x8E00, 0x6440, 0x0E20 }, /* J */
    { 0x4460, 0x0E80, 0xC440, 0x2E00 }, /* L */
    { 0xCC00, 0xCC00, 0xCC00, 0xCC00 }, /* O */
    { 0x06C0, 0x8C40, 0x6C00, 0x4620 }, /* S */
    { 0x0E40, 0x4C40, 0x4E00, 0x4640 }, /* T */
    { 0x0C60, 0x4C80, 0xC600, 0x2640 }, /* Z */
};
static const uint16_t COLOR[7] = {
    GFX_RGB(80, 210, 230), GFX_RGB(80, 120, 230), GFX_RGB(235, 150, 60),
    GFX_RGB(235, 215, 70), GFX_RGB(90, 210, 110), GFX_RGB(190, 110, 220),
    GFX_RGB(235, 90, 90),
};

static int  s_type, s_rot, s_px, s_py, s_score, s_lines;
static uint32_t s_rng;

static int rnd(int n)
{
    s_rng ^= s_rng << 13; s_rng ^= s_rng >> 17; s_rng ^= s_rng << 5;
    return (int)(s_rng % (uint32_t)n);
}

static int cell_set(int type, int rot, int x, int y)
{
    return (SHAPE[type][rot] & (0x8000 >> (y * 4 + x))) != 0;
}

static int collide(int type, int rot, int px, int py)
{
    for (int y = 0; y < 4; y++)
        for (int x = 0; x < 4; x++) {
            if (!cell_set(type, rot, x, y)) continue;
            int bx = px + x, by = py + y;
            if (bx < 0 || bx >= COLS || by >= ROWS) return 1;
            if (by >= 0 && board[by][bx]) return 1;
        }
    return 0;
}

static void spawn(void)
{
    s_type = rnd(7);
    s_rot  = 0;
    s_px   = COLS / 2 - 2;
    s_py   = 0;
}

static void lock_piece(void)
{
    for (int y = 0; y < 4; y++)
        for (int x = 0; x < 4; x++)
            if (cell_set(s_type, s_rot, x, y) && s_py + y >= 0)
                board[s_py + y][s_px + x] = COLOR[s_type];

    /* clear full lines, top-down */
    for (int r = ROWS - 1; r >= 0; r--) {
        int full = 1;
        for (int c = 0; c < COLS; c++) if (!board[r][c]) { full = 0; break; }
        if (full) {
            for (int rr = r; rr > 0; rr--) memcpy(board[rr], board[rr - 1], sizeof(board[0]));
            memset(board[0], 0, sizeof(board[0]));
            s_lines++; s_score += 10;
            r++;   /* recheck this row */
        }
    }
}

static void reset(void)
{
    memset(board, 0, sizeof(board));
    s_score = s_lines = 0;
    spawn();
}

/* Static chrome: title + well border. Drawn once; the per-frame canvas present
 * lands strictly inside the border so this never needs repainting. */
static void draw_chrome(void)
{
    game_clear(&gm, C_BG);
    gfx_rect(gm.gfx, ox - 1, oy - 1, COLS * cell + 2, 1, C_FRAME);
    gfx_rect(gm.gfx, ox - 1, oy + ROWS * cell, COLS * cell + 2, 1, C_FRAME);
    gfx_rect(gm.gfx, ox - 1, oy, 1, ROWS * cell, C_FRAME);
    gfx_rect(gm.gfx, ox + COLS * cell, oy, 1, ROWS * cell, C_FRAME);
}

/* Compose the whole play field in the offscreen canvas, then push it to the
 * panel in a single blit — the well never shows a half-drawn frame, so no
 * flicker. Score text overwrites itself in place (small, no clear needed). */
static void render(void)
{
    gfx_fill(well, C_BG);

    for (int r = 0; r < ROWS; r++)
        for (int c = 0; c < COLS; c++)
            if (board[r][c]) game_cell_at(well, 0, 0, c, r, cell, board[r][c]);

    for (int y = 0; y < 4; y++)
        for (int x = 0; x < 4; x++)
            if (cell_set(s_type, s_rot, x, y) && s_py + y >= 0)
                game_cell_at(well, 0, 0, s_px + x, s_py + y, cell, COLOR[s_type]);

    gfx_canvas_present(gm.gfx, well, ox, oy);

    char s[24];
    snprintf(s, sizeof(s), "tetris  %d", s_score);
    gfx_text(gm.gfx, 2, 1, s, GFX_WHITE, C_BG);
}

/* Drop one row; lock + respawn when it can't. Returns 0 on game over. */
static int gravity(void)
{
    if (!collide(s_type, s_rot, s_px, s_py + 1)) { s_py++; return 1; }
    lock_piece();
    spawn();
    return !collide(s_type, s_rot, s_px, s_py);   /* spawn blocked = over */
}

void app_main(void)
{
    if (game_open(&gm) != 0) duneos_exit(10);
    s_rng = game_now_ms() | 1u;

    cell = (gm.h - BAR) / ROWS;
    if (cell * COLS > gm.w) cell = gm.w / COLS;
    ox = (gm.w - COLS * cell) / 2;
    oy = BAR;

    well = gfx_canvas_new(COLS * cell, ROWS * cell);
    if (!well) { game_close(&gm); duneos_exit(11); }

    reset();
    draw_chrome();
    render();

    for (;;) {
        uint16_t key;
        int level = s_lines / 10;
        uint32_t period = 500 - (level * 40 > 400 ? 400 : level * 40);
        switch (game_wait(&gm, period, &key)) {
        case GAME_KEY:
            switch (key) {
            case KEY_LEFT:  if (!collide(s_type, s_rot, s_px - 1, s_py)) s_px--; break;
            case KEY_RIGHT: if (!collide(s_type, s_rot, s_px + 1, s_py)) s_px++; break;
            case KEY_UP: {
                int nr = (s_rot + 1) & 3;
                if (!collide(s_type, nr, s_px, s_py)) s_rot = nr;
                break;
            }
            case KEY_DOWN:  if (!collide(s_type, s_rot, s_px, s_py + 1)) s_py++; break;
            case KEY_ESC:   goto done;
            default: continue;
            }
            render();
            break;
        case GAME_TICK:
            if (!gravity()) {
                if (game_over(&gm, "Game Over", s_score) == KEY_ESC) goto done;
                reset();
                draw_chrome();   /* overlay clobbered the border */
            }
            render();
            break;
        case GAME_QUIT:
            goto done;
        }
    }

done:
    gfx_canvas_free(well);
    game_close(&gm);
    duneos_exit(0);
}
