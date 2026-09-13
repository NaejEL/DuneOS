#pragma once

/*
 * known.yaml — the WiFi known-networks store, shared by every reader and
 * writer of the file (wifi_daemon, iw, the wifi UI app).
 *
 *   networks:
 *     - ssid: MyNet
 *       psk: secret123
 *
 * "- ssid:" opens an entry; the next "psk:" line at any indent completes it.
 * An entry with an empty ssid is dropped, and its psk line with it. Comment
 * lines (#) and blank lines are skipped. Values are trimmed of surrounding
 * whitespace and of one pair of double quotes, so `psk: ""` is the empty
 * passphrase. Anything longer than the field is truncated.
 *
 * The grammar was hand-rolled three times and the copies disagreed; this is
 * the single implementation, and tests/host/test_known_yaml.c compiles it.
 */

#include <stddef.h>

#define KNOWN_YAML_BUF_SIZE  2048 /* KNOWN_YAML_MAX_NETS full entries, spare */
#define KNOWN_YAML_MAX_NETS  16
#define KNOWN_YAML_SSID_CAP  33   /* 32 chars + NUL — 802.11 SSID maximum */
/* A WPA2 credential is either a 63-character passphrase or a 64 hex-digit
 * pre-shared key; 64 + NUL covers both. The UI app's own input widget caps at
 * the 63-character passphrase, which is a narrower thing and stays there. */
#define KNOWN_YAML_PSK_CAP   65

typedef struct {
    char ssid[KNOWN_YAML_SSID_CAP];
    char psk[KNOWN_YAML_PSK_CAP];
} known_net_t;

/* Trim whitespace and one pair of surrounding double quotes. */
void known_yaml_copy_value(const char *src, char *dst, size_t dstsz);

/* Parse a NUL-terminated buffer, destructively (line terminators are
 * overwritten). Returns the number of entries, capped at `max`. */
int known_yaml_parse(char *buf, known_net_t *nets, int max);

/* Read `path` into `buf`, NUL-terminated. Returns the byte count, or **-1
 * when the file cannot be opened** — callers distinguish "no file here" from
 * "nothing in it" to fall back from /data to the /etc seed. A writer that
 * already owns an output buffer reads through this and parses in place,
 * rather than paying for a second one on the same stack frame. */
int known_yaml_read(const char *path, char *buf, size_t bufsz);

/* Read and parse `path`. Same -1, and an empty or unreadable file parses as
 * 0 entries — which is not the same thing and must not fall back. */
int known_yaml_load(const char *path, known_net_t *nets, int max);

/*
 * The legacy single-network config — `ssid:` / `password:` at any indent, one
 * network per file — merged into `nets` as one more entry. Same comment and
 * value rules as the main grammar.
 *
 * Appends only when the file names a non-empty ssid that `nets` does not
 * already hold and there is room: an entry the user has since re-saved through
 * the UI wins over the file they migrated from. Returns 1 when an entry was
 * appended, 0 otherwise, and advances *count with it.
 */
int known_yaml_merge_legacy(const char *path, known_net_t *nets, int *count,
                            int max);
