#include "duneos/vfs.h"
#include "duneos/klog.h"

#include "esp_vfs_fat.h"
#include "esp_littlefs.h"
#include "esp_partition.h"
#include "driver/spi_master.h"
#include "driver/gpio.h"
#include "sdmmc_cmd.h"

#include "board_config.h"

#include <fcntl.h>
#include <unistd.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <errno.h>

/* DUNEOS_HAS_SD defaults to 1 (SD present) if not set by bspgen */
#ifndef DUNEOS_HAS_SD
#define DUNEOS_HAS_SD 1
#endif

static const char *TAG = "duneos/vfs";

/* LittleFS is the root filesystem ("/"). esp_vfs uses "" as the base path for a
 * root mount; the sub-mounts (/sd, /tmp, /dev) still win via longest-prefix
 * matching. FLASH_MOUNT_POINT is the prefix used for path building (so
 * FLASH_MOUNT_POINT "/bin" == "/bin"); FLASH_MOUNT_NAME is the registry/log name. */
#define FLASH_MOUNT_POINT   ""
#define FLASH_MOUNT_NAME    "/"
#define FLASH_PARTITION     "sysbin"
#define DATA_MOUNT_POINT    "/data"
#define DATA_PARTITION      "userdata"
#define SD_MOUNT_POINT      "/sd"
#define SD_MAX_FILES        8

static sdmmc_card_t *s_card          = NULL;
static bool          s_initialized   = false;
static bool          s_flash_mounted = false;
static bool          s_data_mounted  = false;
static bool          s_sd_mounted    = false;

/* -------------------------------------------------------------------------
 * Mount registry — tracks all active VFS mount points for ls /
 * ---------------------------------------------------------------------- */

#define MOUNT_REGISTRY_MAX 8
#define MOUNT_PATH_MAX     32

static char s_mount_paths[MOUNT_REGISTRY_MAX][MOUNT_PATH_MAX];
static int  s_mount_count = 0;

static void register_mount(const char *path)
{
    if (s_mount_count >= MOUNT_REGISTRY_MAX) return;
    strncpy(s_mount_paths[s_mount_count], path, MOUNT_PATH_MAX - 1);
    s_mount_paths[s_mount_count][MOUNT_PATH_MAX - 1] = '\0';
    s_mount_count++;
}

int duneos_vfs_list_mounts(char (*out)[32], int max)
{
    int n = s_mount_count < max ? s_mount_count : max;
    for (int i = 0; i < n; i++) {
        strncpy(out[i], s_mount_paths[i], 31);
        out[i][31] = '\0';
    }
    return n;
}

/* Total + free bytes of the filesystem backing `path` (for `df`). 0 on success,
 * -errno otherwise. /tmp and /dev have no block accounting (-ENODEV). */
int duneos_fs_info(const char *path, uint64_t *total, uint64_t *freeb)
{
    if (!path || !total || !freeb) return -EINVAL;

    /* /sd (FAT) and the overlay mounts are matched first; everything else —
     * "/", "/bin", "/etc"… — is backed by the root LittleFS. */
    if (strncmp(path, DATA_MOUNT_POINT, sizeof(DATA_MOUNT_POINT) - 1) == 0) {
        size_t t = 0, u = 0;
        if (!s_data_mounted) return -ENODEV;
        if (esp_littlefs_info(DATA_PARTITION, &t, &u) != ESP_OK) return -EIO;
        *total = t;
        *freeb = (t > u) ? (uint64_t)(t - u) : 0;
        return 0;
    }
    if (strncmp(path, SD_MOUNT_POINT, sizeof(SD_MOUNT_POINT) - 1) == 0) {
        uint64_t t = 0, f = 0;
        if (esp_vfs_fat_info(SD_MOUNT_POINT, &t, &f) != ESP_OK) return -EIO;
        *total = t;
        *freeb = f;
        return 0;
    }
    if (strncmp(path, "/tmp", 4) == 0 || strncmp(path, "/dev", 4) == 0)
        return -ENODEV;

    size_t t = 0, u = 0;
    if (esp_littlefs_info(FLASH_PARTITION, &t, &u) != ESP_OK) return -EIO;
    *total = t;
    *freeb = (t > u) ? (uint64_t)(t - u) : 0;
    return 0;
}

/* -------------------------------------------------------------------------
 * Public availability queries
 * ---------------------------------------------------------------------- */

bool          duneos_vfs_flash_available(void) { return s_flash_mounted; }
bool          duneos_vfs_data_available(void)  { return s_data_mounted;  }
bool          duneos_vfs_sd_available(void)    { return s_sd_mounted;    }
sdmmc_card_t *duneos_vfs_get_sd_card(void)     { return s_card;          }

/* -------------------------------------------------------------------------
 * Flash (LittleFS)
 * ---------------------------------------------------------------------- */

esp_err_t duneos_vfs_mount_flash(void)
{
    esp_vfs_littlefs_conf_t conf = {
        .base_path              = FLASH_MOUNT_POINT,
        .partition_label        = FLASH_PARTITION,
        .format_if_mount_failed = true,
        .dont_mount             = false,
    };

    esp_err_t err = esp_vfs_littlefs_register(&conf);
    if (err != ESP_OK) {
        /* esp_littlefs returns ESP_ERR_NOT_FOUND only when
         * esp_partition_find_first() came back NULL, i.e. the partition table
         * has no such entry — which says nothing about the contents of the
         * flash. Reporting that as corruption sends the reader looking for a
         * bad image instead of a bad table or a failed flash mmap. A genuinely
         * unreadable filesystem surfaces as an LFS error (-84 and friends),
         * and format_if_mount_failed then reformats it. */
        if (err == ESP_ERR_NOT_FOUND) {
            klog_e(TAG, "no '" FLASH_PARTITION "' partition in the partition "
                        "table — the entry is missing or could not be mapped");
        } else {
            klog_e(TAG, "'" FLASH_PARTITION "' found but LittleFS mount "
                        "failed: %s", esp_err_to_name(err));
        }
        return err;
    }

    s_flash_mounted = true;
    register_mount(FLASH_MOUNT_NAME);
    klog_i(TAG, "LittleFS mounted at " FLASH_MOUNT_NAME " (root)");
    return ESP_OK;
}

/* -------------------------------------------------------------------------
 * User data (LittleFS) — app-writable state that survives a reflash
 *
 * `dbt system flash` regenerates the whole /flash image, so anything an app
 * writes there (wifi known.yaml, scores, prefs) dies on every firmware
 * update. /data lives on its own `userdata` partition that no flashing flow
 * touches. Formatted on first mount (the partition ships as raw/FAT space
 * on tables generated before 2026-07); absent partition = old table, non-
 * fatal, apps fall back to /flash.
 * ---------------------------------------------------------------------- */

static esp_err_t duneos_vfs_mount_data(void)
{
    if (esp_partition_find_first(ESP_PARTITION_TYPE_DATA,
                                 ESP_PARTITION_SUBTYPE_ANY,
                                 DATA_PARTITION) == NULL) {
        klog_w(TAG, "no '" DATA_PARTITION "' partition — /data unavailable "
                    "(re-run bspgen + reflash kernel to get one)");
        return ESP_ERR_NOT_FOUND;
    }

    esp_vfs_littlefs_conf_t conf = {
        .base_path              = DATA_MOUNT_POINT,
        .partition_label        = DATA_PARTITION,
        .format_if_mount_failed = true,
        .dont_mount             = false,
    };

    esp_err_t err = esp_vfs_littlefs_register(&conf);
    if (err != ESP_OK) {
        klog_e(TAG, "/data mount failed: %s", esp_err_to_name(err));
        return err;
    }

    s_data_mounted = true;
    register_mount(DATA_MOUNT_POINT);
    klog_i(TAG, "LittleFS mounted at " DATA_MOUNT_POINT);
    return ESP_OK;
}

/* -------------------------------------------------------------------------
 * First-boot provisioning
 * ---------------------------------------------------------------------- */

esp_err_t duneos_vfs_provision_flash(void)
{
    if (g_duneos_blob_count == 0) return ESP_OK;

    /* Ensure /bin exists */
    struct stat st;
    if (stat(FLASH_MOUNT_POINT "/bin", &st) != 0) {
        if (mkdir(FLASH_MOUNT_POINT "/bin", 0755) != 0 && errno != EEXIST) {
            klog_w(TAG, "cannot create /bin: %d", errno);
            return ESP_FAIL;
        }
    }

    for (int i = 0; i < g_duneos_blob_count; i++) {
        char path[64];
        snprintf(path, sizeof(path), FLASH_MOUNT_POINT "/bin/%s",
                 g_duneos_blobs[i].name);

        if (stat(path, &st) == 0) continue;  /* already provisioned */

        int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
        if (fd < 0) {
            klog_w(TAG, "cannot create %s: %d", path, errno);
            continue;
        }
        ssize_t written = write(fd, g_duneos_blobs[i].data,
                                g_duneos_blobs[i].size);
        close(fd);

        if (written != (ssize_t)g_duneos_blobs[i].size) {
            klog_w(TAG, "partial write to %s", path);
        } else {
            klog_i(TAG, "provisioned %s (%u B)", path,
                   (unsigned)g_duneos_blobs[i].size);
        }
    }

    return ESP_OK;
}

/* -------------------------------------------------------------------------
 * SD card (FatFS over SPI)
 * ---------------------------------------------------------------------- */

#if DUNEOS_HAS_SD
esp_err_t duneos_vfs_mount_sd(void)
{
    sdmmc_host_t host = SDSPI_HOST_DEFAULT();
    host.slot = DUNEOS_SD_SPI_HOST;

    spi_bus_config_t bus_cfg = {
        .mosi_io_num     = DUNEOS_SD_MOSI_PIN,
        .miso_io_num     = DUNEOS_SD_MISO_PIN,
        .sclk_io_num     = DUNEOS_SD_CLK_PIN,
        .quadwp_io_num   = -1,
        .quadhd_io_num   = -1,
        .max_transfer_sz = 4096,
    };

    esp_err_t err = spi_bus_initialize(DUNEOS_SD_SPI_HOST, &bus_cfg,
                                       SDSPI_DEFAULT_DMA);
    if (err != ESP_OK) {
        klog_e(TAG, "SPI bus init failed: %s", esp_err_to_name(err));
        return err;
    }

    sdspi_device_config_t slot_cfg = SDSPI_DEVICE_CONFIG_DEFAULT();
    slot_cfg.gpio_cs = DUNEOS_SD_CS_PIN;
    slot_cfg.host_id = DUNEOS_SD_SPI_HOST;
#if DUNEOS_SD_CD_PIN >= 0
    slot_cfg.gpio_cd = DUNEOS_SD_CD_PIN;
#endif

    esp_vfs_fat_sdmmc_mount_config_t mount_cfg = {
        .format_if_mount_failed = false,
        .max_files              = SD_MAX_FILES,
        .allocation_unit_size   = 16 * 1024,
    };

    err = esp_vfs_fat_sdspi_mount(SD_MOUNT_POINT, &host, &slot_cfg,
                                   &mount_cfg, &s_card);
    if (err != ESP_OK) {
        klog_e(TAG, "SD mount failed: %s", esp_err_to_name(err));
        if (err == ESP_FAIL)
            klog_e(TAG, "Is a FAT-formatted SD card inserted?");
        spi_bus_free(DUNEOS_SD_SPI_HOST);
        return err;
    }

    s_sd_mounted = true;
    register_mount(SD_MOUNT_POINT);
    klog_i(TAG, "SD mounted at %s — %s %.1f GB",
           SD_MOUNT_POINT,
           s_card->cid.name,
           (double)((uint64_t)s_card->csd.capacity * s_card->csd.sector_size)
               / (1024.0 * 1024.0 * 1024.0));
    return ESP_OK;
}
#endif /* DUNEOS_HAS_SD */

/* -------------------------------------------------------------------------
 * board.info — written at boot so apps can discover hardware capabilities
 * ---------------------------------------------------------------------- */

#ifdef DUNEOS_HAVE_DISPLAY
static void write_board_info(const char *path)
{
    int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) return;

    char buf[768];
    int n = snprintf(buf, sizeof(buf),
        "board: " DUNEOS_BOARD_NAME "\n"
        "display: st7789\n"
        "width: %u\n"
        "height: %u\n"
        "fb: %s\n"
        "display_spi_host: %d\n"
        "display_mosi: %d\n"
        "display_clk: %d\n"
        "display_cs: %d\n"
        "display_dc: %d\n"
        "display_rst: %d\n"
        "display_bl: %d\n"
        "display_freq_hz: %u\n"
        "display_rotation: %u\n"
        "display_madctl: %u\n"
        "display_swap_xy: %u\n"
        "display_col_offset: %u\n"
        "display_row_offset: %u\n"
        "display_bus_shared: %s\n",
        (unsigned)DUNEOS_DISPLAY_WIDTH,
        (unsigned)DUNEOS_DISPLAY_HEIGHT,
#ifdef CONFIG_DUNEOS_DRV_FB
        "true",
#else
        "false",
#endif
        (int)DUNEOS_DISPLAY_SPI_HOST,
        (int)DUNEOS_DISPLAY_MOSI_PIN,
        (int)DUNEOS_DISPLAY_CLK_PIN,
        (int)DUNEOS_DISPLAY_CS_PIN,
        (int)DUNEOS_DISPLAY_DC_PIN,
        (int)DUNEOS_DISPLAY_RST_PIN,
        (int)DUNEOS_DISPLAY_BL_PIN,
        (unsigned)DUNEOS_DISPLAY_FREQ_HZ,
        (unsigned)DUNEOS_DISPLAY_ROTATION,
        (unsigned)DUNEOS_DISPLAY_MADCTL,
        (unsigned)DUNEOS_DISPLAY_SWAP_XY,
        (unsigned)DUNEOS_DISPLAY_COL_OFFSET,
        (unsigned)DUNEOS_DISPLAY_ROW_OFFSET,
#if DUNEOS_DISPLAY_BUS_SHARED
        "true"
#else
        "false"
#endif
    );
    write(fd, buf, n);

#ifdef CONFIG_DUNEOS_DRV_I2C
    /* I2C0 pins, so apps (e.g. i2cscope's bus sniffer) can find SCL/SDA at
     * runtime instead of hardcoding board-specific GPIO numbers. */
    n = snprintf(buf, sizeof(buf),
        "i2c0_scl: %d\n"
        "i2c0_sda: %d\n",
        (int)DUNEOS_I2C0_SCL_PIN, (int)DUNEOS_I2C0_SDA_PIN);
    write(fd, buf, n);
#endif

    close(fd);
}
#endif

/* duneos_vfs_mount_tmp() lives in vfs_tmp.c */
/* duneos_vfs_mount_dev() lives in vfs_dev.c */

/* -------------------------------------------------------------------------
 * Top-level init / deinit
 * ---------------------------------------------------------------------- */

#if defined(CONFIG_DUNEOS_DRV_USB_MSC) || defined(CONFIG_DUNEOS_DRV_USB_CDC)
/* Declared in drv_usb.c — compiled only when USB drivers are enabled. */
extern void drv_usb_preinit(void);
#endif

esp_err_t duneos_vfs_init(void)
{
    if (s_initialized) return ESP_ERR_INVALID_STATE;

#if defined(CONFIG_DUNEOS_DRV_USB_MSC) || defined(CONFIG_DUNEOS_DRV_USB_CDC)
    /* Must be first: with CONFIG_ESP_CONSOLE_USB_CDC=y the _write() hook routes
     * printf/klog through TinyUSB CDC.  TinyUSB must be up before the first
     * console write or those bytes are silently dropped (pre-init) or crash. */
    drv_usb_preinit();
#endif

    /* Flash is the primary storage — required for boot */
    esp_err_t err = duneos_vfs_mount_flash();
    if (err != ESP_OK) return err;

    /* Copy firmware-embedded blobs to /flash/bin/ on first boot */
    duneos_vfs_provision_flash();

    /* /data is best-effort — old partition tables simply don't have it */
    duneos_vfs_mount_data();

#if DUNEOS_HAS_SD
    /* SD is secondary — failure is non-fatal (flash is sufficient) */
    err = duneos_vfs_mount_sd();
    if (err != ESP_OK)
        klog_w(TAG, "SD unavailable — running from root LittleFS only");
#endif

    err = duneos_vfs_mount_tmp();
    if (err != ESP_OK) return err;
    register_mount("/tmp");

    err = duneos_vfs_mount_dev();
    if (err != ESP_OK) return err;
    register_mount("/dev");

    s_initialized = true;

#ifdef DUNEOS_HAVE_DISPLAY
    write_board_info(FLASH_MOUNT_POINT "/board.info");
    if (s_sd_mounted)
        write_board_info(SD_MOUNT_POINT "/board.info");
#endif

    klog_i(TAG, "VFS ready (/ %s%s/tmp /dev)",
           s_data_mounted ? "/data " : "",
           s_sd_mounted ? "/sd " : "");
    return ESP_OK;
}

esp_err_t duneos_vfs_deinit(void)
{
    if (!s_initialized) return ESP_ERR_INVALID_STATE;

#if DUNEOS_HAS_SD
    if (s_sd_mounted) {
        esp_vfs_fat_sdcard_unmount(SD_MOUNT_POINT, s_card);
        spi_bus_free(DUNEOS_SD_SPI_HOST);
        s_card       = NULL;
        s_sd_mounted = false;
    }
#endif

    if (s_data_mounted) {
        esp_vfs_littlefs_unregister(DATA_PARTITION);
        s_data_mounted = false;
    }

    if (s_flash_mounted) {
        esp_vfs_littlefs_unregister(FLASH_PARTITION);
        s_flash_mounted = false;
    }

    s_initialized = false;
    klog_i(TAG, "VFS unmounted");
    return ESP_OK;
}
