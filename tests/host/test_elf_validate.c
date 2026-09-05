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
 * LEG-01, LEG-02 and LEG-03 are three FINDINGS inside one approved spec,
 * specs/SPEC-leg-01-harden-elf-validation.md — not three specs. Its criterion 1
 * covers all three, so the four LEG-* markers below all go in a single change.
 */

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

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

/* ------------------------------------------------------------ corpus driver */

typedef enum { OUT_PASS, OUT_FAIL, OUT_CRASH } outcome_t;

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
        return OUT_CRASH;
    }
    if (pid == 0) {
        _exit(child_run(c) ? 1 : 0);
    }

    int status = 0;
    while (waitpid(pid, &status, 0) < 0) {
        if (errno != EINTR) { perror("waitpid"); return OUT_CRASH; }
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

    int passed = 0, failed = 0, xfailed = 0, xpassed = 0, unplanned = 0;

    for (size_t i = 0; i < elf_corpus_count; i++) {
        const elf_corpus_case_t *c = &elf_corpus[i];
        int signo = 0;
        outcome_t o = run_isolated(c, &signo);

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
           "%d unexpected passes, %d hard failures\n",
           elf_corpus_count, passed, xfailed, xpassed, failed);

    int marked = xfailed + xpassed;
    int pin    = 0;
    if (marked != ELF_CORPUS_EXPECTED_XFAILS) {
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
               "no approved spec removes them yet; each names its docs/backlog.md "
               "id\n", unplanned);
    }

    int rc = t_report("test_elf_validate");
    return (rc || failed || xpassed || pin) ? 1 : 0;
}
