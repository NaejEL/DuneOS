/*
 * wifi — WiFi control-plane CLI (wpa_cli spirit).
 *
 *   wifi scan                 scan via wifi_daemon, print visible networks
 *   wifi status               link state + ssid/rssi/ip
 *   wifi join <ssid> [psk]    remember psk in known.yaml, then connect
 *   wifi disconnect           drop the link (no auto-reconnect)
 *   wifi reconnect            re-enable auto-connect to best known network
 *
 * The daemon owns the link; this tool only sends commands to the
 * "wifi_daemon" slot and reads the state files it publishes.
 */

#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "duneos/ambient.h"
#include "duneos/bin_args.h"
#include "duneos/libdune.h"
#include "duneos/wifi.h"

/* Not exposed by the toolchain's headers under default feature macros. */
extern int usleep(unsigned int usec);

#define USAGE "usage: iw scan | status | join <ssid> [psk] | disconnect | reconnect"

#define KNOWN_YAML      "/data/wifi/known.yaml"
#define KNOWN_YAML_SEED "/etc/wifi/known.yaml"   /* board-provisioned */
#define NET_STATUS     "/tmp/net_status"
#define WIFI_SCAN_PATH "/tmp/state/wifi_scan"

#define MAX_SCAN   16
#define MAX_KNOWN  16   /* must match the UI app (apps/user/wifi) writer */

static void out(const char *s) { write(STDOUT_FILENO, s, strlen(s)); }
static void outf(const char *fmt, ...)
{
    char buf[128];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (n > 0) write(STDOUT_FILENO, buf, (size_t)(n < (int)sizeof(buf) ? n : (int)sizeof(buf) - 1));
}

static int send_cmd(const char *cmd)
{
    if (duneos_send("wifi_daemon", cmd, strlen(cmd) + 1) < 0) {
        out("iw: wifi_daemon not running (try: run /bin/wifi_daemon.dap)\n");
        return -1;
    }
    return 0;
}

/* ---------------------------------------------------------------- scan -- */

typedef struct {
    uint8_t          seq;
    uint8_t          count;
    duneos_wifi_ap_t ap[MAX_SCAN];
} scan_blob_t;

static int read_scan(scan_blob_t *b)
{
    int fd = open(WIFI_SCAN_PATH, O_RDONLY);
    if (fd < 0) return -1;
    int n = (int)read(fd, b, sizeof(*b));
    close(fd);
    if (n < 2) return -1;
    int have = (n - 2) / (int)sizeof(duneos_wifi_ap_t);
    if (b->count > have) b->count = (uint8_t)have;
    if (b->count > MAX_SCAN) b->count = MAX_SCAN;
    return 0;
}

static int cmd_scan(void)
{
    scan_blob_t b;
    int old_seq = (read_scan(&b) == 0) ? (int)b.seq : -1;

    if (send_cmd("SCAN") != 0) return 1;

    for (int i = 0; i < 40; i++) {
        usleep(200 * 1000);
        if (read_scan(&b) == 0 && (int)b.seq != old_seq) {
            if (b.count == 0) { out("no networks found\n"); return 0; }
            outf("%-20s %5s %3s %s\n", "SSID", "RSSI", "CH", "SEC");
            for (int j = 0; j < b.count; j++)
                outf("%-20s %5d %3u %s\n",
                     b.ap[j].ssid, (int)b.ap[j].rssi, (unsigned)b.ap[j].channel,
                     b.ap[j].authmode ? "secured" : "open");
            return 0;
        }
    }
    out("iw: scan timed out\n");
    return 1;
}

/* -------------------------------------------------------------- status -- */

static int cmd_status(void)
{
    if (send_cmd("STATUS") != 0) return 1;
    usleep(300 * 1000);

    ambient_wifi_t w;
    memset(&w, 0, sizeof(w));
    int fd = open(AMBIENT_WIFI_PATH, O_RDONLY);
    if (fd >= 0) {
        read(fd, &w, sizeof(w));
        close(fd);
    }

    const char *state = (w.state == AMBIENT_WIFI_UP)         ? "up"
                      : (w.state == AMBIENT_WIFI_CONNECTING) ? "connecting"
                                                             : "down";
    outf("state: %s\n", state);
    if (w.state != AMBIENT_WIFI_DOWN && w.ssid[0]) outf("ssid: %s\n", w.ssid);
    if (w.state == AMBIENT_WIFI_UP) outf("rssi: %d dBm\n", (int)w.rssi_dbm);

    char buf[256];
    fd = open(NET_STATUS, O_RDONLY);
    if (fd >= 0) {
        int n = (int)read(fd, buf, sizeof(buf) - 1);
        close(fd);
        if (n > 0) {
            buf[n] = '\0';
            char *p = buf;
            while (p) {
                if (strncmp(p, "ip=", 3) == 0) {
                    char *e = strchr(p, '\n');
                    if (e) *e = '\0';
                    outf("ip: %s\n", p + 3);
                    break;
                }
                p = strchr(p, '\n');
                if (p) p++;
            }
        }
    }
    return 0;
}

/* ---------------------------------------------------- known.yaml editor -- */

typedef struct {
    char ssid[33];
    char psk[65];
} known_net_t;

static void copy_value(char *dst, int dstsz, const char *src)
{
    while (*src == ' ' || *src == '\t') src++;
    int len = (int)strlen(src);
    while (len > 0 && (src[len - 1] == ' ' || src[len - 1] == '\t' ||
                       src[len - 1] == '\r'))
        len--;
    if (len >= 2 && src[0] == '"' && src[len - 1] == '"') { src++; len -= 2; }
    if (len > dstsz - 1) len = dstsz - 1;
    memcpy(dst, src, (size_t)len);
    dst[len] = '\0';
}

static int parse_known(char *buf, known_net_t *nets, int max)
{
    int n = 0;
    int open_entry = -1;   /* orphan psk: lines (dropped ssid) are ignored */
    char *p = buf;
    while (p && *p) {
        char *eol = strchr(p, '\n');
        if (eol) *eol = '\0';
        char *t = p;
        while (*t == ' ' || *t == '\t') t++;
        if (*t != '#' && *t) {
            if (strncmp(t, "- ssid:", 7) == 0) {
                open_entry = -1;
                if (n < max) {
                    copy_value(nets[n].ssid, sizeof(nets[n].ssid), t + 7);
                    nets[n].psk[0] = '\0';
                    open_entry = n++;
                }
            } else if (strncmp(t, "psk:", 4) == 0 && open_entry >= 0) {
                copy_value(nets[open_entry].psk, sizeof(nets[open_entry].psk),
                           t + 4);
                open_entry = -1;
            }
        }
        p = eol ? eol + 1 : NULL;
    }
    return n;
}

static int update_known(const char *ssid, const char *psk)
{
    mkdir("/data/wifi", 0755);

    known_net_t nets[MAX_KNOWN];
    int n = 0;

    char fbuf[2048];   /* covers 16 full entries, same as the UI's s_fbuf */
    /* /data once it exists, else seed the first write from the /flash copy */
    int fd = open(KNOWN_YAML, O_RDONLY);
    if (fd < 0) fd = open(KNOWN_YAML_SEED, O_RDONLY);
    if (fd >= 0) {
        int r = (int)read(fd, fbuf, sizeof(fbuf) - 1);
        close(fd);
        if (r > 0) {
            fbuf[r] = '\0';
            n = parse_known(fbuf, nets, MAX_KNOWN);
        }
    }

    int idx = -1;
    for (int i = 0; i < n; i++)
        if (strcmp(nets[i].ssid, ssid) == 0) { idx = i; break; }
    if (idx < 0) {
        if (n >= MAX_KNOWN) return -ENOSPC;
        idx = n++;
        snprintf(nets[idx].ssid, sizeof(nets[idx].ssid), "%s", ssid);
    }
    snprintf(nets[idx].psk, sizeof(nets[idx].psk), "%s", psk);

    int o = snprintf(fbuf, sizeof(fbuf), "networks:\n");
    for (int i = 0; i < n; i++) {
        o += snprintf(fbuf + o, sizeof(fbuf) - (size_t)o,
                      "  - ssid: %s\n    psk: %s\n",
                      nets[i].ssid, nets[i].psk[0] ? nets[i].psk : "\"\"");
        if (o >= (int)sizeof(fbuf)) return -ENOSPC;
    }

    fd = open(KNOWN_YAML, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) {
        /* Old partition table without /data — flash fallback (not
         * reflash-proof, but saving must never silently fail). */
        mkdir("/etc", 0755);
        mkdir("/etc/wifi", 0755);
        fd = open(KNOWN_YAML_SEED, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    }
    if (fd < 0) return -errno;
    int w = (int)write(fd, fbuf, (size_t)o);
    close(fd);
    return (w == o) ? 0 : -EIO;
}

static int cmd_join(const char *ssid, const char *psk)
{
    if (strlen(ssid) > 32) {
        duneos_bin_err("iw", "ssid too long (max 32)");
        return 1;
    }
    if (psk) {
        int rc = update_known(ssid, psk);
        if (rc < 0) {
            char m[80];
            snprintf(m, sizeof(m), "cannot update %s: %s",
                     KNOWN_YAML, strerror(-rc));
            duneos_bin_err("iw", m);
            return 1;
        }
    }
    char cmd[64];
    snprintf(cmd, sizeof(cmd), "JOIN %s", ssid);
    return (send_cmd(cmd) == 0) ? 0 : 1;
}

/* ---------------------------------------------------------------- main -- */

int app_main(void)
{
    char ab[DUNEOS_EXEC_ARGS_BUF_SIZE];
    char *argv[8];
    char *cwd;
    int argc = duneos_bin_args(ab, sizeof(ab), &cwd, argv, 8);
    (void)cwd;

    if (argc < 2) { out(USAGE "\n"); return 2; }

    if (strcmp(argv[1], "scan") == 0)       return cmd_scan();
    if (strcmp(argv[1], "status") == 0)     return cmd_status();
    if (strcmp(argv[1], "disconnect") == 0) return (send_cmd("DISCONNECT") == 0) ? 0 : 1;
    if (strcmp(argv[1], "reconnect") == 0)  return (send_cmd("RECONNECT") == 0) ? 0 : 1;
    if (strcmp(argv[1], "join") == 0) {
        if (argc < 3) { out(USAGE "\n"); return 2; }
        return cmd_join(argv[2], argc > 3 ? argv[3] : NULL);
    }

    out(USAGE "\n");
    return 2;
}
