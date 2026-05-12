#include <unistd.h>
#include <fcntl.h>
#include <string.h>
#include <stdio.h>
#include <sys/stat.h>
#include <errno.h>
#include <stdarg.h>

extern int usleep(unsigned int useconds);

#include <duneos/bin_args.h>

static void out(const char *s) { write(STDOUT_FILENO, s, strlen(s)); }
static void outn(const char *s) { out(s); out("\r\n"); }
static void outf(const char *fmt, ...)
{
    char buf[128]; va_list ap; va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap); va_end(ap); out(buf);
}

void app_main(void)
{
    static char buf[DUNEOS_EXEC_ARGS_BUF_SIZE];
    char *argv[5]; char *cwd;
    int argc = duneos_bin_args(buf, sizeof(buf), &cwd, argv, 5);

    int follow = 0;
    const char *path_arg = NULL;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-f") == 0) follow = 1;
        else path_arg = argv[i];
    }
    if (!path_arg) { out("usage: tail [-f] <file>\r\n"); return; }

    char path[256];
    duneos_bin_resolve(path_arg, cwd, path, sizeof(path));

    int fd = open(path, O_RDONLY);
    if (fd < 0) { outf("tail: %s: %s\r\n", path, strerror(errno)); return; }

    if (!follow) {
        struct stat st;
        if (fstat(fd, &st) == 0 && st.st_size > 512)
            lseek(fd, st.st_size - 512, SEEK_SET);
        char rbuf[128]; ssize_t n;
        while ((n = read(fd, rbuf, sizeof(rbuf))) > 0)
            write(STDOUT_FILENO, rbuf, n);
        out("\r\n");
    } else {
        lseek(fd, 0, SEEK_END);

        int flags = fcntl(STDIN_FILENO, F_GETFL, 0);
        fcntl(STDIN_FILENO, F_SETFL, flags | O_NONBLOCK);
        fcntl(fd, F_SETFL, O_NONBLOCK);

        outn("[following -- Ctrl-C to stop]");
        char rbuf[128];
        while (1) {
            ssize_t n = read(fd, rbuf, sizeof(rbuf));
            if (n > 0) write(STDOUT_FILENO, rbuf, (size_t)n);
            char c;
            if (read(STDIN_FILENO, &c, 1) > 0 && c == '\x03') {
                out("^C\r\n");
                break;
            }
            if (n <= 0) usleep(100000);
        }
        fcntl(STDIN_FILENO, F_SETFL, flags);
    }
    close(fd);
}
