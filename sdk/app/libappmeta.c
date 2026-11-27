/*
 * libappmeta — see <duneos/appmeta.h>.
 *
 * The ELF32 read is the shared lib/elf unit (ADR 043), the same one the kernel
 * loader uses: there is no second reader to harden, fuzz or forget. What stays
 * here is the field extraction, because the manifest JSON is the compact form
 * emitted by tools/dbt/builder.py (no whitespace) and a scoped string scan is
 * what an app can afford where the kernel spends cJSON.
 */

#include "duneos/appmeta.h"

#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "duneos/elf_parse.h"
#include "duneos/loader_limits.h"

#define MANIFEST_SECTION ".duneos_manifest"

/* The loader's own bound (extract_manifest() in loader.c). Rejecting outside
 * it rather than truncating is the point: a truncated manifest loses every
 * field past the cut and the launcher shows the defaults instead. */
#define MANIFEST_MIN 2u
#define MANIFEST_MAX 4096u

static int fd_read_at(void *ctx, long offset, void *buf, size_t len)
{
    int fd = *(const int *)ctx;

    if (lseek(fd, offset, SEEK_SET) != offset) return -EIO;

    size_t         got = 0;
    unsigned char *p   = buf;
    while (got < len) {
        ssize_t n = read(fd, p + got, len - got);
        if (n <= 0) return -EIO;
        got += (size_t)n;
    }
    return 0;
}

/* duneos_elf_io_t.size bounds every section extent, so it must describe
 * exactly the bytes fd_read_at() can serve. */
static size_t fd_size_of(int fd)
{
    off_t end = lseek(fd, 0, SEEK_END);
    return end < 0 ? 0u : (size_t)end;
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

/* Read a boolean "key":true/false. Returns `dflt` when the key is absent or
 * malformed — so a manifest built before the field existed keeps the default. */
static bool json_bool(const char *json, const char *key, bool dflt)
{
    char pat[24];
    int  pn = 0;
    pat[pn++] = '"';
    for (const char *k = key; *k && pn < (int)sizeof(pat) - 3; k++) pat[pn++] = *k;
    pat[pn++] = '"'; pat[pn++] = ':'; pat[pn] = '\0';

    const char *m = strstr(json, pat);
    if (!m) return dflt;
    m += pn;
    if (strncmp(m, "true", 4) == 0)  return true;
    if (strncmp(m, "false", 5) == 0) return false;
    return dflt;
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

    const duneos_elf_io_t io = { .read = fd_read_at, .ctx = &fd,
                                 .size = fd_size_of(fd) };
    duneos_elf_image_t  img;
    duneos_elf_reject_t why = DUNEOS_ELF_REJ_NONE;

    /* expect_machine 0 (Q6): the SDK has no CONFIG_IDF_TARGET_*, and a foreign
     * ISA is reported by the loader at launch rather than hidden from the grid
     * at scan time. Class, data and ET_REL are enforced. */
    if (duneos_elf_image_open(&io, 0, DUNEOS_LOADER_MAX_SECTIONS,
                              &img, &why) != 0) {
        close(fd);
        return -1;
    }

    uint32_t man_off = 0, man_size = 0;
    for (uint16_t i = 0; i < img.hdr.e_shnum; i++) {
        const char *name = duneos_elf_section_name(&img, &img.shdrs[i]);
        if (name && strcmp(name, MANIFEST_SECTION) == 0) {
            man_off  = img.shdrs[i].sh_offset;
            man_size = img.shdrs[i].sh_size;
            break;
        }
    }

    /* Released before the manifest allocation rather than after it: the
     * launcher's per-app pool is 8 KiB (ADR 025) and the two buffers need not
     * be live at the same time. */
    duneos_elf_image_close(&img);

    if (man_size < MANIFEST_MIN || man_size > MANIFEST_MAX) {
        close(fd);
        return -1;
    }

    char *json = malloc((size_t)man_size + 1u);
    if (!json) { close(fd); return -1; }
    if (fd_read_at(&fd, (long)man_off, json, man_size) != 0) {
        free(json);
        close(fd);
        return -1;
    }
    json[man_size] = '\0';
    close(fd);

    json_str(json, "name", out->name, sizeof(out->name));
    json_str(json, "icon", out->icon, sizeof(out->icon));
    out->has_display = json_caps_display(json);
    out->launchable  = json_bool(json, "launchable", true);
    free(json);
    return 0;
}
