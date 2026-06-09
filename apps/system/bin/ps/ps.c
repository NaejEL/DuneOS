/*
 * ps — per-app memory view: what each running app RESERVES vs what it really
 * USES. The gap (reserved − used) is RAM wasted by an oversized manifest
 * stack_size / heap_size — exactly what to reclaim on a RAM-tight board.
 */

#include <unistd.h>
#include <stdio.h>

#include "duneos/libdune.h"

#define MAX_PROCS 16

static void put(const char *s, int n) { write(STDOUT_FILENO, s, n); }

void app_main(void)
{
    duneos_proc_mem_t p[MAX_PROCS];
    int n = duneos_supervisor_list_mem(p, MAX_PROCS);

    char b[160];
    int  len;

    len = snprintf(b, sizeof(b),
        "NAME             DATA   STACK use/rsv     HEAP use/rsv\r\n");
    put(b, len);

    uint32_t t_data = 0, t_su = 0, t_sr = 0, t_hu = 0, t_hr = 0;
    for (int i = 0; i < n; i++) {
        len = snprintf(b, sizeof(b),
            "%-15s %6u   %6u/%-6u   %6u/%-6u\r\n",
            p[i].name,
            (unsigned)p[i].data_size,
            (unsigned)p[i].stack_used, (unsigned)p[i].stack_size,
            (unsigned)p[i].heap_used,  (unsigned)p[i].heap_size);
        put(b, len);
        t_data += p[i].data_size;
        t_su += p[i].stack_used; t_sr += p[i].stack_size;
        t_hu += p[i].heap_used;  t_hr += p[i].heap_size;
    }

    len = snprintf(b, sizeof(b),
        "----\r\n%-15s %6u   %6u/%-6u   %6u/%-6u\r\n",
        "total", (unsigned)t_data,
        (unsigned)t_su, (unsigned)t_sr,
        (unsigned)t_hu, (unsigned)t_hr);
    put(b, len);
}
