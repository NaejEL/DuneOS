#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <fcntl.h>

#include "esp_err.h"
#ifndef CONFIG_ESP_CONSOLE_USB_CDC
#include "driver/usb_serial_jtag.h"
#include "driver/usb_serial_jtag_vfs.h"
#endif
#include "duneos/klog.h"
#include "duneos/abi.h"
#include "duneos/task.h"
#include "duneos/vfs.h"
#include "duneos/loader.h"
#include "duneos/supervisor.h"
#include "duneos/init.h"

static const char *TAG = "duneos";

static void kernel_idle(void)
{
    klog_i(TAG, "kernel idle");
    while (1) duneos_task_delay_ms(5000);
}

/* console_init() wires stdin/stdout through the USB Serial/JTAG hardware
 * peripheral (GPIO19/20 in JTAG mode).  Skip it when TinyUSB OTG is active
 * — they share the same D+/D- pins and the JTAG driver would claim the mux
 * before TinyUSB can enumerate.  With CONFIG_ESP_CONSOLE_USB_CDC=y the
 * _write() hook already routes printf through TinyUSB CDC automatically. */
#ifndef CONFIG_ESP_CONSOLE_USB_CDC
static void console_init(void)
{
    usb_serial_jtag_driver_config_t cfg = USB_SERIAL_JTAG_DRIVER_CONFIG_DEFAULT();
    esp_err_t err = usb_serial_jtag_driver_install(&cfg);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        klog_e(TAG, "usb_serial_jtag_driver_install: %s", esp_err_to_name(err));
        return;
    }
    usb_serial_jtag_vfs_use_driver();
    usb_serial_jtag_vfs_set_rx_line_endings(ESP_LINE_ENDINGS_CR);
    usb_serial_jtag_vfs_set_tx_line_endings(ESP_LINE_ENDINGS_CRLF);
    fcntl(fileno(stdin),  F_SETFL, 0);
    fcntl(fileno(stdout), F_SETFL, 0);
}
#endif

/* Launch all services defined in /flash/init.yaml (and /sd/init.yaml).
 * Returns the number of services successfully started, or -1 on config error. */
static int launch_from_init_yaml(void)
{
    duneos_init_config_t cfg;
    int rc = duneos_init_load(&cfg);

    if (rc == -ENOENT) {
        klog_i(TAG, "no init.yaml — using autoboot fallback");
        return -1;
    }
    if (rc != 0) {
        klog_w(TAG, "init.yaml load error: %s — using autoboot fallback",
               strerror(-rc));
        return -1;
    }

    int launched = 0;
    for (int i = 0; i < cfg.count; i++) {
        klog_i(TAG, "starting service '%s' (restart=%d)",
               cfg.services[i].path, (int)cfg.services[i].restart);
        esp_err_t err = duneos_supervisor_launch_policy(cfg.services[i].path,
                                                        cfg.services[i].restart);
        if (err != ESP_OK)
            klog_e(TAG, "failed to start '%s': %s",
                   cfg.services[i].path, esp_err_to_name(err));
        else
            launched++;
    }
    return launched;
}

/* Legacy single-app boot: scan /sd/apps/, honour /sd/autoboot if present. */
static int launch_autoboot(void)
{
    static duneos_app_info_t apps[DUNEOS_MAX_APPS];
    int count = 0;
    duneos_loader_scan(apps, DUNEOS_MAX_APPS, &count);

    if (count == 0) {
        klog_w(TAG, "no apps found in /flash/bin, /sd/bin or /sd/apps");
        return 0;
    }

    const duneos_app_info_t *target = duneos_loader_select(apps, count);
    klog_i(TAG, "autoboot: launching '%s' v%s",
           target->meta.name, target->meta.version);

    esp_err_t err = duneos_supervisor_launch(target->path);
    if (err != ESP_OK) {
        klog_e(TAG, "launch failed: %s", esp_err_to_name(err));
        return 0;
    }
    return 1;
}

void app_main(void)
{
#ifndef CONFIG_ESP_CONSOLE_USB_CDC
    /* USB Serial/JTAG VFS: wires stdin/stdout to the JTAG peripheral.
     * Skipped when TinyUSB OTG is the console — they share GPIO19/20 and
     * the JTAG driver would claim the USB mux before TinyUSB can enumerate. */
    console_init();
#endif

    /* duneos_vfs_init() calls drv_usb_preinit() as its very first action,
     * which brings up TinyUSB and CDC.  Log the banner after that so the
     * message reliably reaches /dev/ttyACM0 in USB CDC console mode. */
    if (duneos_vfs_init() != ESP_OK) {
        klog_e(TAG, "VFS init failed — sysbin partition missing or corrupt");
        kernel_idle();
        return;
    }

    klog_i(TAG, "DuneOS " DUNEOS_VERSION_STRING " (ABI v%d)", DUNEOS_ABI_VERSION);

    duneos_loader_init();

    if (duneos_supervisor_init() != ESP_OK) {
        klog_e(TAG, "supervisor init failed");
        kernel_idle();
        return;
    }

    int launched = launch_from_init_yaml();
    if (launched < 0) {
        /* init.yaml absent or unparseable — fall back to legacy autoboot */
        launched = launch_autoboot();
    }

    if (launched == 0) {
        klog_w(TAG, "nothing to run");
        kernel_idle();
        return;
    }

    /* Block until all services (and anything they spawned) have exited */
    duneos_supervisor_wait_all();
    klog_i(TAG, "all services exited");

    kernel_idle();
}
