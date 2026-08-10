/*
 * DuneOS WiFi STA kernel helper — drv_wifi.c
 *
 * Provides duneos_wifi_* functions exported to userspace apps.
 * No /dev device node: this driver exposes pure-function ABI, not a file.
 *
 * Architecture:
 *   - duneos_wifi_init()      — lazy, idempotent; initialises NVS, netif,
 *                               event loop, and WiFi driver.
 *   - duneos_wifi_sta_connect() — configures STA, starts radio, connects,
 *                               then blocks on a FreeRTOS event group until
 *                               IP_EVENT_STA_GOT_IP fires or timeout.
 *   - Event handlers run in the esp-event task and only touch the event group
 *     and the static net_info cache — no locking needed for those writes.
 *   - A portMUX spinlock guards s_initialized and s_connecting against
 *     concurrent calls from different app slots (rare but possible).
 */

#include "duneos/wifi.h"
#include "duneos/klog.h"

#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_event.h"
#include "esp_heap_caps.h"
#include "nvs_flash.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/portmacro.h"

#include <stdlib.h>
#include <string.h>
#include <errno.h>

#define TAG "duneos/wifi"

#define EV_CONNECTED  BIT0
#define EV_FAIL       BIT1
#define EV_NETIF_UP   BIT0   /* used in s_netif_evg — set while any IP is valid */

static portMUX_TYPE       s_mux         = portMUX_INITIALIZER_UNLOCKED;
static bool               s_initialized = false;
static bool               s_connecting  = false;
static EventGroupHandle_t s_evg         = NULL;   /* per-connect-attempt signalling */
static EventGroupHandle_t s_netif_evg   = NULL;   /* persistent: set while IP valid */
static duneos_net_info_t  s_info;   /* zero-initialised at BSS */

/* ------------------------------------------------------------------ */
/* Event handlers (run in esp-event task)                             */
/* ------------------------------------------------------------------ */

static void on_wifi_event(void *arg, esp_event_base_t base,
                          int32_t id, void *data)
{
    if (id == WIFI_EVENT_STA_DISCONNECTED) {
        memset(&s_info, 0, sizeof(s_info));
        if (s_netif_evg)
            xEventGroupClearBits(s_netif_evg, EV_NETIF_UP);
        if (s_evg && s_connecting)
            xEventGroupSetBits(s_evg, EV_FAIL);
    }
}

static void on_ip_event(void *arg, esp_event_base_t base,
                        int32_t id, void *data)
{
    if (id != IP_EVENT_STA_GOT_IP) return;

    ip_event_got_ip_t *ev = data;
    snprintf(s_info.ip,      sizeof(s_info.ip),
             IPSTR, IP2STR(&ev->ip_info.ip));
    snprintf(s_info.gw,      sizeof(s_info.gw),
             IPSTR, IP2STR(&ev->ip_info.gw));
    snprintf(s_info.netmask, sizeof(s_info.netmask),
             IPSTR, IP2STR(&ev->ip_info.netmask));

    /* Unblock duneos_netif_wait_ip() callers. */
    if (s_netif_evg)
        xEventGroupSetBits(s_netif_evg, EV_NETIF_UP);
    if (s_evg && s_connecting)
        xEventGroupSetBits(s_evg, EV_CONNECTED);
}

/* ------------------------------------------------------------------ */
/* Public API                                                          */
/* ------------------------------------------------------------------ */

/* WiFi+lwIP bring-up needs ~45 KiB of internal RAM, and the IDF calls
 * below (esp_netif_create_default_wifi_sta in particular) ESP_ERROR_CHECK
 * their allocations — an OOM there is an abort(), not an error return.
 * A daemon poking WiFi during the boot-time launch crunch must get a
 * clean -ENOMEM it can back off from, never take the system down. */
#define WIFI_INIT_MIN_FREE_INTERNAL   (55 * 1024)
#define WIFI_INIT_MIN_LARGEST_BLOCK   (16 * 1024)

int duneos_wifi_init(void)
{
    portENTER_CRITICAL(&s_mux);
    bool already = s_initialized;
    portEXIT_CRITICAL(&s_mux);
    if (already) return 0;

    size_t free_int = heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    size_t largest  = heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (free_int < WIFI_INIT_MIN_FREE_INTERNAL ||
        largest  < WIFI_INIT_MIN_LARGEST_BLOCK) {
        klog_w(TAG, "wifi init deferred: internal RAM tight (%zu free, %zu largest)",
               free_int, largest);
        errno = ENOMEM;
        return -1;
    }

    /* NVS: WiFi calibration data is stored here. */
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES ||
        err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        err = nvs_flash_init();
    }
    if (err != ESP_OK) {
        klog_e(TAG, "nvs_flash_init: %s", esp_err_to_name(err));
        return -1;
    }

    esp_netif_init();

    err = esp_event_loop_create_default();
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        klog_e(TAG, "event loop: %s", esp_err_to_name(err));
        return -1;
    }

    /* Static, not s_initialized: a failure later in this function leaves
     * init incomplete, and the netif/handlers must not be created twice
     * on the retry (duplicate default netif is itself an abort()). */
    static bool s_netif_created = false;
    if (!s_netif_created) {
        esp_netif_create_default_wifi_sta();
        s_netif_created = true;
    }

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    err = esp_wifi_init(&cfg);
    if (err != ESP_OK) {
        klog_e(TAG, "esp_wifi_init: %s", esp_err_to_name(err));
        return -1;
    }

    esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                on_wifi_event, NULL);
    esp_event_handler_register(IP_EVENT,   IP_EVENT_STA_GOT_IP,
                                on_ip_event, NULL);

    esp_wifi_set_mode(WIFI_MODE_STA);

    uint8_t mac[6];
    if (esp_wifi_get_mac(WIFI_IF_STA, mac) == ESP_OK)
        snprintf(s_info.mac, sizeof(s_info.mac),
                 "%02x:%02x:%02x:%02x:%02x:%02x",
                 mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);

    s_evg = xEventGroupCreate();
    if (!s_evg) {
        klog_e(TAG, "xEventGroupCreate failed");
        return -1;
    }

    s_netif_evg = xEventGroupCreate();
    if (!s_netif_evg) {
        klog_e(TAG, "xEventGroupCreate (netif) failed");
        return -1;
    }

    portENTER_CRITICAL(&s_mux);
    s_initialized = true;
    portEXIT_CRITICAL(&s_mux);

    klog_i(TAG, "WiFi driver initialised");
    return 0;
}

int duneos_wifi_sta_connect(const char *ssid, const char *password,
                             uint32_t timeout_ms)
{
    if (!ssid || !password) { errno = EINVAL; return -1; }
    if (duneos_wifi_init() != 0) return -1;

    wifi_config_t wcfg;
    memset(&wcfg, 0, sizeof(wcfg));
    /* Not strlcpy: sta.ssid legitimately holds a full 32-byte SSID and
     * sta.password a full 64-byte raw PSK, both without NUL terminator. */
    memcpy(wcfg.sta.ssid, ssid, strnlen(ssid, sizeof(wcfg.sta.ssid)));
    memcpy(wcfg.sta.password, password,
           strnlen(password, sizeof(wcfg.sta.password)));
    wcfg.sta.threshold.authmode =
        (password[0] != '\0') ? WIFI_AUTH_WPA2_PSK : WIFI_AUTH_OPEN;

    esp_wifi_set_config(WIFI_IF_STA, &wcfg);

    esp_wifi_start();

    portENTER_CRITICAL(&s_mux);
    s_connecting = true;
    portEXIT_CRITICAL(&s_mux);

    /* Clear as late as possible so stale DISCONNECTED events from a previous
     * (aborted) attempt delivered before this point cannot fail this one. */
    xEventGroupClearBits(s_evg, EV_CONNECTED | EV_FAIL);

    esp_err_t cerr = esp_wifi_connect();
    if (cerr != ESP_OK) {
        portENTER_CRITICAL(&s_mux);
        s_connecting = false;
        portEXIT_CRITICAL(&s_mux);
        klog_e(TAG, "esp_wifi_connect: %s", esp_err_to_name(cerr));
        esp_wifi_disconnect();
        return -1;
    }

    TickType_t ticks = (timeout_ms == 0) ? portMAX_DELAY
                                         : pdMS_TO_TICKS(timeout_ms);
    EventBits_t bits = xEventGroupWaitBits(s_evg,
                                           EV_CONNECTED | EV_FAIL,
                                           pdFALSE, pdFALSE, ticks);

    portENTER_CRITICAL(&s_mux);
    s_connecting = false;
    portEXIT_CRITICAL(&s_mux);

    if (bits & EV_CONNECTED) {
        /* Refresh RSSI now that we're confirmed connected. */
        wifi_ap_record_t ap;
        if (esp_wifi_sta_get_ap_info(&ap) == ESP_OK) {
            strlcpy(s_info.ssid, (const char *)ap.ssid, sizeof(s_info.ssid));
            s_info.rssi = ap.rssi;
        }
        klog_i(TAG, "connected  ssid=%s  ip=%s  rssi=%d dBm",
               s_info.ssid, s_info.ip, (int)s_info.rssi);
        return 0;
    }

    if (bits & EV_FAIL)
        klog_w(TAG, "connection refused by AP");
    else
        klog_w(TAG, "connect timeout (%lu ms)", (unsigned long)timeout_ms);

    /* Abort the in-flight attempt: without this a slow association can
     * complete after we reported failure, leaving the radio connected while
     * the caller believes it is down (every later retry then fails). */
    esp_wifi_disconnect();
    return -1;
}

static int cmp_ap_rssi_desc(const void *a, const void *b)
{
    const wifi_ap_record_t *ra = a, *rb = b;
    return (int)rb->rssi - (int)ra->rssi;
}

int duneos_wifi_scan(duneos_wifi_ap_t *out, int max)
{
    if (!out || max <= 0) return -EINVAL;
    if (duneos_wifi_init() != 0) return -EIO;

    /* Scan needs the radio up even when nothing has connected yet;
     * esp_wifi_start() is a no-op if already started. */
    esp_err_t err = esp_wifi_start();
    if (err != ESP_OK) {
        klog_e(TAG, "esp_wifi_start: %s", esp_err_to_name(err));
        return -EIO;
    }

    err = esp_wifi_scan_start(NULL, true);
    if (err == ESP_ERR_WIFI_STATE) return -EBUSY;
    if (err != ESP_OK) {
        klog_e(TAG, "scan_start: %s", esp_err_to_name(err));
        return -EIO;
    }

    uint16_t n = 0;
    esp_wifi_scan_get_ap_num(&n);
    if (n == 0) return 0;

    /* Bound the transient buffer: callers consume at most 16 records and a
     * dense RF environment can report 50+ APs — an unbounded malloc would
     * fragment the small no-PSRAM kernel heap.  2x headroom for the dedup
     * pass; get_ap_records frees the driver's internal list regardless. */
    if (n > 32) n = 32;

    wifi_ap_record_t *recs = malloc((size_t)n * sizeof(*recs));
    if (!recs) {
        esp_wifi_clear_ap_list();
        return -ENOMEM;
    }

    err = esp_wifi_scan_get_ap_records(&n, recs);
    if (err != ESP_OK) {
        klog_e(TAG, "get_ap_records: %s", esp_err_to_name(err));
        free(recs);
        return -EIO;
    }

    /* Compact in place: drop hidden SSIDs, dedup keeping strongest RSSI. */
    uint16_t uniq = 0;
    for (uint16_t i = 0; i < n; i++) {
        if (recs[i].ssid[0] == '\0') continue;
        uint16_t j;
        for (j = 0; j < uniq; j++) {
            if (strcmp((const char *)recs[j].ssid,
                       (const char *)recs[i].ssid) == 0)
                break;
        }
        if (j < uniq) {
            if (recs[i].rssi > recs[j].rssi) recs[j] = recs[i];
        } else {
            recs[uniq++] = recs[i];
        }
    }

    qsort(recs, uniq, sizeof(*recs), cmp_ap_rssi_desc);

    int count = (uniq < (uint16_t)max) ? (int)uniq : max;
    for (int i = 0; i < count; i++) {
        strlcpy(out[i].ssid, (const char *)recs[i].ssid, sizeof(out[i].ssid));
        out[i].rssi     = recs[i].rssi;
        out[i].authmode = (uint8_t)recs[i].authmode;
        out[i].channel  = recs[i].primary;
    }

    free(recs);
    klog_i(TAG, "scan: %u found, %u unique, %d returned",
           (unsigned)n, (unsigned)uniq, count);
    return count;
}

int duneos_wifi_sta_disconnect(void)
{
    if (!s_initialized) return 0;
    esp_wifi_disconnect();
    esp_wifi_stop();
    memset(&s_info, 0, sizeof(s_info));
    klog_i(TAG, "disconnected");
    return 0;
}

int duneos_wifi_get_info(duneos_net_info_t *info)
{
    if (!info) { errno = EINVAL; return -1; }
    if (!s_info.ip[0]) return -1;

    *info = s_info;
    /* Refresh RSSI inline — cheap register read. */
    wifi_ap_record_t ap;
    if (esp_wifi_sta_get_ap_info(&ap) == ESP_OK) {
        info->rssi = ap.rssi;
        s_info.rssi = ap.rssi;
    }
    return 0;
}

int duneos_netif_wait_ip(uint32_t timeout_ms)
{
    /* Fast path: already have an IP. */
    if (s_info.ip[0]) return 0;

    /* WiFi subsystem not yet initialised — caller must start a network daemon
     * before calling this function.  Return immediately rather than hanging. */
    if (!s_initialized || !s_netif_evg) return -1;

    TickType_t ticks = (timeout_ms == 0) ? portMAX_DELAY
                                         : pdMS_TO_TICKS(timeout_ms);
    EventBits_t bits = xEventGroupWaitBits(s_netif_evg, EV_NETIF_UP,
                                           pdFALSE, pdFALSE, ticks);
    return (bits & EV_NETIF_UP) ? 0 : -1;
}
