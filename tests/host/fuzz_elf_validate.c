/*
 * libFuzzer entry point for the pure ELF validation unit (LEG-26).
 *
 * The fuzzer input IS the file: it is served through the injected
 * duneos_elf_io_t exactly as the loader serves an SD-card file, so the whole
 * header/section-header/string-table path is driven from untrusted bytes.
 *
 * Build and run: make -C tests/host fuzz-run (seeds come from the corpus of
 * tests/host/elf_corpus.c, dumped by test_elf_validate --dump-corpus).
 */

#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "duneos/elf.h"
#include "duneos/elf_parse.h"

/* Mirrors loader.c MAX_SECTIONS (l.103) and LOADER_EXPECT_MACHINE (l.237). */
#define FUZZ_MAX_SECTIONS   1024u
#define FUZZ_EXPECT_MACHINE EM_XTENSA

typedef struct {
    const uint8_t *bytes;
    size_t         size;
} fuzz_buf_t;

static int fuzz_read(void *ctx, long offset, void *buf, size_t len)
{
    const fuzz_buf_t *b = ctx;

    if (offset < 0) return -EIO;
    if ((size_t)offset > b->size) return -EIO;
    if (len > b->size - (size_t)offset) return -EIO;

    memcpy(buf, b->bytes + offset, len);
    return 0;
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size);

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
    fuzz_buf_t b = { .bytes = data, .size = size };
    duneos_elf_io_t io = { .read = fuzz_read, .ctx = &b };

    duneos_elf_image_t img;
    duneos_elf_reject_t why = DUNEOS_ELF_REJ_NONE;

    if (duneos_elf_image_open(&io, FUZZ_EXPECT_MACHINE, FUZZ_MAX_SECTIONS,
                              &img, &why) != 0) {
        return 0;
    }

    /* The string accessors are part of the validation surface: they are where
     * an unbounded sh_name escapes the section name table. */
    for (uint16_t i = 0; i < img.hdr.e_shnum; i++) {
        const char *name = duneos_elf_section_name(&img, &img.shdrs[i]);
        if (name != NULL) {
            (void)duneos_elf_classify_section(name, img.shdrs[i].sh_flags);
        }
    }

    duneos_elf_image_close(&img);
    return 0;
}
