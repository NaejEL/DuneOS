/*
 * Directory-scan harness for the hardened ELF validation unit (SPEC-leg-01,
 * criterion 6): duneos_loader_scan() applied to a directory holding malformed
 * files must terminate normally, skip them, and still list the valid apps.
 *
 * SCOPE, stated plainly: duneos_loader_scan() itself lives in loader.c, which
 * pulls in ESP-IDF, FreeRTOS, cJSON and klog and cannot be linked on the host.
 * What is reproduced here is its per-file contract — the readdir loop, the
 * extension filter, and the read_manifest_from_file() body up to and including
 * duneos_elf_image_open() plus the section-name walk extract_manifest() does.
 * That is exactly the surface this spec hardens; everything past it (cJSON
 * parsing, the ABI check, the name dedup) is untouched by the change and is
 * covered on target by the QEMU bench. This harness is a real filesystem scan
 * over real files, not an in-memory replay: the corpus is written to disk with
 * the extensions the scanner filters on, alongside files it must ignore.
 *
 * The limits and the name filter are NOT copied from loader.c: both sides take
 * them from <duneos/loader_limits.h>, so there is one definition to change and
 * no way for the harness to drift out of step with the scanner. What the
 * fixture additionally assumes about that filter is pinned by
 * test_scan_filter_contract() below.
 */

#include <dirent.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>

#include "duneos/elf.h"
#include "duneos/elf_parse.h"
#include "duneos/loader_limits.h"
#include "elf_corpus.h"
#include "tassert.h"

/* The corpus is built as Xtensa ET_REL, so the harness names that ISA's
 * machine explicitly: no CONFIG_IDF_TARGET_ARCH_* exists off-target. */
#define SCAN_EXPECT_MACHINE DUNEOS_LOADER_MACHINE_XTENSA

#define DUNEOS_MANIFEST_SECTION ".duneos_manifest"

/*
 * Which corpus images a scan of a directory must accept.
 *
 * A file is "accepted" when the scan gets far enough to look for a manifest
 * section, i.e. exactly when read_manifest_from_file() does not bail out on
 * duneos_elf_image_open(). Four images are accepted on purpose:
 *   valid                   — the legitimate object (criterion 2);
 *   st_name_past_strtab     — the defect is in the symbol string table, which
 *                             the scan path never reads; it is caught at load
 *                             time instead, by the st_name bound in loader.c;
 *   sh_offset_size_overflow — LEG-34, out of this spec's scope;
 *   symtab_size_zero        — LEG-35, out of this spec's scope.
 * Any other image reaching the manifest lookup is a hole in the hardening.
 */
static const char *const k_accepted[] = {
    "valid",
    "st_name_past_strtab",
    "sh_offset_size_overflow",
    "symtab_size_zero",
    NULL,
};

static int expect_accepted(const char *name)
{
    for (int i = 0; k_accepted[i]; i++) {
        if (strcmp(k_accepted[i], name) == 0) return 1;
    }
    return 0;
}

/* ------------------------------------------------------------ the scan path */

static int file_read_at(void *ctx, long offset, void *buf, size_t len)
{
    FILE *f = ctx;
    if (fseek(f, offset, SEEK_SET) != 0) return -EIO;
    return fread(buf, 1, len, f) == len ? 0 : -EIO;
}

/*
 * read_manifest_from_file() without cJSON: open the image, then walk the
 * section names exactly as extract_manifest() does. Returns 0 when the file
 * would be listed by the scan, -errno when it would be skipped.
 */
static int scan_read_manifest(const char *path)
{
    FILE *f = fopen(path, "rb");
    if (!f) return -ENOENT;

    const duneos_elf_io_t io = { .read = file_read_at, .ctx = f };
    duneos_elf_image_t  img;
    duneos_elf_reject_t why = DUNEOS_ELF_REJ_NONE;

    int rc = duneos_elf_image_open(&io, SCAN_EXPECT_MACHINE,
                                   DUNEOS_LOADER_MAX_SECTIONS, &img, &why);
    if (rc == 0) {
        for (int i = 0; i < img.hdr.e_shnum; i++) {
            const char *name = duneos_elf_section_name(&img, &img.shdrs[i]);
            /* The invariant a successfully opened image now guarantees; the
             * lookup itself is extract_manifest()'s loop. */
            CHECK(name != NULL);
            if (name) (void)strcmp(name, DUNEOS_MANIFEST_SECTION);
        }
    }

    fclose(f);
    duneos_elf_image_close(&img);
    return rc;
}

/* ------------------------------------------------------------- the fixture */

static int write_file(const char *dir, const char *name,
                      const void *bytes, size_t len)
{
    char path[512];
    snprintf(path, sizeof(path), "%s/%s", dir, name);

    FILE *f = fopen(path, "wb");
    if (!f) { perror(path); return -1; }
    size_t n = fwrite(bytes, 1, len, f);
    if (fclose(f) != 0 || n != len) { perror(path); return -1; }
    return 0;
}

/* Every corpus image as <case>.dap, plus files the filter must ignore. */
static int stage_dir(const char *dir)
{
    for (size_t i = 0; i < elf_corpus_count; i++) {
        elf_corpus_image_t img;
        elf_corpus_build(&elf_corpus[i], &img);

        char name[256];
        snprintf(name, sizeof(name), "%s.dap", elf_corpus[i].name);
        if (write_file(dir, name, img.bytes, img.size) != 0) return -1;
    }

    static const char junk[] = "not an ELF at all\n";
    if (write_file(dir, "notes.txt", junk, sizeof(junk) - 1) != 0) return -1;
    if (write_file(dir, "README",    junk, sizeof(junk) - 1) != 0) return -1;
    /* Shorter than the 5-character minimum the scanner requires. */
    if (write_file(dir, ".dap",      junk, sizeof(junk) - 1) != 0) return -1;
    return 0;
}

static void unstage_dir(const char *dir)
{
    DIR *d = opendir(dir);
    if (d) {
        struct dirent *ent;
        while ((ent = readdir(d)) != NULL) {
            if (strcmp(ent->d_name, ".") == 0 || strcmp(ent->d_name, "..") == 0)
                continue;
            char path[512];
            snprintf(path, sizeof(path), "%s/%s", dir, ent->d_name);
            unlink(path);
        }
        closedir(d);
    }
    rmdir(dir);
}

/* ---------------------------------------------------------------- the test */

/*
 * The fixture below stages exactly three files it expects the name filter to
 * reject, and counts on every corpus image being accepted as "<case>.dap".
 * Those are assumptions about duneos_loader_name_is_app(), which now belongs to
 * the loader: a change there that breaks one fails here by name instead of
 * quietly turning a filtered file into a scanned one (or the reverse) and
 * leaving only the CHECK_INT totals to shift.
 */
static void test_scan_filter_contract(void)
{
    CHECK(duneos_loader_name_is_app("valid.dap"));
    CHECK(duneos_loader_name_is_app("VALID.DAP"));
    CHECK(duneos_loader_name_is_app("valid.elf"));
    CHECK(duneos_loader_name_is_app("a.dap"));

    CHECK(!duneos_loader_name_is_app("notes.txt"));
    CHECK(!duneos_loader_name_is_app("README"));
    CHECK(!duneos_loader_name_is_app(".dap"));
    CHECK(!duneos_loader_name_is_app(""));
}

static void test_scan_skips_malformed(const char *dir)
{
    DIR *d = opendir(dir);
    CHECK(d != NULL);
    if (!d) return;

    int seen = 0, listed = 0, skipped = 0, filtered = 0;
    int listed_valid = 0;

    struct dirent *ent;
    while ((ent = readdir(d)) != NULL) {
        if (strcmp(ent->d_name, ".") == 0 || strcmp(ent->d_name, "..") == 0)
            continue;
        seen++;

        if (!duneos_loader_name_is_app(ent->d_name)) { filtered++; continue; }
        size_t len = strlen(ent->d_name);

        char path[512];
        snprintf(path, sizeof(path), "%s/%s", dir, ent->d_name);

        int rc = scan_read_manifest(path);

        char base[256];
        snprintf(base, sizeof(base), "%.*s", (int)(len - 4), ent->d_name);

        if (rc == 0) {
            listed++;
            if (strcmp(base, "valid") == 0) listed_valid = 1;
            if (!expect_accepted(base)) {
                fprintf(stderr, "  scan listed '%s', which must be skipped\n",
                        base);
            }
            CHECK(expect_accepted(base));
        } else {
            skipped++;
            if (expect_accepted(base)) {
                fprintf(stderr, "  scan skipped '%s' (rc=%d), which must be "
                                "listed\n", base, rc);
            }
            CHECK(!expect_accepted(base));
        }
    }
    closedir(d);

    /* Terminated normally over every entry, ignored the three non-app files. */
    CHECK_INT(seen, (int)elf_corpus_count + 3);
    CHECK_INT(filtered, 3);
    CHECK_INT(listed + skipped, (int)elf_corpus_count);

    /* The valid application is still listed despite its malformed neighbours. */
    CHECK_INT(listed_valid, 1);

    int want_listed = 0;
    for (int i = 0; k_accepted[i]; i++) want_listed++;
    CHECK_INT(listed, want_listed);
    CHECK_INT(skipped, (int)elf_corpus_count - want_listed);
}

int main(void)
{
    char dir[] = "elf_scan_XXXXXX";
    if (mkdtemp(dir) == NULL) {
        perror("mkdtemp");
        return 1;
    }

    if (stage_dir(dir) != 0) {
        unstage_dir(dir);
        return 1;
    }

    test_scan_filter_contract();
    test_scan_skips_malformed(dir);
    unstage_dir(dir);

    return t_report("test_elf_scan");
}
