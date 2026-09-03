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

    return 0;

fail: {
        /* Preserve the header for the caller's diagnostic; drop the buffers. */
        elf32_hdr_t saved = out->hdr;
        duneos_elf_image_close(out);
        out->hdr = saved;
        return rc;
    }
}

void duneos_elf_image_close(duneos_elf_image_t *img)
{
    free(img->shdrs);
    free(img->shstrtab);
    memset(img, 0, sizeof(*img));
}

const char *duneos_elf_string(const char *strtab, uint32_t offset)
{
    return strtab + offset;
}

const char *duneos_elf_section_name(const duneos_elf_image_t *img,
                                    const elf32_shdr_t       *sh)
{
    return duneos_elf_string(img->shstrtab, sh->sh_name);
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
