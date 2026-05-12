#include "duneos/loader.h"
#include "duneos/elf.h"
#include "duneos/supervisor.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>
#include <dirent.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <errno.h>

#include "duneos/klog.h"
#include "esp_heap_caps.h"

#ifdef CONFIG_IDF_TARGET_ARCH_XTENSA
#include "soc/soc.h"
#endif

static const char *TAG = "duneos/loader";

/* -------------------------------------------------------------------------
 * Internal app descriptor
 * ---------------------------------------------------------------------- */

#define MAX_SECTIONS 64

struct duneos_app {
    duneos_app_manifest_t manifest;

    /* Runtime base address of each section, indexed by section header index.
     * NULL for sections not loaded into memory (no SHF_ALLOC, size 0, etc.) */
    void *section_bases[MAX_SECTIONS];
    int   section_count;

    /* Heap allocations (data/rodata/bss) freed on unload.
     * Exec sections on Xtensa come from the static pool — not tracked here. */
    void *allocs[MAX_SECTIONS];
    int   alloc_count;

#ifdef CONFIG_IDF_TARGET_ARCH_XTENSA
    size_t exec_pool_mark;  /* s_exec_pool_used before this app loaded */
    size_t exec_pool_end;   /* s_exec_pool_used after section loading */
#endif

    void (*entry)(void);
};

/* -------------------------------------------------------------------------
 * Helpers
 * ---------------------------------------------------------------------- */

#ifdef CONFIG_IDF_TARGET_ARCH_XTENSA

/* On Xtensa, the same physical SRAM is dual-mapped: D-bus (DRAM, r/w) and
 * I-bus (IRAM, exec-only).  D-bus STORE instructions cannot target IRAM
 * addresses — they trigger a cache error.
 *
 * Strategy: pool lives in DRAM BSS (writable via D-bus, predictable address).
 * section_alloc() returns the IRAM alias (pool_dram + SOC_I_D_OFFSET) so the
 * I-bus can fetch instructions from it.  to_write_ptr() converts that alias
 * back to the DRAM address for fread/relocation writes.
 *
 * This requires CONFIG_ESP_SYSTEM_MEMPROT_FEATURE=n (set in the board's
 * sdkconfig.defaults).  With MEMPROT enabled, the I-bus/D-bus split locks the
 * DIRAM region so that the IRAM alias of free DRAM is non-executable and the
 * DRAM alias of static IRAM is read-only — making dynamic code loading
 * impossible regardless of where the pool is placed. */

#ifndef CONFIG_DUNEOS_EXEC_POOL_KB
#define CONFIG_DUNEOS_EXEC_POOL_KB 64
#endif

/* Plain DRAM BSS — written at this address, executed via IRAM alias. */
static uint8_t s_exec_pool[CONFIG_DUNEOS_EXEC_POOL_KB * 1024u] __attribute__((aligned(4)));
static size_t  s_exec_pool_used;

/* Given an IRAM exec address (pool DRAM + SOC_I_D_OFFSET), return the DRAM write ptr. */
static inline void *to_write_ptr(const void *iram_addr)
{
    uintptr_t a      = (uintptr_t)iram_addr;
    uintptr_t pstart = (uintptr_t)s_exec_pool + (uintptr_t)SOC_I_D_OFFSET;
    if (a >= pstart && a < pstart + sizeof(s_exec_pool))
        return (void *)(a - (uintptr_t)SOC_I_D_OFFSET);
    return (void *)iram_addr;
}

#else /* RISC-V or other: no IRAM/DRAM split, all memory is writable */

static inline void *to_write_ptr(const void *addr) { return (void *)addr; }

#endif /* CONFIG_IDF_TARGET_ARCH_XTENSA */

static void *section_alloc(duneos_app_t *app, size_t size, bool exec)
{
#ifdef CONFIG_IDF_TARGET_ARCH_XTENSA
    if (exec) {
        /* Round up to 4-byte alignment (Xtensa instruction alignment). */
        size_t aligned = (size + 3u) & ~3u;
        if (s_exec_pool_used + aligned > sizeof(s_exec_pool)) {
            klog_e(TAG, "exec pool exhausted (%zu + %zu > %u KB)",
                   s_exec_pool_used, aligned, CONFIG_DUNEOS_EXEC_POOL_KB);
            return NULL;
        }
        /* Pool is DRAM; return IRAM alias so the I-bus can execute from it. */
        uint8_t *dram_ptr = s_exec_pool + s_exec_pool_used;
        s_exec_pool_used += aligned;
        return (void *)((uintptr_t)dram_ptr + (uintptr_t)SOC_I_D_OFFSET);
    }
#endif

    /* Data / rodata / bss: heap allocation. */
#ifdef CONFIG_SPIRAM
    void *ptr = heap_caps_malloc(size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!ptr)
        ptr = heap_caps_malloc(size, MALLOC_CAP_8BIT | MALLOC_CAP_INTERNAL);
#else
    void *ptr = heap_caps_malloc(size, MALLOC_CAP_8BIT | MALLOC_CAP_INTERNAL);
#endif
    if (!ptr) return NULL;
    if (app->alloc_count < MAX_SECTIONS)
        app->allocs[app->alloc_count++] = ptr;
    return ptr;
}

static esp_err_t read_at(FILE *f, long offset, void *buf, size_t len)
{
    if (fseek(f, offset, SEEK_SET) != 0) return ESP_ERR_INVALID_ARG;
    if (fread(buf, 1, len, f) != len)    return ESP_ERR_INVALID_SIZE;
    return ESP_OK;
}

static const char *shdr_name(const char *shstrtab, const elf32_shdr_t *sh)
{
    return shstrtab + sh->sh_name;
}

/* -------------------------------------------------------------------------
 * ELF validation
 * ---------------------------------------------------------------------- */

static esp_err_t elf_validate(const elf32_hdr_t *hdr)
{
    if (memcmp(hdr->e_ident, ELF_MAGIC, ELF_MAGIC_SIZE) != 0) {
        klog_e(TAG, "not an ELF file — ident: %02x %02x %02x %02x",
                 hdr->e_ident[0], hdr->e_ident[1],
                 hdr->e_ident[2], hdr->e_ident[3]);
        return ESP_ERR_INVALID_ARG;
    }
    if (hdr->e_ident[EI_CLASS] != ELFCLASS32) {
        klog_e(TAG, "not a 32-bit ELF (EI_CLASS=0x%02x)", hdr->e_ident[EI_CLASS]);
        return ESP_ERR_NOT_SUPPORTED;
    }
    if (hdr->e_ident[EI_DATA] != ELFDATA2LSB) {
        klog_e(TAG, "not little-endian ELF (EI_DATA=0x%02x, expected 0x01)",
                 hdr->e_ident[EI_DATA]);
        return ESP_ERR_NOT_SUPPORTED;
    }
    if (hdr->e_type != ET_REL) {
        klog_e(TAG, "e_type=%u — only ET_REL (1) supported", hdr->e_type);
        return ESP_ERR_NOT_SUPPORTED;
    }
#ifdef CONFIG_IDF_TARGET_ARCH_XTENSA
    if (hdr->e_machine != EM_XTENSA) {
        klog_e(TAG, "e_machine=%u — expected EM_XTENSA (94)", hdr->e_machine);
        return ESP_ERR_NOT_SUPPORTED;
    }
#elif defined(CONFIG_IDF_TARGET_ARCH_RISCV)
    if (hdr->e_machine != EM_RISCV) {
        klog_e(TAG, "e_machine=%u — expected EM_RISCV (243)", hdr->e_machine);
        return ESP_ERR_NOT_SUPPORTED;
    }
#endif
    if (hdr->e_shoff == 0 || hdr->e_shnum == 0) {
        klog_e(TAG, "no section headers");
        return ESP_ERR_INVALID_ARG;
    }
    return ESP_OK;
}

/* -------------------------------------------------------------------------
 * Section classification
 *
 * With -ffunction-sections the compiler emits .text.funcname, .literal.funcname
 * etc. rather than a single .text.  We match by prefix.
 * Literal pools (.literal.*) are executable data adjacent to code — we load
 * them the same way as .text (both have SHF_ALLOC | SHF_EXECINSTR).
 * ---------------------------------------------------------------------- */

typedef enum {
    SEC_IGNORE,
    SEC_TEXT,       /* .text* and .literal* */
    SEC_DATA,       /* .data* */
    SEC_RODATA,     /* .rodata* */
    SEC_BSS,        /* .bss* */
} sec_kind_t;

static sec_kind_t classify_section(const char *name, uint32_t flags)
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

/* -------------------------------------------------------------------------
 * Section loading
 * ---------------------------------------------------------------------- */

static esp_err_t load_sections(FILE              *f,
                                const elf32_hdr_t  *hdr,
                                const elf32_shdr_t *shdrs,
                                const char         *shstrtab,
                                duneos_app_t       *app)
{
    app->section_count = hdr->e_shnum;

    for (int i = 0; i < hdr->e_shnum; i++) {
        const elf32_shdr_t *sh   = &shdrs[i];
        const char         *name = shdr_name(shstrtab, sh);
        app->section_bases[i]    = NULL;

        if (sh->sh_size == 0) continue;

        sec_kind_t kind = classify_section(name, sh->sh_flags);
        if (kind == SEC_IGNORE) continue;

        bool is_exec = (kind == SEC_TEXT);
        void *mem = section_alloc(app, sh->sh_size, is_exec);
        if (!mem) {
            klog_e(TAG, "alloc failed: '%s' (%lu B)",
                     name, (unsigned long)sh->sh_size);
            return ESP_ERR_NO_MEM;
        }
        app->section_bases[i] = mem;

        if (sh->sh_type == SHT_NOBITS) {
            /* .bss: zero-initialise via write pointer (D-bus safe on Xtensa) */
            memset(to_write_ptr(mem), 0, sh->sh_size);
        } else {
            /* Read via write pointer: on Xtensa, fread cannot target IRAM
             * addresses (D-bus restriction); to_write_ptr() converts exec
             * pool IRAM addresses to their DRAM alias. */
            if (read_at(f, sh->sh_offset, to_write_ptr(mem), sh->sh_size) != ESP_OK) {
                klog_e(TAG, "read failed: '%s'", name);
                return ESP_ERR_INVALID_ARG;
            }
        }

        klog_d(TAG, "  loaded %-28s %4lu B @ %p%s",
                 name, (unsigned long)sh->sh_size, mem,
                 is_exec ? " [IRAM]" : " [DRAM]");
    }
    return ESP_OK;
}

/* -------------------------------------------------------------------------
 * Symbol resolution
 * ---------------------------------------------------------------------- */

static void *resolve_symbol(const char *name,
                              const duneos_app_manifest_t *manifest)
{
    const duneos_symbol_t *t = duneos_symbol_table_get();
    for (; t->name; t++) {
        if (strcmp(t->name, name) != 0) continue;
        if (t->required_perm && manifest &&
            !(manifest->permissions & t->required_perm)) {
            klog_w(TAG, "permission denied: '%s' (need 0x%08lx, app has 0x%08lx)",
                   name, (unsigned long)t->required_perm,
                   (unsigned long)manifest->permissions);
            return NULL;
        }
        return t->ptr;
    }
    return NULL;
}

/* Compute the runtime address of an ELF symbol. */
static void *symbol_address(const elf32_sym_t  *sym,
                             const char         *strtab,
                             const duneos_app_t *app)
{
    if (sym->st_shndx == SHN_ABS) {
        return (void *)(uintptr_t)sym->st_value;
    }
    if (sym->st_shndx == SHN_UNDEF) {
        const char *name = strtab + sym->st_name;
        if (!name[0]) return NULL;  /* symbol index 0 — always null, skip silently */
        void *ptr = resolve_symbol(name, &app->manifest);
        if (!ptr) {
            klog_e(TAG, "unresolved symbol: '%s'", name);
        }
        return ptr;
    }
    if (sym->st_shndx < MAX_SECTIONS && app->section_bases[sym->st_shndx]) {
        return (uint8_t *)app->section_bases[sym->st_shndx] + sym->st_value;
    }
    return NULL;
}

/* -------------------------------------------------------------------------
 * Relocation application
 *
 * Xtensa uses RELA (relocation with explicit addend) exclusively.
 *
 * Types handled:
 *   R_XTENSA_32        — 32-bit absolute (additive): *ptr += S + A
 *   R_XTENSA_SLOT0_OP  — patch instruction slot 0 (typically L32R)
 *   R_XTENSA_ASM_EXPAND — assembler hint, no loader action needed
 *
 * The .rela.xt.* sections (Xtensa tool metadata) target sections without
 * SHF_ALLOC and are skipped by the target-section check below.
 * ---------------------------------------------------------------------- */

/* Apply R_XTENSA_SLOT0_OP to a loaded instruction word.
 *
 * The only SLOT0_OP case we see for a simple call through a literal pool is
 * L32R (opcode 0x1), which loads a 32-bit value from a PC-relative address.
 *
 * L32R encoding (24-bit, little-endian):
 *   byte[0] = (dest_reg << 4) | 0x1
 *   byte[1] = imm16[7:0]
 *   byte[2] = imm16[15:8]
 *
 * The 16-bit signed offset encodes: target = ((PC+3) & ~3) + imm16 * 4
 * So:  imm16 = (S + A - ((PC + 3) & ~3)) / 4
 */
static esp_err_t apply_slot0_op(uint8_t  *insn_ptr,
                                 uint32_t  insn_pc,
                                 uint32_t  target_addr)
{
    uint8_t  opcode = insn_ptr[0] & 0x0F;
    int32_t  offset;

    switch (opcode) {

    case 0x1: {
        /* L32R: 16-bit signed offset in bytes[2:1], in units of 4 bytes from
         * the next 4-byte-aligned PC.  offset = (target - aligned_PC) / 4  */
        uint32_t aligned_pc = (insn_pc + 3) & ~3u;
        offset = ((int32_t)target_addr - (int32_t)aligned_pc) / 4;
        if ((offset * 4) != ((int32_t)target_addr - (int32_t)aligned_pc)) {
            klog_e(TAG, "L32R target not aligned: 0x%08lx", (unsigned long)target_addr);
            return ESP_ERR_INVALID_ARG;
        }
        if (offset < -32768 || offset > 32767) {
            klog_e(TAG, "L32R offset out of 16-bit range: %ld", (long)offset);
            return ESP_ERR_INVALID_ARG;
        }
        insn_ptr[1] = (uint8_t)(offset & 0xFF);
        insn_ptr[2] = (uint8_t)((offset >> 8) & 0xFF);
        break;
    }

    case 0x5: {
        /* CALL0/4/8/12: 18-bit signed word-offset in bits[23:6].
         * EA = ((PC+4) & ~3) + SE18 * 4  */
        uint32_t aligned_pc = (insn_pc + 4) & ~3u;
        offset = ((int32_t)target_addr - (int32_t)aligned_pc) >> 2;
        if (offset < -(1<<17) || offset > (1<<17)-1) {
            klog_e(TAG, "CALL offset out of 18-bit range at PC=0x%08lx target=0x%08lx (%ld words) — load aborted",
                   (unsigned long)insn_pc, (unsigned long)target_addr, (long)offset);
            return ESP_ERR_INVALID_ARG;
        }
        insn_ptr[0] = (insn_ptr[0] & 0x3F) | ((uint8_t)(offset & 0x3) << 6);
        insn_ptr[1] = (uint8_t)((offset >>  2) & 0xFF);
        insn_ptr[2] = (uint8_t)((offset >> 10) & 0xFF);
        break;
    }

    case 0x6: {
        /* op0=6 covers three distinct branch sub-formats, distinguished by
         * bits[5:4] of byte[0] (= op1[1:0]):
         *
         *  0x00  J (RI16/CALL format): bits[23:6] = 18-bit signed byte-offset.
         *        n=0 is always in bits[5:4]; bits[7:6] carry offset[1:0].
         *
         *  0x10  BRI12 (BEQZ/BNEZ/BLTZ/BGEZ, op1 ∈ {1,5,9,D}): 12-bit offset.
         *        bits[23:12] = imm12, bits[11:8] = s (register).
         *        byte[1] = {imm12[3:0], s[3:0]}; byte[2] = imm12[11:4].
         *
         *  0x20  BRI8 (BEQI/BNEI/BLTI/BGEI, op1 ∈ {2,6,A,E}): 8-bit offset.
         *        bits[23:16] = imm8, bits[15:8] = {b4const, s} (both preserved).
         *        Only byte[2] is updated; byte[1] is left intact. */
        offset = (int32_t)target_addr - (int32_t)(insn_pc + 4);
        uint8_t bits54 = insn_ptr[0] & 0x30;

        if (bits54 == 0x00 && offset >= -(1<<17) && offset <= (1<<17)-1) {
            /* J: 18-bit byte-offset in bits[23:6] */
            insn_ptr[0] = (insn_ptr[0] & 0x3F) | ((uint8_t)(offset & 0x3) << 6);
            insn_ptr[1] = (uint8_t)((offset >>  2) & 0xFF);
            insn_ptr[2] = (uint8_t)((offset >> 10) & 0xFF);
        } else if (bits54 == 0x10 && offset >= -2048 && offset <= 2047) {
            /* BRI12: imm12 split across byte[1][7:4] and byte[2]; register preserved. */
            insn_ptr[1] = (insn_ptr[1] & 0x0F) | ((uint8_t)(offset & 0x0F) << 4);
            insn_ptr[2] = (uint8_t)((offset >> 4) & 0xFF);
        } else if (bits54 == 0x20 && offset >= -128 && offset <= 127) {
            /* BRI8: imm8 in byte[2] only; byte[1] (b4const + register) unchanged. */
            insn_ptr[2] = (uint8_t)(int8_t)offset;
        } else {
            klog_d(TAG, "SLOT0_OP opcode 6: unhandled (bits54=0x%02x offset=%ld) at %p",
                   bits54, (long)offset, insn_ptr);
        }
        break;
    }

    case 0x7: {
        /* B format: BEQ/BNE/BLT/BGE/etc., 8-bit signed byte-offset in byte[2].
         * EA = PC + 4 + offset  */
        offset = (int32_t)target_addr - (int32_t)(insn_pc + 4);
        if (offset < -128 || offset > 127) {
            klog_d(TAG, "B-format offset out of 8-bit range at %p (%ld)", insn_ptr, (long)offset);
            break;
        }
        insn_ptr[2] = (uint8_t)(int8_t)offset;
        break;
    }

    case 0x8: case 0x9: case 0xa: case 0xb:
    case 0xc: case 0xd: case 0xe: case 0xf:
        /* Narrow (16-bit) instructions — SLOT0_OP unexpected here; skip. */
        break;

    default:
        klog_d(TAG, "SLOT0_OP: unhandled opcode 0x%x at %p — skipping", opcode, insn_ptr);
        break;
    }

    return ESP_OK;
}

#ifdef CONFIG_IDF_TARGET_ARCH_RISCV

/* -------------------------------------------------------------------------
 * RISC-V relocation application (RELA only, ET_REL ET32 LSB)
 *
 * Instruction encodings patched:
 *   U-type  (lui/auipc)  : bits[31:12] = imm[31:12]
 *   I-type  (addi, lw…)  : bits[31:20] = imm[11:0]
 *   S-type  (sw, sb…)    : bits[31:25] = imm[11:5], bits[11:7] = imm[4:0]
 *   CALL    (auipc+jalr) : two consecutive U+I words
 *
 * PCREL_HI20 / PCREL_LO12 pairing:
 *   PCREL_LO12's r_sym resolves to the runtime address of the corresponding
 *   PCREL_HI20 instruction.  We keep a small per-section ring-buffer cache of
 *   (auipc_runtime_addr → absolute_target) to look up the pair.
 * ---------------------------------------------------------------------- */

#define RISCV_HI20_CACHE  8

typedef struct { uint32_t loc; uint32_t target; } riscv_hi20_t;

/* Reset before each RELA section in the relocation loop. */
static riscv_hi20_t s_hi20[RISCV_HI20_CACHE];
static int          s_hi20_n;

static void hi20_put(uint32_t loc, uint32_t target)
{
    s_hi20[s_hi20_n % RISCV_HI20_CACHE] = (riscv_hi20_t){loc, target};
    s_hi20_n++;
}

static bool hi20_get(uint32_t loc, uint32_t *out_target)
{
    int n = s_hi20_n < RISCV_HI20_CACHE ? s_hi20_n : RISCV_HI20_CACHE;
    /* Most-recent first: LO12 always follows its HI20 in relocation order */
    for (int i = 0; i < n; i++) {
        int idx = (s_hi20_n - 1 - i + RISCV_HI20_CACHE) % RISCV_HI20_CACHE;
        if (s_hi20[idx].loc == loc) {
            *out_target = s_hi20[idx].target;
            return true;
        }
    }
    return false;
}

/* Split a signed 32-bit offset into (hi20, lo12) so that lo12 ∈ [-2048, 2047].
 * Rounding by +0x800 corrects for the sign-extension of lo12 when re-added. */
static void riscv_split(int32_t offset, int32_t *hi20, int32_t *lo12)
{
    *hi20 = (offset + 0x800) >> 12;
    *lo12 = offset - (*hi20 << 12);
}

/* U-type (lui / auipc): bits[31:12] = hi20[19:0] */
static void patch_utype(uint32_t *insn, int32_t hi20)
{
    *insn = (*insn & 0x00000FFFU) | ((uint32_t)(hi20 & 0xFFFFF) << 12);
}

/* I-type immediate: bits[31:20] = lo12[11:0] */
static void patch_itype(uint32_t *insn, int32_t lo12)
{
    *insn = (*insn & 0x000FFFFFU) | ((uint32_t)(lo12 & 0xFFF) << 20);
}

/* S-type immediate: imm[11:5] → bits[31:25], imm[4:0] → bits[11:7] */
static void patch_stype(uint32_t *insn, int32_t lo12)
{
    *insn = (*insn & 0x01FFF07FU)
          | ((uint32_t)(lo12 & 0x1F) << 7)
          | ((uint32_t)((lo12 >> 5) & 0x7F) << 25);
}

static esp_err_t apply_riscv_reloc(uint32_t *insn,
                                    uint32_t  pc,
                                    uint32_t  target,
                                    uint8_t   type)
{
    int32_t hi20, lo12, offset;

    switch (type) {

    case R_RISCV_32:
        /* Absolute 32-bit patch (function pointer tables, .rodata references) */
        *insn = target;
        return ESP_OK;

    case R_RISCV_CALL:
    case R_RISCV_CALL_PLT: {
        /* auipc rd, hi   |   jalr rd, rd, lo  (two consecutive words) */
        if (!target) return ESP_OK;  /* unresolved external — crash site logged earlier */
        offset = (int32_t)(target - pc);
        riscv_split(offset, &hi20, &lo12);
        patch_utype(insn,     hi20);
        patch_itype(insn + 1, lo12);
        return ESP_OK;
    }

    case R_RISCV_PCREL_HI20:
        /* auipc: upper 20 bits of PC-relative address; cache for paired LO12 */
        if (!target) return ESP_OK;
        offset = (int32_t)(target - pc);
        riscv_split(offset, &hi20, &lo12);
        patch_utype(insn, hi20);
        hi20_put(pc, target);   /* key = this instruction's runtime address */
        return ESP_OK;

    case R_RISCV_PCREL_LO12_I: {
        /*
         * sym_val was passed as target here (r_addend is always 0 for LO12):
         * target = S + 0 = S = runtime address of the paired PCREL_HI20 auipc.
         * Look up the cache to find the original absolute target of that auipc.
         */
        uint32_t hi_target;
        if (!hi20_get(target, &hi_target)) {
            klog_e(TAG, "PCREL_LO12_I at 0x%08lx: no cached HI20 for key 0x%08lx",
                   (unsigned long)pc, (unsigned long)target);
            return ESP_ERR_INVALID_STATE;
        }
        offset = (int32_t)(hi_target - target);
        riscv_split(offset, &hi20, &lo12);
        patch_itype(insn, lo12);
        return ESP_OK;
    }

    case R_RISCV_PCREL_LO12_S: {
        uint32_t hi_target;
        if (!hi20_get(target, &hi_target)) {
            klog_e(TAG, "PCREL_LO12_S at 0x%08lx: no cached HI20 for key 0x%08lx",
                   (unsigned long)pc, (unsigned long)target);
            return ESP_ERR_INVALID_STATE;
        }
        offset = (int32_t)(hi_target - target);
        riscv_split(offset, &hi20, &lo12);
        patch_stype(insn, lo12);
        return ESP_OK;
    }

    case R_RISCV_HI20:
        /* lui: upper 20 bits of absolute address */
        riscv_split((int32_t)target, &hi20, &lo12);
        patch_utype(insn, hi20);
        return ESP_OK;

    case R_RISCV_LO12_I:
        /* addi / load: lower 12 bits of absolute address (r_sym = same as HI20) */
        riscv_split((int32_t)target, &hi20, &lo12);
        patch_itype(insn, lo12);
        return ESP_OK;

    case R_RISCV_LO12_S:
        riscv_split((int32_t)target, &hi20, &lo12);
        patch_stype(insn, lo12);
        return ESP_OK;

    case R_RISCV_NONE:
    case R_RISCV_RELAX:
    case R_RISCV_ALIGN:
        /* Linker hints — not meaningful in a loader context */
        return ESP_OK;

    default:
        klog_d(TAG, "RISCV: unhandled reloc type %u at pc=0x%08lx — skipping",
               type, (unsigned long)pc);
        return ESP_OK;
    }
}

#endif /* CONFIG_IDF_TARGET_ARCH_RISCV */

static esp_err_t apply_relocations(FILE               *f,
                                    const elf32_hdr_t  *hdr,
                                    const elf32_shdr_t *shdrs,
                                    const char         *shstrtab,
                                    const elf32_sym_t  *symtab,
                                    int                 symcount,
                                    const char         *strtab,
                                    duneos_app_t       *app)
{
    (void)f;

    for (int i = 0; i < hdr->e_shnum; i++) {
        const elf32_shdr_t *rsh = &shdrs[i];
        if (rsh->sh_type != SHT_RELA) continue;

        /* Target section: sh_info for RELA */
        uint32_t target_idx = rsh->sh_info;
        if (target_idx >= (uint32_t)hdr->e_shnum) continue;

        void *target_base = app->section_bases[target_idx];
        if (!target_base) continue; /* section not loaded (e.g. .xt.*) */

        int nentries = rsh->sh_size / sizeof(elf32_rela_t);
        elf32_rela_t *relas = malloc(rsh->sh_size);
        if (!relas) return ESP_ERR_NO_MEM;

        if (read_at(f, rsh->sh_offset, relas, rsh->sh_size) != ESP_OK) {
            free(relas);
            return ESP_ERR_INVALID_ARG;
        }

        klog_d(TAG, "  rela %-28s → %-20s (%d entries)",
                 shdr_name(shstrtab, rsh),
                 shdr_name(shstrtab, &shdrs[target_idx]),
                 nentries);

#ifdef CONFIG_IDF_TARGET_ARCH_RISCV
        /* Reset the PCREL_HI20 cache at the start of each RELA section.
         * HI20/LO12 pairs are always within the same section, so this is safe. */
        s_hi20_n = 0;
#endif

        for (int j = 0; j < nentries; j++) {
            const elf32_rela_t *rel = &relas[j];
            uint32_t sym_idx  = ELF32_R_SYM(rel->r_info);
            uint8_t  rel_type = ELF32_R_TYPE(rel->r_info);

            if (sym_idx >= (uint32_t)symcount) {
                klog_e(TAG, "relocation symbol index %lu out of range",
                         (unsigned long)sym_idx);
                free(relas);
                return ESP_ERR_INVALID_ARG;
            }

            const elf32_sym_t *sym = &symtab[sym_idx];
            void *S = symbol_address(sym, strtab, app);

            /* Write via D-bus-safe alias; PC stays at exec address for offset math.
             * On RISC-V to_write_ptr() is the identity — no I/D split. */
            uint8_t *patch_ptr  = (uint8_t *)to_write_ptr(target_base) + rel->r_offset;
            uint32_t patch_pc   = (uint32_t)((uintptr_t)target_base + rel->r_offset);
            uint32_t target_addr = (uint32_t)(uintptr_t)S + (uint32_t)rel->r_addend;

#ifdef CONFIG_IDF_TARGET_ARCH_XTENSA
            switch (rel_type) {
            case R_XTENSA_32:
                /* Additive: existing content holds the within-section offset
                 * (pre-computed by compiler for SHF_MERGE string sections).
                 * Formula: *(ptr) += S + A, not *(ptr) = S + A. */
                if (S) *(uint32_t *)patch_ptr += target_addr;
                else   *(uint32_t *)patch_ptr  = 0;
                break;

            case R_XTENSA_SLOT0_OP: {
                if (!S) break;  /* skip; calling site will crash if reached */
                esp_err_t err = apply_slot0_op(patch_ptr, patch_pc, target_addr);
                if (err != ESP_OK) { free(relas); return err; }
                break;
            }

            case R_XTENSA_ASM_EXPAND:
                break;  /* assembler relaxation hint — no loader action */

            case R_XTENSA_DIFF32:
                /* 32-bit difference: *ptr -= S + A  (unwind tables) */
                if (S) *(int32_t *)patch_ptr -= (int32_t)target_addr;
                break;

            default:
                klog_d(TAG, "Xtensa: unhandled reloc type %u at offset 0x%lx — skipping",
                         rel_type, (unsigned long)rel->r_offset);
                break;
            }

#elif defined(CONFIG_IDF_TARGET_ARCH_RISCV)
            {
                esp_err_t rerr = apply_riscv_reloc(
                    (uint32_t *)patch_ptr, patch_pc, target_addr, rel_type);
                if (rerr != ESP_OK) { free(relas); return rerr; }
            }

#else
#error "No relocation handler for this CPU architecture — add support in loader.c"
#endif
        }

        free(relas);
    }
    return ESP_OK;
}

/* -------------------------------------------------------------------------
 * Manifest extraction
 * ---------------------------------------------------------------------- */

static esp_err_t extract_manifest(FILE               *f,
                                   const elf32_hdr_t  *hdr,
                                   const elf32_shdr_t *shdrs,
                                   const char         *shstrtab,
                                   duneos_app_manifest_t *out)
{
    for (int i = 0; i < hdr->e_shnum; i++) {
        if (strcmp(shdr_name(shstrtab, &shdrs[i]),
                   DUNEOS_MANIFEST_SECTION) != 0) continue;

        const elf32_shdr_t *sh = &shdrs[i];
        if (sh->sh_size < 2 || sh->sh_size > 4096) return ESP_ERR_INVALID_SIZE;

        char *json = malloc(sh->sh_size + 1);
        if (!json) return ESP_ERR_NO_MEM;
        if (read_at(f, sh->sh_offset, json, sh->sh_size) != ESP_OK) {
            free(json);
            return ESP_ERR_INVALID_ARG;
        }
        json[sh->sh_size] = '\0';

        strlcpy(out->name, "unknown", sizeof(out->name));
        out->required_abi_version = 1;

        const char *p;
        if ((p = strstr(json, "\"name\"")) != NULL) {
            p = strchr(p + 6, '"');
            if (p++) sscanf(p, "%63[^\"]", out->name);
        }
        if ((p = strstr(json, "\"version\"")) != NULL) {
            p = strchr(p + 9, '"');
            if (p++) sscanf(p, "%15[^\"]", out->version);
        }
        if ((p = strstr(json, "\"required_abi_version\"")) != NULL) {
            p = strchr(p + 22, ':');
            if (p) sscanf(p + 1, "%lu",
                          (unsigned long *)&out->required_abi_version);
        }
        out->permissions = 0;
        if ((p = strstr(json, "\"permissions\"")) != NULL) {
            p = strchr(p + 13, ':');
            if (p) sscanf(p + 1, "%lu", (unsigned long *)&out->permissions);
        }
        out->stack_size = 0;  /* 0 → supervisor uses DUNEOS_APP_DEFAULT_STACK */
        if ((p = strstr(json, "\"stack_size\"")) != NULL) {
            p = strchr(p + 12, ':');
            if (p) sscanf(p + 1, "%lu", (unsigned long *)&out->stack_size);
        }

        free(json);
        klog_i(TAG, "manifest: '%s' v%s (ABI>=%lu perms=0x%lx)",
                 out->name, out->version,
                 (unsigned long)out->required_abi_version,
                 (unsigned long)out->permissions);
        return ESP_OK;
    }

    klog_w(TAG, "no " DUNEOS_MANIFEST_SECTION " section");
    return ESP_OK;
}

/* -------------------------------------------------------------------------
 * Public API
 * ---------------------------------------------------------------------- */

void duneos_loader_init(void)
{
#ifdef CONFIG_IDF_TARGET_ARCH_XTENSA
    /* Verify the exec pool's IRAM alias falls within the DIRAM executable
     * region.  Fails at boot if the kernel's DRAM BSS has grown too large. */
    uintptr_t pool_iram_start = (uintptr_t)s_exec_pool + (uintptr_t)SOC_I_D_OFFSET;
    uintptr_t pool_iram_end   = pool_iram_start + sizeof(s_exec_pool);
    if (pool_iram_start < SOC_DIRAM_IRAM_LOW || pool_iram_end > SOC_DIRAM_IRAM_HIGH) {
        klog_e(TAG, "exec pool IRAM alias out of DIRAM range — "
               "reduce DUNEOS_EXEC_POOL_KB or kernel BSS");
        klog_e(TAG, "  pool DRAM %p, IRAM alias %p-%p, DIRAM %p-%p",
               s_exec_pool,
               (void *)pool_iram_start, (void *)pool_iram_end,
               (void *)SOC_DIRAM_IRAM_LOW, (void *)SOC_DIRAM_IRAM_HIGH);
    } else {
        klog_i(TAG, "exec pool DRAM=%p IRAM=%p (%u KB)",
               s_exec_pool, (void *)pool_iram_start,
               CONFIG_DUNEOS_EXEC_POOL_KB);
    }
#endif

    static const duneos_loader_ops_t ops = {
        .load         = duneos_loader_load,
        .run          = duneos_loader_run,
        .unload       = duneos_loader_unload,
        .get_manifest = duneos_loader_get_manifest,
    };
    duneos_supervisor_register_loader(&ops);
}

esp_err_t duneos_loader_load(const char *path, duneos_app_t **out_app)
{
    if (!path || !out_app) return ESP_ERR_INVALID_ARG;
    *out_app = NULL;

    FILE *f = fopen(path, "rb");
    if (!f) {
        klog_e(TAG, "cannot open '%s'", path);
        return ESP_ERR_NOT_FOUND;
    }

    esp_err_t    err     = ESP_FAIL;
    elf32_hdr_t  hdr;
    elf32_shdr_t *shdrs    = NULL;
    char         *shstrtab = NULL;
    elf32_sym_t  *symtab   = NULL;
    char         *strtab   = NULL;
    int           symcount = 0;
    duneos_app_t *app      = NULL;

    /* 1. ELF header */
    if (read_at(f, 0, &hdr, sizeof(hdr)) != ESP_OK) {
        klog_e(TAG, "cannot read ELF header");
        err = ESP_ERR_INVALID_ARG;
        goto out;
    }
    err = elf_validate(&hdr);
    if (err != ESP_OK) goto out;

    if (hdr.e_shnum > MAX_SECTIONS) {
        klog_e(TAG, "too many sections: %u (max %d)", hdr.e_shnum, MAX_SECTIONS);
        err = ESP_ERR_NOT_SUPPORTED;
        goto out;
    }

    klog_d(TAG, "loading '%s' (%u sections)", path, hdr.e_shnum);

    /* 2. Section header table */
    shdrs = malloc(hdr.e_shnum * sizeof(elf32_shdr_t));
    if (!shdrs) { err = ESP_ERR_NO_MEM; goto out; }
    if (read_at(f, hdr.e_shoff, shdrs, hdr.e_shnum * sizeof(elf32_shdr_t)) != ESP_OK) {
        err = ESP_ERR_INVALID_ARG;
        goto out;
    }

    /* 3. Section name string table */
    {
        const elf32_shdr_t *ss = &shdrs[hdr.e_shstrndx];
        shstrtab = malloc(ss->sh_size + 1);
        if (!shstrtab) { err = ESP_ERR_NO_MEM; goto out; }
        if (read_at(f, ss->sh_offset, shstrtab, ss->sh_size) != ESP_OK) {
            err = ESP_ERR_INVALID_ARG;
            goto out;
        }
        shstrtab[ss->sh_size] = '\0';
    }

    /* 4. Symbol table + string table */
    for (int i = 0; i < hdr.e_shnum; i++) {
        if (shdrs[i].sh_type != SHT_SYMTAB) continue;

        symcount = shdrs[i].sh_size / sizeof(elf32_sym_t);
        symtab   = malloc(shdrs[i].sh_size);
        if (!symtab) { err = ESP_ERR_NO_MEM; goto out; }
        if (read_at(f, shdrs[i].sh_offset, symtab, shdrs[i].sh_size) != ESP_OK) {
            err = ESP_ERR_INVALID_ARG;
            goto out;
        }

        const elf32_shdr_t *str_sh = &shdrs[shdrs[i].sh_link];
        strtab = malloc(str_sh->sh_size + 1);
        if (!strtab) { err = ESP_ERR_NO_MEM; goto out; }
        if (read_at(f, str_sh->sh_offset, strtab, str_sh->sh_size) != ESP_OK) {
            err = ESP_ERR_INVALID_ARG;
            goto out;
        }
        strtab[str_sh->sh_size] = '\0';
        break;
    }

    /* 5. Allocate app descriptor */
    app = calloc(1, sizeof(duneos_app_t));
    if (!app) { err = ESP_ERR_NO_MEM; goto out; }

#ifdef CONFIG_IDF_TARGET_ARCH_XTENSA
    app->exec_pool_mark = s_exec_pool_used;
#endif

    /* 6. Manifest */
    err = extract_manifest(f, &hdr, shdrs, shstrtab, &app->manifest);
    if (err != ESP_OK) goto out;

    if (app->manifest.required_abi_version > DUNEOS_ABI_VERSION) {
        klog_e(TAG, "app requires ABI v%lu, kernel is v%d",
                 (unsigned long)app->manifest.required_abi_version,
                 DUNEOS_ABI_VERSION);
        err = ESP_ERR_NOT_SUPPORTED;
        goto out;
    }

    /* 7. Load sections */
    err = load_sections(f, &hdr, shdrs, shstrtab, app);
    if (err != ESP_OK) goto out;

#ifdef CONFIG_IDF_TARGET_ARCH_XTENSA
    app->exec_pool_end = s_exec_pool_used;
    klog_d(TAG, "exec pool: %zu / %u KB used",
           s_exec_pool_used, CONFIG_DUNEOS_EXEC_POOL_KB);
#endif

    /* 8. Apply relocations */
    err = apply_relocations(f, &hdr, shdrs, shstrtab,
                             symtab, symcount, strtab, app);
    if (err != ESP_OK) goto out;

#ifdef CONFIG_IDF_TARGET_ARCH_XTENSA
    /* Scan Xtensa literal pools for stale exec-pool pointers past the loaded
     * code end — jumping there would hit zeroed memory → IllegalInstruction.
     * DROM addresses are NOT rejected here: ABI-resolved functions in flash
     * (newlib, ESP-IDF) are valid call targets even though they live in DROM. */
    {
        uintptr_t pool_iram_base  = (uintptr_t)s_exec_pool + (uintptr_t)SOC_I_D_OFFSET;
        uintptr_t pool_loaded_end = pool_iram_base + app->exec_pool_end;

        for (int i = 0; i < hdr.e_shnum; i++) {
            const char *nm = shdr_name(shstrtab, &shdrs[i]);
            if (strncmp(nm, ".literal", 8) != 0) continue;
            void *base = app->section_bases[i];
            if (!base) continue;
            uint32_t *dram = (uint32_t *)to_write_ptr(base);
            size_t nw = shdrs[i].sh_size / 4;
            for (size_t k = 0; k < nw; k++) {
                uint32_t v = dram[k];
                bool is_bad_pool = ((uintptr_t)v >= pool_loaded_end &&
                                    (uintptr_t)v <  pool_iram_base + sizeof(s_exec_pool));
                if (is_bad_pool) {
                    klog_e(TAG, "literal pool %s[%zu]=0x%08lx — past pool end (jump to zeros)",
                           nm, k, (unsigned long)v);
                    err = ESP_ERR_INVALID_STATE;
                    goto out;
                }
            }
        }
    }

    /* Stores to IRAM via the D-bus alias are not visible to the instruction
     * pipeline until ISYNC completes. */
    asm volatile("isync" ::: "memory");
#endif /* CONFIG_IDF_TARGET_ARCH_XTENSA */

    /* 9. Locate app_main */
    for (int i = 0; i < symcount; i++) {
        const elf32_sym_t *sym = &symtab[i];
        if (sym->st_shndx == SHN_UNDEF || sym->st_shndx >= hdr.e_shnum) continue;
        if (strcmp(strtab + sym->st_name, "app_main") != 0) continue;

        void *base = app->section_bases[sym->st_shndx];
        if (!base) {
            klog_e(TAG, "app_main is in an unloaded section");
            err = ESP_ERR_INVALID_ARG;
            goto out;
        }
        app->entry = (void (*)(void))((uint8_t *)base + sym->st_value);
        klog_d(TAG, "app_main @ %p", (void *)app->entry);
        break;
    }

    if (!app->entry) {
        klog_e(TAG, "app_main not found in symbol table");
        err = ESP_ERR_NOT_FOUND;
        goto out;
    }

    *out_app = app;
    app = NULL;
    err = ESP_OK;

out:
    fclose(f);
    free(shdrs);
    free(shstrtab);
    free(symtab);
    free(strtab);
    if (app) duneos_loader_unload(app);
    return err;
}

/* -------------------------------------------------------------------------
 * App discovery — scan /sd/apps/ for ELF files
 * ---------------------------------------------------------------------- */

/*
 * Lightweight manifest read: open an ELF, find .duneos_manifest, parse it.
 * Does not load any section into PSRAM. Used during scan only.
 */
static esp_err_t read_manifest_from_file(const char            *path,
                                          duneos_app_manifest_t *out)
{
    FILE *f = fopen(path, "rb");
    if (!f) return ESP_ERR_NOT_FOUND;

    esp_err_t    err  = ESP_FAIL;
    elf32_hdr_t  hdr;
    elf32_shdr_t *shdrs    = NULL;
    char         *shstrtab = NULL;

    if (read_at(f, 0, &hdr, sizeof(hdr)) != ESP_OK) goto out;
    if (elf_validate(&hdr) != ESP_OK)                goto out;
    if (hdr.e_shnum > MAX_SECTIONS)                  goto out;

    shdrs = malloc(hdr.e_shnum * sizeof(elf32_shdr_t));
    if (!shdrs) { err = ESP_ERR_NO_MEM; goto out; }
    if (read_at(f, hdr.e_shoff, shdrs,
                hdr.e_shnum * sizeof(elf32_shdr_t)) != ESP_OK) goto out;

    {
        const elf32_shdr_t *ss = &shdrs[hdr.e_shstrndx];
        shstrtab = malloc(ss->sh_size + 1);
        if (!shstrtab) { err = ESP_ERR_NO_MEM; goto out; }
        if (read_at(f, ss->sh_offset, shstrtab, ss->sh_size) != ESP_OK) goto out;
        shstrtab[ss->sh_size] = '\0';
    }

    err = extract_manifest(f, &hdr, shdrs, shstrtab, out);

out:
    fclose(f);
    free(shdrs);
    free(shstrtab);
    return err;
}

esp_err_t duneos_loader_scan(duneos_app_info_t *list, int max, int *found)
{
    if (!list || max <= 0 || !found) return ESP_ERR_INVALID_ARG;
    *found = 0;

    DIR *dir = opendir(DUNEOS_APPS_DIR);
    if (!dir) {
        klog_w(TAG, "cannot open %s", DUNEOS_APPS_DIR);
        return ESP_ERR_NOT_FOUND;
    }

    struct dirent *ent;
    while ((ent = readdir(dir)) != NULL && *found < max) {
        /* Accept .elf (development) and .dap (DuneOS Application Package) */
        size_t len = strlen(ent->d_name);
        if (len < 5) continue;
        const char *ext = ent->d_name + len - 4;
        if (strcasecmp(ext, ".elf") != 0 && strcasecmp(ext, ".dap") != 0) continue;

        duneos_app_info_t *info = &list[*found];
        snprintf(info->path, sizeof(info->path),
                 "%s/%s", DUNEOS_APPS_DIR, ent->d_name);

        esp_err_t err = read_manifest_from_file(info->path, &info->meta);
        if (err != ESP_OK) {
            klog_w(TAG, "skipping '%s': cannot read manifest", ent->d_name);
            continue;
        }

        if (info->meta.required_abi_version > DUNEOS_ABI_VERSION) {
            klog_w(TAG, "skipping '%s': requires ABI v%lu, kernel is v%d",
                     ent->d_name,
                     (unsigned long)info->meta.required_abi_version,
                     DUNEOS_ABI_VERSION);
            continue;
        }

        klog_i(TAG, "  [%d] %s  v%s  %s",
                 *found, info->meta.name, info->meta.version, info->path);
        (*found)++;
    }

    closedir(dir);
    klog_i(TAG, "scan: %d app(s) found in %s", *found, DUNEOS_APPS_DIR);
    return ESP_OK;
}

const duneos_app_info_t *duneos_loader_select(const duneos_app_info_t *list,
                                               int count)
{
    if (!list || count == 0) return NULL;

    /* Read /sd/autoboot — contains just the app name, no extension */
    FILE *f = fopen(DUNEOS_AUTOBOOT_FILE, "r");
    if (f) {
        char name[DUNEOS_APP_NAME_MAX] = {0};
        if (fgets(name, sizeof(name), f)) {
            /* Strip trailing newline */
            name[strcspn(name, "\r\n")] = '\0';
            fclose(f);
            for (int i = 0; i < count; i++) {
                if (strcmp(list[i].meta.name, name) == 0) {
                    klog_i(TAG, "autoboot: '%s'", name);
                    return &list[i];
                }
            }
            klog_w(TAG, "autoboot '%s' not found — using first app", name);
        } else {
            fclose(f);
        }
    }

    return &list[0];
}

const duneos_app_manifest_t *duneos_loader_get_manifest(const duneos_app_t *app)
{
    return app ? &app->manifest : NULL;
}

esp_err_t duneos_loader_run(duneos_app_t *app)
{
    if (!app || !app->entry) return ESP_ERR_INVALID_ARG;

#ifdef CONFIG_IDF_TARGET_ARCH_XTENSA
    /* Memory write barrier + instruction sync: ensures all D-bus writes to
     * the exec pool are visible to the I-bus before the branch. */
    __asm__ volatile("memw" ::: "memory");
    __asm__ volatile("isync" ::: "memory");
#endif

    klog_d(TAG, "jumping to app_main @ %p", (void *)app->entry);
    app->entry();
    return ESP_OK;
}

#define CAPTURE_PATH "/tmp/.duneos_stdout"

esp_err_t duneos_loader_run_captured(duneos_app_t *app,
                                      char **out_buf, size_t *out_len)
{
    if (!app || !app->entry || !out_buf || !out_len) return ESP_ERR_INVALID_ARG;
    *out_buf = NULL;
    *out_len = 0;

    /* fcntl(F_DUPFD) is not supported by the USB-JTAG console VFS driver.
     * Instead: close fd 1 to free the slot, then immediately open the capture
     * file so it lands at fd 1 (lowest available fd).  The caller (g_shell)
     * does not use fd 1 itself — stdout stays unrestored after capture. */
    close(STDOUT_FILENO);
    int capfd = open(CAPTURE_PATH, O_WRONLY | O_CREAT | O_TRUNC, 0600);
    if (capfd < 0) {
        klog_e(TAG, "capture: open failed: errno %d", errno);
        return ESP_FAIL;
    }
    if (capfd != STDOUT_FILENO) {
        close(capfd);
        klog_e(TAG, "capture: expected fd 1, got %d", capfd);
        return ESP_ERR_NOT_SUPPORTED;
    }

    klog_d(TAG, "jumping to app_main @ %p (captured)", (void *)app->entry);
    app->entry();

    close(STDOUT_FILENO);

    int rfd = open(CAPTURE_PATH, O_RDONLY);
    if (rfd < 0) {
        klog_e(TAG, "capture: cannot read back " CAPTURE_PATH);
        return ESP_FAIL;
    }

    struct stat st;
    fstat(rfd, &st);
    size_t size = (size_t)st.st_size;

    char *buf = malloc(size + 1);
    if (!buf) {
        close(rfd);
        return ESP_ERR_NO_MEM;
    }

    ssize_t n = read(rfd, buf, size);
    close(rfd);
    unlink(CAPTURE_PATH);

    if (n < 0) {
        free(buf);
        return ESP_FAIL;
    }

    buf[n] = '\0';
    *out_buf = buf;
    *out_len = (size_t)n;

    klog_d(TAG, "capture: %zu byte(s) captured", (size_t)n);
    return ESP_OK;
}

void duneos_loader_unload(duneos_app_t *app)
{
    if (!app) return;
    for (int i = 0; i < app->alloc_count; i++)
        heap_caps_free(app->allocs[i]);
#ifdef CONFIG_IDF_TARGET_ARCH_XTENSA
    /* LIFO reclaim: if this was the last app to allocate from the pool,
     * rewind the bump pointer so the next load can reuse the space. */
    if (s_exec_pool_used == app->exec_pool_end)
        s_exec_pool_used = app->exec_pool_mark;
#endif
    free(app);
}
