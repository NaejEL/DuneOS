#define SHELL_BANNER "DuneOS uart_shell"
/* Drain stale RX bytes from boot noise before starting the shell.
 * Safe because hal_uart_read uses a 100 ms timeout, not portMAX_DELAY. */
#define SHELL_FLUSH_ON_START
#include "../shell_core/shell_core.c"

void app_main(void)
{
    int fd = open("/dev/uart0", O_RDWR);
    if (fd < 0) {
        duneos_exit(1);
        return;
    }
    shell_run(fd, fd);
    close(fd);
    duneos_exit(0);
}
