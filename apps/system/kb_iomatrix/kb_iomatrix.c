/*
 * kb_iomatrix — userspace keyboard matrix scanner daemon.
 *
 * Replaces the in-kernel kb_iomatrix.c (24-debt #5). Opens /dev/gpiochip0
 * to drive the 74HC138 row decoder + read the column inputs, decodes
 * presses against the layered keymap, and injects each transition into the
 * kernel's /dev/input/event0 ring buffer via ioctl(INPUT_INJECT_EVENT).
 *
 * The keymap is CardPuter-specific (4 rows × 14 cols + Fn/Shift layers).
 * If another iomatrix-style board appears (per ADR 017 "extract on second
 * use" rule), split the keymap into a board-specific header and rename
 * this daemon to apps/system/kb_iomatrix_<board>/.
 *
 * Inter-row settling: relies on the natural latency of multiple ioctl
 * calls (~few µs each on no-MMU) — the 74HC138 settles in ns. No explicit
 * delay needed.
 */

#include <duneos/board.h>

extern void duneos_exit(int code);

#ifndef DUNEOS_KB_MATRIX_ROWS
/* Board has no `keyboard_matrix:` section — daemon is a no-op stub here. */
void app_main(void) { duneos_exit(0); }
#else

#include <fcntl.h>
#include <unistd.h>
#include <string.h>
#include <stdbool.h>
#include <stdint.h>

#include <duneos/gpio_ioctl.h>
#include <duneos/input_ioctl.h>

extern int usleep(unsigned int useconds);

#define SCAN_PERIOD_MS  10

/* ----- keymap (CardPuter, 4 rows × 14 cols × 3 layers) ------------------- */

#define LAYERS 3   /* 0 = normal  1 = shifted  2 = fn */

static const uint8_t s_keymap[4][14][LAYERS] = {
    /* Row 0 — number row */
    /* col 0  */ { {'`',  '~',  KEY_ESC   },
    /* col 1  */   {'1',  '!',  0         },
    /* col 2  */   {'2',  '@',  0         },
    /* col 3  */   {'3',  '#',  0         },
    /* col 4  */   {'4',  '$',  0         },
    /* col 5  */   {'5',  '%',  0         },
    /* col 6  */   {'6',  '^',  0         },
    /* col 7  */   {'7',  '&',  0         },
    /* col 8  */   {'8',  '*',  0         },
    /* col 9  */   {'9',  '(',  0         },
    /* col 10 */   {'0',  ')',  0         },
    /* col 11 */   {'-',  '_',  0         },
    /* col 12 */   {'=',  '+',  0         },
    /* col 13 */   {KEY_BACKSPACE, KEY_BACKSPACE, KEY_DELETE} },

    /* Row 1 — QWERTY */
    /* col 0  */ { {KEY_TAB, KEY_TAB, 0   },
    /* col 1  */   {'q',  'Q',  0         },
    /* col 2  */   {'w',  'W',  0         },
    /* col 3  */   {'e',  'E',  0         },
    /* col 4  */   {'r',  'R',  0         },
    /* col 5  */   {'t',  'T',  0         },
    /* col 6  */   {'y',  'Y',  0         },
    /* col 7  */   {'u',  'U',  0         },
    /* col 8  */   {'i',  'I',  0         },
    /* col 9  */   {'o',  'O',  0         },
    /* col 10 */   {'p',  'P',  0         },
    /* col 11 */   {'[',  '{',  0         },
    /* col 12 */   {']',  '}',  0         },
    /* col 13 */   {'\\', '|',  0         } },

    /* Row 2 — ASDF + Fn/Shift */
    /* col 0  */ { {KEY_FN,    KEY_FN,    0    },
    /* col 1  */   {KEY_SHIFT, KEY_SHIFT, 0    },
    /* col 2  */   {'a',  'A',  0              },
    /* col 3  */   {'s',  'S',  0              },
    /* col 4  */   {'d',  'D',  0              },
    /* col 5  */   {'f',  'F',  0              },
    /* col 6  */   {'g',  'G',  0              },
    /* col 7  */   {'h',  'H',  0              },
    /* col 8  */   {'j',  'J',  0              },
    /* col 9  */   {'k',  'K',  0              },
    /* col 10 */   {'l',  'L',  0              },
    /* col 11 */   {';',  ':',  KEY_UP         },
    /* col 12 */   {'\'', '"',  0              },
    /* col 13 */   {KEY_ENTER, KEY_ENTER, 0    } },

    /* Row 3 — ZXCV + modifiers + Space */
    /* col 0  */ { {KEY_CTRL, KEY_CTRL, 0  },
    /* col 1  */   {KEY_OPT,  KEY_OPT,  0  },
    /* col 2  */   {KEY_ALT,  KEY_ALT,  0  },
    /* col 3  */   {'z',  'Z',  0          },
    /* col 4  */   {'x',  'X',  0          },
    /* col 5  */   {'c',  'C',  0          },
    /* col 6  */   {'v',  'V',  0          },
    /* col 7  */   {'b',  'B',  0          },
    /* col 8  */   {'n',  'N',  0          },
    /* col 9  */   {'m',  'M',  0          },
    /* col 10 */   {',',  '<',  KEY_LEFT   },
    /* col 11 */   {'.',  '>',  KEY_DOWN   },
    /* col 12 */   {'/',  '?',  KEY_RIGHT  },
    /* col 13 */   {' ',  ' ',  0          } },
};

#define FN_ROW    2
#define FN_COL    0
#define SHIFT_ROW 2
#define SHIFT_COL 1

static const int s_row_pins[3] = {
    DUNEOS_KB_ROW_A0_PIN,
    DUNEOS_KB_ROW_A1_PIN,
    DUNEOS_KB_ROW_A2_PIN,
};
static const int s_col_pins[DUNEOS_KB_NUM_COLS] = DUNEOS_KB_COL_PINS;

static bool s_prev_state[DUNEOS_KB_MATRIX_ROWS][DUNEOS_KB_MATRIX_COLS];

/* ----- helpers ----------------------------------------------------------- */

static int gpio_fd  = -1;
static int input_fd = -1;

static int gpio_set_dir(int line, int dir)
{
    gpio_req_t r = { .line = (uint8_t)line, .dir = (uint8_t)dir };
    return ioctl(gpio_fd, GPIOCHIP_SET_DIR, &r);
}

static int gpio_set_pull(int line, int pull)
{
    gpio_req_t r = { .line = (uint8_t)line, .pull = (uint8_t)pull };
    return ioctl(gpio_fd, GPIOCHIP_SET_PULL, &r);
}

static int gpio_write(int line, int val)
{
    gpio_req_t r = { .line = (uint8_t)line, .val = (uint8_t)val };
    return ioctl(gpio_fd, GPIOCHIP_SET_VALUE, &r);
}

static int gpio_read(int line)
{
    gpio_req_t r = { .line = (uint8_t)line };
    if (ioctl(gpio_fd, GPIOCHIP_GET_VALUE, &r) < 0) return -1;
    return r.val;
}

static void inject(const input_event_t *ev)
{
    ioctl(input_fd, INPUT_INJECT_EVENT, (void *)ev);
}

/* ----- main loop --------------------------------------------------------- */

void app_main(void)
{
    gpio_fd  = open("/dev/gpiochip0", O_RDWR);
    if (gpio_fd  < 0) duneos_exit(2);
    input_fd = open(DUNEOS_INPUT_DEV, O_RDWR);
    if (input_fd < 0) { close(gpio_fd); duneos_exit(3); }

    /* Configure row select lines as outputs (low by default). */
    for (int i = 0; i < 3; i++) {
        gpio_set_dir (s_row_pins[i], GPIO_DIR_OUTPUT);
        gpio_set_pull(s_row_pins[i], GPIO_PULL_NONE);
        gpio_write   (s_row_pins[i], 0);
    }
    /* Columns as pulled-up inputs (active-LOW on press). */
    for (int i = 0; i < DUNEOS_KB_NUM_COLS; i++) {
        gpio_set_dir (s_col_pins[i], GPIO_DIR_INPUT);
        gpio_set_pull(s_col_pins[i], GPIO_PULL_UP);
    }

    memset(s_prev_state, 0, sizeof(s_prev_state));
    bool cur[DUNEOS_KB_MATRIX_ROWS][DUNEOS_KB_MATRIX_COLS];

    for (;;) {
        for (int output = 0; output < 8; output++) {
            gpio_write(s_row_pins[0], (output >> 0) & 1);
            gpio_write(s_row_pins[1], (output >> 1) & 1);
            gpio_write(s_row_pins[2], (output >> 2) & 1);

            int row      = (output > 3) ? (7 - output) : (3 - output);
            int col_base = (output > 3) ? 0 : 1;
            for (int bit = 0; bit < DUNEOS_KB_NUM_COLS; bit++) {
                int level = gpio_read(s_col_pins[bit]);
                cur[row][col_base + bit * 2] = (level == 0);
            }
        }

        int layer = cur[FN_ROW][FN_COL]       ? 2 :
                    cur[SHIFT_ROW][SHIFT_COL] ? 1 : 0;

        for (int r = 0; r < DUNEOS_KB_MATRIX_ROWS; r++) {
            for (int c = 0; c < DUNEOS_KB_MATRIX_COLS; c++) {
                if (cur[r][c] == s_prev_state[r][c]) continue;
                s_prev_state[r][c] = cur[r][c];

                uint8_t code = s_keymap[r][c][layer];
                if (code == 0x00) continue;

                input_event_t ev = {
                    .time_ms = 0,   /* kernel side fills it from monotonic_us */
                    .type    = INPUT_EV_KEY,
                    .code    = code,
                    .value   = cur[r][c] ? INPUT_VAL_PRESS : INPUT_VAL_RELEASE,
                };
                inject(&ev);
            }
        }

        usleep(SCAN_PERIOD_MS * 1000);
    }
}
#endif  /* DUNEOS_KB_MATRIX_ROWS */
