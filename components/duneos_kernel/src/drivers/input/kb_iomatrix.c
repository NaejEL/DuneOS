/*
 * IOMatrix keyboard backend — M5Stack CardPuter.
 *
 * 74HC138 3-to-8 row decoder (A0/A1/A2 → Y0..Y7 active-LOW) + 7 column
 * GPIO inputs with pull-ups.  Physical layout: 4 rows × 14 columns.
 *
 * Scan mapping (from M5Stack CardPuter firmware IOMatrix logic):
 *   output 0-3: row = output,     col = bit*2     (left half — even columns)
 *   output 4-7: row = output - 4, col = bit*2 + 1 (right half — odd columns)
 *
 * Keymap layers:
 *   [0] normal   [1] shifted   [2] fn
 *
 * Fn key mappings (user-defined for DuneOS — M5Stack firmware leaves Fn to apps):
 *   `+Fn=ESC   Backspace+Fn=Delete
 *   ;+Fn=Up    ,+Fn=Left   .+Fn=Down   /+Fn=Right
 */

#include "drv_input_priv.h"
#include "duneos/input_ioctl.h"
#include "duneos/klog.h"

#include "board_config.h"

#include "driver/gpio.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_rom_sys.h"

#include <string.h>

static const char *TAG = "duneos/kb_iomatrix";

/* ----- keymap ------------------------------------------------------------- */

#define LAYERS 3   /* 0=normal  1=shifted  2=fn */

static const uint8_t s_keymap[4][14][LAYERS] = {
    /* Row 0 — number row */
    /*         normal        shifted       fn           */
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
    /*         normal        shifted       fn           */
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
    /*         normal        shifted       fn           */
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
    /*         normal        shifted       fn           */
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

/* ----- scan task ---------------------------------------------------------- */

static const int s_row_pins[3] = {
    DUNEOS_KB_ROW_A0_PIN,
    DUNEOS_KB_ROW_A1_PIN,
    DUNEOS_KB_ROW_A2_PIN,
};
static const int s_col_pins[DUNEOS_KB_NUM_COLS] = DUNEOS_KB_COL_PINS;

static bool s_prev_state[DUNEOS_KB_MATRIX_ROWS][DUNEOS_KB_MATRIX_COLS];

static void scan_task(void *arg)
{
    (void)arg;
    bool cur[DUNEOS_KB_MATRIX_ROWS][DUNEOS_KB_MATRIX_COLS];

    while (1) {
        for (int output = 0; output < 8; output++) {
            gpio_set_level(s_row_pins[0], (output >> 0) & 1);
            gpio_set_level(s_row_pins[1], (output >> 1) & 1);
            gpio_set_level(s_row_pins[2], (output >> 2) & 1);
            esp_rom_delay_us(50);

            int row      = (output > 3) ? (7 - output) : (3 - output);
            int col_base = (output > 3) ? 0 : 1;
            for (int bit = 0; bit < DUNEOS_KB_NUM_COLS; bit++)
                cur[row][col_base + bit * 2] = (gpio_get_level(s_col_pins[bit]) == 0);
        }

        /* Layer selection: Fn takes priority over Shift */
        int layer = cur[FN_ROW][FN_COL]    ? 2 :
                    cur[SHIFT_ROW][SHIFT_COL] ? 1 : 0;

        uint32_t now = xTaskGetTickCount() * portTICK_PERIOD_MS;

        for (int r = 0; r < DUNEOS_KB_MATRIX_ROWS; r++) {
            for (int c = 0; c < DUNEOS_KB_MATRIX_COLS; c++) {
                if (cur[r][c] == s_prev_state[r][c]) continue;
                s_prev_state[r][c] = cur[r][c];

                uint8_t code = s_keymap[r][c][layer];
                if (code == 0x00) continue;

                input_event_t ev = {
                    .time_ms = now,
                    .type    = INPUT_EV_KEY,
                    .code    = code,
                    .value   = cur[r][c] ? INPUT_VAL_PRESS : INPUT_VAL_RELEASE,
                };
                drv_input_push_event(&ev);
            }
        }

        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

/* ----- init --------------------------------------------------------------- */

void kb_iomatrix_init(void)
{
    for (int i = 0; i < 3; i++) {
        gpio_config_t cfg = {
            .pin_bit_mask = (1ULL << s_row_pins[i]),
            .mode         = GPIO_MODE_OUTPUT,
            .pull_up_en   = GPIO_PULLUP_DISABLE,
            .pull_down_en = GPIO_PULLDOWN_DISABLE,
            .intr_type    = GPIO_INTR_DISABLE,
        };
        gpio_config(&cfg);
        gpio_set_level(s_row_pins[i], 0);
    }

    for (int i = 0; i < DUNEOS_KB_NUM_COLS; i++) {
        gpio_config_t cfg = {
            .pin_bit_mask = (1ULL << s_col_pins[i]),
            .mode         = GPIO_MODE_INPUT,
            .pull_up_en   = GPIO_PULLUP_ENABLE,
            .pull_down_en = GPIO_PULLDOWN_DISABLE,
            .intr_type    = GPIO_INTR_DISABLE,
        };
        gpio_config(&cfg);
    }

    memset(s_prev_state, 0, sizeof(s_prev_state));
    xTaskCreatePinnedToCore(scan_task, "kb_scan", 2048, NULL, 5, NULL, 0);
    klog_i(TAG, "IOMatrix %dx%d scan task started",
           DUNEOS_KB_MATRIX_ROWS, DUNEOS_KB_MATRIX_COLS);
}
