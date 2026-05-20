/*
 * DuneOS /dev/raw80211 — raw 802.11 frame injection driver.
 *
 * write(fd, frame, len) → esp_wifi_80211_tx(iface, frame, len, false)
 *
 * Interface selection:
 *   Default: WIFI_IF_STA — works while wifi_daemon maintains a STA connection.
 *   WIFI_IF_AP: requires WiFi to be in APSTA or AP mode; the kernel does NOT
 *   automatically switch mode.  Start wifi_daemon in APSTA mode, or have the
 *   app call duneos_wifi_init() with explicit mode management if needed.
 *
 * The open() callback checks that WiFi has been started; if not, it returns
 * ENODEV immediately rather than hanging.
 *
 * Permission model: any app that opens /dev/raw80211 and has
 * DUNEOS_PERM_NET_RAW in its manifest can inject frames.  The permission
 * check is advisory today (enforced by convention, not by hardware MMU).
 */

#include "duneos/dev_driver.h"
#include "duneos/driver_init.h"
#include "duneos/raw80211_ioctl.h"
#include "duneos/klog.h"

#include "esp_wifi.h"

#include <errno.h>
#include <stdint.h>
#include <string.h>

/*
 * The WiFi driver rejects raw frames via this sanity check.
 * We override the weak symbol so esp_wifi_80211_tx() always proceeds.
 * -Wl,-zmuldefs in CMakeLists.txt allows this redefinition.
 */
int ieee80211_raw_frame_sanity_check(int32_t arg, int32_t arg2, int32_t arg3)
{
    (void)arg; (void)arg2; (void)arg3;
    return 0;
}

#define TAG "duneos/raw80211"

typedef struct {
    uint8_t iface;   /* DUNEOS_WIFI_IF_STA or DUNEOS_WIFI_IF_AP */
} raw80211_priv_t;

_Static_assert(sizeof(raw80211_priv_t) <= DUNEOS_DEV_PRIV_SIZE,
               "raw80211_priv_t exceeds DUNEOS_DEV_PRIV_SIZE");

static int raw80211_open(duneos_devfd_t *fd, int flags)
{
    (void)flags;
    raw80211_priv_t *p = (raw80211_priv_t *)fd->priv;
    p->iface = DUNEOS_WIFI_IF_STA;

    /* Probe WiFi state — esp_wifi_80211_tx returns ESP_ERR_WIFI_NOT_STARTED
     * if WiFi is not initialised; catch it early here.                      */
    wifi_mode_t mode;
    if (esp_wifi_get_mode(&mode) != ESP_OK) {
        klog_w(TAG, "open: WiFi not started — call duneos_wifi_init() first");
        errno = ENODEV;
        return -1;
    }
    return 0;
}

static ssize_t raw80211_write(duneos_devfd_t *fd, const void *buf, size_t len)
{
    raw80211_priv_t *p = (raw80211_priv_t *)fd->priv;
    wifi_interface_t iface = (wifi_interface_t)p->iface;

    esp_err_t err = esp_wifi_80211_tx(iface, buf, (int)len, false);
    if (err == ESP_ERR_WIFI_NOT_STARTED) { errno = ENODEV; return -1; }
    if (err == ESP_ERR_WIFI_IF)          { errno = ENXIO;  return -1; }
    if (err != ESP_OK)                   { errno = EIO;    return -1; }
    return (ssize_t)len;
}

static int raw80211_ioctl(duneos_devfd_t *fd, int cmd, void *arg)
{
    raw80211_priv_t *p = (raw80211_priv_t *)fd->priv;
    switch (cmd) {
    case RAW80211_SET_IFACE: {
        int req = *(int *)arg;
        if (req != DUNEOS_WIFI_IF_STA && req != DUNEOS_WIFI_IF_AP) {
            errno = EINVAL;
            return -1;
        }
        p->iface = (uint8_t)req;
        return 0;
    }
    case RAW80211_GET_IFACE:
        *(int *)arg = (int)p->iface;
        return 0;
    default:
        errno = ENOTTY;
        return -1;
    }
}

static const duneos_dev_driver_t s_drv_raw80211 = {
    .name  = "raw80211",
    .open  = raw80211_open,
    .write = raw80211_write,
    .ioctl = raw80211_ioctl,
};

void drv_raw80211_register(void)
{
    duneos_dev_register(&s_drv_raw80211);
}

DUNEOS_DRIVER_REGISTER(5, drv_raw80211_register);
