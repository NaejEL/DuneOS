/*
 * libappmeta — see <duneos/appmeta.h>.
 *
 * Minimal ELF32 reader: parse the section header table, find
 * ".duneos_manifest", read its JSON payload, and pull out the handful of
 * fields the launcher needs. The JSON is the compact form emitted by
 * tools/dbt/builder.py (no whitespace), so the field extraction is a small
 * scoped string scan rather than a full parser.
 */

#include "duneos/appmeta.h"

#include <fcntl.h>
#include <unistd.h>
#include <string.h>
#include <stdint.h>

/* ELF32 little-endian field offsets (ESP32 is LE, ELF data is LE). */
#define EH_SIZE        52
#define EH_SHOFF       32
#define EH_SHENTSIZE   46
#define EH_SHNUM       48
#define EH_SHSTRNDX    50
#define SH_SIZE        40
#define SH_NAME         0
#define SH_OFFSET      16
#define SH_SIZE_FIELD  20

#define JSON_MAX   2048
#define SHSTR_MAX  8192   /* covers ~300+ sections; larger falls back to slow path */

static char s_shstr[SHSTR_MAX];
static char s_json[JSON_MAX];

static uint16_t rd16(const unsigned char *p) { return (uint16_t)(p[0] | (p[1] << 8)); }
static uint32_t rd32(const unsigned char *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static int read_at(int fd, long off, void *buf, unsigned len)
{
    if (lseek(fd, off, SEEK_SET) != off) return -1;
    unsigned got = 0;
    unsigned char *p = buf;
    while (got < len) {
        int n = read(fd, p + got, len - got);
        if (n <= 0) return -1;
        got += (unsigned)n;
    }
    return 0;
}

/* Read a NUL-terminated section name from the string table. Tolerates a short
 * read near EOF; the table stores names NUL-terminated so strcmp stops cleanly
 * even if we read into the following entry. */
static void read_name(int fd, long off, char *out, int outsz)
{
    out[0] = '\0';
    if (outsz <= 0 || lseek(fd, off, SEEK_SET) != off) return;
    int n = read(fd, out, outsz - 1);
    if (n < 0) n = 0;
    out[n] = '\0';
}

/* Copy the string value of "key":"<value>" into out (NUL-terminated). */
static void json_str(const char *json, const char *key, char *out, unsigned outsz)
{
    out[0] = '\0';
    char pat[24];
    int  pn = 0;
    pat[pn++] = '"';
    for (const char *k = key; *k && pn < (int)sizeof(pat) - 3; k++) pat[pn++] = *k;
    pat[pn++] = '"'; pat[pn++] = ':'; pat[pn] = '\0';

    const char *m = strstr(json, pat);
    if (!m) return;
    m += pn;
    if (*m != '"') return;          /* not a string value */
    m++;
    unsigned i = 0;
    while (*m && *m != '"' && i + 1 < outsz) out[i++] = *m++;
    out[i] = '\0';
}

/* True if capabilities[] contains "display" — scoped to that array so a path
 * like "$SDK/display/gfx.c" in sources[] can't false-match. */
static bool json_caps_display(const char *json)
{
    const char *c = strstr(json, "\"capabilities\":[");
    if (!c) return false;
    c += strlen("\"capabilities\":[");
    const char *end = strchr(c, ']');
    if (!end) return false;
    for (const char *p = c; p + 9 <= end; p++)
        if (strncmp(p, "\"display\"", 9) == 0) return true;
    return false;
}

int appmeta_read(const char *dap_path, appmeta_t *out)
{
    memset(out, 0, sizeof(*out));

    int fd = open(dap_path, O_RDONLY);
    if (fd < 0) return -1;

    unsigned char eh[EH_SIZE];
    if (read_at(fd, 0, eh, EH_SIZE) != 0) { close(fd); return -1; }
    if (eh[0] != 0x7f || eh[1] != 'E' || eh[2] != 'L' || eh[3] != 'F') {
        close(fd); return -1;
    }

    uint32_t shoff     = rd32(eh + EH_SHOFF);
    uint16_t shentsize = rd16(eh + EH_SHENTSIZE);
    uint16_t shnum     = rd16(eh + EH_SHNUM);
    uint16_t shstrndx  = rd16(eh + EH_SHSTRNDX);
    if (shentsize < SH_SIZE || shnum == 0 || shstrndx >= shnum) { close(fd); return -1; }

    /* Section header string table. */
    unsigned char sh[SH_SIZE];
    if (read_at(fd, (long)(shoff + (uint32_t)shstrndx * shentsize), sh, SH_SIZE) != 0) {
        close(fd); return -1;
    }
    uint32_t shstr_off  = rd32(sh + SH_OFFSET);
    uint32_t shstr_size = rd32(sh + SH_SIZE_FIELD);

    uint32_t man_off = 0, man_size = 0;

    if (shentsize == SH_SIZE && shstr_size > 0 && shstr_size <= SHSTR_MAX) {
        /* Fast path: read the whole name table once and the section headers in
         * batches, comparing names in memory. Apps carry ~300 sections, so the
         * naive two-reads-per-section makes a directory scan crawl over FAT. */
        if (read_at(fd, (long)shstr_off, s_shstr, shstr_size) != 0) { close(fd); return -1; }
        s_shstr[shstr_size - 1] = '\0';

        unsigned char batch[SH_SIZE * 16];
        for (uint16_t i = 0; i < shnum && man_size == 0; ) {
            int nb = shnum - i;
            if (nb > 16) nb = 16;
            if (read_at(fd, (long)(shoff + (uint32_t)i * SH_SIZE),
                        batch, (unsigned)nb * SH_SIZE) != 0) { close(fd); return -1; }
            for (int j = 0; j < nb; j++) {
                const unsigned char *e = batch + (size_t)j * SH_SIZE;
                uint32_t name_off = rd32(e + SH_NAME);
                if (name_off < shstr_size &&
                    strcmp(s_shstr + name_off, ".duneos_manifest") == 0) {
                    man_off  = rd32(e + SH_OFFSET);
                    man_size = rd32(e + SH_SIZE_FIELD);
                    break;
                }
            }
            i += nb;
        }
    } else {
        /* Fallback (unusual shentsize or oversized name table): read each name. */
        for (uint16_t i = 0; i < shnum; i++) {
            if (read_at(fd, (long)(shoff + (uint32_t)i * shentsize), sh, SH_SIZE) != 0) {
                close(fd); return -1;
            }
            char nm[24];
            read_name(fd, (long)(shstr_off + rd32(sh + SH_NAME)), nm, sizeof(nm));
            if (strcmp(nm, ".duneos_manifest") == 0) {
                man_off  = rd32(sh + SH_OFFSET);
                man_size = rd32(sh + SH_SIZE_FIELD);
                break;
            }
        }
    }
    if (man_size == 0) { close(fd); return -1; }
    if (man_size > JSON_MAX) man_size = JSON_MAX;
    if (read_at(fd, (long)man_off, s_json, man_size) != 0) { close(fd); return -1; }
    s_json[man_size - 1] = '\0';
    close(fd);

    json_str(s_json, "name", out->name, sizeof(out->name));
    json_str(s_json, "icon", out->icon, sizeof(out->icon));
    out->has_display = json_caps_display(s_json);
    return 0;
}
