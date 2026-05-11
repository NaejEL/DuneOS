/*
 * DuneOS devfs — /dev virtual filesystem.
 *
 * Devices:
 *   /dev/null   — discard all writes; reads return EOF immediately
 *   /dev/zero   — reads return 0x00 bytes; writes discarded
 *   /dev/uart0  — UART0 hardware (115200 8N1); maps to uart_read/write_bytes
 *   /dev/klog   — kernel ring buffer (read-only); each open starts from oldest
 *
 * UART0 note: the driver is installed for buffered /dev/uart0 I/O, but fds
 * 0/1/2 (stdin/stdout/stderr) are intentionally left on whatever ESP-IDF
 * configured as the console (USB-JTAG/CDC on CardPuter and most ESP32-S3
 * boards).  The shell therefore reads/writes the interactive terminal on the
 * USB port, not the physical UART0 pins.
 */

#include "duneos/vfs.h"
#include "duneos/klog.h"
#include "duneos/gpio_ioctl.h"

#include "esp_vfs.h"
#include "driver/uart_vfs.h"
#include "driver/uart.h"
#include "hal/uart_types.h"
#include "driver/gpio.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

#include <string.h>
#include <stdlib.h>
#include <stdarg.h>
#include <errno.h>
#include <fcntl.h>
#include <dirent.h>
#include <sys/stat.h>

#define DEVFS_MAX_FDS         16
#define UART0_RX_BUF          2048
#define UART0_BAUD            115200
#define UART0_READ_TIMEOUT_MS 20

static const char *TAG = "duneos/devfs";

typedef enum {
    DEV_NONE     = 0,
    DEV_NULL,
    DEV_ZERO,
    DEV_UART0,
    DEV_KLOG,
    DEV_GPIOCHIP,
} dev_type_t;

typedef struct {
    dev_type_t type;
    int        oflags;
    bool       open;
    size_t     klog_pos;  /* absolute read position, DEV_KLOG only */
    uint8_t    chip;      /* gpiochip index, DEV_GPIOCHIP only     */
} devfs_fd_t;

static devfs_fd_t        s_fds[DEVFS_MAX_FDS];
static SemaphoreHandle_t s_lock;
static bool              s_uart0_installed = false;

/* ----- fd helpers -------------------------------------------------------- */

static int fd_alloc(dev_type_t type, int oflags, size_t klog_pos, uint8_t chip)
{
    for (int i = 0; i < DEVFS_MAX_FDS; i++) {
        if (!s_fds[i].open) {
            s_fds[i].type     = type;
            s_fds[i].oflags   = oflags;
            s_fds[i].open     = true;
            s_fds[i].klog_pos = klog_pos;
            s_fds[i].chip     = chip;
            return i;
        }
    }
    return -1;
}

/* ----- UART0 init -------------------------------------------------------- */

static esp_err_t uart0_ensure_driver(void)
{
    if (s_uart0_installed) return ESP_OK;

    uart_config_t cfg = {
        .baud_rate  = UART0_BAUD,
        .data_bits  = UART_DATA_8_BITS,
        .parity     = UART_PARITY_DISABLE,
        .stop_bits  = UART_STOP_BITS_1,
        .flow_ctrl  = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };

    esp_err_t err = uart_driver_install(UART_NUM_0, UART0_RX_BUF, 0, 0, NULL, 0);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        klog_e(TAG, "uart_driver_install: %s", esp_err_to_name(err));
        return err;
    }

    err = uart_param_config(UART_NUM_0, &cfg);
    if (err != ESP_OK) {
        klog_e(TAG, "uart_param_config: %s", esp_err_to_name(err));
        return err;
    }

    s_uart0_installed = true;
    klog_i(TAG, "UART0 driver installed (%d baud) — /dev/uart0 ready", UART0_BAUD);
    return ESP_OK;
}

/* ----- VFS callbacks ----------------------------------------------------- */

static int devfs_open(const char *path, int flags, int mode)
{
    (void)mode;

    dev_type_t type;
    size_t     klog_pos = 0;

    uint8_t chip = 0;

    if      (strcmp(path, "/null")      == 0) type = DEV_NULL;
    else if (strcmp(path, "/zero")      == 0) type = DEV_ZERO;
    else if (strcmp(path, "/uart0")     == 0) type = DEV_UART0;
    else if (strcmp(path, "/klog")      == 0) { type = DEV_KLOG; klog_pos = klog_ring_oldest(); }
    else if (strcmp(path, "/gpiochip0") == 0) { type = DEV_GPIOCHIP; chip = 0; }
    else { errno = ENOENT; return -1; }

    if (type == DEV_UART0) {
        esp_err_t err = uart0_ensure_driver();
        if (err != ESP_OK) { errno = EIO; return -1; }
    }

    xSemaphoreTake(s_lock, portMAX_DELAY);
    int fd = fd_alloc(type, flags, klog_pos, chip);
    xSemaphoreGive(s_lock);

    if (fd < 0) { errno = EMFILE; return -1; }
    return fd;
}

static int devfs_close(int fd)
{
    if (fd < 0 || fd >= DEVFS_MAX_FDS || !s_fds[fd].open) {
        errno = EBADF;
        return -1;
    }
    s_fds[fd].open = false;
    s_fds[fd].type = DEV_NONE;
    return 0;
}

static ssize_t devfs_read(int fd, void *buf, size_t len)
{
    if (fd < 0 || fd >= DEVFS_MAX_FDS || !s_fds[fd].open) {
        errno = EBADF;
        return -1;
    }
    switch (s_fds[fd].type) {
    case DEV_NULL:
        return 0;
    case DEV_ZERO:
        memset(buf, 0, len);
        return (ssize_t)len;
    case DEV_UART0: {
        int n = uart_read_bytes(UART_NUM_0, buf, len,
                                pdMS_TO_TICKS(UART0_READ_TIMEOUT_MS));
        if (n < 0) { errno = EIO; return -1; }
        return (ssize_t)n;
    }
    case DEV_KLOG: {
        size_t n = klog_ring_read(&s_fds[fd].klog_pos, buf, len);
        return (ssize_t)n;  /* 0 = caught up (EOF-like for one-shot readers) */
    }
    default:
        errno = EBADF;
        return -1;
    }
}

static ssize_t devfs_write(int fd, const void *buf, size_t len)
{
    if (fd < 0 || fd >= DEVFS_MAX_FDS || !s_fds[fd].open) {
        errno = EBADF;
        return -1;
    }
    switch (s_fds[fd].type) {
    case DEV_NULL:
    case DEV_ZERO:
        return (ssize_t)len;
    case DEV_UART0: {
        int n = uart_write_bytes(UART_NUM_0, buf, len);
        if (n < 0) { errno = EIO; return -1; }
        return (ssize_t)n;
    }
    case DEV_KLOG:
        errno = EBADF;  /* read-only device */
        return -1;
    default:
        errno = EBADF;
        return -1;
    }
}

static off_t devfs_lseek(int fd, off_t offset, int whence)
{
    (void)fd; (void)offset; (void)whence;
    errno = ESPIPE;
    return (off_t)-1;
}

/* ----- directory listing ------------------------------------------------- */

static const char *s_dev_entries[] = {
    "null", "zero", "uart0", "klog", "gpiochip0",
};
#define DEV_ENTRY_COUNT  (sizeof(s_dev_entries) / sizeof(s_dev_entries[0]))

typedef struct {
    DIR    dir;     /* must be first — VFS casts DIR* to our struct */
    size_t idx;
} devfs_dir_t;

static DIR *devfs_opendir(const char *name)
{
    (void)name;
    devfs_dir_t *d = calloc(1, sizeof(devfs_dir_t));
    if (!d) { errno = ENOMEM; return NULL; }
    d->idx = 0;
    return (DIR *)d;
}

static struct dirent *devfs_readdir(DIR *pdir)
{
    devfs_dir_t *d = (devfs_dir_t *)pdir;
    if (d->idx >= DEV_ENTRY_COUNT) return NULL;
    static struct dirent ent;
    ent.d_type = DT_CHR;
    strncpy(ent.d_name, s_dev_entries[d->idx++], sizeof(ent.d_name) - 1);
    ent.d_name[sizeof(ent.d_name) - 1] = '\0';
    return &ent;
}

static int devfs_closedir(DIR *pdir)
{
    free(pdir);
    return 0;
}

/* ----- ioctl ------------------------------------------------------------- */

static int devfs_ioctl(int fd, int cmd, va_list args)
{
    if (fd < 0 || fd >= DEVFS_MAX_FDS || !s_fds[fd].open) {
        errno = EBADF;
        return -1;
    }
    if (s_fds[fd].type != DEV_GPIOCHIP) {
        errno = ENOTTY;
        return -1;
    }

    void *arg = va_arg(args, void *);

    switch (cmd) {

    case GPIOCHIP_GET_INFO: {
        gpiochip_info_t *info = (gpiochip_info_t *)arg;
        if (!info) { errno = EINVAL; return -1; }
        snprintf(info->name, sizeof(info->name), "gpiochip%u", (unsigned)s_fds[fd].chip);
        info->lines = (uint8_t)GPIO_NUM_MAX;
        return 0;
    }

    case GPIOCHIP_SET_DIR: {
        gpio_req_t *req = (gpio_req_t *)arg;
        if (!req || req->line >= GPIO_NUM_MAX) { errno = EINVAL; return -1; }
        gpio_mode_t mode = (req->dir == GPIO_DIR_OUTPUT)
                           ? GPIO_MODE_OUTPUT : GPIO_MODE_INPUT;
        if (gpio_set_direction((gpio_num_t)req->line, mode) != ESP_OK) {
            errno = EIO; return -1;
        }
        return 0;
    }

    case GPIOCHIP_GET_VALUE: {
        gpio_req_t *req = (gpio_req_t *)arg;
        if (!req || req->line >= GPIO_NUM_MAX) { errno = EINVAL; return -1; }
        req->val = (uint8_t)gpio_get_level((gpio_num_t)req->line);
        return 0;
    }

    case GPIOCHIP_SET_VALUE: {
        gpio_req_t *req = (gpio_req_t *)arg;
        if (!req || req->line >= GPIO_NUM_MAX) { errno = EINVAL; return -1; }
        if (gpio_set_level((gpio_num_t)req->line, req->val ? 1 : 0) != ESP_OK) {
            errno = EIO; return -1;
        }
        return 0;
    }

    case GPIOCHIP_SET_PULL: {
        gpio_req_t *req = (gpio_req_t *)arg;
        if (!req || req->line >= GPIO_NUM_MAX) { errno = EINVAL; return -1; }
        gpio_pull_mode_t pull;
        switch (req->pull) {
        case GPIO_PULL_UP:     pull = GPIO_PULLUP_ONLY;     break;
        case GPIO_PULL_DOWN:   pull = GPIO_PULLDOWN_ONLY;   break;
        case GPIO_PULL_UPDOWN: pull = GPIO_PULLUP_PULLDOWN; break;
        default:               pull = GPIO_FLOATING;        break;
        }
        if (gpio_set_pull_mode((gpio_num_t)req->line, pull) != ESP_OK) {
            errno = EIO; return -1;
        }
        return 0;
    }

    case GPIOCHIP_SET_IRQ:
        errno = ENOSYS;
        return -1;

    default:
        errno = EINVAL;
        return -1;
    }
}

static int devfs_fstat(int fd, struct stat *st)
{
    if (fd < 0 || fd >= DEVFS_MAX_FDS || !s_fds[fd].open) {
        errno = EBADF;
        return -1;
    }
    memset(st, 0, sizeof(*st));
    st->st_mode = S_IFCHR | 0444;
    if (s_fds[fd].type != DEV_KLOG) st->st_mode |= 0222;
    return 0;
}

static int devfs_stat(const char *path, struct stat *st)
{
    if (strcmp(path, "/null")      != 0 &&
        strcmp(path, "/zero")      != 0 &&
        strcmp(path, "/uart0")     != 0 &&
        strcmp(path, "/klog")      != 0 &&
        strcmp(path, "/gpiochip0") != 0) {
        errno = ENOENT;
        return -1;
    }
    memset(st, 0, sizeof(*st));
    st->st_mode = S_IFCHR | 0444;
    if (strcmp(path, "/klog") != 0) st->st_mode |= 0222;
    return 0;
}

/* ----- mount ------------------------------------------------------------- */

esp_err_t duneos_vfs_mount_dev(void)
{
    s_lock = xSemaphoreCreateMutex();
    if (!s_lock) return ESP_ERR_NO_MEM;

    static const esp_vfs_t vfs = {
        .flags    = ESP_VFS_FLAG_DEFAULT,
        .open     = devfs_open,
        .close    = devfs_close,
        .read     = devfs_read,
        .write    = devfs_write,
        .lseek    = devfs_lseek,
        .ioctl    = devfs_ioctl,
        .fstat    = devfs_fstat,
        .stat     = devfs_stat,
        .opendir  = devfs_opendir,
        .readdir  = devfs_readdir,
        .closedir = devfs_closedir,
    };

    esp_err_t err = esp_vfs_register("/dev", &vfs, NULL);
    if (err != ESP_OK) {
        klog_e(TAG, "esp_vfs_register failed: %s", esp_err_to_name(err));
        vSemaphoreDelete(s_lock);
        return err;
    }

    uart0_ensure_driver();

    klog_i(TAG, "/dev ready (null zero uart0 klog gpiochip0 — %d GPIO lines)", GPIO_NUM_MAX);
    return ESP_OK;
}
