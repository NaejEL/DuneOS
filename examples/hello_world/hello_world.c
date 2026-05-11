#include <unistd.h>
#include <string.h>

extern void duneos_exit(int code);

void app_main(void)
{
    const char msg[] = "Hello from DuneOS!\r\n";
    write(STDOUT_FILENO, msg, strlen(msg));
    duneos_exit(0);
}
