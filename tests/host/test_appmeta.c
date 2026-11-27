/*
 * Host suite for the SDK-side manifest reader (DUP-01).
 *
 * sdk/app/libappmeta.c is compiled here as shipped, against the shipped
 * lib/elf/elf_parse.c — not a copy of either (docs/testing.md). What this
 * fixture adds to the kernel-side suites is the only thing they cannot cover:
 * appmeta_read() takes a PATH, opens it itself, and must reject a malformed
 * file before it reports a manifest. Its single consumer is the launcher, which
 * logs nothing on failure, so an image wrongly accepted here shows wrong
 * metadata and an image wrongly rejected vanishes from the grid in silence.
 *
 * Two fixtures, for two different questions:
 *   - elf_corpus, written to disk: the 24 pinned mutations of the kernel-side
 *     corpus, run through the public entry point.
 *   - a locally built manifest-bearing image (Q3(a)) plus named mutations of
 *     it: the corpus images carry no .duneos_manifest section, so they prove
 *     nothing about what happens AFTER the parse succeeds. These do.
 */

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <dirent.h>

#include "duneos/appmeta.h"
#include "duneos/elf.h"
#include "duneos/elf_parse.h"
#include "duneos/loader_limits.h"
#include "elf_corpus.h"
#include "tassert.h"

/* ------------------------------------------------------ temp dir plumbing */

static char g_dir[] = "appmeta_XXXXXX";

static int write_file(const char *name, const void *bytes, size_t len)
{
    char path[512];
    snprintf(path, sizeof(path), "%s/%s", g_dir, name);

    FILE *f = fopen(path, "wb");
    if (!f) { perror(path); return -1; }
    size_t n = fwrite(bytes, 1, len, f);
    if (fclose(f) != 0 || n != len) { perror(path); return -1; }
    return 0;
}

static void rm_dir(void)
{
    DIR *d = opendir(g_dir);
    if (d) {
        struct dirent *ent;
        while ((ent = readdir(d)) != NULL) {
            if (strcmp(ent->d_name, ".") == 0 || strcmp(ent->d_name, "..") == 0)
                continue;
            char path[512];
            snprintf(path, sizeof(path), "%s/%s", g_dir, ent->d_name);
            unlink(path);
        }
        closedir(d);
    }
    rmdir(g_dir);
}

/*
 * A fixture that could not be written is not a test result. Returning an error
 * code here would satisfy every `CHECK(rc != 0)` below and skip every positive
 * assertion, so the suite would read green on a broken fixture (LEG-38,
 * instance 4 — the shape CLAUDE.md names as the worst, because nothing goes
 * red). Die loudly instead.
 */
static void write_fixture(const char *name, const void *bytes, size_t len)
{
    if (write_file(name, bytes, len) != 0) {
        fprintf(stderr, "  FATAL %s: fixture '%s' could not be written\n",
                __FILE__, name);
        rm_dir();
        exit(1);
    }
}

/* ------------------------------------------- a manifest-bearing ELF image */

/*
 * Four sections: 0 SHN_UNDEF, 1 .text, 2 .duneos_manifest, 3 .shstrtab. The
 * manifest payload is variable-length, so every offset past it is computed
 * rather than tabulated — which is also what keeps this fixture independent of
 * elf_corpus.c's pinned layout (Q3(a)).
 */
#define IMG_MAX 16384

typedef struct {
    unsigned char b[IMG_MAX];
    size_t        size;
    size_t        shdrs_off;
} img_t;

enum { SEC_NULL = 0, SEC_TEXT_IDX = 1, SEC_MAN = 2, SEC_SHSTR = 3,
       IMG_N_SECTIONS = 4 };

enum { OFF_TEXT = 52, SZ_TEXT = 16 };

/* "\0" ".text\0" ".duneos_manifest\0" ".shstrtab\0" */
enum { NAME_TEXT = 1, NAME_MAN = 7, NAME_SHSTR = 24, SZ_SHSTR = 34 };

static const char k_shstr[SZ_SHSTR] =
    "\0" ".text\0" ".duneos_manifest\0" ".shstrtab";

static elf32_hdr_t *hdr_of(img_t *img)
{
    return (elf32_hdr_t *)(void *)img->b;
}

static elf32_shdr_t *shdr_of(img_t *img, int i)
{
    return (elf32_shdr_t *)(void *)(img->b + img->shdrs_off) + i;
}

/* Builds a well-formed ET_REL image carrying `json` (without its terminating
 * NUL — appmeta_read() supplies its own) as .duneos_manifest. */
static void build_image(img_t *img, const char *json, size_t man_len)
{
    memset(img, 0, sizeof(*img));

    size_t man_off   = OFF_TEXT + SZ_TEXT;
    size_t shstr_off = man_off + man_len;
    size_t shdrs_off = (shstr_off + SZ_SHSTR + 3u) & ~(size_t)3u;

    img->shdrs_off = shdrs_off;
    img->size      = shdrs_off + IMG_N_SECTIONS * sizeof(elf32_shdr_t);

    elf32_hdr_t *h = hdr_of(img);
    memcpy(h->e_ident, ELF_MAGIC, ELF_MAGIC_SIZE);
    h->e_ident[EI_CLASS] = ELFCLASS32;
    h->e_ident[EI_DATA]  = ELFDATA2LSB;
    h->e_type      = ET_REL;
    h->e_machine   = EM_XTENSA;
    h->e_version   = 1;
    h->e_shoff     = (uint32_t)shdrs_off;
    h->e_ehsize    = sizeof(elf32_hdr_t);
    h->e_shentsize = sizeof(elf32_shdr_t);
    h->e_shnum     = IMG_N_SECTIONS;
    h->e_shstrndx  = SEC_SHSTR;

    memcpy(img->b + man_off,   json,    man_len);
    memcpy(img->b + shstr_off, k_shstr, SZ_SHSTR);

    elf32_shdr_t *s;

    s = shdr_of(img, SEC_TEXT_IDX);
    s->sh_name      = NAME_TEXT;
    s->sh_type      = SHT_PROGBITS;
    s->sh_flags     = SHF_ALLOC | SHF_EXECINSTR;
    s->sh_offset    = OFF_TEXT;
    s->sh_size      = SZ_TEXT;
    s->sh_addralign = 4;

    s = shdr_of(img, SEC_MAN);
    s->sh_name      = NAME_MAN;
    s->sh_type      = SHT_PROGBITS;
    s->sh_offset    = (uint32_t)man_off;
    s->sh_size      = (uint32_t)man_len;
    s->sh_addralign = 1;

    s = shdr_of(img, SEC_SHSTR);
    s->sh_name      = NAME_SHSTR;
    s->sh_type      = SHT_STRTAB;
    s->sh_offset    = (uint32_t)shstr_off;
    s->sh_size      = SZ_SHSTR;
    s->sh_addralign = 1;
}

/* Write img as <name>.dap and read it back through the public entry point. */
static int read_image(const char *name, const img_t *img, appmeta_t *out)
{
    char fname[256];
    snprintf(fname, sizeof(fname), "%s.dap", name);
    write_fixture(fname, img->b, img->size);

    char path[512];
    snprintf(path, sizeof(path), "%s/%s", g_dir, fname);
    return appmeta_read(path, out);
}

/* Build the reference manifest image, apply a mutation, read it back. */
static int read_mutated(const char *name, void (*mutate)(img_t *))
{
    static const char json[] =
        "{\"name\":\"Ref\",\"capabilities\":[\"display\"],\"launchable\":true}";

    img_t img;
    build_image(&img, json, sizeof(json) - 1);
    if (mutate) mutate(&img);

    appmeta_t meta;
    return read_image(name, &img, &meta);
}

/* ----------------------------------------------------------- criterion 3 */

/*
 * Every elf_corpus image, through appmeta_read().
 *
 * The reject set criterion 3 names is "what the loader's own scan path rejects,
 * intersected with having a manifest section". No corpus image carries a
 * .duneos_manifest section — it is a loader corpus, not a launcher one — so
 * that intersection is empty and the whole corpus must be rejected. The four
 * images test_elf_scan.c pins as ACCEPTED by the scan are rejected here for the
 * missing section rather than for their mutation; what the named mutations
 * below cover is the other half, where the parse is what has to say no.
 */
static void test_corpus_images_are_rejected(void)
{
    for (size_t i = 0; i < elf_corpus_count; i++) {
        elf_corpus_image_t img;
        elf_corpus_build(&elf_corpus[i], &img);

        char fname[256];
        snprintf(fname, sizeof(fname), "corpus_%s.dap", elf_corpus[i].name);
        write_fixture(fname, img.bytes, img.size);

        char path[512];
        snprintf(path, sizeof(path), "%s/%s", g_dir, fname);

        appmeta_t meta;
        int rc = appmeta_read(path, &meta);
        if (rc == 0)
            fprintf(stderr, "  appmeta_read accepted corpus image '%s'\n",
                    elf_corpus[i].name);
        CHECK(rc != 0);
    }
}

static void m_elfclass64(img_t *img)   { hdr_of(img)->e_ident[EI_CLASS] = 2; }
static void m_elfdata_msb(img_t *img)  { hdr_of(img)->e_ident[EI_DATA]  = 2; }
static void m_not_et_rel(img_t *img)   { hdr_of(img)->e_type = 2; }
static void m_bad_magic(img_t *img)    { hdr_of(img)->e_ident[1] = 'X'; }
static void m_no_sections(img_t *img)  { hdr_of(img)->e_shoff = 0; }

static void m_shnum_over_max(img_t *img)
{
    hdr_of(img)->e_shnum = (uint16_t)(DUNEOS_LOADER_MAX_SECTIONS + 1u);
}

/* The 65535-iteration form the hand-written reader walked, two reads per
 * iteration, on FAT. e_shnum is 16-bit, so this is its ceiling. */
static void m_shnum_max_u16(img_t *img)
{
    hdr_of(img)->e_shnum = 0xffffu;
}

static void m_bad_shentsize(img_t *img)
{
    hdr_of(img)->e_shentsize = sizeof(elf32_shdr_t) - 8;
}

static void m_sh_offset_past_eof(img_t *img)
{
    shdr_of(img, SEC_TEXT_IDX)->sh_offset = 0x7fff0000u;
}

/* The wrapping form: sh_offset + sh_size is 0x100 in uint32 arithmetic, so a
 * naive `sh_offset + sh_size > size` accepts it. */
static void m_sh_extent_wraps(img_t *img)
{
    elf32_shdr_t *s = shdr_of(img, SEC_TEXT_IDX);
    s->sh_offset = 0xffffff00u;
    s->sh_size   = 0x00000200u;
}

static void m_sh_size_past_eof(img_t *img)
{
    elf32_shdr_t *s = shdr_of(img, SEC_TEXT_IDX);
    s->sh_offset = OFF_TEXT;
    s->sh_size   = (uint32_t)img->size;
}

/* The manifest section's OWN extent, past EOF: the one the reader would then
 * read the payload from. */
static void m_manifest_offset_past_eof(img_t *img)
{
    shdr_of(img, SEC_MAN)->sh_offset = 0x7fff0000u;
}

static void m_sh_name_past_shstrtab(img_t *img)
{
    shdr_of(img, SEC_TEXT_IDX)->sh_name = SZ_SHSTR + 4096;
}

static void m_shstrndx_ge_shnum(img_t *img)
{
    hdr_of(img)->e_shstrndx = IMG_N_SECTIONS + 7;
}

static void m_shstrtab_size_huge(img_t *img)
{
    shdr_of(img, SEC_SHSTR)->sh_size = 0x94000001u;
}

static void test_named_malformations_are_rejected(void)
{
    static const struct { const char *name; void (*mutate)(img_t *); } cases[] = {
        { "bad_magic",               m_bad_magic },
        { "elfclass64",              m_elfclass64 },
        { "elfdata_msb",             m_elfdata_msb },
        { "not_et_rel",              m_not_et_rel },
        { "no_sections",             m_no_sections },
        { "shnum_over_max",          m_shnum_over_max },
        { "shnum_max_u16",           m_shnum_max_u16 },
        { "bad_shentsize",           m_bad_shentsize },
        { "shstrndx_ge_shnum",       m_shstrndx_ge_shnum },
        { "sh_offset_past_eof",      m_sh_offset_past_eof },
        { "sh_extent_wraps",         m_sh_extent_wraps },
        { "sh_size_past_eof",        m_sh_size_past_eof },
        { "manifest_offset_past_eof", m_manifest_offset_past_eof },
        { "sh_name_past_shstrtab",   m_sh_name_past_shstrtab },
        { "shstrtab_size_huge",      m_shstrtab_size_huge },
    };

    for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
        int rc = read_mutated(cases[i].name, cases[i].mutate);
        if (rc == 0)
            fprintf(stderr, "  appmeta_read accepted malformed image '%s'\n",
                    cases[i].name);
        CHECK(rc != 0);
    }
}

/* Q8: the loader's 2..4096 bound, enforced by rejection rather than by the
 * silent truncation at 2048 the hand-written reader applied. */
static void test_manifest_size_bound(void)
{
    char big[5000];
    memset(big, 'x', sizeof(big));
    memcpy(big, "{\"name\":\"TooBig\",\"pad\":\"", 24);
    big[sizeof(big) - 2] = '"';
    big[sizeof(big) - 1] = '}';

    img_t     img;
    appmeta_t meta;
    build_image(&img, big, sizeof(big));
    CHECK(read_image("manifest_too_big", &img, &meta) != 0);
    CHECK_STR(meta.name, "");

    /* At the bound: 4096 bytes is accepted, and the fields past 2048 — where
     * the old reader cut — are still read. */
    char at_max[4096];
    memset(at_max, ' ', sizeof(at_max));
    memcpy(at_max, "{\"name\":\"AtMax\",", 16);
    static const char tail[] = "\"launchable\":false}";
    memcpy(at_max + sizeof(at_max) - (sizeof(tail) - 1), tail, sizeof(tail) - 1);
    build_image(&img, at_max, sizeof(at_max));
    CHECK_INT(read_image("manifest_at_max", &img, &meta), 0);
    CHECK_STR(meta.name, "AtMax");
    CHECK_INT(meta.launchable, 0);

    /* Below it: a 1-byte section is not a manifest. */
    build_image(&img, "{", 1);
    CHECK(read_image("manifest_too_small", &img, &meta) != 0);

    /* No .duneos_manifest section at all. */
    build_image(&img, "{\"name\":\"X\"}", 12);
    shdr_of(&img, SEC_MAN)->sh_name = NAME_TEXT;
    CHECK(read_image("manifest_absent", &img, &meta) != 0);
}

/* ----------------------------------------------------------- criterion 4 */

static void test_wellformed_is_accepted(void)
{
    /* The shape tools/dbt/builder.py emits: compact, no whitespace. */
    static const char json[] =
        "{\"name\":\"Snake\",\"version\":\"0.1.0\",\"icon\":\"/share/icons/snake.dr\","
        "\"permissions\":352,\"capabilities\":[\"display\",\"input\"],"
        "\"launchable\":true}";

    img_t     img;
    appmeta_t meta;
    build_image(&img, json, sizeof(json) - 1);

    CHECK_INT(read_image("wellformed", &img, &meta), 0);
    CHECK_STR(meta.name, "Snake");
    CHECK_STR(meta.icon, "/share/icons/snake.dr");
    CHECK_INT(meta.has_display, 1);
    CHECK_INT(meta.launchable, 1);
}

/* ----------------------------------------------------------- criterion 5 */

/* Read one manifest body through the public entry point. */
static int read_json(const char *name, const char *json, appmeta_t *out)
{
    img_t img;
    build_image(&img, json, strlen(json));
    return read_image(name, &img, out);
}

static void test_json_field_scan(void)
{
    appmeta_t m;

    /* Absent keys keep the documented defaults. */
    CHECK_INT(read_json("json_absent", "{\"version\":\"1\"}", &m), 0);
    CHECK_STR(m.name, "");
    CHECK_STR(m.icon, "");
    CHECK_INT(m.has_display, 0);
    CHECK_INT(m.launchable, 1);     /* default true (appmeta.h) */

    /* A key whose value is not a string yields "", not the digits. */
    CHECK_INT(read_json("json_name_number", "{\"name\":123,\"icon\":[\"a\"]}", &m), 0);
    CHECK_STR(m.name, "");
    CHECK_STR(m.icon, "");

    /* A boolean key whose value is neither true nor false keeps the default. */
    CHECK_INT(read_json("json_launchable_junk", "{\"launchable\":\"no\"}", &m), 0);
    CHECK_INT(m.launchable, 1);
    CHECK_INT(read_json("json_launchable_false", "{\"launchable\":false}", &m), 0);
    CHECK_INT(m.launchable, 0);

    /* A value truncated at the section end: the scan stops at the NUL
     * appmeta_read() wrote past the payload, and returns what it had. The
     * assertion is the content, not the absence of a crash. */
    CHECK_INT(read_json("json_truncated_value", "{\"name\":\"abcdef", &m), 0);
    CHECK_STR(m.name, "abcdef");

    /* A value longer than the field is truncated to the field, NUL included. */
    CHECK_INT(read_json("json_long_name",
                        "{\"name\":\"0123456789012345678901234567890123456789\"}",
                        &m), 0);
    CHECK_INT((int)strlen(m.name), (int)sizeof(m.name) - 1);
    CHECK_STR(m.name, "0123456789012345678901234567890");

    /* An unclosed capabilities array is not a capability list. */
    CHECK_INT(read_json("json_caps_unclosed",
                        "{\"capabilities\":[\"display\"", &m), 0);
    CHECK_INT(m.has_display, 0);

    /* The scoping the comment claims: a "display" token outside the array —
     * here the sources[] path that motivated the scope — does not match. */
    CHECK_INT(read_json("json_display_in_sources",
                        "{\"sources\":[\"$SDK/display/gfx.c\",\"display\"],"
                        "\"capabilities\":[\"input\"]}", &m), 0);
    CHECK_INT(m.has_display, 0);

    /* ...and the same manifest with display declared really does match. */
    CHECK_INT(read_json("json_display_declared",
                        "{\"sources\":[\"$SDK/display/gfx.c\"],"
                        "\"capabilities\":[\"input\",\"display\"]}", &m), 0);
    CHECK_INT(m.has_display, 1);

    /* No capabilities key at all. */
    CHECK_INT(read_json("json_caps_absent", "{\"name\":\"n\"}", &m), 0);
    CHECK_INT(m.has_display, 0);
}

/* ------------------------------------------------------------------ main */

static void test_missing_file(void)
{
    appmeta_t m;
    CHECK(appmeta_read("appmeta_no_such_dir/nope.dap", &m) != 0);
    CHECK_STR(m.name, "");
}

int main(void)
{
    if (mkdtemp(g_dir) == NULL) {
        perror("mkdtemp");
        return 1;
    }

    test_missing_file();
    test_corpus_images_are_rejected();
    test_named_malformations_are_rejected();
    test_manifest_size_bound();
    test_wellformed_is_accepted();
    test_json_field_scan();

    rm_dir();
    return t_report("test_appmeta");
}
