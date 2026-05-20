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
 * Phase 24.11 (second attempt — first attempt c74f234 crashed boot for
 * unknown reasons; this version adds klog at every step so the last
 * printed message identifies the crash point if it re-occurs).
 *
 * Each `spi:` entry in board.yaml with `role: raw` becomes /dev/spi-<N>.
 * The bus is initialised once at boot via drv_spi_register; from then on
 * fds open()'d on /dev/spi-N share a small pool of ESP-IDF device handles
 * keyed by CS pin. Two apps with the same CS get the SAME underlying
 * spi_device_handle_t — no GPIO matrix conflict, no contention beyond
 * what ESP-IDF's own bus mutex already serialises.
 *
 * Mode/freq reconfig is last-writer-wins on the shared entry, which is
 * fine when the fds talk to the same chip (the natural sharing case).
 */

#define container_of(ptr, type, member) \
    ((type *)((char *)(ptr) - offsetof(type, member)))

#define MAX_DEVICES_PER_BUS 8

typedef struct {
    duneos_hal_spi_dev_t        *dev;       /* NULL = free slot */
    duneos_hal_spi_dev_config_t  cfg;
    int                          cs_pin;    /* -1 = free slot */
    int                          refcount;
} spi_dev_pool_entry_t;

typedef struct {
    duneos_hal_spi_t      *bus;
    duneos_dev_driver_t    drv;
    char                   name_buf[8];     /* "spi-<N>" */
    spi_dev_pool_entry_t   pool[MAX_DEVICES_PER_BUS];
    SemaphoreHandle_t      lock;
} spi_bus_slot_t;

typedef struct {
    spi_dev_pool_entry_t *entry;            /* NULL until SPI_SET_CS */
    uint8_t               mode;
    uint32_t              freq_hz;
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

/* Lazy mutex creation. If creation fails we serialise nothing — accept the
 * race rather than refusing service. Better than a NULL-deref crash. */
static SemaphoreHandle_t slot_lock(spi_bus_slot_t *slot)
{
    if (!slot->lock) {
        slot->lock = xSemaphoreCreateMutex();
        if (!slot->lock) klog_w(TAG, "%s: mutex alloc failed (race possible)",
                                slot->name_buf);
    }
    return slot->lock;
}

static void slot_lock_take(spi_bus_slot_t *slot)
{
    SemaphoreHandle_t l = slot_lock(slot);
    if (l) xSemaphoreTake(l, portMAX_DELAY);
}

static void slot_lock_give(spi_bus_slot_t *slot)
{
    if (slot->lock) xSemaphoreGive(slot->lock);
}

static spi_dev_pool_entry_t *pool_find(spi_bus_slot_t *slot, int cs)
{
    for (int i = 0; i < MAX_DEVICES_PER_BUS; i++)
        if (slot->pool[i].cs_pin == cs && slot->pool[i].dev) return &slot->pool[i];
    return NULL;
}

static spi_dev_pool_entry_t *pool_find_free(spi_bus_slot_t *slot)
{
    for (int i = 0; i < MAX_DEVICES_PER_BUS; i++)
        if (slot->pool[i].cs_pin == -1) return &slot->pool[i];
    return NULL;
}

static void pool_release(spi_bus_slot_t *slot, spi_dev_pool_entry_t *e)
{
    if (!e) return;
    if (--e->refcount <= 0) {
        klog_d(TAG, "%s: pool[cs=%d] last user, removing device",
               slot->name_buf, e->cs_pin);
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
        slot_lock_take(slot);
        pool_release(slot, p->entry);
        slot_lock_give(slot);
        p->entry = NULL;
    }
    return 0;
}

static int spi_set_cs(spi_bus_slot_t *slot, spi_priv_t *p, int cs)
{
    slot_lock_take(slot);

    if (p->entry) {
        pool_release(slot, p->entry);
        p->entry = NULL;
    }

    spi_dev_pool_entry_t *e = pool_find(slot, cs);
    if (!e) {
        e = pool_find_free(slot);
        if (!e) {
            slot_lock_give(slot);
            klog_e(TAG, "%s: pool full (>%d CS pins)", slot->name_buf, MAX_DEVICES_PER_BUS);
            errno = ENOMEM;
            return -1;
        }
        e->cfg.cs_pin  = cs;
        e->cfg.mode    = p->mode;
        e->cfg.freq_hz = p->freq_hz;
        e->dev = duneos_hal_spi_dev_add(slot->bus, &e->cfg);
        if (!e->dev) {
            slot_lock_give(slot);
            klog_e(TAG, "%s: spi_dev_add(cs=%d) failed", slot->name_buf, cs);
            errno = EIO;
            return -1;
        }
        e->cs_pin   = cs;
        e->refcount = 0;
        klog_d(TAG, "%s: pool[cs=%d] new device", slot->name_buf, cs);
    } else {
        klog_d(TAG, "%s: pool[cs=%d] reusing shared device (refcount %d→%d)",
               slot->name_buf, cs, e->refcount, e->refcount + 1);
    }
    e->refcount++;
    p->entry = e;
    slot_lock_give(slot);
    return 0;
}

static int spi_reconfig_entry(spi_bus_slot_t *slot, spi_priv_t *p)
{
    if (!p->entry) return 0;
    slot_lock_take(slot);
    p->entry->cfg.mode    = p->mode;
    p->entry->cfg.freq_hz = p->freq_hz;
    int rc = duneos_hal_spi_dev_reconfig(p->entry->dev, &p->entry->cfg);
    slot_lock_give(slot);
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
    case SPI_SET_CS:
        return spi_set_cs(slot, p, *(int *)arg);
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

/* Register one bus. Klog at every step so the LAST visible message tells
 * us where a crash occurred on next reflash. */
static void register_one_bus(int index,
                             int host, int mosi, int miso, int clk,
                             bool shared)
{
    klog_i(TAG, "register: index=%d host=%d shared=%d", index, host, shared ? 1 : 0);

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
    klog_i(TAG, "register: drv struct filled for %s", slot->name_buf);

    /* Pool entries: BSS-zero gives cs_pin=0 which would be a valid pin.
     * Set them to -1 (= free) explicitly. */
    for (int i = 0; i < MAX_DEVICES_PER_BUS; i++) {
        slot->pool[i].cs_pin   = -1;
        slot->pool[i].dev      = NULL;
        slot->pool[i].refcount = 0;
    }
    /* Lock is created lazily on first use — see slot_lock(). */
    klog_i(TAG, "register: pool init done for %s", slot->name_buf);

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
    klog_i(TAG, "register: bus ready for %s", slot->name_buf);

    if (duneos_dev_register(&slot->drv) != 0) {
        klog_e(TAG, "%s: dev_register failed", slot->name_buf);
        return;
    }
    s_num_slots++;
    klog_i(TAG, "register: %s online (now %d slots)", slot->name_buf, s_num_slots);
}

void drv_spi_register(void)
{
    klog_i(TAG, "drv_spi_register: starting");
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
    klog_i(TAG, "drv_spi_register: done (%d slots)", s_num_slots);
}
