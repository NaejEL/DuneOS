/*
 * wifi_daemon — DuneOS WiFi control-plane daemon (wpa_supplicant spirit).
 *
 * Owns the STA link. Other apps never touch duneos_wifi_* directly; they
 * send plain-text commands to the "wifi_daemon" slot via duneos_send():
 *
 *   SCAN          scan, write /tmp/state/wifi_scan (seq-stamped blob)
 *   JOIN <ssid>   connect to a network listed in known.yaml
 *   DISCONNECT    drop the link, no auto-reconnect until RECONNECT/JOIN
 *   RECONNECT     re-enable auto-connect to the best known network
 *   STATUS        refresh /tmp/state/wifi and /tmp/net_status immediately
 *
 * Known networks: /data/wifi/known.yaml (reflash-proof; falls back to the
 * board-provisioned /flash/etc/wifi/known.yaml seed when /data has none) —
 *   networks:
 *     - ssid: MyNet
 *       psk: secret123
 * Legacy /flash/etc/wifi_daemon/config.yaml (ssid:/password:) is merged in
 * as one more known network to ease migration.
 *
 * Published state:
 *   /tmp/state/wifi       ambient_wifi_t (single write, ADR 027)
 *   /tmp/state/wifi_scan  { uint8 seq; uint8 count; duneos_wifi_ap_t[count] }
 *   /tmp/net_status       text ip=/gw=/netmask=/ssid=/rssi= (for ifconfig)
 */

#include <ctype.h>
#include <fcntl.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "duneos/ambient.h"
#include "duneos/libdune.h"
#include "duneos/wifi.h"
#include "duneos/dlog.h"

#define KNOWN_YAML      "/data/wifi/known.yaml"
#define KNOWN_YAML_SEED "/flash/etc/wifi/known.yaml"
#define NET_STATUS     "/tmp/net_status"
#define WIFI_SCAN_PATH "/tmp/state/wifi_scan"

#define MAX_KNOWN       16 /* must match the UI app (apps/user/wifi) writer */
#define MAX_SCAN        16
#define RECV_TIMEOUT_S  5
#define MAX_BACKOFF_S   60
#define HK_WAKEUPS      6 /* 6 × 5 s recv timeouts ≈ 30 s housekeeping */

typedef struct {
    char ssid[33];
    char psk[65];
} known_net_t;

typedef struct {
    known_net_t known[MAX_KNOWN];
    int         nknown;
    bool        connected;
    bool        user_disconnected;
    uint32_t    backoff_s;
    uint32_t    retry_wakeups;
    uint32_t    hk_wakeups;
} daemon_state_t;

static uint8_t s_scan_seq;

/* ------------------------------------------------------------------ */
/* known.yaml parsing                                                  */
/* ------------------------------------------------------------------ */

/* Trim whitespace and one pair of surrounding double quotes (psk: ""). */
static void copy_value(const char *src, char *dst, size_t dstsz) {
  while (*src && isspace((unsigned char)*src)) src++;
  size_t len = strlen(src);
  while (len > 0 && isspace((unsigned char)src[len - 1])) len--;
  if (len >= 2 && src[0] == '"' && src[len - 1] == '"') {
    src++;
    len -= 2;
  }
  if (len >= dstsz) len = dstsz - 1;
  memcpy(dst, src, len);
  dst[len] = '\0';
}

/* "- ssid:" opens an entry; the next "psk:" line (any indent) completes it. */
static int load_known_yaml(const char *path, known_net_t *nets, int max) {
  int fd = open(path, O_RDONLY);
  if (fd < 0) return -1;

  char buf[2048]; /* covers 16 full entries, same as the UI's s_fbuf */
  ssize_t n = read(fd, buf, sizeof(buf) - 1);
  close(fd);
  if (n <= 0) return 0;
  buf[n] = '\0';

  int count = 0;
  int open_entry = -1;
  char *line = buf;
  while (line && *line) {
    char *nl = strchr(line, '\n');
    if (nl) *nl = '\0';

    char *p = line;
    while (*p && isspace((unsigned char)*p)) p++;
    if (*p && *p != '#') {
      if (strncmp(p, "- ssid:", 7) == 0) {
        open_entry = -1;
        if (count < max) {
          copy_value(p + 7, nets[count].ssid, sizeof(nets[count].ssid));
          nets[count].psk[0] = '\0';
          if (nets[count].ssid[0]) open_entry = count++;
        }
      } else if (open_entry >= 0 && strncmp(p, "psk:", 4) == 0) {
        copy_value(p + 4, nets[open_entry].psk, sizeof(nets[open_entry].psk));
        open_entry = -1;
      }
    }

    line = nl ? nl + 1 : NULL;
  }
  return count;
}

static int find_known(const known_net_t *nets, int count, const char *ssid) {
  for (int i = 0; i < count; i++)
    if (strcmp(nets[i].ssid, ssid) == 0) return i;
  return -1;
}

/* Legacy single-network config, merged as one more known entry. */
static void merge_legacy(known_net_t *nets, int *count, int max) {
  char path[64];
  if (duneos_config_path("wifi_daemon", path, sizeof(path)) != 0) return;

  int fd = open(path, O_RDONLY);
  if (fd < 0) return;

  char buf[256];
  ssize_t n = read(fd, buf, sizeof(buf) - 1);
  close(fd);
  if (n <= 0) return;
  buf[n] = '\0';

  char ssid[33] = "", psk[65] = "";
  char *line = buf;
  while (line && *line) {
    char *nl = strchr(line, '\n');
    if (nl) *nl = '\0';

    char *p = line;
    while (*p && isspace((unsigned char)*p)) p++;
    if (*p && *p != '#') {
      if (strncmp(p, "ssid:", 5) == 0)
        copy_value(p + 5, ssid, sizeof(ssid));
      else if (strncmp(p, "password:", 9) == 0)
        copy_value(p + 9, psk, sizeof(psk));
    }

    line = nl ? nl + 1 : NULL;
  }

  if (!ssid[0] || *count >= max || find_known(nets, *count, ssid) >= 0) return;
  memcpy(nets[*count].ssid, ssid, sizeof(ssid));
  memcpy(nets[*count].psk, psk, sizeof(psk));
  (*count)++;
}

static int load_known(known_net_t *nets) {
  /* /data survives reflashes and is the source of truth once it exists;
   * the /flash copy is only the board-provisioned seed (dbt stages it from
   * boards/<board>/etc) for first boot or /data-less partition tables. */
  int count = load_known_yaml(KNOWN_YAML, nets, MAX_KNOWN);
  if (count < 0)
    count = load_known_yaml(KNOWN_YAML_SEED, nets, MAX_KNOWN);
  if (count < 0) count = 0;
  merge_legacy(nets, &count, MAX_KNOWN);
  return count;
}

/* ------------------------------------------------------------------ */
/* Published state                                                     */
/* ------------------------------------------------------------------ */

static void publish_ambient(uint8_t state, int8_t rssi, const char *ssid) {
  mkdir(AMBIENT_STATE_DIR, 0755);
  int fd = open(AMBIENT_WIFI_PATH, O_WRONLY | O_CREAT | O_TRUNC, 0644);
  if (fd < 0) return;

  ambient_wifi_t w = {.state = state, .rssi_dbm = rssi, .ssid = ""};
  if (ssid) snprintf(w.ssid, sizeof(w.ssid), "%s", ssid);
  write(fd, &w, sizeof(w));
  close(fd);
}

static void write_net_status(const duneos_net_info_t *info) {
  int fd = open(NET_STATUS, O_WRONLY | O_CREAT | O_TRUNC, 0644);
  if (fd < 0) return;

  char buf[256];
  int n =
      snprintf(buf, sizeof(buf), "ip=%s\ngw=%s\nnetmask=%s\nssid=%s\nrssi=%d\n",
               info->ip, info->gw, info->netmask, info->ssid, (int)info->rssi);
  write(fd, buf, n);
  close(fd);
}

static void clear_net_status(void) {
  int fd = open(NET_STATUS, O_WRONLY | O_CREAT | O_TRUNC, 0644);
  if (fd >= 0) close(fd);
}

static void do_scan_cmd(void) {
  duneos_wifi_ap_t aps[MAX_SCAN];
  int n = duneos_wifi_scan(aps, MAX_SCAN);
  if (n < 0) {
    DLOGW("scan failed (%d)", n);
    return;
  }
  if (n > MAX_SCAN) n = MAX_SCAN;

  uint8_t blob[2 + MAX_SCAN * sizeof(duneos_wifi_ap_t)];
  blob[0] = ++s_scan_seq;
  blob[1] = (uint8_t)n;
  memcpy(blob + 2, aps, (size_t)n * sizeof(duneos_wifi_ap_t));

  mkdir(AMBIENT_STATE_DIR, 0755);
  int fd = open(WIFI_SCAN_PATH, O_WRONLY | O_CREAT | O_TRUNC, 0644);
  if (fd < 0) return;
  write(fd, blob, 2 + (size_t)n * sizeof(duneos_wifi_ap_t));
  close(fd);
  DLOGI("scan done, %d AP(s)", n);
}

/* ------------------------------------------------------------------ */
/* Link management                                                     */
/* ------------------------------------------------------------------ */

static bool connect_to(const known_net_t *net) {
  publish_ambient(AMBIENT_WIFI_CONNECTING, 0, net->ssid);
  DLOGI("connecting to '%s'...", net->ssid);

  if (duneos_wifi_sta_connect(net->ssid, net->psk, 30000) != 0) {
    DLOGW("connect to '%s' failed", net->ssid);
    clear_net_status();
    return false;
  }

  duneos_net_info_t info;
  if (duneos_wifi_get_info(&info) == 0) {
    write_net_status(&info);
    publish_ambient(AMBIENT_WIFI_UP, info.rssi, info.ssid);
    DLOGI("connected  ip=%s  rssi=%d dBm", info.ip,
         (int)info.rssi);
  } else {
    publish_ambient(AMBIENT_WIFI_UP, 0, net->ssid);
  }
  return true;
}

/* Scan and return the known network with the best RSSI (scan results are
 * sorted RSSI-descending), or -1 if none in range. */
static int pick_best_known(const known_net_t *nets, int nknown) {
  duneos_wifi_ap_t aps[MAX_SCAN];
  int n = duneos_wifi_scan(aps, MAX_SCAN);
  if (n < 0) {
    DLOGW("scan failed (%d)", n);
    return -1;
  }
  if (n > MAX_SCAN) n = MAX_SCAN;

  for (int i = 0; i < n; i++) {
    int k = find_known(nets, nknown, aps[i].ssid);
    if (k >= 0) return k;
  }
  return -1;
}

/* ssid may be NULL (no target yet — e.g. nothing known in range). Keeping
 * the failing target's ssid in the CONNECTING ambient lets the UI offer a
 * password re-prompt for exactly that network. */
static void schedule_retry(daemon_state_t *st, const char *ssid) {
  publish_ambient(AMBIENT_WIFI_CONNECTING, 0, ssid);
  st->retry_wakeups = (st->backoff_s + RECV_TIMEOUT_S - 1) / RECV_TIMEOUT_S;
  st->backoff_s *= 2;
  if (st->backoff_s > MAX_BACKOFF_S) st->backoff_s = MAX_BACKOFF_S;
}

static void on_link_lost(daemon_state_t *st) {
  DLOGW("link lost");
  st->connected = false;
  duneos_wifi_sta_disconnect();
  clear_net_status();
  st->backoff_s = 5;
  st->retry_wakeups = 0;
  publish_ambient(AMBIENT_WIFI_CONNECTING, 0, NULL);
}

static void auto_connect(daemon_state_t *st) {
  int k = pick_best_known(st->known, st->nknown);
  if (k < 0) {
    DLOGW("no known network in range, retry in %lu s",
         (unsigned long)st->backoff_s);
    schedule_retry(st, NULL);
    return;
  }
  if (connect_to(&st->known[k])) {
    st->connected = true;
    st->backoff_s = 5;
    st->hk_wakeups = 0;
  } else {
    schedule_retry(st, st->known[k].ssid);
  }
}

/* ------------------------------------------------------------------ */
/* Command handling                                                    */
/* ------------------------------------------------------------------ */

static void cmd_status(daemon_state_t *st) {
  if (st->connected) {
    duneos_net_info_t info;
    if (duneos_wifi_get_info(&info) == 0) {
      write_net_status(&info);
      publish_ambient(AMBIENT_WIFI_UP, info.rssi, info.ssid);
      return;
    }
    on_link_lost(st);
    return;
  }
  clear_net_status();
  if (st->user_disconnected || st->nknown == 0)
    publish_ambient(AMBIENT_WIFI_DOWN, 0, NULL);
  else
    publish_ambient(AMBIENT_WIFI_CONNECTING, 0, NULL);
}

static void cmd_join(daemon_state_t *st, const char *ssid) {
  while (*ssid == ' ') ssid++;
  if (!*ssid) {
    DLOGW("JOIN without ssid");
    return;
  }

  int k = find_known(st->known, st->nknown, ssid);
  if (k < 0) {
    /* The UI may have just rewritten known.yaml — pick up the new entry. */
    st->nknown = load_known(st->known);
    k = find_known(st->known, st->nknown, ssid);
  }
  if (k < 0) {
    DLOGW("JOIN '%s': not in known.yaml", ssid);
    publish_ambient(AMBIENT_WIFI_DOWN, 0, NULL);
    return;
  }

  st->user_disconnected = false;
  if (st->connected) {
    duneos_wifi_sta_disconnect();
    st->connected = false;
    clear_net_status();
  }

  if (connect_to(&st->known[k])) {
    st->connected = true;
    st->backoff_s = 5;
    st->hk_wakeups = 0;
  } else {
    st->backoff_s = 5;
    schedule_retry(st, st->known[k].ssid);
  }
}

static void handle_cmd(daemon_state_t *st, char *cmd) {
  if (strcmp(cmd, "SCAN") == 0) {
    do_scan_cmd();
  } else if (strncmp(cmd, "JOIN ", 5) == 0) {
    cmd_join(st, cmd + 5);
  } else if (strcmp(cmd, "DISCONNECT") == 0) {
    DLOGI("user disconnect");
    duneos_wifi_sta_disconnect();
    st->connected = false;
    st->user_disconnected = true;
    clear_net_status();
    publish_ambient(AMBIENT_WIFI_DOWN, 0, NULL);
  } else if (strcmp(cmd, "RECONNECT") == 0) {
    DLOGI("reconnect enabled");
    st->user_disconnected = false;
    st->backoff_s = 5;
    st->retry_wakeups = 0;
  } else if (strcmp(cmd, "STATUS") == 0) {
    cmd_status(st);
  } else {
    DLOGW("unknown command '%s'", cmd);
  }
}

/* ------------------------------------------------------------------ */
/* Main                                                                */
/* ------------------------------------------------------------------ */

void app_main(void) {
  daemon_state_t st = {.backoff_s = 5};

  dlog_open("wifi_daemon");

  publish_ambient(AMBIENT_WIFI_DOWN, 0, NULL);

  st.nknown = load_known(st.known);
  DLOGI("%d known network(s)", st.nknown);

  /* No eager duneos_wifi_init() here: WiFi+lwIP grab ~40-50 KiB of internal
   * RAM, which starves the boot-time launch of the home app (g_shell 20 KiB
   * stack -> ESP_ERR_NO_MEM). scan/connect init the radio on first use, so
   * with no known networks and no commands the radio never comes up. */

  duneos_service_ready();

  for (;;) {
    duneos_msg_t msg;
    int n = duneos_recv(&msg, RECV_TIMEOUT_S * 1000);

    if (n > 0) {
      msg.data[sizeof(msg.data) - 1] = '\0';
      handle_cmd(&st, msg.data);
      continue;
    }

    if (st.connected) {
      if (++st.hk_wakeups < HK_WAKEUPS) continue;
      st.hk_wakeups = 0;
      duneos_net_info_t info;
      if (duneos_wifi_get_info(&info) == 0) {
        write_net_status(&info);
        publish_ambient(AMBIENT_WIFI_UP, info.rssi, info.ssid);
      } else {
        on_link_lost(&st);
      }
      continue;
    }

    if (st.user_disconnected || st.nknown == 0) continue;

    if (st.retry_wakeups > 0) {
      st.retry_wakeups--;
      continue;
    }

    auto_connect(&st);
  }
}
