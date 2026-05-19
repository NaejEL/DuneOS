#include <unistd.h>
#include <stdio.h>

extern int esp_get_free_heap_size(void);

void app_main(void)
{
    char buf[64];
    int n = snprintf(buf, sizeof(buf), "free heap: %d bytes\r\n",
                     esp_get_free_heap_size());
    write(STDOUT_FILENO, buf, n);
}
