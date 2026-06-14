/*
 * drv_shellpipe — /dev/shellpipe, a write-only sink device for streaming a
 * captured app's stdout straight to its shell. See <duneos/shellpipe.h>.
 *
 * The loader points a captured app's fd 1 here; each write() is forwarded to
 * the sink the shell registered. With no sink set, writes are silently dropped
 * (the device always reports the bytes as accepted), so an app that prints
 * without a shell attached never blocks or errors.
 */

#include "duneos/dev_driver.h"
#include "duneos/driver_init.h"
#include "duneos/shellpipe.h"
#include <stddef.h>

static duneos_shell_sink_fn s_sink;
static void                *s_ctx;

void duneos_shellpipe_set_sink(duneos_shell_sink_fn sink, void *ctx)
{
    s_sink = sink;
    s_ctx  = ctx;
}

static int shellpipe_open(duneos_devfd_t *fd, int flags)
{
    (void)fd; (void)flags;
    return 0;
}

static ssize_t shellpipe_write(duneos_devfd_t *fd, const void *buf, size_t len)
{
    (void)fd;
    if (s_sink && len) s_sink((const char *)buf, (int)len, s_ctx);
    return (ssize_t)len;
}

static const duneos_dev_driver_t s_drv_shellpipe = {
    .name  = "shellpipe",
    .open  = shellpipe_open,
    .write = shellpipe_write,
};

void drv_shellpipe_register(void)
{
    duneos_dev_register(&s_drv_shellpipe);
}

DUNEOS_DRIVER_REGISTER(5, drv_shellpipe_register);
