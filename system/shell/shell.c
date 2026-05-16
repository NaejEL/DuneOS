/*
 * DuneOS interactive shell.
 *
 * True built-in commands (require shell state or bootstrapping):
 *   cd [path]     change working directory
 *   pwd           print working directory
 *   echo <text>   print text
 *   exit          exit the shell
 *   help          list available commands
 *   run <app>     load and run a .dap app from /sd/apps/
 *
 * All other commands are looked up as /sd/bin/<cmd>.dap and launched
 * synchronously.  Shell state (cwd) is passed via /tmp/.exec_args.
 * See components/duneos_kernel/include/duneos/bin_args.h for the format.
 *
 * Bin apps must return from app_main() — they must NOT call duneos_exit(),
 * which would delete the shell task.
 */

#include <unistd.h>
#include <fcntl.h>
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <stdarg.h>
#include <sys/stat.h>
#include <errno.h>

/* strlcpy is a BSD extension not in freestanding newlib */
static size_t sh_strlcpy(char *dst, const char *src, size_t n)
{
    size_t i;
    for (i = 0; i + 1 < n && src[i]; i++) dst[i] = src[i];
    if (n) dst[i] = '\0';
    return strlen(src);
}

/* ----- DuneOS kernel exports --------------------------------------------- */

extern void duneos_exit(int code);
extern int  esp_get_free_heap_size(void);
extern void esp_restart(void);
extern int  usleep(unsigned int useconds);

typedef void duneos_app_t;
extern int  duneos_loader_load(const char *path, duneos_app_t **out);
extern int  duneos_loader_run(duneos_app_t *app);
extern void duneos_loader_unload(duneos_app_t *app);
extern int  duneos_supervisor_launch(const char *path);
extern int  duneos_supervisor_running_count(void);

/* ----- configuration ----------------------------------------------------- */

#define SHELL_PROMPT   "$ "
#define LINE_MAX_LEN   256
#define HISTORY_DEPTH  16
#define CWD_MAX        256
#define APPS_DIR       "/sd/apps"
#define BIN_DIR        "/sd/bin"

/* ----- state ------------------------------------------------------------- */

static char s_cwd[CWD_MAX]             = "/sd";
static char s_history[HISTORY_DEPTH][LINE_MAX_LEN];
static int  s_hist_count               = 0;
static int  s_hist_nav                 = -1;

/* ----- I/O helpers ------------------------------------------------------- */

static void shell_write(const char *s)
{
    write(STDOUT_FILENO, s, strlen(s));
}

static void shell_puts(const char *s)
{
    shell_write(s);
    shell_write("\r\n");
}

static void shell_printf(const char *fmt, ...)
{
    char buf[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    shell_write(buf);
}

/* ----- line editor ------------------------------------------------------- */

static void history_push(const char *line)
{
    if (line[0] == '\0') return;
    if (s_hist_count > 0 &&
        strcmp(s_history[(s_hist_count - 1) % HISTORY_DEPTH], line) == 0)
        return;
    sh_strlcpy(s_history[s_hist_count % HISTORY_DEPTH], line, LINE_MAX_LEN);
    s_hist_count++;
}

static int read_line(char *buf, int max)
{
    int n = 0;
    s_hist_nav = -1;

    while (1) {
        char c;
        int r = read(STDIN_FILENO, &c, 1);
        if (r <= 0) continue;

        if (c == '\r' || c == '\n') { shell_write("\r\n"); break; }

        if (c == 127 || c == '\b') {
            if (n > 0) { n--; shell_write("\b \b"); }
            continue;
        }

        if (c == '\x1b') {
            char seq[3] = {0};
            read(STDIN_FILENO, &seq[0], 1);
            read(STDIN_FILENO, &seq[1], 1);
            if (seq[0] == '[') {
                if (seq[1] == 'A') {
                    int next = (s_hist_nav < 0) ? s_hist_count - 1 : s_hist_nav - 1;
                    if (next < 0 || s_hist_count == 0) continue;
                    if (next < s_hist_count - HISTORY_DEPTH)
                        next = s_hist_count - HISTORY_DEPTH;
                    s_hist_nav = next;
                    while (n--) shell_write("\b \b");
                    sh_strlcpy(buf, s_history[s_hist_nav % HISTORY_DEPTH], max);
                    n = (int)strlen(buf);
                    shell_write(buf);
                } else if (seq[1] == 'B') {
                    if (s_hist_nav < 0) continue;
                    int next = s_hist_nav + 1;
                    while (n--) shell_write("\b \b");
                    if (next >= s_hist_count) {
                        s_hist_nav = -1; buf[0] = '\0'; n = 0;
                    } else {
                        s_hist_nav = next;
                        sh_strlcpy(buf, s_history[s_hist_nav % HISTORY_DEPTH], max);
                        n = (int)strlen(buf);
                        shell_write(buf);
                    }
                }
            }
            continue;
        }

        if (c == '\x03') { shell_write("^C\r\n"); buf[0] = '\0'; return 0; }

        if (c >= 0x20 && c < 0x7f && n < max - 1) {
            buf[n++] = c;
            write(STDOUT_FILENO, &c, 1);
        }
    }
    buf[n] = '\0';
    return n;
}

/* ----- path resolution --------------------------------------------------- */

static void resolve_path(const char *arg, char *out, size_t out_sz)
{
    if (arg[0] == '/') sh_strlcpy(out, arg, out_sz);
    else snprintf(out, out_sz, "%s/%s", s_cwd, arg);
}

/* ----- exec args --------------------------------------------------------- */

/*
 * Write /tmp/.exec_args so the bin app can read its cwd + argv.
 * Format: cwd\nargc\narg0\narg1\n...
 */
static void write_exec_args(int argc, char **argv)
{
    int fd = open("/tmp/.exec_args", O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) {
        shell_printf("warn: exec_args open failed: %s\r\n", strerror(errno));
        return;
    }

    /* cwd */
    write(fd, s_cwd, strlen(s_cwd));
    write(fd, "\n", 1);

    /* argc */
    char num[12];
    snprintf(num, sizeof(num), "%d\n", argc);
    write(fd, num, strlen(num));

    /* args */
    for (int i = 0; i < argc; i++) {
        write(fd, argv[i], strlen(argv[i]));
        write(fd, "\n", 1);
    }
    close(fd);
}

/* ----- built-in commands ------------------------------------------------- */

static void cmd_cd(int argc, char **argv)
{
    if (argc < 2) { sh_strlcpy(s_cwd, "/sd", sizeof(s_cwd)); return; }
    char path[CWD_MAX];
    resolve_path(argv[1], path, sizeof(path));
    if (strcmp(path, "/") != 0) {
        struct stat st;
        if (stat(path, &st) != 0 || !S_ISDIR(st.st_mode)) {
            shell_printf("cd: %s: not a directory\r\n", path);
            return;
        }
    }
    sh_strlcpy(s_cwd, path, sizeof(s_cwd));
}

static void cmd_echo(int argc, char **argv)
{
    for (int i = 1; i < argc; i++) {
        shell_write(argv[i]);
        if (i < argc - 1) shell_write(" ");
    }
    shell_write("\r\n");
}

static void cmd_run(int argc, char **argv)
{
    if (argc < 2) { shell_puts("usage: run <app>"); return; }

    /* Add .dap extension if omitted */
    const char *name = argv[1];
    char name_buf[128];
    size_t nlen = strlen(name);
    if (nlen < 4 || strcmp(name + nlen - 4, ".dap") != 0) {
        snprintf(name_buf, sizeof(name_buf), "%s.dap", name);
        name = name_buf;
    }

    char path[CWD_MAX];
    if (name[0] == '/') {
        sh_strlcpy(path, name, sizeof(path));
    } else {
        snprintf(path, sizeof(path), "%s/%s", APPS_DIR, name);
        struct stat st;
        if (stat(path, &st) != 0) resolve_path(name, path, sizeof(path));
    }

    /* Launch via supervisor so the app gets its own FreeRTOS task.
     * Direct duneos_loader_run() here runs app_main in the shell task;
     * if the app calls duneos_exit() that kills the shell task. */
    int count_before = duneos_supervisor_running_count();
    if (duneos_supervisor_launch(path) != 0) {
        shell_printf("run: cannot launch '%s'\r\n", path);
        return;
    }
    /* Brief delay to let xTaskCreate() complete before polling */
    usleep(50000);
    /* Block until the launched app finishes */
    while (duneos_supervisor_running_count() > count_before)
        usleep(100000);
}

static void cmd_help(void)
{
    shell_puts("Built-in commands:");
    shell_puts("  cd [path]     change directory");
    shell_puts("  pwd           print working directory");
    shell_puts("  echo <text>   print text");
    shell_puts("  run <app>     load and run a .dap app from /sd/apps/");
    shell_puts("  exit          exit the shell");
    shell_puts("  help          this message");
    shell_puts("");
    shell_puts("External commands (from /sd/bin/):");
    shell_puts("  ls [-l] [path]                list directory");
    shell_puts("  cat <file>                    print file");
    shell_puts("  mkdir <path>                  create directory");
    shell_puts("  rm <file>                     remove file");
    shell_puts("  mv <src> <dst>                move/rename");
    shell_puts("  free                          show free heap");
    shell_puts("  klog                          dump kernel log");
    shell_puts("  gpio info|get|set|mode|pull   GPIO control");
    shell_puts("  battery                       battery status");
    shell_puts("  services                      list running services");
    shell_puts("  restart <name>                force restart a service");
    shell_puts("  tail [-f] <file>              print end of file");
    shell_puts("  input                         print input events (ESC to stop)");
    shell_puts("  reboot                        restart the device");
}

/* ----- PATH fallback ----------------------------------------------------- */

static int try_run_bin(const char *cmd, int argc, char **argv)
{
    char path[CWD_MAX];

    /* Phase 17: /flash/bin/ will be tried first when it exists */
    snprintf(path, sizeof(path), "%s/%s.dap", BIN_DIR, cmd);

    struct stat st;
    if (stat(path, &st) != 0) return -1;

    write_exec_args(argc, argv);

    duneos_app_t *app = NULL;
    if (duneos_loader_load(path, &app) != 0) {
        shell_printf("%s: load failed\r\n", cmd);
        return 0;
    }
    duneos_loader_run(app);
    duneos_loader_unload(app);
    return 0;
}

/* ----- command parser ---------------------------------------------------- */

static void exec_line(char *line)
{
    char *argv[16];
    int   argc = 0;
    char *p    = line;

    while (*p) {
        while (*p == ' ') p++;
        if (*p == '\0') break;
        argv[argc++] = p;
        if (argc >= 16) break;
        while (*p && *p != ' ') p++;
        if (*p == ' ') *p++ = '\0';
    }
    if (argc == 0) return;

    const char *cmd = argv[0];

    if      (strcmp(cmd, "cd")   == 0) cmd_cd(argc, argv);
    else if (strcmp(cmd, "pwd")  == 0) shell_puts(s_cwd);
    else if (strcmp(cmd, "echo") == 0) cmd_echo(argc, argv);
    else if (strcmp(cmd, "run")  == 0) cmd_run(argc, argv);
    else if (strcmp(cmd, "exit") == 0) duneos_exit(0);
    else if (strcmp(cmd, "help") == 0) cmd_help();
    else if (try_run_bin(cmd, argc, argv) != 0)
        shell_printf("%s: command not found\r\n", cmd);
}

/* ----- entry point ------------------------------------------------------- */

void app_main(void)
{
    shell_puts("\r\nDuneOS shell v0.2 -- type 'help' for commands");
    shell_printf("cwd: %s\r\n", s_cwd);

    char line[LINE_MAX_LEN];

    while (1) {
        shell_printf("[%s]" SHELL_PROMPT, s_cwd);
        int n = read_line(line, sizeof(line));
        if (n < 0) break;
        if (n == 0) continue;

        history_push(line);
        exec_line(line);
    }

    duneos_exit(0);
}
