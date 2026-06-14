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
#include <sys/stat.h>           /* mkdir for /tmp/state */

#include <duneos/gpio_ioctl.h>
#include <duneos/input_ioctl.h>
#include <duneos/ambient.h>     /* AMBIENT_MOD_* + /tmp/state/kbd */

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

/* ----- configurable sticky modifiers (ADR 027, /flash/etc/kb_iomatrix) ----- */
/* Each of Fn/Shift/Ctrl/Alt has a latch MODE from config.yaml:
 *   momentary : hold to use, never latches (the bare modifier)
 *   toggle    : a tap latches until the next tap (nav/gaming without holding Fn)
 *   oneshot   : a tap latches for the NEXT key only (sticky-keys), then clears
 * Fn/Shift select the keymap layer (2 / 1); Ctrl/Alt are held-modifier events
 * apps consume (KEY_CTRL/KEY_ALT). Opt is left as a plain momentary modifier.
 * The latched set is published to /tmp/state/kbd for the status bar. */

extern int duneos_config_path(const char *app, char *out, size_t outsz);

typedef enum { MOD_MOMENTARY, MOD_TOGGLE, MOD_ONESHOT } latch_mode_t;

typedef struct {
    const char  *name;     /* config key                                       */
    uint8_t      row, col; /* matrix position                                  */
    uint8_t      code;     /* KEY_* (held event for Ctrl/Alt; wake for Fn/Shift) */
    uint8_t      abit;     /* AMBIENT_MOD_* for the status bar                 */
    int8_t       layer;    /* keymap layer when active (Fn=2, Shift=1); -1=held */
    latch_mode_t mode;     /* from config (default toggle)                     */
    bool down, used, latch, sent;   /* runtime                                 */
} modifier_t;

static modifier_t s_mods[] = {
    { "fn",    2, 0, KEY_FN,    AMBIENT_MOD_FN,     2, MOD_TOGGLE, 0,0,0,0 },
    { "shift", 2, 1, KEY_SHIFT, AMBIENT_MOD_SHIFT,  1, MOD_TOGGLE, 0,0,0,0 },
    { "ctrl",  3, 0, KEY_CTRL,  AMBIENT_MOD_CTRL,  -1, MOD_TOGGLE, 0,0,0,0 },
    { "alt",   3, 2, KEY_ALT,   AMBIENT_MOD_ALT,   -1, MOD_TOGGLE, 0,0,0,0 },
};
#define NMODS (int)(sizeof(s_mods) / sizeof(s_mods[0]))

static uint16_t s_pub_locks = 0xffff; /* last published (locks|oneshot<<8); forces 1st write */

static bool is_mod_pos(int r, int c)
{
    for (int i = 0; i < NMODS; i++)
        if (s_mods[i].row == r && s_mods[i].col == c) return true;
    return false;
}

/* One modifier's latch state machine for this scan's physical level. A "tap" =
 * press then release with no other key used during the hold. */
static void mod_step(modifier_t *m, bool phys)
{
    if (phys && !m->down)      { m->down = true; m->used = false; }
    else if (!phys && m->down) {
        m->down = false;
        if (m->mode != MOD_MOMENTARY && !m->used) m->latch = !m->latch;
    }
}

static latch_mode_t parse_mode(const char *s)
{
    while (*s == ' ' || *s == '\t') s++;
    if (strncmp(s, "moment",  6) == 0) return MOD_MOMENTARY;
    if (strncmp(s, "oneshot", 7) == 0 || strncmp(s, "one-shot", 8) == 0) return MOD_ONESHOT;
    return MOD_TOGGLE;
}

/* /flash/etc/kb_iomatrix/config.yaml — one "name: mode" line per modifier.
 * Missing file or keys keep the table defaults. */
static void read_config(void)
{
    char path[64];
    if (duneos_config_path("kb_iomatrix", path, sizeof(path)) != 0) return;
    int fd = open(path, O_RDONLY);
    if (fd < 0) return;
    /* Read the whole file: the comment header alone can exceed a small buffer,
     * pushing the actual `name: mode` lines past a single short read. Loop so a
     * partial read() can't truncate the config silently. */
    char buf[1024];
    int total = 0;
    for (;;) {
        int r = (int)read(fd, buf + total, sizeof(buf) - 1 - total);
        if (r <= 0) break;
        total += r;
        if (total >= (int)sizeof(buf) - 1) break;
    }
    close(fd);
    if (total <= 0) return;
    buf[total] = '\0';

    for (char *line = buf; line && *line; ) {
        char *nl = strchr(line, '\n');
        if (nl) *nl = '\0';
        char *colon = strchr(line, ':');
        if (colon) {
            *colon = '\0';
            char *name = line;  while (*name == ' ' || *name == '\t') name++;
            char *end  = name + strlen(name);
            while (end > name && (end[-1] == ' ' || end[-1] == '\t' || end[-1] == '\r'))
                *--end = '\0';
            if (*name && *name != '#')
                for (int i = 0; i < NMODS; i++)
                    if (strcmp(s_mods[i].name, name) == 0)
                        s_mods[i].mode = parse_mode(colon + 1);
        }
        line = nl ? nl + 1 : 0;
    }
}

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

/* Publish the latched modifiers to /tmp/state/kbd (ADR 027) for the status bar:
 * `locks` = all latched, `oneshot` = those armed in one-shot mode (own colour).
 * Only writes on change. */
static void publish_locks(void)
{
    uint8_t locks = 0, oneshot = 0;
    for (int i = 0; i < NMODS; i++)
        if (s_mods[i].latch) {
            locks |= s_mods[i].abit;
            if (s_mods[i].mode == MOD_ONESHOT) oneshot |= s_mods[i].abit;
        }
    uint16_t packed = (uint16_t)locks | ((uint16_t)oneshot << 8);
    if (packed == s_pub_locks) return;
    s_pub_locks = packed;

    mkdir(AMBIENT_STATE_DIR, 0755);   /* harmless if it already exists */
    int fd = open(AMBIENT_KBD_PATH, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) return;
    ambient_kbd_t k = { .locks = locks, .oneshot = oneshot };
    write(fd, &k, sizeof(k));
    close(fd);
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

    read_config();   /* per-modifier latch mode from /flash/etc/kb_iomatrix */

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

        /* --- modifiers: latch state, resolved layer, held events, wake --- */
        bool edge[NMODS];
        for (int i = 0; i < NMODS; i++) {
            bool phys = cur[s_mods[i].row][s_mods[i].col];
            edge[i] = (phys != s_mods[i].down);
            mod_step(&s_mods[i], phys);
        }

        int layer = 0;
        for (int i = 0; i < NMODS; i++) {
            if (s_mods[i].layer < 0) continue;
            bool active = cur[s_mods[i].row][s_mods[i].col] || s_mods[i].latch;
            if (active && s_mods[i].layer > layer) layer = s_mods[i].layer;
        }

        /* Publish before the wake events so the foreground reads fresh state. */
        publish_locks();

        for (int i = 0; i < NMODS; i++) {
            bool phys   = cur[s_mods[i].row][s_mods[i].col];
            bool active = phys || s_mods[i].latch;
            if (s_mods[i].layer < 0) {
                /* Ctrl/Alt: held-modifier event apps consume (Ctrl+C…). The emit
                 * also wakes the foreground app to repaint the indicator. */
                if (active != s_mods[i].sent) {
                    input_event_t ev = { .type = INPUT_EV_KEY, .code = s_mods[i].code,
                                         .value = active ? INPUT_VAL_PRESS : INPUT_VAL_RELEASE };
                    inject(&ev);
                    s_mods[i].sent = active;
                }
            } else if (edge[i]) {
                /* Fn/Shift change the LAYER of other keys — no event otherwise.
                 * Emit the (app-ignored) code on a physical edge so the
                 * foreground app wakes and repaints the indicator. */
                input_event_t ev = { .type = INPUT_EV_KEY, .code = s_mods[i].code,
                                     .value = phys ? INPUT_VAL_PRESS : INPUT_VAL_RELEASE };
                inject(&ev);
            }
        }

        /* --- ordinary keys at the resolved layer --- */
        bool key_pressed = false;
        for (int r = 0; r < DUNEOS_KB_MATRIX_ROWS; r++) {
            for (int c = 0; c < DUNEOS_KB_MATRIX_COLS; c++) {
                if (is_mod_pos(r, c)) continue;   /* configured modifiers handled above */
                if (cur[r][c] == s_prev_state[r][c]) continue;
                s_prev_state[r][c] = cur[r][c];

                uint8_t code = s_keymap[r][c][layer];
                /* No binding on the Fn/Shift layer → fall through to the base key,
                 * so Enter / letters / space still work while a layer modifier is
                 * active (e.g. Fn latched for arrows during a game). */
                if (code == 0x00 && layer > 0) code = s_keymap[r][c][0];
                if (code == 0x00) continue;

                if (cur[r][c]) {   /* press → a held modifier was used, not tapped */
                    key_pressed = true;
                    for (int i = 0; i < NMODS; i++)
                        if (s_mods[i].down) s_mods[i].used = true;
                }

                input_event_t ev = {
                    .time_ms = 0,   /* kernel side fills it from monotonic_us */
                    .type    = INPUT_EV_KEY,
                    .code    = code,
                    .value   = cur[r][c] ? INPUT_VAL_PRESS : INPUT_VAL_RELEASE,
                };
                inject(&ev);
            }
        }

        /* --- one-shot: a tap-latched modifier clears after the next key --- */
        if (key_pressed) {
            bool changed = false;
            for (int i = 0; i < NMODS; i++)
                if (s_mods[i].mode == MOD_ONESHOT && s_mods[i].latch &&
                    !cur[s_mods[i].row][s_mods[i].col]) {
                    s_mods[i].latch = false;
                    changed = true;
                }
            if (changed) {
                publish_locks();
                for (int i = 0; i < NMODS; i++) {
                    if (s_mods[i].layer >= 0) continue;
                    bool active = cur[s_mods[i].row][s_mods[i].col] || s_mods[i].latch;
                    if (active != s_mods[i].sent) {
                        input_event_t ev = { .type = INPUT_EV_KEY, .code = s_mods[i].code,
                                             .value = active ? INPUT_VAL_PRESS : INPUT_VAL_RELEASE };
                        inject(&ev);
                        s_mods[i].sent = active;
                    }
                }
            }
        }

        usleep(SCAN_PERIOD_MS * 1000);
    }
}
#endif  /* DUNEOS_KB_MATRIX_ROWS */
