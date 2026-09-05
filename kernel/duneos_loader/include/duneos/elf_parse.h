#pragma once

/*
 * Pure ELF parsing and validation for the DuneOS loader (LEG-25).
 *
 * This unit is deliberately free of ESP-IDF, FreeRTOS, soc/, cJSON and DuneOS
 * kernel headers so it can be compiled and driven from a host test harness.
 * Its only dependencies are libc and "duneos/elf.h" (plain ELF32 structure
 * definitions over <stdint.h>, no kernel coupling).
 *
 * File bytes are never touched directly: every read goes through an injected
 * duneos_elf_io_t, so a test can back an image with a memory buffer while the
 * kernel backs it with a FILE *.
 *
 * Error convention (ADR 001): 0 on success, -errno on failure. Nothing here
 * logs — the caller receives a duneos_elf_reject_t telling it exactly which
 * check failed so it can emit the diagnostic naming the offending field.
 */

#include <stddef.h>
#include <stdint.h>

#include "duneos/elf.h"

/* Which check rejected the image. Set on every non-zero return of
 * duneos_elf_validate() / duneos_elf_image_open(). */
typedef enum {
    DUNEOS_ELF_REJ_NONE = 0,
    DUNEOS_ELF_REJ_MAGIC,               /* e_ident magic is not "\x7fELF"      */
    DUNEOS_ELF_REJ_CLASS,               /* e_ident[EI_CLASS] != ELFCLASS32     */
    DUNEOS_ELF_REJ_DATA,                /* e_ident[EI_DATA]  != ELFDATA2LSB    */
    DUNEOS_ELF_REJ_TYPE,                /* e_type            != ET_REL         */
    DUNEOS_ELF_REJ_MACHINE,             /* e_machine != expected machine       */
    DUNEOS_ELF_REJ_NO_SECTIONS,         /* e_shoff == 0 or e_shnum == 0        */
    DUNEOS_ELF_REJ_TOO_MANY_SECTIONS,   /* e_shnum > max_sections              */
    DUNEOS_ELF_REJ_HEADER_READ,         /* ELF header could not be read        */
    DUNEOS_ELF_REJ_SHDR_READ,           /* section header table read failed    */
    DUNEOS_ELF_REJ_SHSTRTAB_READ,       /* section name string table read fail */
    DUNEOS_ELF_REJ_NOMEM,               /* allocation failed                   */
} duneos_elf_reject_t;

/*
 * Injected byte source. read() must fill len bytes at offset, and return
 * 0 on success or -errno on failure (short read included).
 */
typedef struct {
    int   (*read)(void *ctx, long offset, void *buf, size_t len);
    void   *ctx;
} duneos_elf_io_t;

/*
 * Parsed image head: the ELF header, the full section header table and the
 * section name string table. Owns shdrs and shstrtab.
 */
typedef struct {
    elf32_hdr_t   hdr;
    elf32_shdr_t *shdrs;      /* hdr.e_shnum entries                        */
    char         *shstrtab;   /* section names, NUL-terminated by open()    */
} duneos_elf_image_t;

/* Section classes the loader places in memory. */
typedef enum {
    SEC_IGNORE,
    SEC_TEXT,       /* .text* and .literal* */
    SEC_DATA,       /* .data*               */
    SEC_RODATA,     /* .rodata*             */
    SEC_BSS,        /* .bss*                */
} sec_kind_t;

/*
 * Validate an ELF header for loading as a DuneOS app.
 * expect_machine is the EM_* value the running kernel accepts, or 0 to skip
 * the machine check. *why receives the failing check (unchanged on success).
 * Returns 0, -EINVAL (malformed) or -ENOTSUP (well-formed but unloadable).
 */
int duneos_elf_validate(const elf32_hdr_t   *hdr,
                        uint16_t             expect_machine,
                        duneos_elf_reject_t *why);

/*
 * Read the ELF header, validate it, enforce max_sections, then read the
 * section header table and the section name string table.
 * *out is zeroed first, so out->hdr is readable by the caller's diagnostic
 * for every reject reason past DUNEOS_ELF_REJ_HEADER_READ.
 * On success the caller owns *out and must release it with
 * duneos_elf_image_close(). On failure *out is already released.
 */
int duneos_elf_image_open(const duneos_elf_io_t *io,
                          uint16_t               expect_machine,
                          uint32_t               max_sections,
                          duneos_elf_image_t    *out,
                          duneos_elf_reject_t   *why);

/* Free the buffers owned by img and reset it. Safe on a zeroed image. */
void duneos_elf_image_close(duneos_elf_image_t *img);

/* String table accessors. */
const char *duneos_elf_string(const char *strtab, uint32_t offset);
const char *duneos_elf_section_name(const duneos_elf_image_t *img,
                                    const elf32_shdr_t       *sh);

/*
 * Classify a section by name and sh_flags.
 *
 * With -ffunction-sections the compiler emits .text.funcname, .literal.funcname
 * etc. rather than a single .text — we match by prefix. Literal pools
 * (.literal.*) are executable data adjacent to code and are loaded like .text
 * (both carry SHF_ALLOC | SHF_EXECINSTR).
 */
sec_kind_t duneos_elf_classify_section(const char *name, uint32_t flags);
