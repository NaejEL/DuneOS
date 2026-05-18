/*
 * libdune — POSIX thread & semaphore wrappers (ABI v3).
 *
 * Thin dispatch to the kernel's pthread / sem implementations (ESP-IDF over
 * FreeRTOS).  The concrete struct types are defined by ESP-IDF, not by the
 * libc, so they are identical regardless of Newlib vs. PicoLibc.
 */

#include <duneos/api.h>   /* sem_t defined here via __has_include */
#include <pthread.h>

extern const duneos_api_t *__duneos_api_ptr;

/* -------------------------------------------------------------------------
 * pthreads
 * ---------------------------------------------------------------------- */

int pthread_create(pthread_t *t, const pthread_attr_t *attr,
                   void *(*fn)(void *), void *arg)
{
    return __duneos_api_ptr->thread.pthread_create(t, attr, fn, arg);
}

int pthread_join(pthread_t t, void **ret)
{
    return __duneos_api_ptr->thread.pthread_join(t, ret);
}

void pthread_exit(void *val)
{
    __duneos_api_ptr->thread.pthread_exit(val);
}

pthread_t pthread_self(void)
{
    return __duneos_api_ptr->thread.pthread_self();
}

int pthread_mutex_init(pthread_mutex_t *m, const pthread_mutexattr_t *a)
{
    return __duneos_api_ptr->thread.pthread_mutex_init(m, a);
}

int pthread_mutex_destroy(pthread_mutex_t *m)
{
    return __duneos_api_ptr->thread.pthread_mutex_destroy(m);
}

int pthread_mutex_lock(pthread_mutex_t *m)
{
    return __duneos_api_ptr->thread.pthread_mutex_lock(m);
}

int pthread_mutex_trylock(pthread_mutex_t *m)
{
    return __duneos_api_ptr->thread.pthread_mutex_trylock(m);
}

int pthread_mutex_unlock(pthread_mutex_t *m)
{
    return __duneos_api_ptr->thread.pthread_mutex_unlock(m);
}

/* -------------------------------------------------------------------------
 * POSIX semaphores
 * ---------------------------------------------------------------------- */

int sem_init(sem_t *s, int pshared, unsigned val)
{
    return __duneos_api_ptr->thread.sem_init(s, pshared, val);
}

int sem_destroy(sem_t *s)
{
    return __duneos_api_ptr->thread.sem_destroy(s);
}

int sem_wait(sem_t *s)
{
    return __duneos_api_ptr->thread.sem_wait(s);
}

int sem_trywait(sem_t *s)
{
    return __duneos_api_ptr->thread.sem_trywait(s);
}

int sem_post(sem_t *s)
{
    return __duneos_api_ptr->thread.sem_post(s);
}
