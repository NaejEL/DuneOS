#include <unistd.h>
#include <fcntl.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <errno.h>
#include <stdarg.h>

#include <duneos/gpio_ioctl.h>
#include <duneos/bin_args.h>

static void out(const char *s)          { write(STDOUT_FILENO, s, strlen(s)); }
static void outf(const char *fmt, ...)
{
    char buf[128]; va_list ap; va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap); va_end(ap); out(buf);
}

void app_main(void)
{
    char buf[DUNEOS_EXEC_ARGS_BUF_SIZE];
    char *argv[8]; char *cwd;
    int argc = duneos_bin_args(buf, sizeof(buf), &cwd, argv, 8);
    (void)cwd;

    if (argc < 2) {
        out("usage: gpio info | get <pin> | set <pin> <0|1> | mode <pin> in|out | pull <pin> none|up|down\r\n");
        return;
    }

    int fd = open("/dev/gpiochip0", O_RDWR);
    if (fd < 0) {
        outf("gpio: cannot open /dev/gpiochip0: %s\r\n", strerror(errno));
        return;
    }

    if (strcmp(argv[1], "info") == 0) {
        gpiochip_info_t info;
        if (ioctl(fd, GPIOCHIP_GET_INFO, &info) == 0)
            outf("%s: %u lines\r\n", info.name, (unsigned)info.lines);
        else
            outf("gpio: info failed: %s\r\n", strerror(errno));
    } else if (strcmp(argv[1], "get") == 0) {
        if (argc < 3) { out("usage: gpio get <pin>\r\n"); goto done; }
        int pin = atoi(argv[2]);
        gpio_req_t req = { .line = (uint8_t)pin, .dir = GPIO_DIR_INPUT };
        ioctl(fd, GPIOCHIP_SET_DIR, &req);
        if (ioctl(fd, GPIOCHIP_GET_VALUE, &req) == 0)
            outf("gpio%d = %d\r\n", pin, (int)req.val);
        else
            outf("gpio: get failed: %s\r\n", strerror(errno));
    } else if (strcmp(argv[1], "set") == 0) {
        if (argc < 4) { out("usage: gpio set <pin> <0|1>\r\n"); goto done; }
        int pin = atoi(argv[2]);
        gpio_req_t req = { .line = (uint8_t)pin, .dir = GPIO_DIR_OUTPUT };
        ioctl(fd, GPIOCHIP_SET_DIR, &req);
        req.val = (uint8_t)(atoi(argv[3]) ? 1 : 0);
        if (ioctl(fd, GPIOCHIP_SET_VALUE, &req) == 0)
            outf("gpio%d <= %d\r\n", pin, (int)req.val);
        else
            outf("gpio: set failed: %s\r\n", strerror(errno));
    } else if (strcmp(argv[1], "mode") == 0) {
        if (argc < 4) { out("usage: gpio mode <pin> in|out\r\n"); goto done; }
        int pin = atoi(argv[2]);
        gpio_req_t req = {
            .line = (uint8_t)pin,
            .dir  = (strcmp(argv[3], "out") == 0) ? GPIO_DIR_OUTPUT : GPIO_DIR_INPUT,
        };
        if (ioctl(fd, GPIOCHIP_SET_DIR, &req) == 0)
            outf("gpio%d mode = %s\r\n", pin, argv[3]);
        else
            outf("gpio: mode failed: %s\r\n", strerror(errno));
    } else if (strcmp(argv[1], "pull") == 0) {
        if (argc < 4) { out("usage: gpio pull <pin> none|up|down\r\n"); goto done; }
        int pin = atoi(argv[2]);
        uint8_t pull = (strcmp(argv[3], "up") == 0)   ? GPIO_PULL_UP :
                       (strcmp(argv[3], "down") == 0) ? GPIO_PULL_DOWN : GPIO_PULL_NONE;
        gpio_req_t req = { .line = (uint8_t)pin, .pull = pull };
        if (ioctl(fd, GPIOCHIP_SET_PULL, &req) == 0)
            outf("gpio%d pull = %s\r\n", pin, argv[3]);
        else
            outf("gpio: pull failed: %s\r\n", strerror(errno));
    } else {
        outf("gpio: unknown subcommand '%s'\r\n", argv[1]);
    }

done:
    close(fd);
}
