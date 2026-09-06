#include "duneos/elf_parse.h"

#include <errno.h>
#include <stdlib.h>
#include <string.h>

int duneos_elf_validate(const elf32_hdr_t   *hdr,
                        uint16_t             expect_machine,
                        duneos_elf_reject_t *why)
{
    if (memcmp(hdr->e_ident, ELF_MAGIC, ELF_MAGIC_SIZE) != 0) {
        *why = DUNEOS_ELF_REJ_MAGIC;
        return -EINVAL;
    }
    if (hdr->e_ident[EI_CLASS] != ELFCLASS32) {
        *why = DUNEOS_ELF_REJ_CLASS;
        return -ENOTSUP;
    }
    if (hdr->e_ident[EI_DATA] != ELFDATA2LSB) {
        *why = DUNEOS_ELF_REJ_DATA;
        return -ENOTSUP;
    }
    if (hdr->e_type != ET_REL) {
        *why = DUNEOS_ELF_REJ_TYPE;
        return -ENOTSUP;
    }
    /* expect_machine == 0: the caller's architecture imposes no constraint. */
    if (expect_machine != 0 && hdr->e_machine != expect_machine) {
        *why = DUNEOS_ELF_REJ_MACHINE;
        return -ENOTSUP;
    }
    if (hdr->e_shoff == 0 || hdr->e_shnum == 0) {
        *why = DUNEOS_ELF_REJ_NO_SECTIONS;
        return -EINVAL;
    }
    if (hdr->e_shstrndx >= hdr->e_shnum) {
        *why = DUNEOS_ELF_REJ_SHSTRNDX;
        return -EINVAL;
    }
    return 0;
}

/*
 * Section types whose sh_link is a section header index. Every other type
 * leaves sh_link at 0 or gives it an unrelated meaning, so bounding it there
 * would reject well-formed objects for a field nothing indexes with.
 */
static int sh_link_is_section_index(uint32_t sh_type)
{
    return sh_type == SHT_SYMTAB || sh_type == SHT_RELA || sh_type == SHT_REL;
}

/*
 * The type a section's sh_link target must have, or 0 when the type imposes
 * none. Only SHT_SYMTAB is constrained here, and for a concrete reason: it is
 * the one link the loader dereferences into an allocation — loader.c reads
 * shdrs[symtab->sh_link] and mallocs sh_size + 1 for the symbol string table.
 * Bounding the index alone leaves that allocation wide open, because a link to
 * a SHT_NOBITS section is in range yet exempt from the size bound below, so its
 * sh_size is unbounded. SHT_REL/SHT_RELA links are left alone:
 * nothing sizes an allocation from them, and constraining a field no caller
 * indexes is how a validator starts rejecting well-formed objects.
 */
static uint32_t sh_link_required_type(uint32_t sh_type)
{
    return sh_type == SHT_SYMTAB ? SHT_STRTAB : 0u;
}

/*
 * WHY the two-step form, and why `sh_offset + sh_size > size` is wrong here:
 * both fields are uint32 read straight from the file, so their sum wraps —
 * 0xffffff00 + 0x200 is 0x100, which passes any comparison against a small
 * size. That wrap IS the defect being fixed (BL-ELF-EXTENT). Bounding
 * sh_offset first is what makes `size - sh_offset` safe to compute.
 *
 * Runs before any size-dependent allocation, so the shstrtab buffer below is
 * bounded by the image size by construction rather than by malloc failing.
 */
static int check_section_extents(const duneos_elf_image_t *img,
                                 size_t                    image_size,
                                 uint32_t                 *bad_index,
                                 uint32_t                 *bad_value,
                                 uint32_t                 *bad_bound,
                                 duneos_elf_reject_t      *why)
{
    for (uint16_t i = 0; i < img->hdr.e_shnum; i++) {
        const elf32_shdr_t *sh = &img->shdrs[i];

        if (sh->sh_offset > image_size) {
            *why       = DUNEOS_ELF_REJ_SH_OFFSET;
            *bad_index = i;
            *bad_value = sh->sh_offset;
            *bad_bound = (uint32_t)image_size;
            return -EINVAL;
        }

        /*
         * sh_size is a MEMORY size for SHT_NOBITS: it does not occupy file
         * bytes, so the file size does not bound it. A .bss larger than the
         * whole object is well-formed and must load — bounding it here would
         * reject any app with a static buffer bigger than its own binary. What
         * bounds a NOBITS sh_size is the memory that has to hold it, which is
         * the loader's pool, not this unit's image (load_sections() caps it
         * there).
         *
         * SHT_NULL is NOT exempt, although it occupies no file bytes either.
         * Its sh_size is meaningless, which is exactly why nothing is lost by
         * bounding it: a well-formed SHT_NULL section carries sh_size == 0, so
         * the bound never rescinds a legitimate object. Exempting it would only
         * carry an attacker-controlled uint32 through the invariant this file
         * advertises — and load_sections() would act on it, because its
         * zero-fill branch tests SHT_NOBITS alone, so a SHT_NULL section named
         * '.data' is placed AND read from the file at its sh_size.
         *
         * sh_offset above is NOT exempt either: a NOBITS sh_offset is still the
         * file position the section would have occupied, every object this
         * loader accepts keeps it inside the file, and leaving it unbounded
         * serves nothing. The section name table is never exempt whatever it
         * claims to be: open() reads it from the file.
         */
        if (i != img->hdr.e_shstrndx && sh->sh_type == SHT_NOBITS) continue;

        if (sh->sh_size > image_size - sh->sh_offset) {
            *why       = DUNEOS_ELF_REJ_SH_SIZE;
            *bad_index = i;
            *bad_value = sh->sh_size;
            *bad_bound = (uint32_t)(image_size - sh->sh_offset);
            return -EINVAL;
        }
    }
    return 0;
}

static int check_section_table(duneos_elf_image_t *img, duneos_elf_reject_t *why)
{
    for (uint16_t i = 0; i < img->hdr.e_shnum; i++) {
        const elf32_shdr_t *sh = &img->shdrs[i];

        if (sh->sh_name >= img->shstrtab_size) {
            *why = DUNEOS_ELF_REJ_SH_NAME;
            img->reject_index = i;
            img->reject_value = sh->sh_name;
            img->reject_bound = (uint32_t)img->shstrtab_size;
            return -EINVAL;
        }
        if (sh_link_is_section_index(sh->sh_type) &&
            sh->sh_link >= img->hdr.e_shnum) {
            *why = DUNEOS_ELF_REJ_SH_LINK;
            img->reject_index = i;
            img->reject_value = sh->sh_link;
            img->reject_bound = img->hdr.e_shnum;
            return -EINVAL;
        }

        uint32_t want_type = sh_link_required_type(sh->sh_type);
        if (want_type != 0 && img->shdrs[sh->sh_link].sh_type != want_type) {
            *why = DUNEOS_ELF_REJ_SH_LINK_TYPE;
            img->reject_index = i;
            img->reject_value = img->shdrs[sh->sh_link].sh_type;
            img->reject_bound = want_type;
            return -EINVAL;
        }
    }
    return 0;
}

int duneos_elf_image_open(const duneos_elf_io_t *io,
                          uint16_t               expect_machine,
                          uint32_t               max_sections,
                          duneos_elf_image_t    *out,
                          duneos_elf_reject_t   *why)
{
    memset(out, 0, sizeof(*out));
    *why = DUNEOS_ELF_REJ_NONE;

    int rc;

    if (io->read(io->ctx, 0, &out->hdr, sizeof(out->hdr)) != 0) {
        *why = DUNEOS_ELF_REJ_HEADER_READ;
        return -EIO;
    }

    rc = duneos_elf_validate(&out->hdr, expect_machine, why);
    if (rc != 0) return rc;

    if (out->hdr.e_shnum > max_sections) {
        *why = DUNEOS_ELF_REJ_TOO_MANY_SECTIONS;
        return -ENOTSUP;
    }

    size_t shdrs_size = (size_t)out->hdr.e_shnum * sizeof(elf32_shdr_t);
    out->shdrs = malloc(shdrs_size);
    if (!out->shdrs) {
        *why = DUNEOS_ELF_REJ_NOMEM;
        return -ENOMEM;
    }
    if (io->read(io->ctx, (long)out->hdr.e_shoff, out->shdrs, shdrs_size) != 0) {
        *why = DUNEOS_ELF_REJ_SHDR_READ;
        rc = -EIO;
        goto fail;
    }

    rc = check_section_extents(out, io->size, &out->reject_index,
                               &out->reject_value, &out->reject_bound, why);
    if (rc != 0) goto fail;

    /* e_shstrndx was bounded by duneos_elf_validate(); its extent by the check
     * above, so this allocation cannot exceed the image size. */
    const elf32_shdr_t *ss = &out->shdrs[out->hdr.e_shstrndx];
    out->shstrtab = malloc((size_t)ss->sh_size + 1u);
    if (!out->shstrtab) {
        *why = DUNEOS_ELF_REJ_NOMEM;
        rc = -ENOMEM;
        goto fail;
    }
    if (io->read(io->ctx, (long)ss->sh_offset, out->shstrtab, ss->sh_size) != 0) {
        *why = DUNEOS_ELF_REJ_SHSTRTAB_READ;
        rc = -EIO;
        goto fail;
    }
    out->shstrtab[ss->sh_size] = '\0';
    out->shstrtab_size = ss->sh_size;

    rc = check_section_table(out, why);
    if (rc != 0) goto fail;

    return 0;

fail: {
        /* Preserve the diagnostic fields for the caller; drop the buffers. */
        elf32_hdr_t saved_hdr   = out->hdr;
        uint32_t    saved_index = out->reject_index;
        uint32_t    saved_value = out->reject_value;
        uint32_t    saved_bound = out->reject_bound;
        duneos_elf_image_close(out);
        out->hdr          = saved_hdr;
        out->reject_index = saved_index;
        out->reject_value = saved_value;
        out->reject_bound = saved_bound;
        return rc;
    }
}

void duneos_elf_image_close(duneos_elf_image_t *img)
{
    free(img->shdrs);
    free(img->shstrtab);
    memset(img, 0, sizeof(*img));
}

const char *duneos_elf_string(const char *strtab, size_t strtab_size,
                              uint32_t offset)
{
    if (!strtab || offset >= strtab_size) return NULL;
    return strtab + offset;
}

const char *duneos_elf_section_name(const duneos_elf_image_t *img,
                                    const elf32_shdr_t       *sh)
{
    return duneos_elf_string(img->shstrtab, img->shstrtab_size, sh->sh_name);
}

sec_kind_t duneos_elf_classify_section(const char *name, uint32_t flags)
{
    if (!(flags & SHF_ALLOC)) return SEC_IGNORE;

    if (strncmp(name, ".text",    5) == 0) return SEC_TEXT;
    if (strncmp(name, ".literal", 8) == 0) return SEC_TEXT;
    if (strncmp(name, ".rodata",  7) == 0) return SEC_RODATA;
    if (strncmp(name, ".data",    5) == 0) return SEC_DATA;
    if (strncmp(name, ".bss",     4) == 0) return SEC_BSS;

    /* Xtensa-specific tool sections — not needed at runtime */
    if (strncmp(name, ".xt.",    4) == 0) return SEC_IGNORE;
    if (strncmp(name, ".xtensa", 7) == 0) return SEC_IGNORE;

    return SEC_IGNORE;
}
