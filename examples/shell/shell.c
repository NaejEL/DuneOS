/*
 * DuneOS interactive shell.
 *
 * A regular DuneOS app (.dap) — not part of the kernel. Uses only exported
 * POSIX symbols plus the handful of DuneOS-specific functions (duneos_exit,
 * duneos_run, duneos_loader_load/run/unload).
 *
 * I/O: uses stdin/stdout (fd 0/1). On CardPuter this is the USB-JTAG console;
 * on boards with UART console it is UART0 (routed by esp_vfs_dev_uart after
 * duneos_vfs_mount_dev installs the UART driver).
 *
 * Built-in commands:
 *   ls [-l] [path]       directory listing
 *   cat <file>           print file to stdout
 *   echo <text>          print text
 *   cd <path>            change working directory (local state, no chdir syscall)
 *   pwd                  print working directory
 *   mkdir <path>         create directory
 *   rm <file>            remove file
 *   mv <src> <dst>       rename/move
 *   run <app.dap>        load and run a DuneOS app synchronously
 *   free                 show available heap
 *   klog                 dump kernel ring buffer
 *   reboot               restart ESP32
 *   help                 list commands
 */

#include <unistd.h>
#include <fcntl.h>
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <stdarg.h>
#include <sys/stat.h>
#include <dirent.h>
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

/* Opaque loader handle — apps only hold a pointer */
typedef void duneos_app_t;
extern int  duneos_loader_load(const char *path, duneos_app_t **out);
extern int  duneos_loader_run(duneos_app_t *app);
extern void duneos_loader_unload(duneos_app_t *app);

/* ----- configuration ----------------------------------------------------- */

#define SHELL_PROMPT    "$ "
#define LINE_MAX_LEN    256
#define HISTORY_DEPTH   16
#define CWD_MAX         256
#define APPS_DIR        "/sd/apps"

/* ----- state ------------------------------------------------------------- */

static char s_cwd[CWD_MAX]              = "/sd";
static char s_history[HISTORY_DEPTH][LINE_MAX_LEN];
static int  s_hist_count                = 0;
static int  s_hist_nav                  = -1;  /* -1 = not navigating */

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
    /* Don't duplicate last entry */
    if (s_hist_count > 0 && strcmp(s_history[(s_hist_count - 1) % HISTORY_DEPTH], line) == 0)
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

        if (c == '\r' || c == '\n') {
            shell_write("\r\n");
            break;
        }

        /* Backspace / DEL */
        if (c == 127 || c == '\b') {
            if (n > 0) {
                n--;
                shell_write("\b \b");
            }
            continue;
        }

        /* ANSI escape sequences (arrows) */
        if (c == '\x1b') {
            char seq[3] = {0};
            read(STDIN_FILENO, &seq[0], 1);
            read(STDIN_FILENO, &seq[1], 1);
            if (seq[0] == '[') {
                if (seq[1] == 'A') {
                    /* Up arrow — older history entry */
                    int next = (s_hist_nav < 0) ? s_hist_count - 1
                                                 : s_hist_nav - 1;
                    if (next < 0 || s_hist_count == 0) continue;
                    if (next < s_hist_count - HISTORY_DEPTH) next = s_hist_count - HISTORY_DEPTH;
                    s_hist_nav = next;
                    /* Erase current line and redraw */
                    while (n--) shell_write("\b \b");
                    sh_strlcpy(buf, s_history[s_hist_nav % HISTORY_DEPTH], max);
                    n = (int)strlen(buf);
                    shell_write(buf);
                } else if (seq[1] == 'B') {
                    /* Down arrow — newer history entry */
                    if (s_hist_nav < 0) continue;
                    int next = s_hist_nav + 1;
                    while (n--) shell_write("\b \b");
                    if (next >= s_hist_count) {
                        s_hist_nav = -1;
                        buf[0] = '\0';
                        n = 0;
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

        /* Ctrl-C */
        if (c == '\x03') {
            shell_write("^C\r\n");
            buf[0] = '\0';
            return 0;
        }

        /* Printable chars */
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
    if (arg[0] == '/') {
        sh_strlcpy(out, arg, out_sz);
    } else {
        snprintf(out, out_sz, "%s/%s", s_cwd, arg);
    }
}

/* ----- command implementations ------------------------------------------- */

static void cmd_ls(int argc, char **argv)
{
    int long_fmt = 0;
    const char *path = s_cwd;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-l") == 0) { long_fmt = 1; continue; }
        path = argv[i];
    }

    char resolved[CWD_MAX];
    resolve_path(path, resolved, sizeof(resolved));

    DIR *dir = opendir(resolved);
    if (!dir) {
        shell_printf("ls: %s: %s\r\n", resolved, strerror(errno));
        return;
    }

    struct dirent *ent;
    while ((ent = readdir(dir)) != NULL) {
        if (long_fmt) {
            char fpath[CWD_MAX + 64];
            snprintf(fpath, sizeof(fpath), "%s/%s", resolved, ent->d_name);
            struct stat st;
            if (stat(fpath, &st) == 0) {
                shell_printf("%c %8ld  %s\r\n",
                             S_ISDIR(st.st_mode) ? 'd' : '-',
                             (long)st.st_size, ent->d_name);
            } else {
                shell_printf("? %8d  %s\r\n", 0, ent->d_name);
            }
        } else {
            shell_printf("%s  ", ent->d_name);
        }
    }
    closedir(dir);
    if (!long_fmt) shell_write("\r\n");
}

static void cmd_cat(int argc, char **argv)
{
    if (argc < 2) { shell_puts("usage: cat <file>"); return; }
    char path[CWD_MAX];
    resolve_path(argv[1], path, sizeof(path));
    int fd = open(path, O_RDONLY);
    if (fd < 0) { shell_printf("cat: %s: %s\r\n", path, strerror(errno)); return; }
    char buf[256];
    ssize_t n;
    while ((n = read(fd, buf, sizeof(buf))) > 0)
        write(STDOUT_FILENO, buf, n);
    close(fd);
    shell_write("\r\n");
}

static void cmd_echo(int argc, char **argv)
{
    for (int i = 1; i < argc; i++) {
        shell_write(argv[i]);
        if (i < argc - 1) shell_write(" ");
    }
    shell_write("\r\n");
}

static void cmd_cd(int argc, char **argv)
{
    if (argc < 2) { sh_strlcpy(s_cwd, "/sd", sizeof(s_cwd)); return; }
    char path[CWD_MAX];
    resolve_path(argv[1], path, sizeof(path));
    struct stat st;
    if (stat(path, &st) != 0 || !S_ISDIR(st.st_mode)) {
        shell_printf("cd: %s: not a directory\r\n", path);
        return;
    }
    sh_strlcpy(s_cwd, path, sizeof(s_cwd));
}

static void cmd_mkdir(int argc, char **argv)
{
    if (argc < 2) { shell_puts("usage: mkdir <path>"); return; }
    char path[CWD_MAX];
    resolve_path(argv[1], path, sizeof(path));
    if (mkdir(path, 0777) != 0)
        shell_printf("mkdir: %s: %s\r\n", path, strerror(errno));
}

static void cmd_rm(int argc, char **argv)
{
    if (argc < 2) { shell_puts("usage: rm <file>"); return; }
    char path[CWD_MAX];
    resolve_path(argv[1], path, sizeof(path));
    if (unlink(path) != 0)
        shell_printf("rm: %s: %s\r\n", path, strerror(errno));
}

static void cmd_mv(int argc, char **argv)
{
    if (argc < 3) { shell_puts("usage: mv <src> <dst>"); return; }
    char src[CWD_MAX], dst[CWD_MAX];
    resolve_path(argv[1], src, sizeof(src));
    resolve_path(argv[2], dst, sizeof(dst));
    if (rename(src, dst) != 0)
        shell_printf("mv: %s → %s: %s\r\n", src, dst, strerror(errno));
}

static void cmd_run(int argc, char **argv)
{
    if (argc < 2) { shell_puts("usage: run <app.dap>"); return; }
    char path[CWD_MAX];
    if (argv[1][0] == '/') {
        sh_strlcpy(path, argv[1], sizeof(path));
    } else {
        /* Look in apps dir first, then cwd */
        snprintf(path, sizeof(path), "%s/%s", APPS_DIR, argv[1]);
        struct stat st;
        if (stat(path, &st) != 0)
            resolve_path(argv[1], path, sizeof(path));
    }

    duneos_app_t *app = NULL;
    if (duneos_loader_load(path, &app) != 0) {
        shell_printf("run: cannot load '%s'\r\n", path);
        return;
    }
    duneos_loader_run(app);
    duneos_loader_unload(app);
}

static void cmd_free(void)
{
    int heap = esp_get_free_heap_size();
    shell_printf("free heap: %d bytes\r\n", heap);
}

static void cmd_klog(void)
{
    int fd = open("/dev/klog", O_RDONLY);
    if (fd < 0) { shell_puts("klog: cannot open /dev/klog"); return; }
    char buf[128];
    ssize_t n;
    while ((n = read(fd, buf, sizeof(buf))) > 0) {
        /* Replace bare \n with \r\n for terminal */
        for (ssize_t i = 0; i < n; i++) {
            if (buf[i] == '\n') shell_write("\r\n");
            else write(STDOUT_FILENO, &buf[i], 1);
        }
    }
    close(fd);
}

static void cmd_help(void)
{
    shell_puts("Commands:");
    shell_puts("  ls [-l] [path]       list directory");
    shell_puts("  cat <file>           print file");
    shell_puts("  echo <text>          print text");
    shell_puts("  cd [path]            change directory");
    shell_puts("  pwd                  print working directory");
    shell_puts("  mkdir <path>         create directory");
    shell_puts("  rm <file>            remove file");
    shell_puts("  mv <src> <dst>       move/rename");
    shell_puts("  run <app>            load and run a DuneOS app");
    shell_puts("  free                 show free heap");
    shell_puts("  klog                 dump kernel log ring buffer");
    shell_puts("  reboot               restart the device");
    shell_puts("  help                 this message");
}

/* ----- command parser ---------------------------------------------------- */

static void exec_line(char *line)
{
    /* Tokenise in-place */
    char *argv[16];
    int   argc = 0;
    char *p = line;

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

    if (strcmp(cmd, "ls")     == 0) cmd_ls(argc, argv);
    else if (strcmp(cmd, "cat")    == 0) cmd_cat(argc, argv);
    else if (strcmp(cmd, "echo")   == 0) cmd_echo(argc, argv);
    else if (strcmp(cmd, "cd")     == 0) cmd_cd(argc, argv);
    else if (strcmp(cmd, "pwd")    == 0) shell_puts(s_cwd);
    else if (strcmp(cmd, "mkdir")  == 0) cmd_mkdir(argc, argv);
    else if (strcmp(cmd, "rm")     == 0) cmd_rm(argc, argv);
    else if (strcmp(cmd, "mv")     == 0) cmd_mv(argc, argv);
    else if (strcmp(cmd, "run")    == 0) cmd_run(argc, argv);
    else if (strcmp(cmd, "free")   == 0) cmd_free();
    else if (strcmp(cmd, "klog")   == 0) cmd_klog();
    else if (strcmp(cmd, "reboot") == 0) esp_restart();
    else if (strcmp(cmd, "exit")   == 0) duneos_exit(0);
    else if (strcmp(cmd, "help")   == 0) cmd_help();
    else shell_printf("%s: command not found\r\n", cmd);
}

/* ----- entry point ------------------------------------------------------- */

void app_main(void)
{
    shell_puts("\r\nDuneOS shell v0.1 — type 'help' for commands");
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
