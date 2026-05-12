#include <unistd.h>
#include <string.h>
#include <stdio.h>
#include <stdarg.h>

extern int duneos_supervisor_restart_by_name(const char *name);

static void out(const char *s) { write(STDOUT_FILENO, s, strlen(s)); }
static void outf(const char *fmt, ...)
{
    char buf[128]; va_list ap; va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap); va_end(ap); out(buf);
}

#include <duneos/bin_args.h>

void app_main(void)
{
    static char buf[DUNEOS_EXEC_ARGS_BUF_SIZE];
    char *argv[4]; char *cwd;
    int argc = duneos_bin_args(buf, sizeof(buf), &cwd, argv, 4);
    (void)cwd;

    if (argc < 2) {
        out("usage: restart <name>\r\n");
        return;
    }

    if (duneos_supervisor_restart_by_name(argv[1]) != 0)
        outf("restart: '%s' not found\r\n", argv[1]);
    else
        outf("restart: '%s' queued\r\n", argv[1]);
}
