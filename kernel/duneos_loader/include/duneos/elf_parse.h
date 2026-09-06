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
    DUNEOS_ELF_REJ_SHSTRNDX,            /* e_shstrndx >= e_shnum               */
    DUNEOS_ELF_REJ_SH_LINK,             /* sh_link   >= e_shnum                */
    DUNEOS_ELF_REJ_SH_NAME,             /* sh_name   >= shstrtab size          */
    DUNEOS_ELF_REJ_SH_OFFSET,           /* sh_offset > image size              */
    DUNEOS_ELF_REJ_SH_SIZE,             /* sh_size   > size - sh_offset        */
    DUNEOS_ELF_REJ_SH_LINK_TYPE,        /* sh_link names a section of the wrong
                                         * type (SHT_SYMTAB -> SHT_STRTAB)    */
} duneos_elf_reject_t;

/*
 * Injected byte source. read() must fill len bytes at offset, and return
 * 0 on success or -errno on failure (short read included).
 *
 * size is the number of bytes the image holds. It is not a hint the reader may
 * disagree with: it is what bounds every section extent, so a caller that
 * cannot know the size of its backing store cannot use this unit. The kernel
 * takes it from the file, the host tests from their buffer.
 */
typedef struct {
    int   (*read)(void *ctx, long offset, void *buf, size_t len);
    void   *ctx;
    size_t  size;
} duneos_elf_io_t;

/*
 * Parsed image head: the ELF header, the full section header table and the
 * section name string table. Owns shdrs and shstrtab.
 */
typedef struct {
    elf32_hdr_t   hdr;
    elf32_shdr_t *shdrs;          /* hdr.e_shnum entries                    */
    char         *shstrtab;       /* section names, NUL-terminated by open()*/
    size_t        shstrtab_size;  /* bytes of shstrtab, excluding that NUL  */

    /*
     * Which value tripped the check named by *why, so the caller can log the
     * offending field with its value and the bound it broke. Only meaningful
     * for the reject reasons that carry one; survives the failure path of
     * duneos_elf_image_open() alongside hdr.
     */
    uint32_t      reject_index;   /* section header index, when applicable   */
    uint32_t      reject_value;   /* the out-of-range value read from file   */
    uint32_t      reject_bound;   /* the limit it had to stay under          */
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
 *
 * The section header table's name and link indices are bounded here, so a
 * successfully opened image guarantees, for all its sections:
 *   sh_name < shstrtab_size — duneos_elf_section_name() never returns NULL;
 *   sh_link < e_shnum for the section types whose sh_link is a section index,
 *   and an SHT_SYMTAB's sh_link names an SHT_STRTAB — so a caller sizing an
 *   allocation from shdrs[symtab->sh_link].sh_size gets a section whose size
 *   IS bounded by the checks below;
 *   sh_offset <= io->size for EVERY section;
 *   sh_size <= io->size - sh_offset for every section that occupies file
 *   bytes, so its extent never escapes the image and never wraps.
 *   SHT_NOBITS and SHT_NULL are exempt from THAT bound only: their sh_size is
 *   a memory size the file cannot bound (a .bss larger than the object is
 *   well-formed), so a caller placing one in memory must bound it against the
 *   memory it has — the loader does that in load_sections().
 * A caller indexing shdrs with a value it read itself still bounds it itself.
 */
int duneos_elf_image_open(const duneos_elf_io_t *io,
                          uint16_t               expect_machine,
                          uint32_t               max_sections,
                          duneos_elf_image_t    *out,
                          duneos_elf_reject_t   *why);

/* Free the buffers owned by img and reset it. Safe on a zeroed image. */
void duneos_elf_image_close(duneos_elf_image_t *img);

/*
 * String table accessors. Both return NULL when the offset is not strictly
 * inside the table, so an offset read from the file can never produce a start
 * pointer past its end. Callers must handle NULL — the string is untrusted
 * input, not an internal invariant.
 *
 * CALLER CONTRACT: only the START offset is bounded here. Nothing stops a
 * string from running to the end of the table, so strtab must hold strtab_size
 * usable bytes FOLLOWED BY a NUL terminator the caller wrote itself — i.e. an
 * allocation of strtab_size + 1 bytes whose last byte is '\0', not counted in
 * strtab_size. Without it, the strcmp()/strlen() the caller runs on the result
 * reads past the buffer. The two producers in tree do exactly that:
 *   - duneos_elf_image_open() for shstrtab, elf_parse.c ("shstrtab[sh_size]");
 *   - the symbol string table read in loader.c ("strtab[str_sh->sh_size]").
 * A caller that only NULL-checks the result may pass an unterminated buffer.
 */
const char *duneos_elf_string(const char *strtab, size_t strtab_size,
                              uint32_t offset);
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
