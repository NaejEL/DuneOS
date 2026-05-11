#include "duneos/dev_driver.h"
#include "duneos/klog.h"
#include <stddef.h>

/* Per-fd position in the klog ring buffer (absolute byte offset). */
typedef struct {
    size_t pos;
} klog_priv_t;

_Static_assert(sizeof(klog_priv_t) <= DUNEOS_DEV_PRIV_SIZE, "klog_priv_t too large");

static int klog_open(duneos_devfd_t *fd, int flags)
{
    (void)flags;
    klog_priv_t *p = (klog_priv_t *)fd->priv;
    p->pos = klog_ring_oldest();
    return 0;
}

static ssize_t klog_read(duneos_devfd_t *fd, void *buf, size_t len)
{
    klog_priv_t *p = (klog_priv_t *)fd->priv;
    return (ssize_t)klog_ring_read(&p->pos, buf, len);
}

static const duneos_dev_driver_t s_drv_klog = {
    .name  = "klog",
    .open  = klog_open,
    .read  = klog_read,
};

void drv_klog_register(void)
{
    duneos_dev_register(&s_drv_klog);
}
