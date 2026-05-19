#include <unistd.h>
#include <fcntl.h>
#include <string.h>

void app_main(void)
{
    int fd = open("/dev/klog", O_RDONLY);
    if (fd < 0) {
        const char msg[] = "klog: cannot open /dev/klog\r\n";
        write(STDOUT_FILENO, msg, sizeof(msg) - 1);
        return;
    }

    char buf[128]; ssize_t n;
    while ((n = read(fd, buf, sizeof(buf))) > 0) {
        for (ssize_t i = 0; i < n; i++) {
            if (buf[i] == '\n') write(STDOUT_FILENO, "\r\n", 2);
            else write(STDOUT_FILENO, &buf[i], 1);
        }
    }
    close(fd);
}
