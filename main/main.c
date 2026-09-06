#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/stat.h>

#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#ifdef CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG
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
#include "duneos/meminfo.h"
#include "duneos/ambient.h"

static const char *TAG = "duneos";

static void kernel_idle(void)
{
    klog_i(TAG, "kernel idle");
    while (1) duneos_task_delay_ms(5000);
}

/* console_init() wires stdin/stdout through the USB Serial/JTAG hardware
 * peripheral.  Only compiled when CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y —
 * the SoC has the peripheral and it is the selected console.
 * Plain ESP32 has no USB hardware; TinyUSB CDC boards use a different path. */
#ifdef CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG
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
#endif /* CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG */

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

    /* duneos_init_run() handles immediate vs deferred launches and registers
     * an exit observer for the `after:` dependency mechanism. Returns the
     * total number of scheduled services (immediate + deferred). */
    return duneos_init_run(&cfg);
}

/* LEG-37: main_task runs the whole boot — the LittleFS and SD mounts and the
 * loader scan of every .dap — and an overflow here lands in the adjacent heap,
 * surfacing as TLSF corruption thousands of instructions later rather than as a
 * stack report. The init.yaml launch is the deepest point, so the mark is taken
 * right after it. It is published as ambient state (ADR 027) rather than logged:
 * the margin stays one `free` away instead of costing a line on every boot.
 * Same StackType_t scaling as supervisor.c uses for app slots. */
static void publish_boot_stack_mark(void)
{
    size_t free_bytes = uxTaskGetStackHighWaterMark(NULL) * sizeof(StackType_t);

    ambient_stack_t st = {
        .total = (uint32_t)CONFIG_ESP_MAIN_TASK_STACK_SIZE,
        .peak  = (uint32_t)(CONFIG_ESP_MAIN_TASK_STACK_SIZE - free_bytes),
    };

    mkdir(AMBIENT_STATE_DIR, 0755);
    int fd = open(AMBIENT_STACK_PATH, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) {
        klog_w(TAG, "cannot publish %s: %s", AMBIENT_STACK_PATH, strerror(errno));
        return;
    }
    write(fd, &st, sizeof(st));
    close(fd);
}

/* Legacy single-app boot: scan /sd/apps/, honour /sd/autoboot if present. */
static int launch_autoboot(void)
{
    static duneos_app_info_t apps[DUNEOS_MAX_APPS];
    int count = 0;
    duneos_loader_scan(apps, DUNEOS_MAX_APPS, &count);

    if (count == 0) {
        klog_w(TAG, "no apps found in /bin, /sd/bin or /sd/apps");
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
    /* Baseline before DuneOS does anything → IDF/FreeRTOS startup heap cost. */
    duneos_meminfo_mark(0);

    /* First thing: capture esp_rom_printf into the klog ring — panic/assert
     * output is unreadable otherwise on boards with no UART0 header. */
    klog_capture_rom_output();

#ifdef CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG
    console_init();
#endif

    /* duneos_vfs_init() calls drv_usb_preinit() as its very first action,
     * which brings up TinyUSB and CDC.  Log the banner after that so the
     * message reliably reaches /dev/ttyACM0 in USB CDC console mode. */
    if (duneos_vfs_init() != ESP_OK) {
        klog_e(TAG, "VFS init failed — see the error above; kernel idle");
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

    /* Baseline after kernel init, before any service → DuneOS kernel cost is
     * (entry − kernel); everything after is running services (apps + WiFi). */
    duneos_meminfo_mark(1);

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

    publish_boot_stack_mark();

    /* Block until all services (and anything they spawned) have exited */
    duneos_supervisor_wait_all();
    klog_i(TAG, "all services exited");

    kernel_idle();
}
