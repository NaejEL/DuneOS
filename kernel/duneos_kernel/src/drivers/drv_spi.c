#include "duneos/dev_driver.h"
#include "duneos/hal_spi.h"
#include "duneos/spi_ioctl.h"
#include "duneos/klog.h"
#include "board_config.h"

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>

static const char *TAG = "duneos/spi";

/*
 * Multi-host SPI bus driver with refcounted device sharing.
 *
 * Each `spi:` entry in board.yaml with `role: raw` becomes /dev/spi-<N>.
 * bspgen emits DUNEOS_SPI<N>_HOST/MOSI/CLK/MISO/MAX_FREQ_HZ for each.
 *
 * **Per-CS device sharing** (fix to the regression observed 2026-05-20:
 * two apps opening /dev/spi-1 with the same CS=37 tripped the ESP-IDF
 * "GPIO N is conflict" warning, then transactions corrupted). Each bus
 * slot owns a small pool of spi_dev_pool_entry_t keyed by CS pin —
 * when two fds set the same CS, they share the underlying ESP-IDF
 * device handle. The ESP-IDF SPI driver already serialises bus
 * transactions internally, so simultaneous fds talking to the same
 * chip just queue up. Mode/speed reconfig is "last writer wins" on
 * the shared entry, which is fine for same-chip sharing (the natural
 * case — two apps drawing on the same display).
 *
 * Per-fd state stays small (16 B max — see DUNEOS_DEV_PRIV_SIZE):
 * just a pointer to the pool entry + cached mode/speed for fds that
 * haven't called SPI_SET_CS yet.
 */

#define container_of(ptr, type, member) \
    ((type *)((char *)(ptr) - offsetof(type, member)))

/* Per-bus device pool: at most N distinct CS pins active simultaneously. */
#define MAX_DEVICES_PER_BUS 8

typedef struct {
    duneos_hal_spi_dev_t        *dev;       /* NULL = free slot */
    duneos_hal_spi_dev_config_t  cfg;       /* current cs/mode/freq */
    int                          cs_pin;    /* -1 = free slot */
    int                          refcount;
} spi_dev_pool_entry_t;

typedef struct {
    duneos_hal_spi_t      *bus;             /* NULL if init failed */
    duneos_dev_driver_t    drv;             /* registered with devfs */
    char                   name_buf[8];     /* "spi-<N>" */
    spi_dev_pool_entry_t   pool[MAX_DEVICES_PER_BUS];
    SemaphoreHandle_t      lock;            /* protects pool + refcounts */
} spi_bus_slot_t;

typedef struct {
    spi_dev_pool_entry_t *entry;            /* NULL until SPI_SET_CS */
    uint8_t               mode;             /* cached until entry exists */
    uint32_t              freq_hz;          /* cached until entry exists */
} spi_priv_t;

_Static_assert(sizeof(spi_priv_t) <= DUNEOS_DEV_PRIV_SIZE, "spi_priv_t too large");

#define DEFAULT_SPEED_HZ  1000000u
#define DEFAULT_MODE      0u

#define MAX_RAW_BUSES 4
static spi_bus_slot_t s_slots[MAX_RAW_BUSES];
static int            s_num_slots = 0;

static spi_bus_slot_t *slot_from_fd(const duneos_devfd_t *fd)
{
    return container_of(fd->drv, spi_bus_slot_t, drv);
}

/* Find existing pool entry by CS pin. Caller must hold slot->lock. */
static spi_dev_pool_entry_t *pool_find(spi_bus_slot_t *slot, int cs)
{
    for (int i = 0; i < MAX_DEVICES_PER_BUS; i++)
        if (slot->pool[i].cs_pin == cs && slot->pool[i].dev) return &slot->pool[i];
    return NULL;
}

/* Find a free pool slot. Caller must hold slot->lock. */
static spi_dev_pool_entry_t *pool_find_free(spi_bus_slot_t *slot)
{
    for (int i = 0; i < MAX_DEVICES_PER_BUS; i++)
        if (slot->pool[i].cs_pin == -1) return &slot->pool[i];
    return NULL;
}

/* Drop refcount on an entry; free the device if last user. Caller must
 * hold slot->lock. */
static void pool_release(spi_dev_pool_entry_t *e)
{
    if (!e) return;
    if (--e->refcount <= 0) {
        if (e->dev) duneos_hal_spi_dev_remove(e->dev);
        e->dev      = NULL;
        e->cs_pin   = -1;
        e->refcount = 0;
    }
}

static int spi_open_cb(duneos_devfd_t *fd, int flags)
{
    (void)flags;
    spi_bus_slot_t *slot = slot_from_fd(fd);
    if (!slot->bus) { errno = ENODEV; return -1; }

    spi_priv_t *p = (spi_priv_t *)fd->priv;
    p->entry   = NULL;
    p->mode    = DEFAULT_MODE;
    p->freq_hz = DEFAULT_SPEED_HZ;
    return 0;
}

static int spi_close_cb(duneos_devfd_t *fd)
{
    spi_bus_slot_t *slot = slot_from_fd(fd);
    spi_priv_t     *p    = (spi_priv_t *)fd->priv;

    if (p->entry) {
        xSemaphoreTake(slot->lock, portMAX_DELAY);
        pool_release(p->entry);
        xSemaphoreGive(slot->lock);
        p->entry = NULL;
    }
    return 0;
}

/* Attach this fd to a pool entry for (cs, current mode, current freq).
 * If an entry with the same CS already exists, share it (refcount++).
 * Otherwise allocate a new pool slot + spi_bus_add_device. Caller must NOT
 * hold slot->lock. */
static int spi_set_cs_locked(spi_bus_slot_t *slot, spi_priv_t *p, int cs)
{
    xSemaphoreTake(slot->lock, portMAX_DELAY);

    /* Drop any previous attachment. */
    if (p->entry) {
        pool_release(p->entry);
        p->entry = NULL;
    }

    spi_dev_pool_entry_t *e = pool_find(slot, cs);
    if (!e) {
        e = pool_find_free(slot);
        if (!e) {
            xSemaphoreGive(slot->lock);
            klog_e(TAG, "%s: pool full (>%d distinct CS pins)",
                   slot->name_buf, MAX_DEVICES_PER_BUS);
            errno = ENOMEM;
            return -1;
        }
        e->cfg.cs_pin  = cs;
        e->cfg.mode    = p->mode;
        e->cfg.freq_hz = p->freq_hz;
        e->dev = duneos_hal_spi_dev_add(slot->bus, &e->cfg);
        if (!e->dev) {
            xSemaphoreGive(slot->lock);
            klog_e(TAG, "%s: spi_dev_add(cs=%d) failed", slot->name_buf, cs);
            errno = EIO;
            return -1;
        }
        e->cs_pin   = cs;
        e->refcount = 0;
    }
    e->refcount++;
    p->entry = e;
    xSemaphoreGive(slot->lock);
    return 0;
}

/* Apply a reconfig (mode or freq change) to the entry the fd is bound to.
 * The new config is "last writer wins" across all fds sharing this entry —
 * acceptable when the fds all talk to the same chip. */
static int spi_reconfig_entry(spi_bus_slot_t *slot, spi_priv_t *p)
{
    if (!p->entry) return 0;   /* not yet bound — config is cached in p */

    xSemaphoreTake(slot->lock, portMAX_DELAY);
    p->entry->cfg.mode    = p->mode;
    p->entry->cfg.freq_hz = p->freq_hz;
    int rc = duneos_hal_spi_dev_reconfig(p->entry->dev, &p->entry->cfg);
    xSemaphoreGive(slot->lock);
    return rc;
}

static int spi_ioctl_cb(duneos_devfd_t *fd, int cmd, void *arg)
{
    spi_bus_slot_t *slot = slot_from_fd(fd);
    spi_priv_t     *p    = (spi_priv_t *)fd->priv;

    switch (cmd) {
    case SPI_SET_MODE: {
        uint8_t mode = *(uint8_t *)arg;
        if (mode > 3) { errno = EINVAL; return -1; }
        p->mode = mode;
        if (spi_reconfig_entry(slot, p) != 0) { errno = EIO; return -1; }
        return 0;
    }
    case SPI_SET_SPEED: {
        p->freq_hz = *(uint32_t *)arg;
        if (spi_reconfig_entry(slot, p) != 0) { errno = EIO; return -1; }
        return 0;
    }
    case SPI_SET_CS: {
        int cs = *(int *)arg;
        return spi_set_cs_locked(slot, p, cs);
    }
    case SPI_TRANSFER: {
        if (!p->entry) { errno = EINVAL; return -1; }
        spi_xfer_t *xfr = (spi_xfer_t *)arg;
        if (duneos_hal_spi_transfer(p->entry->dev, xfr->tx_buf, xfr->rx_buf, xfr->len) != 0) {
            errno = EIO; return -1;
        }
        return 0;
    }
    default:
        errno = ENOTTY;
        return -1;
    }
}

static ssize_t spi_write_cb(duneos_devfd_t *fd, const void *buf, size_t len)
{
    spi_priv_t *p = (spi_priv_t *)fd->priv;
    if (!p->entry) { errno = EINVAL; return -1; }
    if (duneos_hal_spi_transfer(p->entry->dev, buf, NULL, len) != 0) { errno = EIO; return -1; }
    return (ssize_t)len;
}

static ssize_t spi_read_cb(duneos_devfd_t *fd, void *buf, size_t len)
{
    spi_priv_t *p = (spi_priv_t *)fd->priv;
    if (!p->entry) { errno = EINVAL; return -1; }
    if (duneos_hal_spi_transfer(p->entry->dev, NULL, buf, len) != 0) { errno = EIO; return -1; }
    return (ssize_t)len;
}

/* Register one bus: claim a slot, init/attach the hal bus, create its
 * pool mutex, fill the driver struct with the right name, hand it to
 * devfs. */
static void register_one_bus(int index,
                             int host, int mosi, int miso, int clk,
                             bool shared)
{
    if (s_num_slots >= MAX_RAW_BUSES) {
        klog_e(TAG, "MAX_RAW_BUSES=%d reached, cannot register /dev/spi-%d",
               MAX_RAW_BUSES, index);
        return;
    }
    spi_bus_slot_t *slot = &s_slots[s_num_slots];
    snprintf(slot->name_buf, sizeof(slot->name_buf), "spi-%d", index);
    slot->drv.name  = slot->name_buf;
    slot->drv.open  = spi_open_cb;
    slot->drv.close = spi_close_cb;
    slot->drv.read  = spi_read_cb;
    slot->drv.write = spi_write_cb;
    slot->drv.ioctl = spi_ioctl_cb;

    for (int i = 0; i < MAX_DEVICES_PER_BUS; i++) {
        slot->pool[i].cs_pin   = -1;
        slot->pool[i].dev      = NULL;
        slot->pool[i].refcount = 0;
    }
    slot->lock = xSemaphoreCreateMutex();
    if (!slot->lock) { klog_e(TAG, "%s: mutex alloc failed", slot->name_buf); return; }

    if (shared) {
        slot->bus = duneos_hal_spi_bus_attach(host);
        if (!slot->bus) {
            klog_e(TAG, "%s: bus_attach failed (host=%d)", slot->name_buf, host);
            return;
        }
    } else {
        duneos_hal_spi_bus_config_t bus_cfg = {
            .bus_id          = host,
            .mosi_pin        = mosi,
            .miso_pin        = miso,
            .clk_pin         = clk,
            .max_transfer_sz = 4096,
        };
        slot->bus = duneos_hal_spi_bus_init(&bus_cfg);
        if (!slot->bus) {
            klog_e(TAG, "%s: bus_init failed (host=%d)", slot->name_buf, host);
            return;
        }
    }

    duneos_dev_register(&slot->drv);
    s_num_slots++;
}

void drv_spi_register(void)
{
#ifdef DUNEOS_SPI1_HOST
    register_one_bus(1, DUNEOS_SPI1_HOST,
                     DUNEOS_SPI1_MOSI_PIN, DUNEOS_SPI1_MISO_PIN, DUNEOS_SPI1_CLK_PIN,
#  ifdef DUNEOS_SPI1_BUS_SHARED
                     true
#  else
                     false
#  endif
                     );
#endif
#ifdef DUNEOS_SPI2_HOST
    register_one_bus(2, DUNEOS_SPI2_HOST,
                     DUNEOS_SPI2_MOSI_PIN, DUNEOS_SPI2_MISO_PIN, DUNEOS_SPI2_CLK_PIN,
#  ifdef DUNEOS_SPI2_BUS_SHARED
                     true
#  else
                     false
#  endif
                     );
#endif
#ifdef DUNEOS_SPI3_HOST
    register_one_bus(3, DUNEOS_SPI3_HOST,
                     DUNEOS_SPI3_MOSI_PIN, DUNEOS_SPI3_MISO_PIN, DUNEOS_SPI3_CLK_PIN,
#  ifdef DUNEOS_SPI3_BUS_SHARED
                     true
#  else
                     false
#  endif
                     );
#endif
}
