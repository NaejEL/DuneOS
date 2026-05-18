/*
 * usb_shell.c — DuneOS interactive shell over USB CDC.
 *
 * On the host: minicom -D /dev/ttyACM0 -b 115200
 *         or:  screen /dev/ttyACM0 115200
 *
 * Opens /dev/ttyUSB0 (drv_usb_cdc.c — TinyUSB CDC-ACM ring-buffer VFS).
 * Reads block with 100ms timeout; idle reprints prompt after ~5s.
 */

#define SHELL_BANNER "DuneOS usb_shell"
#define SHELL_FLUSH_ON_START
#include "../shell_core/shell_core.c"

void app_main(void)
{
    int fd = open("/dev/ttyUSB0", O_RDWR);
    if (fd < 0) {
        duneos_exit(1);
        return;
    }
    shell_run(fd, fd);
    close(fd);
    duneos_exit(0);
}
