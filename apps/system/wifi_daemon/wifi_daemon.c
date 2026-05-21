/*
 * wifi_daemon — DuneOS WiFi connection daemon.
 *
 * Reads /etc/wifi_daemon/config.yaml, connects in STA mode, signals
 * duneos_service_ready(), then monitors the link and reconnects on drop.
 *
 * Config format (minimal YAML key: value, one per line):
 *   ssid: MyNetwork
 *   password: MyPassword
 *
 * Lines starting with '#' are ignored. Leading whitespace on values is
 * stripped (so `key: value` and `key:value` both work).
 *
 * Legacy /sd/wifi.conf (key=value) is still read as a fallback to ease
 * transition — will be removed in a future phase.
 *
 * /tmp/net_status is written (and refreshed on reconnect) in the format:
 *   ip=192.168.1.100
 *   gw=192.168.1.1
 *   netmask=255.255.255.0
 *   ssid=MyNetwork
 *   rssi=-65
 *
 * Other apps read /tmp/net_status to discover the current IP without needing
 * DUNEOS_PERM_NET themselves.
 */

#include <fcntl.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <ctype.h>

#include "duneos/wifi.h"
#include "duneos/libdune.h"

/* Exported by the kernel symbol table */
extern void duneos_service_ready(void);
extern void duneos_exit(int code);

#define WIFI_CONF_LEGACY "/sd/wifi.conf"
#define NET_STATUS "/tmp/net_status"

/* Maximum backoff before giving up and restarting the daemon task. */
#define MAX_BACKOFF_S 60
#define MONITOR_INTERVAL_S 30

/* ------------------------------------------------------------------ */

static void logf(const char *fmt, ...) {
  char buf[128];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(buf, sizeof(buf), fmt, ap);
  va_end(ap);
  write(STDOUT_FILENO, buf, strlen(buf));
  write(STDOUT_FILENO, "\r\n", 2);
}

/* ------------------------------------------------------------------ */
/* Config parsing                                                      */
/* ------------------------------------------------------------------ */

/*
 * Parse a "key: value" or "key=value" line into key and val (both pointing
 * into buf). Returns 1 if a valid pair was found, 0 otherwise.
 *
 * Supports both YAML-style (':') and legacy '=' separators so the same
 * parser handles /etc/wifi_daemon/config.yaml and /sd/wifi.conf.
 */
static int parse_kv(char *line, char **key, char **val) {
  if (!line) return 0;
  /* skip leading whitespace */
  while (*line && isspace((unsigned char)*line)) line++;
  if (line[0] == '#' || line[0] == '\0') return 0;

  char *sep = strchr(line, ':');
  if (!sep) sep = strchr(line, '=');
  if (!sep) return 0;
  *sep = '\0';
  *key = line;

  char *v = sep + 1;
  while (*v && isspace((unsigned char)*v)) v++;   /* strip leading WS */
  *val = v;

  /* strip trailing \r and whitespace */
  size_t vlen = strlen(*val);
  while (vlen > 0 && isspace((unsigned char)(*val)[vlen - 1])) {
    (*val)[--vlen] = '\0';
  }
  return 1;
}

static int parse_buf(char *buf, char ssid[33], char pass[65]) {
  char *line = buf;
  while (line && *line) {
    char *nl = strchr(line, '\n');
    if (nl) *nl = '\0';

    char *key, *val;
    if (parse_kv(line, &key, &val)) {
      if (strcmp(key, "ssid") == 0)
        snprintf(ssid, 33, "%s", val);
      if (strcmp(key, "password") == 0)
        snprintf(pass, 65, "%s", val);
    }

    line = nl ? nl + 1 : NULL;
  }
  return ssid[0] ? 0 : -1;
}

static int try_read_conf(const char *path, char ssid[33], char pass[65]) {
  int fd = open(path, O_RDONLY);
  if (fd < 0) return -1;

  char buf[256];
  ssize_t n = read(fd, buf, sizeof(buf) - 1);
  close(fd);
  if (n <= 0) return -1;
  buf[n] = '\0';
  return parse_buf(buf, ssid, pass);
}

static int read_wifi_conf(char ssid[33], char pass[65]) {
  ssid[0] = '\0';
  pass[0] = '\0';

  /* Primary path: /etc/wifi_daemon/config.yaml (DuneOS Phase 25.5 standard). */
  char conf_path[64];
  if (duneos_config_path("wifi_daemon", conf_path, sizeof(conf_path)) == 0) {
    if (try_read_conf(conf_path, ssid, pass) == 0) return 0;
  }

  /* Fallback: legacy /sd/wifi.conf for boards that haven't migrated. */
  if (try_read_conf(WIFI_CONF_LEGACY, ssid, pass) == 0) {
    logf("wifi_daemon: using legacy " WIFI_CONF_LEGACY
         " — migrate to /etc/wifi_daemon/config.yaml");
    return 0;
  }

  logf("wifi_daemon: no config found "
       "(tried /etc/wifi_daemon/config.yaml and " WIFI_CONF_LEGACY ")");
  return -1;
}

/* ------------------------------------------------------------------ */
/* /tmp/net_status                                                     */
/* ------------------------------------------------------------------ */

static void write_net_status(const duneos_net_info_t *info) {
  int fd = open(NET_STATUS, O_WRONLY | O_CREAT | O_TRUNC);
  if (fd < 0)
    return;

  char buf[256];
  int n =
      snprintf(buf, sizeof(buf), "ip=%s\ngw=%s\nnetmask=%s\nssid=%s\nrssi=%d\n",
               info->ip, info->gw, info->netmask, info->ssid, (int)info->rssi);
  write(fd, buf, n);
  close(fd);
}

static void clear_net_status(void) {
  int fd = open(NET_STATUS, O_WRONLY | O_CREAT | O_TRUNC);
  if (fd >= 0)
    close(fd);
}

/* ------------------------------------------------------------------ */
/* Main                                                                */
/* ------------------------------------------------------------------ */

void app_main(void) {
  char ssid[33], pass[65];

  if (read_wifi_conf(ssid, pass) != 0) {
    duneos_exit(1);
    return;
  }

  logf("wifi_daemon: connecting to '%s'...", ssid);

  uint32_t backoff = 5;
  bool ready_signalled = false;

  for (;;) {
    int rc = duneos_wifi_sta_connect(ssid, pass, 30000);

    if (rc == 0) {
      duneos_net_info_t info;
      duneos_wifi_get_info(&info);
      write_net_status(&info);
      logf("wifi_daemon: connected  ip=%s  rssi=%d dBm", info.ip,
           (int)info.rssi);

      if (!ready_signalled) {
        duneos_service_ready();
        ready_signalled = true;
      }

      backoff = 5; /* reset backoff on successful connect */

      /* Monitor loop: poll every MONITOR_INTERVAL_S seconds. */
      for (;;) {
        sleep(MONITOR_INTERVAL_S);

        duneos_net_info_t cur;
        if (duneos_wifi_get_info(&cur) == 0) {
          write_net_status(&cur); /* refresh RSSI */
        } else {
          /* Lost IP — fall through to reconnect. */
          logf("wifi_daemon: link lost, reconnecting...");
          clear_net_status();
          duneos_wifi_sta_disconnect();
          break;
        }
      }
    } else {
      logf("wifi_daemon: connect failed, retry in %lu s",
           (unsigned long)backoff);
      clear_net_status();
      sleep(backoff);
      if (backoff < MAX_BACKOFF_S)
        backoff *= 2;
      if (backoff > MAX_BACKOFF_S)
        backoff = MAX_BACKOFF_S;
    }
  }
}
