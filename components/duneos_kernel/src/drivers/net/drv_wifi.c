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
#include "nvs_flash.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/portmacro.h"

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

int duneos_wifi_init(void)
{
    portENTER_CRITICAL(&s_mux);
    bool already = s_initialized;
    portEXIT_CRITICAL(&s_mux);
    if (already) return 0;

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

    esp_netif_create_default_wifi_sta();

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

    xEventGroupClearBits(s_evg, EV_CONNECTED | EV_FAIL);

    wifi_config_t wcfg;
    memset(&wcfg, 0, sizeof(wcfg));
    strlcpy((char *)wcfg.sta.ssid,     ssid,     sizeof(wcfg.sta.ssid));
    strlcpy((char *)wcfg.sta.password, password, sizeof(wcfg.sta.password));
    wcfg.sta.threshold.authmode =
        (password[0] != '\0') ? WIFI_AUTH_WPA2_PSK : WIFI_AUTH_OPEN;

    esp_wifi_set_config(WIFI_IF_STA, &wcfg);

    portENTER_CRITICAL(&s_mux);
    s_connecting = true;
    portEXIT_CRITICAL(&s_mux);

    esp_wifi_start();
    esp_wifi_connect();

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

    return -1;
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
