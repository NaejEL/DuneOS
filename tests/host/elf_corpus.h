#pragma once

/*
 * Generated malformed-ELF corpus for the DuneOS loader (LEG-26).
 *
 * The corpus is code, not committed binaries: every case is a named mutation
 * applied to one well-formed ET_REL image built in memory, so the defect under
 * test is isolated and the case stays readable and modifiable.
 *
 * Error convention under test (ADR 001): 0 on success, -errno on failure.
 */

#include <stddef.h>
#include <stdint.h>

#include "duneos/elf_parse.h"

/* The reference image is small and fixed-layout; no case grows it. */
#define ELF_CORPUS_IMAGE_MAX 512

typedef struct {
    uint8_t bytes[ELF_CORPUS_IMAGE_MAX];
    size_t  size;                 /* bytes the injected io will serve */
} elf_corpus_image_t;

/*
 * How much the driver may assert about the reject reason of a case.
 *
 * WHY_UNOBSERVED exists because a probe that reaches its defect through a
 * string accessor never touches `why`: duneos_elf_image_open() succeeded and
 * left DUNEOS_ELF_REJ_NONE behind. Demanding a reason there would keep the case
 * failing after the defect is fixed, so the expected-failure marker could never
 * flip to XPASS and the fix would land unreported — the permanent mask this
 * corpus exists to prevent. For those cases the return code is the whole
 * assertion (criterion 3), which is exactly what changes when they are fixed.
 */
typedef enum {
    ELF_CORPUS_WHY_EXACT = 0,  /* why must equal want_why */
    ELF_CORPUS_WHY_ANY,        /* a rejection must name some reason */
    ELF_CORPUS_WHY_UNOBSERVED, /* probe never goes through image_open's verdict */
} elf_corpus_why_mode_t;

/*
 * A case is (mutation, expected return code). why_mode says how much of the
 * reject reason is asserted: WHY_EXACT when the unit already has an enumerator
 * for the check, WHY_ANY when the check does not exist yet so no enumerator can
 * be named, WHY_UNOBSERVED when the probe never reaches image_open's verdict.
 */
typedef struct {
    const char *name;
    const char *desc;

    void (*mutate)(elf_corpus_image_t *img);

    /* Runs the unit against img and returns the observed rc. why is filled
     * when the probe went through duneos_elf_image_open(). */
    int (*probe)(const elf_corpus_image_t *img, duneos_elf_reject_t *why);

    int                   want_rc;
    duneos_elf_reject_t   want_why;
    elf_corpus_why_mode_t why_mode;

    /*
     * Expected-failure marker. NULL means the case must hold on every commit.
     * Otherwise: the finding id whose fix makes the case pass and which MUST
     * delete this marker. LEG-01/02/03 are three findings inside the single
     * approved spec specs/SPEC-leg-01-harden-elf-validation.md, not three
     * specs — its criterion 1 removes all four LEG-* markers in one change.
     * An owner starting with "UNPLANNED" flags a defect no approved spec covers
     * yet; it carries a docs/backlog.md id so the marker has a tracked home, and
     * the driver counts those separately so they cannot rot here.
     */
    const char *xfail_owner;
    const char *defect;
} elf_corpus_case_t;

/*
 * Corpus size pins. A stale expected-failure marker is caught by the XPASS
 * rule, but deleting a case is otherwise invisible: it only changes a printed
 * count and the suite stays green. These two constants make any change to the
 * corpus deliberate — the case total is checked at compile time in
 * elf_corpus.c, the marker total at run time in test_elf_validate.c.
 *
 * ELF_CORPUS_EXPECTED_XFAILS legitimately shrinks when a LEG finding is fixed
 * and its markers are deleted; that change must update this constant, exactly
 * like the markers themselves.
 */
#define ELF_CORPUS_EXPECTED_CASES  17
#define ELF_CORPUS_EXPECTED_XFAILS 6

extern const elf_corpus_case_t elf_corpus[];
extern const size_t            elf_corpus_count;

/* Build the well-formed reference image, then apply the case mutation. */
void elf_corpus_build(const elf_corpus_case_t *c, elf_corpus_image_t *out);

/* duneos_elf_io_t backend over an elf_corpus_image_t. Short reads are -EIO. */
int elf_corpus_read(void *ctx, long offset, void *buf, size_t len);

/* Default probe: duneos_elf_image_open() with the loader's own parameters. */
int elf_corpus_probe_open(const elf_corpus_image_t *img,
                          duneos_elf_reject_t      *why);
