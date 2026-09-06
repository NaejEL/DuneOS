/*
 * Host suite for the pure ELF validation unit (LEG-26).
 *
 * Two parts: direct assertions on duneos_elf_validate(), then the malformed-ELF
 * corpus driven end to end through duneos_elf_image_open(). Everything runs from
 * in-memory buffers — no filesystem access.
 *
 * WHY every corpus case runs in a forked child: on the current commit some cases
 * are known to read out of bounds (elf_parse.c:76), which is a crash under ASan
 * and undefined behaviour without it. A crash in the parent would take the whole
 * suite — and CI — down, and the crash of a case that is already marked as an
 * expected failure is precisely the defect being documented, not a suite error.
 * The child's exit status turns a signal into an observation.
 *
 * Expected-failure policy: a marked case that starts PASSING fails the suite.
 * The marker is temporary by construction — the LEG that fixes the defect must
 * delete it in the same change, and CI turns red until it does. Deleting a case
 * outright is caught instead by the two size pins in elf_corpus.h.
 *
 * LEG-01, LEG-02 and LEG-03 are three FINDINGS inside one spec,
 * specs/SPEC-leg-01-harden-elf-validation.md — not three specs. Its criterion 1
 * covers all three, so the four LEG-* markers below all go in a single change.
 * An UNPLANNED owner, by contrast, means no spec covers the defect at all —
 * only a docs/backlog.md entry does.
 */

#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

#include "duneos/loader_limits.h"

#include "elf_corpus.h"
#include "tassert.h"

static void test_validate_direct(void)
{
    elf32_hdr_t hdr;
    duneos_elf_reject_t why;

    memset(&hdr, 0, sizeof(hdr));
    memcpy(hdr.e_ident, ELF_MAGIC, ELF_MAGIC_SIZE);
    hdr.e_ident[EI_CLASS] = ELFCLASS32;
    hdr.e_ident[EI_DATA]  = ELFDATA2LSB;
    hdr.e_type    = ET_REL;
    hdr.e_machine = EM_XTENSA;
    hdr.e_shoff   = 52;
    hdr.e_shnum   = 5;

    why = DUNEOS_ELF_REJ_MAGIC;
    CHECK_INT(duneos_elf_validate(&hdr, EM_XTENSA, &why), 0);
    CHECK_INT(why, DUNEOS_ELF_REJ_MAGIC);   /* untouched on success */

    /* expect_machine 0 waives the machine check (LOADER_EXPECT_MACHINE on a
     * target with no arch macro). */
    hdr.e_machine = EM_RISCV;
    CHECK_INT(duneos_elf_validate(&hdr, 0, &why), 0);
    CHECK_INT(duneos_elf_validate(&hdr, EM_XTENSA, &why), -ENOTSUP);
    CHECK_INT(why, DUNEOS_ELF_REJ_MACHINE);
    hdr.e_machine = EM_XTENSA;

    hdr.e_shnum = 0;
    CHECK_INT(duneos_elf_validate(&hdr, EM_XTENSA, &why), -EINVAL);
    CHECK_INT(why, DUNEOS_ELF_REJ_NO_SECTIONS);
}

/*
 * SPEC-leg-34 criterion 3, made explicit rather than left to the corpus verdict.
 *
 * The natural way to bound a section extent is `sh_offset + sh_size > size`,
 * and it is exactly the defect: both fields are uint32, so the sum wraps. This
 * asserts the wrap first — the naive check really would accept this image —
 * then that the unit rejects it anyway. Without the first assertion, a corpus
 * case turning green proves nothing about which arithmetic produced it.
 */
static void test_extent_arithmetic(void)
{
    const elf_corpus_case_t *c = NULL;
    for (size_t i = 0; i < elf_corpus_count; i++) {
        if (strcmp(elf_corpus[i].name, "sh_offset_size_overflow") == 0)
            c = &elf_corpus[i];
    }
    CHECK(c != NULL);
    if (!c) return;

    elf_corpus_image_t img;
    elf_corpus_build(c, &img);

    const elf32_hdr_t *h = (const elf32_hdr_t *)(const void *)img.bytes;
    const elf32_shdr_t *sh =
        (const elf32_shdr_t *)(const void *)(img.bytes + h->e_shoff) + 1;

    CHECK_INT(sh->sh_offset, 0xffffff00u);
    CHECK_INT(sh->sh_size,   0x00000200u);

    /* The wrong check, spelled out: the sum wraps to 0x100, which is inside a
     * 348-byte image, so `sh_offset + sh_size > size` would accept it. */
    uint32_t naive = sh->sh_offset + sh->sh_size;
    CHECK_INT(naive, 0x100u);
    CHECK(!(naive > img.size));

    /* The right check, both halves, in the order that makes the subtraction
     * safe. */
    CHECK(sh->sh_offset > img.size);

    duneos_elf_reject_t why = DUNEOS_ELF_REJ_NONE;
    CHECK_INT(elf_corpus_probe_open(&img, &why), -EINVAL);
    CHECK_INT(why, DUNEOS_ELF_REJ_SH_OFFSET);
}

/*
 * Pins the accumulator guard of load_sections() pass 1 (loader.c).
 *
 * The guard itself is not reachable from this suite: load_sections() lives in
 * the target-only half of the loader, and the input that reaches it is a .dap
 * of at least 4 MiB carrying DUNEOS_LOADER_MAX_SECTIONS sections — not a corpus
 * image this harness can build cheaply. What is pinned here is the arithmetic,
 * the same way test_extent_arithmetic() pins the extent check: the wrong shape
 * next to the right one.
 *
 * size_t is 32-bit on the target and 64-bit on this host, so the target's
 * accumulator is reproduced in uint32_t.
 */
static void test_section_total_arithmetic(void)
{
    /* LOADER_MAX_SECTION_BYTES on a CONFIG_SPIRAM board, and MAX_SECTIONS. */
    const uint32_t cap      = 4u * 1024u * 1024u;
    const uint32_t sections = DUNEOS_LOADER_MAX_SECTIONS;

    /* The unguarded accumulator: 1024 sections of exactly 4 MiB sum to 2^32 and
     * wrap to 0 — no pool is allocated and pass 2 writes through NULL. Every
     * per-section value here is under the ceiling, so the per-section cap does
     * not see it. */
    uint32_t naive = 0;
    for (uint32_t i = 0; i < sections; i++) naive += (cap + 3u) & ~3u;
    CHECK_INT(naive, 0u);

    /* The guard: reject before adding, on the form that cannot itself
     * overflow. */
    uint32_t total       = 0;
    uint32_t rejected_at = sections;
    for (uint32_t i = 0; i < sections; i++) {
        uint32_t rounded = (cap + 3u) & ~3u;
        if (total > UINT32_MAX - rounded) { rejected_at = i; break; }
        total += rounded;
    }
    CHECK_INT(rejected_at, sections - 1u);
    CHECK_INT(total, 0xffc00000u);

    /* And it does not fire on a total that merely fails to allocate: that one
     * must reach the malloc and be reported as -ENOMEM, not as a bad image. */
    total = 0;
    for (uint32_t i = 0; i < sections - 1u; i++) {
        uint32_t rounded = (cap + 3u) & ~3u;
        CHECK(!(total > UINT32_MAX - rounded));
        total += rounded;
    }
}

/* ------------------------------------------------------------ corpus driver */

/*
 * OUT_ERROR is not an observation about the case: it means the harness could
 * not run it at all (fork/waitpid failed). It must never be absorbed by an
 * expected-failure marker, or a runner too loaded to fork would report six
 * XFAILs and exit 0 having executed nothing.
 */
typedef enum { OUT_PASS, OUT_FAIL, OUT_CRASH, OUT_ERROR } outcome_t;

static int child_run(const elf_corpus_case_t *c)
{
    elf_corpus_image_t img;
    elf_corpus_build(c, &img);

    duneos_elf_reject_t why = DUNEOS_ELF_REJ_NONE;
    int rc = c->probe(&img, &why);

    int bad = 0;

    if (rc != c->want_rc) {
        fprintf(stderr, "    rc = %d (want %d)\n", rc, c->want_rc);
        bad = 1;
    }
    switch (c->why_mode) {
    case ELF_CORPUS_WHY_EXACT:
        if (why != c->want_why) {
            fprintf(stderr, "    why = %d (want %d)\n", (int)why, (int)c->want_why);
            bad = 1;
        }
        break;
    case ELF_CORPUS_WHY_ANY:
        if (rc != 0 && why == DUNEOS_ELF_REJ_NONE) {
            fprintf(stderr, "    rejected with no reason reported\n");
            bad = 1;
        }
        break;
    case ELF_CORPUS_WHY_UNOBSERVED:
        break;
    }
    return bad;
}

static outcome_t run_isolated(const elf_corpus_case_t *c, int *signo)
{
    fflush(NULL);

    pid_t pid = fork();
    if (pid < 0) {
        perror("fork");
        return OUT_ERROR;
    }
    if (pid == 0) {
        _exit(child_run(c) ? 1 : 0);
    }

    int status = 0;
    while (waitpid(pid, &status, 0) < 0) {
        if (errno != EINTR) { perror("waitpid"); return OUT_ERROR; }
    }
    if (WIFSIGNALED(status)) {
        *signo = WTERMSIG(status);
        return OUT_CRASH;
    }
    return WEXITSTATUS(status) == 0 ? OUT_PASS : OUT_FAIL;
}

static int dump_corpus(const char *dir)
{
    if (mkdir(dir, 0755) != 0 && errno != EEXIST) {
        perror(dir);
        return 1;
    }
    for (size_t i = 0; i < elf_corpus_count; i++) {
        elf_corpus_image_t img;
        elf_corpus_build(&elf_corpus[i], &img);

        char path[512];
        snprintf(path, sizeof(path), "%s/%s.elf", dir, elf_corpus[i].name);

        FILE *f = fopen(path, "wb");
        if (!f) { perror(path); return 1; }
        size_t n = fwrite(img.bytes, 1, img.size, f);
        if (fclose(f) != 0 || n != img.size) { perror(path); return 1; }
    }
    printf("dumped %zu corpus images to %s\n", elf_corpus_count, dir);
    return 0;
}

int main(int argc, char **argv)
{
    if (argc == 3 && strcmp(argv[1], "--dump-corpus") == 0) {
        return dump_corpus(argv[2]);
    }
    if (argc != 1) {
        fprintf(stderr, "usage: %s [--dump-corpus DIR]\n", argv[0]);
        return 2;
    }

    test_validate_direct();
    test_extent_arithmetic();
    test_section_total_arithmetic();

    int passed = 0, failed = 0, xfailed = 0, xpassed = 0, unplanned = 0;
    int errored = 0;

    for (size_t i = 0; i < elf_corpus_count; i++) {
        const elf_corpus_case_t *c = &elf_corpus[i];
        int signo = 0;
        outcome_t o = run_isolated(c, &signo);

        if (o == OUT_ERROR) {
            errored++;
            fprintf(stderr,
                    "  ERROR %s — the harness could not run this case (see the "
                    "fork/waitpid message above). Not an observation about the "
                    "case, and not absorbed by its expected-failure marker.\n",
                    c->name);
            continue;
        }

        if (c->xfail_owner == NULL) {
            if (o == OUT_PASS) {
                passed++;
            } else {
                failed++;
                fprintf(stderr, "  FAIL %s — %s%s\n", c->name, c->desc,
                        o == OUT_CRASH ? " (child crashed)" : "");
                if (o == OUT_CRASH) {
                    fprintf(stderr, "    killed by signal %d\n", signo);
                }
            }
            continue;
        }

        if (o == OUT_PASS) {
            xpassed++;
            fprintf(stderr,
                    "  XPASS %s — now rejected as expected. Delete its "
                    "expected-failure marker (%s) in tests/host/elf_corpus.c.\n",
                    c->name, c->xfail_owner);
        } else {
            xfailed++;
            if (strncmp(c->xfail_owner, "UNPLANNED", 9) == 0) unplanned++;
            printf("  XFAIL %s [%s] — %s%s\n", c->name, c->xfail_owner,
                   c->defect, o == OUT_CRASH ? " (child crashed)" : "");
        }
    }

    printf("elf corpus: %zu cases, %d passed, %d expected failures, "
           "%d unexpected passes, %d hard failures, %d not run\n",
           elf_corpus_count, passed, xfailed, xpassed, failed, errored);

    int marked = xfailed + xpassed;
    int pin    = 0;
    /* A case the harness could not run leaves its marker uncounted, so the pin
     * would misfire with a message about deleting markers. Report the real
     * cause instead. */
    if (errored == 0 && marked != ELF_CORPUS_EXPECTED_XFAILS) {
        pin = 1;
        fprintf(stderr,
                "  PIN %d expected-failure marker(s) present, "
                "ELF_CORPUS_EXPECTED_XFAILS says %d. Shrinking that total is "
                "expected when a LEG finding is fixed and its markers are "
                "deleted — update the constant in tests/host/elf_corpus.h in "
                "the same change, exactly like the markers themselves.\n",
                marked, ELF_CORPUS_EXPECTED_XFAILS);
    }
    if (unplanned > 0) {
        printf("elf corpus: %d expected failure(s) marked UNPLANNED — "
               "no spec covers them yet; each names its docs/backlog.md "
               "id\n", unplanned);
    }

    int rc = t_report("test_elf_validate");
    return (rc || failed || xpassed || pin || errored) ? 1 : 0;
}
