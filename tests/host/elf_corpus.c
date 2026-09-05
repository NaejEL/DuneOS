#include "elf_corpus.h"

#include <errno.h>
#include <string.h>

#include "duneos/elf.h"

/*
 * Mirrors loader.c: MAX_SECTIONS (l.103) and LOADER_EXPECT_MACHINE (l.237) on
 * an Xtensa target. The corpus must be driven with the parameters the kernel
 * actually passes, otherwise a bound is exercised at the wrong value.
 */
#define CORPUS_MAX_SECTIONS   1024u
#define CORPUS_EXPECT_MACHINE EM_XTENSA

_Static_assert(sizeof(elf32_hdr_t) == 52, "elf32_hdr_t must be packed");
_Static_assert(sizeof(elf32_shdr_t) == 40, "elf32_shdr_t must be packed");
_Static_assert(sizeof(elf32_sym_t) == 16, "elf32_sym_t must be packed");

/*
 * Reference image layout. Section indices:
 *   0 SHN_UNDEF   1 .text   2 .symtab   3 .strtab   4 .shstrtab
 */
enum {
    OFF_EHDR     = 0,
    OFF_TEXT     = 52,   SZ_TEXT     = 16,
    OFF_STRTAB   = 68,   SZ_STRTAB   = 10,   /* "\0app_main\0" */
    OFF_SYMTAB   = 80,   SZ_SYMTAB   = 32,   /* 2 * elf32_sym_t */
    OFF_SHSTRTAB = 112,  SZ_SHSTRTAB = 33,
    OFF_SHDRS    = 148,
    N_SECTIONS   = 5,
    IMAGE_SIZE   = OFF_SHDRS + N_SECTIONS * 40,

    SEC_NULL = 0, SEC_DOT_TEXT = 1, SEC_SYMTAB = 2, SEC_STRTAB = 3,
    SEC_SHSTRTAB = 4,
};

/* Offsets inside the .shstrtab blob below. */
enum { NAME_TEXT = 1, NAME_SYMTAB = 7, NAME_STRTAB = 15, NAME_SHSTRTAB = 23 };

static const char k_shstrtab[SZ_SHSTRTAB] =
    "\0" ".text\0" ".symtab\0" ".strtab\0" ".shstrtab";

static const char k_strtab[SZ_STRTAB] = "\0" "app_main";

static elf32_hdr_t *hdr_of(elf_corpus_image_t *img)
{
    return (elf32_hdr_t *)(void *)(img->bytes + OFF_EHDR);
}

static elf32_shdr_t *shdr_of(elf_corpus_image_t *img, int i)
{
    return (elf32_shdr_t *)(void *)(img->bytes + OFF_SHDRS) + i;
}

static void build_valid(elf_corpus_image_t *img)
{
    memset(img, 0, sizeof(*img));
    img->size = IMAGE_SIZE;

    elf32_hdr_t *h = hdr_of(img);
    memcpy(h->e_ident, ELF_MAGIC, ELF_MAGIC_SIZE);
    h->e_ident[EI_CLASS] = ELFCLASS32;
    h->e_ident[EI_DATA]  = ELFDATA2LSB;
    h->e_type      = ET_REL;
    h->e_machine   = CORPUS_EXPECT_MACHINE;
    h->e_version   = 1;
    h->e_shoff     = OFF_SHDRS;
    h->e_ehsize    = sizeof(elf32_hdr_t);
    h->e_shentsize = sizeof(elf32_shdr_t);
    h->e_shnum     = N_SECTIONS;
    h->e_shstrndx  = SEC_SHSTRTAB;

    memcpy(img->bytes + OFF_SHSTRTAB, k_shstrtab, SZ_SHSTRTAB);
    memcpy(img->bytes + OFF_STRTAB,   k_strtab,   SZ_STRTAB);

    elf32_sym_t *syms = (elf32_sym_t *)(void *)(img->bytes + OFF_SYMTAB);
    syms[1].st_name  = 1;                             /* "app_main" */
    syms[1].st_value = 0;
    syms[1].st_info  = (uint8_t)((STB_GLOBAL << 4) | STT_FUNC);
    syms[1].st_shndx = SEC_DOT_TEXT;

    elf32_shdr_t *s;

    s = shdr_of(img, SEC_DOT_TEXT);
    s->sh_name      = NAME_TEXT;
    s->sh_type      = SHT_PROGBITS;
    s->sh_flags     = SHF_ALLOC | SHF_EXECINSTR;
    s->sh_offset    = OFF_TEXT;
    s->sh_size      = SZ_TEXT;
    s->sh_addralign = 4;

    s = shdr_of(img, SEC_SYMTAB);
    s->sh_name      = NAME_SYMTAB;
    s->sh_type      = SHT_SYMTAB;
    s->sh_offset    = OFF_SYMTAB;
    s->sh_size      = SZ_SYMTAB;
    s->sh_link      = SEC_STRTAB;
    s->sh_info      = 1;
    s->sh_addralign = 4;
    s->sh_entsize   = sizeof(elf32_sym_t);

    s = shdr_of(img, SEC_STRTAB);
    s->sh_name      = NAME_STRTAB;
    s->sh_type      = SHT_STRTAB;
    s->sh_offset    = OFF_STRTAB;
    s->sh_size      = SZ_STRTAB;
    s->sh_addralign = 1;

    s = shdr_of(img, SEC_SHSTRTAB);
    s->sh_name      = NAME_SHSTRTAB;
    s->sh_type      = SHT_STRTAB;
    s->sh_offset    = OFF_SHSTRTAB;
    s->sh_size      = SZ_SHSTRTAB;
    s->sh_addralign = 1;
}

int elf_corpus_read(void *ctx, long offset, void *buf, size_t len)
{
    const elf_corpus_image_t *img = ctx;

    if (offset < 0) return -EIO;
    if ((size_t)offset > img->size) return -EIO;
    if (len > img->size - (size_t)offset) return -EIO;

    memcpy(buf, img->bytes + offset, len);
    return 0;
}

int elf_corpus_probe_open(const elf_corpus_image_t *img,
                          duneos_elf_reject_t      *why)
{
    duneos_elf_io_t io = { .read = elf_corpus_read, .ctx = (void *)img };
    duneos_elf_image_t parsed;

    int rc = duneos_elf_image_open(&io, CORPUS_EXPECT_MACHINE,
                                   CORPUS_MAX_SECTIONS, &parsed, why);
    if (rc == 0) duneos_elf_image_close(&parsed);
    return rc;
}

/* ---------------------------------------------------------------- mutations */

static void mut_none(elf_corpus_image_t *img) { (void)img; }

static void mut_bad_magic(elf_corpus_image_t *img)
{
    hdr_of(img)->e_ident[1] = 'X';
}

static void mut_bad_class(elf_corpus_image_t *img)
{
    hdr_of(img)->e_ident[EI_CLASS] = 2;   /* ELFCLASS64 */
}

static void mut_bad_data(elf_corpus_image_t *img)
{
    hdr_of(img)->e_ident[EI_DATA] = 2;    /* ELFDATA2MSB */
}

static void mut_not_et_rel(elf_corpus_image_t *img)
{
    hdr_of(img)->e_type = 2;              /* ET_EXEC */
}

static void mut_wrong_machine(elf_corpus_image_t *img)
{
    hdr_of(img)->e_machine = EM_RISCV;
}

static void mut_shoff_zero(elf_corpus_image_t *img)
{
    hdr_of(img)->e_shoff = 0;
}

static void mut_shnum_zero(elf_corpus_image_t *img)
{
    hdr_of(img)->e_shnum = 0;
}

static void mut_shnum_over_max(elf_corpus_image_t *img)
{
    hdr_of(img)->e_shnum = CORPUS_MAX_SECTIONS + 1u;
}

static void mut_shoff_past_eof(elf_corpus_image_t *img)
{
    hdr_of(img)->e_shoff = 0x7fff0000u;
}

static void mut_shdr_table_truncated(elf_corpus_image_t *img)
{
    /* File ends in the middle of the third section header. */
    img->size = OFF_SHDRS + 2 * sizeof(elf32_shdr_t) + 12;
}

static void mut_shstrndx_ge_shnum(elf_corpus_image_t *img)
{
    hdr_of(img)->e_shstrndx = N_SECTIONS + 7;
}

static void mut_sh_link_ge_shnum(elf_corpus_image_t *img)
{
    shdr_of(img, SEC_SYMTAB)->sh_link = N_SECTIONS + 3;
}

static void mut_sh_name_past_shstrtab(elf_corpus_image_t *img)
{
    shdr_of(img, SEC_DOT_TEXT)->sh_name = SZ_SHSTRTAB + 4096;
}

static void mut_st_name_past_strtab(elf_corpus_image_t *img)
{
    elf32_sym_t *syms = (elf32_sym_t *)(void *)(img->bytes + OFF_SYMTAB);
    syms[1].st_name = SZ_STRTAB + 4096;
}

static void mut_sh_offset_size_overflow(elf_corpus_image_t *img)
{
    elf32_shdr_t *s = shdr_of(img, SEC_DOT_TEXT);
    s->sh_offset = 0xffffff00u;
    s->sh_size   = 0x00000200u;   /* sh_offset + sh_size wraps past UINT32_MAX */
}

static void mut_symtab_size_zero(elf_corpus_image_t *img)
{
    shdr_of(img, SEC_SYMTAB)->sh_size = 0;
}

/* ------------------------------------------------------------- extra probes */

/*
 * LEG-03 surface. duneos_elf_section_name() returns shstrtab + sh_name with no
 * bound on sh_name; a hardened accessor reports the out-of-range offset instead
 * of handing back a pointer past the table, so the probe wants a rejection.
 *
 * duneos_elf_image_open() succeeds here, so `why` stays DUNEOS_ELF_REJ_NONE and
 * says nothing about the accessor. Hence ELF_CORPUS_WHY_UNOBSERVED on both
 * LEG-03 cases: the return code is the whole assertion.
 */
static int probe_section_name(const elf_corpus_image_t *img,
                              duneos_elf_reject_t      *why)
{
    duneos_elf_io_t io = { .read = elf_corpus_read, .ctx = (void *)img };
    duneos_elf_image_t parsed;

    int rc = duneos_elf_image_open(&io, CORPUS_EXPECT_MACHINE,
                                   CORPUS_MAX_SECTIONS, &parsed, why);
    if (rc != 0) return rc;

    const char *name = duneos_elf_section_name(&parsed, &parsed.shdrs[SEC_DOT_TEXT]);
    rc = (name == NULL) ? -EINVAL : 0;

    duneos_elf_image_close(&parsed);
    return rc;
}

/*
 * LEG-03 surface, symbol side. The symbol string table is read by loader.c, not
 * by the image; the probe reproduces that read from the raw bytes and then goes
 * through the same accessor the loader uses on sym->st_name. Nothing in this
 * path can ever set a reject reason — see probe_section_name above.
 */
static int probe_symbol_name(const elf_corpus_image_t *img,
                             duneos_elf_reject_t      *why)
{
    duneos_elf_io_t io = { .read = elf_corpus_read, .ctx = (void *)img };
    duneos_elf_image_t parsed;

    int rc = duneos_elf_image_open(&io, CORPUS_EXPECT_MACHINE,
                                   CORPUS_MAX_SECTIONS, &parsed, why);
    if (rc != 0) return rc;

    const elf32_sym_t *sym =
        (const elf32_sym_t *)(const void *)(img->bytes + OFF_SYMTAB) + 1;
    const char *strtab = (const char *)img->bytes + OFF_STRTAB;

    const char *name = duneos_elf_string(strtab, sym->st_name);
    rc = (name == NULL) ? -EINVAL : 0;

    duneos_elf_image_close(&parsed);
    return rc;
}

/* ---------------------------------------------------------------- the corpus */

const elf_corpus_case_t elf_corpus[] = {
    { "valid", "well-formed ET_REL Xtensa object",
      mut_none, elf_corpus_probe_open,
      0, DUNEOS_ELF_REJ_NONE, ELF_CORPUS_WHY_EXACT, NULL, NULL },

    { "bad_magic", "e_ident does not start with \\x7fELF",
      mut_bad_magic, elf_corpus_probe_open,
      -EINVAL, DUNEOS_ELF_REJ_MAGIC, ELF_CORPUS_WHY_EXACT, NULL, NULL },

    { "bad_class", "e_ident[EI_CLASS] is ELFCLASS64",
      mut_bad_class, elf_corpus_probe_open,
      -ENOTSUP, DUNEOS_ELF_REJ_CLASS, ELF_CORPUS_WHY_EXACT, NULL, NULL },

    { "bad_data", "e_ident[EI_DATA] is big-endian",
      mut_bad_data, elf_corpus_probe_open,
      -ENOTSUP, DUNEOS_ELF_REJ_DATA, ELF_CORPUS_WHY_EXACT, NULL, NULL },

    { "not_et_rel", "e_type is ET_EXEC, not ET_REL",
      mut_not_et_rel, elf_corpus_probe_open,
      -ENOTSUP, DUNEOS_ELF_REJ_TYPE, ELF_CORPUS_WHY_EXACT, NULL, NULL },

    { "wrong_machine", "e_machine is EM_RISCV on an Xtensa kernel",
      mut_wrong_machine, elf_corpus_probe_open,
      -ENOTSUP, DUNEOS_ELF_REJ_MACHINE, ELF_CORPUS_WHY_EXACT, NULL, NULL },

    { "shoff_zero", "e_shoff is 0: no section header table",
      mut_shoff_zero, elf_corpus_probe_open,
      -EINVAL, DUNEOS_ELF_REJ_NO_SECTIONS, ELF_CORPUS_WHY_EXACT, NULL, NULL },

    { "shnum_zero", "e_shnum is 0: no sections",
      mut_shnum_zero, elf_corpus_probe_open,
      -EINVAL, DUNEOS_ELF_REJ_NO_SECTIONS, ELF_CORPUS_WHY_EXACT, NULL, NULL },

    { "shnum_over_max", "e_shnum exceeds MAX_SECTIONS",
      mut_shnum_over_max, elf_corpus_probe_open,
      -ENOTSUP, DUNEOS_ELF_REJ_TOO_MANY_SECTIONS, ELF_CORPUS_WHY_EXACT, NULL, NULL },

    { "shoff_past_eof", "e_shoff points past the end of the file",
      mut_shoff_past_eof, elf_corpus_probe_open,
      -EIO, DUNEOS_ELF_REJ_SHDR_READ, ELF_CORPUS_WHY_EXACT, NULL, NULL },

    { "shdr_table_truncated", "file ends inside the section header table",
      mut_shdr_table_truncated, elf_corpus_probe_open,
      -EIO, DUNEOS_ELF_REJ_SHDR_READ, ELF_CORPUS_WHY_EXACT, NULL, NULL },

    { "shstrndx_ge_shnum", "e_shstrndx indexes past the section header table",
      mut_shstrndx_ge_shnum, elf_corpus_probe_open,
      -EINVAL, DUNEOS_ELF_REJ_NONE, ELF_CORPUS_WHY_ANY, "LEG-01",
      "elf_parse.c:76 reads ss->sh_size through shdrs[e_shstrndx] (address formed at\n       l.75) with no e_shstrndx < e_shnum check" },

    { "sh_link_ge_shnum", "symtab sh_link indexes past the section header table",
      mut_sh_link_ge_shnum, elf_corpus_probe_open,
      -EINVAL, DUNEOS_ELF_REJ_NONE, ELF_CORPUS_WHY_ANY, "LEG-02",
      "loader.c:1165 indexes shdrs[shdrs[i].sh_link] unbounded; no unit-level check exists" },

    { "sh_name_past_shstrtab", "sh_name offset lies past the section name table",
      mut_sh_name_past_shstrtab, probe_section_name,
      -EINVAL, DUNEOS_ELF_REJ_NONE, ELF_CORPUS_WHY_UNOBSERVED, "LEG-03",
      "elf_parse.c:109 duneos_elf_string() returns strtab + offset with no size bound" },

    { "st_name_past_strtab", "st_name offset lies past the symbol string table",
      mut_st_name_past_strtab, probe_symbol_name,
      -EINVAL, DUNEOS_ELF_REJ_NONE, ELF_CORPUS_WHY_UNOBSERVED, "LEG-03",
      "elf_parse.c:109 duneos_elf_string() returns strtab + offset with no size bound" },

    { "sh_offset_size_overflow", "sh_offset + sh_size wraps and escapes the file",
      mut_sh_offset_size_overflow, elf_corpus_probe_open,
      -EINVAL, DUNEOS_ELF_REJ_NONE, ELF_CORPUS_WHY_ANY, "UNPLANNED:LEG-34/BL-ELF-EXTENT",
      "no spec bounds sh_offset + sh_size against the file size; loader.c:448 reads at\n       sh_offset. Converting this case needs an API change: duneos_elf_io_t carries no\n       file size, so duneos_elf_image_open() cannot check a section extent. See\n       docs/backlog.md BL-ELF-EXTENT" },

    { "symtab_size_zero", "symtab sh_size is 0 on a table required to be non-empty",
      mut_symtab_size_zero, elf_corpus_probe_open,
      -EINVAL, DUNEOS_ELF_REJ_NONE, ELF_CORPUS_WHY_ANY, "UNPLANNED:LEG-35/BL-ELF-EMPTY-SYMTAB",
      "no spec rejects an empty symtab; loader.c:1156 derives symcount 0 and malloc(0),\n       and the image is refused only later by the app_main-not-found check at loader.c:1293.\n       duneos_elf_image_open() never inspects a non-shstrtab section, so converting this\n       case means either a new check in the unit or a probe reproducing the loader read.\n       See docs/backlog.md BL-ELF-EMPTY-SYMTAB" },
};

const size_t elf_corpus_count = sizeof(elf_corpus) / sizeof(elf_corpus[0]);

_Static_assert(sizeof(elf_corpus) / sizeof(elf_corpus[0]) == ELF_CORPUS_EXPECTED_CASES,
               "corpus size changed: adding or deleting a case must update "
               "ELF_CORPUS_EXPECTED_CASES in elf_corpus.h in the same change");

void elf_corpus_build(const elf_corpus_case_t *c, elf_corpus_image_t *out)
{
    build_valid(out);
    c->mutate(out);
}
