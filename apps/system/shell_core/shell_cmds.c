/*
 * shell_cmds.c — DuneOS shell command dispatch (shared by all shell backends).
 *
 * NOT a standalone compilation unit. Include from a backend that provides:
 *
 *   static char  s_cwd[CWD_MAX];          // current working directory
 *   static void  sh_out(const char *s);   // write a string to the shell output
 *   static void  sh_outln(const char *s); // write a string + newline
 *
 * Provides: exec_line(char *line), try_run_bin(), write_exec_args().
 */

#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <sys/stat.h>
#include <errno.h>
#include <fcntl.h>
#include <unistd.h>

static size_t sh_strlcpy(char *dst, const char *src, size_t n)
{
    size_t i;
    for (i = 0; i + 1 < n && src[i]; i++) dst[i] = src[i];
    if (n) dst[i] = '\0';
    return strlen(src);
}

/* ----- kernel ABI -------------------------------------------------------- */

#include "duneos/abi.h"   /* duneos_app_t + duneos_app_manifest_t */

extern void duneos_exit(int code);
extern int  usleep(unsigned int useconds);

extern int  duneos_loader_load(const char *path, duneos_app_t **out);
extern int  duneos_loader_run_captured(duneos_app_t *app, char **out_buf, size_t *out_len);
extern void duneos_loader_unload(duneos_app_t *app);
extern int  duneos_supervisor_launch(const char *path);
extern int  duneos_supervisor_running_count(void);
extern const duneos_app_manifest_t *duneos_loader_get_manifest(const duneos_app_t *app);

/* ----- configuration ----------------------------------------------------- */

#define CWD_MAX       256
#define APPS_DIR      "/sd/apps"
#define BIN_DIR       "/sd/bin"
#define FLASH_BIN_DIR "/flash/bin"

/* ----- path resolution --------------------------------------------------- */

static void resolve_path(const char *arg, char *out, size_t out_sz)
{
    if (arg[0] == '/') sh_strlcpy(out, arg, out_sz);
    else               snprintf(out, out_sz, "%s/%s", s_cwd, arg);
}

/* ----- exec args --------------------------------------------------------- */

static void write_exec_args(int argc, char **argv)
{
    int fd = open("/tmp/.exec_args", O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) return;
    write(fd, s_cwd, strlen(s_cwd));
    write(fd, "\n", 1);
    char num[12];
    snprintf(num, sizeof(num), "%d\n", argc);
    write(fd, num, strlen(num));
    for (int i = 0; i < argc; i++) {
        write(fd, argv[i], strlen(argv[i]));
        write(fd, "\n", 1);
    }
    close(fd);
}

/* ----- built-in commands ------------------------------------------------- */

static void cmd_cd(int argc, char **argv)
{
    if (argc < 2) { sh_strlcpy(s_cwd, "/sd", CWD_MAX); return; }
    char path[CWD_MAX];
    resolve_path(argv[1], path, sizeof(path));
    if (strcmp(path, "/") != 0) {
        struct stat st;
        if (stat(path, &st) != 0 || !S_ISDIR(st.st_mode)) {
            char msg[CWD_MAX + 32];
            snprintf(msg, sizeof(msg), "cd: %s: not a directory", path);
            sh_outln(msg);
            return;
        }
    }
    sh_strlcpy(s_cwd, path, CWD_MAX);
}

static void cmd_echo(int argc, char **argv)
{
    char buf[512];
    int  pos = 0;
    for (int i = 1; i < argc && pos < (int)sizeof(buf) - 1; i++) {
        if (i > 1 && pos < (int)sizeof(buf) - 1) buf[pos++] = ' ';
        int rem  = sizeof(buf) - pos - 1;
        int slen = strlen(argv[i]);
        if (slen > rem) slen = rem;
        memcpy(buf + pos, argv[i], slen);
        pos += slen;
    }
    buf[pos] = '\0';
    sh_outln(buf);
}

static void cmd_run(int argc, char **argv)
{
    if (argc < 2) { sh_outln("usage: run <app>"); return; }
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
    int count_before = duneos_supervisor_running_count();
    if (duneos_supervisor_launch(path) != 0) {
        char msg[CWD_MAX + 32];
        snprintf(msg, sizeof(msg), "run: cannot launch '%s'", path);
        sh_outln(msg);
        return;
    }
    usleep(50000);
    while (duneos_supervisor_running_count() > count_before) usleep(100000);
}

static void cmd_help(void)
{
    sh_outln("Built-in commands:");
    sh_outln("  cd [path]     change directory");
    sh_outln("  pwd           print working directory");
    sh_outln("  echo <text>   print text");
    sh_outln("  run <app>     load and run a .dap app from /sd/apps/");
    sh_outln("  exit          exit the shell");
    sh_outln("  help          this message");
    sh_outln("External (/flash/bin/ or /sd/bin/):");
    sh_outln("  ls cat mkdir rm mv free klog gpio battery");
    sh_outln("  services restart reboot tail input");
}

/* ----- bin fallback ------------------------------------------------------ */

static int try_run_bin(const char *cmd, int argc, char **argv)
{
    char path[CWD_MAX];
    struct stat st;

    snprintf(path, sizeof(path), "%s/%s.dap", FLASH_BIN_DIR, cmd);
    if (stat(path, &st) != 0) {
        snprintf(path, sizeof(path), "%s/%s.dap", BIN_DIR, cmd);
        if (stat(path, &st) != 0) return -1;
    }

    write_exec_args(argc, argv);

    duneos_app_t *app = NULL;
    if (duneos_loader_load(path, &app) != 0) {
        char msg[64];
        snprintf(msg, sizeof(msg), "%s: load failed", cmd);
        sh_outln(msg);
        return 0;
    }

    /* Dispatch heuristic: an app that declares heap_size > 0 in its manifest
     * is signalling "I need a dedicated heap pool". Captured mode runs in the
     * caller's task with no per-app heap, so its malloc() falls back to the
     * global kernel heap — which may be too small/fragmented to satisfy a
     * 64 KiB allocation (e.g. libgfx back-buffer). In that case, unload the
     * captured copy and re-dispatch through supervisor_launch (spawned mode),
     * which honours heap_size and gives the app its own slot.
     *
     * Side effect: stdout is NOT captured for spawned apps. That's fine for
     * gfx apps (they draw, they don't print) and for any future app that
     * declares heap_size — it implicitly opts out of captured stdout.       */
    const duneos_app_manifest_t *m = duneos_loader_get_manifest(app);
    if (m && m->heap_size > 0) {
        duneos_loader_unload(app);
        int count_before = duneos_supervisor_running_count();
        if (duneos_supervisor_launch(path) != 0) {
            char msg[CWD_MAX + 32];
            snprintf(msg, sizeof(msg), "%s: cannot launch", cmd);
            sh_outln(msg);
            return 0;
        }
        usleep(50000);
        while (duneos_supervisor_running_count() > count_before) usleep(100000);
        return 0;
    }

    char  *buf = NULL;
    size_t len = 0;
    duneos_loader_run_captured(app, &buf, &len);
    duneos_loader_unload(app);

    if (buf) {
        if (len > 0) sh_out(buf);  /* backend decides newline handling */
        free(buf);
    }
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
    else if (strcmp(cmd, "pwd")  == 0) sh_outln(s_cwd);
    else if (strcmp(cmd, "echo") == 0) cmd_echo(argc, argv);
    else if (strcmp(cmd, "run")  == 0) cmd_run(argc, argv);
    else if (strcmp(cmd, "exit") == 0) duneos_exit(0);
    else if (strcmp(cmd, "help") == 0) cmd_help();
    else if (try_run_bin(cmd, argc, argv) != 0) {
        char msg[64];
        snprintf(msg, sizeof(msg), "%s: command not found", cmd);
        sh_outln(msg);
    }
}
