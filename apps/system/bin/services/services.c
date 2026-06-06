#include <unistd.h>
#include <string.h>
#include <stdio.h>
#include <stdarg.h>

/* Listing buffer size — app slots are RAM-limited (grow-only pool) in the
 * kernel; this just bounds how many we show in one `services` call. */
#define DUNEOS_MAX_RUNNING_APPS  16
#define DUNEOS_APP_NAME_MAX      64
typedef enum {
    DUNEOS_RESTART_NO         = 0,
    DUNEOS_RESTART_ALWAYS     = 1,
    DUNEOS_RESTART_ON_FAILURE = 2,
} duneos_restart_policy_t;
typedef struct {
    char                    name[DUNEOS_APP_NAME_MAX];
    int                     active;
    duneos_restart_policy_t restart_policy;
    unsigned int            restart_count;
} duneos_slot_info_t;
extern int duneos_supervisor_list_slots(duneos_slot_info_t *out, int count);

static void out(const char *s) { write(STDOUT_FILENO, s, strlen(s)); }
static void outn(const char *s) { out(s); out("\r\n"); }
static void outf(const char *fmt, ...)
{
    char buf[128]; va_list ap; va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap); va_end(ap); out(buf);
}

static const char *policy_str(duneos_restart_policy_t p)
{
    if (p == DUNEOS_RESTART_ALWAYS)     return "always";
    if (p == DUNEOS_RESTART_ON_FAILURE) return "on-failure";
    return "no";
}

void app_main(void)
{
    duneos_slot_info_t slots[DUNEOS_MAX_RUNNING_APPS];
    int n = duneos_supervisor_list_slots(slots, DUNEOS_MAX_RUNNING_APPS);
    outn("NAME                STATE   POLICY      RESTARTS");
    outn("--------------------------------------------------");
    int found = 0;
    for (int i = 0; i < n; i++) {
        if (!slots[i].active) continue;
        outf("%-20s%-8s%-12s%u\r\n", slots[i].name, "active",
             policy_str(slots[i].restart_policy), slots[i].restart_count);
        found++;
    }
    if (!found) outn("(no running services)");
}
