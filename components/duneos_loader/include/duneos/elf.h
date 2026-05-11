#pragma once

#include <stdint.h>

/*
 * Minimal ELF types for ET_REL (relocatable object) files.
 * Only the fields DuneOS actually uses are listed — not an exhaustive ELF spec.
 */

/* ELF magic */
#define ELF_MAGIC       "\x7f" "ELF"
#define ELF_MAGIC_SIZE  4

/* e_type */
#define ET_REL          1       /* relocatable object — the only type we load */

/* e_machine */
#define EM_XTENSA       94
#define EM_RISCV        243

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

/* Xtensa relocation types (RELA-only; DuneOS handles the subset below) */
#define R_XTENSA_NONE           0
#define R_XTENSA_32             1   /* word32: *(u32) += S + A                         */
#define R_XTENSA_ASM_EXPAND     11  /* assembler relaxation hint — no-op in loader      */
#define R_XTENSA_32_PCREL       14  /* 32-bit PC-relative — rare, not currently handled */
#define R_XTENSA_DIFF8          17  /* difference relocs — unwind tables                */
#define R_XTENSA_DIFF16         18
#define R_XTENSA_DIFF32         19
#define R_XTENSA_SLOT0_OP       20  /* patch instruction slot 0 (L32R, CALL, branch)    */
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

/*
 * RISC-V relocation types (RELA; subset used by -ffreestanding -nostdlib apps).
 *
 * PCREL pairs (HI20 + LO12_I/S):
 *   The LO12 relocation's r_sym points to the runtime address of the paired
 *   PCREL_HI20 instruction, not to the original target.  The loader caches each
 *   HI20's (PC → target) so the subsequent LO12 can recover it.
 */
#define R_RISCV_NONE            0   /* no-op                                            */
#define R_RISCV_32              1   /* *(u32) = S + A  (absolute 32-bit)                */
#define R_RISCV_BRANCH          16  /* B-type branch  (±4 KB) — usually intra-function  */
#define R_RISCV_JAL             17  /* J-type jump    (±1 MB) — usually intra-section   */
#define R_RISCV_CALL            18  /* auipc+jalr pair, 32-bit PC-relative call          */
#define R_RISCV_CALL_PLT        19  /* same as CALL (PLT irrelevant in ET_REL)           */
#define R_RISCV_PCREL_HI20      23  /* auipc: upper 20 bits of PC-relative addr          */
#define R_RISCV_PCREL_LO12_I    24  /* I-type lower 12 bits — pairs with PCREL_HI20     */
#define R_RISCV_PCREL_LO12_S    25  /* S-type lower 12 bits — pairs with PCREL_HI20     */
#define R_RISCV_HI20            26  /* lui: upper 20 bits of absolute addr               */
#define R_RISCV_LO12_I          27  /* I-type lower 12 bits of absolute addr             */
#define R_RISCV_LO12_S          28  /* S-type lower 12 bits of absolute addr             */
#define R_RISCV_ADD8            33  /* *(u8)  += S  — in debug/.eh_frame, SHF_ALLOC=0   */
#define R_RISCV_ADD16           34
#define R_RISCV_ADD32           35
#define R_RISCV_SUB8            37  /* *(u8)  -= S                                       */
#define R_RISCV_SUB16           38
#define R_RISCV_SUB32           39
#define R_RISCV_ALIGN           43  /* alignment directive — no-op in loader             */
#define R_RISCV_RVC_BRANCH      44  /* 16-bit compressed branch — usually intra-function */
#define R_RISCV_RVC_JUMP        45  /* 16-bit compressed jump   — usually intra-section  */
#define R_RISCV_RELAX           51  /* linker relaxation hint — no-op in loader          */
#define R_RISCV_SUB6            52
#define R_RISCV_SET6            53
#define R_RISCV_SET8            54
#define R_RISCV_SET16           55
#define R_RISCV_SET32           56
#define R_RISCV_32_PCREL        57  /* 32-bit PC-relative — in debug info, SHF_ALLOC=0  */

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
