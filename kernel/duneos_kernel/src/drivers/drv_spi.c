#include "duneos/dev_driver.h"
#include "duneos/hal_spi.h"
#include "duneos/spi_ioctl.h"
#include "duneos/klog.h"
#include "board_config.h"

#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>

static const char *TAG = "duneos/spi";

/*
 * Multi-host SPI bus driver.
 *
 * Every spi: entry in board.yaml with `role: raw` becomes a /dev/spi-<N>
 * device, where N is the raw-bus index (1-based, in declaration order).
 * bspgen emits DUNEOS_SPI<N>_HOST/MOSI/CLK/MISO/MAX_FREQ_HZ for each.
 *
 * The `duneos_dev_driver_t` interface doesn't carry per-instance context,
 * so we attach the bus handle to the driver via container_of: the
 * driver_t is embedded inside a spi_bus_slot_t alongside its hal bus
 * pointer. From any open()/read()/write()/ioctl() callback we receive
 * a `fd->drv` pointer, and container_of recovers the bus slot.
 *
 * Per-fd state stays small (16 B max — see DUNEOS_DEV_PRIV_SIZE).
 */

#define container_of(ptr, type, member) \
    ((type *)((char *)(ptr) - offsetof(type, member)))

typedef struct {
    duneos_hal_spi_dev_t        *dev;
    duneos_hal_spi_dev_config_t  cfg;
} spi_priv_t;

_Static_assert(sizeof(spi_priv_t) <= DUNEOS_DEV_PRIV_SIZE, "spi_priv_t too large");

#define DEFAULT_SPEED_HZ  1000000u
#define DEFAULT_MODE      0u

typedef struct {
    duneos_hal_spi_t   *bus;        /* NULL if init failed; ops then refuse to open. */
    duneos_dev_driver_t drv;        /* registered with devfs — drv.name = "spi-<N>" */
    char                name_buf[8];/* backing storage for drv.name */
} spi_bus_slot_t;

/* Max number of raw SPI buses a board can expose. Bumped if a board ever
 * declares more than this; safe to keep small (compile-time constant). */
#define MAX_RAW_BUSES 4
static spi_bus_slot_t s_slots[MAX_RAW_BUSES];
static int            s_num_slots = 0;

static spi_bus_slot_t *slot_from_fd(const duneos_devfd_t *fd)
{
    return container_of(fd->drv, spi_bus_slot_t, drv);
}

static int spi_open_cb(duneos_devfd_t *fd, int flags)
{
    (void)flags;
    spi_bus_slot_t *slot = slot_from_fd(fd);
    if (!slot->bus) { errno = ENODEV; return -1; }

    spi_priv_t *p = (spi_priv_t *)fd->priv;
    memset(p, 0, sizeof(*p));
    p->cfg.cs_pin  = -1;
    p->cfg.freq_hz = DEFAULT_SPEED_HZ;
    p->cfg.mode    = DEFAULT_MODE;

    p->dev = duneos_hal_spi_dev_add(slot->bus, &p->cfg);
    if (!p->dev) {
        klog_e(TAG, "%s: spi_dev_add failed", slot->drv.name);
        errno = EIO;
        return -1;
    }
    return 0;
}

static int spi_close_cb(duneos_devfd_t *fd)
{
    spi_priv_t *p = (spi_priv_t *)fd->priv;
    duneos_hal_spi_dev_remove(p->dev);
    p->dev = NULL;
    return 0;
}

static int spi_ioctl_cb(duneos_devfd_t *fd, int cmd, void *arg)
{
    spi_priv_t *p = (spi_priv_t *)fd->priv;

    switch (cmd) {
    case SPI_SET_MODE: {
        uint8_t mode = *(uint8_t *)arg;
        if (mode > 3) { errno = EINVAL; return -1; }
        p->cfg.mode = mode;
        if (duneos_hal_spi_dev_reconfig(p->dev, &p->cfg) != 0) { errno = EIO; return -1; }
        return 0;
    }
    case SPI_SET_SPEED: {
        p->cfg.freq_hz = *(uint32_t *)arg;
        if (duneos_hal_spi_dev_reconfig(p->dev, &p->cfg) != 0) { errno = EIO; return -1; }
        return 0;
    }
    case SPI_SET_CS: {
        p->cfg.cs_pin = *(int *)arg;
        if (duneos_hal_spi_dev_reconfig(p->dev, &p->cfg) != 0) { errno = EIO; return -1; }
        return 0;
    }
    case SPI_TRANSFER: {
        spi_xfer_t *xfr = (spi_xfer_t *)arg;
        if (duneos_hal_spi_transfer(p->dev, xfr->tx_buf, xfr->rx_buf, xfr->len) != 0) {
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
    if (duneos_hal_spi_transfer(p->dev, buf, NULL, len) != 0) { errno = EIO; return -1; }
    return (ssize_t)len;
}

static ssize_t spi_read_cb(duneos_devfd_t *fd, void *buf, size_t len)
{
    spi_priv_t *p = (spi_priv_t *)fd->priv;
    if (duneos_hal_spi_transfer(p->dev, NULL, buf, len) != 0) { errno = EIO; return -1; }
    return (ssize_t)len;
}

/* Register one bus: claim a slot, init/attach the hal bus, fill the driver
 * struct with the right name, hand it to devfs. */
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
