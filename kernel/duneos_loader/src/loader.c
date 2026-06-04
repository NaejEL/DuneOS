#include "duneos/loader.h"
#include "duneos/elf.h"
#include "duneos/supervisor.h"
#include "duneos/api.h"

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
#include "esp_rom_sys.h"
#include "cJSON.h"

#include <setjmp.h>
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

#ifdef CONFIG_IDF_TARGET_ARCH_XTENSA
#include "soc/soc.h"
/* Plain ESP32 soc.h omits this convenience define; compute it the same way. */
#ifndef SOC_I_D_OFFSET
#define SOC_I_D_OFFSET (SOC_DIRAM_IRAM_LOW - SOC_DIRAM_DRAM_LOW)
#endif
#endif

static const char *TAG = "duneos/loader";

/* -------------------------------------------------------------------------
 * Captured-app exit handshake (ADR 016).
 *
 * loader_run_captured() runs the app's app_main as a regular function call
 * in the caller's task. If the app calls duneos_exit(N) the supervisor's
 * default path is vTaskDelete(NULL) — which would kill the caller (the
 * shell). To unwind cleanly back to the loader, we install a setjmp
 * checkpoint before app_main() and ask duneos_exit() to longjmp here
 * instead of deleting the task.
 *
 * Single global because captured runs do not nest — the lock enforces
 * that. A nested attempt fails fast rather than corrupting the jmp_buf.
 * ---------------------------------------------------------------------- */
static jmp_buf            *s_captured_jmp  = NULL;   /* protected by s_captured_mux */
static int                 s_captured_code = 0;
static portMUX_TYPE        s_captured_mux  = portMUX_INITIALIZER_UNLOCKED;
static SemaphoreHandle_t   s_captured_lock = NULL;   /* one-captured-run-at-a-time */
/* Serializes load/unload: both mutate the exec-pool bump allocator and the
 * exec-staging globals, and supervisor_launch() calls load() outside its own
 * lock, so two tasks could otherwise load concurrently and corrupt them. */
static SemaphoreHandle_t   s_loader_lock   = NULL;
static void unload_locked(duneos_app_t *app);   /* caller holds s_loader_lock */

bool duneos_loader_captured_active(void)
{
    bool active;
    portENTER_CRITICAL(&s_captured_mux);
    active = (s_captured_jmp != NULL);
    portEXIT_CRITICAL(&s_captured_mux);
    return active;
}

void __attribute__((noreturn)) duneos_loader_captured_longjmp(int code)
{
    portENTER_CRITICAL(&s_captured_mux);
    jmp_buf *env = s_captured_jmp;
    s_captured_code = code;
    portEXIT_CRITICAL(&s_captured_mux);

    if (env) longjmp(*env, 1);

    /* Reachable only on a programming error (active reported true, env
     * cleared between then and now). Spin instead of returning so the
     * caller (duneos_exit) can never resume into freed code. */
    klog_e(TAG, "captured_longjmp: env vanished (code=%d)", code);
    while (1) {}
}

int duneos_loader_get_captured_exit_code(void)
{
    int code;
    portENTER_CRITICAL(&s_captured_mux);
    code = s_captured_code;
    portEXIT_CRITICAL(&s_captured_mux);
    return code;
}

/* -------------------------------------------------------------------------
 * Internal app descriptor
 * ---------------------------------------------------------------------- */

#define MAX_SECTIONS 512

struct duneos_app {
    duneos_app_manifest_t manifest;

    /* Runtime base address of each section, indexed by section header index.
     * NULL for sections not loaded into memory (no SHF_ALLOC, size 0, etc.) */
    void *section_bases[MAX_SECTIONS];
    int   section_count;

    /* Monolithic pool for all data sections (rodata + data + bss).
     * One contiguous allocation freed in a single heap_caps_free on unload. */
    uint8_t *data_pool;
    size_t   data_pool_size;

#ifdef CONFIG_IDF_TARGET_ARCH_XTENSA
    void  *exec_block;       /* IRAM alias — used as section_bases[i] */
    size_t exec_block_size;
    size_t exec_pool_mark;   /* s_exec_pool_used before this app's exec alloc */
    size_t exec_pool_end;    /* s_exec_pool_used after this app's exec alloc */
#endif

    void (*entry)(void);
};

/* -------------------------------------------------------------------------
 * Helpers
 * ---------------------------------------------------------------------- */

#ifdef CONFIG_IDF_TARGET_ARCH_XTENSA

/* On Xtensa, the same physical SRAM is dual-mapped: D-bus (DRAM, r/w) and
 * I-bus (IRAM, exec-only).  D-bus stores cannot target IRAM addresses, so each
 * app's .text/.literal sections are staged in a contiguous DRAM scratch buffer,
 * relocated there, then installed into the IRAM exec pool — executed via the
 * IRAM address directly.
 *
 * The install must respect the per-SoC IRAM↔DRAM alias:
 *   ESP32-S3 / S2 : DIRAM is linearly mapped — alias = IRAM - SOC_I_D_OFFSET,
 *                   valid for the whole region (contiguous in both views).
 *   ESP32 (LX6)   : SOC_DIRAM_INVERTED — the D-bus and I-bus views of SRAM1 are
 *                   in REVERSE word order across the entire region, so contiguous
 *                   IRAM is NON-contiguous in DRAM.  A single base-pointer memcpy
 *                   corrupts the image; every word must be aliased individually,
 *                   per esp_ptr_diram_iram_to_dram() in ESP-IDF:
 *                     DRAM = SOC_DIRAM_DRAM_LOW + (SOC_DIRAM_IRAM_HIGH - IRAM) - 4
 *
 * Requires CONFIG_ESP_SYSTEM_MEMPROT_FEATURE=n (sdkconfig.defaults). */

#ifndef CONFIG_DUNEOS_EXEC_POOL_KB
#define CONFIG_DUNEOS_EXEC_POOL_KB 64
#endif

/* DRAM alias of one word-aligned DIRAM IRAM address — used only by the final
 * install pass (build_install_exec).  Mirrors ESP-IDF esp_ptr_diram_iram_to_dram. */
static inline void *iram_word_dram_alias(uintptr_t iram)
{
#ifdef CONFIG_IDF_TARGET_ESP32
    return (void *)(SOC_DIRAM_DRAM_LOW + (SOC_DIRAM_IRAM_HIGH - iram) - 4u);
#else
    return (void *)(iram - (uintptr_t)SOC_I_D_OFFSET);
#endif
}

static uint8_t *s_exec_pool      = NULL;
static size_t   s_exec_pool_size = 0;
static size_t   s_exec_pool_used = 0;

/* While an app loads, its exec-pool (.text/.literal) sections are staged in this
 * contiguous DRAM scratch buffer, laid out 1:1 with the IRAM exec block.
 * to_write_ptr() redirects exec-pool writes here; build_install_exec() copies the
 * finished image into IRAM and frees the buffer before the app runs — transient,
 * so it leaves no lasting allocation and cannot fragment the exec pool. */
static uint8_t  *s_build_scratch   = NULL;
static uintptr_t s_build_exec_base = 0;   /* IRAM base of the app's exec block */
static size_t    s_build_exec_size = 0;

/* Writable address for a load-time store.  Exec-pool addresses are redirected to
 * the DRAM scratch buffer (contiguous, no inversion); everything else (data pool)
 * is written directly. */
static inline void *to_write_ptr(const void *addr)
{
    uintptr_t a = (uintptr_t)addr;
    if (s_build_scratch && a >= s_build_exec_base &&
            a < s_build_exec_base + s_build_exec_size)
        return s_build_scratch + (a - s_build_exec_base);
    return (void *)addr;
}

/* Install the relocated exec image from the DRAM scratch buffer into the IRAM
 * exec block, one word at a time via each word's DRAM alias (mandatory on ESP32,
 * where the DIRAM word order is inverted).  size must be a 4-byte multiple. */
static void build_install_exec(uintptr_t iram_base, const uint8_t *scratch,
                               size_t size)
{
    const uint32_t *src   = (const uint32_t *)scratch;
    size_t          words = size / 4u;
    for (size_t k = 0; k < words; k++)
        *(uint32_t *)iram_word_dram_alias(iram_base + 4u * k) = src[k];
}

#else /* RISC-V or other: no IRAM/DRAM split, all memory is writable */

static inline void *to_write_ptr(const void *addr) { return (void *)addr; }

#endif /* CONFIG_IDF_TARGET_ARCH_XTENSA */

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

    /* --- Pass 1: measure total section sizes ---
     * On Xtensa: exec_total covers .text/.literal; data_total covers the rest.
     * On RISC-V: no IRAM/DRAM split — all sections go into data_total. */
    size_t data_total = 0;
#ifdef CONFIG_IDF_TARGET_ARCH_XTENSA
    size_t exec_total = 0;
#endif
    for (int i = 0; i < hdr->e_shnum; i++) {
        const elf32_shdr_t *sh   = &shdrs[i];
        const char         *name = shdr_name(shstrtab, sh);
        if (sh->sh_size == 0) continue;
        sec_kind_t kind = classify_section(name, sh->sh_flags);
        if (kind == SEC_IGNORE) continue;
#ifdef CONFIG_IDF_TARGET_ARCH_XTENSA
        if (kind == SEC_TEXT) {
            exec_total += (sh->sh_size + 3u) & ~3u;
            continue;
        }
#endif
        data_total += (sh->sh_size + 3u) & ~3u;
    }

    /* --- Allocate exec block from static DRAM pool (Xtensa IRAM alias) --- */
#ifdef CONFIG_IDF_TARGET_ARCH_XTENSA
    if (exec_total > 0) {
        size_t aligned = (s_exec_pool_used + 3u) & ~3u;
        if (!s_exec_pool || aligned + exec_total > s_exec_pool_size) {
            klog_e(TAG, "exec pool full (%zu B requested, %zu B free)",
                   exec_total, s_exec_pool ? s_exec_pool_size - aligned : 0u);
            return ESP_ERR_NO_MEM;
        }
        app->exec_pool_mark  = aligned;
        void *iram           = s_exec_pool + aligned;  /* already IRAM — executable */
        s_exec_pool_used     = aligned + exec_total;
        app->exec_pool_end   = s_exec_pool_used;
        app->exec_block      = iram;
        app->exec_block_size = exec_total;

        /* Stage exec sections in DRAM; build_install_exec() copies the relocated
         * image into IRAM after relocation (freed in load_app before the app runs). */
        s_build_exec_base = (uintptr_t)iram;
        s_build_exec_size = exec_total;
        s_build_scratch   = heap_caps_malloc(exec_total,
                                             MALLOC_CAP_8BIT | MALLOC_CAP_INTERNAL);
        if (!s_build_scratch) {
            klog_e(TAG, "exec staging alloc failed (%zu B)", exec_total);
            return ESP_ERR_NO_MEM;
        }
        klog_d(TAG, "exec block: %zu B IRAM=%p scratch=%p pool=%zu/%zu",
               exec_total, iram, (void *)s_build_scratch,
               s_exec_pool_used, s_exec_pool_size);
    }
#endif

    /* --- Allocate monolithic data pool (one malloc for all data sections) --- */
    if (data_total > 0) {
#ifdef CONFIG_SPIRAM
        app->data_pool = heap_caps_malloc(data_total,
                                          MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (!app->data_pool)
            app->data_pool = heap_caps_malloc(data_total,
                                               MALLOC_CAP_8BIT | MALLOC_CAP_INTERNAL);
#else
        app->data_pool = heap_caps_malloc(data_total,
                                          MALLOC_CAP_8BIT | MALLOC_CAP_INTERNAL);
#endif
        if (!app->data_pool) {
            klog_e(TAG, "data pool alloc failed (%zu B)", data_total);
            return ESP_ERR_NO_MEM;
        }
        app->data_pool_size = data_total;
        klog_d(TAG, "data pool: %zu B @ %p", data_total, app->data_pool);
    }

    /* --- Pass 2: place each section into exec block or data pool --- */
    size_t data_offset = 0;
#ifdef CONFIG_IDF_TARGET_ARCH_XTENSA
    size_t exec_offset = 0;
#endif

    for (int i = 0; i < hdr->e_shnum; i++) {
        const elf32_shdr_t *sh   = &shdrs[i];
        const char         *name = shdr_name(shstrtab, sh);
        app->section_bases[i]    = NULL;

        if (sh->sh_size == 0) continue;

        sec_kind_t kind = classify_section(name, sh->sh_flags);
        if (kind == SEC_IGNORE) continue;

        void *mem;

#ifdef CONFIG_IDF_TARGET_ARCH_XTENSA
        if (kind == SEC_TEXT) {
            mem = (uint8_t *)app->exec_block + exec_offset;
            exec_offset += (sh->sh_size + 3u) & ~3u;
        } else
#endif
        {
            /* Place in data pool at current offset */
            mem = app->data_pool + data_offset;
            data_offset += (sh->sh_size + 3u) & ~3u;
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

        klog_d(TAG, "  loaded %-28s %4lu B @ %p [%s]",
                 name, (unsigned long)sh->sh_size, mem,
                 kind == SEC_TEXT ? "IRAM" : "pool");
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

        uint32_t target_sec_size = shdrs[target_idx].sh_size;

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

            /* Guard against out-of-bounds r_offset — a corrupt or Xtensa-generated
             * section-end sentinel reloc would write past the data_pool into
             * adjacent heap metadata, silently corrupting FreeRTOS list structures.
             * Skip rather than abort: if the reloc was needed the app will fault
             * and be killed by the exception handler instead of crashing the kernel. */
            if (rel->r_offset >= target_sec_size) {
                klog_d(TAG, "reloc[%d] skip: r_offset=0x%lx >= sec_size=0x%lx (%s)",
                       j, (unsigned long)rel->r_offset, (unsigned long)target_sec_size,
                       shdr_name(shstrtab, &shdrs[target_idx]));
                continue;
            }

            const elf32_sym_t *sym = &symtab[sym_idx];
            void *S = symbol_address(sym, strtab, app);

            /* Write via D-bus-safe alias; PC stays at exec address for offset math.
             * to_write_ptr is called on the final per-byte address, not the section
             * base, so cross-block relocations on ESP32 (non-contiguous DIRAM) work
             * correctly.  On RISC-V to_write_ptr() is the identity. */
            uint8_t *patch_ptr  = (uint8_t *)to_write_ptr((uint8_t *)target_base + rel->r_offset);
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

/* Parse a cJSON number item as uint32, with a sentinel for "not present" */
static uint32_t json_u32(const cJSON *root, const char *key, uint32_t dflt)
{
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(root, key);
    if (!cJSON_IsNumber(item)) return dflt;
    double v = item->valuedouble;
    if (v < 0.0) return dflt;
    if (v > (double)UINT32_MAX) return UINT32_MAX;
    return (uint32_t)v;
}

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
        if (sh->sh_size < 2 || sh->sh_size > 4096) {
            klog_e(TAG, "manifest section size invalid: %lu",
                   (unsigned long)sh->sh_size);
            return ESP_ERR_INVALID_SIZE;
        }

        char *raw = malloc(sh->sh_size + 1);
        if (!raw) return ESP_ERR_NO_MEM;
        if (read_at(f, sh->sh_offset, raw, sh->sh_size) != ESP_OK) {
            free(raw);
            return ESP_ERR_INVALID_ARG;
        }
        raw[sh->sh_size] = '\0';

        /* Apply safe defaults before parsing — unknown fields keep these. */
        strlcpy(out->name,    "unknown", sizeof(out->name));
        strlcpy(out->version, "0.0.0",   sizeof(out->version));
        out->required_abi_version = 1;
        out->permissions    = 0;
        out->stack_size     = 0;   /* 0 → supervisor uses DUNEOS_APP_DEFAULT_STACK */
        out->heap_size      = 0;   /* 0 → global heap */
        out->wdt_timeout_ms = 0;   /* 0 → WDT disabled */

        cJSON *root = cJSON_ParseWithLength(raw, sh->sh_size);
        free(raw);  /* cJSON owns its own copy — we can free raw now */

        if (!root) {
            klog_w(TAG, "manifest JSON parse error — booting with defaults");
            return ESP_OK;  /* non-fatal: boot with defaults rather than reject */
        }

        const cJSON *item;
        if ((item = cJSON_GetObjectItemCaseSensitive(root, "name")) &&
            cJSON_IsString(item) && item->valuestring)
            strlcpy(out->name, item->valuestring, sizeof(out->name));

        if ((item = cJSON_GetObjectItemCaseSensitive(root, "version")) &&
            cJSON_IsString(item) && item->valuestring)
            strlcpy(out->version, item->valuestring, sizeof(out->version));

        if ((item = cJSON_GetObjectItemCaseSensitive(root, "arch")) &&
            cJSON_IsString(item) && item->valuestring)
            strlcpy(out->arch, item->valuestring, sizeof(out->arch));

        out->required_abi_version = json_u32(root, "required_abi_version", 1);
        out->permissions          = json_u32(root, "permissions",          0);
        out->stack_size           = json_u32(root, "stack_size",           0);
        out->heap_size            = json_u32(root, "heap_size",            0);
        out->wdt_timeout_ms       = json_u32(root, "wdt_timeout_ms",       0);

        cJSON_Delete(root);

        klog_i(TAG, "manifest: '%s' v%s arch='%s' (ABI>=%lu perms=0x%lx"
               " stack=%lu heap=%lu wdt=%lu ms)",
               out->name, out->version, out->arch,
               (unsigned long)out->required_abi_version,
               (unsigned long)out->permissions,
               (unsigned long)out->stack_size,
               (unsigned long)out->heap_size,
               (unsigned long)out->wdt_timeout_ms);
        return ESP_OK;
    }

    klog_w(TAG, "no " DUNEOS_MANIFEST_SECTION " section — booting with defaults");
    return ESP_OK;
}

/* -------------------------------------------------------------------------
 * Public API
 * ---------------------------------------------------------------------- */

void duneos_loader_init(void)
{
    if (!s_loader_lock) s_loader_lock = xSemaphoreCreateMutex();

#ifdef CONFIG_IDF_TARGET_ARCH_XTENSA
    /* IRAM on ESP32 requires 32-bit aligned access — do NOT combine
     * MALLOC_CAP_EXEC with MALLOC_CAP_8BIT.  Writing always goes via the
     * DRAM alias (to_write_ptr); executing via the IRAM address directly.
     * Try the configured size first; halve down to 8 KB if IRAM is tight. */
    size_t _pool_bytes = CONFIG_DUNEOS_EXEC_POOL_KB * 1024u;
    while (_pool_bytes >= 8 * 1024u) {
        s_exec_pool = heap_caps_aligned_alloc(16, _pool_bytes, MALLOC_CAP_EXEC);
        if (s_exec_pool) break;
        _pool_bytes /= 2;
    }
    if (!s_exec_pool) {
        klog_e(TAG, "exec pool alloc failed — apps cannot be loaded");
    } else {
        s_exec_pool_size = _pool_bytes;
        if (_pool_bytes < CONFIG_DUNEOS_EXEC_POOL_KB * 1024u)
            klog_w(TAG, "exec pool reduced to %zu KB (IRAM heap tight)",
                   _pool_bytes / 1024u);
        uintptr_t pool_start = (uintptr_t)s_exec_pool;
        uintptr_t pool_end   = pool_start + _pool_bytes;
        if (pool_start < SOC_DIRAM_IRAM_LOW || pool_end > SOC_DIRAM_IRAM_HIGH) {
            klog_e(TAG, "exec pool outside DIRAM IRAM range — apps may crash");
            klog_e(TAG, "  pool=%p-%p  DIRAM=%p-%p",
                   (void *)pool_start, (void *)pool_end,
                   (void *)SOC_DIRAM_IRAM_LOW, (void *)SOC_DIRAM_IRAM_HIGH);
        } else {
            klog_i(TAG, "exec pool IRAM=%p DRAM(base word)=%p (%u KB)",
                   s_exec_pool,
                   iram_word_dram_alias((uintptr_t)s_exec_pool),
                   (unsigned)(_pool_bytes / 1024u));
        }
    }
    s_exec_pool_used = 0;
#endif

    static const duneos_loader_ops_t ops = {
        .load             = duneos_loader_load,
        .run              = duneos_loader_run,
        .unload           = duneos_loader_unload,
        .get_manifest     = duneos_loader_get_manifest,
        .get_data_pool    = duneos_loader_get_data_pool,
        /* ADR 016: captured-mode exit unwinding. */
        .captured_active  = duneos_loader_captured_active,
        .captured_longjmp = duneos_loader_captured_longjmp,
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

    /* Serialize against concurrent load/unload — see s_loader_lock. */
    if (s_loader_lock) xSemaphoreTake(s_loader_lock, portMAX_DELAY);

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

    /* Arch compatibility check — reject binaries for a different ISA. */
    if (app->manifest.arch[0] != '\0') {
#if defined(CONFIG_IDF_TARGET_ESP32)
        static const char kernel_arch[] = "xtensa-esp32";
#elif defined(CONFIG_IDF_TARGET_ESP32S2)
        static const char kernel_arch[] = "xtensa-esp32s2";
#elif defined(CONFIG_IDF_TARGET_ARCH_XTENSA)
        static const char kernel_arch[] = "xtensa-esp32s3";
#else
        static const char kernel_arch[] = "riscv32";
#endif
        if (strcmp(app->manifest.arch, kernel_arch) != 0) {
            klog_e(TAG, "arch mismatch: app='%s' kernel='%s'",
                   app->manifest.arch, kernel_arch);
            err = ESP_ERR_NOT_SUPPORTED;
            goto out;
        }
    }

    /* 7. Load sections */
    err = load_sections(f, &hdr, shdrs, shstrtab, app);
    if (err != ESP_OK) goto out;

    /* 8. Apply relocations */
    err = apply_relocations(f, &hdr, shdrs, shstrtab,
                             symtab, symcount, strtab, app);
    if (err != ESP_OK) goto out;

#ifdef CONFIG_IDF_TARGET_ARCH_XTENSA
    /* Install the relocated image (staged in DRAM scratch) into the IRAM exec
     * block, then release the scratch buffer.  ISYNC makes the freshly written
     * instructions visible to the fetch pipeline. */
    if (app->exec_block_size > 0 && s_build_scratch)
        build_install_exec((uintptr_t)app->exec_block,
                            s_build_scratch, app->exec_block_size);
    if (s_build_scratch) {
        heap_caps_free(s_build_scratch);
        s_build_scratch   = NULL;
        s_build_exec_base = 0;
        s_build_exec_size = 0;
    }
    asm volatile("isync" ::: "memory");
#endif /* CONFIG_IDF_TARGET_ARCH_XTENSA */

    /* 8.5. API table injection (Phase 22 / ABI v3).
     *
     * Apps built with libdune.a contain a DEFINED symbol named
     * DUNEOS_API_SYMBOL ("__duneos_api_ptr") in their data section.
     * libdune.a initialises it to NULL; we overwrite it with the kernel's
     * API table pointer here — before app_main is called — so that every
     * libdune wrapper can dispatch through it from the very first call.
     *
     * We search DEFINED symbols (st_shndx != SHN_UNDEF) for the magic name.
     * The pointer variable lives in the data pool (D-bus accessible), so a
     * plain dereference is safe.  to_write_ptr() is called for correctness
     * on Xtensa, though data sections are never in the exec pool.           */
    for (int i = 0; i < symcount; i++) {
        const elf32_sym_t *sym = &symtab[i];
        if (sym->st_shndx == SHN_UNDEF || sym->st_shndx >= hdr.e_shnum) continue;
        if (strcmp(strtab + sym->st_name, DUNEOS_API_SYMBOL) != 0)        continue;

        void *section_base = app->section_bases[sym->st_shndx];
        if (!section_base) {
            klog_w(TAG, DUNEOS_API_SYMBOL " is in an unloaded section — skip");
            break;
        }
        void *sym_addr = (uint8_t *)section_base + sym->st_value;
        /* to_write_ptr is a no-op for data sections; keeps the code correct
         * unconditionally without a separate Xtensa ifdef.                  */
        const duneos_api_t **inject_ptr =
            (const duneos_api_t **)to_write_ptr(sym_addr);
        *inject_ptr = duneos_api_get();
        klog_d(TAG, "API table (v%u) injected @ %p",
               (unsigned)duneos_api_get()->version, (void *)inject_ptr);
        break;
    }

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
#ifdef CONFIG_IDF_TARGET_ARCH_XTENSA
    /* Release the transient exec-staging buffer if an error left it held. */
    if (s_build_scratch) {
        heap_caps_free(s_build_scratch);
        s_build_scratch   = NULL;
        s_build_exec_base = 0;
        s_build_exec_size = 0;
    }
#endif
    fclose(f);
    free(shdrs);
    free(shstrtab);
    free(symtab);
    free(strtab);
    if (app) unload_locked(app);   /* lock already held */
    if (s_loader_lock) xSemaphoreGive(s_loader_lock);
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

/* Search order: flash takes priority over SD, bin/ over apps/. */
static const char *const s_scan_dirs[] = {
    "/flash/bin",
    "/sd/bin",
    "/sd/apps",
    NULL,
};

esp_err_t duneos_loader_scan(duneos_app_info_t *list, int max, int *found)
{
    if (!list || max <= 0 || !found) return ESP_ERR_INVALID_ARG;
    *found = 0;

    for (int d = 0; s_scan_dirs[d]; d++) {
        DIR *dir = opendir(s_scan_dirs[d]);
        if (!dir) continue;  /* directory absent — silently skip */

        struct dirent *ent;
        while ((ent = readdir(dir)) != NULL && *found < max) {
            size_t len = strlen(ent->d_name);
            if (len < 5) continue;
            const char *ext = ent->d_name + len - 4;
            if (strcasecmp(ext, ".elf") != 0 && strcasecmp(ext, ".dap") != 0)
                continue;

            duneos_app_info_t *info = &list[*found];
            snprintf(info->path, sizeof(info->path),
                     "%s/%s", s_scan_dirs[d], ent->d_name);

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

            /* Dedup by app name — earlier directory wins (flash > SD). */
            bool dup = false;
            for (int j = 0; j < *found; j++) {
                if (strcmp(list[j].meta.name, info->meta.name) == 0) {
                    dup = true;
                    break;
                }
            }
            if (dup) continue;

            klog_i(TAG, "  [%d] %s  v%s  %s",
                   *found, info->meta.name, info->meta.version, info->path);
            (*found)++;
        }
        closedir(dir);
    }

    klog_i(TAG, "scan: %d app(s) found", *found);
    return (*found > 0) ? ESP_OK : ESP_ERR_NOT_FOUND;
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

void duneos_loader_get_data_pool(const duneos_app_t *app,
                                  uintptr_t *base, size_t *size)
{
    if (base) *base = app ? (uintptr_t)app->data_pool : 0;
    if (size) *size = app ? app->data_pool_size : 0;
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

    /* Lazy-init the nested-run lock. */
    if (!s_captured_lock) {
        s_captured_lock = xSemaphoreCreateMutex();
        if (!s_captured_lock) return ESP_ERR_NO_MEM;
    }
    /* Refuse to nest. The single global s_captured_jmp would corrupt if
     * an outer captured run set it and an inner one overwrote it before
     * unwinding. ADR 016: nested captured runs are forbidden. */
    if (xSemaphoreTake(s_captured_lock, 0) != pdTRUE) {
        klog_w(TAG, "captured: refusing nested run");
        return ESP_ERR_INVALID_STATE;
    }

    /* fcntl(F_DUPFD) is not supported by the USB-JTAG console VFS driver.
     * Instead: close fd 1 to free the slot, then immediately open the capture
     * file so it lands at fd 1 (lowest available fd).  The caller (g_shell)
     * does not use fd 1 itself — stdout stays unrestored after capture. */
    close(STDOUT_FILENO);
    int capfd = open(CAPTURE_PATH, O_WRONLY | O_CREAT | O_TRUNC, 0600);
    if (capfd < 0) {
        klog_e(TAG, "capture: open failed: errno %d", errno);
        xSemaphoreGive(s_captured_lock);
        return ESP_FAIL;
    }
    if (capfd != STDOUT_FILENO) {
        close(capfd);
        klog_e(TAG, "capture: expected fd 1, got %d", capfd);
        xSemaphoreGive(s_captured_lock);
        return ESP_ERR_NOT_SUPPORTED;
    }

    klog_d(TAG, "jumping to app_main @ %p (captured)", (void *)app->entry);

    /* Install the setjmp checkpoint so duneos_exit() can unwind here
     * instead of vTaskDelete(NULL)-ing our caller (the shell). */
    jmp_buf env;
    portENTER_CRITICAL(&s_captured_mux);
    s_captured_jmp  = &env;
    s_captured_code = 0;
    portEXIT_CRITICAL(&s_captured_mux);

    if (setjmp(env) == 0) {
        /* First entry — normal app body. If app_main returns without
         * calling duneos_exit, s_captured_code stays 0 (=clean exit). */
        app->entry();
    }
    /* Both paths (normal return + longjmp from duneos_exit) land here.
     * Clear the jmp_buf before unlocking so a subsequent caller in
     * another task starts with a clean state. */
    portENTER_CRITICAL(&s_captured_mux);
    s_captured_jmp = NULL;
    portEXIT_CRITICAL(&s_captured_mux);

    close(STDOUT_FILENO);

    int rfd = open(CAPTURE_PATH, O_RDONLY);
    if (rfd < 0) {
        klog_e(TAG, "capture: cannot read back " CAPTURE_PATH);
        xSemaphoreGive(s_captured_lock);
        return ESP_FAIL;
    }

    struct stat st;
    fstat(rfd, &st);
    size_t size = (size_t)st.st_size;

    char *buf = malloc(size + 1);
    if (!buf) {
        close(rfd);
        xSemaphoreGive(s_captured_lock);
        return ESP_ERR_NO_MEM;
    }

    ssize_t n = read(rfd, buf, size);
    close(rfd);
    unlink(CAPTURE_PATH);

    if (n < 0) {
        free(buf);
        xSemaphoreGive(s_captured_lock);
        return ESP_FAIL;
    }

    buf[n] = '\0';
    *out_buf = buf;
    *out_len = (size_t)n;

    klog_d(TAG, "capture: %zu byte(s) captured (exit code %d)",
           (size_t)n, s_captured_code);
    xSemaphoreGive(s_captured_lock);
    return ESP_OK;
}

/* Body of the unload; caller must hold s_loader_lock (it mutates the exec-pool
 * bump allocator).  Called directly from load()'s error path (lock already
 * held) and via the public wrapper below. */
static void unload_locked(duneos_app_t *app)
{
    if (!app) return;
    heap_caps_free(app->data_pool);
#ifdef CONFIG_IDF_TARGET_ARCH_XTENSA
    /* LIFO reclaim: only possible when this app was the last to allocate. */
    if (app->exec_block_size > 0 && s_exec_pool_used == app->exec_pool_end) {
        s_exec_pool_used = app->exec_pool_mark;
        klog_d(TAG, "exec pool reclaimed: pool now %zu B used", s_exec_pool_used);
    }
#endif
    free(app);
}

void duneos_loader_unload(duneos_app_t *app)
{
    if (!app) return;
    if (s_loader_lock) xSemaphoreTake(s_loader_lock, portMAX_DELAY);
    unload_locked(app);
    if (s_loader_lock) xSemaphoreGive(s_loader_lock);
}
