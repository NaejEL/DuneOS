/*
 * shell_cmds.c — DuneOS shell command dispatch (shared by all shell backends).
 *
 * NOT a standalone compilation unit. Include from a backend that provides:
 *
 *   static char  s_cwd[CWD_MAX];          // current working directory
 *   static void  sh_out(const char *s);   // write a string to the shell output
 *   static void  sh_outln(const char *s); // write a string + newline
 *   static void  sh_write(const char *d, int len); // write a raw chunk (stream sink)
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
#include <dirent.h>
#include <ctype.h>

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

typedef void (*duneos_shell_sink_fn)(const char *data, int len, void *ctx);

extern int  duneos_loader_load(const char *path, duneos_app_t **out);
extern int  duneos_loader_run_captured(duneos_app_t *app, char **out_buf, size_t *out_len);
extern int  duneos_loader_run_captured_streamed(duneos_app_t *app,
                void (*sink)(const char *data, int len, void *ctx), void *ctx);
extern void duneos_loader_unload(duneos_app_t *app);
extern int  duneos_loader_get_captured_exit_code(void);
extern int  duneos_supervisor_launch(const char *path);
extern int  duneos_supervisor_running_count(void);
extern void duneos_supervisor_wait_for_completion(int target_count);
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

/* ----- redirectable output ----------------------------------------------- */
/* Builtin normal output goes through sh_emit so a pipeline / `>` can capture it
 * to a file (s_emit_fd >= 0); otherwise it renders on the shell (backend sh_out).
 * Errors keep using sh_outln directly — they belong on the terminal, not in a
 * redirected file. */
static int s_emit_fd = -1;

static void sh_emitln(const char *s)
{
    if (s_emit_fd >= 0) { write(s_emit_fd, s, strlen(s)); write(s_emit_fd, "\n", 1); }
    else sh_outln(s);
}

/* ----- built-in commands ------------------------------------------------- */

static int cmd_cd(int argc, char **argv)
{
    if (argc < 2) { sh_strlcpy(s_cwd, "/sd", CWD_MAX); return 0; }
    char path[CWD_MAX];
    resolve_path(argv[1], path, sizeof(path));
    if (strcmp(path, "/") != 0) {
        struct stat st;
        if (stat(path, &st) != 0 || !S_ISDIR(st.st_mode)) {
            char msg[CWD_MAX + 32];
            snprintf(msg, sizeof(msg), "cd: %s: not a directory", path);
            sh_outln(msg);
            return 1;
        }
    }
    sh_strlcpy(s_cwd, path, CWD_MAX);
    return 0;
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
    sh_emitln(buf);
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
    duneos_supervisor_wait_for_completion(count_before);
}

static void cmd_help(void)
{
    sh_emitln("Built-in commands:");
    sh_emitln("  cd [path]     change directory");
    sh_emitln("  pwd           print working directory");
    sh_emitln("  echo <text>   print text");
    sh_emitln("  run <app>     load and run a .dap app from /sd/apps/");
    sh_emitln("  test / [ ]    evaluate a condition (sets $?)");
    sh_emitln("  set           list shell variables");
    sh_emitln("  exit          exit the shell");
    sh_emitln("  help          this message");
    sh_emitln("Scripting:  NAME=value   $NAME ${NAME} $?   '...' \"...\"");
    sh_emitln("            $(cmd) subst   $((expr)) math   ; && ||");
    sh_emitln("            if C; then ..; [elif C; then ..;] [else ..;] fi");
    sh_emitln("            for V in W..; do ..; done   while C; do ..; done");
    sh_emitln("            break  continue  source FILE");
    sh_emitln("Pipes/redir: cmd1 | cmd2   cmd > file   cmd >> file   cmd < file");
    sh_emitln("Globs:      *  ?  [..]   (expanded against the filesystem)");
    sh_emitln("External (/flash/bin/ or /sd/bin/):");
    sh_emitln("  ls cat cp head tail touch wc du df grep sed find");
    sh_emitln("  mkdir(-p) rm(-rf) mv  free klog gpio battery");
    sh_emitln("  services restart reboot input ping");
}

/* ----- bin fallback ------------------------------------------------------ */

/* Stream sink for captured apps: the loader hands us each chunk of the app's
 * stdout as it writes. sh_write (backend-provided) renders it. */
/* Re-entrancy guard: a sink must never call back into a sink. If a captured
 * app's fd 1 ever ends up pointing at /dev/shellpipe-of-itself, the sink would
 * recurse forever (instant reboot). One static flag (captured runs are single-
 * threaded, on the shell's task) turns that into a dropped write, not a crash. */
static int s_in_sink = 0;

static void sh_write(const char *data, int len);   /* defined by the backend */
static void stream_sink(const char *data, int len, void *ctx)
{
    (void)ctx;
    if (s_in_sink) return;
    s_in_sink = 1;
    sh_write(data, len);
    s_in_sink = 0;
}

/* Sink that writes a captured app's output to an fd (pipe scratch / `>` file). */
static void file_sink(const char *data, int len, void *ctx)
{
    if (s_in_sink) return;
    s_in_sink = 1;
    int fd = *(int *)ctx;
    if (fd >= 0 && len > 0) write(fd, data, len);
    s_in_sink = 0;
}

/* Run an external bin, streaming its stdout to `sink`. Returns the app's exit
 * code, or -1 if no such command (caller prints "not found"). */
static int try_run_bin(const char *cmd, int argc, char **argv,
                       duneos_shell_sink_fn sink, void *ctx)
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
        return 1;
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
            return 1;
        }
        usleep(50000);
        while (duneos_supervisor_running_count() > count_before) usleep(100000);
        return 0;   /* spawned: exit status not captured */
    }

    /* Stream the app's stdout to the chosen sink as it writes — no spool, no
     * whole-output buffer (so `cat` of a large file is not bounded by heap). */
    int rc = duneos_loader_run_captured_streamed(app, sink, ctx);
    duneos_loader_unload(app);
    if (rc != 0) {   /* e.g. the loader's stack guard refused to run it */
        char m[96];
        snprintf(m, sizeof(m), "%s: not enough stack to run (reduce pipe/script nesting)", cmd);
        sh_outln(m);
        return 1;
    }
    return duneos_loader_get_captured_exit_code();
}

/* ----- tab completion (shared; terminal-agnostic gathering) -------------- */
/*
 * The line editor is shell-specific (VT100 escapes for serial, a libui widget
 * for g_shell), but *what* completes is not — it's builtins + /bin apps for the
 * command word, and filesystem entries for arguments. So the gathering lives
 * here and each backend renders the result (extend the line, list the choices)
 * in its own idiom. State is a small static table; the shells are spawned
 * daemons with their own data pool, so static storage here is fine.
 */

#define COMP_MAX   48
#define COMP_NAME  64

static char s_comp[COMP_MAX][COMP_NAME];
static char s_comp_isdir[COMP_MAX];
static int  s_comp_n;

static const char *const k_builtins[] = {
    "cd", "pwd", "echo", "run", "test", "set", "exit", "help",
};

static void comp_add(const char *name, int isdir)
{
    if (s_comp_n >= COMP_MAX) return;
    for (int i = 0; i < s_comp_n; i++)
        if (strcmp(s_comp[i], name) == 0) return;   /* dedup (e.g. /flash + /sd bin) */
    sh_strlcpy(s_comp[s_comp_n], name, COMP_NAME);
    s_comp_isdir[s_comp_n] = (char)isdir;
    s_comp_n++;
}

/* Case-insensitive ".dap" suffix test — FAT upper-cases 8.3 names. */
static int comp_has_dap(const char *nm, int *base_len)
{
    int l = (int)strlen(nm);
    if (l < 4) return 0;
    const char *e = nm + l - 4;
    if (e[0] != '.' || (e[1] | 0x20) != 'd' || (e[2] | 0x20) != 'a' || (e[3] | 0x20) != 'p')
        return 0;
    *base_len = l - 4;
    return 1;
}

static void comp_scan_bin(const char *dir, const char *pfx, int plen)
{
    DIR *d = opendir(dir);
    if (!d) return;
    struct dirent *e;
    while ((e = readdir(d)) != NULL) {
        int bl;
        if (!comp_has_dap(e->d_name, &bl)) continue;
        if (bl >= COMP_NAME) bl = COMP_NAME - 1;
        char base[COMP_NAME];
        memcpy(base, e->d_name, bl);
        base[bl] = '\0';
        if (plen == 0 || strncmp(base, pfx, plen) == 0) comp_add(base, 0);
    }
    closedir(d);
}

static void comp_scan_dir(const char *dir, const char *base, int blen)
{
    DIR *d = opendir(dir);
    if (!d) return;
    int dlen  = (int)strlen(dir);
    int trail = (dlen > 0 && dir[dlen - 1] == '/');
    struct dirent *e;
    while ((e = readdir(d)) != NULL) {
        const char *nm = e->d_name;
        if (strcmp(nm, ".") == 0 || strcmp(nm, "..") == 0) continue;
        if (nm[0] == '.' && (blen == 0 || base[0] != '.')) continue;  /* hide dotfiles */
        if (blen > 0 && strncmp(nm, base, blen) != 0) continue;
        char full[CWD_MAX];
        if (trail) snprintf(full, sizeof(full), "%s%s",  dir, nm);
        else       snprintf(full, sizeof(full), "%s/%s", dir, nm);
        struct stat st;
        int isdir = (stat(full, &st) == 0 && S_ISDIR(st.st_mode));
        comp_add(nm, isdir);
    }
    closedir(d);
}

/*
 * Gather completions for the trailing token of line[0..len). Fills s_comp[].
 *   *tok_start = index in line where the trailing token begins
 *   *base_off  = offset within that token where the completable prefix starts
 *                (past the last '/', for path arguments)
 * Returns the candidate count.
 */
static int shell_completions(const char *line, int len, int *tok_start, int *base_off)
{
    s_comp_n = 0;

    int ts = len;
    while (ts > 0 && line[ts - 1] != ' ') ts--;
    const char *token = line + ts;
    int tlen = len - ts;

    int is_cmd = 1;
    for (int i = 0; i < ts; i++) if (line[i] != ' ') { is_cmd = 0; break; }

    if (is_cmd) {
        for (size_t i = 0; i < sizeof(k_builtins) / sizeof(k_builtins[0]); i++)
            if (tlen == 0 || strncmp(k_builtins[i], token, tlen) == 0)
                comp_add(k_builtins[i], 0);
        comp_scan_bin(FLASH_BIN_DIR, token, tlen);
        comp_scan_bin(BIN_DIR,       token, tlen);
        *base_off = 0;
    } else {
        int si = -1;
        for (int i = 0; i < tlen; i++) if (token[i] == '/') si = i;
        int boff = (si >= 0) ? si + 1 : 0;

        char dir[CWD_MAX];
        if      (si <  0) sh_strlcpy(dir, s_cwd, sizeof(dir));
        else if (si == 0) sh_strlcpy(dir, "/",   sizeof(dir));
        else {
            char dp[CWD_MAX];
            int dl = si; if (dl >= (int)sizeof(dp)) dl = (int)sizeof(dp) - 1;
            memcpy(dp, token, dl); dp[dl] = '\0';
            if (dp[0] == '/') sh_strlcpy(dir, dp, sizeof(dir));
            else snprintf(dir, sizeof(dir), "%s/%s", s_cwd, dp);
        }
        comp_scan_dir(dir, token + boff, tlen - boff);
        *base_off = boff;
    }
    *tok_start = ts;
    return s_comp_n;
}

/* Longest common prefix of the gathered candidates (for partial completion). */
static int shell_comp_lcp(char *out, int outsz)
{
    if (s_comp_n == 0) { if (outsz) out[0] = '\0'; return 0; }
    sh_strlcpy(out, s_comp[0], outsz);
    int l = (int)strlen(out);
    for (int i = 1; i < s_comp_n; i++) {
        int j = 0;
        while (j < l && s_comp[i][j] && s_comp[i][j] == out[j]) j++;
        l = j; out[l] = '\0';
    }
    return l;
}

static const char *shell_comp_name(int i, int *isdir)
{
    if (i < 0 || i >= s_comp_n) return NULL;
    if (isdir) *isdir = s_comp_isdir[i];
    return s_comp[i];
}

/* ----- shell variables + expansion (scripting, ADR 037) ------------------ */
/* The shell is a spawned daemon (own data pool), so static state is fine here —
 * unlike captured bins. Scope: variables, $VAR/${VAR}/$? expansion, '/" quoting,
 * and ; && || sequencing. Control flow (if/while/for) is the next step. */

#define SH_MAXVARS 24
#define SH_VARNAME 24
#define SH_VARVAL  192

static struct { char name[SH_VARNAME]; char val[SH_VARVAL]; } s_vars[SH_MAXVARS];
static int s_nvars;
static int s_status;       /* $? — exit status of the last command */

enum { LOOP_NONE, LOOP_BREAK, LOOP_CONTINUE };
static int s_loop_ctl = LOOP_NONE;   /* set by break/continue, consumed by the loop */

static int is_name_char(char c)
{
    return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
           (c >= '0' && c <= '9') || c == '_';
}

static const char *var_get(const char *name)
{
    for (int i = 0; i < s_nvars; i++)
        if (strcmp(s_vars[i].name, name) == 0) return s_vars[i].val;
    return "";
}

static void var_set(const char *name, const char *val)
{
    for (int i = 0; i < s_nvars; i++)
        if (strcmp(s_vars[i].name, name) == 0) { sh_strlcpy(s_vars[i].val, val, SH_VARVAL); return; }
    if (s_nvars >= SH_MAXVARS) return;
    sh_strlcpy(s_vars[s_nvars].name, name, SH_VARNAME);
    sh_strlcpy(s_vars[s_nvars].val,  val,  SH_VARVAL);
    s_nvars++;
}

/* Run a command and capture its stdout into `out` (for $(...) substitution).
 * Re-enters the command machinery, so it's deep — the loader's stack guard
 * turns "too deep" into a graceful refusal, not a crash. */
static void run_andor(char *seg);          /* defined later (same TU) */

static void cmd_capture(const char *cmd, char *out, int osz)
{
    out[0] = '\0';
    char line[280];
    snprintf(line, sizeof(line), "%s > /tmp/.cmdsub", cmd);
    run_andor(line);
    int fd = open("/tmp/.cmdsub", O_RDONLY);
    if (fd < 0) return;
    int n = (int)read(fd, out, osz - 1);
    close(fd);
    unlink("/tmp/.cmdsub");
    if (n < 0) n = 0;
    out[n] = '\0';
    while (n > 0 && (out[n - 1] == '\n' || out[n - 1] == '\r')) out[--n] = '\0';
}

/* Integer arithmetic for $((...)) — recursive descent: + - * / % and ( ). */
static const char *s_arith;
static long arith_expr(void);
static void arith_skip(void) { while (*s_arith == ' ' || *s_arith == '\t') s_arith++; }
static long arith_factor(void)
{
    arith_skip();
    if (*s_arith == '(') { s_arith++; long v = arith_expr(); arith_skip(); if (*s_arith == ')') s_arith++; return v; }
    if (*s_arith == '-') { s_arith++; return -arith_factor(); }
    if (*s_arith == '+') { s_arith++; return  arith_factor(); }
    if (*s_arith == '$') s_arith++;                       /* allow $var inside */
    if (*s_arith >= '0' && *s_arith <= '9') {
        long v = 0;
        while (*s_arith >= '0' && *s_arith <= '9') v = v * 10 + (*s_arith++ - '0');
        return v;
    }
    char name[SH_VARNAME]; int n = 0;
    while (is_name_char(*s_arith) && n < SH_VARNAME - 1) name[n++] = *s_arith++;
    name[n] = '\0';
    return n ? atoi(var_get(name)) : 0;   /* atoi is exported; long==int (ILP32) */
}
static long arith_term(void)
{
    long v = arith_factor();
    for (;;) {
        arith_skip();
        char op = *s_arith;
        if      (op == '*') { s_arith++; v *= arith_factor(); }
        else if (op == '/') { s_arith++; long d = arith_factor(); v = d ? v / d : 0; }
        else if (op == '%') { s_arith++; long d = arith_factor(); v = d ? v % d : 0; }
        else break;
    }
    return v;
}
static long arith_expr(void)
{
    long v = arith_term();
    for (;;) {
        arith_skip();
        char op = *s_arith;
        if      (op == '+') { s_arith++; v += arith_term(); }
        else if (op == '-') { s_arith++; v -= arith_term(); }
        else break;
    }
    return v;
}
static long eval_arith(const char *e) { s_arith = e; return arith_expr(); }

/* Append the $-expansion at `p` (points at '$') into out[*o]. Returns new p. */
static const char *expand_dollar(const char *p, char *out, int *o, int osz)
{
    p++;                                    /* skip '$' */
    if (*p == '(') {
        if (p[1] == '(') {                  /* $((expr)) — arithmetic */
            p += 2;
            const char *e = p; int d = 0;
            while (*p) { if (*p == '(') d++; else if (*p == ')') { if (d == 0) break; d--; } p++; }
            char expr[160]; int el = (int)(p - e); if (el > 159) el = 159;
            memcpy(expr, e, el); expr[el] = '\0';
            if (*p == ')') p++;
            if (*p == ')') p++;
            char b[24]; int bn = snprintf(b, sizeof(b), "%ld", eval_arith(expr));
            for (int i = 0; i < bn && *o < osz - 1; i++) out[(*o)++] = b[i];
            return p;
        }
        p++;                                /* $(cmd) — command substitution */
        const char *e = p; int d = 1;
        while (*p) { if (*p == '(') d++; else if (*p == ')') { if (--d == 0) break; } p++; }
        char cmd[256]; int cl = (int)(p - e); if (cl > 255) cl = 255;
        memcpy(cmd, e, cl); cmd[cl] = '\0';
        if (*p == ')') p++;
        char cap[256];
        cmd_capture(cmd, cap, sizeof(cap));
        for (const char *c = cap; *c && *o < osz - 1; c++) out[(*o)++] = (*c == '\n') ? ' ' : *c;
        return p;
    }
    if (*p == '?') {
        p++;
        char b[12];
        int n = snprintf(b, sizeof(b), "%d", s_status);
        for (int i = 0; i < n && *o < osz - 1; i++) out[(*o)++] = b[i];
        return p;
    }
    int braced = (*p == '{');
    if (braced) p++;
    char name[SH_VARNAME];
    int  n = 0;
    while (is_name_char(*p) && n < SH_VARNAME - 1) name[n++] = *p++;
    name[n] = '\0';
    if (braced && *p == '}') p++;
    const char *v = var_get(name);
    while (*v && *o < osz - 1) out[(*o)++] = *v++;
    return p;
}

/* Shell glob: * (any run), ? (one char), [..]/[!..] class. Recursive. */
static int sh_globmatch(const char *p, const char *s)
{
    while (*p) {
        if (*p == '*') {
            p++;
            if (!*p) return 1;
            for (; *s; s++) if (sh_globmatch(p, s)) return 1;
            return sh_globmatch(p, s);
        } else if (*p == '?') {
            if (!*s) return 0;
            p++; s++;
        } else if (*p == '[') {
            const char *q = p + 1;
            int neg = (*q == '!' || *q == '^');
            if (neg) q++;
            int hit = 0;
            while (*q && *q != ']') {
                if (q[1] == '-' && q[2] && q[2] != ']') {
                    if ((unsigned char)*s >= (unsigned char)q[0] &&
                        (unsigned char)*s <= (unsigned char)q[2]) hit = 1;
                    q += 3;
                } else { if (*s == *q) hit = 1; q++; }
            }
            if (*q == ']') q++;
            if (!*s || hit == neg) return 0;
            p = q; s++;
        } else {
            if (*p != *s) return 0;
            p++; s++;
        }
    }
    return *s == '\0';
}

/* Expand a glob token (last path component only) against the filesystem. Matches
 * are written NUL-separated into `out`; argv[] entries point into it. Returns the
 * match count (0 = no match → caller keeps the literal), *written = bytes used. */
static int glob_expand(const char *pat, char *out, int outsz, char **argv, int maxv, int *written)
{
    const char *slash = strrchr(pat, '/');
    const char *base;
    char dir[CWD_MAX], prefix[CWD_MAX];

    if (slash) {
        int dl = (int)(slash - pat);
        if (dl + 1 >= (int)sizeof(prefix)) return 0;
        memcpy(prefix, pat, dl + 1); prefix[dl + 1] = '\0';   /* includes the '/' */
        base = slash + 1;
        if (dl == 0) sh_strlcpy(dir, "/", sizeof(dir));
        else { char dp[CWD_MAX]; memcpy(dp, pat, dl); dp[dl] = '\0'; resolve_path(dp, dir, sizeof(dir)); }
    } else {
        prefix[0] = '\0';
        base = pat;
        sh_strlcpy(dir, s_cwd, sizeof(dir));
    }

    DIR *d = opendir(dir);
    if (!d) return 0;
    int count = 0, o = 0;
    struct dirent *e;
    while ((e = readdir(d)) != NULL && count < maxv) {
        if (e->d_name[0] == '.' && base[0] != '.') continue;   /* glob skips dotfiles */
        if (!sh_globmatch(base, e->d_name)) continue;
        char *start = &out[o];
        int n = snprintf(&out[o], outsz - o, "%s%s", prefix, e->d_name);
        if (n < 0 || o + n + 1 > outsz) break;
        o += n + 1;
        argv[count++] = start;
    }
    closedir(d);
    *written = o;
    return count;
}

/* Expand quotes + $vars + globs while splitting `in` into argv (tokens written
 * to `scratch`). '...' is literal; "..." expands but doesn't split; an unquoted
 * token with * ? [ is matched against the filesystem. Returns argc. */
static int expand_tokenize(const char *in, char *scratch, int ssz, char **argv, int maxargv)
{
    int argc = 0, o = 0;
    const char *p = in;
    while (*p) {
        while (*p == ' ' || *p == '\t') p++;
        if (!*p || argc >= maxargv) break;
        int tok_start = o;
        int has_glob = 0;
        while (*p && *p != ' ' && *p != '\t') {
            char c = *p;
            if (c == '\'') {
                p++;
                while (*p && *p != '\'') { if (o < ssz - 1) scratch[o++] = *p; p++; }
                if (*p == '\'') p++;
            } else if (c == '"') {
                p++;
                while (*p && *p != '"') {
                    if (*p == '$') p = expand_dollar(p, scratch, &o, ssz);
                    else { if (o < ssz - 1) scratch[o++] = *p; p++; }
                }
                if (*p == '"') p++;
            } else if (c == '$') {
                p = expand_dollar(p, scratch, &o, ssz);
            } else if (c == '\\' && p[1]) {
                if (o < ssz - 1) scratch[o++] = p[1];
                p += 2;
            } else {
                if (c == '*' || c == '?' || c == '[') has_glob = 1;
                if (o < ssz - 1) scratch[o++] = c;
                p++;
            }
        }
        if (o < ssz) scratch[o++] = '\0';

        char *tok = &scratch[tok_start];
        if (has_glob && argc < maxargv) {
            int written = 0;
            int n = glob_expand(tok, &scratch[o], ssz - o, &argv[argc], maxargv - argc, &written);
            if (n > 0) { argc += n; o += written; }
            else argv[argc++] = tok;       /* no match → literal (nullglob off) */
        } else {
            argv[argc++] = tok;
        }
    }
    return argc;
}

/* Expand the RHS of an assignment into a single value (no word splitting). */
static void expand_value(const char *p, char *out, int osz)
{
    int o = 0;
    while (*p) {
        char c = *p;
        if (c == '\'') {
            p++;
            while (*p && *p != '\'') { if (o < osz - 1) out[o++] = *p; p++; }
            if (*p == '\'') p++;
        } else if (c == '"') {
            p++;
            while (*p && *p != '"') {
                if (*p == '$') p = expand_dollar(p, out, &o, osz);
                else { if (o < osz - 1) out[o++] = *p; p++; }
            }
            if (*p == '"') p++;
        } else if (c == '$') {
            p = expand_dollar(p, out, &o, osz);
        } else if (c == '\\' && p[1]) {
            if (o < osz - 1) out[o++] = p[1];
            p += 2;
        } else {
            if (o < osz - 1) out[o++] = c;
            p++;
        }
    }
    out[o] = '\0';
}

/* If `stmt` begins `NAME=`, split it (NUL the '='), point *val at the RHS, and
 * return 1. Mutates `stmt`. */
static int assign_split(char *stmt, char **val)
{
    char *q = stmt;
    if (!((*q >= 'a' && *q <= 'z') || (*q >= 'A' && *q <= 'Z') || *q == '_')) return 0;
    while (is_name_char(*q)) q++;
    if (*q != '=') return 0;
    *q = '\0';
    *val = q + 1;
    return 1;
}

/* ----- test / [ builtin -------------------------------------------------- */

static int cmd_test(int argc, char **argv)
{
    int n = argc;
    if (strcmp(argv[0], "[") == 0) {
        if (n < 2 || strcmp(argv[n - 1], "]") != 0) return 2;   /* missing ] */
        n--;
    }
    int   c = n - 1;          /* operand count */
    char **a = argv + 1;
    if (c == 0) return 1;
    if (c == 1) return a[0][0] ? 0 : 1;
    if (c == 2) {
        if (strcmp(a[0], "-z") == 0) return a[1][0] ? 1 : 0;
        if (strcmp(a[0], "-n") == 0) return a[1][0] ? 0 : 1;
        if (strcmp(a[0], "-e") == 0 || strcmp(a[0], "-f") == 0 || strcmp(a[0], "-d") == 0) {
            char path[CWD_MAX];
            resolve_path(a[1], path, sizeof(path));
            struct stat st;
            if (stat(path, &st) != 0) return 1;
            if (a[0][1] == 'f') return S_ISREG(st.st_mode) ? 0 : 1;
            if (a[0][1] == 'd') return S_ISDIR(st.st_mode) ? 0 : 1;
            return 0;   /* -e */
        }
        return 1;
    }
    if (c == 3) {
        const char *op = a[1];
        if (strcmp(op, "=")  == 0) return strcmp(a[0], a[2]) == 0 ? 0 : 1;
        if (strcmp(op, "!=") == 0) return strcmp(a[0], a[2]) != 0 ? 0 : 1;
        int x = atoi(a[0]), y = atoi(a[2]);
        if (strcmp(op, "-eq") == 0) return x == y ? 0 : 1;
        if (strcmp(op, "-ne") == 0) return x != y ? 0 : 1;
        if (strcmp(op, "-lt") == 0) return x <  y ? 0 : 1;
        if (strcmp(op, "-le") == 0) return x <= y ? 0 : 1;
        if (strcmp(op, "-gt") == 0) return x >  y ? 0 : 1;
        if (strcmp(op, "-ge") == 0) return x >= y ? 0 : 1;
        return 1;
    }
    return 1;
}

/* ----- command dispatch + sequencing ------------------------------------- */

static int cmd_source(int argc, char **argv);   /* defined after run_script */

static int is_builtin(const char *cmd)
{
    static const char *const b[] = { "cd", "pwd", "echo", "run", "exit", "help",
                                     "test", "[", "set", "source", ".", "break", "continue" };
    for (unsigned i = 0; i < sizeof(b) / sizeof(b[0]); i++)
        if (strcmp(cmd, b[i]) == 0) return 1;
    return 0;
}

/* Builtins only — output goes through sh_emit (redirectable). */
static int dispatch(int argc, char **argv)
{
    const char *cmd = argv[0];
    if      (strcmp(cmd, "cd")   == 0) return cmd_cd(argc, argv);
    else if (strcmp(cmd, "pwd")  == 0) { sh_emitln(s_cwd); return 0; }
    else if (strcmp(cmd, "echo") == 0) { cmd_echo(argc, argv); return 0; }
    else if (strcmp(cmd, "run")  == 0) { cmd_run(argc, argv); return 0; }
    else if (strcmp(cmd, "exit") == 0) duneos_exit(0);
    else if (strcmp(cmd, "help") == 0) { cmd_help(); return 0; }
    else if (strcmp(cmd, "source") == 0 || strcmp(cmd, ".") == 0) return cmd_source(argc, argv);
    else if (strcmp(cmd, "break")    == 0) { s_loop_ctl = LOOP_BREAK;    return 0; }
    else if (strcmp(cmd, "continue") == 0) { s_loop_ctl = LOOP_CONTINUE; return 0; }
    else if (strcmp(cmd, "test") == 0 || strcmp(cmd, "[") == 0) return cmd_test(argc, argv);
    else if (strcmp(cmd, "set")  == 0) {
        for (int i = 0; i < s_nvars; i++) {
            char b[SH_VARNAME + SH_VARVAL + 2];
            snprintf(b, sizeof(b), "%s=%s", s_vars[i].name, s_vars[i].val);
            sh_emitln(b);
        }
        return 0;
    }
    return 0;
}

/* ----- pipelines & redirection (ADR 034, Model A — serialized via tmpfs) --- */

/* Strip <, >, >> and their filenames out of `in`, leaving the command in
 * `cmd_out`; record the in/out filenames + append flag. Redir filenames are not
 * variable-expanded in this pass (literal). */
static void parse_redirs(const char *in, char *cmd_out, int cmdsz,
                         char *infile, char *outfile, int *append)
{
    int co = 0;
    infile[0] = outfile[0] = '\0';
    *append = 0;
    const char *p = in;
    while (*p) {
        if (*p == '\'' || *p == '"') {
            char q = *p;
            if (co < cmdsz - 1) cmd_out[co++] = *p;
            p++;
            while (*p && *p != q) { if (co < cmdsz - 1) cmd_out[co++] = *p; p++; }
            if (*p) { if (co < cmdsz - 1) cmd_out[co++] = *p; p++; }
        } else if (*p == '<' || *p == '>') {
            char which = *p; p++;
            int app = 0;
            if (which == '>' && *p == '>') { app = 1; p++; }
            while (*p == ' ' || *p == '\t') p++;
            char fn[96]; int fo = 0;
            while (*p && *p != ' ' && *p != '\t' && *p != '<' && *p != '>') {
                if (fo < (int)sizeof(fn) - 1) fn[fo++] = *p;
                p++;
            }
            fn[fo] = '\0';
            if (which == '<') sh_strlcpy(infile, fn, 96);
            else { sh_strlcpy(outfile, fn, 96); *append = app; }
        } else {
            if (co < cmdsz - 1) cmd_out[co++] = *p;
            p++;
        }
    }
    cmd_out[co] = '\0';
}

/* Run one pipeline stage. `in_file` (a pipe scratch or `<` target) is appended
 * as the trailing argument for tools that read files; `out_fd` is where stdout
 * goes (>=0 file/scratch, -1 terminal). Returns the command's exit status. */
static int run_stage(char *stage, const char *in_file, int out_fd)
{
    char  scratch[512];
    char *argv[24];
    int   argc = expand_tokenize(stage, scratch, sizeof(scratch), argv, 22);
    if (argc == 0) return s_status;
    if (in_file && in_file[0] && argc < 23) argv[argc++] = (char *)in_file;

    const char *cmd = argv[0];
    if (is_builtin(cmd)) {
        int prev = s_emit_fd;
        s_emit_fd = out_fd;
        int st = dispatch(argc, argv);
        s_emit_fd = prev;
        return st;
    }
    int r;
    if (out_fd >= 0) { int fd = out_fd; r = try_run_bin(cmd, argc, argv, file_sink, &fd); }
    else             r = try_run_bin(cmd, argc, argv, stream_sink, NULL);
    if (r == -1) {
        char msg[64];
        snprintf(msg, sizeof(msg), "%s: command not found", cmd);
        sh_outln(msg);
        return 127;
    }
    return r;
}

/* Open a redirection/scratch file, forcing the fd to >= 3.
 *
 * A captured run leaves fd 1 closed (the loader reopens /dev/shellpipe there each
 * time), so a target landing on fd 0/1/2 is catastrophic: the loader's next
 * close(1)+open(shellpipe) would put shellpipe AT our target fd, and file_sink
 * writing to it loops back through shellpipe → infinite recursion → instant
 * stack overflow → hard reboot. fcntl(F_DUPFD) can't move it (tmpfs has no
 * fcntl), so instead we occupy fds 0/1/2 with /dev/null first, which forces the
 * real target to the lowest free fd >= 3, then release the holders. */
static int open_target(const char *name, int append)
{
    int hold[3], nh = 0;
    while (nh < 3) {
        int h = open("/dev/null", O_WRONLY);
        if (h < 0) break;
        if (h >= 3) { close(h); break; }   /* 0/1/2 already occupied */
        hold[nh++] = h;
    }

    char path[CWD_MAX];
    resolve_path(name, path, sizeof(path));
    int fl = O_WRONLY | O_CREAT | (append ? O_APPEND : O_TRUNC);
    int fd = open(path, fl, 0644);

    for (int i = 0; i < nh; i++) close(hold[i]);
    return fd;
}

/* Split a statement on top-level | into stages, run them serially feeding each
 * stage's output file to the next as input. Sets $? to the last stage's status. */
static void run_pipeline(char *stmt)
{
    char *stages[8];
    int   ns = 0;
    char *p = stmt;
    stages[ns++] = p;
    while (*p && ns < 8) {
        if (*p == '\'' || *p == '"') { char q = *p++; while (*p && *p != q) p++; if (*p) p++; continue; }
        if (*p == '|' && p[1] != '|') { *p++ = '\0'; stages[ns++] = p; continue; }
        p++;
    }

    const char *scratch_a = "/tmp/.pipe0", *scratch_b = "/tmp/.pipe1";
    char in_file[64] = "";
    int  status = 0;

    for (int i = 0; i < ns; i++) {
        int last = (i == ns - 1);
        char cmd[256], rin[96], rout[96]; int app;
        parse_redirs(stages[i], cmd, sizeof(cmd), rin, rout, &app);
        /* Redirect filenames carry $vars/quotes like any word — expand them. */
        char rin_x[96], rout_x[96];
        expand_value(rin, rin_x, sizeof(rin_x));   sh_strlcpy(rin, rin_x, sizeof(rin));
        expand_value(rout, rout_x, sizeof(rout_x)); sh_strlcpy(rout, rout_x, sizeof(rout));

        const char *stdin_file = rin[0] ? rin : (in_file[0] ? in_file : NULL);

        int  out_fd = -1;
        const char *out_name = NULL;
        if (rout[0])      out_name = rout;                       /* explicit > / >> */
        else if (!last)   out_name = (i & 1) ? scratch_b : scratch_a;
        if (out_name) {
            out_fd = open_target(out_name, rout[0] ? app : 0);
            if (out_fd < 0) { sh_outln("shell: cannot open redirection target"); status = 1; break; }
        }

        status = run_stage(cmd, stdin_file, out_fd);
        if (out_fd >= 0) close(out_fd);

        /* Next stage reads this stage's scratch (only when we piped, not on `>`). */
        if (!last && !rout[0]) sh_strlcpy(in_file, out_name, sizeof(in_file));
        else in_file[0] = '\0';
    }

    unlink("/tmp/.pipe0");
    unlink("/tmp/.pipe1");
    s_status = status;
}

/* Run one statement (already ;/&&/|| separated): assignment or a pipeline. */
static void run_statement(char *stmt)
{
    while (*stmt == ' ' || *stmt == '\t') stmt++;
    char *end = stmt + strlen(stmt);
    while (end > stmt && (end[-1] == ' ' || end[-1] == '\t')) *--end = '\0';
    if (!*stmt) return;

    char *val;
    if (assign_split(stmt, &val)) {
        char v[SH_VARVAL];
        expand_value(val, v, sizeof(v));
        var_set(stmt, v);
        s_status = 0;
        return;
    }
    run_pipeline(stmt);
}

/* Run one "segment" (no ';'/newline): split on top-level && / || and run each
 * statement with short-circuit. */
static void run_andor(char *seg)
{
    char *p = seg;
    int   run = 1;
    while (*p) {
        char *start = p;
        while (*p) {
            if (*p == '\'') { p++; while (*p && *p != '\'') p++; if (*p) p++; continue; }
            if (*p == '"')  { p++; while (*p && *p != '"')  p++; if (*p) p++; continue; }
            if ((p[0] == '&' && p[1] == '&') || (p[0] == '|' && p[1] == '|')) break;
            p++;
        }
        char  sep0 = p[0], sep1 = sep0 ? p[1] : 0;
        char *sepp = p, saved = *sepp;
        *sepp = '\0';
        if (run) run_statement(start);
        *sepp = saved;
        if      (sep0 == '&' && sep1 == '&') { run = (s_status == 0); p = sepp + 2; }
        else if (sep0 == '|' && sep1 == '|') { run = (s_status != 0); p = sepp + 2; }
        else break;
    }
}

/* ----- control flow: if / for / while (ADR 037) -------------------------- */
/* Operates on a list of `;`/newline-separated statements; control keywords lead
 * a statement (then/do/else inline bodies are split off during the scan). */

typedef struct { char **v; int n; } stmts_t;

/* If `s` begins with word `k`, return the rest (past following spaces), else NULL. */
static const char *kw(const char *s, const char *k)
{
    int l = (int)strlen(k);
    if (strncmp(s, k, l) == 0 && (s[l] == '\0' || s[l] == ' ' || s[l] == '\t')) {
        const char *r = s + l;
        while (*r == ' ' || *r == '\t') r++;
        return r;
    }
    return NULL;
}

static int is_term(const char *s)
{
    return kw(s, "fi") || kw(s, "done") || kw(s, "else") || kw(s, "elif") != NULL;
}

/* Run a statement string without mutating it (loop bodies re-run). */
static void run_stmt_str(const char *s)
{
    char tmp[256];
    sh_strlcpy(tmp, s, sizeof(tmp));
    run_andor(tmp);
}

/* Index of the fi/done that closes a block opened just before `start`. */
static int find_block_end(stmts_t *S, int start)
{
    int depth = 1;
    for (int j = start; j < S->n; j++) {
        const char *s = S->v[j];
        if (kw(s, "if") || kw(s, "for") || kw(s, "while")) depth++;
        else if (kw(s, "fi") || kw(s, "done")) { if (--depth == 0) return j; }
    }
    return S->n;
}

static int ctrl_exec(stmts_t *S, int i, int run);

/* Once break/continue fires, statements are skipped (run gated off) but still
 * scanned so i advances correctly past the rest of the block; the enclosing loop
 * consumes s_loop_ctl. */
static int ctrl_seq_until(stmts_t *S, int i, int run)   /* until a terminator */
{
    while (i < S->n && !is_term(S->v[i])) i = ctrl_exec(S, i, run && !s_loop_ctl);
    return i;
}
static int ctrl_run_range(stmts_t *S, int i, int end, int run)
{
    while (i < end) i = ctrl_exec(S, i, run && !s_loop_ctl);
    return i;
}

static int ctrl_exec(stmts_t *S, int i, int run)
{
    char *s = S->v[i];
    const char *r;

    if ((r = kw(s, "if"))) {
        int cond = 0;
        if (run) { run_stmt_str(r); cond = (s_status == 0); }
        i++;
        if (i < S->n && kw(S->v[i], "then")) i++;
        i = ctrl_seq_until(S, i, run && cond);
        int taken = cond;
        while (i < S->n && (r = kw(S->v[i], "elif"))) {
            int c2 = 0;
            if (run && !taken) { run_stmt_str(r); c2 = (s_status == 0); }
            i++;
            if (i < S->n && kw(S->v[i], "then")) i++;
            i = ctrl_seq_until(S, i, run && !taken && c2);
            if (c2) taken = 1;
        }
        if (i < S->n && kw(S->v[i], "else")) { i++; i = ctrl_seq_until(S, i, run && !taken); }
        if (i < S->n && kw(S->v[i], "fi")) i++;
        return i;
    }

    if ((r = kw(s, "for"))) {
        char var[SH_VARNAME]; int vi = 0;
        const char *p = r;
        while (is_name_char(*p) && vi < SH_VARNAME - 1) var[vi++] = *p++;
        var[vi] = '\0';
        while (*p == ' ' || *p == '\t') p++;
        const char *words = kw(p, "in");
        i++;
        if (i < S->n && kw(S->v[i], "do")) i++;
        int body = i, end = find_block_end(S, i);
        if (run && var[0] && words) {
            char  wbuf[256]; char *wv[32];
            int   wc = expand_tokenize(words, wbuf, sizeof(wbuf), wv, 32);
            for (int w = 0; w < wc; w++) {
                var_set(var, wv[w]);
                ctrl_run_range(S, body, end, 1);
                if (s_loop_ctl == LOOP_BREAK)    { s_loop_ctl = LOOP_NONE; break; }
                if (s_loop_ctl == LOOP_CONTINUE) { s_loop_ctl = LOOP_NONE; }
            }
        }
        i = end;
        if (i < S->n && kw(S->v[i], "done")) i++;
        return i;
    }

    if ((r = kw(s, "while"))) {
        i++;
        if (i < S->n && kw(S->v[i], "do")) i++;
        int body = i, end = find_block_end(S, i), guard = 0;
        while (run) {
            run_stmt_str(r);
            if (s_status != 0) break;
            ctrl_run_range(S, body, end, 1);
            if (s_loop_ctl == LOOP_BREAK)    { s_loop_ctl = LOOP_NONE; break; }
            if (s_loop_ctl == LOOP_CONTINUE) { s_loop_ctl = LOOP_NONE; }
            if (++guard > 100000) break;          /* runaway guard */
        }
        i = end;
        if (i < S->n && kw(S->v[i], "done")) i++;
        return i;
    }

    if (run) run_stmt_str(s);
    return i + 1;
}

/* Split `buf` into ;/newline statements (quote-aware, comments skipped, inline
 * then/do/else bodies split off) and run them through the control-flow engine. */
static void run_script(char *buf)
{
    char *stmts[192];
    int   n = 0;
    char *p = buf;
    while (*p && n < 190) {
        while (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r' || *p == ';') p++;
        if (!*p) break;
        char *start = p;
        /* A quote does not cross a newline (line-oriented model) — otherwise an
         * unmatched ' or " (e.g. an apostrophe in a # comment) would swallow the
         * rest of the script up to the next matching quote. */
        while (*p) {
            if (*p == '\'') { p++; while (*p && *p != '\'' && *p != '\n') p++; if (*p == '\'') p++; continue; }
            if (*p == '"')  { p++; while (*p && *p != '"'  && *p != '\n') p++; if (*p == '"')  p++; continue; }
            if (*p == ';' || *p == '\n') break;
            p++;
        }
        char *sepp = p;
        if (*p) p++;
        *sepp = '\0';
        char *e = sepp;
        while (e > start && (e[-1] == ' ' || e[-1] == '\t' || e[-1] == '\r')) *--e = '\0';
        if (*start == '\0' || *start == '#') continue;

        /* Split an inline body off then/do/else so bodies are their own stmts. */
        const char *rest;
        int klen = kw(start, "then") ? 4 : kw(start, "do") ? 2 : kw(start, "else") ? 4 : 0;
        if (klen && (rest = start + klen) && start[klen] != '\0') {
            char *body = start + klen;
            while (*body == ' ' || *body == '\t') body++;
            start[klen] = '\0';
            stmts[n++] = start;
            if (*body && n < 190) stmts[n++] = body;
            continue;
        }
        stmts[n++] = start;
    }
    stmts_t S = { stmts, n };
    int i = 0;
    while (i < S.n) i = ctrl_exec(&S, i, 1);
    s_loop_ctl = LOOP_NONE;   /* don't leak a stray break/continue out of the script */
}

static void exec_line(char *line) { run_script(line); }

/* source / . — run a script file through the interpreter. */
static int cmd_source(int argc, char **argv)
{
    if (argc < 2) { sh_outln("usage: source FILE"); return 1; }
    char path[CWD_MAX];
    resolve_path(argv[1], path, sizeof(path));
    int fd = open(path, O_RDONLY);
    if (fd < 0) { char m[128]; snprintf(m, sizeof(m), "source: %s: %s", argv[1], strerror(errno)); sh_outln(m); return 1; }

    /* Stack buffer, not malloc: read() validates the destination against the
     * app's bounds, and the shell's malloc heap can fall outside them (EFAULT,
     * silently truncating to 0). Kept small — each script command executes at
     * DOUBLE nesting (the `source` command's chain + the command's own), so this
     * buffer plus a captured tool plus the VFS write chain must all fit the
     * shell stack. A script larger than this is truncated (warned). */
    char buf[3072];
    int  over = 0;
    int  tot = 0, k;
    while ((k = (int)read(fd, buf + tot, (int)sizeof(buf) - 1 - tot)) > 0) {
        tot += k;
        if (tot >= (int)sizeof(buf) - 1) { over = 1; break; }
    }
    close(fd);
    if (tot < 0) tot = 0;
    buf[tot] = '\0';
    if (over) sh_outln("source: script truncated (max 3071 bytes)");
    run_script(buf);
    return s_status;
}
