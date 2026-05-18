/*
 * libdune — time wrappers (ABI v3).
 *
 * Dispatches to the kernel's time implementations so that app-visible time
 * calls share the same monotonic clock and RTC as the kernel.  nanosleep is
 * emulated by the kernel via usleep (ESP-IDF has no native nanosleep).
 */

#include <duneos/api.h>
#include <time.h>
#include <sys/time.h>
#include <unistd.h>

extern const duneos_api_t *__duneos_api_ptr;

int clock_gettime(clockid_t id, struct timespec *tp)
{
    return __duneos_api_ptr->time.clock_gettime(id, tp);
}

int gettimeofday(struct timeval *tv, void *tz)
{
    return __duneos_api_ptr->time.gettimeofday(tv, tz);
}

int usleep(useconds_t usec)
{
    return __duneos_api_ptr->time.usleep((unsigned)usec);
}

unsigned int sleep(unsigned int sec)
{
    return __duneos_api_ptr->time.sleep(sec);
}

int nanosleep(const struct timespec *req, struct timespec *rem)
{
    return __duneos_api_ptr->time.nanosleep(req, rem);
}
