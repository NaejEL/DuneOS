#include <stdio.h>
#include <fcntl.h>

#include "esp_err.h"
#include "driver/usb_serial_jtag.h"
#include "driver/usb_serial_jtag_vfs.h"
#include "duneos/klog.h"
#include "duneos/abi.h"
#include "duneos/task.h"
#include "duneos/vfs.h"
#include "duneos/loader.h"
#include "duneos/supervisor.h"

static const char *TAG = "duneos";

static void kernel_idle(void)
{
    klog_i(TAG, "kernel idle");
    while (1) duneos_task_delay_ms(5000);
}

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

void app_main(void)
{
    console_init();
    klog_i(TAG, "DuneOS " DUNEOS_VERSION_STRING " (ABI v%d)", DUNEOS_ABI_VERSION);

    if (duneos_vfs_init() != ESP_OK) {
        klog_w(TAG, "VFS init failed — no SD card?");
        kernel_idle();
        return;
    }

    duneos_loader_init();

    if (duneos_supervisor_init() != ESP_OK) {
        klog_e(TAG, "supervisor init failed");
        kernel_idle();
        return;
    }

    /* Scan /sd/apps/ for .elf / .dap files */
    static duneos_app_info_t apps[DUNEOS_MAX_APPS];
    int count = 0;
    duneos_loader_scan(apps, DUNEOS_MAX_APPS, &count);

    if (count == 0) {
        klog_w(TAG, "no apps found in %s", DUNEOS_APPS_DIR);
        kernel_idle();
        return;
    }

    /* Select app — reads /sd/autoboot or defaults to first found */
    const duneos_app_info_t *target = duneos_loader_select(apps, count);
    klog_i(TAG, "launching '%s' v%s",
           target->meta.name, target->meta.version);

    esp_err_t err = duneos_supervisor_launch(target->path);
    if (err != ESP_OK) {
        klog_e(TAG, "launch failed: %s", esp_err_to_name(err));
        kernel_idle();
        return;
    }

    /* Block until the app (and any apps it spawned) finishes */
    duneos_supervisor_wait_all();
    klog_i(TAG, "all apps exited");

    kernel_idle();
}
