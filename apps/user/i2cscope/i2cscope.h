#pragma once

/*
 * i2cscope — shared app context for the screen modules.
 *
 * The app is split into one file per tool to serve as the reference layout for
 * a multi-screen DuneOS app: i2cscope.c owns app_main + the menu + the event
 * dispatch; scan.c / xfer.c / sniff.c each own one screen. dbt compiles every
 * *.c in the app dir automatically, and -I<app_dir> makes this header visible
 * as "i2cscope.h" — so adding a screen is: drop a new .c, expose open()/event().
 *
 * Each screen exposes:
 *   void <s>_open(void)        — enter the screen: (re)init widgets, draw.
 *   int  <s>_event(uint16_t k) — handle one key; return 1 to pop back to the
 *                                menu, 0 to stay. Screens own their sub-state.
 */

#include <unistd.h>
#include <fcntl.h>
#include <string.h>
#include <stdio.h>

#include "duneos/i2c_ioctl.h"
#include "duneos/input_ioctl.h"
#include "duneos/gfx.h"
#include "duneos/ui.h"

/* Shared hardware + UI context, defined in i2cscope.c. */
extern gfx_ctx_t *g_gfx;
extern ui_t      *g_ui;
extern int        g_input;     /* /dev/input/event0 */
extern int        g_i2c;       /* /dev/i2c-0        */
extern uint16_t   g_sw, g_sh;  /* screen size       */
extern int        g_bar_h;     /* title/status bar height */
extern int        g_scl_pin, g_sda_pin;  /* resolved from board.info */

/* Colour palette shared by the colour-coded screens. */
#define C_MARK GFX_RGB(  0, 200, 200)   /* START / STOP        */
#define C_ADDR GFX_RGB( 90, 170, 255)   /* address + R/W       */
#define C_DATA GFX_RGB(225, 225, 225)   /* data / written byte */
#define C_ACK  GFX_RGB( 70, 220, 110)   /* ACK                 */
#define C_NACK GFX_RGB(240,  90,  90)   /* NACK                */
#define C_RESP GFX_RGB(255, 200,  90)   /* xfer read-back      */

static inline int hexnib(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    c |= 0x20;
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    return -1;
}

/* Screen modules. */
void scan_open(void);      int scan_event(uint16_t k);
void xfer_open(void);      int xfer_event(uint16_t k);
void sniff_open(void);     int sniff_event(uint16_t k);
void scenario_open(void);  int scenario_event(uint16_t k);
