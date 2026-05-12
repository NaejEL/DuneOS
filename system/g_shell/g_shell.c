/*
 * DuneOS graphical shell — g_shell
 *
 * ST7789 terminal: 30 cols × 16 rows at 8×8 px (240×128 of the 135-px panel).
 * Row 0:    status bar (dark blue)
 * Rows 1-14: scrolling command output (14 lines)
 * Row 15:   input line  "$ <text><cursor>"
 *
 * Input via /dev/input/event0.  Output captured via duneos_loader_run_captured.
 * Launched by the supervisor; calls duneos_exit(0) on "exit".
 */

#include <unistd.h>
#include <fcntl.h>
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <sys/stat.h>
#include <errno.h>

#include "duneos/input_ioctl.h"
#include "duneos/disp_ioctl.h"
#include "duneos/bin_args.h"
#include "font8x8.h"

/* POSIX extensions not declared under -ffreestanding */
extern int dprintf(int fd, const char *fmt, ...);
extern int usleep(unsigned int useconds);

/* ---- kernel ABI forward declarations ------------------------------------ */

extern void duneos_exit(int code);
typedef void duneos_app_t;
extern int  duneos_loader_load(const char *path, duneos_app_t **out);
extern int  duneos_loader_run_captured(duneos_app_t *app, char **out_buf, size_t *out_len);
extern void duneos_loader_unload(duneos_app_t *app);

/* ---- layout constants --------------------------------------------------- */

#define COLS        30
#define ROWS        16
#define OUT_ROWS    14
#define STATUS_ROW   0
#define INPUT_ROW   15
#define INPUT_MAX   64
#define CWD_MAX     128
#define BIN_DIR     "/sd/bin"

/* ---- RGB565 color palette (host-endian; kernel byte-swaps for SPI) ------ */

#define C_BG        0x0000u   /* black         */
#define C_FG        0xFFFFu   /* white         */
#define C_STATUS_BG 0x000Fu   /* dark navy     */
#define C_STATUS_FG 0xFFFFu
#define C_PROMPT    0x07E0u   /* bright green  */
#define C_CURSOR_BG 0x07E0u   /* green block   */
#define C_CURSOR_FG 0x0000u

/* ---- global state ------------------------------------------------------- */

static int  s_disp  = -1;
static int  s_input = -1;

static char s_out[OUT_ROWS][COLS + 1];
static int  s_out_used = 0;

static char s_cwd[CWD_MAX] = "/sd";

static char s_line[INPUT_MAX + 1];
static int  s_line_len = 0;

static int  s_exit = 0;

/* Row pixel buffer — 240 × 8 × 2 bytes = 3840 bytes (static, not on stack) */
static uint16_t s_px[240 * 8];

/* ---- rendering ---------------------------------------------------------- */

static void render_row(int row, const char *text, int len,
                        uint16_t fg, uint16_t bg, int cursor_col)
{
    for (int col = 0; col < COLS; col++) {
        char c = (col < len) ? text[col] : ' ';
        if ((unsigned char)c < 32 || (unsigned char)c > 126) c = ' ';
        const uint8_t *g = font8x8_basic[(unsigned char)c - 32];
        uint16_t this_fg = fg, this_bg = bg;
        if (col == cursor_col) { this_fg = C_CURSOR_FG; this_bg = C_CURSOR_BG; }
        for (int r = 0; r < 8; r++) {
            uint8_t bits = g[r];
            for (int x = 0; x < 8; x++)
                s_px[r * 240 + col * 8 + x] = ((bits >> x) & 1) ? this_fg : this_bg;
        }
    }
    disp_window_t win = { 0, (uint16_t)(row * 8), 240, 8 };
    ioctl(s_disp, DISP_SET_WINDOW, &win);
    write(s_disp, s_px, sizeof(s_px));
}

static void draw_status(void)
{
    char buf[COLS + 1];
    snprintf(buf, sizeof(buf), " g_shell  %s", s_cwd);
    render_row(STATUS_ROW, buf, strlen(buf), C_STATUS_FG, C_STATUS_BG, -1);
}

static void draw_output(void)
{
    for (int i = 0; i < OUT_ROWS; i++) {
        if (i < s_out_used)
            render_row(i + 1, s_out[i], strlen(s_out[i]), C_FG, C_BG, -1);
        else
            render_row(i + 1, "", 0, C_FG, C_BG, -1);
    }
}

static void draw_input(void)
{
    /* "$ " prefix (2 cols) + up to COLS-3 chars of input + cursor block */
    char buf[COLS + 1];
    buf[0] = '$'; buf[1] = ' ';
    int avail  = COLS - 3;
    int start  = (s_line_len > avail) ? s_line_len - avail : 0;
    int show   = s_line_len - start;
    memcpy(buf + 2, s_line + start, show);
    int cur_col = 2 + show;
    buf[cur_col] = ' ';
    render_row(INPUT_ROW, buf, cur_col + 1, C_PROMPT, C_BG, cur_col);
}

static void full_redraw(void)
{
    draw_status();
    draw_output();
    draw_input();
}

/* ---- output scrolling --------------------------------------------------- */

static void out_push(const char *line)
{
    if (s_out_used < OUT_ROWS) {
        strncpy(s_out[s_out_used], line, COLS);
        s_out[s_out_used][COLS] = '\0';
        s_out_used++;
    } else {
        memmove(s_out[0], s_out[1], (OUT_ROWS - 1) * (COLS + 1));
        strncpy(s_out[OUT_ROWS - 1], line, COLS);
        s_out[OUT_ROWS - 1][COLS] = '\0';
    }
}

/* Append potentially multi-line text, wrapping at COLS */
static void out_text(const char *text)
{
    char line[COLS + 1];
    int  col = 0;
    for (const char *p = text; *p; p++) {
        if (*p == '\r') continue;
        if (*p == '\n' || col == COLS) {
            line[col] = '\0';
            out_push(line);
            col = 0;
            if (*p == '\n') continue;
        }
        line[col++] = *p;
    }
    if (col > 0) { line[col] = '\0'; out_push(line); }
}

/* ---- argument passing --------------------------------------------------- */

static void write_exec_args(int argc, char **argv)
{
    int fd = open(DUNEOS_EXEC_ARGS_PATH, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) return;
    dprintf(fd, "%s\n%d\n", s_cwd, argc);
    for (int i = 0; i < argc; i++) dprintf(fd, "%s\n", argv[i]);
    close(fd);
}

/* ---- tokenizer ---------------------------------------------------------- */

static int tokenize(char *line, char **argv, int maxargv)
{
    int   argc = 0;
    char *p    = line;
    while (*p && argc < maxargv) {
        while (*p == ' ' || *p == '\t') p++;
        if (!*p) break;
        argv[argc++] = p;
        while (*p && *p != ' ' && *p != '\t') p++;
        if (*p) *p++ = '\0';
    }
    return argc;
}

/* ---- bin app runner with stdout capture --------------------------------- */

static int run_bin(const char *cmd, int argc, char **argv)
{
    char path[256];
    snprintf(path, sizeof(path), "%s/%s.dap", BIN_DIR, cmd);
    struct stat st;
    if (stat(path, &st) != 0) return -1;

    write_exec_args(argc, argv);

    duneos_app_t *app = NULL;
    if (duneos_loader_load(path, &app) != 0) {
        out_push("error: load failed");
        return 0;
    }

    char  *buf = NULL;
    size_t len = 0;
    duneos_loader_run_captured(app, &buf, &len);
    duneos_loader_unload(app);

    if (buf) {
        if (len > 0) out_text(buf);
        free(buf);
    }
    return 0;
}

/* ---- path resolution ---------------------------------------------------- */

static void resolve_path(const char *arg, char *out, int outsz)
{
    if (arg[0] == '/') snprintf(out, outsz, "%s", arg);
    else               snprintf(out, outsz, "%s/%s", s_cwd, arg);
}

/* ---- command dispatch --------------------------------------------------- */

static void exec_line(char *line)
{
    char *argv[16];
    int   argc = tokenize(line, argv, 16);
    if (argc == 0) return;
    const char *cmd = argv[0];

    if (strcmp(cmd, "exit") == 0) {
        s_exit = 1;
        return;
    }
    if (strcmp(cmd, "clear") == 0) {
        s_out_used = 0;
        return;
    }
    if (strcmp(cmd, "pwd") == 0) {
        out_push(s_cwd);
        return;
    }
    if (strcmp(cmd, "cd") == 0) {
        char newcwd[CWD_MAX];
        if (argc < 2) snprintf(newcwd, sizeof(newcwd), "/sd");
        else          resolve_path(argv[1], newcwd, sizeof(newcwd));
        struct stat st;
        if (stat(newcwd, &st) == 0 && S_ISDIR(st.st_mode))
            strncpy(s_cwd, newcwd, CWD_MAX - 1);
        else
            out_push("cd: not a directory");
        return;
    }
    if (strcmp(cmd, "help") == 0) {
        out_push("built-ins: cd pwd clear help exit");
        out_push("/sd/bin/<cmd>.dap for all others");
        return;
    }

    if (run_bin(cmd, argc, argv) != 0) {
        char msg[COLS + 1];
        snprintf(msg, sizeof(msg), "%.28s: not found", cmd);
        out_push(msg);
    }
}

/* ---- main loop ---------------------------------------------------------- */

void app_main(void)
{
    s_disp  = open("/dev/disp0",        O_WRONLY);
    s_input = open("/dev/input/event0", O_RDONLY);

    if (s_disp < 0 || s_input < 0) {
        if (s_disp  >= 0) close(s_disp);
        if (s_input >= 0) close(s_input);
        duneos_exit(1);
    }

    full_redraw();

    while (!s_exit) {
        input_event_t ev;
        if (read(s_input, &ev, sizeof(ev)) != (int)sizeof(ev)) continue;

        if (ev.type == INPUT_EV_KEY && ev.value != INPUT_VAL_RELEASE) {
            uint16_t k = ev.code;
            if (k >= 0x20 && k < 0x7F && s_line_len < INPUT_MAX) {
                s_line[s_line_len++] = (char)k;
                s_line[s_line_len]   = '\0';
                draw_input();
            } else if (k == KEY_BACKSPACE && s_line_len > 0) {
                s_line[--s_line_len] = '\0';
                draw_input();
            } else if (k == KEY_ENTER && s_line_len > 0) {
                char echo[COLS + 1];
                snprintf(echo, sizeof(echo), "$ %.*s", COLS - 2, s_line);
                out_push(echo);

                char cmd[INPUT_MAX + 1];
                memcpy(cmd, s_line, s_line_len + 1);
                s_line_len = 0;
                s_line[0]  = '\0';

                exec_line(cmd);

                draw_status();
                draw_output();
                draw_input();
            } else if (k == KEY_ESC) {
                s_line_len = 0;
                s_line[0]  = '\0';
                draw_input();
            }
        }
        /* REL_WHEEL: could implement output scrollback; skip for now */
    }

    close(s_disp);
    close(s_input);
    duneos_exit(0);
}
