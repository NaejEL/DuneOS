/*
 * wifi — scan list + join UI (the wpa_gui to wifi_daemon's wpa_supplicant).
 *
 * The daemon owns the link. This app only sends it plain-text commands over
 * the supervisor mailbox (SCAN / JOIN <ssid> / DISCONNECT) and watches the
 * files it publishes: /tmp/state/wifi_scan (binary scan blob with a seq byte
 * for freshness), /tmp/state/wifi (ambient link state) and /tmp/net_status
 * (text, for the IP). Credentials persist in /data/wifi/known.yaml,
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
#include "duneos/input.h"
#include "duneos/ui_text_entry.h"
#include "duneos/ambient.h"
#include "duneos/known_yaml.h"
#include "duneos/libdune.h"
#include "duneos/wifi.h"      /* duneos_wifi_ap_t — layout of the scan blob */

/* Not exposed by the toolchain's headers under default feature macros. */
extern int usleep(unsigned int usec);
extern int dprintf(int fd, const char *fmt, ...);

#define DAEMON_NAME  "wifi_daemon"
#define SCAN_PATH    "/tmp/state/wifi_scan"
#define NET_STATUS   "/tmp/net_status"
#define KNOWN_PATH   "/data/wifi/known.yaml"
#define KNOWN_SEED   "/etc/wifi/known.yaml"   /* board-provisioned, RO */

#define MAX_AP     16
#define MAX_KNOWN  KNOWN_YAML_MAX_NETS
#define ROW_LEN    48
#define PSK_CAP    64        /* what the prompt accepts: a 63-char WPA2
                             * passphrase + NUL. The stored capacity is
                             * KNOWN_YAML_PSK_CAP, which also fits a
                             * 64 hex-digit PSK. */
#define TICK_US      150000
#define SCAN_WAIT_MS 8000

enum { MODE_LIST, MODE_PROMPT };

typedef struct {
    ambient_wifi_t w;
    char           ip[20];
} link_t;

static gfx_ctx_t *g;
static ui_t      *ui;
/* File scope because quit() and the scan-wait loop both need it. Safe here:
 * the manifest declares heap_size, so this app always runs spawned — its BSS
 * is inside its own slot's bounds, unlike a captured app's. */
static duneos_input_t s_dev;
static uint16_t   s_sw, s_sh;
static int        s_bar_h;

static duneos_wifi_ap_t s_ap[MAX_AP];
static int              s_nap;
static char             s_rows[MAX_AP][ROW_LEN];
static const char      *s_row_ptrs[MAX_AP];
static ui_list_t        s_list;

static link_t s_link;
static int    s_mode;

static known_net_t s_known[MAX_KNOWN];
static int         s_kn;

static ui_text_entry_t s_te;
static char       s_mask[PSK_CAP];    /* the '*' the widget draws           */
static char       s_psk[PSK_CAP];     /* the real passphrase                */
static char       s_join_ssid[33];

static void quit(void) __attribute__((noreturn));
static void quit(void)
{
    if (ui) ui_destroy(ui);
    duneos_input_close(&s_dev);
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
    if (duneos_supervisor_launch("/bin/" DAEMON_NAME ".dap") != 0 &&
        duneos_supervisor_launch("/sd/bin/" DAEMON_NAME ".dap") != 0)
        return -1;
    for (int i = 0; i < 20; i++) {
        if (daemon_running()) return 0;
        usleep(TICK_US);
    }
    return -1;
}

/* ----- known.yaml (read-modify-write, whole file) -------------------------- */

static void load_known(void)
{
    /* /data (reflash-proof) once it exists; else the /flash seed. */
    s_kn = known_yaml_load(KNOWN_PATH, s_known, MAX_KNOWN);
    if (s_kn < 0) s_kn = known_yaml_load(KNOWN_SEED, s_known, MAX_KNOWN);
    if (s_kn < 0) s_kn = 0;
}

static int find_known(const char *ssid)
{
    for (int i = 0; i < s_kn; i++)
        if (strcmp(s_known[i].ssid, ssid) == 0) return i;
    return -1;
}

static int save_known(void)
{
    mkdir("/data/wifi", 0755);
    int fd = open(KNOWN_PATH, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) {
        /* Old partition table without /data — fall back to the flash path
         * (lost on reflash, but saving must never silently fail). */
        mkdir("/etc", 0755);
        mkdir("/etc/wifi", 0755);
        fd = open(KNOWN_SEED, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    }
    if (fd < 0) return -1;
    dprintf(fd, "networks:\n");
    for (int i = 0; i < s_kn; i++)
        dprintf(fd, "  - ssid: %s\n    psk: %s\n",
                s_known[i].ssid, s_known[i].psk[0] ? s_known[i].psk : "\"\"");
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
        snprintf(s_known[i].ssid, sizeof(s_known[i].ssid), "%s", ssid);
    }
    snprintf(s_known[i].psk, sizeof(s_known[i].psk), "%s", psk);
    save_known();
}

static void remove_known(const char *ssid)
{
    load_known();
    int i = find_known(ssid);
    if (i < 0) return;
    for (; i < s_kn - 1; i++)
        s_known[i] = s_known[i + 1];
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
    ui_label(ui, 2, s_te.line.y - 10, s_sw - 4, t, UI_ALIGN_LEFT,
             ui_theme(ui)->accent, ui_theme(ui)->bg);
    ui_text_entry_draw(ui, &s_te);
    ui_statusbar(ui, s_te.use_vkbd ? "Turn=move  Press=pick  Side=cancel"
                                   : "Enter=ok  Esc=cancel");
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
        int ifd = duneos_input_fd(&s_dev);
        for (;;) {
            fd_set rf;
            FD_ZERO(&rf);
            FD_SET(ifd, &rf);
            struct timeval tv = { 0, wait_us };
            if (select(ifd + 1, &rf, NULL, NULL, &tv) <= 0) break;
            duneos_intent_t it[INPUT_INTENT_BATCH_MAX];
            int n = duneos_input_read(&s_dev, it, INPUT_INTENT_BATCH_MAX);
            if (n < 0) break;
            for (int i = 0; i < n; i++)
                if (it[i].kind == INPUT_INTENT_BACK) quit();
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

/* The text entry picks the line editor or the on-screen keyboard from the
 * board's own answer about its hardware. On a keyboard board this is the same
 * single line as before; on the T-Embed the grid fills the area below it. */
static void open_prompt(const char *ssid)
{
    snprintf(s_join_ssid, sizeof(s_join_ssid), "%s", ssid);
    int top = s_bar_h + 14;
    ui_text_entry_init(&s_te, 2, top, s_sw - 4, s_sh - top - s_bar_h,
                       s_psk, s_mask, PSK_CAP, "psk ", true,
                       duneos_input_has_keyboard(&s_dev.topo),
                       ui_vkbd_backend());
    s_mode = MODE_PROMPT;
    draw_prompt();
}

/* The widget edits s_psk directly and masks only at draw time, so there is no
 * edit to reconstruct from the key that caused it. */
static void prompt_intent(const duneos_intent_t *it)
{
    switch (ui_text_entry_intent(&s_te, it)) {
    case UI_TEXT_ENTRY_CHANGED:
        draw_prompt();
        break;
    case UI_TEXT_ENTRY_SUBMIT:
        set_known(s_join_ssid, s_psk);
        join(s_join_ssid);
        s_mode = MODE_LIST;
        draw_main();
        break;
    case UI_TEXT_ENTRY_CANCEL:
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
        set_known(ap->ssid, s_known[k].psk);   /* refresh the entry per contract */
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
    g = gfx_open_mode(GFX_MODE_STREAM);
    if (!g) duneos_exit(10);
    if (duneos_input_open(&s_dev) != 0) { gfx_close(g); duneos_exit(11); }
    ui = ui_create(g);
    if (!ui) { duneos_input_close(&s_dev); gfx_close(g); duneos_exit(10); }

    ui_size(ui, &s_sw, &s_sh);
    s_bar_h = 8 + 2 * ui_theme(ui)->pad;
    ui_list_init(&s_list, 0, s_bar_h + 14, s_sw,
                 s_sh - (s_bar_h + 14) - s_bar_h);

    read_link(&s_link);
    start_scan();

    for (;;) {
        int ifd = duneos_input_fd(&s_dev);
        fd_set rf;
        FD_ZERO(&rf);
        FD_SET(ifd, &rf);
        struct timeval tv = { 0, TICK_US };
        if (select(ifd + 1, &rf, NULL, NULL, &tv) <= 0) {
            poll_link();
            ui_statusbar_top_poll(ui, "WiFi");
            continue;
        }

        duneos_intent_t it[INPUT_INTENT_BATCH_MAX];
        int n = duneos_input_read(&s_dev, it, INPUT_INTENT_BATCH_MAX);
        if (n <= 0) {
            /* Sticky modifiers arm/latch on RELEASE, which translates to no
             * intent — repaint the bar now instead of waiting for the next
             * idle tick. */
            ui_statusbar_top_poll(ui, "WiFi");
            continue;
        }

        for (int i = 0; i < n; i++) {
            if (s_mode == MODE_PROMPT) {
                prompt_intent(&it[i]);
                continue;
            }

            if (s_nap > 0) {
                switch (ui_list_intent(&s_list, &it[i])) {
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
            } else if (it[i].kind == INPUT_INTENT_BACK) {
                quit();
            }

            /* Letter shortcuts stay keyboard-only: a rotary board reaches the
             * list and the prompt, not these. */
            if (it[i].kind != INPUT_INTENT_TEXT) continue;
            if (it[i].ch == 'r')      start_scan();
            else if (it[i].ch == 'd') send_cmd("DISCONNECT");
            else if (it[i].ch == 'f') forget_ap();
        }
    }
}
