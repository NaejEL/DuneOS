/*
 * libgame — see <duneos/game.h>.
 */

#include "duneos/game.h"
#include "duneos/input_ioctl.h"

#include <unistd.h>
#include <fcntl.h>
#include <stdio.h>
#include <time.h>
#include <sys/select.h>
#include <sys/time.h>

int game_open(game_t *gm)
{
    gm->gfx = gfx_open_mode(GFX_MODE_STREAM);
    if (!gm->gfx) return -1;
    gm->input = open("/dev/input/event0", O_RDONLY);
    if (gm->input < 0) { gfx_close(gm->gfx); return -1; }
    gm->ui = ui_create(gm->gfx);
    if (!gm->ui) { close(gm->input); gfx_close(gm->gfx); return -1; }
    ui_size(gm->ui, &gm->w, &gm->h);
    gm->next_tick_ms = 0;
    return 0;
}

void game_close(game_t *gm)
{
    if (gm->ui)  ui_destroy(gm->ui);
    if (gm->input >= 0) close(gm->input);
    if (gm->gfx) gfx_close(gm->gfx);
}

uint32_t game_now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint32_t)((uint64_t)ts.tv_sec * 1000u + ts.tv_nsec / 1000000u);
}

game_event_t game_wait(game_t *gm, uint32_t period_ms, uint16_t *key)
{
    if (gm->next_tick_ms == 0) gm->next_tick_ms = game_now_ms() + period_ms;

    for (;;) {
        uint32_t now = game_now_ms();
        if ((int32_t)(gm->next_tick_ms - now) <= 0) {
            gm->next_tick_ms = now + period_ms;
            return GAME_TICK;
        }
        uint32_t wait = gm->next_tick_ms - now;

        fd_set r;
        FD_ZERO(&r);
        FD_SET(gm->input, &r);
        struct timeval tv = { wait / 1000, (wait % 1000) * 1000 };
        int n = select(gm->input + 1, &r, NULL, NULL, &tv);
        if (n < 0) return GAME_QUIT;
        if (n == 0) continue;               /* timeout → loop, next pass ticks */

        input_event_t ev;
        if (read(gm->input, &ev, sizeof(ev)) != (int)sizeof(ev)) return GAME_QUIT;
        if (ev.type == INPUT_EV_KEY && ev.value != INPUT_VAL_RELEASE) {
            *key = ev.code;
            return GAME_KEY;
        }
        /* key release / non-key → keep waiting for the same tick */
    }
}

void game_clear(game_t *gm, uint16_t color)
{
    gfx_rect(gm->gfx, 0, 0, gm->w, gm->h, color);
}

void game_cell(game_t *gm, int ox, int oy, int gx, int gy, int px, uint16_t color)
{
    gfx_rect(gm->gfx, ox + gx * px, oy + gy * px, px - 1, px - 1, color);
}

uint16_t game_over(game_t *gm, const char *title, int score)
{
    char line[32];
    snprintf(line, sizeof(line), "score %d   any key", score);
    ui_message(gm->ui, title, line);
    ui_flush(gm->ui);
    for (;;) {
        input_event_t ev;
        if (read(gm->input, &ev, sizeof(ev)) != (int)sizeof(ev)) return KEY_ESC;
        if (ev.type == INPUT_EV_KEY && ev.value != INPUT_VAL_RELEASE) return ev.code;
    }
}
