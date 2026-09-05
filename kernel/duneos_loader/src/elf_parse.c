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

    /* e_shstrndx was bounded by duneos_elf_validate(). */
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
