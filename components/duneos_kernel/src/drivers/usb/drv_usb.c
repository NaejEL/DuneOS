/*
 * drv_usb.c — TinyUSB device driver for DuneOS
 *
 * Two-phase init:
 *
 *   drv_usb_preinit()   — called as the FIRST thing in duneos_vfs_init(), before
 *                         any klog/printf.  Installs the TinyUSB hardware stack and
 *                         the CDC-ACM class so that _write() (printf) works.
 *                         With CONFIG_ESP_CONSOLE_USB_CDC=y, _write() routes
 *                         through TinyUSB CDC; that means TinyUSB must be up
 *                         before the very first console write.
 *
 *   drv_usb_register()  — called from vfs_dev.c AFTER duneos_vfs_mount_sd().
 *                         Registers the SD card as the MSC storage backend.
 *                         The MSC interface is already present in the USB descriptor
 *                         from preinit; the host sees "no media" until this runs.
 *
 * MSC lifecycle:
 *   After register the SD card is accessible to the OS at /sd (normal operation).
 *   Call duneos_usb_msc_expose() to hand control to the USB host (unmounts /sd).
 *   Call duneos_usb_msc_reclaim() to take control back (remounts /sd).
 */

#include "duneos/klog.h"
#include "duneos/vfs.h"
#include "board_config.h"

#include "tinyusb.h"
#include "tinyusb_default_config.h"

#ifdef CONFIG_DUNEOS_DRV_USB_MSC
#include "tinyusb_msc.h"
#include "sdmmc_cmd.h"
#endif

#ifdef CONFIG_DUNEOS_DRV_USB_CDC
#include "drv_usb_cdc_priv.h"
#include "tinyusb_cdc_acm.h"
#endif

#include <string.h>

static const char *TAG = "duneos/usb";

/* -------------------------------------------------------------------------
 * USB device descriptors
 * ---------------------------------------------------------------------- */

/* VID 0x303A = Espressif Systems (dev use); PID 0x4DAE = DuneOS device */
static const tusb_desc_device_t s_device_desc = {
    .bLength            = sizeof(tusb_desc_device_t),
    .bDescriptorType    = TUSB_DESC_DEVICE,
    .bcdUSB             = 0x0200,
    .bDeviceClass       = TUSB_CLASS_MISC,
    .bDeviceSubClass    = MISC_SUBCLASS_COMMON,
    .bDeviceProtocol    = MISC_PROTOCOL_IAD,
    .bMaxPacketSize0    = CFG_TUD_ENDPOINT0_SIZE,
    .idVendor           = 0x303A,
    .idProduct          = 0x4DAE,
    .bcdDevice          = 0x0100,
    .iManufacturer      = 0x01,
    .iProduct           = 0x02,
    .iSerialNumber      = 0x03,
    .bNumConfigurations = 0x01,
};

static const char *s_string_desc[] = {
    "\x09\x04",          /* 0: language = English (0x0409) */
    "DuneOS",            /* 1: Manufacturer */
    "DuneOS Storage",    /* 2: Product */
    "000001",            /* 3: Serial — fixed; replace with chip UID if needed */
    "DuneOS CDC",        /* 4: CDC Interface */
    "DuneOS MSC",        /* 5: MSC Interface */
};

/* -------------------------------------------------------------------------
 * Configuration descriptor — built at compile time based on enabled classes
 * ---------------------------------------------------------------------- */

#if defined(CONFIG_DUNEOS_DRV_USB_MSC) && defined(CONFIG_DUNEOS_DRV_USB_CDC)

/* Composite: CDC (2 interfaces) + MSC (1 interface) */
enum { ITF_CDC_CMD = 0, ITF_CDC_DATA, ITF_MSC, ITF_TOTAL };
enum {
    EP_CTRL_OUT = 0x00, EP_CTRL_IN  = 0x80,
    EP_CDC_NOTIF = 0x81,
    EP_CDC_OUT   = 0x02, EP_CDC_IN   = 0x82,
    EP_MSC_OUT   = 0x03, EP_MSC_IN   = 0x83,
};
#define CFG_DESC_LEN (TUD_CONFIG_DESC_LEN + TUD_CDC_DESC_LEN + TUD_MSC_DESC_LEN)

static const uint8_t s_config_desc[] = {
    TUD_CONFIG_DESCRIPTOR(1, ITF_TOTAL, 0, CFG_DESC_LEN,
                          TUSB_DESC_CONFIG_ATT_REMOTE_WAKEUP, 100),
    TUD_CDC_DESCRIPTOR(ITF_CDC_CMD, 4, EP_CDC_NOTIF, 8,
                       EP_CDC_OUT, EP_CDC_IN, 64),
    TUD_MSC_DESCRIPTOR(ITF_MSC, 5, EP_MSC_OUT, EP_MSC_IN, 64),
};

#elif defined(CONFIG_DUNEOS_DRV_USB_MSC)

/* MSC only */
enum { ITF_MSC = 0, ITF_TOTAL };
enum {
    EP_CTRL_OUT = 0x00, EP_CTRL_IN = 0x80,
    EP_MSC_OUT  = 0x01, EP_MSC_IN  = 0x81,
};
#define CFG_DESC_LEN (TUD_CONFIG_DESC_LEN + TUD_MSC_DESC_LEN)

static const uint8_t s_config_desc[] = {
    TUD_CONFIG_DESCRIPTOR(1, ITF_TOTAL, 0, CFG_DESC_LEN,
                          TUSB_DESC_CONFIG_ATT_REMOTE_WAKEUP, 100),
    TUD_MSC_DESCRIPTOR(ITF_MSC, 5, EP_MSC_OUT, EP_MSC_IN, 64),
};

#elif defined(CONFIG_DUNEOS_DRV_USB_CDC)

/* CDC only */
enum { ITF_CDC_CMD = 0, ITF_CDC_DATA, ITF_TOTAL };
enum {
    EP_CTRL_OUT  = 0x00, EP_CTRL_IN   = 0x80,
    EP_CDC_NOTIF = 0x81,
    EP_CDC_OUT   = 0x02, EP_CDC_IN    = 0x82,
};
#define CFG_DESC_LEN (TUD_CONFIG_DESC_LEN + TUD_CDC_DESC_LEN)

static const uint8_t s_config_desc[] = {
    TUD_CONFIG_DESCRIPTOR(1, ITF_TOTAL, 0, CFG_DESC_LEN,
                          TUSB_DESC_CONFIG_ATT_REMOTE_WAKEUP, 100),
    TUD_CDC_DESCRIPTOR(ITF_CDC_CMD, 4, EP_CDC_NOTIF, 8,
                       EP_CDC_OUT, EP_CDC_IN, 64),
};

#endif /* class combinations */

/* -------------------------------------------------------------------------
 * MSC storage handle
 * ---------------------------------------------------------------------- */

#ifdef CONFIG_DUNEOS_DRV_USB_MSC
static tinyusb_msc_storage_handle_t s_msc_hdl = NULL;

static void msc_mount_changed_cb(tinyusb_msc_storage_handle_t hdl,
                                  tinyusb_msc_event_t *event,
                                  void *arg)
{
    (void)hdl; (void)arg;
    if (event->mount_point == TINYUSB_MSC_STORAGE_MOUNT_APP)
        klog_i(TAG, "MSC: SD remounted to /sd (host ejected)");
    else
        klog_i(TAG, "MSC: SD exposed to USB host — /sd unmounted");
}

esp_err_t duneos_usb_msc_expose(void)
{
    if (!s_msc_hdl) return ESP_ERR_INVALID_STATE;
    return tinyusb_msc_set_storage_mount_point(s_msc_hdl,
                                               TINYUSB_MSC_STORAGE_MOUNT_USB);
}

esp_err_t duneos_usb_msc_reclaim(void)
{
    if (!s_msc_hdl) return ESP_ERR_INVALID_STATE;
    return tinyusb_msc_set_storage_mount_point(s_msc_hdl,
                                               TINYUSB_MSC_STORAGE_MOUNT_APP);
}
#endif /* CONFIG_DUNEOS_DRV_USB_MSC */

/* -------------------------------------------------------------------------
 * Phase 1: hardware + class init — called BEFORE any klog from vfs.c
 * ---------------------------------------------------------------------- */

void drv_usb_preinit(void)
{
    esp_err_t err;

#ifdef CONFIG_DUNEOS_DRV_USB_MSC
    /* Install MSC driver before tinyusb_driver_install.  No storage instance
     * yet — SCSI returns "no media" until drv_usb_register() supplies the
     * SD card handle after the SD is mounted. */
    tinyusb_msc_driver_config_t drv_cfg = {
        .user_flags.auto_mount_off = 1,
        .callback     = msc_mount_changed_cb,
        .callback_arg = NULL,
    };
    err = tinyusb_msc_install_driver(&drv_cfg);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        /* klog may or may not reach the host here; logged anyway for UART mode */
        klog_e(TAG, "MSC driver install failed: %s", esp_err_to_name(err));
    }
#endif

#if defined(CONFIG_DUNEOS_DRV_USB_CDC) && !defined(CONFIG_ESP_CONSOLE_USB_CDC)
    /* Ring buffer + semaphore for /dev/ttyUSB0 — only when DuneOS owns CDC I/O.
     * Skipped when the ESP console subsystem owns CDC (console mode). */
    drv_usb_cdc_preinit();
#endif

    tinyusb_config_t tusb_cfg = TINYUSB_DEFAULT_CONFIG();
    tusb_cfg.descriptor.device             = &s_device_desc;
    tusb_cfg.descriptor.full_speed_config  = s_config_desc;
    tusb_cfg.descriptor.string             = s_string_desc;
    tusb_cfg.descriptor.string_count       =
        sizeof(s_string_desc) / sizeof(s_string_desc[0]);

    err = tinyusb_driver_install(&tusb_cfg);
    if (err != ESP_OK) {
        klog_e(TAG, "tinyusb_driver_install failed: %s", esp_err_to_name(err));
        return;
    }

    /* From this point klog/printf are safe in CDC console mode. */

#ifdef CONFIG_DUNEOS_DRV_USB_CDC
    tinyusb_config_cdcacm_t cdc_cfg = {
        .cdc_port                    = TINYUSB_CDC_ACM_0,
#ifdef CONFIG_ESP_CONSOLE_USB_CDC
        /* ESP-IDF console owns CDC I/O — null callbacks; the console subsystem
         * registers its own RX handler after tinyusb_cdcacm_init(). */
        .callback_rx                 = NULL,
        .callback_rx_wanted_char     = NULL,
        .callback_line_state_changed = NULL,
        .callback_line_coding_changed = NULL,
#else
        .callback_rx                 = drv_usb_cdc_rx_cb,
        .callback_rx_wanted_char     = NULL,
        .callback_line_state_changed = drv_usb_cdc_line_state_cb,
        .callback_line_coding_changed = NULL,
#endif
    };
    err = tinyusb_cdcacm_init(&cdc_cfg);
    if (err != ESP_OK)
        klog_e(TAG, "CDC ACM init failed: %s", esp_err_to_name(err));
    else {
        klog_i(TAG, "USB CDC: %s",
#ifdef CONFIG_ESP_CONSOLE_USB_CDC
               "console mode (printf -> /dev/ttyACM0; UART0 free for apps)"
#else
               "/dev/ttyUSB0 ready"
#endif
               );
#ifndef CONFIG_ESP_CONSOLE_USB_CDC
        /* Route klog/ESP_LOG to CDC so boot messages appear in /dev/ttyACM0.
         * Early pre-enumeration messages are lost (TX FIFO cleared on reset).
         * Messages from app tasks will interleave with usb_shell output —
         * acceptable for debugging; remove this call to suppress. */
        drv_usb_cdc_attach_log();
#endif
    }
#endif /* CONFIG_DUNEOS_DRV_USB_CDC */

    klog_i(TAG, "TinyUSB device stack started");
}

/* -------------------------------------------------------------------------
 * Phase 2: MSC storage backend — called from vfs_dev.c after SD is mounted
 * ---------------------------------------------------------------------- */

void drv_usb_register(void)
{
#ifdef CONFIG_DUNEOS_DRV_USB_MSC
    sdmmc_card_t *card = duneos_vfs_get_sd_card();
    if (!card) {
        klog_w(TAG, "USB MSC enabled but SD card not mounted — MSC unavailable");
        return;
    }

    /* MOUNT_USB: serve raw sectors to the host; vfs.c already owns /sd via
     * esp_vfs_fat_sdspi_mount and we must not attempt to re-register it. */
    tinyusb_msc_storage_config_t msc_cfg = {
        .mount_point       = TINYUSB_MSC_STORAGE_MOUNT_USB,
        .medium.card       = card,
        .fat_fs.base_path  = "/sd",
    };
    esp_err_t err = tinyusb_msc_new_storage_sdmmc(&msc_cfg, &s_msc_hdl);
    if (err != ESP_OK)
        klog_e(TAG, "MSC storage init failed: %s", esp_err_to_name(err));
    else
        klog_i(TAG, "USB MSC: SD card storage registered (raw sector mode)");
#endif
}
