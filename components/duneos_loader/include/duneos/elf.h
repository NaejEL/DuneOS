#pragma once

#include <stdint.h>

/*
 * Minimal ELF types for ET_REL (relocatable object) files targeting Xtensa.
 * Only the fields DuneOS actually uses are listed — not an exhaustive ELF spec.
 */

/* ELF magic */
#define ELF_MAGIC       "\x7f" "ELF"
#define ELF_MAGIC_SIZE  4

/* e_type */
#define ET_REL          1       /* relocatable object — the only type we load */

/* e_machine */
#define EM_XTENSA       94

/* e_ident indices */
#define EI_CLASS        4
#define EI_DATA         5
#define ELFCLASS32      1
#define ELFDATA2LSB     1       /* little-endian */

/* Section header sh_type */
#define SHT_NULL        0
#define SHT_PROGBITS    1       /* .text, .data, .rodata */
#define SHT_SYMTAB      2
#define SHT_STRTAB      3
#define SHT_RELA        4       /* relocation with addend (Xtensa uses RELA) */
#define SHT_NOBITS      8       /* .bss — occupies no space in file */
#define SHT_REL         9       /* relocation without addend */

/* Section header sh_flags */
#define SHF_WRITE       (1u << 0)   /* .data */
#define SHF_ALLOC       (1u << 1)   /* section occupies memory at runtime */
#define SHF_EXECINSTR   (1u << 2)   /* .text */

/* Symbol binding (ELF32_ST_BIND) */
#define STB_LOCAL       0
#define STB_GLOBAL      1
#define STB_WEAK        2

/* Symbol type (ELF32_ST_TYPE) */
#define STT_NOTYPE      0
#define STT_OBJECT      1
#define STT_FUNC        2
#define STT_SECTION     3

/* Special section indices */
#define SHN_UNDEF       0       /* undefined — must be resolved from kernel table */
#define SHN_ABS         0xfff1  /* absolute value, not relocated */
#define SHN_COMMON      0xfff2

/* Xtensa relocation types (the ones DuneOS must handle) */
#define R_XTENSA_NONE           0
#define R_XTENSA_32             1   /* word32: S + A */
#define R_XTENSA_SLOT0_OP       20  /* instruction slot 0 — most common call/jump */
#define R_XTENSA_SLOT1_OP       21
#define R_XTENSA_SLOT2_OP       22
#define R_XTENSA_SLOT3_OP       23
#define R_XTENSA_SLOT4_OP       24
#define R_XTENSA_SLOT5_OP       25
#define R_XTENSA_SLOT6_OP       26
#define R_XTENSA_SLOT7_OP       27
#define R_XTENSA_SLOT8_OP       28
#define R_XTENSA_SLOT9_OP       29
#define R_XTENSA_SLOT10_OP      30
#define R_XTENSA_SLOT11_OP      31
#define R_XTENSA_SLOT12_OP      32
#define R_XTENSA_SLOT13_OP      33
#define R_XTENSA_SLOT14_OP      34
#define R_XTENSA_ASM_EXPAND     11
#define R_XTENSA_32_PCREL       14

#pragma pack(push, 1)

typedef struct {
    uint8_t  e_ident[16];
    uint16_t e_type;
    uint16_t e_machine;
    uint32_t e_version;
    uint32_t e_entry;
    uint32_t e_phoff;       /* program header offset (0 for ET_REL) */
    uint32_t e_shoff;       /* section header table offset */
    uint32_t e_flags;
    uint16_t e_ehsize;
    uint16_t e_phentsize;
    uint16_t e_phnum;
    uint16_t e_shentsize;
    uint16_t e_shnum;
    uint16_t e_shstrndx;    /* section index of the section name string table */
} elf32_hdr_t;

typedef struct {
    uint32_t sh_name;       /* index into section name string table */
    uint32_t sh_type;
    uint32_t sh_flags;
    uint32_t sh_addr;       /* virtual address (0 for ET_REL before relocation) */
    uint32_t sh_offset;     /* offset in file */
    uint32_t sh_size;
    uint32_t sh_link;       /* section link (e.g. symtab → strtab index) */
    uint32_t sh_info;
    uint32_t sh_addralign;
    uint32_t sh_entsize;    /* size of each entry if fixed-size table */
} elf32_shdr_t;

typedef struct {
    uint32_t st_name;       /* index into string table */
    uint32_t st_value;
    uint32_t st_size;
    uint8_t  st_info;       /* binding (high 4) + type (low 4) */
    uint8_t  st_other;
    uint16_t st_shndx;      /* section index or SHN_UNDEF / SHN_ABS */
} elf32_sym_t;

typedef struct {
    uint32_t r_offset;      /* byte offset within section of the relocation */
    uint32_t r_info;        /* symbol index (high 24) + relocation type (low 8) */
} elf32_rel_t;

typedef struct {
    uint32_t r_offset;
    uint32_t r_info;
    int32_t  r_addend;
} elf32_rela_t;

#pragma pack(pop)

#define ELF32_R_SYM(info)   ((info) >> 8)
#define ELF32_R_TYPE(info)  ((uint8_t)(info))
#define ELF32_ST_BIND(i)    ((i) >> 4)
#define ELF32_ST_TYPE(i)    ((i) & 0xf)
