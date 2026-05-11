#include <unistd.h>
#include <fcntl.h>
#include <string.h>

extern void duneos_exit(int code);

void app_main(void)
{
    int fd = open("/dev/uart0", O_RDWR);
    if (fd < 0) {
        /* Fall back to stdin/stdout if /dev/uart0 not available */
        fd = STDIN_FILENO;
    }

    const char banner[] = "uart_echo — press Ctrl-C to exit\r\n";
    write(fd > 1 ? fd : STDOUT_FILENO, banner, strlen(banner));

    char buf[64];
    ssize_t n;
    while ((n = read(fd, buf, sizeof(buf))) > 0) {
        /* Ctrl-C = 0x03 */
        for (ssize_t i = 0; i < n; i++) {
            if (buf[i] == '\x03') {
                if (fd > 1) close(fd);
                duneos_exit(0);
            }
        }
        write(fd > 1 ? fd : STDOUT_FILENO, buf, n);
    }

    if (fd > 1) close(fd);
    duneos_exit(0);
}
