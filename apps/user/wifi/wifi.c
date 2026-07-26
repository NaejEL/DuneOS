/*
 * wifi — scan list + join UI (the wpa_gui to wifi_daemon's wpa_supplicant).
 *
 * The daemon owns the link. This app only sends it plain-text commands over
 * the supervisor mailbox (SCAN / JOIN <ssid> / DISCONNECT) and watches the
 * files it publishes: /tmp/state/wifi_scan (binary scan blob with a seq byte
 * for freshness), /tmp/state/wifi (ambient link state) and /tmp/net_status
 * (text, for the IP). Credentials persist in /flash/etc/wifi/known.yaml,
 * rewritten whole on every change — so no NET permission is needed here.
 */

#include <unistd.h>
#include <fcntl.h>
#include <string.h>
#include <stdio.h>
#include <stdint.h>
#include <sys/stat.h>
#include <sys/select.h>
#include <sys/time.h>

#include "duneos/gfx.h"
#include "duneos/ui.h"
#include "duneos/input_ioctl.h"
#include "duneos/ambient.h"
#include "duneos/libdune.h"
#include "duneos/wifi.h"      /* duneos_wifi_ap_t — layout of the scan blob */

/* Not exposed by the toolchain's headers under default feature macros. */
extern int usleep(unsigned int usec);
extern int dprintf(int fd, const char *fmt, ...);

#define DAEMON_NAME  "wifi_daemon"
#define SCAN_PATH    "/tmp/state/wifi_scan"
#define NET_STATUS   "/tmp/net_status"
#define KNOWN_PATH   "/flash/etc/wifi/known.yaml"

#define MAX_AP     16
#define MAX_KNOWN  16
#define ROW_LEN    48
#define PSK_CAP    64        /* 63 chars + NUL (WPA2 max passphrase) */
#define TICK_US      150000
#define SCAN_WAIT_MS 8000

enum { MODE_LIST, MODE_PROMPT };

typedef struct {
    ambient_wifi_t w;
    char           ip[20];
} link_t;

static gfx_ctx_t *g;
static ui_t      *ui;
static int        s_input;
static uint16_t   s_sw, s_sh;
static int        s_bar_h;

static duneos_wifi_ap_t s_ap[MAX_AP];
static int              s_nap;
static char             s_rows[MAX_AP][ROW_LEN];
static const char      *s_row_ptrs[MAX_AP];
static ui_list_t        s_list;

static link_t s_link;
static int    s_mode;

static char       s_kssid[MAX_KNOWN][33];
static char       s_kpsk[MAX_KNOWN][PSK_CAP + 1];
static int        s_kn;
static char       s_fbuf[2048];

static ui_input_t s_in;
static char       s_mask[PSK_CAP];    /* what ui_input draws: '*' repeated  */
static char       s_psk[PSK_CAP];     /* the real passphrase, same indexing */
static char       s_join_ssid[33];

static void quit(void) __attribute__((noreturn));
static void quit(void)
{
    if (ui) ui_destroy(ui);
    if (s_input >= 0) close(s_input);
    if (g) gfx_close(g);
    duneos_exit(0);
}

/* ----- daemon control ----------------------------------------------------- */

static int send_cmd(const char *cmd)
{
    return duneos_send(DAEMON_NAME, cmd, strlen(cmd) + 1);
}

static int daemon_running(void)
{
    duneos_slot_info_t slots[8];
    int n = duneos_supervisor_list_slots(slots, 8);
    for (int i = 0; i < n; i++)
        if (slots[i].active && strcmp(slots[i].name, DAEMON_NAME) == 0)
            return 1;
    return 0;
}

static int ensure_daemon(void)
{
    if (daemon_running()) return 0;
    if (duneos_supervisor_launch("/flash/bin/" DAEMON_NAME ".dap") != 0 &&
        duneos_supervisor_launch("/sd/bin/" DAEMON_NAME ".dap") != 0)
        return -1;
    for (int i = 0; i < 20; i++) {
        if (daemon_running()) return 0;
        usleep(TICK_US);
    }
    return -1;
}

/* ----- known.yaml (read-modify-write, whole file) -------------------------- */

static char *trim(char *s)
{
    while (*s == ' ' || *s == '\t') s++;
    char *e = s + strlen(s);
    while (e > s && (e[-1] == ' ' || e[-1] == '\t' || e[-1] == '\r' || e[-1] == '\n'))
        *--e = '\0';
    return s;
}

static void strip_quotes(char *s)
{
    int n = (int)strlen(s);
    if (n >= 2 && s[0] == '"' && s[n - 1] == '"') {
        s[n - 1] = '\0';
        memmove(s, s + 1, (size_t)n - 1);
    }
}

static void load_known(void)
{
    s_kn = 0;
    int fd = open(KNOWN_PATH, O_RDONLY);
    if (fd < 0) return;
    int n = (int)read(fd, s_fbuf, sizeof(s_fbuf) - 1);
    close(fd);
    if (n <= 0) return;
    s_fbuf[n] = '\0';

    int pending = 0;
    char *p = s_fbuf;
    while (p && *p) {
        char *nl = strchr(p, '\n');
        if (nl) *nl = '\0';
        char *t = trim(p);
        if (t[0] != '#') {
            if (strncmp(t, "- ssid:", 7) == 0 && s_kn < MAX_KNOWN) {
                char *v = trim(t + 7);
                strip_quotes(v);
                snprintf(s_kssid[s_kn], sizeof(s_kssid[0]), "%s", v);
                s_kpsk[s_kn][0] = '\0';
                s_kn++;
                pending = 1;
            } else if (pending && strncmp(t, "psk:", 4) == 0) {
                char *v = trim(t + 4);
                strip_quotes(v);
                snprintf(s_kpsk[s_kn - 1], sizeof(s_kpsk[0]), "%s", v);
                pending = 0;
            }
        }
        p = nl ? nl + 1 : NULL;
    }
}

static int find_known(const char *ssid)
{
    for (int i = 0; i < s_kn; i++)
        if (strcmp(s_kssid[i], ssid) == 0) return i;
    return -1;
}

static int save_known(void)
{
    mkdir("/flash/etc", 0755);
    mkdir("/flash/etc/wifi", 0755);
    int fd = open(KNOWN_PATH, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) return -1;
    dprintf(fd, "networks:\n");
    for (int i = 0; i < s_kn; i++)
        dprintf(fd, "  - ssid: %s\n    psk: %s\n",
                s_kssid[i], s_kpsk[i][0] ? s_kpsk[i] : "\"\"");
    close(fd);
    return 0;
}

static void set_known(const char *ssid, const char *psk)
{
    load_known();
    int i = find_known(ssid);
    if (i < 0) {
        if (s_kn >= MAX_KNOWN) return;
        i = s_kn++;
        snprintf(s_kssid[i], sizeof(s_kssid[0]), "%s", ssid);
    }
    snprintf(s_kpsk[i], sizeof(s_kpsk[0]), "%s", psk);
    save_known();
}

static void remove_known(const char *ssid)
{
    load_known();
    int i = find_known(ssid);
    if (i < 0) return;
    for (; i < s_kn - 1; i++) {
        memcpy(s_kssid[i], s_kssid[i + 1], sizeof(s_kssid[0]));
        memcpy(s_kpsk[i], s_kpsk[i + 1], sizeof(s_kpsk[0]));
    }
    s_kn--;
    save_known();
}

/* ----- daemon-published state ---------------------------------------------- */

static int read_scan(uint8_t *seq)
{
    int fd = open(SCAN_PATH, O_RDONLY);
    if (fd < 0) return -1;
    uint8_t hdr[2];
    if (read(fd, hdr, 2) != 2) { close(fd); return -1; }
    int cnt = hdr[1];
    if (cnt > MAX_AP) cnt = MAX_AP;
    int want = cnt * (int)sizeof(duneos_wifi_ap_t);
    int got = (int)read(fd, s_ap, (size_t)want);
    close(fd);
    if (got != want) return -1;
    *seq = hdr[0];
    return cnt;
}

static void read_link(link_t *out)
{
    memset(out, 0, sizeof(*out));
    int fd = open(AMBIENT_WIFI_PATH, O_RDONLY);
    if (fd >= 0) {
        if (read(fd, &out->w, sizeof(out->w)) != (int)sizeof(out->w))
            memset(&out->w, 0, sizeof(out->w));
        close(fd);
    }
    fd = open(NET_STATUS, O_RDONLY);
    if (fd < 0) return;
    char buf[256];
    int n = (int)read(fd, buf, sizeof(buf) - 1);
    close(fd);
    if (n <= 0) return;
    buf[n] = '\0';
    char *p = strstr(buf, "ip=");
    if (p && (p == buf || p[-1] == '\n')) {
        p += 3;
        int i = 0;
        while (p[i] && p[i] != '\n' && i < (int)sizeof(out->ip) - 1) {
            out->ip[i] = p[i];
            i++;
        }
        out->ip[i] = '\0';
    }
}

/* ----- rendering ------------------------------------------------------------ */

static void build_rows(void)
{
    for (int i = 0; i < s_nap; i++) {
        const duneos_wifi_ap_t *ap = &s_ap[i];
        int bars = ap->rssi >= -55 ? 4 : ap->rssi >= -67 ? 3 :
                   ap->rssi >= -78 ? 2 : 1;
        char pfx = (s_link.w.state == AMBIENT_WIFI_UP &&
                    strcmp(s_link.w.ssid, ap->ssid) == 0) ? '>' : ' ';
        snprintf(s_rows[i], ROW_LEN, "%c%s  %.*s%s",
                 pfx, ap->ssid, bars, "||||", ap->authmode ? "  *" : "");
        s_row_ptrs[i] = s_rows[i];
    }
}

static void header_text(char *out, int cap)
{
    if (s_link.w.state == AMBIENT_WIFI_UP)
        snprintf(out, (size_t)cap, "Connected %s  %s  %d dBm",
                 s_link.w.ssid, s_link.ip, (int)s_link.w.rssi_dbm);
    else if (s_link.w.state == AMBIENT_WIFI_CONNECTING)
        snprintf(out, (size_t)cap, "Connecting %s... (Enter=retype psk)",
                 s_link.w.ssid);
    else
        snprintf(out, (size_t)cap, "Disconnected");
}

static void draw_header(void)
{
    char hdr[64];
    header_text(hdr, sizeof(hdr));
    ui_label(ui, 2, s_bar_h + 2, s_sw - 4, hdr, UI_ALIGN_LEFT,
             ui_theme(ui)->accent, ui_theme(ui)->bg);
}

static void draw_main(void)
{
    ui_clear(ui);
    ui_statusbar_top(ui, "WiFi");
    draw_header();
    ui_list_draw(ui, &s_list);
    ui_statusbar(ui, "Enter=join r=scan d=drop f=forget");
    ui_flush(ui);
}

static void draw_prompt(void)
{
    char t[48];
    snprintf(t, sizeof(t), "Join %s", s_join_ssid);
    ui_label(ui, 2, s_in.y - 10, s_sw - 4, t, UI_ALIGN_LEFT,
             ui_theme(ui)->accent, ui_theme(ui)->bg);
    ui_input_draw(ui, &s_in);
    ui_statusbar(ui, "Enter=ok  Esc=cancel");
    ui_flush(ui);
}

/* ----- actions --------------------------------------------------------------- */

static void join(const char *ssid)
{
    char cmd[64];
    snprintf(cmd, sizeof(cmd), "JOIN %s", ssid);
    send_cmd(cmd);
}

static void poll_link(void)
{
    link_t cur;
    read_link(&cur);
    if (memcmp(&cur, &s_link, sizeof(cur)) == 0) return;
    s_link = cur;
    if (s_mode == MODE_LIST) {
        build_rows();
        draw_main();
    }
}

/* Blocking scan wait: SCAN then poll the blob for a fresh seq. ESC still
 * exits during the wait; the send retries in case the daemon just launched
 * and its mailbox wasn't up yet. */
static uint64_t now_ms(void)
{
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return (uint64_t)tv.tv_sec * 1000u + (uint64_t)tv.tv_usec / 1000u;
}

static void rescan(void)
{
    uint8_t old_seq = 0;
    int have_old = read_scan(&old_seq) >= 0;
    int sent = send_cmd("SCAN") == 0;

    ui_message(ui, "WiFi", "Scanning...");
    ui_flush(ui);

    uint64_t deadline    = now_ms() + SCAN_WAIT_MS;
    uint64_t next_resend = 0;
    while (now_ms() < deadline) {
        if (!sent && now_ms() >= next_resend) {
            sent = send_cmd("SCAN") == 0;
            next_resend = now_ms() + 1000;
        }

        /* One paced wait, then drain every pending input event without
         * sleeping again — key repeat must not starve the scan poll. */
        int wait_us = TICK_US;
        for (;;) {
            fd_set rf;
            FD_ZERO(&rf);
            FD_SET(s_input, &rf);
            struct timeval tv = { 0, wait_us };
            if (select(s_input + 1, &rf, NULL, NULL, &tv) <= 0) break;
            input_event_t ev;
            if (read(s_input, &ev, sizeof(ev)) != (int)sizeof(ev)) break;
            if (ev.type == INPUT_EV_KEY && ev.value != INPUT_VAL_RELEASE &&
                ev.code == KEY_ESC)
                quit();
            wait_us = 0;
        }

        uint8_t seq;
        int n = read_scan(&seq);
        if (n >= 0 && (!have_old || seq != old_seq)) {
            s_nap = n;
            build_rows();
            ui_list_set_items(&s_list, s_row_ptrs, s_nap);
            read_link(&s_link);
            draw_main();
            return;
        }
    }
    ui_message(ui, "Scan failed", "r=retry  Esc=quit");
    ui_flush(ui);
}

static void start_scan(void)
{
    if (ensure_daemon() != 0) {
        ui_message(ui, "WiFi error",
                   "wifi_daemon not found\nr=retry  Esc=quit");
        ui_flush(ui);
        return;
    }
    send_cmd("STATUS");
    rescan();
}

static void open_prompt(const char *ssid)
{
    snprintf(s_join_ssid, sizeof(s_join_ssid), "%s", ssid);
    s_psk[0] = '\0';
    ui_input_init(&s_in, 2, s_sh - s_bar_h - 12, s_sw - 4,
                  s_mask, sizeof(s_mask), "psk ");
    s_mode = MODE_PROMPT;
    draw_prompt();
}

/* ui_input edits s_mask (that's what it draws). Mirror each edit into s_psk
 * at the same index, then flatten the mask back to '*' — the widget itself
 * only ever tells us CHANGED, so the edit is reconstructed from the key and
 * the pos/len delta. */
static void prompt_key(uint16_t k)
{
    int oldlen = s_in.len, oldpos = s_in.pos;
    switch (ui_input_key(&s_in, k)) {
    case UI_INPUT_CHANGED:
        if (s_in.len > oldlen) {
            memmove(s_psk + oldpos + 1, s_psk + oldpos,
                    (size_t)(oldlen - oldpos + 1));
            s_psk[oldpos] = (char)k;
        } else if (s_in.len < oldlen) {
            int at = (k == KEY_BACKSPACE) ? oldpos - 1 : oldpos;
            memmove(s_psk + at, s_psk + at + 1, (size_t)(oldlen - at));
        }
        memset(s_mask, '*', (size_t)s_in.len);
        s_mask[s_in.len] = '\0';
        ui_input_draw(ui, &s_in);
        ui_flush(ui);
        break;
    case UI_INPUT_SUBMIT:
        set_known(s_join_ssid, s_psk);
        join(s_join_ssid);
        s_mode = MODE_LIST;
        draw_main();
        break;
    case UI_INPUT_CANCEL:
        s_mode = MODE_LIST;
        draw_main();
        break;
    default:
        break;
    }
}

static void select_ap(void)
{
    if (s_nap == 0) return;
    const duneos_wifi_ap_t *ap = &s_ap[s_list.sel];

    if (s_link.w.state == AMBIENT_WIFI_UP &&
        strcmp(s_link.w.ssid, ap->ssid) == 0)
        return;                            /* already connected to it */

    load_known();
    int k = find_known(ap->ssid);
    if (k >= 0 && ap->authmode != 0 &&
        s_link.w.state == AMBIENT_WIFI_CONNECTING &&
        strcmp(s_link.w.ssid, ap->ssid) == 0) {
        /* The stored psk is already failing (daemon stuck retrying this
         * ssid) — a second Enter means "let me retype it", not "send the
         * same wrong password again". */
        open_prompt(ap->ssid);
        return;
    }
    if (k >= 0) {
        set_known(ap->ssid, s_kpsk[k]);   /* refresh the entry per contract */
        join(ap->ssid);
    } else if (ap->authmode == 0) {
        set_known(ap->ssid, "");
        join(ap->ssid);
    } else {
        open_prompt(ap->ssid);
    }
}

static void forget_ap(void)
{
    if (s_nap == 0) return;
    const char *ssid = s_ap[s_list.sel].ssid;
    load_known();
    if (find_known(ssid) < 0) return;
    remove_known(ssid);
    if (s_link.w.state != AMBIENT_WIFI_DOWN &&
        strcmp(s_link.w.ssid, ssid) == 0)
        send_cmd("DISCONNECT");
}

/* ----- main ------------------------------------------------------------------ */

void app_main(void)
{
    s_input = -1;
    g = gfx_open_mode(GFX_MODE_STREAM);
    if (!g) duneos_exit(10);
    s_input = open("/dev/input/event0", O_RDONLY);
    if (s_input < 0) { gfx_close(g); duneos_exit(11); }
    ui = ui_create(g);
    if (!ui) { close(s_input); gfx_close(g); duneos_exit(10); }

    ui_size(ui, &s_sw, &s_sh);
    s_bar_h = 8 + 2 * ui_theme(ui)->pad;
    ui_list_init(&s_list, 0, s_bar_h + 14, s_sw,
                 s_sh - (s_bar_h + 14) - s_bar_h);

    read_link(&s_link);
    start_scan();

    for (;;) {
        fd_set rf;
        FD_ZERO(&rf);
        FD_SET(s_input, &rf);
        struct timeval tv = { 0, TICK_US };
        if (select(s_input + 1, &rf, NULL, NULL, &tv) <= 0) {
            poll_link();
            ui_statusbar_top_poll(ui, "WiFi");
            continue;
        }

        input_event_t ev;
        if (read(s_input, &ev, sizeof(ev)) != (int)sizeof(ev)) continue;
        if (ev.type != INPUT_EV_KEY || ev.value == INPUT_VAL_RELEASE) {
            /* Sticky modifiers arm/latch on RELEASE — repaint the bar now
             * instead of waiting for the next idle tick. */
            ui_statusbar_top_poll(ui, "WiFi");
            continue;
        }
        uint16_t k = ev.code;

        if (s_mode == MODE_PROMPT) {
            prompt_key(k);
            continue;
        }

        if (s_nap > 0) {
            switch (ui_list_key(&s_list, k)) {
            case UI_LIST_MOVED:
                ui_list_draw(ui, &s_list);
                ui_flush(ui);
                continue;
            case UI_LIST_SELECT:
                select_ap();
                continue;
            case UI_LIST_CANCEL:
                quit();
            case UI_LIST_NONE:
                break;
            }
        } else if (k == KEY_ESC) {
            quit();
        }

        if (k == 'r')      start_scan();
        else if (k == 'd') send_cmd("DISCONNECT");
        else if (k == 'f') forget_ap();
    }
}
