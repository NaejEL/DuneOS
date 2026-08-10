#include <unistd.h>
#include <fcntl.h>
#include <string.h>
#include <stdio.h>
#include <errno.h>

extern int usleep(unsigned int usec);

void app_main(void)
{
    int fd = open("/dev/klog", O_RDONLY);
    if (fd < 0) {
        char msg[64];
        int  n = snprintf(msg, sizeof(msg),
                          "klog: cannot open /dev/klog (errno=%d)\r\n", errno);
        if (n > 0) write(STDOUT_FILENO, msg, (size_t)n);
        return;
    }

    /* Emit one whole line per write, then pause ~2 ms so the USB-CDC FIFO
     * drains. Bursting the 16 KiB ring byte-by-byte overruns TinyUSB's TX and
     * silently drops the tail — which hid the boot's app-launch logs. */
    char out[160];
    int  pos = 0;
    char buf[128]; ssize_t n;
    while ((n = read(fd, buf, sizeof(buf))) > 0) {
        for (ssize_t i = 0; i < n; i++) {
            char c = buf[i];
            if (c == '\n' || pos >= (int)sizeof(out) - 3) {
                out[pos++] = '\r'; out[pos++] = '\n';
                write(STDOUT_FILENO, out, (size_t)pos);
                pos = 0;
                usleep(2000);
                if (c != '\n') out[pos++] = c;
            } else {
                out[pos++] = c;
            }
        }
    }
    if (pos > 0) write(STDOUT_FILENO, out, (size_t)pos);
    close(fd);
}
